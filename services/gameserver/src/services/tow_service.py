"""
Tractor Beam tow service (WO-AF).

Single source of truth for the Tractor Beam ship-tow operation — the consent
lock between a HAULER (a ship with a tractor_beam installed) and a TOWED ship it
drags through space. Separate from the Carrier ship-hangar (WO-AE, hangar_service)
and from regular cargo. Implements the canon spec at:

  - FEATURES/gameplay/ships.md "Tractor Beam tow operations" (lines 348-371)
  - DATA_MODELS/ships.md "Ship tow state" (the Ship.tow_state JSONB shape)
  - ADR-0067 (group-e-tractor-tow-quantum-jump) — detach-priority + QJ-vs-combat

Mechanics owned here:

  LOCK-ON (consent)  — a hauler with a tractor_beam requests a tow on a target
                       ship in the same sector; the target pilot ACCEPTS. 60s
                       request expiry. Rejects: no tractor, different sector,
                       either ship not IN_SPACE (IN_COMBAT/HARMONIZING/DOCKED),
                       capital-size target (not towable — branch BEFORE
                       size_units_for), NULL-size target, no-nesting (hauler
                       being towed), hangar-exclusion (either ship in a Carrier
                       hangar). On accept: set hauler.tow_state with the cached
                       surcharge from the canon tow-surcharge table.
  SURCHARGE          — surcharge_per_move cached at lock-on from towed_size
                       (tiny+1 / small+2 / medium+3 / large+5 — the canon
                       TOW_SURCHARGE table, DISTINCT from SIZE_UNITS).
  TOW-ALONG          — when the hauler moves, the towed ship's sector follows;
                       the towed pilot pays 0 turns (movement_service hook).
  DETACH             — the hauler OR the towed pilot breaks the tow at any sector
                       for 0 turns, INCLUDING from IN_COMBAT (detach priority
                       over combat lock — ADR-0067 S-F3). Attackers who are
                       neither party cannot break the tow. Clears tow_state.
  DETACH-ON-DESTRUCTION
                     — hauler destroyed -> tow auto-detaches; towed ship stays
                       intact (pilot aboard, no eject, no wreck). Towed ship
                       destroyed -> tow auto-detaches; hauler continues. (Hooked
                       from ship_service.destroy_ship, covering combat / genesis /
                       warp-gate-anchor uniformly.)

Size axis (WO-AD): a ship's size lives on ShipSpecification.ship_size. CONTRACT
(from WO-AD): treat a NULL ship_size as INELIGIBLE, and branch on CAPITAL
(not-towable) BEFORE calling size_units_for() / tow_surcharge_for() (both RAISE
for CAPITAL). The QJ eligibility cap (size_units <= 4) uses SIZE_UNITS; the
per-move surcharge uses the DISTINCT TOW_SURCHARGE table.

All turn charges (move cost + surcharge, QJ +5 flat, gate +2 flat) are applied by
the movement / quantum services that own those move paths — lock-on / detach
themselves cost 0 turns, so this service stays a pure tow-state manager that
also relocates the towed ship/pilot on a hauler move.
"""

import logging
import uuid
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, Optional, Tuple

from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from src.models.player import Player
from src.models.ship import (
    Ship,
    ShipSize,
    ShipSpecification,
    ShipStatus,
    size_units_for,
    tow_surcharge_for,
)
from src.services.ship_upgrade_service import ShipUpgradeService

logger = logging.getLogger(__name__)

# Tow request consent expiry (FEATURES/gameplay/ships.md:367 — "tow requests
# sent to a target pilot expire 60 seconds after issuance if not accepted").
TOW_REQUEST_EXPIRY_SECONDS = 60

# QJ towed-size cap (ships.md:358 — size_units <= 4: tiny/small/medium eligible,
# large/capital excluded). Uses the SIZE_UNITS axis, NOT the surcharge table.
QJ_MAX_TOWED_SIZE_UNITS = 4

# Flat turn surcharges that do NOT scale with size (ships.md:357-358):
#   player warp gate transit: +2 flat; quantum jump commit: +5 flat.
GATE_TOW_SURCHARGE_FLAT = 2
QJ_TOW_SURCHARGE_FLAT = 5

# Lock-request lifecycle states held in the hauler's pending-request scratch.
# (The committed lock lives in Ship.tow_state; a PENDING request is tracked on
# the hauler's tow_state with request_state PENDING until the target accepts.)
REQUEST_PENDING = "PENDING"
REQUEST_LOCKED = "LOCKED"


class TowError(Exception):
    """Raised for a rejected tow operation. ``message`` is player-safe."""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class TowService:
    def __init__(self, db: Session):
        self.db = db

    # ------------------------------------------------------------------ #
    # Equipment / size helpers
    # ------------------------------------------------------------------ #
    @staticmethod
    def has_tractor_beam(ship: Ship) -> bool:
        """True iff ``ship`` carries a tractor_beam in tow_capable mode (WO-BC
        equipment effects {tow_capable: true}). Defensive: a missing
        equipment_slots JSONB simply yields False, never a crash."""
        try:
            effects = ShipUpgradeService.get_equipment_effects(ship)
            return bool(effects.get("tow_capable"))
        except Exception as e:
            logger.error("Tractor equipment read failed (treating as no tractor): %s", e)
            return False

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

    def _eligible_towed_size(self, ship: Ship) -> ShipSize:
        """Return the towed ship's canonical size, or raise TowError if it is
        ineligible to be towed. Order matters (WO-AD CONTRACT):
          1. NULL ship_size -> INELIGIBLE (NPC-only / unspecced hulls).
          2. CAPITAL -> not-towable (branch BEFORE size_units_for /
             tow_surcharge_for, which RAISE for CAPITAL).
          3. otherwise -> a finite, towable size.
        """
        size = self._ship_size(ship)
        if size is None:
            raise TowError(
                "That ship cannot be towed (no canonical size — NPC or "
                "unspecified hull)."
            )
        if size == ShipSize.CAPITAL:
            raise TowError(
                "Capital-size ships cannot be tractor-towed — their mass exceeds "
                "the Tractor Beam's structural rating."
            )
        return size

    # ------------------------------------------------------------------ #
    # State lookups
    # ------------------------------------------------------------------ #
    def is_being_towed(self, ship_id: uuid.UUID) -> bool:
        """True iff ``ship_id`` is currently the TOWED ship of some hauler's
        active (LOCKED) tow_state. Used by movement / quantum independent-move
        guards. A PENDING request does NOT make a ship 'being towed'."""
        return self.find_hauler_towing(ship_id) is not None

    def find_hauler_towing(self, ship_id: uuid.UUID) -> Optional[Ship]:
        """Return the hauler whose LOCKED tow_state tows ``ship_id``, or None.
        Scans only ships carrying a non-NULL tow_state (cheap: very few active
        tows). A PENDING request is excluded — the target is still free."""
        sid = str(ship_id)
        haulers = (
            self.db.query(Ship)
            .filter(Ship.tow_state.isnot(None), Ship.is_destroyed.is_(False))
            .all()
        )
        for hauler in haulers:
            ts = hauler.tow_state or {}
            if (
                ts.get("request_state") == REQUEST_LOCKED
                and ts.get("towed_ship_id") == sid
            ):
                return hauler
        return self._scan_legacy_locked(sid, haulers)

    @staticmethod
    def _scan_legacy_locked(sid: str, haulers) -> Optional[Ship]:
        """Defensive: treat a tow_state with a towed_ship_id but NO request_state
        as LOCKED (a committed tow), so a legacy/migrated row is still honored."""
        for hauler in haulers:
            ts = hauler.tow_state or {}
            if "request_state" not in ts and ts.get("towed_ship_id") == sid:
                return hauler
        return None

    @staticmethod
    def is_actively_towing(ship: Ship) -> bool:
        """True iff ``ship`` currently holds an active (LOCKED) tow on another
        ship. A PENDING-only request does not count as actively towing."""
        ts = getattr(ship, "tow_state", None) or {}
        if not ts.get("towed_ship_id"):
            return False
        # No request_state (legacy) or explicit LOCKED both mean a live tow.
        return ts.get("request_state", REQUEST_LOCKED) == REQUEST_LOCKED

    def _is_hangared(self, ship_id: uuid.UUID) -> bool:
        """True iff ``ship_id`` is a docked passenger in any Carrier hangar
        (WO-AE). Imported lazily to avoid an import cycle."""
        try:
            from src.services.hangar_service import HangarService
            return HangarService(self.db).is_ship_hangared(ship_id)
        except Exception as e:
            logger.error("Hangar-exclusion check failed (treating as not hangared): %s", e)
            return False

    @staticmethod
    def _expiry_passed(ts: Dict[str, Any]) -> bool:
        """True iff a PENDING request's 60s window has elapsed."""
        requested_at = ts.get("requested_at")
        if not requested_at:
            return False
        try:
            issued = datetime.fromisoformat(requested_at)
        except (ValueError, TypeError):
            return False
        if issued.tzinfo is None:
            issued = issued.replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) - issued > timedelta(
            seconds=TOW_REQUEST_EXPIRY_SECONDS
        )

    # ------------------------------------------------------------------ #
    # LOCK-ON — request + accept (consent flow)
    # ------------------------------------------------------------------ #
    def request_tow(self, hauler: Ship, target: Ship) -> Dict[str, Any]:
        """Stage 1 of the consent flow: the hauler pilot requests a tow on
        ``target``. Validates tractor-equipped, same-sector, both IN_SPACE,
        eligibility (size / capital / NULL), no-nesting, hangar-exclusion, and
        that neither ship is already in a tow. Stages a PENDING tow_state on the
        hauler. Charges NO turns. Does NOT commit (caller owns the txn)."""
        if hauler.id == target.id:
            raise TowError("A ship cannot tow itself.")
        if hauler.is_destroyed or target.is_destroyed:
            raise TowError("Destroyed ships cannot be involved in a tow.")

        # Hauler must carry a tractor_beam (tow_capable).
        if not self.has_tractor_beam(hauler):
            raise TowError("Your ship has no Tractor Beam installed.")

        # Same sector (ships.md:353).
        if hauler.sector_id != target.sector_id:
            raise TowError("The target must be in your sector to lock a tow.")

        # Lock-on requires BOTH ships IN_SPACE (not IN_COMBAT) — ships.md:359.
        # HARMONIZING / DOCKED ships are likewise un-lockable (frozen / port).
        if hauler.status != ShipStatus.IN_SPACE:
            raise TowError("Your ship must be in open space to tow (not docked, in combat, or harmonizing).")
        if target.status != ShipStatus.IN_SPACE:
            raise TowError("The target must be in open space (not docked, in combat, or harmonizing) to be towed.")

        # CAPITAL / NULL-size rejection — branch BEFORE size_units_for /
        # tow_surcharge_for (WO-AD contract). Resolves the towable size.
        size = self._eligible_towed_size(target)

        # No-nesting: a hauler that is itself being towed cannot tow a third ship
        # (ships.md:369). Also reject if the hauler already actively tows someone.
        if self.is_being_towed(hauler.id):
            raise TowError("Your ship is itself being towed — it cannot tow another ship (no nesting).")
        if self.is_actively_towing(hauler):
            raise TowError("Your ship is already towing a ship — detach first.")
        if getattr(hauler, "tow_state", None) and hauler.tow_state.get("request_state") == REQUEST_PENDING:
            raise TowError("Your ship already has a pending tow request — cancel or wait for it to expire.")

        # The target cannot already be towed by anyone, nor be a hauler itself.
        if self.is_being_towed(target.id):
            raise TowError("That ship is already being towed.")
        if self.is_actively_towing(target):
            raise TowError("That ship is towing another ship — it cannot also be towed (no nesting).")

        # Hangar exclusion (ships.md:370): a ship docked inside a Carrier hangar
        # cannot be the source OR target of a tow lock-on.
        if self._is_hangared(hauler.id):
            raise TowError("Your ship is docked inside a Carrier — it cannot tow. Undock first.")
        if self._is_hangared(target.id):
            raise TowError("That ship is docked inside a Carrier — it cannot be towed. It must undock first.")

        surcharge = tow_surcharge_for(size)
        hauler.tow_state = {
            "towed_ship_id": str(target.id),
            "towed_owner_id": str(target.owner_id) if target.owner_id else None,
            "towed_size": size.value.lower(),  # canon lowercase: tiny/small/medium/large
            "surcharge_per_move": surcharge,
            "locked_at": None,
            "lock_sector_id": hauler.sector_id,
            "request_state": REQUEST_PENDING,
            "requested_at": datetime.now(timezone.utc).isoformat(),
        }
        flag_modified(hauler, "tow_state")
        logger.info(
            "Tow REQUEST: hauler %s -> target %s (size=%s, surcharge=%d, pending)",
            hauler.id, target.id, size.value, surcharge,
        )
        return {
            "status": REQUEST_PENDING,
            "hauler_id": str(hauler.id),
            "towed_ship_id": str(target.id),
            "towed_size": size.value.lower(),
            "surcharge_per_move": surcharge,
        }

    def accept_tow(self, hauler: Ship, target: Ship) -> Dict[str, Any]:
        """Stage 2: the TARGET pilot ACCEPTS the hauler's pending tow request.
        Re-validates the live invariants (same-sector, both IN_SPACE, not
        expired, not hangared, neither already towed), flips the tow_state to
        LOCKED. Charges NO turns. Does NOT commit."""
        ts = getattr(hauler, "tow_state", None)
        if not ts or ts.get("request_state") != REQUEST_PENDING:
            raise TowError("No pending tow request from that ship.")
        if ts.get("towed_ship_id") != str(target.id):
            raise TowError("That tow request is for a different ship.")

        # 60s consent expiry (ships.md:367): a stale request cannot be accepted.
        if self._expiry_passed(ts):
            hauler.tow_state = None
            flag_modified(hauler, "tow_state")
            raise TowError("The tow request has expired (60s). Ask the hauler to re-issue it.")

        if hauler.is_destroyed or target.is_destroyed:
            raise TowError("Destroyed ships cannot be involved in a tow.")

        # Re-validate the live invariants at accept time (state may have changed
        # since the request: a fight started, a ship moved away, got hangared).
        if hauler.sector_id != target.sector_id:
            hauler.tow_state = None
            flag_modified(hauler, "tow_state")
            raise TowError("The ships are no longer in the same sector — the tow lock-on failed.")
        if hauler.status != ShipStatus.IN_SPACE or target.status != ShipStatus.IN_SPACE:
            hauler.tow_state = None
            flag_modified(hauler, "tow_state")
            raise TowError("Both ships must be in open space to complete the tow lock-on.")
        if not self.has_tractor_beam(hauler):
            hauler.tow_state = None
            flag_modified(hauler, "tow_state")
            raise TowError("The hauler no longer has a Tractor Beam installed.")
        if self._is_hangared(hauler.id) or self._is_hangared(target.id):
            hauler.tow_state = None
            flag_modified(hauler, "tow_state")
            raise TowError("A ship docked inside a Carrier cannot tow or be towed.")
        # Another tow may have committed against the target since the request.
        existing_hauler = self.find_hauler_towing(target.id)
        if existing_hauler is not None and existing_hauler.id != hauler.id:
            hauler.tow_state = None
            flag_modified(hauler, "tow_state")
            raise TowError("That ship is already being towed by another hauler.")

        ts["request_state"] = REQUEST_LOCKED
        ts["locked_at"] = datetime.now(timezone.utc).isoformat()
        ts["lock_sector_id"] = hauler.sector_id
        flag_modified(hauler, "tow_state")
        logger.info(
            "Tow ACCEPT: hauler %s now towing %s (surcharge=%s)",
            hauler.id, target.id, ts.get("surcharge_per_move"),
        )
        return {
            "status": REQUEST_LOCKED,
            "hauler_id": str(hauler.id),
            "towed_ship_id": str(target.id),
            "surcharge_per_move": ts.get("surcharge_per_move"),
            "lock_sector_id": ts.get("lock_sector_id"),
        }

    def cancel_request(self, hauler: Ship) -> Dict[str, Any]:
        """Either party drops a still-PENDING tow request (0 turns). A LOCKED tow
        is broken via detach(), not here."""
        ts = getattr(hauler, "tow_state", None)
        if not ts or ts.get("request_state") != REQUEST_PENDING:
            raise TowError("No pending tow request to cancel.")
        hauler.tow_state = None
        flag_modified(hauler, "tow_state")
        return {"status": "CANCELLED", "hauler_id": str(hauler.id)}

    # ------------------------------------------------------------------ #
    # DETACH (0 turns, unrestricted — including from IN_COMBAT)
    # ------------------------------------------------------------------ #
    def detach(self, hauler: Ship) -> Dict[str, Any]:
        """Break an active (LOCKED) tow for 0 turns. Detach is UNRESTRICTED —
        it works from any state INCLUDING IN_COMBAT (detach priority over combat
        lock — ADR-0067 S-F3). Clears the hauler's tow_state. The towed ship's
        row is left untouched (intact, wherever it currently sits). Does NOT
        commit. Caller (route) authorizes that the requester is the hauler pilot
        OR the towed pilot — attackers who are neither cannot break the tow."""
        ts = getattr(hauler, "tow_state", None)
        if not ts or not ts.get("towed_ship_id"):
            raise TowError("That ship is not towing anything.")
        towed_id = ts.get("towed_ship_id")
        hauler.tow_state = None
        flag_modified(hauler, "tow_state")
        logger.info("Tow DETACH: hauler %s released %s (0 turns)", hauler.id, towed_id)
        return {"status": "DETACHED", "hauler_id": str(hauler.id), "towed_ship_id": towed_id}

    # ------------------------------------------------------------------ #
    # TOW-ALONG (movement_service / quantum_service hook)
    # ------------------------------------------------------------------ #
    def carry_towed_ship(self, hauler: Ship, destination_sector_id: int) -> bool:
        """Relocate the LOCKED towed ship (and, if it's the towed pilot's active
        hull, that pilot) to ``destination_sector_id`` when the hauler moves /
        jumps. The towed pilot pays 0 turns for the hauler's movement
        (ships.md:354). Returns True if a ship was carried. Does NOT commit
        (rides the hauler-move transaction). No-op when no active tow."""
        ts = getattr(hauler, "tow_state", None)
        if not ts or ts.get("request_state", REQUEST_LOCKED) != REQUEST_LOCKED:
            return False
        towed_id = ts.get("towed_ship_id")
        if not towed_id:
            return False
        towed = self.db.query(Ship).filter(Ship.id == uuid.UUID(towed_id)).first()
        if towed is None or towed.is_destroyed:
            return False
        towed.sector_id = destination_sector_id
        # The towed pilot's location follows the hauler (0 turns) only when this
        # towed hull is the pilot's ACTIVE ship.
        region_id = self._hauler_region(hauler)
        if towed.owner_id is not None:
            pilot = self.db.query(Player).filter(Player.id == towed.owner_id).first()
            if pilot is not None and pilot.current_ship_id == towed.id:
                pilot.current_sector_id = destination_sector_id
                pilot.current_region_id = region_id
                pilot.is_docked = False
                pilot.is_landed = False
                pilot.current_port_id = None
                pilot.current_planet_id = None
                # WO-DOCK-500 Leg 1: a tow ride-along out of a port implicitly
                # undocks the pilot; release the slip or it orphans + 500s redock.
                from src.services.docking_service import release as _release_docking_slip
                _release_docking_slip(self.db, None, pilot)
        logger.info(
            "Tow ride-along: hauler %s carried towed %s to sector %s",
            hauler.id, towed.id, destination_sector_id,
        )
        return True

    # ------------------------------------------------------------------ #
    # DETACH-ON-DESTRUCTION (ship_service.destroy_ship hook)
    # ------------------------------------------------------------------ #
    def detach_on_destruction(self, dying_ship: Ship) -> None:
        """Called from ship_service.destroy_ship BEFORE the standard destruction
        flow runs on ``dying_ship``. Handles both roles uniformly (covers combat,
        genesis, and warp-gate-anchor destruction):

          - If the dying ship is a HAULER (its own tow_state is LOCKED): the tow
            auto-detaches; the towed ship is left INTACT where it sits (the towed
            pilot stays aboard — destroy_ship is operating on the HAULER, never
            the towed hull, so the towed pilot is naturally untouched: no eject,
            no wreck, no insurance, cargo preserved — ships.md:362). For the WJ
            Phase-3 anchor-sacrifice (cause warp_gate_anchor), the towed ship has
            already ridden along to the destination and now survives intact there
            (ships.md:364).

          - If the dying ship is the TOWED ship of some hauler: clear that
            hauler's tow_state so the hauler continues at base cost; the dying
            (towed) ship runs the STANDARD destruction flow / wreck (ships.md:363).

        Best-effort: a tow-cleanup hiccup must never block the kill (it would
        strand the destruction). Does NOT commit (rides the destruction txn)."""
        try:
            # Role A — dying ship is the HAULER.
            if self.is_actively_towing(dying_ship):
                ts = dying_ship.tow_state or {}
                towed_id = ts.get("towed_ship_id")
                dying_ship.tow_state = None
                flag_modified(dying_ship, "tow_state")
                logger.info(
                    "Hauler %s destroyed mid-tow — auto-detached; towed %s survives intact",
                    dying_ship.id, towed_id,
                )
        except Exception as e:
            logger.error("Tow detach-on-destruction (hauler role) failed for %s: %s", dying_ship.id, e)

        try:
            # Role B — dying ship is the TOWED ship of some hauler.
            hauler = self.find_hauler_towing(dying_ship.id)
            if hauler is not None:
                hauler.tow_state = None
                flag_modified(hauler, "tow_state")
                logger.info(
                    "Towed ship %s destroyed — auto-detached; hauler %s continues at base cost",
                    dying_ship.id, hauler.id,
                )
        except Exception as e:
            logger.error("Tow detach-on-destruction (towed role) failed for %s: %s", dying_ship.id, e)

    # ------------------------------------------------------------------ #
    # internals
    # ------------------------------------------------------------------ #
    @staticmethod
    def _hauler_region(hauler: Ship):
        """The region a hauler currently sits in, via its owning pilot's
        current_region_id (Ship has no region column). Best effort — None when
        the hauler is NPC-owned or the owner is absent."""
        owner = hauler.owner
        return owner.current_region_id if owner is not None else None
