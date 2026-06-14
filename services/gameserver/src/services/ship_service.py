"""
Ship Service
Handles ship creation, destruction, and special ship mechanics
"""

import logging
import uuid
from datetime import datetime
from typing import Dict, Any, Optional
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from src.models.player import Player
from src.models.ship import Ship, ShipType, ShipSpecification
from src.core.ship_specifications_seeder import SHIP_SPECIFICATIONS

logger = logging.getLogger(__name__)


class ShipService:
    """Service for managing ships and ship operations"""
    
    def __init__(self, db: Session):
        self.db = db
    
    def create_ship(self, 
                   ship_type: ShipType, 
                   owner_id: uuid.UUID, 
                   sector_id: int,
                   name: Optional[str] = None) -> Ship:
        """Create a new ship based on specifications"""
        
        # Get ship specification
        spec = self.db.query(ShipSpecification).filter(
            ShipSpecification.type == ship_type
        ).first()
        
        if not spec:
            raise ValueError(f"No specification found for ship type {ship_type}")
        
        # Generate ship name if not provided
        if not name:
            name = f"{ship_type.value.replace('_', ' ').title()}"
        
        # Create ship with specifications
        ship = Ship(
            name=name,
            type=ship_type,
            owner_id=owner_id,
            sector_id=sector_id,
            base_speed=spec.speed,
            current_speed=spec.speed,
            turn_cost=spec.turn_cost,
            warp_capable=spec.warp_compatible,
            
            # Initialize operational status
            is_active=True,
            maintenance={
                "condition": 100.0,
                "last_maintenance": datetime.utcnow().isoformat(),
                "next_maintenance": None,
                "repair_needed": False
            },
            
            # Initialize cargo with the spec's hold size — an empty dict
            # made every purchased ship fall back to the default capacity
            # of 50 (a Cargo Hauler shipped with a rowboat's hold)
            cargo={"capacity": spec.max_cargo, "used": 0, "contents": {}},
            
            # Initialize combat stats based on specifications
            combat={
                "shields": spec.max_shields,
                "max_shields": spec.max_shields,
                "shield_recharge_rate": spec.shield_recharge_rate,
                "hull": spec.hull_points,
                "max_hull": spec.hull_points,
                "evasion": spec.evasion,
                "attack_rating": spec.attack_rating,
                "defense_rating": spec.defense_rating
            },
            
            # Combat turn cost
            attack_turn_cost=getattr(spec, 'attack_turn_cost', None),

            # Genesis and equipment
            genesis_devices=0,
            max_genesis_devices=spec.max_genesis_devices,
            mines=0,
            max_mines=spec.max_drones,  # Using drones as mine capacity for simplicity

            # Special flags for Escape Pod
            is_destroyed=False,
            is_flagship=True,  # Initially the flagship
            purchase_value=spec.base_cost,
            current_value=spec.base_cost,

            # Initialize upgrades
            upgrades={},

            # Initialize equipment slots
            equipment_slots={},

            # Initialize insurance (none by default)
            insurance=None
        )
        
        # Add to database
        self.db.add(ship)
        self.db.flush()  # Get the ID
        
        logger.info(f"Created ship {ship.name} ({ship_type.value}) for player {owner_id}")
        return ship
    
    def destroy_ship(self, ship: Ship, destroyer: Optional[Player] = None, cause: str = "combat") -> Ship:
        """
        Destroy a ship and handle Escape Pod ejection if needed.
        Returns the ship the player ends up in (could be escape pod).

        cause="warp_gate_anchor" is the ADR-0029 planned dismantle: the Warp
        Jumper hull fuses into the gate focus, so there is NO insurance payout
        (Warp Jumpers are non-insurable), NO 10% emergency-cargo haircut (ALL
        non-bound cargo transfers to the pod), and destruction_cause is set to
        WARP_GATE_ANCHOR. No CargoWreck is generated on ANY path through this
        method — wreck generation lives in CombatService, which never handles
        this cause.
        """
        player = ship.owner

        # Check if ship is an Escape Pod - if so, it cannot be destroyed
        if ship.type == ShipType.ESCAPE_POD:
            logger.warning(f"Attempted to destroy indestructible Escape Pod for player {player.id}")
            return ship  # Return the same ship (indestructible)

        is_planned_dismantle = cause == "warp_gate_anchor"

        # Is the owner actually piloting THIS hull? Only the piloted hull's
        # destruction ejects the pilot into the escape pod. Consuming an
        # unpiloted hull (the owner switched ships — e.g. a Warp Jumper
        # anchored as a gate focus while the player flies something else)
        # must NOT reseat the player or relocate their active vehicle
        # (FIX 6 — pilot hijack). Cargo still transfers to a pod at the
        # dead hull's sector so the owner's property isn't silently lost.
        is_piloted = player.current_ship_id == ship.id

        # Mark ship as destroyed
        ship.is_destroyed = True
        ship.is_active = False
        # Contract: WARP_GATE_ANCHOR for the planned dismantle; other causes
        # record their raw string (e.g. "combat").
        ship.destruction_cause = "WARP_GATE_ANCHOR" if is_planned_dismantle else cause

        if is_piloted:
            # Pilot ejects: reuse/relocate the player's escape pod and reseat.
            escape_pod = self._ensure_escape_pod(player, ship.sector_id)
        else:
            # Unpiloted hull: materialize a pod at the dead hull's sector to
            # receive cargo WITHOUT moving the player's active pod or
            # reseating them.
            escape_pod = self._pod_for_unpiloted_hull(player, ship.sector_id)

        if is_planned_dismantle:
            # ADR-0029: planned dismantle — all non-bound cargo transfers
            self._transfer_all_cargo(ship, escape_pod)
        else:
            # Transfer emergency cargo to escape pod (10% of original cargo)
            self._transfer_emergency_cargo(ship, escape_pod)

        # Set escape pod as player's current ship ONLY when the piloted hull
        # was destroyed (FIX 6).
        if is_piloted:
            player.current_ship_id = escape_pod.id

        # Apply insurance if available. Skipped entirely for the warp-gate
        # anchor: no underwriter writes a policy on a hull whose canonical
        # use is its own destruction (ADR-0029).
        # Coverage attaches to the HULL (ship-insurance.md), so the policy lives
        # on ship.insurance; the payout credits player == ship.owner (the
        # registered owner, never the current pilot — handles stolen hulls).
        if not is_planned_dismantle and ship.insurance:
            compensation = self._calculate_insurance_payout(ship, ship.insurance)
            if compensation > 0:
                player.credits += compensation
                logger.info(f"Applied insurance payout of {compensation} credits to player {player.id}")

        logger.info(
            f"Ship {ship.name} destroyed for player {player.id} (cause: {cause}), "
            f"{'pilot ejected to Escape Pod' if is_piloted else 'unpiloted hull — pilot untouched'}"
        )
        return escape_pod
    
    def _ensure_escape_pod(self, player: Player, sector_id: int) -> Ship:
        """Ensure player has an escape pod, create one if needed"""
        
        # Check if player already has an escape pod
        escape_pod = self.db.query(Ship).filter(
            Ship.owner_id == player.id,
            Ship.type == ShipType.ESCAPE_POD,
            Ship.is_destroyed == False
        ).first()
        
        if escape_pod:
            # Move existing escape pod to current sector
            escape_pod.sector_id = sector_id
            escape_pod.is_active = True
            logger.info(f"Using existing Escape Pod for player {player.id}")
            return escape_pod
        
        # Create new escape pod
        escape_pod = self.create_ship(
            ship_type=ShipType.ESCAPE_POD,
            owner_id=player.id,
            sector_id=sector_id,
            name="Emergency Escape Pod"
        )
        
        logger.info(f"Created new Escape Pod for player {player.id}")
        return escape_pod
    
    def _pod_for_unpiloted_hull(self, player: Player, sector_id: int) -> Ship:
        """Provide an escape pod to receive the cargo of an UNPILOTED hull
        being consumed, without reseating the player (FIX 6).

        Preserves the single-pod-per-player invariant (_ensure_escape_pod uses
        .first()): if the player owns a pod they are NOT currently piloting,
        reuse it and relocate it to the dead hull's sector to receive the
        cargo (the player isn't aboard, so relocation is harmless). If the
        player IS currently piloting their only pod, or owns none, create a
        fresh pod at the hull's sector so cargo isn't silently destroyed. In
        no case is player.current_ship_id touched here."""
        existing = self.db.query(Ship).filter(
            Ship.owner_id == player.id,
            Ship.type == ShipType.ESCAPE_POD,
            Ship.is_destroyed == False  # noqa: E712
        ).first()

        if existing is not None and existing.id != player.current_ship_id:
            existing.sector_id = sector_id
            existing.is_active = True
            return existing

        return self.create_ship(
            ship_type=ShipType.ESCAPE_POD,
            owner_id=player.id,
            sector_id=sector_id,
            name="Emergency Escape Pod"
        )

    def _transfer_all_cargo(self, destroyed_ship: Ship, escape_pod: Ship) -> None:
        """ADR-0029 planned-dismantle transfer: ALL non-bound cargo moves to
        the escape pod, intentionally ignoring the pod's capacity — canon's
        "non-bound cargo transfers to the pilot's escape pod inventory" is
        unconditional, and dropping the gate-builder's remaining materials
        over a capacity clamp would silently destroy player property. The pod
        may sit over capacity until the player offloads (purchases/loads onto
        an over-full pod are blocked by the normal space checks)."""
        destroyed_cargo = destroyed_ship.cargo or {}
        destroyed_contents: Dict[str, int] = destroyed_cargo.get("contents") or {}
        if not destroyed_contents:
            return

        pod_cargo = escape_pod.cargo or {"capacity": 0, "used": 0, "contents": {}}
        pod_contents: Dict[str, int] = pod_cargo.get("contents") or {}

        transferred: Dict[str, int] = {}
        for resource, amount in list(destroyed_contents.items()):
            if not isinstance(amount, (int, float)) or amount <= 0:
                continue
            pod_contents[resource] = int(pod_contents.get(resource, 0)) + int(amount)
            transferred[resource] = int(amount)
            del destroyed_contents[resource]

        if not transferred:
            return

        destroyed_cargo["contents"] = destroyed_contents
        destroyed_cargo["used"] = sum(
            int(q) for q in destroyed_contents.values() if isinstance(q, (int, float))
        )
        pod_cargo["contents"] = pod_contents
        pod_cargo["used"] = sum(
            int(q) for q in pod_contents.values() if isinstance(q, (int, float))
        )
        destroyed_ship.cargo = destroyed_cargo
        escape_pod.cargo = pod_cargo
        flag_modified(destroyed_ship, "cargo")
        flag_modified(escape_pod, "cargo")

        logger.info(f"Transferred full cargo to Escape Pod (planned dismantle): {transferred}")

    def _transfer_emergency_cargo(self, destroyed_ship: Ship, escape_pod: Ship) -> None:
        """Transfer 10% of cargo contents from destroyed ship to escape pod.

        Operates on the real cargo JSONB shape
        {"capacity": n, "used": n, "contents": {commodity: qty}} (see
        create_ship) — mirrors CombatService._transfer_cargo. Treating the
        cargo dict as flat {resource: qty} made sum() blow up on the nested
        contents dict, 500ing every ship destruction.
        """
        destroyed_cargo = destroyed_ship.cargo or {}
        destroyed_contents: Dict[str, int] = destroyed_cargo.get("contents") or {}
        if not destroyed_contents:
            return

        pod_cargo = escape_pod.cargo or {}
        pod_contents: Dict[str, int] = pod_cargo.get("contents") or {}

        # Pod capacity from its own cargo record, falling back to the spec
        pod_capacity = pod_cargo.get("capacity") or 0
        if not pod_capacity:
            escape_pod_spec = self.db.query(ShipSpecification).filter(
                ShipSpecification.type == ShipType.ESCAPE_POD
            ).first()
            pod_capacity = escape_pod_spec.max_cargo if escape_pod_spec else 0

        pod_used = sum(int(q) for q in pod_contents.values() if isinstance(q, (int, float)))
        available_space = max(0, int(pod_capacity) - pod_used)
        if available_space <= 0:
            return

        # Move 10% of each commodity (at least 1 unit), clamped to what the
        # destroyed ship actually holds and the pod's remaining space
        transferred: Dict[str, int] = {}
        for resource, amount in list(destroyed_contents.items()):
            if available_space <= 0:
                break
            if not isinstance(amount, (int, float)) or amount <= 0:
                continue
            emergency_amount = min(max(1, int(amount * 0.1)), int(amount), available_space)
            if emergency_amount <= 0:
                continue

            destroyed_contents[resource] = int(amount) - emergency_amount
            if destroyed_contents[resource] <= 0:
                del destroyed_contents[resource]
            pod_contents[resource] = int(pod_contents.get(resource, 0)) + emergency_amount
            transferred[resource] = emergency_amount
            available_space -= emergency_amount

        if not transferred:
            return

        # Write back with recalculated usage; flag_modified is required for
        # SQLAlchemy to detect in-place JSONB mutation
        destroyed_cargo["contents"] = destroyed_contents
        destroyed_cargo["used"] = sum(int(q) for q in destroyed_contents.values())
        pod_cargo["contents"] = pod_contents
        pod_cargo["used"] = sum(int(q) for q in pod_contents.values())
        destroyed_ship.cargo = destroyed_cargo
        escape_pod.cargo = pod_cargo
        flag_modified(destroyed_ship, "cargo")
        flag_modified(escape_pod, "cargo")

        logger.info(f"Transferred emergency cargo to Escape Pod: {transferred}")
    
    def _calculate_insurance_payout(self, ship: Ship, insurance: Dict[str, Any]) -> int:
        """Calculate insurance payout for a destroyed ship.

        Canon (ADR-0061 S-D3): payout = (coverage% - deductible%) x purchase_value.
        Net payout per tier = BASIC 50-5=45%, STANDARD 75-10=65%, PREMIUM 90-15=75%
        (the deductible was previously not applied — fixed here).
        """
        insurance_type = insurance.get("type", "NONE")
        net_payout = {"BASIC": 0.45, "STANDARD": 0.65, "PREMIUM": 0.75}.get(insurance_type, 0.0)
        return int(ship.purchase_value * net_payout)
    
    def is_ship_indestructible(self, ship: Ship) -> bool:
        """Check if a ship is indestructible (like Escape Pod)"""
        return ship.type == ShipType.ESCAPE_POD
    
    def get_ship_specifications(self, ship_type: ShipType) -> Optional[ShipSpecification]:
        """Get ship specifications for a given ship type"""
        return self.db.query(ShipSpecification).filter(
            ShipSpecification.type == ship_type
        ).first()
    
    def repair_ship(self, ship: Ship, repair_percentage: float = 100.0) -> Dict[str, Any]:
        """Repair a ship's hull and shields"""
        if ship.is_destroyed:
            return {"success": False, "message": "Cannot repair destroyed ship"}
        
        if ship.type == ShipType.ESCAPE_POD:
            # Escape pods have minimal repair needs
            ship.combat["hull"] = ship.combat["max_hull"]
            ship.combat["shields"] = ship.combat["max_shields"]
            # In-place JSONB mutation needs an explicit dirty flag or the
            # caller's commit silently drops the restore.
            flag_modified(ship, "combat")
            return {"success": True, "message": "Escape Pod systems restored"}

        # Get current combat stats
        combat = ship.combat
        max_hull = combat["max_hull"]
        max_shields = combat["max_shields"]
        cur_hull = combat["hull"]
        cur_shields = combat["shields"]

        if repair_percentage >= 100:
            # Full repair restores hull/shields to max exactly — no float
            # truncation gap at the cap.
            new_hull = max_hull
            new_shields = max_shields
        else:
            # Partial repair: restore repair_percentage of the missing pool.
            # Round to 1 decimal to match the resolver's stored precision
            # (combat-resolver.md damage stack rounds hull/shields to 1dp);
            # the old int() truncated fractional restores away.
            new_hull = min(max_hull, round(cur_hull + (max_hull - cur_hull) * (repair_percentage / 100.0), 1))
            new_shields = min(max_shields, round(cur_shields + (max_shields - cur_shields) * (repair_percentage / 100.0), 1))

        hull_repair = round(new_hull - cur_hull, 1)
        shield_repair = round(new_shields - cur_shields, 1)

        # Apply repairs
        combat["hull"] = new_hull
        combat["shields"] = new_shields
        # In-place JSONB mutation: SQLAlchemy needs flag_modified to detect it
        # and emit the UPDATE, otherwise the repair is lost on commit.
        flag_modified(ship, "combat")

        # Update maintenance
        if "maintenance" not in ship.maintenance:
            ship.maintenance = {}
        ship.maintenance["last_maintenance"] = datetime.utcnow().isoformat()
        ship.maintenance["condition"] = min(100.0, ship.maintenance.get("condition", 0) + repair_percentage)
        
        return {
            "success": True,
            "message": f"Ship repaired: +{hull_repair} hull, +{shield_repair} shields",
            "hull_repaired": hull_repair,
            "shields_repaired": shield_repair
        }