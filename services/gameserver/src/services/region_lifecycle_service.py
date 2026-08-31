"""Region lifecycle cron -- SUSPENDED -> GRACE -> TERMINATED daily
advancement (WO-P8-region-lifecycle-cron). Schema shipped separately by
P8-region-lifecycle-schema (``RegionStatus`` enum + ``suspended_at`` /
``terminated_at`` / ``scheduled_hard_delete_at`` columns, migration
``b7e4a29f1c68_region_lifecycle_columns.py``, verified present) -- this WO
is additive-only, no new schema.

Timers follow ``SYSTEMS/region-lifecycle.md``: suspended_at + 7d → GRACE,
suspended_at + 30d → TERMINATED (both from the original suspended_at, not
reset on grace), then a 7-day terminated → hard-delete window.

Both advance functions are pure, session-injectable BULK conditional
UPDATEs -- canon's trigger table lists no per-region side effect for
either transition (unlike, say, a takeover), so this mirrors contract_
service.sweep_expired_contracts' bulk-UPDATE shape for the "no per-row
Python touch needed" case rather than a per-row loop. Both are flush-only;
the caller (economy_governance_sweeps._run_region_lifecycle_advance_gated,
Phase 7 of the daily governance sweep) owns the commit.
"""
import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Dict, Optional

from sqlalchemy import exists, or_, update
from sqlalchemy.orm import Session

from src.models.pirate_holding import PirateHolding
from src.models.planet import Planet
from src.models.player import Player
from src.models.region import Region, RegionStatus
from src.models.sector import Sector
from src.models.ship import Ship
from src.models.station import Station
from src.models.warp_gate import WarpGate, WarpGateBeacon
from src.services.realtime_outbox import RealtimeOutbox
from src.services.region_termination_cascade_service import (
    dispatch_station_termination,
    process_planet_termination,
)
from src.services.warp_gate_service import cascade_region_gate_teardown

logger = logging.getLogger(__name__)

# ADR-0054 X-D1 -- suspended-region stakeholder-ingress error contract
# (region-lifecycle.md's "The rule keeps stakeholders connected to their
# assets" paragraph; movement_service surfaces this verbatim, mirroring
# ERR_GALACTIC_CITIZEN_REQUIRED's own string-as-code convention on the
# sibling ADR-0043 Nexus-subscription gate).
ERR_REGION_NEW_RESIDENTS_BLOCKED = "ERR_REGION_NEW_RESIDENTS_BLOCKED"

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
    as a whole (live since PR #563 — Path A/B relocate/charge/lose), and
    ``cascade_region_gate_teardown`` (ADR-0052 SK38) to tear down every
    player-built warp gate with an endpoint in the region. ADR-0052 SK38
    states no ordering dependency between the gate cascade and the
    planet/station cascade -- each processes a disjoint entity type -- so
    the gate teardown runs alongside them in the same per-region pass.

    Stamps ``Region.cleanup_completed_at`` once all three cascades finish
    for a region in this pass (WO-FIX-REGION-CLEANUP-COMPLETED-AT-STAMP /
    DECISIONS.md cycle26-design-flags-fix update 2026-08-16: station
    termination is no longer a stub). Planet re-entry remains gated by
    ``Planet.termination_compensated_at``; gate teardown is status-flip
    idempotent. Eligibility filters ``cleanup_completed_at IS NULL`` so a
    stamped region is not re-dispatched on subsequent ticks.

    Flush-only -- caller owns the commit, per this codebase's
    route-owns-commit convention (mirrors both cascade functions below it).

    ADR-0054 X-V1: accumulates realtime events (currently ``process_planet_
    termination``'s ``region.planet_terminated`` notice) in a per-call
    ``RealtimeOutbox`` rather than emitting them directly, and returns it
    to the caller as ``result["_outbox"]`` -- this function does NOT own
    the commit (Phase 7 of the governance sweep does, one call up via
    ``_run_region_lifecycle_advance_gated``), so it cannot safely flush
    here. The caller must call ``result["_outbox"].flush()`` after ITS
    ``db.commit()`` succeeds, and must NOT call it on the rollback path."""
    now = now or datetime.now(UTC)
    outbox = RealtimeOutbox()
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
            process_planet_termination(db, planet, now=now, outbox=outbox)
        dispatch_station_termination(db, region.id)
        cascade_region_gate_teardown(db, region.id)
        region.cleanup_completed_at = now
        logger.info(
            "region_lifecycle: dispatched cleanup cascade for region %s "
            "(%d planet(s) processed; cleanup_completed_at stamped)",
            region.id, len(planets),
        )
    return {"cleanup_eligible": len(eligible), "_outbox": outbox}


def is_region_stakeholder(db: Session, player_id: uuid.UUID, region_id: uuid.UUID) -> bool:
    """ADR-0054 X-D1 -- one query covering every asset class that makes a
    player a "stakeholder" of ``region_id``: owns a planet, owns a station,
    controls a captured pirate holding (individually OR via their team --
    ``PirateHolding.owner_team_id`` is a TEAM FK, not a player FK, so a
    teammate's capture counts too), owns a player-built warp-gate endpoint
    anchored in the region, or has a registered ship currently sitting in
    one of the region's sectors.

    Ship/WarpGate don't carry ``region_id`` directly (unlike Planet/
    Station/PirateHolding, which do) -- they're keyed by the human-readable
    Integer ``sector_id`` (Ship.sector_id / WarpGateBeacon.source_sector_id
    /destination_sector_id), so those two arms correlate through ``Sector.
    region_id`` instead.

    Implemented as up to five short-circuiting, individually-indexed EXISTS
    checks (each is exactly the "one query per ownership type, indexed by
    player_id" region-lifecycle.md describes) rather than one mega-SELECT
    OR-ing five correlated subqueries together -- the ADR's "one SQL
    query" framing is about avoiding an N+1 loop over the player's assets,
    not mandating a single monolithic statement, and short-circuiting on
    the common case (a stakeholder usually owns exactly one asset type)
    means most calls fire ONE query, not five.

    Region ownership and the post-takeover-commit case are handled by the
    CALLER (movement_service._check_region_ingress_gate), not here --
    ``Region.owner_id`` isn't an asset-ownership row this query touches,
    and a just-committed takeover has already flipped ``Region.status`` to
    ACTIVE by the time any traversal check runs, so the gate never reaches
    this function for that case in the first place.
    """
    if (
        db.query(exists().where(
            Planet.owner_id == player_id, Planet.region_id == region_id,
        )).scalar()
    ):
        return True

    if (
        db.query(exists().where(
            Station.owner_id == player_id, Station.region_id == region_id,
        )).scalar()
    ):
        return True

    player = db.query(Player).filter(Player.id == player_id).first()
    team_id = player.team_id if player is not None else None
    holding_conditions = [PirateHolding.owner_player_id == player_id]
    if team_id is not None:
        holding_conditions.append(PirateHolding.owner_team_id == team_id)
    if (
        db.query(exists().where(
            or_(*holding_conditions), PirateHolding.region_id == region_id,
        )).scalar()
    ):
        return True

    if (
        db.query(exists().where(
            WarpGate.player_id == player_id,
            WarpGate.beacon_id == WarpGateBeacon.id,
            or_(
                exists().where(
                    Sector.sector_id == WarpGateBeacon.source_sector_id,
                    Sector.region_id == region_id,
                ),
                exists().where(
                    Sector.sector_id == WarpGateBeacon.destination_sector_id,
                    Sector.region_id == region_id,
                ),
            ),
        )).scalar()
    ):
        return True

    if (
        db.query(exists().where(
            or_(Ship.owner_id == player_id, Ship.registered_owner_id == player_id),
            Sector.sector_id == Ship.sector_id,
            Sector.region_id == region_id,
        )).scalar()
    ):
        return True

    return False
