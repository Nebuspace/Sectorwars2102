"""Message-beacon expiry sweep (WO-P4-play-beacon-kernel).

Own SessionLocal, commit after, close on exit; the pure, testable core
(message_beacon_service.sweep_expired) lives in its own service module —
this wrapper is session-management + advisory-lock glue only, mirroring
contract_sweeps.py's own _run_contract_expire_sweep_sync exactly.

Returns (count, events) rather than a bare int -- unlike the contract
sweep, beacon expiry needs to broadcast `beacon_expired` frames to any
player still in the affected sectors (message-beacons.md:53), and this
wrapper runs inside asyncio.to_thread (a worker thread, no running event
loop) so it CANNOT dispatch the WS calls itself. The caller (core_loop.py)
hands the returned events to scheduler._common._broadcast_events back on
the event loop -- same "dual-transport" split every other sweep-originated
broadcast in this package already uses (see message_beacon_service.py's
own module docstring for the full rationale).
"""

import logging
from datetime import datetime, UTC
from typing import Any, Dict, List, Tuple

from sqlalchemy import text

from src.services.scheduler._common import (
    _BEACON_EXPIRE_STATE_KEY,
    BEACON_EXPIRE_SWEEP_SECONDS,
    _BEACON_EXPIRE_LOCK_KEY,
    _sweep_due_and_advance,
)

logger = logging.getLogger(__name__)


def _run_beacon_expire_sweep_sync() -> Tuple[int, List[Dict[str, Any]]]:
    """Expire due message beacons. Mirrors _run_contract_expire_sweep_sync's
    lock-then-due-check ordering exactly: the advisory lock is acquired
    FIRST, and only a successful acquirer proceeds to the durable due-check.
    On lock contention this logs and returns (0, []) rather than skipping
    silently (WO-SWEEP-SILENT-SWEEPS discipline)."""
    from src.core.database import SessionLocal
    from src.services.message_beacon_service import sweep_expired

    db = SessionLocal()
    try:
        got_lock = db.execute(
            text("SELECT pg_try_advisory_xact_lock(:key)"),
            {"key": _BEACON_EXPIRE_LOCK_KEY},
        ).scalar()
        if not got_lock:
            logger.info("NPC scheduler: beacon expire sweep — lock busy, skipped")
            return 0, []
        if not _sweep_due_and_advance(
            db, _BEACON_EXPIRE_STATE_KEY, BEACON_EXPIRE_SWEEP_SECONDS, datetime.now(UTC),
        ):
            return 0, []
        result = sweep_expired(db)
        db.commit()
        return result.get("expired", 0), result.get("events", [])
    except Exception:
        logger.exception("Beacon expire sweep failed")
        db.rollback()
        return 0, []
    finally:
        db.close()
