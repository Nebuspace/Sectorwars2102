import uuid
import enum
from datetime import datetime
from typing import List, Optional, Dict, Any, TYPE_CHECKING
from sqlalchemy import Boolean, Column, DateTime, String, Integer, Float, ForeignKey, Enum, func, text
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship

from src.core.database import Base

if TYPE_CHECKING:
    from src.models.player import Player
    from src.models.genesis_device import GenesisDevice


class ShipType(enum.Enum):
    ESCAPE_POD = "ESCAPE_POD"
    LIGHT_FREIGHTER = "LIGHT_FREIGHTER"
    CARGO_HAULER = "CARGO_HAULER"
    FAST_COURIER = "FAST_COURIER"
    # Galactic-Citizen courier — membership-gated SHAPE/QoL re-skin anchored to
    # FAST_COURIER (P2W firewall: NO combat/income axis exceeds the free anchor;
    # see ship_specifications_seeder.py CITIZEN_CLIPPER spec). Player-facing
    # (is_npc_only False) so it appears in /catalog.
    CITIZEN_CLIPPER = "CITIZEN_CLIPPER"
    SCOUT_SHIP = "SCOUT_SHIP"
    COLONY_SHIP = "COLONY_SHIP"
    DEFENDER = "DEFENDER"
    CARRIER = "CARRIER"
    WARP_JUMPER = "WARP_JUMPER"
    # NPC-only special-issue police hulls (police-forces.md "NPC-only hull
    # classes"; DATA_MODELS/ships.md ship_type enum). Never serialized to
    # player-facing ShipType lists — the filter lives at the serializer
    # layer (ship_upgrades.py /catalog + /purchase via
    # ShipSpecification.is_npc_only).
    NPC_MARSHAL_INTERDICTOR = "NPC_MARSHAL_INTERDICTOR"
    NPC_SENTINEL_INTERDICTOR = "NPC_SENTINEL_INTERDICTOR"


class ShipSize(enum.Enum):
    """Canonical ship-size axis (FEATURES/gameplay/ships.md "Ship size axis";
    DATA_MODELS/ships.md ShipSpecification `size` enum).

    The size axis is consumed by exactly two systems — the Carrier ship-hangar
    fit check (WO-AE) and the Tractor Beam tow per-move turn surcharge (WO-AF).
    Each finite size carries a `size_units` weight used by both:

        tiny=1, small=2, medium=4, large=8

    A `capital`-size hull (only the Carrier at launch) has NO finite
    size_units: it can be neither hangared nor towed (its mass exceeds both
    the Carrier hangar's structural rating and the Tractor Beam's). Querying
    SIZE_UNITS for CAPITAL therefore raises — callers must treat capital as
    not-dockable / not-towable, never as "some large number".
    """
    TINY = "TINY"
    SMALL = "SMALL"
    MEDIUM = "MEDIUM"
    LARGE = "LARGE"
    CAPITAL = "CAPITAL"


# Canonical size-unit weights (FEATURES/gameplay/ships.md:324-328). CAPITAL is
# deliberately absent: it is not-dockable and not-towable, so it has no finite
# hangar/tow size-unit cost. Use size_units_for() for a safe accessor.
SIZE_UNITS: Dict["ShipSize", int] = {
    ShipSize.TINY: 1,
    ShipSize.SMALL: 2,
    ShipSize.MEDIUM: 4,
    ShipSize.LARGE: 8,
    # ShipSize.CAPITAL intentionally omitted — not-dockable / not-towable.
}


def size_units_for(size: "ShipSize") -> int:
    """Finite hangar/tow size-unit cost for a ship size.

    Raises ValueError for CAPITAL (not-dockable / not-towable — it has no
    finite size-unit cost; callers must branch on capital explicitly rather
    than fall through to a number).
    """
    if size not in SIZE_UNITS:
        raise ValueError(
            f"{size} has no finite size_units — capital-size hulls cannot be "
            f"hangared or towed (FEATURES/gameplay/ships.md ship-size-axis)."
        )
    return SIZE_UNITS[size]


# Canonical Tractor Beam tow per-move turn SURCHARGE by towed size
# (FEATURES/gameplay/ships.md "Ship size axis" surcharge column + WO-AF order:
# tiny +1 / small +2 / medium +3 / large +5). This is a DISTINCT mapping from
# SIZE_UNITS above (1/2/4/8) — the surcharge does NOT equal the hangar size-unit
# weight. CAPITAL is deliberately absent: a capital-size hull cannot be towed
# (its mass exceeds the Tractor Beam's structural rating), so it has no
# surcharge. Use tow_surcharge_for() for a safe accessor that raises on CAPITAL.
TOW_SURCHARGE: Dict["ShipSize", int] = {
    ShipSize.TINY: 1,
    ShipSize.SMALL: 2,
    ShipSize.MEDIUM: 3,
    ShipSize.LARGE: 5,
    # ShipSize.CAPITAL intentionally omitted — not-towable.
}


def tow_surcharge_for(size: "ShipSize") -> int:
    """Per-move turn surcharge a hauler pays to tow a ship of ``size``.

    Raises ValueError for CAPITAL (not-towable — its mass exceeds the Tractor
    Beam's structural rating; callers must branch on capital explicitly via the
    size axis BEFORE calling this, never fall through to a number).
    """
    if size not in TOW_SURCHARGE:
        raise ValueError(
            f"{size} has no tow surcharge — capital-size hulls cannot be towed "
            f"(FEATURES/gameplay/ships.md ship-size-axis; ADR-0067)."
        )
    return TOW_SURCHARGE[size]


class FailureType(enum.Enum):
    NONE = "NONE"
    MINOR = "MINOR"
    MAJOR = "MAJOR"
    CATASTROPHIC = "CATASTROPHIC"


class UpgradeType(enum.Enum):
    ENGINE = "ENGINE"
    CARGO_HOLD = "CARGO_HOLD"
    SHIELD = "SHIELD"
    HULL = "HULL"
    SENSOR = "SENSOR"
    DRONE_BAY = "DRONE_BAY"
    GENESIS_CONTAINMENT = "GENESIS_CONTAINMENT"
    MAINTENANCE_SYSTEM = "MAINTENANCE_SYSTEM"


class InsuranceType(enum.Enum):
    NONE = "NONE"
    BASIC = "BASIC"
    STANDARD = "STANDARD"
    PREMIUM = "PREMIUM"


class ShipStatus(enum.Enum):
    DOCKED = "DOCKED"
    IN_SPACE = "IN_SPACE"
    IN_COMBAT = "IN_COMBAT"
    DESTROYED = "DESTROYED"
    MAINTENANCE = "MAINTENANCE"
    # Warp Jumper anchored to a beacon, harmonizing into a warp gate
    # (ADR-0011 invulnerability window, ADR-0029 WJ consumption at gate
    # creation, ADR-0036 harmonization atomicity). The hull cannot move,
    # dock, or jump while harmonizing; harmonization_completes_at holds
    # the deadline.
    HARMONIZING = "HARMONIZING"
    # Ship is harvesting an ASTEROID_FIELD sector (FEATURES/economy/mining.md;
    # DATA_MODELS/ships.md). Momentary: the harvest resolves synchronously in
    # one request, so MINING is set and reset to IN_SPACE inside the same
    # transaction. While MINING the hull is stationary and PvP-vulnerable
    # (the interrupt-refund window is a deferred enhancement).
    MINING = "MINING"


def effective_cargo_capacity(ship) -> int:
    """The ship's usable cargo capacity AFTER the Cargo-Hold ship-mod bonus.

    Base capacity lives in ``ship.cargo["capacity"]`` (default 50). The Cargo-Hold
    upgrade / module writes a single pre-summed ``_capacity_bonus_percent`` meta-key
    into the same ``cargo`` JSONB (ship-systems.md §2.4: +30% per level), and the
    effective capacity is ``base * (1 + bonus/100)``.

    The best-3 module stacking cap is ALREADY applied UPSTREAM, where
    ``_capacity_bonus_percent`` is written (``ShipUpgradeService`` §4.2 via
    ``_best_n_flat`` before the JSONB write) — so this consumer MUST NOT re-cap;
    it simply consumes the pre-capped percent. The single home for this read so the
    +30%/level applies consistently at every cargo-capacity reader (trade-profit-per-haul,
    the warp-gate per-run limit, the ShipResponse.cargo_capacity field).

    Reproduce-exactly: a ship with NO Cargo-Hold mod (bonus absent → 0) returns the
    base capacity unchanged.
    """
    cargo = ship.cargo if isinstance(getattr(ship, "cargo", None), dict) else {}
    try:
        base = int(cargo.get("capacity", 50))
    except (TypeError, ValueError):
        base = 50
    try:
        bonus_percent = float(cargo.get("_capacity_bonus_percent", 0) or 0)
    except (TypeError, ValueError):
        bonus_percent = 0.0
    return int(base * (1 + bonus_percent / 100))


class Ship(Base):
    __tablename__ = "ships"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(100), nullable=False)
    type = Column(Enum(ShipType, name="ship_type"), nullable=False)
    # NULL owner = NPC-piloted hull (see is_npc + NPCCharacter.ship_id);
    # player ships always carry an owner.
    owner_id = Column(UUID(as_uuid=True), ForeignKey("players.id", ondelete="CASCADE"), nullable=True)
    # True for NPC-piloted ships. Instance-level companion to canon's
    # ShipSpecification.is_npc_only flag (DATA_MODELS/ships.md): police
    # Interdictors carry both, while v1 pirate hulls reuse player
    # ShipTypes (no canon pirate hull stats exist yet) and rely on this
    # instance flag alone.
    is_npc = Column(Boolean, nullable=False, default=False, server_default=text("false"))
    sector_id = Column(Integer, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    last_updated = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    
    # Movement
    base_speed = Column(Float, nullable=False)
    current_speed = Column(Float, nullable=False)
    turn_cost = Column(Integer, nullable=False)
    warp_capable = Column(Boolean, nullable=False, default=False)
    
    # Operational status
    is_active = Column(Boolean, nullable=False, default=True)
    status = Column(Enum(ShipStatus, name="ship_status"), nullable=False, default=ShipStatus.DOCKED)
    maintenance = Column(JSONB, nullable=False)
    
    # Cargo & special equipment
    cargo = Column(JSONB, nullable=False)
    has_cloaking = Column(Boolean, nullable=False, default=False)
    genesis_devices = Column(Integer, nullable=False, default=0)
    max_genesis_devices = Column(Integer, nullable=False, default=0)
    mines = Column(Integer, nullable=False, default=0)
    max_mines = Column(Integer, nullable=False, default=0)
    has_automated_maintenance = Column(Boolean, nullable=False, default=False)
    # WO-DBB-QR1: set when a Quantum Harvester is installed (equip flips it); prereq for QR2.
    quantum_harvester_slot = Column(Boolean, nullable=False, default=False, server_default=text("false"))

    # Quantum Drive (ADR-0030) — Warp Jumper only. Charges are refined
    # 1-Shard-to-1-Charge at any Class-3+ station or SpaceDock and sit in
    # the WJ's special-equipment slot (this column), NOT in regular cargo.
    quantum_charges = Column(Integer, nullable=False, default=0, server_default=text("0"))
    # Cooldown deadlines are wall-clock instants, pre-scaled through
    # src.core.game_time.scaled_deadline at set time (24h jump / 4h scan
    # canonical). NULL or in the past = no active cooldown.
    quantum_jump_cooldown_until = Column(DateTime(timezone=True), nullable=True)
    quantum_scan_cooldown_until = Column(DateTime(timezone=True), nullable=True)
    # Nebula harvest cooldown (quantum-resources.md § Harvest mechanics: "2-hour
    # real-time per ship"). Pre-scaled through scaled_deadline at set time (2h
    # canonical). NULL or in the past = no active cooldown. Per-ship so swapping
    # hulls cannot stack harvest bursts from a single sector.
    quantum_harvest_cooldown_until = Column(DateTime(timezone=True), nullable=True)
    # Warp gate construction (ADR-0036): set when status == HARMONIZING,
    # cleared when harmonization resolves (1h canonical, scaled).
    harmonization_completes_at = Column(DateTime(timezone=True), nullable=True)
    # Why a destroyed hull died, e.g. 'WARP_GATE_ANCHOR' (ADR-0029 — the WJ
    # is consumed as the gate's anchor mass) or 'combat'. NULL while alive.
    destruction_cause = Column(String, nullable=True)

    # Combat
    combat = Column(JSONB, nullable=False)
    attack_turn_cost = Column(Integer, nullable=True)  # Turn cost to initiate combat with this ship
    # Combat resolver storage (combat_service._apply_weapon_damage): fraction of
    # incoming damage absorbed by shields / armor before hull damage applies.
    shield_resistance = Column(Float, nullable=False, default=0.0, server_default=text("0"))
    armor_rating = Column(Float, nullable=False, default=0.0, server_default=text("0"))

    # Upgrades and modifications
    upgrades = Column(JSONB, nullable=False, default=[])

    # Equipment slots
    equipment_slots = Column(JSONB, nullable=False, default={})

    # SHIP-MODS slot grid — per-instance installed modules (SHIP-MODS-MASTER §9.1;
    # WO-SM-1). A FRESH column beside equipment_slots — the grid never reads,
    # writes, or deletes the ADR-0030 sensor/slipdrive/special_equipment keys
    # that live in equipment_slots. Shape:
    #   {"v":1, "installed": { "<slot_i_as_str>": {"class":str, "tier":int,
    #     "super_at_install":bool, "installed_at":isoZ} } }
    # null / {} → no modules (defensive getattr(ship,'modules',None) or {}).
    # Only class+tier+super_at_install are stored; effect magnitudes resolve from
    # MODULE_DEFINITIONS at bake time (re-tune-safe). super_at_install is
    # snapshotted at install so a later slot-layout re-tune never silently
    # changes a fielded ship. Written ONLY by _apply_module_effects (SM-2/SM-3);
    # this column stays null on every ship until the first install.
    modules = Column(JSONB, nullable=True)

    # Carrier ship-hangar (WO-AE; DATA_MODELS/ships.md#carrier-ship-hangar).
    # Only populated on capital-size hulls (the Carrier at launch); NULL on
    # every other ship. Holds whole player ships in transit, SEPARATE from the
    # Carrier's 12-drone bay (no shared budget). Shape:
    #   {"capacity_units": 8, "docked": [{ship_id, owner_id, size, size_units,
    #     docked_at, request_state}]}
    # Managed exclusively by hangar_service (the single source of truth for
    # dock / undock / disembark / jettison-on-destruction).
    hangar = Column(JSONB, nullable=True)

    # Tractor Beam tow state (WO-AF; DATA_MODELS/ships.md#ship-tow-state).
    # Set on the HAULER (the ship doing the towing) while a Tractor Beam tow
    # operation is active; NULL otherwise (the default for every ship). Shape:
    #   {"towed_ship_id", "towed_owner_id", "towed_size",
    #    "surcharge_per_move", "locked_at", "lock_sector_id"}
    # surcharge_per_move is cached at lock-on from towed_size via the canon
    # tow-surcharge table (tiny+1 / small+2 / medium+3 / large+5 — DISTINCT
    # from the SIZE_UNITS hangar weights) so movement_service never re-traverses
    # ShipSpecification per move. A ship with tow_state != NULL cannot itself be
    # towed (no nesting), cannot dock into a Carrier hangar, and cannot fire its
    # Tractor Beam in weapon mode (mutual exclusion). The towed ship's own row
    # is unmodified — its current_pilot stays set, but movement_service /
    # quantum_service reject independent move/jump attempts while it's towed.
    # Managed exclusively by tow_service (the single source of truth for
    # lock-on / detach / tow-along / detach-on-destruction).
    tow_state = Column(JSONB, nullable=True)

    # Insurance
    insurance = Column(JSONB, nullable=True)
    
    # Special flags
    is_destroyed = Column(Boolean, nullable=False, default=False)
    is_flagship = Column(Boolean, nullable=False, default=False)
    purchase_value = Column(Integer, nullable=False)
    current_value = Column(Integer, nullable=False)

    # Relationships
    owner = relationship("Player", back_populates="ships", foreign_keys=[owner_id])
    flagship_of = relationship("Player", foreign_keys="Player.current_ship_id", post_update=True, overlaps="current_ship")
    sector = relationship("Sector", primaryjoin="Ship.sector_id==Sector.sector_id", foreign_keys=[sector_id])
    
    # New relationships
    genesis_device_objects = relationship("GenesisDevice", back_populates="ship")
    fleet_membership = relationship("FleetMember", back_populates="ship", uselist=False)

    def __repr__(self):
        return f"<Ship {self.name} ({self.type.name}) - Owner: {self.owner_id}>"
        
    @property
    def owner_name(self) -> str:
        """Return the ship owner's name - uses the Player.username property.

        NPC-piloted ships (owner_id NULL, is_npc True) have no Player owner;
        their pilot's display name lives on NPCCharacter (ship_id FK).
        """
        if self.owner:
            return self.owner.username
        return "Unknown"


class ShipSpecification(Base):
    __tablename__ = "ship_specifications"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    type = Column(Enum(ShipType, name="ship_type"), nullable=False, unique=True)
    # NPC-only special-issue hull (canon DATA_MODELS/ships.md +
    # police-forces.md "Interdictor hulls"): players can never purchase,
    # capture, salvage, or claim these — ownership-transfer paths reject
    # with ERR_NPC_ONLY_HULL and player-facing catalogs filter them out.
    is_npc_only = Column(Boolean, nullable=False, default=False, server_default=text("false"))
    # Canonical ship-size axis (FEATURES/gameplay/ships.md "Ship size axis";
    # DATA_MODELS/ships.md ShipSpecification `size`). Drives the Carrier
    # ship-hangar fit check (WO-AE) and the Tractor Beam tow surcharge
    # (WO-AF). Stored as a native Postgres enum, MIRRORING how `type`
    # (ShipType) is stored on this model. Nullable for additive rollout:
    # existing rows are valid pre-seed, and the idempotent boot seeder
    # upserts the canonical size onto every player ShipType. NPC-only
    # Interdictor hulls carry NULL — canon assigns them no size (they are
    # never hangared or towed; ERR_NPC_ONLY_HULL blocks player transfer).
    ship_size = Column(Enum(ShipSize, name="ship_size"), nullable=True)
    base_cost = Column(Integer, nullable=False)
    speed = Column(Float, nullable=False)
    turn_cost = Column(Integer, nullable=False)
    max_cargo = Column(Integer, nullable=False)
    max_colonists = Column(Integer, nullable=False)
    max_drones = Column(Integer, nullable=False)
    
    # Defense
    max_shields = Column(Integer, nullable=False)
    shield_recharge_rate = Column(Float, nullable=False)
    hull_points = Column(Integer, nullable=False)
    evasion = Column(Integer, nullable=False)
    
    # Capabilities
    genesis_compatible = Column(Boolean, nullable=False)
    max_genesis_devices = Column(Integer, nullable=False)
    warp_compatible = Column(Boolean, nullable=False)
    warp_creation_capable = Column(Boolean, nullable=False)
    quantum_jump_capable = Column(Boolean, nullable=False)
    scanner_range = Column(Integer, nullable=False)
    
    # Performance
    attack_rating = Column(Integer, nullable=False)
    defense_rating = Column(Integer, nullable=False)
    attack_turn_cost = Column(Integer, nullable=True)  # Turn cost to initiate combat
    # Combat resolver storage (combat_service._apply_weapon_damage): per-hull-type
    # baseline shield/armor mitigation, copied onto Ship instances at construction.
    shield_resistance = Column(Float, nullable=False, default=0.0, server_default=text("0"))
    armor_rating = Column(Float, nullable=False, default=0.0, server_default=text("0"))
    # DEAD/UNUSED: nothing reads this column for decay. Canon ship decay uses the
    # by-hull-class decay table, NOT this per-spec value. Column is NOT NULL so it
    # cannot be nulled without a migration; seeded to a neutral 0.0 in
    # ship_specifications_seeder.py (WO-BI). Do not reintroduce per-spec decay rates here.
    maintenance_rate = Column(Float, nullable=False)
    construction_time = Column(Integer, nullable=False)
    fuel_efficiency = Column(Integer, nullable=False)
    
    # Upgrades
    max_upgrade_levels = Column(JSONB, nullable=False)

    # SHIP-MODS slot grid — per-type slot lattice (SHIP-MODS-MASTER §9.2;
    # WO-SM-1). Authored once and seeded per ShipType by the idempotent boot
    # upserter in ship_specifications_seeder.py (conflict key on `type`). Shape:
    #   {"v":1, "cols":int, "rows":int,
    #    "slots":[ {"i":int, "x":int, "y":int, "super":bool,
    #               "class":str|null, "requires":str|null} ]}
    # Slot COUNT keys off ship_size via the literal table {small:3, medium:4,
    # large:6}; CAPITAL hand-set 8; tiny/Escape-Pod 0; NULL ship_size
    # (NPC-only Interdictor hulls) → 0. WHICH slot indices are
    # supercharged / class-locked is hand-authored per hull for distinct
    # identity (§3). (x,y) are stored FROM DAY ONE even though adjacency is
    # Phase B — so adjacency becomes a behaviour toggle, never a re-migration.
    # null module_slots → hull predates the feature → "no grid yet."
    module_slots = Column(JSONB, nullable=True)
    
    # Special abilities and metadata
    special_abilities = Column(JSONB, nullable=False, default=[])
    description = Column(String, nullable=False)
    acquisition_methods = Column(JSONB, nullable=False, default=[])
    faction_requirements = Column(JSONB, nullable=True)

    def __repr__(self):
        return f"<ShipSpecification for {self.type.name}>" 