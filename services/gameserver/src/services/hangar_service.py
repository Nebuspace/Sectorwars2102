"""
Carrier ship-hangar service (WO-AE).

Single source of truth for the Carrier's ship-hangar — the 8 size-unit bay that
holds whole player ships in transit, SEPARATE from the Carrier's 12-drone bay
(no shared budget). Implements the canon spec at:

  - FEATURES/gameplay/ships.md "Carrier hangar" (lines 332-346)
  - DATA_MODELS/ships.md "Carrier ship-hangar" (the Ship.hangar JSONB shape)

Mechanics owned here:

  DOCK (consent)   — a docking pilot requests a slot; the Carrier captain
                     accepts. On accept the docking ship pays 1 turn (Carrier 0)
                     and becomes an inert passenger.
  UNDOCK           — the docked pilot pays 1 turn, resumes control in the
                     Carrier's CURRENT sector; NO Carrier consent.
  DISEMBARK        — when the Carrier is docked at a station, a passenger may
                     step off to the port at 0 turns.
  RIDE-ALONG       — when the Carrier moves, hangared ships' sectors follow and
                     their pilots pay 0 turns (movement_service hook).
  JETTISON         — on Carrier hull -> 0, all docked ships are jettisoned
                     INTACT into the destruction sector, pilots auto-eject to
                     Escape Pods, the docked ships spawn NO wrecks
                     (cargo/insurance unaffected). (ship_service / combat_service
                     destruction hook.)

Size axis (WO-AD): a ship's size lives on ShipSpecification.ship_size, weighted
by SIZE_UNITS via size_units_for(). CONTRACT (from WO-AD): treat a NULL
ship_size as INELIGIBLE, and branch on CAPITAL (not-dockable) BEFORE calling
size_units_for() (which RAISES for CAPITAL).

All turn charges are deferred to the route layer's spend_turns so this service
stays a pure hangar-state manager — but the dock/undock/disembark resolvers DO
update player/ship location and status, returning the canonical turn cost the
caller must charge.
"""

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from src.models.player import Player
from src.models.ship import (
    Ship,
    ShipSize,
    ShipSpecification,
    ShipStatus,
    ShipType,
    size_units_for,
)

logger = logging.getLogger(__name__)

# Canon: the Carrier ship-hangar holds 8 size-units total
# (FEATURES/gameplay/ships.md:336; DATA_MODELS/ships.md#carrier-ship-hangar).
HANGAR_CAPACITY_UNITS = 8

# Dock-request lifecycle states held in each docked[] entry's "request_state".
# PENDING  — the docking pilot has requested a slot; awaiting Carrier consent.
# DOCKED   — the Carrier captain accepted; the ship is an inert passenger.
REQUEST_PENDING = "PENDING"
REQUEST_DOCKED = "DOCKED"

# NO-CANON micro-bit (flagged in report): canon does not specify a dock-request
# expiry for the HANGAR (it DOES for the Tractor tow: 60s — ships.md:367). We
# do NOT auto-expire hangar dock requests here; a pending request lives until
# the Carrier captain accepts or the docking pilot cancels. This is the
# smallest non-inventive choice (no timer, no background sweep) and leaves the
# canon door open. See report's NO-CANON note.


class HangarError(Exception):
    """Raised for a rejected hangar operation. ``message`` is player-safe."""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class HangarService:
    def __init__(self, db: Session):
        self.db = db

    # ------------------------------------------------------------------ #
    # State helpers
    # ------------------------------------------------------------------ #
    @staticmethod
    def empty_hangar() -> Dict[str, Any]:
        """The canonical empty-hangar JSONB shape."""
        return {"capacity_units": HANGAR_CAPACITY_UNITS, "docked": []}

    def _ensure_hangar(self, carrier: Ship) -> Dict[str, Any]:
        """Return the carrier's hangar dict, initializing the empty shape on
        first use. Mutating the returned dict requires flag_modified."""
        if carrier.hangar is None:
            carrier.hangar = self.empty_hangar()
            flag_modified(carrier, "hangar")
        # Heal a partially-shaped legacy dict (defensive — never invents data).
        if "capacity_units" not in carrier.hangar:
            carrier.hangar["capacity_units"] = HANGAR_CAPACITY_UNITS
        if "docked" not in carrier.hangar or carrier.hangar["docked"] is None:
            carrier.hangar["docked"] = []
        return carrier.hangar

    @staticmethod
    def used_units(hangar: Dict[str, Any]) -> int:
        """Sum of size_units across DOCKED entries (PENDING requests do NOT yet
        consume capacity — capacity is committed at accept time)."""
        return sum(
            int(e.get("size_units", 0))
            for e in (hangar.get("docked") or [])
            if e.get("request_state") == REQUEST_DOCKED
        )

    def _ship_size(self, ship: Ship) -> Optional[ShipSize]:
        """Resolve a ship's canonical size from its ShipSpecification. Returns
        None when the spec is missing OR ship_size is NULL (NPC-only / unspecced
        hulls) — both INELIGIBLE per the WO-AD contract."""
        spec = (
            self.db.query(ShipSpecification)
            .filter(ShipSpecification.type == ship.type)
            .first()
        )
        if spec is None:
            return None
        return spec.ship_size

    def _entry_for_ship(
        self, hangar: Dict[str, Any], ship_id: uuid.UUID, state: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        sid = str(ship_id)
        for e in hangar.get("docked") or []:
            if e.get("ship_id") == sid and (state is None or e.get("request_state") == state):
                return e
        return None

    def is_ship_hangared(self, ship_id: uuid.UUID) -> bool:
        """True iff ``ship_id`` is currently a DOCKED passenger inside ANY
        Carrier's hangar. Used by the combat / movement inertness guards.

        A PENDING request does NOT make a ship hangared — the ship is still
        free in space until the Carrier accepts.
        """
        return self.find_carrier_for_docked_ship(ship_id) is not None

    def find_carrier_for_docked_ship(self, ship_id: uuid.UUID) -> Optional[Ship]:
        """Return the Carrier whose hangar holds ``ship_id`` as a DOCKED
        passenger, or None. Scans only capital-size carriers with a non-NULL
        hangar (cheap: there are very few Carriers)."""
        sid = str(ship_id)
        carriers = (
            self.db.query(Ship)
            .filter(Ship.hangar.isnot(None), Ship.is_destroyed.is_(False))
            .all()
        )
        for carrier in carriers:
            entry = self._entry_for_ship(carrier.hangar, ship_id, REQUEST_DOCKED)
            if entry is not None:
                return carrier
        return None

    # ------------------------------------------------------------------ #
    # Eligibility (the WO-AD contract: NULL ineligible; branch CAPITAL first)
    # ------------------------------------------------------------------ #
    def _eligible_size_units(self, ship: Ship) -> int:
        """Return the docking ship's size_units, or raise HangarError if it is
        ineligible to be hangared. Order matters (WO-AD CONTRACT):
          1. NULL ship_size -> INELIGIBLE (NPC-only / unspecced hulls).
          2. CAPITAL -> not-dockable (branch BEFORE size_units_for, which RAISES).
          3. otherwise -> finite size_units.
        """
        size = self._ship_size(ship)
        if size is None:
            raise HangarError(
                "That ship cannot be hangared (no canonical size — NPC or "
                "unspecified hull)."
            )
        if size == ShipSize.CAPITAL:
            raise HangarError(
                "Capital-size ships cannot be hangared — their mass exceeds the "
                "Carrier hangar's structural rating."
            )
        return size_units_for(size)

    def _require_carrier(self, ship: Ship) -> None:
        """Assert ``ship`` is a capital-size hull (the Carrier) that can OWN a
        hangar. Branch on CAPITAL via the size axis, never the ship type name
        alone, so the rule tracks the canon size enum."""
        size = self._ship_size(ship)
        if size != ShipSize.CAPITAL:
            raise HangarError("Only a Carrier has a ship-hangar.")

    # ------------------------------------------------------------------ #
    # DOCK — request + accept (consent flow)
    # ------------------------------------------------------------------ #
    def request_dock(
        self, docking_ship: Ship, carrier: Ship
    ) -> Dict[str, Any]:
        """Stage 1 of the consent flow: the docking pilot requests a hangar slot
        on ``carrier``. Validates same-sector, not-in-combat, carrier-is-carrier,
        eligibility, and capacity (against the WOULD-BE committed total). Adds a
        PENDING entry. Charges NO turns (the 1-turn cost is on accept). Does NOT
        commit (caller owns the transaction)."""
        if docking_ship.id == carrier.id:
            raise HangarError("A ship cannot dock into itself.")
        if carrier.is_destroyed or docking_ship.is_destroyed:
            raise HangarError("Destroyed ships cannot dock.")

        self._require_carrier(carrier)

        # Same sector (FEATURES/gameplay/ships.md:338).
        if docking_ship.sector_id != carrier.sector_id:
            raise HangarError("The Carrier must be in your sector to dock.")

        # Dock initiation BLOCKED while EITHER ship is IN_COMBAT
        # (no mid-fight escapes via hangar — ships.md:341).
        if docking_ship.status == ShipStatus.IN_COMBAT or carrier.status == ShipStatus.IN_COMBAT:
            raise HangarError("Cannot dock while either ship is in combat.")

        # A ship harmonizing into a warp gate focus is frozen and cannot dock.
        if docking_ship.status == ShipStatus.HARMONIZING:
            raise HangarError("Your ship is harmonizing and cannot dock.")

        # Hangar/Tractor exclusion: a ship already hangared, or actively towing
        # / being towed, cannot also be a hangar dock source
        # (DATA_MODELS/ships.md:118; ships.md:370). tow_state guard is defensive
        # for the WO-AF lane that follows.
        if self.is_ship_hangared(docking_ship.id):
            raise HangarError("That ship is already docked inside a Carrier.")
        if getattr(docking_ship, "tow_state", None):
            raise HangarError("A ship being towed cannot dock — detach the tow first.")

        units = self._eligible_size_units(docking_ship)

        hangar = self._ensure_hangar(carrier)

        # Reject a duplicate request from the same ship.
        if self._entry_for_ship(hangar, docking_ship.id) is not None:
            raise HangarError("That ship already has a pending or active dock with this Carrier.")

        # Capacity check against the committed (DOCKED) total — a PENDING
        # request does not consume units, but it MUST fit if accepted, so we
        # validate the would-be total now to give early feedback.
        if self.used_units(hangar) + units > hangar["capacity_units"]:
            raise HangarError(
                f"Not enough hangar capacity: {units} units needed, "
                f"{hangar['capacity_units'] - self.used_units(hangar)} free."
            )

        entry = {
            "ship_id": str(docking_ship.id),
            "owner_id": str(docking_ship.owner_id) if docking_ship.owner_id else None,
            "size": self._ship_size(docking_ship).value,
            "size_units": units,
            "docked_at": None,
            "request_state": REQUEST_PENDING,
            "requested_at": datetime.now(timezone.utc).isoformat(),
        }
        hangar["docked"].append(entry)
        flag_modified(carrier, "hangar")
        logger.info(
            "Hangar dock REQUEST: ship %s -> carrier %s (%s units pending)",
            docking_ship.id, carrier.id, units,
        )
        return {"status": REQUEST_PENDING, "ship_id": str(docking_ship.id), "size_units": units}

    def accept_dock(
        self, carrier: Ship, docking_ship: Ship, docking_pilot: Player
    ) -> Tuple[Dict[str, Any], int]:
        """Stage 2: the Carrier captain ACCEPTS a pending dock request. Commits
        capacity, flips the entry to DOCKED, marks the docking ship an inert
        passenger (its pilot follows the Carrier's sector). Returns
        (result, docking_turn_cost). The CALLER charges 1 turn to the docking
        pilot (Carrier pays 0). Does NOT commit."""
        self._require_carrier(carrier)
        hangar = self._ensure_hangar(carrier)

        entry = self._entry_for_ship(hangar, docking_ship.id, REQUEST_PENDING)
        if entry is None:
            raise HangarError("No pending dock request from that ship.")

        # Re-validate the live invariants at accept time (state may have changed
        # since the request: a fight started, the ship moved, the docking pilot
        # got hangared elsewhere). Same-sector + not-in-combat are the canon
        # gates; re-checking closes a request-then-fly-away / request-then-fight
        # race.
        if docking_ship.is_destroyed or carrier.is_destroyed:
            raise HangarError("Destroyed ships cannot dock.")
        if docking_ship.sector_id != carrier.sector_id:
            raise HangarError("The docking ship is no longer in the Carrier's sector.")
        if docking_ship.status == ShipStatus.IN_COMBAT or carrier.status == ShipStatus.IN_COMBAT:
            raise HangarError("Cannot complete dock while either ship is in combat.")
        if self.is_ship_hangared(docking_ship.id):
            raise HangarError("That ship is already docked inside a Carrier.")

        units = int(entry["size_units"])
        if self.used_units(hangar) + units > hangar["capacity_units"]:
            # Capacity filled by another accept since the request — drop the
            # stale request rather than overflow.
            self._remove_entry(carrier, hangar, docking_ship.id)
            raise HangarError("Hangar filled up before this request could be accepted.")

        # Commit: flip to DOCKED.
        entry["request_state"] = REQUEST_DOCKED
        entry["docked_at"] = datetime.now(timezone.utc).isoformat()

        # The docked ship becomes an inert passenger: its sector follows the
        # Carrier; its pilot's sector follows too (so the player's UI shows the
        # Carrier's location). Status DOCKED reflects "stowed in a hangar".
        docking_ship.sector_id = carrier.sector_id
        docking_ship.status = ShipStatus.DOCKED
        docking_pilot.current_sector_id = carrier.sector_id
        docking_pilot.current_region_id = self._carrier_region(carrier)
        self._schedule_region_hop(docking_pilot, docking_pilot.current_region_id)
        # A passenger is neither port-docked nor planet-landed.
        docking_pilot.is_docked = False
        docking_pilot.is_landed = False
        docking_pilot.current_port_id = None
        docking_pilot.current_planet_id = None
        # WO-DOCK-500 Leg 1: stowing a port-docked ship into a Carrier implicitly
        # undocks its pilot; release the docking-slip occupancy or it orphans and
        # 500s the next dock. (Launch/jettison FROM a hangar never held a port
        # slip, so those paths need no release.)
        from src.services.docking_service import release as _release_docking_slip
        _release_docking_slip(self.db, None, docking_pilot)

        flag_modified(carrier, "hangar")
        logger.info(
            "Hangar dock ACCEPT: ship %s now passenger in carrier %s (%d/%d units)",
            docking_ship.id, carrier.id, self.used_units(hangar), hangar["capacity_units"],
        )
        # Docking ship pays 1 turn; Carrier pays 0 (ships.md:338).
        return (
            {
                "status": REQUEST_DOCKED,
                "ship_id": str(docking_ship.id),
                "used_units": self.used_units(hangar),
                "capacity_units": hangar["capacity_units"],
            },
            1,
        )

    def cancel_request(self, carrier: Ship, ship_id: uuid.UUID) -> Dict[str, Any]:
        """Either party drops a still-PENDING request (0 turns)."""
        hangar = self._ensure_hangar(carrier)
        entry = self._entry_for_ship(hangar, ship_id, REQUEST_PENDING)
        if entry is None:
            raise HangarError("No pending dock request from that ship.")
        self._remove_entry(carrier, hangar, ship_id)
        return {"status": "CANCELLED", "ship_id": str(ship_id)}

    # ------------------------------------------------------------------ #
    # UNDOCK / DISEMBARK
    # ------------------------------------------------------------------ #
    def undock(
        self, docked_ship: Ship, docked_pilot: Player
    ) -> Tuple[Dict[str, Any], int]:
        """The docked pilot resumes control in the Carrier's CURRENT sector. NO
        Carrier consent (passengers can always disembark — ships.md:339).
        Returns (result, undock_turn_cost=1). Caller charges the pilot 1 turn.
        Does NOT commit."""
        carrier = self.find_carrier_for_docked_ship(docked_ship.id)
        if carrier is None:
            raise HangarError("That ship is not docked inside a Carrier.")

        # Resume in the Carrier's current sector.
        docked_ship.sector_id = carrier.sector_id
        docked_ship.status = ShipStatus.IN_SPACE
        docked_pilot.current_sector_id = carrier.sector_id
        docked_pilot.current_region_id = self._carrier_region(carrier)
        self._schedule_region_hop(docked_pilot, docked_pilot.current_region_id)
        docked_pilot.is_docked = False
        docked_pilot.is_landed = False

        self._remove_entry(carrier, carrier.hangar, docked_ship.id)
        logger.info(
            "Hangar UNDOCK: ship %s out of carrier %s into sector %s",
            docked_ship.id, carrier.id, carrier.sector_id,
        )
        return ({"status": "UNDOCKED", "sector_id": carrier.sector_id}, 1)

    def disembark_to_port(
        self, docked_ship: Ship, docked_pilot: Player
    ) -> Tuple[Dict[str, Any], int]:
        """When the Carrier is docked at a station, a passenger steps off to the
        PORT at 0 turns (ships.md:343). Returns (result, 0). Does NOT commit."""
        carrier = self.find_carrier_for_docked_ship(docked_ship.id)
        if carrier is None:
            raise HangarError("That ship is not docked inside a Carrier.")

        carrier_owner = carrier.owner
        carrier_at_port = (
            carrier_owner is not None
            and carrier_owner.is_docked
            and carrier_owner.current_port_id is not None
        )
        if not carrier_at_port:
            raise HangarError(
                "The Carrier is not docked at a station — you cannot disembark to a port."
            )

        # Passenger steps off INTO the port the Carrier is docked at, at 0 turns.
        docked_ship.sector_id = carrier.sector_id
        docked_ship.status = ShipStatus.DOCKED
        docked_pilot.current_sector_id = carrier.sector_id
        docked_pilot.current_region_id = carrier_owner.current_region_id
        self._schedule_region_hop(docked_pilot, docked_pilot.current_region_id)
        docked_pilot.is_docked = True
        docked_pilot.is_landed = False
        docked_pilot.current_port_id = carrier_owner.current_port_id

        self._remove_entry(carrier, carrier.hangar, docked_ship.id)
        logger.info(
            "Hangar DISEMBARK: ship %s off carrier %s to port %s (0 turns)",
            docked_ship.id, carrier.id, carrier_owner.current_port_id,
        )
        return ({"status": "DISEMBARKED", "port_id": str(carrier_owner.current_port_id)}, 0)

    # ------------------------------------------------------------------ #
    # RIDE-ALONG (movement_service hook)
    # ------------------------------------------------------------------ #
    def carry_hangared_ships(self, carrier: Ship, destination_sector_id: int) -> int:
        """Move every DOCKED passenger's ship + pilot to ``destination_sector_id``
        when the Carrier moves. Pilots pay 0 turns for the Carrier's movement
        (ships.md:340). Returns the count carried. Does NOT commit (rides the
        Carrier-move transaction). No-op for a non-Carrier or empty hangar."""
        hangar = carrier.hangar
        if not hangar or not hangar.get("docked"):
            return 0

        carried = 0
        region_id = self._carrier_region(carrier)
        for entry in hangar["docked"]:
            if entry.get("request_state") != REQUEST_DOCKED:
                continue  # PENDING requests don't ride along
            ship = self.db.query(Ship).filter(Ship.id == uuid.UUID(entry["ship_id"])).first()
            if ship is None or ship.is_destroyed:
                continue
            ship.sector_id = destination_sector_id
            # The passenger's pilot location follows the Carrier (0 turns).
            if ship.owner_id is not None:
                pilot = self.db.query(Player).filter(Player.id == ship.owner_id).first()
                if pilot is not None and pilot.current_ship_id == ship.id:
                    pilot.current_sector_id = destination_sector_id
                    pilot.current_region_id = region_id
                    self._schedule_region_hop(pilot, region_id)
            carried += 1

        if carried:
            logger.info(
                "Carrier %s carried %d hangared ship(s) to sector %s",
                carrier.id, carried, destination_sector_id,
            )
        return carried

    # ------------------------------------------------------------------ #
    # JETTISON (destruction hook)
    # ------------------------------------------------------------------ #
    def jettison_all(
        self, carrier: Ship, destruction_sector_id: int, ship_service
    ) -> List[uuid.UUID]:
        """Carrier hull -> 0: jettison ALL docked ships INTACT into the
        destruction sector; pilots auto-eject to Escape Pods. The docked ships
        are NOT destroyed and spawn NO wrecks — cargo/insurance unaffected
        (ships.md:341). Returns the list of jettisoned ship ids. Does NOT commit
        (rides the Carrier-destruction transaction).

        ``ship_service`` is the live ShipService instance from the caller so the
        escape-pod ejection reuses the canonical _ensure_escape_pod path (single
        pod per player). We do NOT call ship_service.destroy_ship on the docked
        ships — they survive intact."""
        hangar = carrier.hangar
        if not hangar or not hangar.get("docked"):
            return []

        jettisoned: List[uuid.UUID] = []
        for entry in list(hangar["docked"]):
            if entry.get("request_state") != REQUEST_DOCKED:
                continue
            ship = self.db.query(Ship).filter(Ship.id == uuid.UUID(entry["ship_id"])).first()
            if ship is None or ship.is_destroyed:
                continue

            # Jettison the ship INTACT into the destruction sector. It is NOT
            # destroyed (no is_destroyed flip, no wreck, no insurance payout,
            # cargo preserved). Leave it IN_SPACE / Drifting at the sector.
            ship.sector_id = destruction_sector_id
            ship.status = ShipStatus.IN_SPACE

            # Auto-eject the pilot to an Escape Pod in the destruction sector.
            # Only the owner who is actually PILOTING this hangared hull ejects;
            # a passenger whose flagship is this hull is the normal case.
            if ship.owner_id is not None:
                pilot = self.db.query(Player).filter(Player.id == ship.owner_id).first()
                if pilot is not None and pilot.current_ship_id == ship.id:
                    escape_pod = ship_service._ensure_escape_pod(pilot, destruction_sector_id)
                    pilot.current_ship_id = escape_pod.id
                    from src.services.ship_service import sync_current_pilot
                    sync_current_pilot(pilot, escape_pod, old_ship=ship)  # QUEUE-REGISTRY-PILOT-WIRING
                    pilot.current_sector_id = destruction_sector_id
                    pilot.is_docked = False
                    pilot.is_landed = False
                    logger.info(
                        "Jettison: pilot %s ejected to Escape Pod; ship %s survives intact",
                        pilot.id, ship.id,
                    )
            jettisoned.append(ship.id)

        # Empty the Carrier's hangar — every passenger has been jettisoned.
        carrier.hangar["docked"] = []
        flag_modified(carrier, "hangar")
        logger.info(
            "Carrier %s destroyed — jettisoned %d ship(s) intact into sector %s",
            carrier.id, len(jettisoned), destruction_sector_id,
        )
        return jettisoned

    # ------------------------------------------------------------------ #
    # internals
    # ------------------------------------------------------------------ #
    def _remove_entry(self, carrier: Ship, hangar: Dict[str, Any], ship_id: uuid.UUID) -> None:
        sid = str(ship_id)
        hangar["docked"] = [e for e in (hangar.get("docked") or []) if e.get("ship_id") != sid]
        flag_modified(carrier, "hangar")

    @staticmethod
    def _carrier_region(carrier: Ship):
        """The region a Carrier currently sits in, via its owning pilot's
        current_region_id (Ship has no region column; the pilot tracks it). Best
        effort — None when the Carrier is NPC-owned or the owner is absent."""
        owner = carrier.owner
        return owner.current_region_id if owner is not None else None

    @staticmethod
    def _schedule_region_hop(pilot: Optional[Player], new_region_id) -> None:
        """Best-effort WS region-room hop for a hangar-driven pilot relocation
        (WO-RT-ROOM-HOP). Mirrors movement_service._broadcast_sector_presence /
        _dispatch_hostile_detected: import inside the function, grab the
        running loop, schedule connection_manager.update_user_region with
        loop.create_task (never blocks the sync hangar transaction — these
        callers commit AFTER returning, not here), and swallow any failure (no
        loop, no socket) so a quiet socket can never break a dock / undock /
        disembark / ride-along. No-op when ``pilot`` or its ``user_id`` is
        unavailable.

        NO-CANON (flagged in report): only the region room is corrected here.
        Whether these carrier-implied relocations should ALSO hop the WS
        SECTOR room — which would additionally emit player_entered_sector /
        player_left_sector frames to the destination sector's other
        occupants — is left open. A hangared passenger is currently modeled
        as an inert rider on the Carrier's own move (ships.md:338-340), not an
        independent arrival, so this hop stays silent on the sector axis."""
        if pilot is None or pilot.user_id is None:
            return
        try:
            import asyncio
            from src.services.websocket_service import connection_manager

            loop = asyncio.get_running_loop()
            loop.create_task(connection_manager.update_user_region(
                str(pilot.user_id),
                str(new_region_id) if new_region_id is not None else None,
            ))
        except Exception:
            logger.debug(
                "Skipped hangar region WS room-hop (no loop or socket)",
                exc_info=True,
            )
