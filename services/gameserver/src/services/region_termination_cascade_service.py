"""Region-termination cascade (ADR-0050 asset disposition).

Built:
- **Planet-safe transport** (20% loss, or 100% via ``Planet.transport_prepaid``)
  + **Genesis-device compensation** (citadel-level table).
- **Station relocation** (WO-BUILD-REGION-TERMINATION-STATION-CASCADE): Path A
  automatic fee debit (treasury → wallet → strip capital-ledger upgrades →
  lose+Bank compensation) and Path B ``relocation_prepaid`` intact move.
  Fee = ``port_ownership_service.relocation_fee`` (30% of acquisition +
  capital ledger). Loss compensation deposits via
  ``apply_station_loss_compensation`` → Central Bank — never ``Player.credits``.

Surviving safe credits + commodity stacks deposit into
``PlayerCentralBankAccount`` (ADR-0050 / WO-BUILD-PLAYER-CENTRAL-BANK-
ACCOUNT). Genesis credit compensation still credits ``Player.credits``
(ADR-0050: partial-loss buffer into the wallet, not the Bank).

The FORFEITED 20% (or 0% if prepaid) is never credited anywhere -- recorded
as an ``AuditService`` entry (action=FORFEIT,
resource_type="planet_safe_forfeiture") tied to the planet/region.

FLAG COLUMN PLACEMENT -- ``transport_prepaid`` / ``relocation_prepaid`` live
on ``Planet`` / ``Station`` (migration c4d8e61f97ab), not ``Region`` —
ADR-0050 ties pre-pay to a specific asset.

Path C (manual disassembly) stays design-only / out of scope.
"""
import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from src.models.genesis_device import GenesisDevice, GenesisStatus, GenesisType
from src.models.planet import Planet
from src.models.player import Player
from src.models.region import Region, RegionType
from src.models.sector import Sector
from src.models.station import Station, StationStatus
from src.services import central_bank_service as bank
from src.services.audit_service import AuditAction, AuditService
from src.services.citadel_service import CitadelService
from src.services.port_ownership_service import (
    _acquisition_cost,
    _station_revenue,
    relocation_fee,
)
from src.services.realtime_outbox import RealtimeOutbox

# ADR-0050 re-anchor defaults after a successful relocate.
_RELOCATE_TAX_RATE = 0.05
_RELOCATE_SECURITY = {"tier": "basic"}
# Path A final-fallback compensation: 50% of acquisition + trailing 30d revenue.
_LOSS_ACQUISITION_PCT = 0.50
_LOSS_REVENUE_DAYS = 30

logger = logging.getLogger(__name__)

# ADR-0050 "Planet-safe transport paths" A: 20% loss on both credits and
# each commodity stack, rounded down to whole units.
SAFE_TRANSPORT_LOSS_PCT = 20

# ADR-0050 "Planet-loss compensation (citadel level -> reward)" table.
# GenesisType.STANDARD == "Basic terraforming" (see genesis_device.py:11),
# GenesisType.ADVANCED == "Advanced multi-phase terraforming" (:14) --
# the closest existing enum members to the ADR's "Basic" / "Advanced" tiers.
PLANET_LOSS_COMPENSATION: Dict[int, Dict[str, Any]] = {
    1: {"devices": [GenesisType.STANDARD], "credits": 50_000},
    2: {"devices": [GenesisType.STANDARD, GenesisType.ADVANCED], "credits": 250_000},
    3: {"devices": [GenesisType.ADVANCED, GenesisType.ADVANCED], "credits": 1_000_000},
    4: {"devices": [GenesisType.ADVANCED] * 3, "credits": 5_000_000},
    5: {"devices": [GenesisType.ADVANCED] * 5, "credits": 25_000_000},
}


def _mint_genesis_compensation(db: Session, owner_id: uuid.UUID, device_type: GenesisType) -> GenesisDevice:
    """Creates one INACTIVE, undeployed GenesisDevice owned by ``owner_id`` --
    ADR-0050:131 "they can land in the Central Nexus ... and seed a new
    colony with the recovered devices" describes a portable, not-yet-
    deployed device, matching GenesisStatus.INACTIVE (":not yet deployed").
    """
    device = GenesisDevice(
        name=f"Cascade Compensation Genesis Device ({device_type.value})",
        serial_number=f"CASCADE-{uuid.uuid4().hex[:20].upper()}",
        type=device_type,
        status=GenesisStatus.INACTIVE,
        owner_id=owner_id,
    )
    db.add(device)
    return device


def process_planet_termination(
    db: Session, planet: Planet, now: Optional[datetime] = None,
    outbox: Optional[RealtimeOutbox] = None,
) -> Dict[str, Any]:
    """ADR-0050 "Player-owned planet" disposition, planet-safe-transport +
    Genesis-compensation slice only (see module docstring for the full-scope
    blocker and gap-fallbacks). Flush-only -- caller owns the commit, per
    this codebase's route-owns-commit convention.

    An orphaned planet (``owner_id is None``) has nothing to compensate --
    logged and returned early, mirroring warp_gate_service.
    cascade_region_gate_teardown's own orphaned-owner handling (point 4 of
    its docstring: WARNed, never raised, region terminates regardless).

    ``outbox`` (ADR-0054 X-V1) queues a ``region.planet_terminated``
    personal event for the owner instead of emitting directly -- the caller
    (``dispatch_terminated_cleanup`` -> Phase 7 of the governance sweep)
    flushes it ONLY after that phase's own ``db.commit()`` succeeds, so a
    rollback (e.g. Genesis mint or Bank deposit failing mid-cascade) never
    leaves a ghost "your planet was terminated" notification for data that
    was never persisted. ``outbox=None`` (e.g. a direct unit-test call) is
    a no-op -- queuing is best-effort instrumentation, never load-bearing.
    """
    now = now or datetime.now(UTC)
    audit = AuditService(db)
    result: Dict[str, Any] = {
        "planet_id": str(planet.id),
        "owner_id": str(planet.owner_id) if planet.owner_id else None,
        "credited_credits": 0,
        "forfeited_credits": 0,
        "forfeited_commodities": {},
        "genesis_devices_minted": 0,
        "genesis_credit_compensation": 0,
    }

    # Idempotency (WO-ESCALATE-CYCLE26-DESIGN-FLAGS): a prior successful pass
    # stamps termination_compensated_at so daily re-entry while region
    # cleanup_completed_at stays null cannot re-mint Genesis / re-bank.
    if getattr(planet, "termination_compensated_at", None) is not None:
        result["skipped"] = "already_compensated"
        return result

    if planet.owner_id is None:
        logger.warning(
            "region_termination_cascade: planet %s has no owner; safe/genesis "
            "compensation skipped, forfeiting in place", planet.id,
        )
        audit.log_action(
            user_id=None,
            action=AuditAction.FORFEIT,
            resource_type="planet_safe_forfeiture",
            resource_id=str(planet.id),
            details={"reason": "orphaned_planet", "region_id": str(planet.region_id) if planet.region_id else None},
        )
        planet.termination_compensated_at = now
        return result

    # ADR-0054 X-I2: lock the owner's Player row before any wallet read/
    # compute/mutate below (Genesis credit compensation debits/credits
    # owner.credits directly further down) -- serializes this cascade
    # against any concurrent player action (trade, ARIA dialogue, etc.)
    # touching the same wallet. This is the first read of this Player row
    # in this function/session, so a plain .with_for_update() (no
    # .populate_existing() needed) is sufficient.
    owner = (
        db.query(Player)
        .filter(Player.id == planet.owner_id)
        .with_for_update()
        .first()
    )
    if owner is None:
        logger.warning(
            "region_termination_cascade: planet %s owner_id %s has no Player "
            "row; safe/genesis compensation skipped", planet.id, planet.owner_id,
        )
        audit.log_action(
            user_id=None,
            action=AuditAction.FORFEIT,
            resource_type="planet_safe_forfeiture",
            resource_id=str(planet.id),
            details={"reason": "missing_player_row", "owner_id": str(planet.owner_id)},
        )
        planet.termination_compensated_at = now
        return result

    citadel = CitadelService(db)
    safe_credits = int(getattr(planet, "citadel_safe_credits", 0) or 0)
    safe_commodities = citadel._get_safe_commodities(planet)
    prepaid = bool(getattr(planet, "transport_prepaid", False))

    if prepaid:
        surviving_credits = safe_credits
        surviving_commodities = dict(safe_commodities)
        forfeited_credits = 0
        forfeited_commodities: Dict[str, int] = {}
    else:
        surviving_credits = safe_credits * (100 - SAFE_TRANSPORT_LOSS_PCT) // 100
        forfeited_credits = safe_credits - surviving_credits
        surviving_commodities = {}
        forfeited_commodities = {}
        for commodity, qty in safe_commodities.items():
            qty = int(qty)
            kept = qty * (100 - SAFE_TRANSPORT_LOSS_PCT) // 100
            surviving_commodities[commodity] = kept
            lost = qty - kept
            if lost:
                forfeited_commodities[commodity] = lost

    # Surviving safe → Central Bank (credits + commodity stacks). Canon
    # deposits commodities as commodities — no credit-equivalent liquidation.
    source = f"planet:{planet.id}"
    if surviving_credits:
        bank.deposit_credits(
            db,
            owner.id,
            surviving_credits,
            entry_type=bank.ENTRY_CASCADE_SAFE_TRANSFER,
            source=source,
            notes="planet_safe_transport" + ("_prepaid" if prepaid else "_20pct_loss"),
            access_override=True,
        )
    surviving_commodities_kept = {
        k: int(v) for k, v in surviving_commodities.items() if int(v) > 0
    }
    if surviving_commodities_kept:
        bank.deposit_commodities(
            db,
            owner.id,
            surviving_commodities_kept,
            entry_type=bank.ENTRY_CASCADE_SAFE_TRANSFER,
            source=source,
            notes="planet_safe_commodities" + ("_prepaid" if prepaid else "_20pct_loss"),
            access_override=True,
        )
    credited_total = surviving_credits

    # Drain the safe now that its surviving value has been banked -- the
    # planet (and its safe) is lost with the region, so nothing should be
    # left behind for a re-run of this function to double-credit.
    planet.citadel_safe_credits = 0
    citadel._set_safe_commodities(planet, {})

    if forfeited_credits or forfeited_commodities:
        audit.log_action(
            user_id=owner.id,
            action=AuditAction.FORFEIT,
            resource_type="planet_safe_forfeiture",
            resource_id=str(planet.id),
            details={
                "region_id": str(planet.region_id) if planet.region_id else None,
                "prepaid": prepaid,
                "forfeited_credits": forfeited_credits,
                "forfeited_commodities": forfeited_commodities,
            },
        )

    citadel_level = int(getattr(planet, "citadel_level", 0) or 0)
    compensation = PLANET_LOSS_COMPENSATION.get(citadel_level)
    genesis_ids: List[str] = []
    genesis_credit_compensation = 0
    if compensation is not None:
        for device_type in compensation["devices"]:
            device = _mint_genesis_compensation(db, owner.id, device_type)
            db.flush([device])
            genesis_ids.append(str(device.id))
        genesis_credit_compensation = compensation["credits"]
        owner.credits = (owner.credits or 0) + genesis_credit_compensation

    result.update({
        "credited_credits": credited_total,
        "bank_credits": surviving_credits,
        "bank_commodities": surviving_commodities_kept,
        "forfeited_credits": forfeited_credits,
        "forfeited_commodities": forfeited_commodities,
        "genesis_devices_minted": len(genesis_ids),
        "genesis_device_ids": genesis_ids,
        "genesis_credit_compensation": genesis_credit_compensation,
    })
    planet.termination_compensated_at = now
    logger.info(
        "region_termination_cascade: planet %s terminated -- owner %s banked "
        "%d credits + %s commodities (safe) + %d (genesis wallet) credits, "
        "%d genesis device(s) minted, %d credits / %s commodities forfeited",
        planet.id, owner.id, surviving_credits, surviving_commodities_kept,
        genesis_credit_compensation, len(genesis_ids), forfeited_credits,
        forfeited_commodities,
    )
    if outbox is not None:
        outbox.queue_personal(
            "region.planet_terminated",
            {
                "planet_id": str(planet.id),
                "region_id": str(planet.region_id) if planet.region_id else None,
                "bank_credits": surviving_credits,
                "bank_commodities": surviving_commodities_kept,
                "forfeited_credits": forfeited_credits,
                "forfeited_commodities": forfeited_commodities,
                "genesis_devices_minted": len(genesis_ids),
                "genesis_credit_compensation": genesis_credit_compensation,
            },
            owner.user_id,
        )
    return result


def apply_station_loss_compensation(
    db: Session,
    player_id: uuid.UUID,
    amount: int,
    *,
    station_id: Optional[uuid.UUID] = None,
    notes: Optional[str] = None,
) -> Any:
    """Path-A station-loss compensation → Central Bank (ADR-0050).

    Never deposits into ``Player.credits`` — Bank only.
    """
    return bank.pay_station_loss_compensation(
        db, player_id, amount, station_id=station_id, notes=notes,
    )


def _pick_central_nexus_sector(db: Session) -> Optional[Sector]:
    """Default relocation destination: any sector in the Central Nexus region."""
    nexus = (
        db.query(Region)
        .filter(Region.region_type == RegionType.CENTRAL_NEXUS.value)
        .first()
    )
    if nexus is None:
        # Enum compare fallback (some sessions store enum members)
        nexus = (
            db.query(Region)
            .filter(Region.region_type == RegionType.CENTRAL_NEXUS)
            .first()
        )
    if nexus is None:
        return None
    return (
        db.query(Sector)
        .filter(Sector.region_id == nexus.id)
        .order_by(Sector.sector_id.asc())
        .first()
    )


def _flag_json(station: Station, key: str) -> None:
    """flag_modified for real ORM rows; no-op for unit-test SimpleNamespace."""
    if getattr(station, "_sa_instance_state", None) is not None:
        flag_modified(station, key)


def _strip_highest_capital_upgrade(station: Station) -> Optional[int]:
    """Remove the highest-amount capital_cost_ledger entry. Returns amount stripped."""
    ledger = list(station.capital_cost_ledger or [])
    if not ledger:
        return None
    best_i = None
    best_amt = -1
    for i, entry in enumerate(ledger):
        if not isinstance(entry, dict):
            continue
        amt = int(entry.get("amount", 0) or 0)
        if amt > best_amt:
            best_amt = amt
            best_i = i
    if best_i is None or best_amt <= 0:
        return None
    ledger.pop(best_i)
    station.capital_cost_ledger = ledger
    _flag_json(station, "capital_cost_ledger")
    return best_amt


def _debit_relocation_fee(station: Station, owner: Player, fee: int) -> bool:
    """Debit ``fee`` from station treasury then owner wallet. Returns False if short."""
    if fee <= 0:
        return True
    treasury = int(station.treasury_balance or 0)
    if treasury >= fee:
        station.treasury_balance = treasury - fee
        return True
    deficit = fee - treasury
    wallet = int(owner.credits or 0)
    if wallet < deficit:
        return False
    station.treasury_balance = 0
    owner.credits = wallet - deficit
    return True


def _execute_station_relocate(station: Station, dest: Sector) -> None:
    """Re-anchor station to destination sector; reset security + tariffs."""
    station.sector_id = dest.sector_id
    station.sector_uuid = dest.id
    station.region_id = dest.region_id
    station.security = dict(_RELOCATE_SECURITY)
    _flag_json(station, "security")
    station.tax_rate = _RELOCATE_TAX_RATE
    station.relocation_prepaid = False


def _loss_compensation_amount(db: Session, station: Station, now: datetime) -> int:
    acq = _acquisition_cost(station)
    since = now - timedelta(days=_LOSS_REVENUE_DAYS)
    trailing = _station_revenue(db, station.id, since, until=now)
    return int(acq * _LOSS_ACQUISITION_PCT) + int(trailing or 0)


def _lose_station(
    db: Session, station: Station, owner: Player, now: datetime,
) -> Dict[str, Any]:
    amount = _loss_compensation_amount(db, station, now)
    if amount > 0:
        apply_station_loss_compensation(
            db, owner.id, amount, station_id=station.id,
            notes=f"cascade_station_loss region-termination station={station.id}",
        )
    station.is_destroyed = True
    station.status = StationStatus.ABANDONED
    station.owner_id = None
    station.relocation_prepaid = False
    return {
        "station_id": str(station.id),
        "outcome": "lost",
        "compensation": amount,
        "owner_id": str(owner.id),
    }


def _relocate_one_station(
    db: Session,
    station: Station,
    dest: Sector,
    now: datetime,
) -> Dict[str, Any]:
    """Path A/B for a single player-owned station. Caller holds locks."""
    owner_id = station.owner_id
    if owner_id is None:
        return {"station_id": str(station.id), "outcome": "skipped", "reason": "no_owner"}

    owner = (
        db.query(Player)
        .filter(Player.id == owner_id)
        .populate_existing()
        .with_for_update()
        .first()
    )
    if owner is None:
        return {"station_id": str(station.id), "outcome": "skipped", "reason": "owner_missing"}

    # Path B — fee already paid during grace
    if station.relocation_prepaid:
        _execute_station_relocate(station, dest)
        return {
            "station_id": str(station.id),
            "outcome": "relocated",
            "path": "B",
            "fee_paid": 0,
            "owner_id": str(owner.id),
            "dest_sector_id": dest.sector_id,
        }

    # Path A — pay fee (strip upgrades / recompute fee until covered or lose)
    while True:
        fee = relocation_fee(station)
        if _debit_relocation_fee(station, owner, fee):
            _execute_station_relocate(station, dest)
            return {
                "station_id": str(station.id),
                "outcome": "relocated",
                "path": "A",
                "fee_paid": fee,
                "owner_id": str(owner.id),
                "dest_sector_id": dest.sector_id,
            }
        stripped = _strip_highest_capital_upgrade(station)
        if stripped is None:
            return _lose_station(db, station, owner, now)


def dispatch_station_termination(db: Session, region_id: uuid.UUID) -> Dict[str, Any]:
    """ADR-0050 station relocation cascade for every player-owned station in
    ``region_id``. Flush-only — caller owns the commit.

    Path B (``relocation_prepaid``): relocate intact, no fee debit.
    Path A: treasury → wallet → strip highest capital upgrades (fee
    recomputed) → lose + Central Bank compensation.
    Destination defaults to Central Nexus (first sector by ``sector_id``).
    """
    now = datetime.now(UTC)
    stations = (
        db.query(Station)
        .join(Sector, Sector.id == Station.sector_uuid)
        .filter(Sector.region_id == region_id)
        .filter(Station.owner_id.isnot(None))
        .filter(Station.is_destroyed.is_(False))
        .populate_existing()
        .with_for_update()
        .all()
    )
    result: Dict[str, Any] = {
        "station_relocation_eligible": len(stations),
        "relocated": [],
        "lost": [],
        "skipped": [],
    }
    if not stations:
        return result

    dest = _pick_central_nexus_sector(db)
    if dest is None:
        logger.error(
            "region_termination_cascade: no Central Nexus sector for station "
            "relocation (region %s); %d station(s) skipped",
            region_id, len(stations),
        )
        for st in stations:
            result["skipped"].append(
                {"station_id": str(st.id), "outcome": "skipped", "reason": "no_nexus_destination"}
            )
        return result

    for station in stations:
        outcome = _relocate_one_station(db, station, dest, now)
        bucket = outcome.get("outcome")
        if bucket == "relocated":
            result["relocated"].append(outcome)
        elif bucket == "lost":
            result["lost"].append(outcome)
        else:
            result["skipped"].append(outcome)

    logger.info(
        "region_termination_cascade: region %s stations eligible=%d relocated=%d "
        "lost=%d skipped=%d",
        region_id,
        result["station_relocation_eligible"],
        len(result["relocated"]),
        len(result["lost"]),
        len(result["skipped"]),
    )
    return result
