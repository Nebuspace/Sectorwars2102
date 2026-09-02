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
from typing import Any, Dict, Optional

from sqlalchemy import exists, or_, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from src.models.pirate_holding import PirateHolding
from src.models.planet import Planet
from src.models.player import Player
from src.models.region import Region, RegionStatus, RegionType
from src.models.takeover_intent import TakeoverIntent, TakeoverIntentStatus
from src.models.sector import Sector
from src.models.ship import Ship
from src.models.station import Station
from src.models.warp_gate import WarpGate, WarpGateBeacon
from src.services.realtime_outbox import RealtimeOutbox
from src.services.region_termination_cascade_service import (
    dispatch_station_termination,
    process_planet_termination,
)
from src.services.paypal_service import paypal_service
from src.services.scheduler._common import region_lock_key
from src.services.warp_gate_service import cascade_region_gate_teardown

logger = logging.getLogger(__name__)

# Region GC-subscription takeover (LEG-3764 / SYSTEMS/region-lifecycle.md).
ERR_REGION_NOT_AVAILABLE_FOR_TAKEOVER = "ERR_REGION_NOT_AVAILABLE_FOR_TAKEOVER"
ERR_GALACTIC_CITIZEN_REQUIRED = "ERR_GALACTIC_CITIZEN_REQUIRED"
ERR_ONE_REGION_PER_OWNER = "ERR_ONE_REGION_PER_OWNER"
ERR_REGION_NOT_FOUND = "ERR_REGION_NOT_FOUND"
ERR_TAKEOVER_INTENT_PENDING = "ERR_TAKEOVER_INTENT_PENDING"
TAKEOVER_PAYPAL_FLOW_HOURS = 1

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


class AdminRegionTerminationError(Exception):
    """Business-rule rejection for admin manual region termination."""

    def __init__(self, code: str, detail: str) -> None:
        self.code = code
        self.detail = detail
        super().__init__(detail)


def admin_region_terminate_preview(db: Session, region_id: uuid.UUID) -> Dict[str, Any]:
    """Dependent-entity counts for LEG-DEC-103 multi-step confirmation."""
    region = db.query(Region).filter(Region.id == region_id).first()
    if region is None:
        raise AdminRegionTerminationError("not_found", "Region not found")

    planet_count = db.query(Planet).filter(Planet.region_id == region.id).count()
    station_count = (
        db.query(Station)
        .join(Sector, Sector.id == Station.sector_uuid)
        .filter(Sector.region_id == region.id)
        .filter(Station.is_destroyed.is_(False))
        .count()
    )
    sector_count = db.query(Sector).filter(Sector.region_id == region.id).count()
    planet_owners = (
        db.query(Planet.owner_id)
        .filter(Planet.region_id == region.id, Planet.owner_id.isnot(None))
        .distinct()
        .count()
    )
    station_owners = (
        db.query(Station.owner_id)
        .join(Sector, Sector.id == Station.sector_uuid)
        .filter(Sector.region_id == region.id, Station.owner_id.isnot(None))
        .filter(Station.is_destroyed.is_(False))
        .distinct()
        .count()
    )

    return {
        "regionId": str(region.id),
        "regionName": region.name,
        "displayName": region.display_name,
        "status": region.status,
        "regionType": region.region_type,
        "planetCount": planet_count,
        "stationCount": station_count,
        "sectorCount": sector_count,
        "playerStakeholderCount": planet_owners + station_owners,
        "terminable": region.region_type == RegionType.PLAYER_OWNED.value
        and region.cleanup_completed_at is None,
    }


def admin_execute_region_termination(
    db: Session,
    region_id: uuid.UUID,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Admin manual region termination (LEG-DEC-103 / LEG-3205).

    Marks the region TERMINATED and runs the full cleanup cascade atomically
    within the caller's session. The route wraps this with
    ``admin_action_attempt`` for commit + AdminActionLog.
    """
    now = now or datetime.now(UTC)
    region = (
        db.query(Region)
        .filter(Region.id == region_id)
        .with_for_update()
        .first()
    )
    if region is None:
        raise AdminRegionTerminationError("not_found", "Region not found")

    if region.region_type in (
        RegionType.CENTRAL_NEXUS.value,
        RegionType.TERRAN_SPACE.value,
    ):
        raise AdminRegionTerminationError(
            "system_region",
            "System regions (Central Nexus, Terran Space) cannot be terminated",
        )

    if region.cleanup_completed_at is not None:
        raise AdminRegionTerminationError(
            "already_terminated",
            "Region cleanup already completed",
        )

    planets = db.query(Planet).filter(Planet.region_id == region.id).all()
    region.status = RegionStatus.TERMINATED
    region.terminated_at = now
    region.scheduled_hard_delete_at = now

    outbox = RealtimeOutbox()
    for planet in planets:
        process_planet_termination(db, planet, now=now, outbox=outbox)
    station_result = dispatch_station_termination(db, region.id)
    cascade_region_gate_teardown(db, region.id)
    region.cleanup_completed_at = now

    return {
        "regionId": str(region.id),
        "regionName": region.name,
        "planetsProcessed": len(planets),
        "stationCascade": station_result,
        "_outbox": outbox,
    }


def _serialize_takeover_eligible_region(region: Region) -> Dict[str, Any]:
    """JSON-safe region summary for takeover discovery (LEG-3956)."""
    return {
        "id": str(region.id),
        "name": region.name,
        "display_name": region.display_name,
        "status": region.status,
        "suspended_at": region.suspended_at.isoformat() if region.suspended_at else None,
    }


async def list_takeover_eligible_regions(db: AsyncSession) -> list[Dict[str, Any]]:
    """Return suspended/grace regions available for GC-subscription takeover."""
    result = await db.execute(
        select(Region)
        .where(
            Region.status.in_(
                (RegionStatus.SUSPENDED.value, RegionStatus.GRACE.value),
            ),
        )
        .order_by(Region.name)
    )
    return [_serialize_takeover_eligible_region(r) for r in result.scalars().all()]


def _serialize_takeover_intent(intent: TakeoverIntent) -> Dict[str, Any]:
    """JSON-safe TakeoverIntent payload for the route response."""
    return {
        "id": str(intent.id),
        "region_id": str(intent.region_id),
        "caller_user_id": str(intent.caller_user_id),
        "approval_url": intent.approval_url,
        "status": intent.status,
        "created_at": intent.created_at.isoformat() if intent.created_at else None,
        "expires_at": intent.expires_at.isoformat() if intent.expires_at else None,
        "completed_at": intent.completed_at.isoformat() if intent.completed_at else None,
    }


async def execute_takeover(
    db: AsyncSession,
    *,
    region_id: uuid.UUID,
    caller_user_id: uuid.UUID,
    return_url: str,
    cancel_url: str,
) -> Dict[str, Any]:
    """Initiate region GC-subscription takeover for a suspended/grace region.

    Canon: SYSTEMS/region-lifecycle.md § Takeover endpoint (LEG-3764 slice 2).
    Serializes concurrent claims via per-region advisory lock + ``SELECT FOR
    UPDATE`` on the ``Region`` row before recording a ``TakeoverIntent``.
    PayPal activation / ownership commit is a later webhook slice.
    """
    now = datetime.now(UTC)

    await db.execute(
        text("SELECT pg_advisory_xact_lock(:key)"),
        {"key": region_lock_key(region_id)},
    )

    region_result = await db.execute(
        select(Region).where(Region.id == region_id).with_for_update()
    )
    region = region_result.scalar_one_or_none()
    if region is None:
        return {"ok": False, "code": ERR_REGION_NOT_FOUND}

    if region.status not in (RegionStatus.SUSPENDED.value, RegionStatus.GRACE.value):
        return {"ok": False, "code": ERR_REGION_NOT_AVAILABLE_FOR_TAKEOVER}

    player_result = await db.execute(
        select(Player).where(Player.user_id == caller_user_id)
    )
    player = player_result.scalar_one_or_none()
    if player is None or not player.is_galactic_citizen:
        return {"ok": False, "code": ERR_GALACTIC_CITIZEN_REQUIRED}

    owned_region_result = await db.execute(
        select(Region.id).where(Region.owner_id == caller_user_id).limit(1)
    )
    if owned_region_result.scalar_one_or_none() is not None:
        return {"ok": False, "code": ERR_ONE_REGION_PER_OWNER}

    pending_result = await db.execute(
        select(TakeoverIntent.id).where(
            TakeoverIntent.region_id == region_id,
            TakeoverIntent.caller_user_id == caller_user_id,
            TakeoverIntent.status == TakeoverIntentStatus.PENDING.value,
            TakeoverIntent.expires_at > now,
        ).limit(1)
    )
    if pending_result.scalar_one_or_none() is not None:
        return {"ok": False, "code": ERR_TAKEOVER_INTENT_PENDING}

    intent = TakeoverIntent(
        region_id=region_id,
        caller_user_id=caller_user_id,
        approval_url=return_url,
        status=TakeoverIntentStatus.PENDING.value,
        expires_at=now + timedelta(hours=TAKEOVER_PAYPAL_FLOW_HOURS),
    )
    db.add(intent)
    await db.flush()

    paypal_result = await paypal_service.create_regional_ownership_subscription(
        user_id=str(caller_user_id),
        region_name=region.name,
        return_url=return_url,
        cancel_url=cancel_url,
        takeover_intent_id=str(intent.id),
    )
    approval_url = next(
        (link["href"] for link in paypal_result.get("links", []) if link.get("rel") == "approve"),
        None,
    )
    if not approval_url:
        logger.error(
            "PayPal regional subscription for region %s missing approval link",
            region.name,
        )
        return {"ok": False, "code": "ERR_PAYPAL_APPROVAL_URL_MISSING"}

    intent.approval_url = approval_url
    await db.flush()

    return {
        "ok": True,
        "intent": intent,
        "takeover_intent": _serialize_takeover_intent(intent),
        "subscription_id": paypal_result.get("id"),
    }


async def _mark_takeover_intent_lost(
    intent: TakeoverIntent,
    *,
    now: datetime,
    refund_subscription_id: Optional[str] = None,
) -> None:
    """Mark a takeover intent lost and best-effort refund its PayPal subscription."""
    intent.status = TakeoverIntentStatus.LOST.value
    intent.completed_at = now
    if refund_subscription_id:
        await paypal_service.refund_subscription(refund_subscription_id)


async def commit_takeover(
    db: AsyncSession,
    *,
    takeover_intent_id: uuid.UUID | str,
    paypal_subscription_id: str,
) -> Dict[str, Any]:
    """Commit region ownership after PayPal ACTIVATED for a takeover intent.

    Canon: ``SYSTEMS/region-lifecycle.md`` § Takeover endpoint (LEG-3775 slice 3).
    Serializes via per-region advisory lock + ``SELECT FOR UPDATE`` on the
    ``Region`` row. Losers are marked ``lost`` in the same transaction as the
    winner's ``transferred`` commit; PayPal refunds run for the activating
    subscription when the region is no longer takeover-eligible.
    """
    if isinstance(takeover_intent_id, str):
        takeover_intent_id = uuid.UUID(takeover_intent_id)

    now = datetime.now(UTC)

    intent_result = await db.execute(
        select(TakeoverIntent)
        .where(TakeoverIntent.id == takeover_intent_id)
        .with_for_update()
    )
    intent = intent_result.scalar_one_or_none()
    if intent is None:
        return {"ok": False, "code": "ERR_TAKEOVER_INTENT_NOT_FOUND"}

    if intent.status == TakeoverIntentStatus.TRANSFERRED.value:
        return {"ok": True, "code": "ALREADY_TRANSFERRED", "intent_id": str(intent.id)}

    if intent.status == TakeoverIntentStatus.EXPIRED.value:
        if paypal_subscription_id:
            await paypal_service.refund_subscription(paypal_subscription_id)
        return {
            "ok": False,
            "code": "ERR_TAKEOVER_INTENT_EXPIRED",
            "intent_id": str(intent.id),
        }

    if intent.status != TakeoverIntentStatus.PENDING.value:
        return {"ok": False, "code": "ERR_TAKEOVER_INTENT_TERMINAL", "status": intent.status}

    await db.execute(
        text("SELECT pg_advisory_xact_lock(:key)"),
        {"key": region_lock_key(intent.region_id)},
    )

    region_result = await db.execute(
        select(Region).where(Region.id == intent.region_id).with_for_update()
    )
    region = region_result.scalar_one_or_none()
    if region is None:
        await _mark_takeover_intent_lost(intent, now=now, refund_subscription_id=paypal_subscription_id)
        return {"ok": False, "code": ERR_REGION_NOT_FOUND}

    transferred_exists = await db.execute(
        select(TakeoverIntent.id).where(
            TakeoverIntent.region_id == intent.region_id,
            TakeoverIntent.status == TakeoverIntentStatus.TRANSFERRED.value,
        ).limit(1)
    )
    if transferred_exists.scalar_one_or_none() is not None:
        await _mark_takeover_intent_lost(intent, now=now, refund_subscription_id=paypal_subscription_id)
        return {"ok": False, "code": "ERR_TAKEOVER_LOST"}

    if region.status not in (RegionStatus.SUSPENDED.value, RegionStatus.GRACE.value):
        await _mark_takeover_intent_lost(intent, now=now, refund_subscription_id=paypal_subscription_id)
        return {"ok": False, "code": ERR_REGION_NOT_AVAILABLE_FOR_TAKEOVER}

    if intent.expires_at and intent.expires_at < now:
        await _mark_takeover_intent_lost(intent, now=now, refund_subscription_id=paypal_subscription_id)
        return {"ok": False, "code": "ERR_TAKEOVER_INTENT_EXPIRED"}

    old_owner_id = region.owner_id
    region.owner_id = intent.caller_user_id
    region.paypal_subscription_id = paypal_subscription_id
    region.status = RegionStatus.ACTIVE.value
    region.subscription_status = "active"
    region.suspended_at = None
    region.terminated_at = None
    region.scheduled_hard_delete_at = None

    intent.status = TakeoverIntentStatus.TRANSFERRED.value
    intent.completed_at = now

    losers_result = await db.execute(
        select(TakeoverIntent).where(
            TakeoverIntent.region_id == intent.region_id,
            TakeoverIntent.id != intent.id,
            TakeoverIntent.status == TakeoverIntentStatus.PENDING.value,
        ).with_for_update()
    )
    for loser in losers_result.scalars().all():
        loser.status = TakeoverIntentStatus.LOST.value
        loser.completed_at = now

    logger.info(
        "region takeover committed: region=%s new_owner=%s old_owner=%s intent=%s",
        region.id,
        intent.caller_user_id,
        old_owner_id,
        intent.id,
    )

    return {
        "ok": True,
        "region_id": str(region.id),
        "intent_id": str(intent.id),
        "old_owner_id": str(old_owner_id) if old_owner_id else None,
        "new_owner_id": str(intent.caller_user_id),
    }


def sweep_expired_takeover_intents(
    db: Session,
    *,
    now: Optional[datetime] = None,
) -> Dict[str, int]:
    """Mark overdue pending takeover intents ``expired`` (LEG-3791 slice 4).

    Canon: ``SYSTEMS/region-lifecycle.md`` failure modes — periodic sweep
    marks intents whose PayPal flow window elapsed; a late
    ``BILLING.SUBSCRIPTION.ACTIVATED`` callback refunds via
    ``commit_takeover`` when the intent is already ``expired``.

    FLUSH only — the governance-sweep caller owns commit.
    """
    now = now or datetime.now(UTC)
    overdue = (
        db.query(TakeoverIntent)
        .filter(
            TakeoverIntent.status == TakeoverIntentStatus.PENDING.value,
            TakeoverIntent.expires_at < now,
        )
        .all()
    )
    for intent in overdue:
        intent.status = TakeoverIntentStatus.EXPIRED.value
        intent.completed_at = now

    if overdue:
        db.flush()
        logger.info(
            "region_lifecycle: %d takeover intent(s) marked expired by sweep",
            len(overdue),
        )
    return {"expired": len(overdue)}
