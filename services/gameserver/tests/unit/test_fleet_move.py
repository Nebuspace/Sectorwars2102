"""LEG-49: FleetService.move_fleet — relocate fleet + member ships as a unit."""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Dict, List, Optional
from uuid import uuid4

import pytest

from src.models.fleet import Fleet, FleetMember, FleetStatus
from src.models.sector import Sector
from src.models.ship import Ship
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

    def with_for_update(self, *args, **kwargs):
        return self

    def order_by(self, *args, **kwargs):
        return self

    def all(self):
        matches = [
            r for r in self._pool if all(_condition_matches(r, c) for c in self._conditions)
        ]
        return matches if self._conditions else list(self._pool)

    def first(self):
        matches = self.all() if self._conditions else list(self._pool)
        # When filter applied, all() already filtered; when empty conditions,
        # prefer first of pool — but all() with empty conditions returns full pool.
        if self._conditions:
            matches = [
                r
                for r in self._pool
                if all(_condition_matches(r, c) for c in self._conditions)
            ]
        else:
            matches = list(self._pool)
        return matches[0] if matches else None


class _FakeSession:
    def __init__(self, pools: Dict[type, List[Any]]):
        self._pools = pools
        self.committed = False

    def query(self, model):
        return _FakeQuery(self._pools.get(model, []))

    def commit(self):
        self.committed = True

    def refresh(self, obj):
        pass


def _make_fleet(*, status: str = FleetStatus.READY.value, sector_uuid=None):
    fid = uuid4()
    ship_a = SimpleNamespace(id=uuid4(), sector_id=10)
    ship_b = SimpleNamespace(id=uuid4(), sector_id=10)
    members = [
        SimpleNamespace(ship=ship_a),
        SimpleNamespace(ship=ship_b),
    ]
    fleet = SimpleNamespace(
        id=fid,
        status=status,
        sector_id=sector_uuid,
        members=members,
        commander_id=uuid4(),
    )
    return fleet, ship_a, ship_b


def test_move_fleet_updates_fleet_and_member_ships():
    origin_uuid = uuid4()
    dest_uuid = uuid4()
    fleet, ship_a, ship_b = _make_fleet(sector_uuid=origin_uuid)
    origin = SimpleNamespace(id=origin_uuid, sector_id=10)
    dest = SimpleNamespace(id=dest_uuid, sector_id=99)
    db = _FakeSession({Fleet: [fleet], Sector: [origin, dest]})

    # Patch lock helper to return our fleet (FakeQuery with_for_update path)
    svc = FleetService(db)
    svc._lock_fleets_ascending = lambda ids: {fleet.id: fleet}  # type: ignore[method-assign]

    out = svc.move_fleet(fleet.id, dest_uuid)

    assert fleet.sector_id == dest_uuid
    assert ship_a.sector_id == 99
    assert ship_b.sector_id == 99
    assert db.committed is True
    assert out["event"]["type"] == "fleet_moved"
    assert out["event"]["origin_sector_id"] == str(origin_uuid)
    assert out["event"]["destination_sector_id"] == str(dest_uuid)
    assert out["event"]["origin_sector_number"] == 10
    assert out["event"]["destination_sector_number"] == 99


def test_move_fleet_rejects_in_battle():
    fleet, _, _ = _make_fleet(status=FleetStatus.IN_BATTLE.value)
    dest_uuid = uuid4()
    dest = SimpleNamespace(id=dest_uuid, sector_id=5)
    db = _FakeSession({Fleet: [fleet], Sector: [dest]})
    svc = FleetService(db)
    svc._lock_fleets_ascending = lambda ids: {fleet.id: fleet}  # type: ignore[method-assign]

    with pytest.raises(ValueError, match="battle"):
        svc.move_fleet(fleet.id, dest_uuid)


def test_move_fleet_rejects_missing_sector():
    fleet, _, _ = _make_fleet()
    db = _FakeSession({Fleet: [fleet], Sector: []})
    svc = FleetService(db)
    svc._lock_fleets_ascending = lambda ids: {fleet.id: fleet}  # type: ignore[method-assign]

    with pytest.raises(ValueError, match="not found"):
        svc.move_fleet(fleet.id, uuid4())


def test_move_fleet_route_registered():
    from src.api.routes import fleets as fleets_routes

    paths = {getattr(r, "path", None) for r in fleets_routes.router.routes}
    assert "/fleets/{fleet_id}/move" in paths
