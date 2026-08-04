"""Region-termination cascade -- reduced scope (WO-BUILD-REGION-LIFECYCLE-
CLEANUP-CASCADE, lead-approved reduction after the full-scope blocker below).

BLOCKER on the full ADR-0050 cascade (unresolved, not built here): the
**station relocation** branch (ADR-0050 "Region-termination asset
disposition" table + "Station relocation paths") needs a 30% fee of
``(acquisition cost + sum of upgrade capital costs)``. Neither
``Station.acquisition_cost`` nor any per-upgrade capital-cost ledger exists
anywhere in ``src/models`` -- there is no player-buildable-station economy
shipped at all to derive either number from. Computing the fee would mean
fabricating a value, which this codebase's conventions forbid. This mirrors
the exact shape of the pre-existing gap warp_gate_service.py:2736-2746
documents for the Central-Bank routing branch of the SAME cascade (design-
only text in ADR-0050, no backing schema) -- flagged, not silently guessed.
``dispatch_station_termination`` below is therefore a **discovery-only
stub**, identical in spirit to that precedent and to region_lifecycle_
service.dispatch_terminated_cleanup: it identifies affected stations and logs
them, and does nothing destructive or fee-charging.

Built under the reduced scope (lead-approved): **planet-safe transport** (20%
loss, or 100% via ``Planet.transport_prepaid``) + **Genesis-device
compensation** (citadel-level table) for terminating planets, using only
existing columns/services -- ``Planet.citadel_safe_credits`` /
``Planet.active_events["safe_commodities"]`` (via ``CitadelService``),
``GenesisDevice.owner_id``, ``Planet.citadel_level``, ``Player.credits``.

No ``PlayerCentralBankAccount`` exists (verified: grep of src/models turns up
nothing, only the same warp_gate_service.py design-only citations above) --
ADR-0050 says the safe's SURVIVING (non-forfeited) value should land in that
Bank. Absent the Bank, this deposits it straight into the owner's
``Player.credits`` instead, the same gap-fallback warp_gate_service.py uses
for its own Central-Bank-routing gap. Non-cash commodities have no Player-
side storage either (Player has no cargo/inventory field), so the surviving
commodity stack is liquidated to its credit-equivalent via
``CitadelService.COMMODITY_CREDIT_VALUE`` -- the same conversion table
``CitadelService._safe_total_value`` already uses -- and folded into that
same credit deposit, rather than inventing a new storage location.

The FORFEITED 20% (or 0% if prepaid) is never credited anywhere -- per lead
direction 3, it is recorded as an ``AuditService`` entry (action=FORFEIT,
resource_type="planet_safe_forfeiture") tied to the planet/region, not
routed to any Player-side destination.

WALLET-DEFICIT HANDLING -- explicitly NOT built here, flagged rather than
silently included: none of this reduced scope's three obligations (safe
transport, Genesis compensation, forfeiture logging) ever DEBITS the wallet
-- they only credit it or leave it untouched. The "wallet-deficit" language
in ADR-0050's station-relocation Path A (treasury insufficient -> debit
wallet -> insufficient -> strip upgrades -> lose station) is specific to the
station-relocation branch, which stays a discovery-only stub above pending
the acquisition_cost/upgrade-capital blocker. There is nothing to attach
deficit-handling to on the planet-safe-transport path.

FLAG COLUMN PLACEMENT -- a deviation from "Region, most likely" worth
surfacing: ``transport_prepaid`` / ``relocation_prepaid`` were added to
``Planet`` / ``Station`` respectively (migration
c4d8e61f97ab_add_cascade_prepaid_flags), not ``Region``. ADR-0050's own
prose ties pre-pay to a specific asset -- "Player visits **the planet**
during Suspended/Grace ... `transport_prepaid` flag locks" (planet-safe
table Path B) and "Player visits **the station** ... `relocation_prepaid`
flag locks" (station-relocation table Path B) -- and a region can hold many
planets/stations that each independently choose to pre-pay; a single
region-level flag could not represent that. Both columns still live where
SUSPENDED/GRACE/TERMINATED region state anchors the cascade trigger
(``Region.status`` / ``Region.suspended_at`` / ``Region.terminated_at`` in
``src/models/region.py``), just not ON that row.
"""
import logging
import uuid
from datetime import datetime, UTC
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from src.models.genesis_device import GenesisDevice, GenesisStatus, GenesisType
from src.models.planet import Planet
from src.models.player import Player
from src.models.region import Region
from src.models.sector import Sector
from src.models.station import Station
from src.services.audit_service import AuditAction, AuditService
from src.services.citadel_service import COMMODITY_CREDIT_VALUE, CitadelService

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

    commodity_credit_value = sum(
        qty * COMMODITY_CREDIT_VALUE.get(commodity, 0)
        for commodity, qty in surviving_commodities.items()
    )
    credited_total = surviving_credits + commodity_credit_value
    if credited_total:
        owner.credits = (owner.credits or 0) + credited_total

    # Drain the safe now that its surviving value has been credited -- the
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
        "forfeited_credits": forfeited_credits,
        "forfeited_commodities": forfeited_commodities,
        "genesis_devices_minted": len(genesis_ids),
        "genesis_device_ids": genesis_ids,
        "genesis_credit_compensation": genesis_credit_compensation,
    })
    logger.info(
        "region_termination_cascade: planet %s terminated -- owner %s credited "
        "%d (safe) + %d (genesis) credits, %d genesis device(s) minted, "
        "%d credits / %s commodities forfeited",
        planet.id, owner.id, credited_total, genesis_credit_compensation,
        len(genesis_ids), forfeited_credits, forfeited_commodities,
    )
    return result


def dispatch_station_termination(db: Session, region_id: uuid.UUID) -> Dict[str, Any]:
    """DISCOVERY-ONLY STUB -- see module docstring's BLOCKER section. Finds
    stations in ``region_id`` and logs them as eligible for the relocation
    cascade; does NOT relocate, charge a fee, strip upgrades, or destroy
    anything. Mirrors region_lifecycle_service.dispatch_terminated_cleanup's
    own discovery-only shape for the same reason: the real cascade needs
    schema this codebase does not have yet."""
    stations = (
        db.query(Station.id, Station.name)
        .join(Sector, Sector.id == Station.sector_uuid)
        .filter(Sector.region_id == region_id)
        .all()
    )
    if stations:
        logger.info(
            "region_termination_cascade: %d station(s) in region %s eligible "
            "for relocation cascade (NOT executed -- acquisition_cost / "
            "upgrade-capital-cost tracking does not exist; discovery only)",
            len(stations), region_id,
        )
    return {"station_relocation_eligible": len(stations)}
