"""Unit tests for MovementService._calculate_warp_cost's uniform move-cost
canon (WO-SHIP-MOVECOST-CANON).

movement.md:13 ('Ship.current_speed does not change the turn cost of any
traversal') and movement.md:24 ('A Scout (speed 2.5) and a Cargo Hauler
(speed 0.5) both pay the same') mandate that direct-warp turn cost is
uniform across hull types -- the NO-CANON ship-type multiplier
(FAST_COURIER x0.7 / SCOUT_SHIP x0.8 / CARGO_HAULER x1.2 / COLONY_SHIP x1.3)
and the current_speed ratio factor are deleted. Only the maintenance-band
speed factor (ships.md:89, canon-shipped) still adjusts the base
warp.turn_cost.

Mock-session style mirrors test_movement_drone_encounters.py: a MagicMock
stands in for the SQLAlchemy session, with db.query() branching on the
queried target (sector_warps) to a fixed stand-in warp row.
"""
import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock

from src.models.sector import sector_warps
from src.models.ship import ShipType
from src.services.movement_service import MovementService


def make_sector():
    return SimpleNamespace(id=uuid.uuid4())


def make_warp(turn_cost, is_bidirectional=True):
    return SimpleNamespace(turn_cost=turn_cost, is_bidirectional=is_bidirectional)


def make_ship(ship_type, condition=80.0, current_speed=1.0, base_speed=1.0):
    """condition=80.0 sits in the neutral Good band (75-90, ships.md:68-75)
    -- multiplier exactly 1.0, so a Good-band ship's cost equals the raw
    warp.turn_cost. last_maintenance=None skips lazy decay entirely so the
    condition value is read back exactly as given."""
    return SimpleNamespace(
        type=ship_type,
        current_speed=current_speed,
        base_speed=base_speed,
        maintenance={"condition": condition, "last_maintenance": None},
    )


def build_service(warp):
    """MovementService over a mock session whose db.query(sector_warps)
    always resolves to the fixed warp stand-in -- the warp-lookup path
    itself (forward/reverse-bidirectional fallback) is untouched by this WO
    and is not under test here."""
    mock_db = MagicMock()

    def query_side_effect(model):
        if model is sector_warps:
            q = MagicMock()
            q.filter.return_value.first.return_value = warp
            return q
        raise AssertionError(f"unexpected query target: {model!r}")

    mock_db.query.side_effect = query_side_effect
    return MovementService(mock_db)


class TestUniformWarpMoveCost:
    """movement.md:13/:24 -- every hull type pays the same turn cost."""

    def test_all_hull_types_in_good_band_pay_the_raw_warp_cost(self):
        """turn_cost=3, Good-band condition -> every listed hull type pays
        exactly 3. Falsified if any type-keyed multiplier survives."""
        warp = make_warp(turn_cost=3)
        service = build_service(warp)
        sector_a, sector_b = make_sector(), make_sector()

        for ship_type in (
            ShipType.SCOUT_SHIP,
            ShipType.CARGO_HAULER,
            ShipType.FAST_COURIER,
            ShipType.CITIZEN_CLIPPER,
            ShipType.COLONY_SHIP,
        ):
            ship = make_ship(ship_type)
            cost = service._calculate_warp_cost(sector_a, sector_b, ship)
            assert cost == 3, f"{ship_type} paid {cost}, expected uniform 3"

    def test_halved_current_speed_does_not_change_cost(self):
        """Ship.current_speed must not change the turn cost of any
        traversal (movement.md:13) -- halving it leaves the cost identical."""
        warp = make_warp(turn_cost=3)
        service = build_service(warp)
        sector_a, sector_b = make_sector(), make_sector()

        full_speed_ship = make_ship(ShipType.SCOUT_SHIP, current_speed=2.0, base_speed=2.0)
        half_speed_ship = make_ship(ShipType.SCOUT_SHIP, current_speed=1.0, base_speed=2.0)

        cost_full = service._calculate_warp_cost(sector_a, sector_b, full_speed_ship)
        cost_half = service._calculate_warp_cost(sector_a, sector_b, half_speed_ship)

        assert cost_full == cost_half == 3

    def test_worn_ship_pays_more_than_good_ship_on_same_warp(self):
        """Regression pin: the maintenance speed-band factor (ships.md:68-75)
        must survive the NO-CANON multiplier deletion -- a Worn-band ship
        still pays more than a Good-band ship on the identical warp."""
        # turn_cost=20 is large enough that the ~5% band delta survives
        # int() truncation as a strict integer difference.
        warp = make_warp(turn_cost=20)
        service = build_service(warp)
        sector_a, sector_b = make_sector(), make_sector()

        good_ship = make_ship(ShipType.CARGO_HAULER, condition=80.0)
        worn_ship = make_ship(ShipType.CARGO_HAULER, condition=60.0)

        good_cost = service._calculate_warp_cost(sector_a, sector_b, good_ship)
        worn_cost = service._calculate_warp_cost(sector_a, sector_b, worn_ship)

        assert worn_cost > good_cost

    def test_no_ship_returns_the_raw_warp_turn_cost(self):
        """ship=None path (e.g. path-preview) -- no maintenance band to
        apply, so the raw warp.turn_cost passes through unchanged."""
        warp = make_warp(turn_cost=7)
        service = build_service(warp)
        sector_a, sector_b = make_sector(), make_sector()

        cost = service._calculate_warp_cost(sector_a, sector_b, None)

        assert cost == 7
