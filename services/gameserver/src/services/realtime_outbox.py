"""Transactional outbox for realtime events (ADR-0054 X-V1).

First real implementation of this pattern -- ADR-0060 and SYSTEMS/
realtime-bus.md both cite "the ADR-0054 transactional outbox" as an
already-established, multi-consumer pattern ("used by region-lifecycle
cleanup (B), ship destruction (D), and pirate-ecosystem capture (A)"), but
a repo-wide grep for ``pending_realtime_events`` / an outbox class turned
up zero hits anywhere in ``services/gameserver/src`` before this file.
Those citations describe an aspiration, not shipped code -- this module is
the first instance, not a reuse of an existing one. Future consumers
(ADR-0060 G-*, ship-destruction, holding-capture) should import
``RealtimeOutbox`` from here rather than re-inventing the accumulate/flush
shape.

Per ADR-0054 X-V1: realtime events emitted from a multi-table transaction
accumulate in-process during the transaction and flush to the realtime bus
only after the owning commit succeeds. A rollback silently discards the
queued events -- no ghost-state broadcast for data that was never
persisted. Event delivery is best-effort post-commit: a broadcast failure
is logged, never rolled back (the DB write already succeeded).

Bridges into the async ``websocket_service.connection_manager`` from
whatever thread flush() is called on, using the SAME sync->async bridge
``intrasystem_movement_service._resolve_broadcast_loop`` already
established for exactly this shape (a scheduler tick running via
``asyncio.to_thread`` with no event loop of its own on that thread,
falling back to the loop captured at scheduler startup via
``set_scheduler_event_loop``). Reused here rather than duplicated.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List, Tuple

logger = logging.getLogger(__name__)

# ("personal", event_type, payload, target_user_id) |
# ("sector", event_type, payload, target_sector_id)
_QueuedEvent = Tuple[str, str, Dict[str, Any], Any]


class RealtimeOutbox:
    """Per-transaction accumulator. Construct one at the start of the
    transaction whose commit should gate delivery; queue events as work
    happens; call ``flush()`` ONLY after the owning commit has succeeded.
    On a rollback path, simply let the instance be discarded -- never call
    flush()."""

    def __init__(self) -> None:
        self._events: List[_QueuedEvent] = []

    def queue_personal(self, event_type: str, payload: Dict[str, Any], user_id: Any) -> None:
        """Queue a direct-to-player event (mirrors ``websocket_service.
        ConnectionManager.send_personal_message``)."""
        if user_id is None:
            return
        self._events.append(("personal", event_type, dict(payload), str(user_id)))

    def queue_sector(self, event_type: str, payload: Dict[str, Any], sector_id: Any) -> None:
        """Queue a sector-broadcast event (mirrors ``ConnectionManager.
        broadcast_to_sector``)."""
        if sector_id is None:
            return
        self._events.append(("sector", event_type, dict(payload), int(sector_id)))

    def __len__(self) -> int:
        return len(self._events)

    def flush(self) -> int:
        """Best-effort post-commit delivery. Returns the number of events
        attempted (not the number that actually reached a live socket --
        delivery is fire-and-forget, matching this codebase's existing
        WS-broadcast convention, e.g. intrasystem_movement_service.
        _emit_leg_ws_event). Safe to call with zero queued events."""
        if not self._events:
            return 0
        events = self._events
        self._events = []

        from src.services.intrasystem_movement_service import _resolve_broadcast_loop
        from src.services.websocket_service import connection_manager

        loop = _resolve_broadcast_loop()
        if loop is None or loop.is_closed():
            logger.debug(
                "realtime_outbox: flush skipped for %d event(s) -- no live "
                "event loop resolvable", len(events),
            )
            return len(events)

        for kind, event_type, payload, target in events:
            frame = {"type": event_type, **payload}
            try:
                if kind == "personal":
                    asyncio.run_coroutine_threadsafe(
                        connection_manager.send_personal_message(target, frame), loop,
                    )
                elif kind == "sector":
                    asyncio.run_coroutine_threadsafe(
                        connection_manager.broadcast_to_sector(target, frame), loop,
                    )
            except Exception:
                logger.warning(
                    "realtime_outbox: emit failed for event_type=%s", event_type,
                    exc_info=True,
                )
        return len(events)
