"""Unit coverage for WO-ANCHOR-REPAIR-SERVICE detect-only thin v1.

DB-free smart session evaluates SQLAlchemy filter expressions against
in-memory SimpleNamespace rows. Pins skip rules, TERRA→TERRAN, CLASS_1,
unset SpaceDock role → unverifiable, and region_anchor_missing events.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace

from sqlalchemy.sql import operators as sa_ops

from src.models.planet import Planet, PlanetType
from src.models.region import Region, RegionStatus, RegionType
from src.models.sector import Sector
from src.models.station import Station, StationClass, StationStatus
from src.services import anchor_repair_service as ars


def _region(**kw):
    defaults = dict(
        id=uuid.uuid4(),
        name="Rylan Reach",
        status=RegionStatus.ACTIVE,
        region_type=RegionType.PLAYER_OWNED,
        capital_sector_number=1,
    )
    defaults.update(kw)
    return SimpleNamespace(**defaults)


def _sector(region_id, sector_id):
    return SimpleNamespace(region_id=region_id, sector_id=sector_id)


def _planet(region_id, sector_id, ptype=PlanetType.TERRAN):
    return SimpleNamespace(
        id=uuid.uuid4(), region_id=region_id, sector_id=sector_id, type=ptype,
    )


def _station(region_id, sector_id, **kw):
    defaults = dict(
        id=uuid.uuid4(),
        region_id=region_id,
        sector_id=sector_id,
        station_class=StationClass.CLASS_1,
        status=StationStatus.OPERATIONAL,
        is_spacedock=False,
        region_assignment_role=None,
    )
    defaults.update(kw)
    return SimpleNamespace(**defaults)


class _SmartQuery:
    def __init__(self, rows, project_sector_id=False):
        self._rows = list(rows)
        self._filters = []
        self._project_sector_id = project_sector_id

    def filter(self, *conds):
        self._filters.extend(conds)
        return self

    def _eval_cond(self, row, cond) -> bool:
        if hasattr(cond, "clauses") and not hasattr(cond, "left"):
            return all(self._eval_cond(row, c) for c in cond.clauses)

        # Column.is_(True) → UnaryExpression
        if hasattr(cond, "element") and getattr(cond, "modifier", None) is not None:
            key = getattr(cond.element, "key", None)
            if key is not None:
                return bool(getattr(row, key, None)) is True

        left = getattr(cond, "left", None)
        right = getattr(cond, "right", None)
        op = getattr(cond, "operator", None)
        if left is None:
            return True
        key = getattr(left, "key", None)
        if key is None:
            return True
        actual = getattr(row, key, None)
        op_name = getattr(op, "__name__", str(op))

        if op is sa_ops.in_op or op_name == "in_op":
            if hasattr(right, "value"):
                raw = right.value
                values = list(raw) if isinstance(raw, (list, tuple, set)) else [raw]
            elif hasattr(right, "clauses"):
                values = [getattr(c, "value", c) for c in right.clauses]
            else:
                try:
                    values = list(right)
                except TypeError:
                    values = [getattr(right, "value", right)]
            return actual in values

        if op is sa_ops.is_ or op_name in ("is_", "is"):
            expected = getattr(right, "value", right)
            return actual is expected

        expected = getattr(right, "value", right)
        return actual == expected

    def all(self):
        matched = [
            r for r in self._rows
            if all(self._eval_cond(r, c) for c in self._filters)
        ]
        if self._project_sector_id:
            return [(r.sector_id,) for r in matched]
        return matched

    def first(self):
        rows = self.all()
        return rows[0] if rows else None


class _SmartSession:
    def __init__(self, *, regions, sectors, planets, stations):
        self.regions = regions
        self.sectors = sectors
        self.planets = planets
        self.stations = stations

    def query(self, *entities):
        first = entities[0]
        if getattr(first, "class_", None) is Sector and getattr(first, "key", None) == "sector_id":
            return _SmartQuery(self.sectors, project_sector_id=True)
        if first is Region or getattr(first, "class_", None) is Region:
            return _SmartQuery(self.regions)
        if first is Planet or getattr(first, "class_", None) is Planet:
            return _SmartQuery(self.planets)
        if first is Station or getattr(first, "class_", None) is Station:
            return _SmartQuery(self.stations)
        raise AssertionError(f"unhandled query {entities!r}")


def _scan(region, sectors, planets, stations):
    return ars.scan_region(
        _SmartSession(
            regions=[region], sectors=sectors, planets=planets, stations=stations,
        ),
        region,
    )


def test_region_is_scannable_skips_nexus_and_non_active():
    assert ars.region_is_scannable(_region()) is True
    assert ars.region_is_scannable(
        _region(region_type=RegionType.CENTRAL_NEXUS),
    ) is False
    assert ars.region_is_scannable(
        _region(status=RegionStatus.SUSPENDED),
    ) is False
    assert ars.region_is_scannable(_region(status="active")) is True


def test_capital_global_id_mapping():
    assert ars._region_capital_global_id(
        _region(capital_sector_number=1), [100, 101, 102],
    ) == 100
    assert ars._region_capital_global_id(
        _region(capital_sector_number=3), [100, 101, 102],
    ) == 102


def test_scan_all_present_no_events():
    rid = uuid.uuid4()
    region = _region(id=rid)
    sectors = [_sector(rid, 10), _sector(rid, 11), _sector(rid, 12)]
    planets = [_planet(rid, 10)]
    stations = [
        _station(rid, 11, station_class=StationClass.CLASS_1),
        _station(
            rid, 10, is_spacedock=True, region_assignment_role=ars.ROLE_STARTER,
            station_class=StationClass.CLASS_0,
        ),
        _station(
            rid, 12, is_spacedock=True, region_assignment_role=ars.ROLE_FRONTIER,
            station_class=StationClass.CLASS_0,
        ),
    ]
    outcome = _scan(region, sectors, planets, stations)
    assert outcome["checks"][ars.ANCHOR_CAPITAL_TERRA] == "present"
    assert outcome["checks"][ars.ANCHOR_CLASS1_COMMERCE] == "present"
    assert outcome["checks"][ars.ANCHOR_SPACEDOCK_STARTER] == "present"
    assert outcome["checks"][ars.ANCHOR_SPACEDOCK_FRONTIER] == "present"
    assert outcome["events"] == []


def test_scan_missing_emits_region_anchor_missing():
    rid = uuid.uuid4()
    region = _region(id=rid, name="Empty Reach")
    sectors = [_sector(rid, 50), _sector(rid, 51)]
    outcome = _scan(region, sectors, [], [])
    assert all(v == "missing" for v in outcome["checks"].values())
    assert len(outcome["events"]) == 4
    assert {e["type"] for e in outcome["events"]} == {"region_anchor_missing"}
    assert {e["anchor_type"] for e in outcome["events"]} == {
        ars.ANCHOR_CAPITAL_TERRA,
        ars.ANCHOR_CLASS1_COMMERCE,
        ars.ANCHOR_SPACEDOCK_STARTER,
        ars.ANCHOR_SPACEDOCK_FRONTIER,
    }


def test_spacedock_unset_role_is_unverifiable_not_missing():
    rid = uuid.uuid4()
    region = _region(id=rid)
    sectors = [_sector(rid, 1), _sector(rid, 2)]
    planets = [_planet(rid, 1)]
    stations = [
        _station(rid, 2, station_class=StationClass.CLASS_1),
        _station(
            rid, 1, is_spacedock=True, region_assignment_role=None,
            station_class=StationClass.CLASS_0,
        ),
        _station(
            rid, 2, is_spacedock=True, region_assignment_role=None,
            station_class=StationClass.CLASS_0,
        ),
    ]
    outcome = _scan(region, sectors, planets, stations)
    assert outcome["checks"][ars.ANCHOR_SPACEDOCK_STARTER] == "unverifiable"
    assert outcome["checks"][ars.ANCHOR_SPACEDOCK_FRONTIER] == "unverifiable"
    assert not any(
        e["anchor_type"] in (
            ars.ANCHOR_SPACEDOCK_STARTER, ars.ANCHOR_SPACEDOCK_FRONTIER,
        )
        for e in outcome["events"]
    )


def test_run_daily_scan_skips_nexus():
    nexus = _region(region_type=RegionType.CENTRAL_NEXUS)
    active = _region()
    sectors = [_sector(active.id, 1), _sector(active.id, 2)]
    planets = [_planet(active.id, 1)]
    stations = [
        _station(active.id, 2, station_class=StationClass.CLASS_1),
        _station(
            active.id, 1, is_spacedock=True,
            region_assignment_role=ars.ROLE_STARTER,
            station_class=StationClass.CLASS_0,
        ),
        _station(
            active.id, 2, is_spacedock=True,
            region_assignment_role=ars.ROLE_FRONTIER,
            station_class=StationClass.CLASS_0,
        ),
    ]
    db = _SmartSession(
        regions=[nexus, active],
        sectors=sectors,
        planets=planets,
        stations=stations,
    )
    result = ars.run_daily_scan(db)
    assert result["regions_scanned"] == 1
    assert result["missing_count"] == 0
