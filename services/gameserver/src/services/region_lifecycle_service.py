"""Region lifecycle cron -- SUSPENDED -> GRACE -> TERMINATED daily
advancement (WO-P8-region-lifecycle-cron). Schema shipped separately by
P8-region-lifecycle-schema (``RegionStatus`` enum + ``suspended_at`` /
``terminated_at`` / ``scheduled_hard_delete_at`` columns, migration
``b7e4a29f1c68_region_lifecycle_columns.py``, verified present) -- this WO
is additive-only, no new schema.

CANON-VS-WO-BRIEF CONFLICT (flagged, not silently resolved either way):
this WO's own brief cited ``suspended_at + 8 days -> GRACE`` and
``suspended_at + 31 days -> TERMINATED``. ``SYSTEMS/region-lifecycle.md``'s
state diagram (lines 17-46), transition-trigger table (lines 59-60), and
worked pseudocode (lines 764/770/773-774) unambiguously say **7** and
**30** days instead -- both measured from the ORIGINAL ``Region.
suspended_at``, not reset on entering grace -- with a **7**-day terminated
-> hard-delete window (line 80, 773-774), which DOES match the brief's
third number. Built against the DOCUMENTED numbers (7/30/7) per this
codebase's docs-win convention; the 8/31 discrepancy is surfaced back to
the lead for a ruling, not silently picked either way.

Both advance functions are pure, session-injectable BULK conditional
UPDATEs -- canon's trigger table lists no per-region side effect for
either transition (unlike, say, a takeover), so this mirrors contract_
service.sweep_expired_contracts' bulk-UPDATE shape for the "no per-row
Python touch needed" case rather than a per-row loop. Both are flush-only;
the caller (economy_governance_sweeps._run_region_lifecycle_advance_gated,
Phase 7 of the daily governance sweep) owns the commit.
"""
import logging
from datetime import UTC, datetime, timedelta
from typing import Dict, Optional

from sqlalchemy import update
from sqlalchemy.orm import Session

from src.models.planet import Planet
from src.models.region import Region, RegionStatus
from src.services.region_termination_cascade_service import (
    dispatch_station_termination,
    process_planet_termination,
)
from src.services.warp_gate_service import cascade_region_gate_teardown

logger = logging.getLogger(__name__)

# region-lifecycle.md:59 / :764 -- 7 days elapsed since Region.suspended_at,
# payment unrecovered.
SUSPENDED_TO_GRACE_DAYS = 7
# region-lifecycle.md:60 / :770 -- 30 days elapsed since the SAME original
# Region.suspended_at (NOT reset on entering grace), payment still
# unrecovered.
SUSPENDED_TO_TERMINATED_DAYS = 30
# region-lifecycle.md:80 / :773-774 -- scheduled_hard_delete_at =
# terminated_at + 7 days.
TERMINATED_TO_HARD_DELETE_DAYS = 7


def advance_to_grace(db: Session, now: Optional[datetime] = None) -> Dict[str, int]:
    """SUSPENDED -> GRACE for every region whose ``suspended_at`` is at
    least ``SUSPENDED_TO_GRACE_DAYS`` in the past. The WHERE clause's own
    ``Region.status == SUSPENDED`` re-check at write time means a region a
    concurrent takeover already returned to ACTIVE (canon: "suspended /
    grace -> active" via ``execute_takeover`` or payment recovery) is
    naturally excluded -- no extra coordination needed with that path."""
    now = now or datetime.now(UTC)
    cutoff = now - timedelta(days=SUSPENDED_TO_GRACE_DAYS)
    stmt = (
        update(Region)
        .where(
            Region.status == RegionStatus.SUSPENDED,
            Region.suspended_at.isnot(None),
            Region.suspended_at <= cutoff,
        )
        .values(status=RegionStatus.GRACE)
    )
    result = db.execute(stmt)
    count = result.rowcount or 0
    if count:
        logger.info("region_lifecycle: %d region(s) advanced suspended -> grace", count)
    return {"advanced_to_grace": count}


def advance_to_terminated(db: Session, now: Optional[datetime] = None) -> Dict[str, int]:
    """GRACE -> TERMINATED for every region whose ORIGINAL ``Region.
    suspended_at`` is at least ``SUSPENDED_TO_TERMINATED_DAYS`` in the
    past -- canon measures this window from the original suspension, not
    from entry into grace. Sets ``terminated_at = now`` and
    ``scheduled_hard_delete_at = now + TERMINATED_TO_HARD_DELETE_DAYS`` in
    the SAME bulk UPDATE -- both values are identical across the whole
    matched batch (this call's single ``now``), so no per-row Python touch
    is needed here either.

    Called AFTER ``advance_to_grace`` in the same pass (see the gated
    wrapper): a region overdue enough to have missed a grace-transition
    cron run entirely (e.g. suspended 40 days ago, only just now getting
    swept) correctly catches up through BOTH transitions in one call
    rather than waiting an extra day for terminated -- consistent with
    every other durable-timestamp-driven sweep in this scheduler package,
    where the per-row timestamp is authoritative, not perfect real-time
    cadence."""
    now = now or datetime.now(UTC)
    cutoff = now - timedelta(days=SUSPENDED_TO_TERMINATED_DAYS)
    stmt = (
        update(Region)
        .where(
            Region.status == RegionStatus.GRACE,
            Region.suspended_at.isnot(None),
            Region.suspended_at <= cutoff,
        )
        .values(
            status=RegionStatus.TERMINATED,
            terminated_at=now,
            scheduled_hard_delete_at=now + timedelta(days=TERMINATED_TO_HARD_DELETE_DAYS),
        )
    )
    result = db.execute(stmt)
    count = result.rowcount or 0
    if count:
        logger.info("region_lifecycle: %d region(s) advanced grace -> terminated", count)
    return {"advanced_to_terminated": count}


def dispatch_terminated_cleanup(db: Session, now: Optional[datetime] = None) -> Dict[str, int]:
    """Finds TERMINATED regions past their ``scheduled_hard_delete_at``
    (region-lifecycle.md:293's ``cleanup_orchestrator`` daily-cron trigger
    condition) and dispatches ``region_termination_cascade_service``'s
    reduced-scope cascade (WO-BUILD-REGION-LIFECYCLE-CLEANUP-CASCADE,
    commit bae0abcf) against each eligible region's planets and stations:
    ``process_planet_termination`` per planet (planet-safe transport +
    Genesis compensation), ``dispatch_station_termination`` for the region
    as a whole (still a discovery-only stub there pending the
    acquisition_cost/upgrade-capital blocker documented in that module --
    this dispatch does not change that module's own scope), and
    ``cascade_region_gate_teardown`` (ADR-0052 SK38) to tear down every
    player-built warp gate with an endpoint in the region. ADR-0052 SK38
    states no ordering dependency between the gate cascade and the
    planet/station cascade -- each processes a disjoint entity type -- so
    the gate teardown runs alongside them in the same per-region pass.

    Does NOT stamp ``Region.cleanup_completed_at`` while
    ``dispatch_station_termination`` remains discovery-only
    (WO-ESCALATE-CYCLE26-DESIGN-FLAGS / DECISIONS.md cycle26-design-flags-fix):
    asserting "cleanup complete" while stations are never terminated is a
    data-integrity bug. Planet re-entry is gated by
    ``Planet.termination_compensated_at`` instead; gate teardown is already
    status-flip idempotent. Eligibility still filters
    ``cleanup_completed_at IS NULL`` so a future station-termination
    implementation can stamp the region marker once and stop re-dispatch.

    Flush-only -- caller owns the commit, per this codebase's
    route-owns-commit convention (mirrors both cascade functions below it)."""
    now = now or datetime.now(UTC)
    eligible = (
        db.query(Region)
        .filter(
            Region.status == RegionStatus.TERMINATED,
            Region.scheduled_hard_delete_at.isnot(None),
            Region.scheduled_hard_delete_at <= now,
            Region.cleanup_completed_at.is_(None),
        )
        .all()
    )
    for region in eligible:
        planets = db.query(Planet).filter(Planet.region_id == region.id).all()
        for planet in planets:
            process_planet_termination(db, planet, now=now)
        dispatch_station_termination(db, region.id)
        cascade_region_gate_teardown(db, region.id)
        # Intentionally leave cleanup_completed_at NULL until station
        # termination is real (cycle26-design-flags-fix).
        logger.info(
            "region_lifecycle: dispatched cleanup cascade for region %s "
            "(%d planet(s) processed; cleanup_completed_at deferred — "
            "station termination still discovery-only)",
            region.id, len(planets),
        )
    return {"cleanup_eligible": len(eligible)}
