"""WO-FIX-QUANTUM-JUMP-COOLDOWN-OWNER-VS-PILOT.

FEATURES/galaxy/sectors.md: "a player may commit at most one Quantum Jump
per 24h regardless of which Warp Jumper they pilot" -- the per-player
anti-stacking gate from ADR-0049's SK11 close-out. quantum_service.jump()'s
own comment already said "the canon cooldown is per-pilot, not per-hull",
but the fleet-wide query filtered on ``Ship.owner_id == player.id`` -- which
reopens the exact SK11 team-rotation-stacking exploit whenever the pilot
does not own the hull (a crew/lend arrangement: ``Ship.current_pilot_id !=
Ship.owner_id``, see ``ship_service.select_ship``'s invariant that
``current_pilot_id == player.id`` iff ``Player.current_ship_id ==
ship.id``). A player piloting a teammate's Warp Jumper owns nothing, so the
owner_id filter always returned NULL for them no matter how many jumps
they'd just made on borrowed hulls.

The fix is a UNION (owner_id OR current_pilot_id), not a swap of one column
for the other: current_pilot_id ALONE reopens a different hole, because
``ship_service.select_ship`` NULLs the old hull's current_pilot_id on a
swap, so a pilot-only filter matches exactly one ship and collapses into
the per-ship gate immediately above it. Each single-column variant fails
exactly one test here.

The union is deliberately fail-CLOSED where the two columns disagree: an
owner whose fleet-mate is flying their cooling hull is blocked, which is a
rare false-block rather than an exploit. A truly per-pilot cooldown needs a
Player-level column (the ship-stored deadline is only ever a proxy for it);
that is a schema change and is escalated, not smuggled in here.

DB-free: real Ship/Player/Sector ORM instances (never added to a session),
with a fake Session whose query() dispatches on entity identity and whose
fleet-cooldown fake evaluates the REAL SQLAlchemy filter criteria jump()
passes against a small fleet, so these tests fail on the pre-fix code and
pass on the fix -- they are not merely asserting a hardcoded outcome.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, UTC
from typing import Any, List, Optional

import pytest

from src.models.player import Player
from src.models.sector import Sector, SectorType
from src.models.ship import Ship, ShipStatus, ShipType
from src.services.quantum_service import QuantumError, jump


def _eval_eq(row: Any, cond: Any) -> bool:
    """Evaluate the filter shapes the fleet-cooldown query uses against a fake
    ship row: ``Column == value`` BinaryExpressions and the ``or_(...)``
    disjunction over owner_id/current_pilot_id. ``Column == False`` compiles
    to a bare SQL boolean literal (no ``.value``), not a BindParameter, so
    that's handled explicitly rather than falling through to None."""
    clauses = getattr(cond, "clauses", None)
    if clauses is not None:
        operator_name = getattr(getattr(cond, "operator", None), "__name__", "")
        assert operator_name == "or_", f"unhandled clause list operator {operator_name!r}"
        return any(_eval_eq(row, c) for c in clauses)
    key = cond.left.key
    right = cond.right
    right_str = str(right)
    if right_str == "false":
        value: Any = False
    elif right_str == "true":
        value = True
    else:
        value = right.value if hasattr(right, "value") else None
    return getattr(row, key) == value


class _FleetAggQuery:
    """Fakes ``db.query(func.max(Ship.quantum_jump_cooldown_until))
    .filter(...).scalar()`` -- applies jump()'s REAL filter criteria against
    ``fleet`` so the test proves which column the code actually filtered on."""

    def __init__(self, fleet: List[Ship]):
        self.fleet = fleet
        self.criteria: List[Any] = []

    def filter(self, *conds: Any) -> "_FleetAggQuery":
        self.criteria.extend(conds)
        return self

    def scalar(self) -> Any:
        matched = [s for s in self.fleet if all(_eval_eq(s, c) for c in self.criteria)]
        values = [s.quantum_jump_cooldown_until for s in matched if s.quantum_jump_cooldown_until]
        return max(values) if values else None


class _SingleQuery:
    def __init__(self, first: Any = None, all_rows: Optional[List[Any]] = None):
        self._first = first
        self._all = all_rows if all_rows is not None else []

    def filter(self, *a: Any, **kw: Any) -> "_SingleQuery":
        return self

    def populate_existing(self) -> "_SingleQuery":
        return self

    def with_for_update(self) -> "_SingleQuery":
        return self

    def first(self) -> Any:
        return self._first

    def all(self) -> List[Any]:
        return self._all


class _FakeSession:
    def __init__(self, *, player: Player, fleet: List[Ship], sector_points, destination_sector):
        self.player = player
        self.fleet = fleet
        self.sector_points = sector_points
        self.destination_sector = destination_sector
        self.commits = 0

    def query(self, *entities: Any) -> Any:
        head = entities[0]
        if head is Player:
            return _SingleQuery(first=self.player)
        if getattr(head, "name", None) == "max":
            return _FleetAggQuery(self.fleet)
        if head is Ship:
            # tow-hauler scan (TowService.is_being_towed via find_hauler_towing)
            return _SingleQuery(all_rows=[])
        if head is Sector:
            return _SingleQuery(first=self.destination_sector)
        if getattr(head, "class_", None) is Sector:
            return _SingleQuery(all_rows=self.sector_points)
        raise AssertionError(f"unexpected query for {entities!r}")

    def refresh(self, obj: Any) -> None:
        pass

    def flush(self) -> None:
        pass

    def commit(self) -> None:
        self.commits += 1


def _make_ship(*, owner_id, current_pilot_id, cooldown_until=None) -> Ship:
    return Ship(
        id=uuid.uuid4(),
        owner_id=owner_id,
        current_pilot_id=current_pilot_id,
        type=ShipType.WARP_JUMPER,
        is_destroyed=False,
        status=ShipStatus.IN_SPACE,
        sector_id=1,
        quantum_charges=1,
        quantum_jump_cooldown_until=cooldown_until,
        quantum_scan_cooldown_until=None,
        tow_state=None,
        upgrades={},
        combat={"hull": 100, "max_hull": 100},
    )


def _make_player(*, piloted_ship: Ship) -> Player:
    player = Player(
        id=uuid.uuid4(),
        turns=100,
        lifetime_turns_spent=0,
        is_docked=False,
        is_landed=False,
        current_sector_id=1,
        current_region_id=None,
        current_port_id=None,
        current_planet_id=None,
        settings={},
    )
    player.current_ship = piloted_ship
    return player


def _make_sectors():
    origin = Sector(
        id=uuid.uuid4(), sector_id=1, name="Origin",
        x_coord=0.0, y_coord=0.0, z_coord=0.0,
        type=SectorType.STANDARD, region_id=None,
    )
    destination = Sector(
        id=uuid.uuid4(), sector_id=2, name="Destination",
        x_coord=10.0, y_coord=0.0, z_coord=0.0,
        type=SectorType.STANDARD, region_id=None,
    )
    destination.is_discovered = True
    destination.discovered_by_id = None
    return origin, destination


def _stub_side_effects(monkeypatch):
    """Neutralize arrival hooks unrelated to the cooldown gate under test
    (mirrors test_quantum_jump_discovery_flag.py's stub set)."""
    import src.services.docking_service as docking_service
    import src.services.movement_service as movement_service
    import src.services.contraband_service as contraband_service
    import src.services.medal_service as medal_service
    from src.services.tow_service import TowService

    monkeypatch.setattr(docking_service, "release", lambda db, station, player: False)
    monkeypatch.setattr(
        movement_service.MovementService, "_update_player_presence",
        lambda self, player, old_sector_id, new_sector_id: None,
    )
    monkeypatch.setattr(
        contraband_service, "scan_in_transit_best_effort", lambda *a, **kw: None,
    )
    monkeypatch.setattr(
        medal_service, "check_and_award_exploration_medals", lambda *a, **kw: None,
    )
    monkeypatch.setattr(TowService, "is_being_towed", lambda self, ship_id: False)


@pytest.mark.unit
class TestQuantumJumpCooldownPilotNotOwner:
    def test_blocks_on_a_ship_the_player_piloted_but_does_not_own(self, monkeypatch):
        """SK11 reproduction: player B owns their own clean Warp Jumper (no
        cooldown) but is the CURRENT PILOT of a teammate-owned Warp Jumper
        that is still on cooldown from a jump B just made on it (crew/lend).
        The per-player gate must key on B's PILOTED history, not B's OWNED
        fleet -- so committing from B's own clean ship must still be
        blocked."""
        _stub_side_effects(monkeypatch)
        player_b = uuid.uuid4()
        player_a = uuid.uuid4()

        own_clean_ship = _make_ship(
            owner_id=player_b, current_pilot_id=player_b, cooldown_until=None,
        )
        borrowed_cooling_down_ship = _make_ship(
            owner_id=player_a, current_pilot_id=player_b,
            cooldown_until=datetime.now(UTC) + timedelta(hours=20),
        )
        fleet = [own_clean_ship, borrowed_cooling_down_ship]

        player = _make_player(piloted_ship=own_clean_ship)
        player.id = player_b
        origin, destination = _make_sectors()
        db = _FakeSession(
            player=player, fleet=fleet,
            sector_points=[origin, destination], destination_sector=destination,
        )

        with pytest.raises(QuantumError) as exc_info:
            jump(db, player.id, yaw_deg=0.0, pitch_deg=0.0, range_band="near")

        assert exc_info.value.error_code == "ERR_QJ_PLAYER_COOLDOWN_ACTIVE"
        # Never reached Phase 2 commit -- the borrowed ship's charge/cooldown
        # must be untouched and no commit issued.
        assert db.commits == 0

    def test_blocks_after_swapping_off_the_hull_that_is_cooling_down(self, monkeypatch):
        """Hull-swap regression guard. Player E jumped their Warp Jumper (now
        cooling) then swapped into a second owned Warp Jumper --
        ``ship_service.select_ship`` NULLs the old hull's ``current_pilot_id``
        on the swap, so the cooling ship is no longer matched by pilot at all.
        A current_pilot_id-ONLY filter therefore collapses into the per-ship
        gate and lets E jump again immediately; only the owner_id half of the
        disjunction still sees the cooling hull. This is the case that makes
        the fix a UNION rather than a swap of one column for the other."""
        _stub_side_effects(monkeypatch)
        player_e = uuid.uuid4()

        swapped_into_clean_ship = _make_ship(
            owner_id=player_e, current_pilot_id=player_e, cooldown_until=None,
        )
        swapped_out_cooling_ship = _make_ship(
            owner_id=player_e, current_pilot_id=None,
            cooldown_until=datetime.now(UTC) + timedelta(hours=20),
        )
        fleet = [swapped_into_clean_ship, swapped_out_cooling_ship]

        player = _make_player(piloted_ship=swapped_into_clean_ship)
        player.id = player_e
        origin, destination = _make_sectors()
        db = _FakeSession(
            player=player, fleet=fleet,
            sector_points=[origin, destination], destination_sector=destination,
        )

        with pytest.raises(QuantumError) as exc_info:
            jump(db, player.id, yaw_deg=0.0, pitch_deg=0.0, range_band="near")

        assert exc_info.value.error_code == "ERR_QJ_PLAYER_COOLDOWN_ACTIVE"
        assert db.commits == 0

    def test_unrelated_players_ships_never_gate_the_jump(self, monkeypatch):
        """Negative control: a cooling Warp Jumper that player F neither owns
        nor pilots must not gate F. Without this, a filter that accidentally
        matched everything would satisfy both blocking tests above."""
        _stub_side_effects(monkeypatch)
        player_f = uuid.uuid4()
        stranger = uuid.uuid4()

        own_clean_ship = _make_ship(
            owner_id=player_f, current_pilot_id=player_f, cooldown_until=None,
        )
        strangers_cooling_ship = _make_ship(
            owner_id=stranger, current_pilot_id=stranger,
            cooldown_until=datetime.now(UTC) + timedelta(hours=5),
        )
        fleet = [own_clean_ship, strangers_cooling_ship]

        player = _make_player(piloted_ship=own_clean_ship)
        player.id = player_f
        origin, destination = _make_sectors()
        db = _FakeSession(
            player=player, fleet=fleet,
            sector_points=[origin, destination], destination_sector=destination,
        )

        result = jump(db, player.id, yaw_deg=0.0, pitch_deg=0.0, range_band="near")

        assert result["outcome"] in ("jump", "misfire")
        assert db.commits == 1
