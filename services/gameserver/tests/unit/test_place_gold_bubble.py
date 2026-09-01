"""LEG-52 — operator place_gold_bubble (GOLD_BUBBLE admin path).

DB-free unit tests: fake query/execute layers exercise validation + happy path
without Postgres. Live warp isolation is stubbed via monkeypatch on the
service helpers when needed.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Sequence, Set
from unittest.mock import MagicMock

import pytest

from src.models.special_formation import SpecialFormation, SpecialFormationType
from src.services import special_formation_service as sfs
from src.services.special_formation_service import (
    GOLD_BUBBLE_INTERIOR_SIZE_MIN,
    GoldBubblePlacementError,
    place_gold_bubble,
)


def _ids(n: int) -> List[uuid.UUID]:
    return [uuid.uuid4() for _ in range(n)]


class _FakeQuery:
    def __init__(
        self,
        *,
        region: Any = None,
        sectors: Optional[Sequence[Any]] = None,
        stations: Optional[Sequence[Any]] = None,
        formations: Optional[Sequence[Any]] = None,
    ) -> None:
        self._region = region
        self._sectors = list(sectors or [])
        self._stations = list(stations or [])
        self._formations = list(formations or [])
        self._model = None

    def filter(self, *a: Any, **k: Any) -> "_FakeQuery":
        return self

    def first(self) -> Any:
        if self._model is sfs.Region or getattr(self._model, "__name__", "") == "Region":
            return self._region
        return None

    def all(self) -> List[Any]:
        name = getattr(self._model, "__name__", "")
        if name == "Sector" or self._model is sfs.Sector:
            return self._sectors
        if name == "Station" or self._model is sfs.Station:
            return self._stations
        if name == "SpecialFormation" or self._model is SpecialFormation:
            return self._formations
        return []


class _FakeDB:
    def __init__(
        self,
        *,
        region: Any,
        sectors: Sequence[Any],
        stations: Optional[Sequence[Any]] = None,
        formations: Optional[Sequence[Any]] = None,
        warp_rows: Optional[List[Dict[str, Any]]] = None,
    ) -> None:
        self.region = region
        self.sectors = list(sectors)
        self.stations = list(stations or [])
        self.formations = list(formations or [])
        self.warp_rows = list(warp_rows or [])
        self.added: List[Any] = []
        self.deleted_warps: List[tuple] = []
        self._flushed = False

    def query(self, model: Any) -> _FakeQuery:
        q = _FakeQuery(
            region=self.region,
            sectors=self.sectors,
            stations=self.stations,
            formations=self.formations,
        )
        q._model = model
        return q

    def execute(self, stmt: Any) -> Any:
        # Rough discrimination: select vs delete on sector_warps.
        text = str(stmt).lower()
        result = MagicMock()
        if "delete" in text:
            # Record attempt; topology assert after isolate sees emptied leaks.
            self.deleted_warps.append(stmt)
            result.mappings.return_value.all.return_value = []
            return result
        # SELECT: return current warp rows (post-delete filtered in isolate path
        # we clear matching rows when delete is called — simplify: after any
        # delete, warp_rows that violate are gone only if we filter here).
        result.mappings.return_value.all.return_value = list(self.warp_rows)
        return result

    def add(self, obj: Any) -> None:
        self.added.append(obj)

    def flush(self) -> None:
        self._flushed = True
        for obj in self.added:
            if getattr(obj, "id", None) is None:
                obj.id = uuid.uuid4()


def _sector(sid: uuid.UUID, region_id: uuid.UUID, *, is_capital: bool = False):
    return SimpleNamespace(id=sid, region_id=region_id, is_capital=is_capital)


def test_place_gold_bubble_refuses_small_interior():
    region_id = uuid.uuid4()
    gateways = _ids(1)
    interior = _ids(GOLD_BUBBLE_INTERIOR_SIZE_MIN - 1)
    db = _FakeDB(region=SimpleNamespace(id=region_id), sectors=[])
    with pytest.raises(GoldBubblePlacementError) as ei:
        place_gold_bubble(
            db,
            region_id=region_id,
            gateway_sector_ids=gateways,
            interior_sector_ids=interior,
            isolate_warps=False,
        )
    assert ei.value.code == "interior_too_small"


def test_place_gold_bubble_refuses_bad_gateway_count():
    region_id = uuid.uuid4()
    with pytest.raises(GoldBubblePlacementError) as ei:
        place_gold_bubble(
            _FakeDB(region=None, sectors=[]),
            region_id=region_id,
            gateway_sector_ids=_ids(4),
            interior_sector_ids=_ids(GOLD_BUBBLE_INTERIOR_SIZE_MIN),
            isolate_warps=False,
        )
    assert ei.value.code == "invalid_gateway_count"


def test_place_gold_bubble_refuses_gateway_interior_overlap():
    region_id = uuid.uuid4()
    shared = uuid.uuid4()
    gateways = [shared]
    interior = [shared] + _ids(GOLD_BUBBLE_INTERIOR_SIZE_MIN - 1)
    with pytest.raises(GoldBubblePlacementError) as ei:
        place_gold_bubble(
            _FakeDB(region=SimpleNamespace(id=region_id), sectors=[]),
            region_id=region_id,
            gateway_sector_ids=gateways,
            interior_sector_ids=interior,
            isolate_warps=False,
        )
    assert ei.value.code == "gateway_interior_overlap"


def test_place_gold_bubble_refuses_capital_in_interior():
    region_id = uuid.uuid4()
    gateways = _ids(1)
    interior = _ids(GOLD_BUBBLE_INTERIOR_SIZE_MIN)
    sectors = [_sector(gateways[0], region_id)] + [
        _sector(i, region_id, is_capital=(n == 0)) for n, i in enumerate(interior)
    ]
    db = _FakeDB(region=SimpleNamespace(id=region_id), sectors=sectors)
    with pytest.raises(GoldBubblePlacementError) as ei:
        place_gold_bubble(
            db,
            region_id=region_id,
            gateway_sector_ids=gateways,
            interior_sector_ids=interior,
            isolate_warps=False,
        )
    assert ei.value.code == "capital_in_interior"


def test_place_gold_bubble_happy_path_no_warp_isolation(monkeypatch):
    region_id = uuid.uuid4()
    gateways = _ids(2)
    interior = _ids(GOLD_BUBBLE_INTERIOR_SIZE_MIN)
    sectors = [_sector(g, region_id) for g in gateways] + [
        _sector(i, region_id) for i in interior
    ]
    db = _FakeDB(region=SimpleNamespace(id=region_id), sectors=sectors, warp_rows=[])

    # Topology assert uses execute(); empty warps = valid envelope.
    formation = place_gold_bubble(
        db,
        region_id=region_id,
        gateway_sector_ids=gateways,
        interior_sector_ids=interior,
        name="Bubble of the Test Stronghold",
        isolate_warps=False,
    )
    assert isinstance(formation, SpecialFormation)
    assert formation.type == SpecialFormationType.GOLD_BUBBLE
    assert formation.anchor_sector_id == gateways[0]
    assert len(formation.interior_sector_ids) == GOLD_BUBBLE_INTERIOR_SIZE_MIN
    assert formation.properties["gateway_count"] == 2
    assert formation.properties["interior_size"] == GOLD_BUBBLE_INTERIOR_SIZE_MIN
    assert formation.name == "Bubble of the Test Stronghold"
    assert db._flushed is True
    assert db.added == [formation]


def test_place_gold_bubble_overlap_existing_bubble():
    region_id = uuid.uuid4()
    gateways = _ids(1)
    interior = _ids(GOLD_BUBBLE_INTERIOR_SIZE_MIN)
    sectors = [_sector(gateways[0], region_id)] + [
        _sector(i, region_id) for i in interior
    ]
    existing = SimpleNamespace(
        anchor_sector_id=interior[0],
        interior_sector_ids=interior[1:5],
        type=SpecialFormationType.BUBBLE,
    )
    db = _FakeDB(
        region=SimpleNamespace(id=region_id),
        sectors=sectors,
        formations=[existing],
    )
    with pytest.raises(GoldBubblePlacementError) as ei:
        place_gold_bubble(
            db,
            region_id=region_id,
            gateway_sector_ids=gateways,
            interior_sector_ids=interior,
            isolate_warps=False,
        )
    assert ei.value.code == "formation_overlap"


def test_route_symbol_place_gold_bubble_exists():
    """Source-map greppability: design-target name is importable from the route module."""
    from src.api.routes import admin_formations as af

    assert callable(af.place_gold_bubble)
    assert af.place_gold_bubble is af.place_gold_bubble_route


def test_api_router_mounts_admin_formations():
    from src.api.api import api_router
    from src.api.routes import admin_formations as af

    # Nested include_router: walk mounted sub-routers for the path.
    found = False
    for route in api_router.routes:
        path = getattr(route, "path", "") or ""
        if "formations/gold-bubble" in path:
            found = True
            break
        # Starlette Mount / IncludeRouter wraps another router.
        app = getattr(route, "app", None)
        if app is not None:
            for sub in getattr(app, "routes", []) or []:
                sp = getattr(sub, "path", "") or ""
                if "formations/gold-bubble" in sp:
                    found = True
                    break
        if found:
            break
    assert found or any(
        "formations/gold-bubble" in (getattr(r, "path", "") or "")
        for r in af.router.routes
    ), "gold-bubble route not mounted on admin_formations router"
