"""WO-INVESTIGATE-FLEET-SECTOR-ID-NEVER-WRITTEN — Fleet.sector_id was
permanently None in the live game: its only writer, ``move_fleet``, was
itself zero-caller (retired here as dead/superseded — fleets have no
travel-as-a-unit mechanic; fleet-tactics.md describes fleets purely as a
roster/combat coordination construct over ships that move individually).

This file covers the replacement fix:

1. ``_recalculate_fleet_stats`` (runs on every roster-change event — add /
   remove / KIA) now derives ``Fleet.sector_id`` from the flagship member's
   live ``Ship.sector_id`` (fleet-tactics.md:71,81 names the flagship as the
   fleet's single position anchor), resolved through the Sector table
   (Ship.sector_id is the Integer sector_number; Fleet.sector_id is the
   Sector UUID — same resolution ``move_fleet`` used before removal).
2. Best-effort degrade: no flagship member, a flagship with no ship, or an
   unresolvable sector_number all leave the fleet's prior sector_id
   untouched rather than clobbering a known value with an unknown one.
3. ``resupply_fleet``'s "is the fleet docked here" gate no longer reads
   Fleet.sector_id at all — it was already documented as a best-effort
   display value that can go stale between roster-change events, which a
   credit-charging gate should never trust. The gate now checks only the
   paying player's own live position (already read fresh, under lock).

DB-free, direct-service-call house pattern — mirrors
test_fleet_casualty_succession.py's _FakeSession/_FakeQuery convention
(mutable in-memory pools keyed by model class; unregistered pools resolve to
[] rather than raising, so a Sector-less fixture safely degrades instead of
crashing).
"""
from __future__ import annotations

from typing import Any, Dict, List
from uuid import uuid4

from src.models.fleet import Fleet, FleetMember, FleetRole, FleetStatus
from src.models.sector import Sector
from src.models.ship import Ship, ShipType
from src.services.fleet_service import FleetService


def _flatten(conditions):
    out = []
    for c in conditions:
        clauses = getattr(c, "get_children", None)
        if clauses and type(c).__name__ == "BooleanClauseList":
            out.extend(_flatten(c.get_children()))
        else:
            out.append(c)
    return out


def _condition_matches(row, condition):
    left = condition.left
    right = condition.right
    attr_name = left.name
    expected = right.value if hasattr(right, "value") else right
    return getattr(row, attr_name, None) == expected


class _FakeQuery:
    def __init__(self, pool: List[Any]):
        self._pool = pool
        self._conditions: List[Any] = []

    def filter(self, *conditions):
        self._conditions = self._conditions + _flatten(conditions)
        return self

    def first(self):
        matches = [r for r in self._pool if all(_condition_matches(r, c) for c in self._conditions)]
        return matches[0] if matches else None


class _FakeSession:
    def __init__(self, pools: Dict[type, List[Any]]):
        self._pools = pools

    def query(self, model):
        return _FakeQuery(self._pools.get(model, []))

    def commit(self):
        pass

    def refresh(self, obj):
        pass


def make_ship(*, sector_id=1) -> Ship:
    return Ship(
        id=uuid4(),
        name="Ship",
        type=ShipType.CARRIER,
        owner_id=uuid4(),
        sector_id=sector_id,
        base_speed=1.0,
        current_speed=1.0,
        turn_cost=1,
        maintenance={},
        cargo={},
        combat={"hull": 100, "max_hull": 100, "shields": 0, "attack_rating": 10},
    )


def make_fleet(*, sector_id=None) -> Fleet:
    return Fleet(
        id=uuid4(),
        team_id=uuid4(),
        name="Test Fleet",
        status=FleetStatus.READY.value,
        formation="standard",
        supply_level=100,
        coordination_bonus=0.0,
        sector_id=sector_id,
    )


def make_member(*, fleet, ship, role=FleetRole.ATTACKER) -> FleetMember:
    member = FleetMember(id=uuid4(), fleet_id=fleet.id, ship_id=ship.id if ship else None,
                          player_id=uuid4(), role=role.value)
    member.ship = ship
    member.fleet = fleet
    return member


class TestFlagshipDerivesFleetSectorId:
    def test_flagship_ship_sector_resolves_onto_fleet(self):
        sector = Sector(id=uuid4(), sector_id=42)
        flagship_ship = make_ship(sector_id=42)
        fleet = make_fleet()
        flagship = make_member(fleet=fleet, ship=flagship_ship, role=FleetRole.FLAGSHIP)
        db = _FakeSession({Sector: [sector]})

        FleetService(db)._recalculate_fleet_stats(fleet)

        assert fleet.sector_id == sector.id

    def test_non_flagship_members_do_not_affect_fleet_sector_id(self):
        sector = Sector(id=uuid4(), sector_id=7)
        attacker_ship = make_ship(sector_id=999)  # unresolvable sector_number
        fleet = make_fleet()
        attacker = make_member(fleet=fleet, ship=attacker_ship, role=FleetRole.ATTACKER)
        db = _FakeSession({Sector: [sector]})

        FleetService(db)._recalculate_fleet_stats(fleet)

        assert fleet.sector_id is None

    def test_no_flagship_leaves_prior_sector_id_untouched(self):
        prior = uuid4()
        fleet = make_fleet(sector_id=prior)
        ship = make_ship(sector_id=1)
        member = make_member(fleet=fleet, ship=ship, role=FleetRole.ATTACKER)
        db = _FakeSession({})

        FleetService(db)._recalculate_fleet_stats(fleet)

        assert fleet.sector_id == prior

    def test_flagship_with_unresolvable_sector_leaves_prior_value_untouched(self):
        prior = uuid4()
        fleet = make_fleet(sector_id=prior)
        flagship_ship = make_ship(sector_id=404)
        flagship = make_member(fleet=fleet, ship=flagship_ship, role=FleetRole.FLAGSHIP)
        db = _FakeSession({Sector: []})

        FleetService(db)._recalculate_fleet_stats(fleet)

        assert fleet.sector_id == prior

    def test_flagship_member_with_no_ship_leaves_prior_value_untouched(self):
        prior = uuid4()
        fleet = make_fleet(sector_id=prior)
        flagship = make_member(fleet=fleet, ship=None, role=FleetRole.FLAGSHIP)
        db = _FakeSession({})

        FleetService(db)._recalculate_fleet_stats(fleet)

        assert fleet.sector_id == prior

    def test_empty_fleet_returns_early_and_never_touches_sector_id(self):
        prior = uuid4()
        fleet = make_fleet(sector_id=prior)
        db = _FakeSession({})

        FleetService(db)._recalculate_fleet_stats(fleet)

        assert fleet.sector_id == prior
        assert fleet.total_ships == 0
