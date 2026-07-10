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
from datetime import datetime, timedelta, UTC
from typing import Dict, Optional

from sqlalchemy import update
from sqlalchemy.orm import Session

from src.models.region import Region, RegionStatus

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
    """DISCOVERY STUB (WO-P8 lane c), NOT a cleanup implementation. Finds
    TERMINATED regions past their ``scheduled_hard_delete_at`` (region-
    lifecycle.md:293's ``cleanup_orchestrator`` daily-cron trigger
    condition) and logs them as eligible -- the discoverable dispatch
    POINT gate-cascade (W12, ruled GO) wires the real cascade onto when it
    lands. Deliberately does NOTHING destructive: no ship evacuation,
    station relocation, planet-safe Bank transfer, or ``hard_delete_
    region`` call.

    Canon's ``cleanup_started_at`` / ``cleanup_completed_at`` tracking
    columns (region-lifecycle.md:303 pseudocode) are NOT in the shipped
    schema -- P8-region-lifecycle-schema's migration only added
    ``suspended_at`` / ``terminated_at`` / ``scheduled_hard_delete_at``.
    Adding them would be a NEW schema change outside this WO's additive-
    only scope, so this stub can only detect ELIGIBILITY, not track an
    in-progress/completed cleanup state across ticks (idempotent by
    construction: re-finding the same eligible region every day until
    gate-cascade actually processes it is harmless — a read-only re-log,
    not a re-charge or re-mutation). Read-only; never writes."""
    now = now or datetime.now(UTC)
    eligible = (
        db.query(Region.id, Region.name)
        .filter(
            Region.status == RegionStatus.TERMINATED,
            Region.scheduled_hard_delete_at.isnot(None),
            Region.scheduled_hard_delete_at <= now,
        )
        .all()
    )
    if eligible:
        logger.info(
            "region_lifecycle: %d region(s) eligible for cleanup cascade "
            "(gate-cascade dispatch not yet wired -- discovery only)",
            len(eligible),
        )
    return {"cleanup_eligible": len(eligible)}
