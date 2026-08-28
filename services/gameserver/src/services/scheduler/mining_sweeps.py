"""Mining harvest resolve sweep (WO-MINING-ASYNC-HARVEST lane c).

Completes PENDING ``mining_harvests`` rows whose ``resolves_at`` has elapsed.
Advisory-lock gated so multi-instance gameservers do not double-grant cargo.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Tuple

from sqlalchemy import text

from src.services.mining_service import MiningService, build_harvest_notification_events
from src.services.scheduler._common import (
    _MINING_HARVEST_LOCK_KEY,
    _MINING_LICENSE_EXPIRY_LOCK_KEY,
)

logger = logging.getLogger(__name__)


def _run_mining_harvest_resolve_sync() -> Tuple[int, List[Dict[str, Any]]]:
    """Resolve due PENDING asteroid harvests. Returns (completed, events).

    Events are best-effort ``mining_harvest_completed`` frames for WS
    broadcast by the caller POST-COMMIT (same shape as genesis_progress).
    """
    from src.core.database import SessionLocal

    db = SessionLocal()
    events: List[Dict[str, Any]] = []
    completed = 0
    try:
        got_lock = db.execute(
            text("SELECT pg_try_advisory_xact_lock(:key)"),
            {"key": _MINING_HARVEST_LOCK_KEY},
        ).scalar()
        if not got_lock:
            return 0, events

        results = MiningService(db).resolve_due_harvests()
        for result in results:
            if not result.get("success"):
                continue
            completed += 1
            player_id = result.get("player_id")
            user_id = result.get("user_id")
            if player_id:
                events.append(
                    {
                        "type": "mining_harvest_completed",
                        "player_id": player_id,
                        "payload": {
                            "harvest_id": result.get("harvest_id"),
                            "ore": result.get("ore", 0),
                            "precious_metals": result.get("precious_metals", 0),
                            "quantum_shards": result.get("quantum_shards", 0),
                            "am_rep_delta": result.get("am_rep_delta", 0),
                            "remaining_turns": result.get("remaining_turns", 0),
                            "depletion_state": result.get("depletion_state") or {},
                        },
                    }
                )
            if user_id:
                events.extend(build_harvest_notification_events(user_id, result))
        db.commit()
        return completed, events
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _run_mining_license_expiry_warn_sync() -> Tuple[int, List[Dict[str, Any]]]:
    """Warn players whose active ClaimLicense expires within 1 hour."""
    from src.core.database import SessionLocal

    db = SessionLocal()
    try:
        got_lock = db.execute(
            text("SELECT pg_try_advisory_xact_lock(:key)"),
            {"key": _MINING_LICENSE_EXPIRY_LOCK_KEY},
        ).scalar()
        if not got_lock:
            return 0, []

        events = MiningService(db).collect_license_expiry_warning_events()
        db.commit()
        return len(events), events
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
