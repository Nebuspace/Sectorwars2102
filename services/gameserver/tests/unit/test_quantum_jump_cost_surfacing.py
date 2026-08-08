"""WO-API-PHASE2 Lane B5 -- surface quantum turn-costs + fix can_jump BUG-1.

BUG-1: get_status()'s can_jump previously gated on the flat JUMP_TURN_COST
(50), ignoring the +5 flat QJ tow surcharge (WO-AF) jump() itself charges
when the piloted Warp Jumper is towing. A 50-54-turn TOWING pilot read
can_jump=true but jump() rejected ("Need 55, have N") -- the server's own
READ and ACTION disagreed. Fixed by extracting the exact inline cost
computation jump() already did into _compute_jump_cost(db, ship) -> (base,
surcharge, total) and routing BOTH jump()'s turn-check and get_status()'s
can_jump through it.

DB-free: real Ship/Player ORM instances (never added to a session, never
flushed -- see test_message_beacon_deploy.py / test_ship_registry.py for the
same house pattern), a minimal entity-dispatch FakeSession/FakeQuery (this
service's query shapes are few and known, so a full WHERE-interpreter is
unneeded machinery). Sector "points" are a single-point galaxy (just the
origin) -- jump() then finds no candidates in EITHER the resolve loop or the
misfire-line search, so it takes the degenerate misfire-collapses-in-place
branch (destination stays the origin sector). That branch skips the
sector-change block entirely (docking_service.release / MovementService /
tow ride-along), which is exactly the part this WO does not touch --
everything BEFORE it (the tow-surcharge cost computation and the turn
debit) is the part under test, and it still runs unconditionally.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any, List, Optional

import pytest

from src.models.player import Player
from src.models.sector import Sector, SectorType
from src.models.ship import Ship, ShipStatus, ShipType
from src.services import quantum_service
from src.services.quantum_service import (
    JUMP_TURN_COST,
    QuantumError,
    _compute_jump_cost,
    get_status,
    jump,
)
from src.services.tow_service import QJ_TOW_SURCHARGE_FLAT

# --- minimal entity-dispatch fake session (this module's query shapes are
# few and known in advance -- Player / Sector-columns / Ship / func.max) --- #

class _FakeQuery:
    def __init__(self, first: Any = None, all_rows: Optional[List[Any]] = None, scalar: Any = None):
        self._first = first
        self._all = all_rows if all_rows is not None else []
        self._scalar = scalar

    def filter(self, *a, **kw) -> "_FakeQuery":
        return self

    def populate_existing(self) -> "_FakeQuery":
        return self

    def with_for_update(self) -> "_FakeQuery":
        return self

    def first(self) -> Any:
        return self._first

    def all(self) -> List[Any]:
        return self._all

    def scalar(self) -> Any:
        return self._scalar


class _FakeSession:
    def __init__(self, *, player=None, sector_points=None, tow_haulers=None):
        self.player = player
        self.sector_points = sector_points or []
        self.tow_haulers = tow_haulers or []
        self.commits = 0

    def query(self, *entities: Any) -> _FakeQuery:
        head = entities[0]
        if head is Player:
            return _FakeQuery(first=self.player)
        if head is Ship:
            # TowService.find_hauler_towing's hauler scan -- no OTHER ship
            # is towing this one in any of these scenarios.
            return _FakeQuery(all_rows=self.tow_haulers)
        if getattr(head, "name", None) == "max":
            # fleet-wide jump-cooldown probe (jump() and get_status() both
            # run it) -- no active cooldown in any of these scenarios.
            return _FakeQuery(scalar=None)
        if getattr(head, "class_", None) is Sector:
            return _FakeQuery(all_rows=self.sector_points)
        raise AssertionError(f"unexpected query for {entities!r}")

    def refresh(self, obj: Any) -> None:
        pass

    def commit(self) -> None:
        self.commits += 1


def _make_env(*, turns: int, towing: bool = False, towed_size: str = "small"):
    """A single-sector galaxy: one Warp Jumper, one pilot, one charted
    sector (the origin) -- jump() degenerates to a same-sector misfire
    (see module docstring), so no DB is needed past the cost computation."""
    tow_state = None
    if towing:
        tow_state = {
            "towed_ship_id": str(uuid.uuid4()),
            "towed_size": towed_size,
            "request_state": "LOCKED",
        }
    ship = Ship(
        id=uuid.uuid4(),
        type=ShipType.WARP_JUMPER,
        is_destroyed=False,
        status=ShipStatus.IN_SPACE,
        sector_id=1,
        quantum_charges=1,
        quantum_jump_cooldown_until=None,
        quantum_scan_cooldown_until=None,
        tow_state=tow_state,
        upgrades={},
        combat={"hull": 100, "max_hull": 100},
    )
    player = Player(
        id=uuid.uuid4(),
        turns=turns,
        lifetime_turns_spent=0,
        is_docked=False,
        is_landed=False,
        current_sector_id=1,
        current_region_id=None,  # short-circuits _resolve_nexus_warp_marker
        current_port_id=None,
        current_planet_id=None,
    )
    player.current_ship = ship
    origin = SimpleNamespace(
        id=1, sector_id=1, name="Origin", x_coord=0.0, y_coord=0.0, z_coord=0.0,
        type=SectorType.STANDARD, region_id=None,
    )
    db = _FakeSession(player=player, sector_points=[origin])
    return db, player, ship


# --- _compute_jump_cost itself (the extracted helper both call sites share) #

@pytest.mark.unit
class TestComputeJumpCost:
    def test_no_tow_is_base_only(self) -> None:
        _db, _player, ship = _make_env(turns=100, towing=False)
        base, surcharge, total = _compute_jump_cost(_db, ship)
        assert (base, surcharge, total) == (JUMP_TURN_COST, 0, JUMP_TURN_COST)

    def test_valid_tow_adds_flat_surcharge(self) -> None:
        _db, _player, ship = _make_env(turns=100, towing=True, towed_size="small")
        base, surcharge, total = _compute_jump_cost(_db, ship)
        assert (base, surcharge, total) == (
            JUMP_TURN_COST, QJ_TOW_SURCHARGE_FLAT, JUMP_TURN_COST + QJ_TOW_SURCHARGE_FLAT,
        )

    def test_oversized_tow_raises_before_any_cost_is_returned(self) -> None:
        _db, _player, ship = _make_env(turns=100, towing=True, towed_size="capital")
        with pytest.raises(QuantumError):
            _compute_jump_cost(_db, ship)


# --- P1: jump()'s charged turns are byte-identical before/after the extract #

@pytest.mark.unit
class TestP1ChargedTurnsUnchanged:
    def test_no_tow_charges_exactly_base(self) -> None:
        db, player, _ship = _make_env(turns=100, towing=False)
        jump(db, player.id, yaw_deg=0.0, pitch_deg=0.0, range_band="near")
        assert player.turns == 100 - JUMP_TURN_COST
        assert 100 - player.turns == JUMP_TURN_COST == 50

    def test_towing_charges_base_plus_surcharge(self) -> None:
        db, player, _ship = _make_env(turns=100, towing=True, towed_size="small")
        jump(db, player.id, yaw_deg=0.0, pitch_deg=0.0, range_band="near")
        expected_total = JUMP_TURN_COST + QJ_TOW_SURCHARGE_FLAT
        assert player.turns == 100 - expected_total
        assert 100 - player.turns == expected_total == 55

    def test_insufficient_turns_rejects_and_charges_nothing(self) -> None:
        db, player, ship = _make_env(turns=JUMP_TURN_COST - 1, towing=False)
        with pytest.raises(QuantumError, match=r"Need 50, have 49"):
            jump(db, player.id, yaw_deg=0.0, pitch_deg=0.0, range_band="near")
        # Rejection unchanged: turns AND the charge are both untouched --
        # the check runs before ship.quantum_charges -= 1 / spend_turns().
        assert player.turns == JUMP_TURN_COST - 1
        assert ship.quantum_charges == 1
        assert db.commits == 0


# --- P2: can_jump now AGREES with jump() for the tow case (BUG-1) -------- #

@pytest.mark.unit
class TestP2TowBugFixed:
    def test_towing_at_exactly_base_cost_both_reject(self) -> None:
        db, player, _ship = _make_env(turns=JUMP_TURN_COST, towing=True, towed_size="small")
        status = get_status(db, player)
        assert status["can_jump"] is False
        assert status["jump_turn_cost"] == JUMP_TURN_COST
        assert status["jump_tow_surcharge"] == QJ_TOW_SURCHARGE_FLAT

        with pytest.raises(QuantumError, match=r"Need 55, have 50"):
            jump(db, player.id, yaw_deg=0.0, pitch_deg=0.0, range_band="near")

    def test_towing_at_base_plus_surcharge_both_allow(self) -> None:
        total = JUMP_TURN_COST + QJ_TOW_SURCHARGE_FLAT
        db, player, _ship = _make_env(turns=total, towing=True, towed_size="small")
        status = get_status(db, player)
        assert status["can_jump"] is True

        jump(db, player.id, yaw_deg=0.0, pitch_deg=0.0, range_band="near")
        assert player.turns == 0


# --- P3: non-tow can_jump is UNCHANGED (no new false-negatives) --------- #

@pytest.mark.unit
class TestP3NonTowUnchanged:
    def test_non_towing_at_exactly_base_cost_both_allow(self) -> None:
        db, player, _ship = _make_env(turns=JUMP_TURN_COST, towing=False)
        status = get_status(db, player)
        assert status["can_jump"] is True
        assert status["jump_turn_cost"] == JUMP_TURN_COST
        assert status["jump_tow_surcharge"] == 0
        assert status["scan_turn_cost"] == quantum_service.SCAN_TURN_COST

        jump(db, player.id, yaw_deg=0.0, pitch_deg=0.0, range_band="near")
        assert player.turns == 0
