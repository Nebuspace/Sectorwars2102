"""
Ship Upgrades, Equipment & Purchase API Routes

Player-facing endpoints for upgrading ships, managing equipment slots,
and purchasing pre-fabricated ships at shipyard-capable stations.
"""

import logging
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified
from pydantic import BaseModel, Field

from src.core.database import get_db
from src.auth.dependencies import get_current_player
from src.models.player import Player
from src.models.ship import Ship, ShipSpecification, ShipStatus, ShipType, UpgradeType
from src.models.station import Station, StationType
from src.models.faction import Faction
from src.models.reputation import Reputation, ReputationLevel
from src.services.ship_service import ShipService
from src.services.ship_upgrade_service import (
    ShipUpgradeService,
    is_galactic_citizen as gc_is_galactic_citizen,
)
from src.services import maintenance_service
from src.services.emergent_reputation_service import apply_emergent_action, FACTION_CODE_TO_TYPE

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ships", tags=["ship-upgrades"])


# Reputation-gated hulls (ShipSpecification.faction_requirements). A spec's
# faction_requirements is a dict of {faction_code: required_ReputationLevel_name},
# e.g. {"terran_federation": "TRUSTED"}; None/empty == no gate. The faction_code
# is the roster slug resolved to a FactionType via the canonical
# FACTION_CODE_TO_TYPE map (the same map emergent_reputation uses), then to the
# Faction row, then to the player's Reputation row with that faction.
#
# ReputationLevel is declared low->high (PUBLIC_ENEMY .. EXALTED), so a member's
# index in the enum's declaration order IS its monotonic rank — "TRUSTED required"
# is satisfied by TRUSTED and every level above it.
_REPUTATION_RANK = {level: rank for rank, level in enumerate(ReputationLevel)}


def _player_reputation_level(db: Session, player_id, faction_type) -> ReputationLevel:
    """The player's current ReputationLevel with the given FactionType.

    Resolves the FactionType to its Faction row and reads the player's
    Reputation row (mirrors apply_faction_rep_delta's resolution path). A
    player with no Reputation row for that faction is at the seeded default
    of NEUTRAL.
    """
    faction = db.query(Faction).filter(Faction.faction_type == faction_type).first()
    if faction is None:
        return ReputationLevel.NEUTRAL
    rep = (
        db.query(Reputation)
        .filter(Reputation.player_id == player_id, Reputation.faction_id == faction.id)
        .first()
    )
    if rep is None or rep.current_level is None:
        return ReputationLevel.NEUTRAL
    return rep.current_level


def check_faction_eligibility(db: Session, player_id, spec: ShipSpecification):
    """Return (eligible: bool, reason: Optional[str]) for a player buying `spec`.

    No faction_requirements -> always eligible. Otherwise EVERY {faction_code:
    required_level} entry must be met: the player's reputation LEVEL with that
    faction must rank >= the required level. The first unmet requirement is the
    returned reason (ERR_CITIZEN_ONLY_HULL-style, naming faction + level).
    """
    requirements = spec.faction_requirements or {}
    if not requirements:
        return True, None

    for faction_code, required_name in requirements.items():
        try:
            required_level = ReputationLevel(required_name)
        except ValueError:
            # Mis-seeded requirement name: fail closed rather than sell a
            # gated hull on a typo (better a stuck ship than a free one).
            return False, (
                f"ERR_CITIZEN_ONLY_HULL: this hull has a misconfigured "
                f"faction requirement ({faction_code}: {required_name})"
            )

        faction_type = FACTION_CODE_TO_TYPE.get(faction_code)
        if faction_type is None:
            return False, (
                f"ERR_CITIZEN_ONLY_HULL: this hull requires standing with an "
                f"unknown faction ({faction_code})"
            )

        player_level = _player_reputation_level(db, player_id, faction_type)
        if _REPUTATION_RANK[player_level] < _REPUTATION_RANK[required_level]:
            faction_label = faction_code.replace("_", " ").title()
            return False, (
                f"ERR_CITIZEN_ONLY_HULL: requires {required_level.value} standing "
                f"with the {faction_label} (you are {player_level.value})"
            )

    return True, None


# Request/Response Models

class UpgradeRequest(BaseModel):
    # ship_id is the URL path param (the route ignores any body ship_id), so it is
    # OPTIONAL in the body — a required body ship_id 422s the client, which sends
    # only {upgrade_type} per the path-carries-the-id REST convention. (WO-CC live
    # browser proof: the mounted upgrade UI 422'd on every purchase because of this.)
    ship_id: Optional[str] = None
    upgrade_type: str = Field(..., description="One of: ENGINE, CARGO_HOLD, SHIELD, HULL, SENSOR, DRONE_BAY, GENESIS_CONTAINMENT")


class EquipmentRequest(BaseModel):
    # ship_id is the URL path param (see UpgradeRequest) — optional in the body.
    ship_id: Optional[str] = None
    equipment_key: str = Field(..., description="One of: quantum_harvester, mining_laser, planetary_lander")


class ModuleInstallRequest(BaseModel):
    # ship_id is the URL path param (see UpgradeRequest) — optional in the body.
    ship_id: Optional[str] = None
    slot_index: int = Field(..., ge=0, description="Index of the ship's module slot to fit")
    module_class: str = Field(..., description="Module class, e.g. shield, engine, hull, sensor")
    tier: int = Field(..., ge=1, le=3, description="Module tier (1=Mk I, 2=Mk II, 3=Mk III)")


class ModuleRemoveRequest(BaseModel):
    ship_id: Optional[str] = None
    slot_index: int = Field(..., ge=0, description="Index of the ship's module slot to strip")


class CosmeticRequest(BaseModel):
    """WO-GC-B: apply (value set) or clear (value=null) a Citizen cosmetic."""
    slot: str = Field(..., pattern="^(frame|slot_glow|crest)$")
    value: Optional[str] = Field(None, max_length=64)


class ShipPurchaseRequest(BaseModel):
    ship_type: str = Field(..., description="Ship type to purchase, e.g. LIGHT_FREIGHTER or 'Light Freighter'")
    name: Optional[str] = Field(None, max_length=100, description="Optional custom name for the new ship")


class InsurancePurchaseRequest(BaseModel):
    tier: str = Field(..., description="Insurance tier to buy/upgrade to: BASIC, STANDARD, or PREMIUM")


# Ship insurance (ADR-0081 premium pricing, ADR-0061 payout formula).
# Premium = % of purchase_value paid upfront; net payout on destruction =
# (coverage - deductible)% of purchase_value.
INSURANCE_PREMIUM_PCT = {"BASIC": 0.10, "STANDARD": 0.17, "PREMIUM": 0.22}
INSURANCE_NET_PAYOUT_PCT = {"BASIC": 0.45, "STANDARD": 0.65, "PREMIUM": 0.75}
INSURANCE_TIER_ORDER = ["NONE", "BASIC", "STANDARD", "PREMIUM"]
# Non-insurable hulls: no policy is ever written (ADR-0029).
NON_INSURABLE_TYPES = {ShipType.WARP_JUMPER, ShipType.ESCAPE_POD}

# Mercantile-Guild emergent-rep rewards for buying ship insurance
# (factions-and-teams.md MG table: BASIC +2 / STANDARD +5 / PREMIUM +10, each
# "one-time per hull"). The emergent dispatcher (emergent_reputation_service.py)
# owns the magnitudes; here we map a purchased tier to its canon action key.
# Tracked per-hull in ship.insurance["mg_rep_awarded"] (additive JSONB, no
# migration) so a repurchase/downgrade attempt never re-awards.
#
# NO-CANON (flagged in WO-AX): canon lists each tier as its own "+N one-time per
# hull" row but is silent on whether an UPGRADE (e.g. BASIC -> PREMIUM) grants
# the new tier's FULL value or only the delta over the already-awarded tier. We
# grant the NEW TIER'S FULL VALUE on first reach of each tier — the most faithful
# reading of the per-tier "one-time per hull" canon rows (each tier is an
# independently earnable milestone). A given tier is awarded at most once per
# hull; reaching PREMIUM directly grants +10 once, while a BASIC->PREMIUM path
# grants +2 (at BASIC) then +10 (at PREMIUM) = +12 total. (If canon later
# specifies delta-only, change this to subtract the highest already-awarded tier.)
INSURANCE_REP_ACTION = {
    "BASIC": "BUY_INSURANCE_BASIC",
    "STANDARD": "BUY_INSURANCE_STANDARD",
    "PREMIUM": "BUY_INSURANCE_PREMIUM",
}


# Helpers

def _station_offers_shipyard(station: Station) -> bool:
    """A station offers shipyard services if it is a SpaceDock, a SHIPYARD-type
    station, or advertises ship sales in its services JSONB."""
    services = station.services or {}
    return (
        bool(station.is_spacedock)
        or station.type == StationType.SHIPYARD
        or bool(services.get("shipyard"))
        or bool(services.get("ship_dealer"))
    )


def _station_offers_insurance(station: Station) -> bool:
    """A station sells insurance if it advertises the service (SpaceDocks and
    Tier-A/B TradeDocks per bang seeding).

    NOTE: canon (ship-insurance.md) also requires the player to have >= NEUTRAL
    reputation with the station's controlling faction ("friendly port"). That
    refinement is a documented follow-up — no station-service path enforces
    faction standing today and players default to NEUTRAL — so v1 gates on the
    service being offered.
    """
    services = station.services or {}
    return bool(services.get("insurance"))


def _insurance_status(ship: Ship) -> dict:
    """Build the insurance status payload for a ship (current tier + buyable tiers)."""
    pv = ship.purchase_value or 0
    current = (ship.insurance or {}).get("type", "NONE")
    if current not in INSURANCE_TIER_ORDER:
        current = "NONE"
    current_idx = INSURANCE_TIER_ORDER.index(current)
    current_premium = int(pv * INSURANCE_PREMIUM_PCT[current]) if current in INSURANCE_PREMIUM_PCT else 0
    insurable = ship.type not in NON_INSURABLE_TYPES

    tiers = []
    for tier in ("BASIC", "STANDARD", "PREMIUM"):
        tier_idx = INSURANCE_TIER_ORDER.index(tier)
        purchasable = insurable and tier_idx > current_idx
        upgrade_cost = int(pv * INSURANCE_PREMIUM_PCT[tier]) - current_premium if purchasable else None
        tiers.append({
            "tier": tier,
            "premium_pct": INSURANCE_PREMIUM_PCT[tier],
            "premium_full": int(pv * INSURANCE_PREMIUM_PCT[tier]),
            "net_payout_pct": INSURANCE_NET_PAYOUT_PCT[tier],
            "payout_amount": int(pv * INSURANCE_NET_PAYOUT_PCT[tier]),
            "upgrade_cost": upgrade_cost,
            "purchasable": purchasable,
        })

    return {
        "ship_id": str(ship.id),
        "ship_name": ship.name,
        "ship_type": ship.type.value if ship.type else None,
        "insurable": insurable,
        "current_tier": current,
        "purchase_value": pv,
        "current_payout_amount": int(pv * INSURANCE_NET_PAYOUT_PCT.get(current, 0.0)),
        "tiers": tiers,
    }


# Endpoints

@router.get("/catalog")
async def get_ship_catalog(
    player: Player = Depends(get_current_player),
    db: Session = Depends(get_db),
):
    """List ship types and their specifications so the shipyard UI can render
    real catalog data. Viewing does not require being docked at a shipyard."""
    # NPC-only special-issue hulls (police Interdictors) are never
    # serialized to player-facing ShipType lists (police-forces.md
    # "NPC-only hull classes") — filter at this serializer layer.
    specs = (
        db.query(ShipSpecification)
        .filter(ShipSpecification.is_npc_only == False)  # noqa: E712 — SQLAlchemy boolean comparison
        .order_by(ShipSpecification.base_cost)
        .all()
    )

    ships = []
    for spec in specs:
        acquisition_methods = spec.acquisition_methods or []
        # Galactic-Citizen-only hulls: unlocked by membership, not faction rep.
        # Surfaced as a VISIBLE flag so the shipyard UI can render the lock state
        # openly (never a hidden 403). A hull is citizen-only when it offers the
        # "citizen" acquisition method WITHOUT a generic "purchase" path.
        citizen_only = (
            "citizen" in acquisition_methods and "purchase" not in acquisition_methods
        )
        # Reputation-gated hulls: surface the requirement + whether THIS player
        # currently meets it, so the shipyard UI can lock the card and show why.
        eligible, ineligible_reason = check_faction_eligibility(db, player.id, spec)
        if citizen_only:
            # For citizen-only hulls, eligibility tracks membership (the
            # P2W-firewall gate), not faction standing.
            eligible = gc_is_galactic_citizen(db, player)
            ineligible_reason = (
                None if eligible
                else "Requires an active Galactic Citizen membership"
            )
        ships.append({
            "type": spec.type.value,
            "name": spec.type.value.replace("_", " ").title(),
            "base_cost": spec.base_cost,
            "purchasable": "purchase" in acquisition_methods,
            "citizen_only": citizen_only,
            "faction_requirements": spec.faction_requirements or None,
            "eligible": eligible,
            "ineligible_reason": ineligible_reason,
            "speed": spec.speed,
            "turn_cost": spec.turn_cost,
            "max_cargo": spec.max_cargo,
            "max_colonists": spec.max_colonists,
            "max_drones": spec.max_drones,
            "max_shields": spec.max_shields,
            "hull_points": spec.hull_points,
            "evasion": spec.evasion,
            "attack_rating": spec.attack_rating,
            "defense_rating": spec.defense_rating,
            "max_genesis_devices": spec.max_genesis_devices,
            "warp_compatible": spec.warp_compatible,
            "description": spec.description,
        })

    return {"ships": ships}


@router.post("/purchase")
async def purchase_ship(
    request: ShipPurchaseRequest,
    player: Player = Depends(get_current_player),
    db: Session = Depends(get_db),
):
    """Purchase a pre-fabricated ship at the station the player is docked at.

    Requires the player to be docked at a station offering shipyard services.
    Deducts the specification's base cost from the player's credits and creates
    the ship in the player's current sector. The new ship does NOT replace the
    player's current ship unless they have none.
    """
    # Normalize and validate ship type ("Light Freighter" -> LIGHT_FREIGHTER)
    normalized_type = request.ship_type.strip().upper().replace(" ", "_").replace("-", "_")
    try:
        ship_type = ShipType(normalized_type)
    except ValueError:
        # NPC-only hulls (NPC_* values) are excluded from the player-facing
        # valid-types list (police-forces.md "NPC-only hull classes").
        valid_types = [t.value for t in ShipType if not t.value.startswith("NPC_")]
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Unknown ship type: {request.ship_type}. Valid types: {valid_types}",
        )

    # Lock the player row to prevent concurrent purchases double-spending credits
    player = db.query(Player).filter(Player.id == player.id).with_for_update().first()

    # Must be docked at a station
    if not player.is_docked or not player.current_port_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You must be docked at a station to purchase a ship",
        )

    station = db.query(Station).filter(Station.id == player.current_port_id).first()
    if not station:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Docked station not found",
        )

    if not _station_offers_shipyard(station):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This station does not offer shipyard services",
        )

    spec = db.query(ShipSpecification).filter(
        ShipSpecification.type == ship_type
    ).first()
    if not spec:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No specification available for ship type {ship_type.value}",
        )

    # NPC-only special-issue hulls can never transfer to player ownership
    # (police-forces.md "Interdictor hulls"; DATA_MODELS/ships.md
    # ERR_NPC_ONLY_HULL)
    if spec.is_npc_only:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"ERR_NPC_ONLY_HULL: {ship_type.value.replace('_', ' ').title()} "
                f"is an NPC-only special-issue hull and cannot be player-owned"
            ),
        )

    # Galactic-Citizen hulls (P2W firewall): a citizen-anchored hull mirrors a
    # free anchor's combat/income ceiling and differs only in shape/utility/QoL/
    # cosmetics — the membership UNLOCKS the buy, it does not buy power. The
    # citizen branch gates on membership BEFORE the generic "not purchasable"
    # reject so a citizen-eligible hull bypasses that reject and still hits the
    # credit check + deduction + ship creation (members pay full credits).
    acquisition_methods = spec.acquisition_methods or []
    if "citizen" in acquisition_methods:
        if not gc_is_galactic_citizen(db, player):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    f"ERR_CITIZEN_ONLY_HULL: {ship_type.value.replace('_', ' ').title()} "
                    f"requires an active Galactic Citizen membership to acquire"
                ),
            )
        # Citizen is eligible — fall through to faction/credit checks + creation.
    # Only ship types flagged as purchasable (or citizen-unlocked above) can be
    # bought at a shipyard (blocks ESCAPE_POD free-dupes and WARP_JUMPER special
    # construction)
    elif "purchase" not in acquisition_methods:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"{ship_type.value.replace('_', ' ').title()} cannot be purchased at a shipyard",
        )

    # Reputation-gated hulls: a player must hold the required faction standing
    # before they can buy (e.g. terran_federation: TRUSTED). Checked before any
    # credits are charged so a rejected buy is free of side effects.
    eligible, reason = check_faction_eligibility(db, player.id, spec)
    if not eligible:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=reason,
        )

    if player.credits < spec.base_cost:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Insufficient credits. Need {spec.base_cost}, have {player.credits}",
        )

    # Deduct credits and create the ship in one transaction
    player.credits -= spec.base_cost

    custom_name = request.name.strip() if request.name and request.name.strip() else None
    ship_service = ShipService(db)
    try:
        ship = ship_service.create_ship(
            ship_type=ship_type,
            owner_id=player.id,
            sector_id=player.current_sector_id,
            name=custom_name,
        )
    except ValueError as e:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))

    # Only adopt the new ship as current/flagship if the player has none
    if player.current_ship_id is None:
        player.current_ship_id = ship.id
        ship.is_flagship = True
    else:
        ship.is_flagship = False

    db.commit()

    logger.info(
        f"Player {player.id} purchased {ship_type.value} '{ship.name}' "
        f"for {spec.base_cost} credits at station {station.id}"
    )

    return {
        "ship": {
            "id": str(ship.id),
            "name": ship.name,
            "type": ship.type.value,
        },
        "remaining_credits": player.credits,
    }


@router.post("/{ship_id}/set-active")
async def set_active_ship(
    ship_id: str,
    player: Player = Depends(get_current_player),
    db: Session = Depends(get_db),
):
    """Make one of the player's ships the active (piloted) ship.

    The client (GameContext.setCurrentShip, the Hangar's MAKE ACTIVE SHIP
    button) has always called this endpoint; it never existed until now.
    The target ship must be in the player's current sector — you walk
    across the hangar, you don't teleport across the galaxy.
    """
    import uuid as _uuid
    try:
        ship_uuid = _uuid.UUID(str(ship_id))
    except (ValueError, AttributeError, TypeError):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ship not found")

    locked_player = db.query(Player).filter(Player.id == player.id).with_for_update().first()
    ship = db.query(Ship).filter(Ship.id == ship_uuid, Ship.owner_id == player.id).first()
    if not ship:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ship not found")
    if ship.sector_id != locked_player.current_sector_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"{ship.name} is in sector {ship.sector_id}; travel there to board it",
        )
    if ship.is_destroyed:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"{ship.name} is destroyed",
        )
    if ship.status == ShipStatus.HARMONIZING:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"{ship.name} is harmonizing into a warp gate focus and cannot be boarded",
        )
    if locked_player.is_landed:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Lift off before switching ships",
        )

    locked_player.current_ship_id = ship.id
    db.commit()
    return {
        "message": f"{ship.name} is now your active ship",
        "current_ship_id": str(ship.id),
    }


def _resolve_owned_ship(ship_id: str, player: Player, db: Session, lock: bool = False) -> Ship:
    import uuid as _uuid
    try:
        ship_uuid = _uuid.UUID(str(ship_id))
    except (ValueError, AttributeError, TypeError):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ship not found")
    q = db.query(Ship).filter(Ship.id == ship_uuid, Ship.owner_id == player.id)
    if lock:
        # Lock the hull row so a concurrent ownership transfer can't race the
        # insurance write (TOCTOU on owner_id).
        q = q.with_for_update()
    ship = q.first()
    if not ship:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ship not found")
    return ship


@router.get("/{ship_id}/insurance")
async def get_ship_insurance(
    ship_id: str,
    player: Player = Depends(get_current_player),
    db: Session = Depends(get_db),
):
    """Current insurance coverage for one of the player's ships plus the buyable
    tiers (premium cost = upgrade difference; net payout per ADR-0061/0081)."""
    ship = _resolve_owned_ship(ship_id, player, db)
    return _insurance_status(ship)


@router.post("/{ship_id}/insurance")
async def purchase_ship_insurance(
    ship_id: str,
    request: InsurancePurchaseRequest,
    player: Player = Depends(get_current_player),
    db: Session = Depends(get_db),
):
    """Buy or upgrade insurance on one of the player's ships at a friendly port.

    Premium is paid upfront (ADR-0081); upgrades cost the difference between
    tiers; coverage attaches to the hull for its lifetime. No downgrades, no
    refunds, no claims (ship-insurance.md). Warp Jumpers / Escape Pods are
    non-insurable (ADR-0029).
    """
    tier = request.tier.strip().upper()
    if tier not in INSURANCE_PREMIUM_PCT:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid insurance tier '{request.tier}'. Valid tiers: BASIC, STANDARD, PREMIUM",
        )

    # Lock the player row first (credits), then resolve + lock the ship row.
    locked_player = db.query(Player).filter(Player.id == player.id).with_for_update().first()
    ship = _resolve_owned_ship(ship_id, locked_player, db, lock=True)

    if ship.is_destroyed:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"{ship.name} is destroyed")
    if ship.type in NON_INSURABLE_TYPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"{ship.type.value.replace('_', ' ').title()} hulls are non-insurable",
        )
    if not ship.purchase_value or ship.purchase_value <= 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"{ship.name} has no insurable value",
        )

    # Must be docked at a station that offers insurance (a friendly port).
    if not locked_player.is_docked or not locked_player.current_port_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You must be docked at a station offering insurance",
        )
    station = db.query(Station).filter(Station.id == locked_player.current_port_id).first()
    if not station or not _station_offers_insurance(station):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This station does not offer insurance services",
        )

    # No downgrades, no same-tier repurchase; upgrades pay the difference.
    current = (ship.insurance or {}).get("type", "NONE")
    if current not in INSURANCE_TIER_ORDER:
        current = "NONE"
    if INSURANCE_TIER_ORDER.index(tier) <= INSURANCE_TIER_ORDER.index(current):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Insurance cannot be downgraded or repurchased at the same tier (current: {current})",
        )

    pv = ship.purchase_value
    current_premium = int(pv * INSURANCE_PREMIUM_PCT[current]) if current in INSURANCE_PREMIUM_PCT else 0
    cost = int(pv * INSURANCE_PREMIUM_PCT[tier]) - current_premium

    # Defense-in-depth: upgrades are always to a strictly higher tier (downgrades
    # rejected above) and premiums are monotonic, so cost is always > 0. Guard
    # anyway so no data anomaly can ever gift credits.
    if cost <= 0:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid insurance premium")

    if locked_player.credits < cost:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Insufficient credits: premium is {cost:,} cr, you have {locked_player.credits:,}",
        )

    locked_player.credits -= cost

    # Carry forward the per-hull MG-rep award ledger so re-writing the policy
    # dict doesn't lose which tiers were already rewarded. Stored on the same
    # insurance JSONB (additive — no migration).
    prior_awarded = (ship.insurance or {}).get("mg_rep_awarded")
    awarded = list(prior_awarded) if isinstance(prior_awarded, list) else []
    ship.insurance = {"type": tier, "mg_rep_awarded": awarded}
    flag_modified(ship, "insurance")

    # Mercantile-Guild emergent rep: award the purchased tier's reward ONCE per
    # hull (factions-and-teams.md MG table). Fires inside the existing locked
    # txn (FLUSH-ONLY dispatcher — we own the single db.commit below), so the
    # rep delta is atomic with the policy + credit write and never double-fires:
    # a repurchase/downgrade can't reach a strictly-higher unawarded tier (the
    # downgrade guard above), and the ledger blocks re-award of a seen tier.
    rep_action = INSURANCE_REP_ACTION.get(tier)
    if rep_action and tier not in awarded:
        apply_emergent_action(
            db,
            locked_player,
            rep_action,
            {"ship_id": str(ship.id), "sector_id": locked_player.current_sector_id},
        )
        awarded.append(tier)
        # Re-assign so SQLAlchemy sees the mutated list and the UPDATE rides the
        # caller's commit.
        ship.insurance = {"type": tier, "mg_rep_awarded": awarded}
        flag_modified(ship, "insurance")

    db.commit()
    db.refresh(ship)

    status_payload = _insurance_status(ship)
    return {
        "message": f"{ship.name} insured at {tier} ({cost:,} cr)",
        "premium_paid": cost,
        "credits_remaining": locked_player.credits,
        **status_payload,
    }


@router.get("/{ship_id}/upgrades")
async def get_ship_upgrades(
    ship_id: str,
    player: Player = Depends(get_current_player),
    db: Session = Depends(get_db),
):
    """Get upgrade and equipment info for a specific ship."""
    service = ShipUpgradeService(db)
    result = service.get_upgrade_info(ship_id, player.id)
    if not result.get("success"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=result.get("message", "Failed to get upgrade info"),
        )
    return result


@router.post("/{ship_id}/upgrades/purchase")
async def purchase_ship_upgrade(
    ship_id: str,
    request: UpgradeRequest,
    player: Player = Depends(get_current_player),
    db: Session = Depends(get_db),
):
    """Purchase an upgrade for a ship."""
    # Validate upgrade type
    try:
        upgrade_type = UpgradeType(request.upgrade_type)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid upgrade type: {request.upgrade_type}. Valid types: {[t.value for t in UpgradeType]}",
        )

    service = ShipUpgradeService(db)
    result = service.purchase_upgrade(ship_id, player.id, upgrade_type)
    if not result.get("success"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=result.get("message", "Upgrade purchase failed"),
        )
    db.commit()
    return result


@router.post("/{ship_id}/equipment/install")
async def install_ship_equipment(
    ship_id: str,
    request: EquipmentRequest,
    player: Player = Depends(get_current_player),
    db: Session = Depends(get_db),
):
    """Install equipment into a ship's equipment slot."""
    service = ShipUpgradeService(db)
    result = service.install_equipment(ship_id, player.id, request.equipment_key)
    if not result.get("success"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=result.get("message", "Equipment installation failed"),
        )
    db.commit()
    return result


@router.post("/{ship_id}/equipment/uninstall")
async def uninstall_ship_equipment(
    ship_id: str,
    request: EquipmentRequest,
    player: Player = Depends(get_current_player),
    db: Session = Depends(get_db),
):
    """Uninstall equipment from a ship's equipment slot. No refund."""
    service = ShipUpgradeService(db)
    result = service.uninstall_equipment(ship_id, player.id, request.equipment_key)
    if not result.get("success"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=result.get("message", "Equipment uninstall failed"),
        )
    db.commit()
    return result


# --- SHIP-MODS (WO-SM-3): module slot grid install / remove ---

@router.get("/{ship_id}/modules")
async def get_ship_modules(
    ship_id: str,
    player: Player = Depends(get_current_player),
    db: Session = Depends(get_db),
):
    """The ship's module slot lattice (from its spec) + the currently-installed
    modules (from the ship's modules JSONB), so the shipyard UI can render the
    grid and what's fitted."""
    ship = _resolve_owned_ship(ship_id, player, db)
    spec = db.query(ShipSpecification).filter(ShipSpecification.type == ship.type).first()
    module_slots = (spec.module_slots if spec else None) or None
    modules = ship.modules if isinstance(ship.modules, dict) else {}
    installed = modules.get("installed") if isinstance(modules.get("installed"), dict) else {}
    cosmetics = modules.get("cosmetics") if isinstance(modules.get("cosmetics"), dict) else {}
    return {
        "ship_id": str(ship.id),
        "ship_name": ship.name,
        "ship_type": ship.type.value if ship.type else None,
        "module_slots": module_slots,
        "installed": installed,
        # WO-GC-B: the Citizen cosmetic overlay + live membership status so the
        # grid can render the skin/glow + the "Galactic Citizen" label (greyed
        # when lapsed). cosmetics live OUTSIDE `installed` (never eat a slot).
        "cosmetics": cosmetics,
        "is_galactic_citizen": gc_is_galactic_citizen(db, player),
    }


@router.post("/{ship_id}/modules/install")
async def install_ship_module(
    ship_id: str,
    request: ModuleInstallRequest,
    player: Player = Depends(get_current_player),
    db: Session = Depends(get_db),
):
    """Fit a module into one of the ship's module slots at a shipyard."""
    service = ShipUpgradeService(db)
    result = service.install_module(
        ship_id, player.id, request.slot_index, request.module_class, request.tier
    )
    if not result.get("success"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=result.get("message", "Module install failed"),
        )
    db.commit()
    return result


@router.post("/{ship_id}/modules/remove")
async def remove_ship_module(
    ship_id: str,
    request: ModuleRemoveRequest,
    player: Player = Depends(get_current_player),
    db: Session = Depends(get_db),
):
    """Strip a module out of one of the ship's module slots at a shipyard (salvage refund)."""
    service = ShipUpgradeService(db)
    result = service.remove_module(ship_id, player.id, request.slot_index)
    if not result.get("success"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=result.get("message", "Module remove failed"),
        )
    db.commit()
    return result


# --- Galactic-Citizen L1 cosmetics (WO-GC-B) -------------------------------

@router.get("/{ship_id}/cosmetics")
async def get_ship_cosmetics(
    ship_id: str,
    player: Player = Depends(get_current_player),
    db: Session = Depends(get_db),
):
    """The Citizen cosmetic catalog + the ship's applied overlay + the player's
    live Citizen status. Owner-only; no mutation."""
    service = ShipUpgradeService(db)
    result = service.get_cosmetics(ship_id, player.id)
    if not result.get("success"):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=result.get("message", "Could not read cosmetics"),
        )
    return result


@router.post("/{ship_id}/cosmetics")
async def set_ship_cosmetic(
    ship_id: str,
    request: CosmeticRequest,
    player: Player = Depends(get_current_player),
    db: Session = Depends(get_db),
):
    """Apply or clear a Citizen cosmetic overlay (owner-only + Citizen-gated)."""
    service = ShipUpgradeService(db)
    result = service.set_cosmetic(ship_id, player.id, request.slot, request.value)
    if not result.get("success"):
        # 403 when the block is the membership gate; 400 otherwise.
        code = status.HTTP_403_FORBIDDEN if result.get("requires_citizen") else status.HTTP_400_BAD_REQUEST
        raise HTTPException(status_code=code, detail=result.get("message", "Could not set cosmetic"))
    db.commit()  # route owns the commit (service flushed) — matches install/remove
    return result


# --- Ship maintenance (ships.md; decay + performance bands + shipyard repair) ---

class MaintenanceRepairRequest(BaseModel):
    tier: str = Field(..., description="Repair tier: basic, emergency, or premium")


# Canon repair cost = tier% of ship value per +10% rating restored (ships.md:84-87).
MAINTENANCE_REPAIR_TIER_PCT = {"basic": 0.05, "emergency": 0.10, "premium": 0.15}


def _station_offers_repair(station: Station) -> bool:
    services = station.services or {}
    return bool(station.is_spacedock) or bool(services.get("ship_repair")) or bool(services.get("ship_maintenance"))


def _maintenance_status(ship: Ship, condition: float, station: Station = None) -> dict:
    band = maintenance_service.maintenance_band(condition)
    value = ship.current_value or 0
    # premium needs a Class-I / Military yard (approximated by a SpaceDock here)
    premium_here = bool(station and station.is_spacedock)
    # Owner service-charge (B4 consume-side): the docked station owner's
    # service-charge multiplier (0.8x-2.0x) scales the quoted repair costs so the
    # quote matches what the repair endpoint will actually charge. 1.0 (== today)
    # when unset or when reading a quote outside a station context.
    from src.services import docking_service
    service_mult = docking_service.service_charge_multiplier_for(station) if station else 1.0
    options = []
    for tier, pct in MAINTENANCE_REPAIR_TIER_PCT.items():
        cost = round((max(0.0, 100.0 - condition) / 10.0) * pct * value * service_mult)
        options.append({
            "tier": tier,
            "cost_pct_per_10": pct,
            "cost_to_full": cost,
            "available": True if tier != "premium" else premium_here,
        })
    return {
        "ship_id": str(ship.id),
        "ship_name": ship.name,
        "condition": round(condition, 1),
        "decay_pct_per_day": maintenance_service.DECAY_PCT_PER_DAY.get(ship.type, 0.0),
        "band": {
            "tier": band["tier"],
            "speed_pct": round(band["speed"] * 100),
            "combat_pct": round(band["combat"] * 100),
            "fuel_pct": round(band["fuel"] * 100),
            "failure_pct": round(band["failure"] * 100),
            "failure_tier": band["failure_tier"],
        },
        # Honesty: v1 applies only the combat-effectiveness band; the speed/fuel
        # modifiers and the per-jump failure roll are canon but not yet wired.
        "applied_effects": ["combat"],
        "repair_options": options,
    }


@router.get("/{ship_id}/maintenance")
async def get_ship_maintenance(
    ship_id: str,
    player: Player = Depends(get_current_player),
    db: Session = Depends(get_db),
):
    """Current maintenance condition + performance band + repair quotes for one
    of the player's ships. Decay is computed live (pure) for display."""
    ship = _resolve_owned_ship(ship_id, player, db)
    condition = maintenance_service.effective_condition(ship)
    station = None
    if player.is_docked and player.current_port_id:
        station = db.query(Station).filter(Station.id == player.current_port_id).first()
    return _maintenance_status(ship, condition, station)


@router.post("/{ship_id}/maintenance/repair")
async def repair_ship_maintenance(
    ship_id: str,
    request: MaintenanceRepairRequest,
    player: Player = Depends(get_current_player),
    db: Session = Depends(get_db),
):
    """Restore a ship's maintenance condition to 100% at a shipyard. Cost is the
    canon tier rate (basic 5% / emergency 10% / premium 15% of ship value per
    +10% restored). Premium needs a SpaceDock (Class-I/Military). Instant in v1
    (repair timers are a documented follow-up)."""
    tier = request.tier.strip().lower()
    if tier not in MAINTENANCE_REPAIR_TIER_PCT:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid repair tier '{request.tier}'. Valid: basic, emergency, premium",
        )

    locked_player = db.query(Player).filter(Player.id == player.id).with_for_update().first()
    ship = _resolve_owned_ship(ship_id, locked_player, db, lock=True)
    if ship.is_destroyed:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"{ship.name} is destroyed")
    if ship.type == ShipType.ESCAPE_POD:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Escape Pods need no maintenance")

    if not locked_player.is_docked or not locked_player.current_port_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You must be docked at a shipyard to service your ship",
        )
    station = db.query(Station).filter(Station.id == locked_player.current_port_id).first()
    if not station or not _station_offers_repair(station):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This station does not offer ship maintenance",
        )
    if tier == "premium" and not station.is_spacedock:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Premium servicing is only available at a SpaceDock (Class-I/Military) yard",
        )

    # Persist decay to now, then price the restore from the current condition.
    condition = maintenance_service.apply_maintenance_decay(ship)
    if condition >= 99.95:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"{ship.name} is already in pristine condition",
        )

    value = ship.current_value or 0
    pct = MAINTENANCE_REPAIR_TIER_PCT[tier]
    # Owner service-charge (B4 consume-side): the docked station owner's
    # service-charge multiplier (0.8x-2.0x) scales the repair cost. Returns 1.0
    # when unset, so a port with no service charge prices servicing exactly as
    # before. Applied inside the rounding so the charge matches the quote in
    # _maintenance_status.
    from src.services import docking_service
    service_mult = docking_service.service_charge_multiplier_for(station)
    cost = round((max(0.0, 100.0 - condition) / 10.0) * pct * value * service_mult)
    # Never restore for free: a near-pristine or zero-value hull whose cost rounds
    # to <=0 would otherwise get a free full-condition reset.
    if cost <= 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"{ship.name} is in good enough condition that servicing isn't worthwhile",
        )
    if locked_player.credits < cost:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Insufficient credits: servicing costs {cost:,} cr, you have {locked_player.credits:,}",
        )

    locked_player.credits -= cost
    from datetime import datetime, timezone
    m = dict(ship.maintenance or {})
    m["condition"] = 100.0
    m["last_maintenance"] = datetime.now(timezone.utc).isoformat()
    m["repair_needed"] = False
    m["failure_status"] = "NONE"
    ship.maintenance = m
    flag_modified(ship, "maintenance")
    db.commit()
    db.refresh(ship)
    db.refresh(locked_player)

    status_payload = _maintenance_status(ship, 100.0, station)
    return {
        "message": f"{ship.name} serviced to pristine condition ({cost:,} cr)",
        "cost": cost,
        "credits_remaining": locked_player.credits,
        **status_payload,
    }
