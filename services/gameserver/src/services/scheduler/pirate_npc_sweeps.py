"""Suspect auto-clear + pirate-ecosystem sweeps
(WO-QUALITY-techdebt-scheduler-split).

Two previously-held sweep wirings (WO-CMB-SUSPECT-LIFE-1 / WO-PIRATE-ECO-2)
grouped together as the NPC-crime/pirate-facing periodic sweeps; the third
sibling sweep from that original grouping (team-reputation) lives in
``reputation_team_sweeps`` instead.

Moved verbatim from the old ``npc_scheduler_service.py``.
"""

import logging
from datetime import datetime, UTC
from typing import Dict

from sqlalchemy import text

from src.services.scheduler._common import (
    _SUSPECT_CLEAR_STATE_KEY,
    _PIRATE_ECOSYSTEM_TICK_STATE_KEY,
    SUSPECT_CLEAR_SWEEP_SECONDS,
    PIRATE_ECOSYSTEM_TICK_SWEEP_SECONDS,
    _SUSPECT_CLEAR_LOCK_KEY,
    _PIRATE_ECOSYSTEM_TICK_LOCK_KEY,
    _sweep_due_and_advance,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Three previously-held sweep wirings (WO-CMB-SUSPECT-LIFE-1 / WO-RT-TEAM-REP
# / WO-PIRATE-ECO-2) — each sync core was built and independently unit-tested
# by its own author; wiring it behind this scheduler's loop dispatch was the
# explicitly-reported open item on all three. Own SessionLocal, commit/
# rollback/close discipline mirroring _run_price_alert_sweep_sync exactly.
# ---------------------------------------------------------------------------

def _run_suspect_clear_sweep_sync() -> int:
    """Auto-clear every player whose suspect_until has elapsed
    (ships.md:293's "auto-clears at suspect_until" guarantee — nothing else
    re-checks a stale is_suspect flag once the triggering encounter is
    over). Returns the count cleared.

    WO-SWEEP-SILENT-SWEEPS: got_lock=False used to return 0 with no log
    line at all -- indistinguishable, from the log, from "ran and found
    nothing due" (the caller only logs `if cleared:`). Both are legitimately
    silent-most-of-the-time states, but only ONE of them means the sweep
    never actually ran this tick -- that one now gets its own line."""
    from src.core.database import SessionLocal
    from src.services.suspect_service import clear_expired_suspects

    db = SessionLocal()
    try:
        got_lock = db.execute(
            text("SELECT pg_try_advisory_xact_lock(:key)"),
            {"key": _SUSPECT_CLEAR_LOCK_KEY},
        ).scalar()
        if not got_lock:
            logger.info("NPC scheduler: suspect auto-clear sweep — lock busy, skipped")
            return 0
        if not _sweep_due_and_advance(
            db, _SUSPECT_CLEAR_STATE_KEY, SUSPECT_CLEAR_SWEEP_SECONDS, datetime.now(UTC),
        ):
            return 0
        cleared = clear_expired_suspects(db)
        db.commit()
        return cleared
    except Exception:
        logger.exception("Suspect auto-clear sweep failed")
        db.rollback()
        return 0
    finally:
        db.close()



def _run_pirate_ecosystem_tick_sync() -> Dict[str, int]:
    """The outer per-region/per-holding loop pirate_ecosystem_service's own
    docstrings name as "the outer scheduler's job (Lane B, deferred)": for
    every ACTIVE region, run the weekly growth tick; for every
    pirate-controlled (owner_player_id IS NULL), non-Stronghold holding in
    that region, run the evolution tick. Both engines are self-contained
    and idempotent per their own window (see the cadence constant's own
    docstring). Per-region/per-holding SAVEPOINT isolation (db.begin_nested)
    mirrors the citizen-rebake sweep's per-ship isolation — one bad row
    can't abort the rest of the pass.

    Realtime telemetry for growth/evolution
    (pirate_ecosystem_service._broadcast_pirate_event) is a documented,
    PRE-EXISTING no-op from a worker-thread caller ("no running loop —
    sync/worker context; nothing polls this today", per that function's own
    docstring) — unrelated to this wiring, not fixed here.

    Returns {regions_ticked, growth_actions, holdings_evaluated,
    evolutions}."""
    from src.core.database import SessionLocal
    from src.models.pirate_holding import PirateHolding, PirateHoldingTier
    from src.models.region import Region, RegionStatus
    from src.services.pirate_ecosystem_service import evolution_tick, run_weekly_tick

    empty = {"regions_ticked": 0, "growth_actions": 0, "holdings_evaluated": 0, "evolutions": 0}
    db = SessionLocal()
    try:
        got_lock = db.execute(
            text("SELECT pg_try_advisory_xact_lock(:key)"),
            {"key": _PIRATE_ECOSYSTEM_TICK_LOCK_KEY},
        ).scalar()
        if not got_lock:
            return empty
        if not _sweep_due_and_advance(
            db, _PIRATE_ECOSYSTEM_TICK_STATE_KEY, PIRATE_ECOSYSTEM_TICK_SWEEP_SECONDS, datetime.now(UTC),
        ):
            return empty

        counts = dict(empty)
        regions = db.query(Region).filter(Region.status == RegionStatus.ACTIVE.value).all()
        for region in regions:
            counts["regions_ticked"] += 1
            try:
                with db.begin_nested():
                    result = run_weekly_tick(db, region)
                if result.get("action") not in (
                    "skipped", "already_ticked_this_window", "no_growth",
                ):
                    counts["growth_actions"] += 1
            except Exception:
                logger.exception(
                    "Pirate-ecosystem growth tick failed for region %s", region.id,
                )

            holdings = (
                db.query(PirateHolding)
                .filter(
                    PirateHolding.region_id == region.id,
                    PirateHolding.owner_player_id.is_(None),
                    PirateHolding.tier != PirateHoldingTier.STRONGHOLD,
                )
                .all()
            )
            for holding in holdings:
                counts["holdings_evaluated"] += 1
                try:
                    with db.begin_nested():
                        result = evolution_tick(db, holding)
                    if result.get("action") == "evolved":
                        counts["evolutions"] += 1
                except Exception:
                    logger.exception(
                        "Pirate-ecosystem evolution tick failed for holding %s", holding.id,
                    )

        db.commit()
        return counts
    except Exception:
        logger.exception("Pirate-ecosystem tick sweep failed")
        db.rollback()
        return empty
    finally:
        db.close()


