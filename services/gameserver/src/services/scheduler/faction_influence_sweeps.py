"""SectorFactionInfluence daily idle-decay sweep (LEG-INI-05 / LEG-65).

UTC-day cadence: −0.5 influence_percentage points per idle day when the
activity clock (``last_action_at``, else ``updated_at``) is ≥3 UTC days old.
Decay never writes ``last_action_at``. After each change, refresh
``patrol_spawn_weight``.
"""

from __future__ import annotations

import logging
from datetime import datetime, UTC
from typing import Dict

from sqlalchemy import text
from sqlalchemy.orm.attributes import flag_modified

from src.services.scheduler._common import (
    _SFI_DECAY_LOCK_KEY,
    _SFI_DECAY_STATE_KEY,
)

logger = logging.getLogger(__name__)


def _run_sector_faction_influence_decay_sync() -> Dict[str, int]:
    """Decay idle SectorFactionInfluence rows once per UTC calendar day.

    Returns ``{"rows": n_changed, "scanned": n}``; zeros on lock-held /
    already-ran-today paths.
    """
    from src.core.database import SessionLocal
    from src.models.galaxy import Galaxy
    from src.models.sector_faction_influence import SectorFactionInfluence
    from src.services.faction_service import apply_sector_influence_daily_decay

    result = {"rows": 0, "scanned": 0}
    db = SessionLocal()
    try:
        got_lock = db.execute(
            text("SELECT pg_try_advisory_xact_lock(:key)"),
            {"key": _SFI_DECAY_LOCK_KEY},
        ).scalar()
        if not got_lock:
            return result
        db.commit()

        today_str = datetime.now(UTC).date().isoformat()
        galaxy = db.query(Galaxy).order_by(Galaxy.created_at.asc()).first()
        if galaxy is not None:
            gstate = dict(galaxy.state or {})
            if gstate.get(_SFI_DECAY_STATE_KEY) == today_str:
                return result

        now = datetime.now(UTC)
        rows = db.query(SectorFactionInfluence).all()
        for row in rows:
            result["scanned"] += 1
            try:
                if apply_sector_influence_daily_decay(row, now=now):
                    result["rows"] += 1
                    db.commit()
            except Exception:
                logger.exception(
                    "SFI decay: row %s failed (skipped)", getattr(row, "id", "?")
                )
                db.rollback()

        if galaxy is not None:
            try:
                gstate = dict(galaxy.state or {})
                gstate[_SFI_DECAY_STATE_KEY] = today_str
                galaxy.state = gstate
                flag_modified(galaxy, "state")
                db.commit()
            except Exception:
                logger.exception("SFI decay: failed to advance Galaxy.state anchor")
                db.rollback()

        return result
    except Exception:
        logger.exception("SFI decay sweep crashed")
        db.rollback()
        return result
    finally:
        db.close()
