"""ARIA narration kernel (WO-ARIA-NARRATE-KERNEL / ADR-0068).

Manual, template-only narration for the ARIA narration hooks event
catalog (aria-companion.md:208-241). This kernel covers the 5 rows
buildable without further design work: P-F1, P-F7, P-F8, P-A2, P-A3.
ZERO LLM -- every line is rendered from a fixed ``str.format`` template
in ``REGISTRY`` below.

The kernel is deliberately DB-free: suppression state, the global
narration ceiling, and the priority-aware backlog queue all live in
plain in-memory structures on a per-process singleton, mirroring the
``AISecurityService.player_profiles`` / ``cost_tracking`` precedent
(src/services/ai_security_service.py) rather than adding a migration.
Callers own every DB read (assistance level, sector/station/team
context) and pass the results in as plain values -- see
``resolve_assistance_level`` for the one shared DB helper.

Delivery seam: this WO does not wire a live transport (WebSocket lane
is locked by another in-flight worker). ``drain_due_lines`` is the seam
-- a later scheduler/WS hook calls it per player on a cadence, and pushes
whatever it returns via ``NarrationLine.to_payload()``
(``{event_id, line, priority, ts}``, matching the client's expected
shape) over the existing connection. No further kernel changes should
be required to wire that up.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Set, Tuple

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


# --- assistance-level gating --------------------------------------------
# [NO-CANON -- provisional pending Max's 3-vs-4-level vocab ruling]
# ADR-0068 / aria-companion.md:212 specs a 4-level vocab (minimal / quiet /
# standard / full) where `quiet` additionally skips P-I* rows on top of
# what `minimal` skips. The LIVE column this kernel actually reads --
# PlayerTradingProfile.ai_assistance_level (src/models/ai_trading.py:61)
# and its API contract (src/api/routes/ai.py:91, pattern
# "^(minimal|medium|full)$") -- only recognizes 3 values: minimal / medium
# / full. There is no `quiet` tier anywhere in code, and aria-companion.md
# :181-187's own player-facing table also only documents 3 levels
# (minimal / medium / full), directly conflicting with :212's 4-level
# gating table in the same doc.
#
# Per dispatch: this kernel implements the CURRENTLY-LIVE 3-level vocab --
# `minimal` skips all P-A* (atmospheric) rows; `medium` and `full` fire
# everything this kernel knows about (P-I* rows aren't in this WO's 5
# buildable rows, so the `quiet` distinction has no observable effect yet
# either way). If Max ratifies the 4-level vocab, `quiet` needs adding to
# the live column + API pattern, and this gate needs a second tier once a
# P-I* row is built.
ASSISTANCE_LEVELS_SUPPRESSING_ATMOSPHERIC = {"minimal"}


# Priority classes per aria-companion.md:238 ("P-F* critical/standard >
# P-I* interactive > P-A* atmospheric"). Smaller rank = higher priority.
PRIORITY_P_F = 0
PRIORITY_P_I = 1
PRIORITY_P_A = 2

GLOBAL_CEILING_SECONDS = 60  # aria-companion.md:212 -- one line/minute/player
BACKLOG_MAX = 3              # aria-companion.md:212/238 -- backlog of 3, oldest-first


@dataclass(frozen=True)
class NarrationEventDef:
    """One row of the ARIA narration hooks event catalog."""

    event_id: str
    trigger: str
    template: str
    priority_rank: int
    suppression_scope: str  # "session" | "ever"


# Data-driven registry -- INSERTS, not if-cascades. A new catalog row is a
# new list entry here; nothing in the suppression/ceiling/eviction
# machinery below branches on event_id.
REGISTRY: Dict[str, NarrationEventDef] = {
    d.event_id: d
    for d in [
        NarrationEventDef(
            event_id="P-F1",
            trigger="Player completes first profitable trade in a session",
            template=(
                "Nice — that's a {margin} cr margin. Want me to flag the "
                "same route on your next dock?"
            ),
            priority_rank=PRIORITY_P_F,
            suppression_scope="session",
        ),
        NarrationEventDef(
            event_id="P-F7",
            trigger="First docking at any station with available contracts",
            template=(
                "There's a contract board at {station_name} — "
                "{contract_count} job{plural} open. Want me to filter by "
                "what fits your ship?"
            ),
            priority_rank=PRIORITY_P_F,
            suppression_scope="ever",
        ),
        NarrationEventDef(
            event_id="P-F8",
            trigger="military_rank changes",
            template=(
                "You've made {new_rank}. Combat bonus +{combat_bonus}%, "
                "max-turn cap up by {max_turns_bonus}. Crews notice."
            ),
            priority_rank=PRIORITY_P_F,
            suppression_scope="ever",
        ),
        NarrationEventDef(
            event_id="P-A2",
            trigger="Player first enters a sector flagged discovered=false for them",
            template="New sector. I'm reading {sector_type_desc}. Worth a scan?",
            priority_rank=PRIORITY_P_A,
            suppression_scope="ever",
        ),
        NarrationEventDef(
            event_id="P-A3",
            trigger="Player.team_id transitions from null to non-null",
            template=(
                "Welcome to {team_name}. Their territory map syncs to "
                "yours; you'll see member positions in shared sectors. "
                "Team chat is the `team` channel."
            ),
            priority_rank=PRIORITY_P_A,
            suppression_scope="ever",
        ),
    ]
}


@dataclass
class NarrationLine:
    """A rendered narration line, either delivered immediately (under the
    global ceiling) or sitting in a player's backlog queue awaiting
    ``drain_due_lines``."""

    event_id: str
    player_id: str
    text: str
    priority_rank: int
    created_at: datetime
    delivered_immediately: bool = False

    def to_payload(self) -> Dict[str, Any]:
        """Full WS envelope, matching the client's ARIANarrationMessage
        contract (services/player-client/src/services/websocket.ts)
        exactly: ``{type: 'aria_narration', event_id, line, priority, ts}``."""
        return {
            "type": "aria_narration",
            "event_id": self.event_id,
            "line": self.text,
            "priority": self.priority_rank,
            "ts": self.created_at.isoformat(),
        }


class AriaNarrationService:
    """Per-process kernel: suppression, global ceiling, and the
    priority-aware backlog queue. See module docstring for the
    DB-free / delivery-seam rationale."""

    def __init__(self) -> None:
        self._queues: Dict[str, List[NarrationLine]] = {}
        self._last_emit: Dict[str, datetime] = {}
        # (event_id, player_id) -> session_token that already fired it.
        self._session_fired: Dict[Tuple[str, str], Any] = {}
        # (event_id, player_id, dedupe_key) already fired, permanent for
        # process lifetime.
        self._ever_fired: Set[Tuple[str, str, Any]] = set()

    def record_event(
        self,
        event_id: str,
        player_id: Any,
        *,
        assistance_level: str = "medium",
        session_token: Optional[Any] = None,
        dedupe_key: Optional[Any] = None,
        context: Optional[Dict[str, Any]] = None,
        now: Optional[datetime] = None,
    ) -> Optional[NarrationLine]:
        """Evaluate one narration trigger. Returns the rendered
        ``NarrationLine`` if it was accepted (delivered immediately or
        queued), or ``None`` if suppressed / gated / unknown event_id /
        template render failure. Never raises -- callers still wrap this
        in their own try/except per the best-effort hook convention, but
        the kernel itself degrades gracefully.
        """
        definition = REGISTRY.get(event_id)
        if definition is None:
            logger.warning("aria_narration: unknown event_id %s", event_id)
            return None

        player_key = str(player_id)
        now = now or datetime.now(timezone.utc)

        if (
            definition.priority_rank == PRIORITY_P_A
            and assistance_level in ASSISTANCE_LEVELS_SUPPRESSING_ATMOSPHERIC
        ):
            return None

        if self._is_suppressed(definition, player_key, session_token, dedupe_key):
            return None

        try:
            text = definition.template.format(**(context or {}))
        except (KeyError, IndexError) as e:
            logger.error(
                "aria_narration: template render failed for %s: %s", event_id, e
            )
            return None

        self._mark_fired(definition, player_key, session_token, dedupe_key)

        line = NarrationLine(
            event_id=event_id,
            player_id=player_key,
            text=text,
            priority_rank=definition.priority_rank,
            created_at=now,
        )

        last_emit = self._last_emit.get(player_key)
        if last_emit is None or (now - last_emit) >= timedelta(
            seconds=GLOBAL_CEILING_SECONDS
        ):
            self._last_emit[player_key] = now
            line.delivered_immediately = True
            return line

        dropped = self._enqueue(player_key, line)
        if dropped is not None:
            logger.debug(
                "aria_narration: backlog full for player %s -- dropped %s "
                "in favor of %s",
                player_key,
                dropped.event_id,
                event_id,
            )
        return line

    def drain_due_lines(
        self, player_id: Any, now: Optional[datetime] = None
    ) -> List[NarrationLine]:
        """Pop the oldest queued line for this player if the global
        ceiling now allows delivering it. Returns at most one line --
        the ceiling caps delivery at one per minute regardless of how
        many are backlogged. This is the delivery seam a later WS/poller
        hook calls on a cadence.
        """
        player_key = str(player_id)
        queue = self._queues.get(player_key)
        if not queue:
            return []

        now = now or datetime.now(timezone.utc)
        last_emit = self._last_emit.get(player_key)
        if last_emit is not None and (now - last_emit) < timedelta(
            seconds=GLOBAL_CEILING_SECONDS
        ):
            return []

        line = queue.pop(0)
        self._last_emit[player_key] = now
        return [line]

    def queue_depth(self, player_id: Any) -> int:
        return len(self._queues.get(str(player_id), []))

    # -- internal --------------------------------------------------------

    def _enqueue(
        self, player_key: str, line: NarrationLine
    ) -> Optional[NarrationLine]:
        """Push onto the player's backlog, evicting per
        aria-companion.md:238 if already at ``BACKLOG_MAX``: prefer to
        drop the OLDEST entry with a strictly lower priority (higher
        ``priority_rank``) than the incoming line; if nothing queued is
        lower priority than the incoming line, fall back to plain
        oldest-first. Returns the evicted line, if any.
        """
        queue = self._queues.setdefault(player_key, [])
        dropped = None
        if len(queue) >= BACKLOG_MAX:
            worse = [
                i for i, q in enumerate(queue) if q.priority_rank > line.priority_rank
            ]
            drop_index = min(worse) if worse else 0
            dropped = queue.pop(drop_index)
        queue.append(line)
        return dropped

    def _is_suppressed(
        self,
        definition: NarrationEventDef,
        player_key: str,
        session_token: Optional[Any],
        dedupe_key: Optional[Any],
    ) -> bool:
        if definition.suppression_scope == "session":
            key = (definition.event_id, player_key)
            return (
                key in self._session_fired
                and self._session_fired[key] == session_token
            )
        # "ever"
        key = (definition.event_id, player_key, dedupe_key)
        return key in self._ever_fired

    def _mark_fired(
        self,
        definition: NarrationEventDef,
        player_key: str,
        session_token: Optional[Any],
        dedupe_key: Optional[Any],
    ) -> None:
        if definition.suppression_scope == "session":
            self._session_fired[(definition.event_id, player_key)] = session_token
        else:
            self._ever_fired.add((definition.event_id, player_key, dedupe_key))


_aria_narration_service = AriaNarrationService()


def get_aria_narration_service() -> AriaNarrationService:
    """Singleton getter -- mirrors get_aria_intelligence_service()."""
    return _aria_narration_service


def resolve_assistance_level(db: Session, player_id: Any) -> str:
    """Sync read of the live 3-level ``PlayerTradingProfile
    .ai_assistance_level`` column (see the NO-CANON note above). Defaults
    to the column's own 'medium' default when no profile row exists yet
    (profiles are created lazily elsewhere, e.g. the /ai/profile GET in
    src/api/routes/ai.py). Never raises.

    WO-QTI-PHANTOM-SCHEMA lane c: the query is SAVEPOINT-scoped
    (``begin_nested``) -- an unguarded query failure would poison the
    caller's shared session, so the host route's OWN later ``db.commit()``
    would raise ``PendingRollbackError`` even though this function's own
    bare except already logged and moved on (the exact P1 shape the
    addendum fixed elsewhere -- catching the Python exception does not
    undo a Postgres-aborted transaction). House precedent: bounty_service
    .py/combat_service.py/faction_service.py wrap writes the same way;
    this wraps a read for the identical reason -- ANY failed statement,
    read or write, aborts a Postgres transaction until rolled back.
    """
    try:
        from src.models.ai_trading import PlayerTradingProfile

        with db.begin_nested():
            profile = (
                db.query(PlayerTradingProfile)
                .filter(PlayerTradingProfile.player_id == player_id)
                .first()
            )
        if profile and profile.ai_assistance_level:
            return profile.ai_assistance_level
    except Exception as e:
        logger.debug("aria_narration: assistance-level lookup failed: %s", e)
    return "medium"


def dispatch_narration_push(player: Any, line: NarrationLine) -> None:
    """Fire-and-forget WS push of a single already-accepted narration
    line to ``player``'s live connection (WO-ARIA-NARRATE-KERNEL lane C).

    Mirrors medal_service._dispatch_medal_awarded_event / movement_service
    's warp corp-share dispatch: deferred import (avoids an import cycle
    with websocket_service), grab the running loop, schedule with
    ``loop.create_task`` so this never blocks the sync hook call site that
    invokes it, and swallow every failure -- no running loop (a sync
    worker/scheduler context), no live socket, or any other hiccup must
    never affect the host operation the hook rides. A dead/disconnected
    socket is handled by connection_manager.send_personal_message itself
    (it silently no-ops -- "a dead socket drops silently" per the WO).

    Only call this for a line with ``delivered_immediately=True`` --
    queued (backlogged) lines are delivered later via the heartbeat-driven
    ``drain_due_lines`` sweep in websocket_service.handle_websocket_message,
    not from here (calling this for a queued line would double-delivery
    it: once now, once when drained).
    """
    try:
        import asyncio

        from src.services.websocket_service import connection_manager

        user_id = getattr(player, "user_id", None)
        if not user_id:
            return

        loop = asyncio.get_running_loop()
        loop.create_task(
            connection_manager.send_personal_message(str(user_id), line.to_payload())
        )
    except Exception:
        logger.debug(
            "Skipped aria_narration WS push for %s (no loop or socket)",
            line.event_id, exc_info=True,
        )
