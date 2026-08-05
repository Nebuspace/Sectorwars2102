"""Ship-registry periodic sweeps (WO-FIX-SHIP-REGISTRY-AUTO-ARCHIVE-MISSING).

Own SessionLocal + advisory-lock + due-check wrappers — pure logic lives in
``ship_registry_service``. Mirrors ``beacon_sweeps`` / wanted-clear discipline.
"""

import logging
from datetime import datetime, UTC

from sqlalchemy import text

from src.services.scheduler._common import (
    _ABANDONMENT_ARCHIVE_STATE_KEY,
    ABANDONMENT_ARCHIVE_SWEEP_SECONDS,
    _ABANDONMENT_ARCHIVE_LOCK_KEY,
    _sweep_due_and_advance,
)

logger = logging.getLogger(__name__)


def _run_abandonment_archive_sweep_sync() -> int:
    """Archive abandoned ships past the 7-real-day unclaimed window.
    Lock acquired FIRST; only a successful acquirer proceeds to the due-check.
    """
    from src.core.database import SessionLocal
    from src.services.ship_registry_service import archive_expired_abandoned_ships

    db = SessionLocal()
    try:
        got_lock = db.execute(
            text("SELECT pg_try_advisory_xact_lock(:key)"),
            {"key": _ABANDONMENT_ARCHIVE_LOCK_KEY},
        ).scalar()
        if not got_lock:
            logger.info("NPC scheduler: abandonment archive sweep — lock busy, skipped")
            return 0
        if not _sweep_due_and_advance(
            db,
            _ABANDONMENT_ARCHIVE_STATE_KEY,
            ABANDONMENT_ARCHIVE_SWEEP_SECONDS,
            datetime.now(UTC),
        ):
            return 0
        archived = archive_expired_abandoned_ships(db, now=datetime.now(UTC))
        db.commit()
        return archived
    except Exception:
        logger.exception("Abandonment archive sweep failed")
        db.rollback()
        return 0
    finally:
        db.close()
