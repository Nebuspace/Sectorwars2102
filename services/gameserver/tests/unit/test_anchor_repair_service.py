"""Unit coverage for anchor-repair detect + Phase-11 reinjection.

DB-free smart session evaluates SQLAlchemy filter expressions against
in-memory SimpleNamespace / ORM rows. Pins skip rules, TERRA→TERRAN,
CLASS_1, unset SpaceDock role → unverifiable, region_anchor_missing,
and reinject placement (capital / capital+1 / capital+9 / total−5).
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import patch

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
    return SimpleNamespace(
        id=uuid.uuid4(), region_id=region_id, sector_id=sector_id,
    )


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
        self._order_by = None

    def filter(self, *conds):
        self._filters.extend(conds)
        return self

    def order_by(self, *args):
        self._order_by = args
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
        self.regions = list(regions)
        self.sectors = list(sectors)
        self.planets = list(planets)
        self.stations = list(stations)
        self.other = []

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
        if first is Sector or getattr(first, "class_", None) is Sector:
            return _SmartQuery(self.sectors)
        raise AssertionError(f"unhandled query {entities!r}")

    def add(self, obj):
        if isinstance(obj, Planet) or getattr(obj, "__tablename__", None) == "planets":
            sid = getattr(obj, "id", None) or uuid.uuid4()
            self.planets.append(SimpleNamespace(
                id=sid,
                region_id=obj.region_id,
                sector_id=obj.sector_id,
                type=obj.type,
            ))
            obj.id = sid
            return
        if isinstance(obj, Station) or getattr(obj, "__tablename__", None) == "stations":
            sid = getattr(obj, "id", None) or uuid.uuid4()
            self.stations.append(SimpleNamespace(
                id=sid,
                region_id=obj.region_id,
                sector_id=obj.sector_id,
                station_class=obj.station_class,
                status=obj.status,
                is_spacedock=bool(getattr(obj, "is_spacedock", False)),
                region_assignment_role=getattr(obj, "region_assignment_role", None),
                commodities=getattr(obj, "commodities", None),
            ))
            obj.id = sid
            return
        self.other.append(obj)

    def flush(self):
        return None

    def execute(self, *_a, **_k):
        return None


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


def test_phase11_candidate_offsets():
    sids = list(range(100, 120))  # local 1..20
    capital = 100
    class1 = ars._class1_candidates(sids, capital, capital_local=1)
    assert class1[0] == 101  # capital+1
    starter_dock = ars._starter_dock_candidates(sids, capital, capital_local=1)
    assert starter_dock[0] == 109  # capital+9
    frontier = ars._frontier_dock_candidates(sids, capital)
    assert frontier[0] == 114  # total 20 → local 15 → global 114


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
    out = _scan(region, sectors, planets, stations)
    assert out["events"] == []
    assert all(v == "present" for v in out["checks"].values())


def test_scan_missing_terra_and_class1():
    rid = uuid.uuid4()
    region = _region(id=rid)
    sectors = [_sector(rid, i) for i in range(10, 20)]
    out = _scan(region, sectors, planets=[], stations=[])
    assert out["checks"][ars.ANCHOR_CAPITAL_TERRA] == "missing"
    assert out["checks"][ars.ANCHOR_CLASS1_COMMERCE] == "missing"
    assert out["checks"][ars.ANCHOR_SPACEDOCK_STARTER] == "missing"
    assert out["checks"][ars.ANCHOR_SPACEDOCK_FRONTIER] == "missing"
    assert any(e["type"] == "region_anchor_missing" for e in out["events"])


def test_spacedock_unset_roles_unverifiable():
    rid = uuid.uuid4()
    region = _region(id=rid)
    sectors = [_sector(rid, 1), _sector(rid, 2)]
    planets = [_planet(rid, 1)]
    stations = [
        _station(rid, 1, station_class=StationClass.CLASS_1),
        _station(
            rid, 1, is_spacedock=True, region_assignment_role=None,
            station_class=StationClass.CLASS_0,
        ),
        _station(
            rid, 2, is_spacedock=True, region_assignment_role=None,
            station_class=StationClass.CLASS_0,
        ),
    ]
    out = _scan(region, sectors, planets, stations)
    assert out["checks"][ars.ANCHOR_SPACEDOCK_STARTER] == "unverifiable"
    assert out["checks"][ars.ANCHOR_SPACEDOCK_FRONTIER] == "unverifiable"


def test_run_daily_scan_skips_nexus():
    nexus = _region(region_type=RegionType.CENTRAL_NEXUS, name="Nexus")
    active = _region(name="Active")
    rid = active.id
    sectors = [_sector(rid, 1), _sector(rid, 2), _sector(rid, 3)]
    planets = [_planet(rid, 1)]
    stations = [
        _station(rid, 2, station_class=StationClass.CLASS_1),
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
    assert result["repaired_count"] == 0


def test_reinject_places_all_four_phase11_anchors():
    rid = uuid.uuid4()
    region = _region(id=rid, capital_sector_number=1)
    # Contiguous band local 1..20 → global 100..119
    sectors = [_sector(rid, sid) for sid in range(100, 120)]
    db = _SmartSession(
        regions=[region], sectors=sectors, planets=[], stations=[],
    )
    inert = {
        "ore": {"buys": True, "sells": False, "quantity": 10,
                "base_price": 15, "current_price": 15},
    }

    with patch.object(ars, "_seed_commodities", return_value=inert), \
            patch.object(ars, "_attach_market_prices"):
        repair = ars.reinject_missing_anchors(
            db,
            region,
            {
                ars.ANCHOR_CAPITAL_TERRA: "missing",
                ars.ANCHOR_CLASS1_COMMERCE: "missing",
                ars.ANCHOR_SPACEDOCK_STARTER: "missing",
                ars.ANCHOR_SPACEDOCK_FRONTIER: "missing",
            },
            sids=[s.sector_id for s in sectors],
        )

    assert repair["repaired"] == 4
    assert repair["failed"] == 0
    assert all(e["type"] == "region_anchor_repaired" for e in repair["events"])

    terra = [p for p in db.planets if getattr(p, "type", None) == PlanetType.TERRAN]
    assert len(terra) == 1
    assert terra[0].sector_id == 100

    class1 = [
        s for s in db.stations
        if getattr(s, "station_class", None) == StationClass.CLASS_1
        and not getattr(s, "is_spacedock", False)
    ]
    assert len(class1) == 1
    assert class1[0].sector_id == 101  # capital+1

    starter = [
        s for s in db.stations
        if getattr(s, "region_assignment_role", None) == ars.ROLE_STARTER
    ]
    assert len(starter) == 1
    assert starter[0].sector_id == 109  # capital+9
    assert starter[0].is_spacedock is True

    frontier = [
        s for s in db.stations
        if getattr(s, "region_assignment_role", None) == ars.ROLE_FRONTIER
    ]
    assert len(frontier) == 1
    assert frontier[0].sector_id == 114  # total−5

    # Idempotent: re-scan finds all present (no further repair needed).
    again = ars.scan_region(db, region)
    assert all(v == "present" for v in again["checks"].values())
    assert again["events"] == []


def test_reinject_class1_falls_back_when_preferred_occupied():
    rid = uuid.uuid4()
    region = _region(id=rid, capital_sector_number=1)
    sectors = [_sector(rid, sid) for sid in range(100, 110)]
    # Terra + both SpaceDocks present; capital+1 occupied by unrelated port.
    planets = [_planet(rid, 100)]
    stations = [
        _station(rid, 101, station_class=StationClass.CLASS_5),
        _station(
            rid, 109, is_spacedock=True, region_assignment_role=ars.ROLE_STARTER,
            station_class=StationClass.CLASS_0,
        ),
        _station(
            rid, 108, is_spacedock=True, region_assignment_role=ars.ROLE_FRONTIER,
            station_class=StationClass.CLASS_0,
        ),
    ]
    db = _SmartSession(
        regions=[region], sectors=sectors, planets=planets, stations=stations,
    )
    inert = {
        "ore": {"buys": True, "sells": False, "quantity": 10,
                "base_price": 15, "current_price": 15},
    }
    with patch.object(ars, "_seed_commodities", return_value=inert), \
            patch.object(ars, "_attach_market_prices"):
        repair = ars.reinject_missing_anchors(
            db, region,
            {ars.ANCHOR_CLASS1_COMMERCE: "missing"},
            sids=[s.sector_id for s in sectors],
        )
    assert repair["repaired"] == 1
    class1 = [
        s for s in db.stations
        if getattr(s, "station_class", None) == StationClass.CLASS_1
    ]
    assert len(class1) == 1
    assert class1[0].sector_id != 101
    assert class1[0].sector_id in set(range(100, 110))
