"""Region-termination cascade -- reduced scope (WO-BUILD-REGION-LIFECYCLE-
CLEANUP-CASCADE, lead-approved reduction after the full-scope blocker below).

BLOCKER on the full ADR-0050 cascade, NARROWED (not built here): the
**station relocation** branch (ADR-0050 "Region-termination asset
disposition" table + "Station relocation paths") needs a 30% fee of
``(acquisition cost + sum of upgrade capital costs)``. The formula itself is
now computable -- ``port_ownership_service.relocation_fee`` (WO-BUILD-
STATION-ACQUISITION-COST-CAPITAL-LEDGER; acquisition_cost was already
tracked in ``station.ownership['acquisition_cost']``, the ledger for the
upgrade-capital half is new). What remains unbuilt here is wiring that
formula into an actual cascade dispatch (charge/debit/relocate/destroy) --
``dispatch_station_termination`` below remains a **discovery-only stub** for
that. Station-loss *credit compensation* (Path A final fallback) deposits
via ``apply_station_loss_compensation`` → Central Bank once a caller
computes the amount (the formula can now supply it).

Built under the reduced scope (lead-approved): **planet-safe transport** (20%
loss, or 100% via ``Planet.transport_prepaid``) + **Genesis-device
compensation** (citadel-level table) for terminating planets.

Surviving safe credits + commodity stacks deposit into
``PlayerCentralBankAccount`` (ADR-0050 / WO-BUILD-PLAYER-CENTRAL-BANK-
ACCOUNT). Genesis credit compensation still credits ``Player.credits``
(ADR-0050: partial-loss buffer into the wallet, not the Bank).

The FORFEITED 20% (or 0% if prepaid) is never credited anywhere -- recorded
as an ``AuditService`` entry (action=FORFEIT,
resource_type="planet_safe_forfeiture") tied to the planet/region.

WALLET-DEFICIT HANDLING -- explicitly NOT built here: none of this reduced
scope's obligations ever DEBITS the wallet. Station Path A deficit math is
computable now (relocation_fee) but still stays blocked on the cascade
dispatch itself being unwired here (see BLOCKER section above).

FLAG COLUMN PLACEMENT -- ``transport_prepaid`` / ``relocation_prepaid`` live
on ``Planet`` / ``Station`` (migration c4d8e61f97ab), not ``Region`` —
ADR-0050 ties pre-pay to a specific asset.
"""
import logging
import uuid
from datetime import datetime, UTC
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from src.models.genesis_device import GenesisDevice, GenesisStatus, GenesisType
from src.models.planet import Planet
from src.models.player import Player
from src.models.sector import Sector
from src.models.station import Station
from src.services import central_bank_service as bank
from src.services.audit_service import AuditAction, AuditService
from src.services.citadel_service import CitadelService

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
) -> Dict[str, Any]:
    """ADR-0050 "Player-owned planet" disposition, planet-safe-transport +
    Genesis-compensation slice only (see module docstring for the full-scope
    blocker and gap-fallbacks). Flush-only -- caller owns the commit, per
    this codebase's route-owns-commit convention.

    An orphaned planet (``owner_id is None``) has nothing to compensate --
    logged and returned early, mirroring warp_gate_service.
    cascade_region_gate_teardown's own orphaned-owner handling (point 4 of
    its docstring: WARNed, never raised, region terminates regardless).
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

    owner = db.query(Player).filter(Player.id == planet.owner_id).first()
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
    return result


def apply_station_loss_compensation(
    db: Session,
    player_id: uuid.UUID,
    amount: int,
    *,
    station_id: Optional[uuid.UUID] = None,
    notes: Optional[str] = None,
) -> Any:
    """Path-A station-loss compensation → Central Bank (third GAP re-point).

    Fee/acquisition math is computable via ``port_ownership_service.
    relocation_fee`` (WO-BUILD-STATION-ACQUISITION-COST-CAPITAL-LEDGER);
    once a caller invokes the cascade dispatch that isn't wired here yet,
    it deposits the computed ``amount`` here — never into ``Player.credits``.
    """
    return bank.pay_station_loss_compensation(
        db, player_id, amount, station_id=station_id, notes=notes,
    )


def dispatch_station_termination(db: Session, region_id: uuid.UUID) -> Dict[str, Any]:
    """DISCOVERY-ONLY STUB -- see module docstring's BLOCKER section. Finds
    stations in ``region_id`` and logs them as eligible for the relocation
    cascade; does NOT relocate, charge a fee, strip upgrades, or destroy
    anything. When Path A final-fallback loss is wired, compensation deposits
    via ``apply_station_loss_compensation`` (Bank), not ``Player.credits``."""
    stations = (
        db.query(Station.id, Station.name)
        .join(Sector, Sector.id == Station.sector_uuid)
        .filter(Sector.region_id == region_id)
        .all()
    )
    if stations:
        logger.info(
            "region_termination_cascade: %d station(s) in region %s eligible "
            "for relocation cascade (NOT executed -- fee formula is now "
            "computable via port_ownership_service.relocation_fee, but the "
            "cascade dispatch itself remains discovery-only; "
            "loss-compensation deposit target = Central Bank)",
            len(stations), region_id,
        )
    return {"station_relocation_eligible": len(stations)}
