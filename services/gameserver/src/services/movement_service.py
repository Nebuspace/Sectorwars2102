import logging
import uuid
from typing import List, Dict, Any, Optional, Tuple
from datetime import datetime
from sqlalchemy.orm import Session
from sqlalchemy import and_, or_

from src.models.player import Player
from src.models.ship import Ship, ShipStatus, ShipType
from src.models.sector import Sector, sector_warps
from src.models.warp_tunnel import WarpTunnel, WarpTunnelStatus, WarpTunnelType
from src.models.combat import CombatResult
from src.models.combat_log import CombatLog
from sqlalchemy.orm.attributes import flag_modified

from src.services import warp_gate_service
from src.services.turn_service import spend_turns

logger = logging.getLogger(__name__)


def _is_player_gate(tunnel: WarpTunnel) -> bool:
    """Player-built warp gate: ARTIFICIAL tunnel with created_by_player_id set
    (the created_by_player_id IS NOT NULL predicate distinguishes player gates
    from generator-placed ARTIFICIAL connections — warp-gates.md). Traversal
    is 0 turns and strictly one-way."""
    return (
        tunnel.type == WarpTunnelType.ARTIFICIAL
        and tunnel.created_by_player_id is not None
    )


class MovementService:
    """Service for managing player movement through the galaxy."""
    
    def __init__(self, db: Session):
        self.db = db
    
    def move_player_to_sector(self, player_id: uuid.UUID, destination_sector_id: int) -> Dict[str, Any]:
        """
        Move a player to a destination sector.
        Returns a dict with success status, message, and turn cost.
        """
        # Lazily complete any HARMONIZING warp gates touching the player's
        # current sector BEFORE locking the player row (gate row is locked
        # first — same lock order as warp_gate_service) so a freshly
        # harmonized gate is traversable without a separate listing call.
        # Failures here must not block normal movement (ARIA-hook pattern).
        try:
            pre_player = self.db.query(Player).filter(Player.id == player_id).first()
            if pre_player:
                warp_gate_service.advance_gates_touching_sector(
                    self.db, pre_player.current_sector_id
                )
        except Exception as e:
            # Roll back so a failed advance can't poison the movement
            # transaction that follows (nothing else is pending yet).
            logger.error("Failed lazy warp-gate advance during movement: %s", e)
            self.db.rollback()

        # Lock player row to prevent concurrent movement race conditions
        player = self.db.query(Player).filter(Player.id == player_id).with_for_update().first()
        if not player:
            return {"success": False, "message": "Player not found", "turn_cost": 0}

        # Block movement if player is docked at a port or landed on a planet
        if player.is_docked:
            return {"success": False, "message": "You must undock before moving to another sector", "turn_cost": 0}
        if player.is_landed:
            return {"success": False, "message": "You must leave the planet before moving to another sector", "turn_cost": 0}

        # Ensure player has an active ship
        if not player.current_ship:
            return {"success": False, "message": "No active ship selected", "turn_cost": 0}

        # A Warp Jumper harmonizing into a gate focus is frozen in place
        # (ADR-0029 / ADR-0036). Reject movement before any turn charge so a
        # mid-build hull can't fly off mid-harmonization. 0 turn cost.
        if player.current_ship.status == ShipStatus.HARMONIZING:
            return {
                "success": False,
                "message": "Your ship is harmonizing into a warp gate focus "
                           "and cannot move — cancel the anchor first",
                "turn_cost": 0,
            }

        current_sector_id = player.current_sector_id
        
        # Return early if already in the destination sector
        if current_sector_id == destination_sector_id:
            return {"success": True, "message": "Already in this sector", "turn_cost": 0}
        
        # Prefer a 0-turn ACTIVE player-built warp gate over a parallel direct
        # warp (FIX 7). The available-moves listing advertises the gate at 0
        # turns; if a direct warp ALSO connects origin -> destination, charging
        # the direct-warp cost here would contradict the advertised 0. Take the
        # gate first so the charged cost matches what the player was shown.
        if self._has_player_gate(current_sector_id, destination_sector_id):
            result = self._execute_movement(player, destination_sector_id, 0)
            tunnel_events = self._check_for_tunnel_events(
                player, current_sector_id, destination_sector_id
            )
            encounters = self._check_for_encounters(player, destination_sector_id)
            result.update({"tunnel_events": tunnel_events, "encounters": encounters})
            return result

        # Check if direct warp exists
        can_warp, warp_cost, warp_message = self._check_direct_warp(
            current_sector_id, destination_sector_id, player.current_ship
        )

        if can_warp:
            # Check if player has enough turns
            if player.turns < warp_cost:
                return {"success": False, "message": "Not enough turns for this movement", "turn_cost": warp_cost}

            # Execute the move
            result = self._execute_movement(player, destination_sector_id, warp_cost)

            # Check for encounters
            encounters = self._check_for_encounters(player, destination_sector_id)

            # Combine results
            result.update({"encounters": encounters})
            return result

        # Check if warp tunnel exists
        can_tunnel, tunnel_cost, tunnel_message = self._check_warp_tunnel(
            current_sector_id, destination_sector_id, player.current_ship
        )
        
        if can_tunnel:
            # Check if player has enough turns
            if player.turns < tunnel_cost:
                return {"success": False, "message": "Not enough turns for this warp tunnel jump", "turn_cost": tunnel_cost}
            
            # Execute the move
            result = self._execute_movement(player, destination_sector_id, tunnel_cost)
            
            # Check for tunnel-specific events
            tunnel_events = self._check_for_tunnel_events(player, current_sector_id, destination_sector_id)
            
            # Check for encounters
            encounters = self._check_for_encounters(player, destination_sector_id)
            
            # Combine results
            result.update({"tunnel_events": tunnel_events, "encounters": encounters})
            return result
        
        # If we get here, no valid path was found
        return {"success": False, "message": "No valid path to destination sector", "turn_cost": 0}
    
    def get_available_moves(self, player_id: uuid.UUID) -> Dict[str, Any]:
        """
        Get all sectors a player can move to from their current position.
        Returns a dict with direct warps and warp tunnels available.
        """
        player = self.db.query(Player).filter(Player.id == player_id).first()
        if not player:
            return {"warps": [], "tunnels": []}

        # Lazily complete HARMONIZING warp gates touching this sector so a
        # freshly-harmonized gate appears in the listing without a separate
        # poll (warp_gate_service owns the completion semantics). Listing
        # must keep working even if the advance fails.
        try:
            if warp_gate_service.advance_gates_touching_sector(
                self.db, player.current_sector_id
            ):
                self.db.commit()
        except Exception as e:
            logger.error("Failed lazy warp-gate advance during move listing: %s", e)
            self.db.rollback()

        # Get current sector
        current_sector = self.db.query(Sector).filter(Sector.sector_id == player.current_sector_id).first()
        if not current_sector:
            return {"warps": [], "tunnels": []}
        
        # Get ship for capabilities
        ship = player.current_ship
        
        # Get direct warps. sector_warps stores bidirectional connections
        # as ONE row (source, dest, is_bidirectional=true) per the
        # bang-integration schema map — so a sector reaches its
        # bidirectional neighbours via *incoming* rows too, not just
        # outgoing. Walk both sides.
        direct_warps = []
        seen_sector_ids: set = set()

        # Outgoing edges (this sector is the source).
        for connected_sector in current_sector.outgoing_warps:
            warp_cost = self._calculate_warp_cost(current_sector, connected_sector, ship)
            direct_warps.append({
                "sector_id": connected_sector.sector_id,
                "name": connected_sector.name,
                "type": connected_sector.type.name,
                "turn_cost": warp_cost,
                "can_afford": player.turns >= warp_cost
            })
            seen_sector_ids.add(connected_sector.sector_id)

        # Incoming bidirectional edges — the bang translator stores a
        # two-way warp A↔B as one row (source=A, dest=B, bidir=true),
        # so from B's POV the reverse traversal is this incoming row.
        # Without this branch a player can leave a sector but cannot
        # warp back along a bidirectional connection.
        incoming_bidir_rows = self.db.execute(
            sector_warps.select().where(
                sector_warps.c.destination_sector_id == current_sector.id,
                sector_warps.c.is_bidirectional == True,  # noqa: E712 — SQLA boolean column compare
            )
        ).fetchall()
        for row in incoming_bidir_rows:
            origin = self.db.query(Sector).filter(Sector.id == row.source_sector_id).first()
            if origin is None or origin.sector_id in seen_sector_ids:
                continue
            warp_cost = self._calculate_warp_cost(current_sector, origin, ship)
            direct_warps.append({
                "sector_id": origin.sector_id,
                "name": origin.name,
                "type": origin.type.name,
                "turn_cost": warp_cost,
                "can_afford": player.turns >= warp_cost
            })
            seen_sector_ids.add(origin.sector_id)

        # Get warp tunnels - both outgoing and incoming (for bidirectional)
        warp_tunnels = []

        # Outgoing tunnels (origin is current sector)
        outgoing_tunnels = self.db.query(WarpTunnel).filter(
            WarpTunnel.origin_sector_id == current_sector.id,
            WarpTunnel.status == WarpTunnelStatus.ACTIVE
        ).all()

        for tunnel in outgoing_tunnels:
            dest_sector = self.db.query(Sector).filter(Sector.id == tunnel.destination_sector_id).first()
            if dest_sector:
                tunnel_cost = tunnel.turn_cost
                player_gate = _is_player_gate(tunnel)

                if player_gate:
                    # Player warp gate: 0 turns flat — no ship-type
                    # multipliers and no max(1, ...) clamp (warp-gates.md).
                    tunnel_cost = 0
                elif ship and ship.warp_capable:
                    tunnel_cost = max(1, int(tunnel_cost * 0.8))  # 20% reduction for warp-capable ships

                warp_tunnels.append({
                    "sector_id": dest_sector.sector_id,
                    "name": dest_sector.name,
                    "type": dest_sector.type.name,
                    "turn_cost": tunnel_cost,
                    # Contract: gate entries surface as tunnel_type
                    # "warp_gate" so the client renders them unchanged.
                    "tunnel_type": "warp_gate" if player_gate else tunnel.type.name,
                    "stability": tunnel.stability,
                    "one_way": not tunnel.is_bidirectional,
                    "can_afford": player.turns >= tunnel_cost
                })

        # Incoming bidirectional tunnels (destination is current sector, but tunnel is bidirectional)
        incoming_bidirectional = self.db.query(WarpTunnel).filter(
            WarpTunnel.destination_sector_id == current_sector.id,
            WarpTunnel.is_bidirectional == True,
            WarpTunnel.status == WarpTunnelStatus.ACTIVE
        ).all()

        for tunnel in incoming_bidirectional:
            # The "destination" for travel is the tunnel's origin sector
            dest_sector = self.db.query(Sector).filter(Sector.id == tunnel.origin_sector_id).first()
            if dest_sector:
                # Don't add duplicates (in case there's already a tunnel in the other direction)
                if any(t["sector_id"] == dest_sector.sector_id for t in warp_tunnels):
                    continue

                tunnel_cost = tunnel.turn_cost

                # Apply ship-specific adjustments
                if ship and ship.warp_capable:
                    tunnel_cost = max(1, int(tunnel_cost * 0.8))  # 20% reduction for warp-capable ships

                warp_tunnels.append({
                    "sector_id": dest_sector.sector_id,
                    "name": dest_sector.name,
                    "type": dest_sector.type.name,
                    "turn_cost": tunnel_cost,
                    "tunnel_type": tunnel.type.name,
                    "stability": tunnel.stability,
                    # This branch only matches is_bidirectional rows, so the
                    # reverse traversal is by definition not one-way. Player
                    # gates (always one-way) can never appear here.
                    "one_way": False,
                    "can_afford": player.turns >= tunnel_cost
                })

        return {
            "warps": direct_warps,
            "tunnels": warp_tunnels
        }
    
    def get_path_between_sectors(self, start_sector_id: int, end_sector_id: int) -> List[Dict[str, Any]]:
        """
        Find the shortest path between two sectors.
        Returns a list of sectors in the path with turn costs.
        """
        # Get sectors
        start_sector = self.db.query(Sector).filter(Sector.sector_id == start_sector_id).first()
        end_sector = self.db.query(Sector).filter(Sector.sector_id == end_sector_id).first()
        
        if not start_sector or not end_sector:
            return []
        
        # Simple BFS for path finding
        visited = {start_sector.id: None}  # Maps sector ID to previous sector ID
        queue = [(start_sector, 0)]  # (sector, distance)
        
        while queue:
            current, distance = queue.pop(0)
            
            # If we've reached the destination
            if current.id == end_sector.id:
                break
            
            # Add all neighbors to the queue. Walk both outgoing edges and
            # incoming bidirectional edges so BFS pathfinding can traverse
            # bang's bidirectional sector_warps in reverse.
            for neighbor in current.outgoing_warps:
                if neighbor.id not in visited:
                    visited[neighbor.id] = current.id
                    queue.append((neighbor, distance + 1))
            incoming_bidir = self.db.execute(
                sector_warps.select().where(
                    sector_warps.c.destination_sector_id == current.id,
                    sector_warps.c.is_bidirectional == True,  # noqa: E712
                )
            ).fetchall()
            for row in incoming_bidir:
                if row.source_sector_id in visited:
                    continue
                origin = self.db.query(Sector).filter(Sector.id == row.source_sector_id).first()
                if origin is None:
                    continue
                visited[origin.id] = current.id
                queue.append((origin, distance + 1))
            
            # Check warp tunnels
            tunnels = self.db.query(WarpTunnel).filter(
                WarpTunnel.origin_sector_id == current.id,
                WarpTunnel.status == WarpTunnelStatus.ACTIVE
            ).all()
            
            for tunnel in tunnels:
                dest = self.db.query(Sector).filter(Sector.id == tunnel.destination_sector_id).first()
                if dest and dest.id not in visited:
                    visited[dest.id] = current.id
                    queue.append((dest, distance + 1))
        
        # If we didn't reach the end sector
        if end_sector.id not in visited:
            return []
        
        # Reconstruct the path
        path = []
        current_id = end_sector.id
        
        while current_id is not None:
            current_sector = self.db.query(Sector).filter(Sector.id == current_id).first()
            if current_sector:
                path.insert(0, {
                    "sector_id": current_sector.sector_id,
                    "name": current_sector.name,
                    "type": current_sector.type.name
                })
            
            current_id = visited[current_id]
        
        # Calculate turn costs between each step
        for i in range(len(path) - 1):
            from_sector_id = path[i]["sector_id"]
            to_sector_id = path[i + 1]["sector_id"]
            
            from_sector = self.db.query(Sector).filter(Sector.sector_id == from_sector_id).first()
            to_sector = self.db.query(Sector).filter(Sector.sector_id == to_sector_id).first()
            
            # Check if direct warp (either direction for bidirectional
            # sector_warps rows) or tunnel.
            if self._is_directly_connected(from_sector, to_sector):
                path[i + 1]["turn_cost"] = self._calculate_warp_cost(from_sector, to_sector, None)
                path[i + 1]["connection_type"] = "warp"
            else:
                # Must be a tunnel
                tunnel = self.db.query(WarpTunnel).filter(
                    WarpTunnel.origin_sector_id == from_sector.id,
                    WarpTunnel.destination_sector_id == to_sector.id,
                    WarpTunnel.status == WarpTunnelStatus.ACTIVE
                ).first()
                
                if tunnel:
                    path[i + 1]["turn_cost"] = tunnel.turn_cost
                    path[i + 1]["connection_type"] = "tunnel"
                else:
                    path[i + 1]["turn_cost"] = 999  # Should not happen
                    path[i + 1]["connection_type"] = "unknown"
        
        # Set turn cost for first sector to 0
        if path:
            path[0]["turn_cost"] = 0
            path[0]["connection_type"] = "start"
        
        return path
    
    def _is_directly_connected(self, from_sector: Sector, to_sector: Sector) -> bool:
        """True if there's a usable direct warp from ``from_sector`` to ``to_sector``.

        bang's translator stores bidirectional warps as a single row
        (source=A, dest=B, is_bidirectional=True). The reverse traversal
        is therefore NOT in ``from_sector.outgoing_warps`` — we have to
        also accept ``to_sector`` as the source of a row that points back
        to ``from_sector`` when that row is bidirectional. See
        DOCS/PLANS/bang-integration-schema-map.md.
        """
        if to_sector in from_sector.outgoing_warps:
            return True
        reverse_row = self.db.execute(
            sector_warps.select().where(
                sector_warps.c.source_sector_id == to_sector.id,
                sector_warps.c.destination_sector_id == from_sector.id,
                sector_warps.c.is_bidirectional == True,  # noqa: E712
            )
        ).first()
        return reverse_row is not None

    def _check_direct_warp(self, current_sector_id: int, destination_sector_id: int, ship: Ship) -> Tuple[bool, int, str]:
        """Check if a direct warp is possible and calculate turn cost."""
        # Get sector objects
        current_sector = self.db.query(Sector).filter(Sector.sector_id == current_sector_id).first()
        destination_sector = self.db.query(Sector).filter(Sector.sector_id == destination_sector_id).first()

        if not current_sector or not destination_sector:
            return False, 0, "Invalid sector IDs"

        # Check if destination is directly connected (either direction
        # for bidirectional sector_warps rows).
        if not self._is_directly_connected(current_sector, destination_sector):
            return False, 0, "Sectors are not directly connected"

        # Calculate turn cost
        turn_cost = self._calculate_warp_cost(current_sector, destination_sector, ship)

        return True, turn_cost, "Direct warp available"
    
    def _has_player_gate(self, current_sector_id: int, destination_sector_id: int) -> bool:
        """True if an ACTIVE player-built warp gate connects origin ->
        destination (FIX 7). Player gates are one-way ARTIFICIAL tunnels with
        created_by_player_id set and a flat 0-turn cost; they outrank a
        parallel direct warp so the charged cost matches the advertised 0."""
        current_sector = self.db.query(Sector).filter(
            Sector.sector_id == current_sector_id
        ).first()
        destination_sector = self.db.query(Sector).filter(
            Sector.sector_id == destination_sector_id
        ).first()
        if not current_sector or not destination_sector:
            return False

        tunnel = self.db.query(WarpTunnel).filter(
            WarpTunnel.origin_sector_id == current_sector.id,
            WarpTunnel.destination_sector_id == destination_sector.id,
            WarpTunnel.status == WarpTunnelStatus.ACTIVE,
        ).first()
        return tunnel is not None and _is_player_gate(tunnel)

    def _check_warp_tunnel(self, current_sector_id: int, destination_sector_id: int, ship: Ship) -> Tuple[bool, int, str]:
        """Check if a warp tunnel is available and calculate turn cost."""
        # Get sector objects
        current_sector = self.db.query(Sector).filter(Sector.sector_id == current_sector_id).first()
        destination_sector = self.db.query(Sector).filter(Sector.sector_id == destination_sector_id).first()

        if not current_sector or not destination_sector:
            return False, 0, "Invalid sector IDs"

        # Check for active warp tunnel (outgoing direction)
        tunnel = self.db.query(WarpTunnel).filter(
            WarpTunnel.origin_sector_id == current_sector.id,
            WarpTunnel.destination_sector_id == destination_sector.id,
            WarpTunnel.status == WarpTunnelStatus.ACTIVE
        ).first()

        # If no outgoing tunnel, check for bidirectional tunnel in reverse direction
        if not tunnel:
            tunnel = self.db.query(WarpTunnel).filter(
                WarpTunnel.origin_sector_id == destination_sector.id,
                WarpTunnel.destination_sector_id == current_sector.id,
                WarpTunnel.is_bidirectional == True,
                WarpTunnel.status == WarpTunnelStatus.ACTIVE
            ).first()

        if not tunnel:
            return False, 0, "No active warp tunnel found"

        # Player-built warp gate: 0 turns flat, no ship-type multipliers and
        # no max(1, ...) clamp (warp-gates.md). Strictly one-way: the reverse
        # branch above only matches is_bidirectional rows and gates are
        # created is_bidirectional=False, so a gate matched here is
        # guaranteed origin -> destination.
        if _is_player_gate(tunnel):
            return True, tunnel.turn_cost, "Warp gate available"

        # Get base turn cost
        turn_cost = tunnel.turn_cost

        # Non-warp-capable ships pay a higher cost for advanced tunnel types
        if tunnel.type.name in ["QUANTUM", "UNSTABLE"] and ship and not getattr(ship, 'warp_capable', False):
            turn_cost = max(1, int(turn_cost * 1.5))  # 50% surcharge for non-warp-capable ships
        elif ship and getattr(ship, 'warp_capable', False):
            turn_cost = max(1, int(turn_cost * 0.8))  # 20% reduction for warp-capable ships

        return True, turn_cost, "Warp tunnel available"
    
    def _calculate_warp_cost(self, from_sector: Sector, to_sector: Sector, ship: Optional[Ship]) -> int:
        """Calculate turn cost for a direct warp between sectors."""
        # Find the warp connection details. Try the forward direction first;
        # if missing, try the reverse direction with is_bidirectional=true
        # (the bang translator stores A↔B as ONE row per the schema map,
        # so reverse traversal reads the same row).
        warp = self.db.query(sector_warps).filter(
            sector_warps.c.source_sector_id == from_sector.id,
            sector_warps.c.destination_sector_id == to_sector.id
        ).first()

        if not warp:
            warp = self.db.query(sector_warps).filter(
                sector_warps.c.source_sector_id == to_sector.id,
                sector_warps.c.destination_sector_id == from_sector.id,
                sector_warps.c.is_bidirectional == True,  # noqa: E712
            ).first()

        if not warp:
            return 999  # Very high cost if no direct connection (should not happen)
        
        # Get base turn cost from the warp
        base_cost = warp.turn_cost if warp.turn_cost else 1
        
        # Adjust based on ship type and capabilities
        if ship:
            # Fast ships have reduced movement costs
            if ship.type == ShipType.FAST_COURIER:
                base_cost = max(1, int(base_cost * 0.7))  # 30% reduction
            elif ship.type == ShipType.SCOUT_SHIP:
                base_cost = max(1, int(base_cost * 0.8))  # 20% reduction
            
            # Slower ships have increased movement costs
            elif ship.type == ShipType.CARGO_HAULER:
                base_cost = int(base_cost * 1.2)
            elif ship.type == ShipType.COLONY_SHIP:
                base_cost = int(base_cost * 1.3)
            
            # Apply ship's current speed adjustment
            if ship.current_speed < ship.base_speed:
                speed_ratio = ship.current_speed / ship.base_speed
                base_cost = int(base_cost * (2 - speed_ratio))  # 1.0-2.0x multiplier based on speed
        
        # No turn cost can be less than 1
        return max(1, base_cost)
    
    # Hull damage one armored mine deals to a hostile ship entering the sector.
    # Proposed in ADR-0083 (pending Max bless); deterrent-scale, non-lethal
    # (hull is floored at 1.0 so a minefield cripples but does not destroy —
    # lethal mines / destruction-on-zero is a documented future refinement).
    MINE_DETONATION_DAMAGE = 200.0

    def _detonate_sector_mines(self, player: Player, sector: Sector) -> None:
        """Detonate one hostile armored mine in `sector` against the entering ship.

        Mines live in sector.defenses {mines, mine_owner_id, mine_team_id}. Same
        non-null team is friendly (no detonation); the owner is never mined by
        their own field. One mine is consumed per hostile entry.
        """
        defenses = sector.defenses or {}
        mine_count = int(defenses.get("mines", 0) or 0)
        owner_id = defenses.get("mine_owner_id")
        if mine_count <= 0 or not owner_id or str(owner_id) == str(player.id):
            return

        owner_team = defenses.get("mine_team_id")
        entrant_team = str(player.team_id) if player.team_id else None
        if owner_team and entrant_team and owner_team == entrant_team:
            return  # friendly minefield — same team

        if not player.current_ship:
            return

        from src.services.combat_service import CombatService
        combat = CombatService(self.db)._ensure_combat_state(player.current_ship)
        hull = float(combat.get("hull", 0) or 0)
        # Floor at 1.0: a mine cripples, it does not destroy (v1; see ADR-0083).
        combat["hull"] = max(1.0, round(hull - self.MINE_DETONATION_DAMAGE, 1))
        flag_modified(player.current_ship, "combat")

        new_def = dict(defenses)
        remaining = mine_count - 1
        new_def["mines"] = remaining
        if remaining <= 0:
            new_def["mines"] = 0
            new_def["mine_owner_id"] = None
            new_def["mine_team_id"] = None
        sector.defenses = new_def
        flag_modified(sector, "defenses")

        logger.info(
            f"Mine detonated on player {player.id} entering sector {sector.sector_id}: "
            f"-{self.MINE_DETONATION_DAMAGE} hull (now {combat['hull']}), {remaining} mine(s) remain"
        )

    def _execute_movement(self, player: Player, destination_sector_id: int, turn_cost: int) -> Dict[str, Any]:
        """Execute a player's movement to a destination sector."""
        old_sector_id = player.current_sector_id

        # Fetch the destination up front: the move needs its region for the
        # player sync below, and the response reuses it for sector_info.
        # Fail fast BEFORE any player mutation — moving a player into a
        # sector that doesn't exist would strand them with a stale region.
        destination_sector = self.db.query(Sector).filter(Sector.sector_id == destination_sector_id).first()
        if not destination_sector:
            return {
                "success": False,
                "message": "Destination sector not found",
                "turn_cost": 0
            }

        # Update player position and clear all location state
        player.current_sector_id = destination_sector_id
        # Keep current_region_id in sync with the destination — warp tunnels
        # cross regions, and a stale region makes the region-filtered routes
        # (e.g. /player/current-sector) 404 until the next correction
        player.current_region_id = destination_sector.region_id
        player.is_docked = False  # Player is no longer docked at a port
        player.is_landed = False  # Player is no longer landed on a planet
        player.current_port_id = None  # Clear dangling port reference
        player.current_planet_id = None  # Clear dangling planet reference
        
        # Update ship position
        if player.current_ship:
            player.current_ship.sector_id = destination_sector_id

        # Mine detonation: hostile armored mines in the destination detonate
        # against the entering ship (combat.md "mines damage hostile entrants";
        # ADR-0083 damage model). Best-effort — a hook failure must never strand
        # the move; it rides the move's own commit below.
        try:
            self._detonate_sector_mines(player, destination_sector)
        except Exception as e:
            logger.error("Mine detonation hook failed: %s", e)

        # Consume turns
        spend_turns(player, turn_cost)

        # ARIA consciousness hook — movement counts as interaction
        try:
            player.aria_total_interactions += 1
            thresholds = {50: (2, 1.1), 150: (3, 1.2), 400: (4, 1.35), 1000: (5, 1.5)}
            for threshold, (level, multiplier) in thresholds.items():
                if player.aria_total_interactions >= threshold and player.aria_consciousness_level < level:
                    player.aria_consciousness_level = level
                    player.aria_bonus_multiplier = multiplier
        except Exception as e:
            logger.error("Failed ARIA hook during movement: %s", e)

        # ARIA exploration map — the per-sector visit record aria-companion.md
        # documents. This table is the known-graph source for course plotting
        # (ADR-0072): without the row ARIA has no memory of ever being here
        # and refuses to plot back. Best-effort like the consciousness hook
        # above; rides the move's own commit.
        try:
            from src.models.aria_personal_intelligence import ARIAExplorationMap
            visit = (
                self.db.query(ARIAExplorationMap)
                .filter(
                    ARIAExplorationMap.player_id == player.id,
                    ARIAExplorationMap.sector_id == destination_sector.id,
                )
                .first()
            )
            if visit:
                visit.visit_count = (visit.visit_count or 0) + 1
                visit.last_visit = datetime.utcnow()
            else:
                self.db.add(
                    ARIAExplorationMap(
                        player_id=player.id,
                        sector_id=destination_sector.id,
                    )
                )
        except Exception as e:
            logger.error("Failed ARIA exploration-map hook during movement: %s", e)

        # Updates player's presence in sector records
        self._update_player_presence(player, old_sector_id, destination_sector_id)
        
        # Commit changes
        self.db.commit()

        # Get sector information for response (sector fetched and
        # existence-checked pre-mutation above)
        sector_info = {
            "id": destination_sector.sector_id,
            "name": destination_sector.name,
            "type": destination_sector.type.name,
            "hazard_level": destination_sector.hazard_level,
            "radiation_level": destination_sector.radiation_level
        }
        
        return {
            "success": True,
            "message": f"Moved to Sector {destination_sector_id}",
            "turn_cost": turn_cost,
            "sector": sector_info,
            "turns_remaining": player.turns
        }
    
    def _update_player_presence(self, player: Player, old_sector_id: int, new_sector_id: int) -> None:
        """Update player presence records in sectors.

        Locks both sector rows (ascending sector_id) before the JSONB
        read-modify-write: with NPC movers also rewriting players_present
        (npc_movement_service), an unlocked stale-array write here would
        silently erase or resurrect NPC presence entries. Lock order is
        Player → Sector — the caller already holds the player row, and
        ascending sector order matches every other NPC-system sector
        writer, so the paths cannot deadlock AB-BA.
        """
        old_sector = None
        new_sector = None
        for sid in sorted({old_sector_id, new_sector_id}):
            row = (
                self.db.query(Sector)
                .filter(Sector.sector_id == sid)
                .with_for_update()
                .first()
            )
            if row is None:
                continue
            if sid == old_sector_id:
                old_sector = row
            if sid == new_sector_id:
                new_sector = row

        if old_sector:
            # Remove player from old sector's players_present
            players_present = list(old_sector.players_present or [])
            player_entry = next((p for p in players_present if p.get("player_id") == str(player.id)), None)
            if player_entry:
                players_present.remove(player_entry)
            old_sector.players_present = players_present
            flag_modified(old_sector, 'players_present')

        if new_sector:
            # Add player to new sector's players_present
            players_present = list(new_sector.players_present or [])
            player_entry = {
                "player_id": str(player.id),
                "username": player.username,
                "ship_id": str(player.current_ship_id) if player.current_ship_id else None,
                "ship_name": player.current_ship.name if player.current_ship else "None",
                "ship_type": player.current_ship.type.name if player.current_ship else "None",
                "team_id": str(player.team_id) if player.team_id else None,
                "arrived_at": datetime.now().isoformat()
            }

            # Check if player is already in the list (shouldn't be, but safety check)
            existing = next((p for p in players_present if p.get("player_id") == str(player.id)), None)
            if existing:
                players_present.remove(existing)

            players_present.append(player_entry)
            new_sector.players_present = players_present
            flag_modified(new_sector, 'players_present')
    
    def _check_for_encounters(self, player: Player, sector_id: int) -> List[Dict[str, Any]]:
        """Check for encounters upon entering a sector."""
        encounters = []
        
        # Get the destination sector
        sector = self.db.query(Sector).filter(Sector.sector_id == sector_id).first()
        if not sector:
            return encounters
        
        # Check for other players (PvP opportunity)
        other_players = [p for p in sector.players_present if p.get("player_id") != str(player.id)]
        if other_players:
            encounters.append({
                "type": "players",
                "players": other_players,
                "threat_level": "varies"
            })
        
        # Check for special sector events
        if sector.type.name in ["BLACK_HOLE", "NEBULA", "ASTEROID_FIELD", "WORMHOLE"]:
            encounters.append({
                "type": "sector_hazard",
                "hazard": sector.type.name,
                "threat_level": "medium" if sector.hazard_level < 7 else "high"
            })
        
        # Check for sector drones
        defense_drones = sector.defenses.get('defense_drones', 0) if sector.defenses else 0
        if defense_drones > 0:
            encounters.append({
                "type": "drones",
                "count": defense_drones,
                "threat_level": "low" if defense_drones < 10 else "medium"
            })
        
        return encounters
    
    def _check_for_tunnel_events(self, player: Player, from_sector_id: int, to_sector_id: int) -> List[Dict[str, Any]]:
        """Check for events during warp tunnel travel."""
        events = []
        
        # Get sectors
        from_sector = self.db.query(Sector).filter(Sector.sector_id == from_sector_id).first()
        to_sector = self.db.query(Sector).filter(Sector.sector_id == to_sector_id).first()
        
        if not from_sector or not to_sector:
            return events
        
        # Get the tunnel
        tunnel = self.db.query(WarpTunnel).filter(
            WarpTunnel.origin_sector_id == from_sector.id,
            WarpTunnel.destination_sector_id == to_sector.id
        ).first()
        
        if not tunnel:
            return events
        
        # Check for tunnel stability issues
        if tunnel.stability < 0.7:
            # Chance of tunnel instability causing issues
            if tunnel.stability < 0.5:
                # High instability = radiation exposure
                events.append({
                    "type": "radiation_exposure",
                    "severity": "high" if tunnel.stability < 0.3 else "medium",
                    "effect": "ship_damage"
                })
            
            # For quantum or unstable tunnels, chance of time/space anomalies
            if tunnel.type.name in ["QUANTUM", "UNSTABLE"]:
                events.append({
                    "type": "spacetime_anomaly",
                    "severity": "medium",
                    "effect": "random"
                })
        
        # Update tunnel usage counter
        if tunnel.max_uses is not None:
            tunnel.current_uses += 1
            
            # Check if tunnel is about to collapse
            if tunnel.max_uses - tunnel.current_uses <= 3:
                events.append({
                    "type": "tunnel_degradation",
                    "stability": tunnel.stability,
                    "remaining_uses": tunnel.max_uses - tunnel.current_uses,
                    "effect": "warning"
                })
                
                # If this was the last use, collapse the tunnel
                if tunnel.current_uses >= tunnel.max_uses:
                    tunnel.status = WarpTunnelStatus.COLLAPSED
                    events.append({
                        "type": "tunnel_collapse",
                        "severity": "high",
                        "effect": "permanent"
                    })
        
        return events