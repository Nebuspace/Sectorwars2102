"""Unit tests for MovementService._check_for_encounters' drone-encounter
rewire (WO-DRN-SECTOR-ENCOUNTER).

The sector-entry 'drones' branch used to read the write-orphaned
Sector.defenses['defense_drones'] JSONB key -- zero writers (only three
zero-init sites: models/sector.py, nexus_generation_service.py,
bang_import_service.py), so the branch could never fire. It is now rewired
to count live hostile deployed Drone rows, mirroring the attackable set
attack_sector_drones defines (combat_service.py:1426-1431): same sector,
excluding the moving player's own drones, status DEPLOYED/DAMAGED, health
> 0.

Mock-session style mirrors test_movement_region_sync.py: a MagicMock
stands in for the SQLAlchemy session. The Sector query is driven the usual
way (query().filter().first() returns a fixed stand-in); the Drone query
is driven by a tiny in-memory FakeDroneQuery that interprets the actual
filter() conditions the SUT constructs against a real candidate list --
so exclusion (own drones / dead-or-returning status / health<=0) is
exercised for real, not merely asserted by inspection.
"""
import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock

from sqlalchemy.sql import operators

from src.models.drone import Drone, DroneStatus
from src.models.sector import Sector
from src.services.movement_service import MovementService


def make_player(player_id=None):
    return SimpleNamespace(id=player_id or uuid.uuid4())


def make_sector(sector_uuid=None, sector_num=1301, defenses=None):
    """Destination sector stand-in. defenses defaults to None -- the
    rewired branch must not touch the JSONB at all (regression: the old
    code None-guarded sector.defenses; the new code has no reason to)."""
    return SimpleNamespace(
        id=sector_uuid or uuid.uuid4(),
        sector_id=sector_num,
        players_present=[],
        type=SimpleNamespace(name="STANDARD"),
        hazard_level=0,
        defenses=defenses,
    )


def make_drone(sector_id, player_id=None, status=DroneStatus.DEPLOYED.value, health=100):
    return SimpleNamespace(
        sector_id=sector_id,
        player_id=player_id or uuid.uuid4(),
        status=status,
        health=health,
    )


def _condition_matches(drone, condition):
    """Interpret one Drone.<col> <op> <value> clause against a real drone
    stand-in -- the minimal evaluator a fake in-memory query needs to honor
    the exact filter the SUT builds."""
    column = condition.left.key
    actual = getattr(drone, column)
    op = condition.operator
    if op is operators.in_op:
        return actual in condition.right.value
    if op is operators.eq:
        return actual == condition.right.value
    if op is operators.ne:
        return actual != condition.right.value
    if op is operators.gt:
        return actual > condition.right.value
    raise AssertionError(f"unhandled operator {op!r} on column {column!r}")


class FakeDroneQuery:
    """In-memory stand-in for db.query(Drone): filter() records the real
    SQLAlchemy conditions the SUT constructed; count() applies them against
    a fixed candidate list -- no live DB required."""

    def __init__(self, drones):
        self._drones = drones
        self._conditions = ()

    def filter(self, *conditions):
        self._conditions = conditions
        return self

    def count(self):
        return sum(
            1
            for d in self._drones
            if all(_condition_matches(d, c) for c in self._conditions)
        )


def build_service(sector, drones):
    """MovementService over a mock session whose db.query() branches on the
    queried model: Sector -> the fixed destination; Drone -> FakeDroneQuery
    over the given candidate rows."""
    mock_db = MagicMock()

    def query_side_effect(model):
        if model is Sector:
            q = MagicMock()
            q.filter.return_value.first.return_value = sector
            return q
        if model is Drone:
            return FakeDroneQuery(drones)
        raise AssertionError(f"unexpected query target: {model!r}")

    mock_db.query.side_effect = query_side_effect
    return MovementService(mock_db)


def drones_encounter(encounters):
    return next((e for e in encounters if e["type"] == "drones"), None)


class TestDroneSectorEncounter:
    def test_hostile_drones_below_threshold_yield_low_encounter(self):
        """3 hostile deployed drones -> a 'low' threat encounter, count 3."""
        player = make_player()
        sector = make_sector()
        drones = [make_drone(sector.id) for _ in range(3)]
        service = build_service(sector, drones)

        encounters = service._check_for_encounters(player, sector.sector_id)

        assert drones_encounter(encounters) == {
            "type": "drones",
            "count": 3,
            "threat_level": "low",
        }

    def test_hostile_drones_at_or_above_ten_yield_medium_encounter(self):
        """10+ hostile deployed drones -> 'medium' threat."""
        player = make_player()
        sector = make_sector()
        drones = [make_drone(sector.id) for _ in range(12)]
        service = build_service(sector, drones)

        encounters = service._check_for_encounters(player, sector.sector_id)

        assert drones_encounter(encounters) == {
            "type": "drones",
            "count": 12,
            "threat_level": "medium",
        }

    def test_own_drones_excluded_from_hostile_count(self):
        """A sector where every deployed drone belongs to the moving player
        yields no drones encounter -- you cannot ambush yourself."""
        player = make_player()
        sector = make_sector()
        drones = [make_drone(sector.id, player_id=player.id) for _ in range(4)]
        service = build_service(sector, drones)

        encounters = service._check_for_encounters(player, sector.sector_id)

        assert drones_encounter(encounters) is None

    def test_dead_or_returning_drones_excluded_from_hostile_count(self):
        """DESTROYED, RETURNING, and health<=0 drones don't count as
        hostile presence; only the lone standing DAMAGED drone does."""
        player = make_player()
        sector = make_sector()
        drones = [
            make_drone(sector.id, status=DroneStatus.DESTROYED.value),
            make_drone(sector.id, status=DroneStatus.RETURNING.value),
            make_drone(sector.id, status=DroneStatus.DEPLOYED.value, health=0),
            make_drone(sector.id, status=DroneStatus.DAMAGED.value, health=1),
        ]
        service = build_service(sector, drones)

        encounters = service._check_for_encounters(player, sector.sector_id)

        assert drones_encounter(encounters) == {
            "type": "drones",
            "count": 1,
            "threat_level": "low",
        }

    def test_no_drones_and_no_defenses_jsonb_yields_no_encounter(self):
        """Regression: the old code None-guarded sector.defenses; the new
        code doesn't read the JSONB at all, so a sector with defenses=None
        and zero live drones must not raise and must yield nothing."""
        player = make_player()
        sector = make_sector(defenses=None)
        service = build_service(sector, [])

        encounters = service._check_for_encounters(player, sector.sector_id)

        assert drones_encounter(encounters) is None
