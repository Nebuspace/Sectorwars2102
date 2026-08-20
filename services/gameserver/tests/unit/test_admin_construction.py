"""LEG-40: admin construction read helpers (DB-free / fake-db style)."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from src.services import construction_service
from src.services.construction_service import ConstructionError


class _FakeQuery:
    def __init__(self, rows):
        self._rows = list(rows)

    def filter(self, *args, **kwargs):
        return self

    def order_by(self, *args, **kwargs):
        return self

    def all(self):
        return list(self._rows)

    def first(self):
        return self._rows[0] if self._rows else None


class _FakeDb:
    def __init__(self, by_model):
        self._by_model = by_model

    def query(self, model):
        return _FakeQuery(self._by_model.get(model, []))


def test_admin_list_tradedocks_filters_null_tier():
    from src.models.station import Station

    a = SimpleNamespace(id=uuid4(), name="TradeDock Prime", tradedock_tier="A", sector_id=50)
    b = SimpleNamespace(id=uuid4(), name="Ordinary Port", tradedock_tier=None, sector_id=10)
    # Fake filter: admin_list uses .filter(Station.tradedock_tier.isnot(None))
    # Our FakeQuery ignores filter predicates — feed only TradeDock rows.
    db = _FakeDb({Station: [a]})
    out = construction_service.admin_list_tradedocks(db)
    assert len(out["tradedocks"]) == 1
    assert out["tradedocks"][0]["tradedock_tier"] == "A"
    assert out["tradedocks"][0]["name"] == "TradeDock Prime"
    assert b.name not in {t["name"] for t in out["tradedocks"]}


def test_admin_reservation_detail_404():
    from src.models.construction import ConstructionReservation

    db = _FakeDb({ConstructionReservation: []})
    with pytest.raises(ConstructionError) as exc:
        construction_service.admin_reservation_detail(db, uuid4())
    assert exc.value.status_code == 404


def test_admin_station_overview_404():
    from src.models.station import Station

    db = _FakeDb({Station: []})
    with pytest.raises(ConstructionError) as exc:
        construction_service.admin_station_overview(db, uuid4())
    assert exc.value.status_code == 404
    assert "station" in exc.value.detail


def test_admin_station_overview_includes_queue_and_slips():
    from src.models.construction import ConstructionReservation
    from src.models.station import Station

    station_id = uuid4()
    station = SimpleNamespace(
        id=station_id, name="TD Alpha", tradedock_tier="B", sector_id=99
    )
    queued = SimpleNamespace(
        id=uuid4(),
        player_id=uuid4(),
        ship_type="SCOUT",
        state="queued",
        priority_bumps_count=1,
        station_id=station_id,
    )
    building = SimpleNamespace(
        id=uuid4(),
        player_id=uuid4(),
        ship_type="FREIGHTER",
        state="building",
        priority_bumps_count=0,
        station_id=station_id,
    )
    claimed = SimpleNamespace(
        id=uuid4(),
        player_id=uuid4(),
        ship_type="SCOUT",
        state="claimed",
        priority_bumps_count=0,
        station_id=station_id,
    )
    db = _FakeDb(
        {
            Station: [station],
            ConstructionReservation: [queued, building, claimed],
        }
    )
    quote_payload = {
        "station_id": str(station_id),
        "station_name": "TD Alpha",
        "tradedock_tier": "B",
        "slips": {
            "standard": {"capacity": 10, "in_use": 1},
            "specialized": {"capacity": 2, "in_use": 0},
        },
        "queue_length": 1,
        "quotes": [{"ship_type": "SCOUT"}],
    }

    with (
        patch.object(construction_service, "quote", return_value=dict(quote_payload)),
        patch.object(
            construction_service,
            "_sorted_queue",
            return_value=[queued],
        ),
        patch.object(
            construction_service,
            "status_payload",
            side_effect=lambda _db, r, now=None: {
                "reservation_id": str(r.id),
                "state": r.state,
            },
        ),
    ):
        out = construction_service.admin_station_overview(db, station_id)

    assert "quotes" not in out
    assert out["slips"]["standard"]["capacity"] == 10
    assert out["queue"] == [
        {
            "position": 1,
            "reservation_id": str(queued.id),
            "player_id": str(queued.player_id),
            "ship_type": "SCOUT",
            "priority_bumps_count": 1,
        }
    ]
    assert out["reservation_count_active"] == 2
    assert out["reservation_count_total"] == 3
    assert {r["state"] for r in out["reservations"]} == {"queued", "building"}


def test_admin_reservation_detail_advances_active():
    from src.models.construction import ConstructionReservation

    rid = uuid4()
    reservation = SimpleNamespace(id=rid, state="building")
    db = _FakeDb({ConstructionReservation: [reservation]})

    with (
        patch.object(construction_service, "advance") as advance,
        patch.object(
            construction_service,
            "status_payload",
            return_value={"reservation_id": str(rid), "state": "building"},
        ) as payload,
    ):
        out = construction_service.admin_reservation_detail(db, rid)

    advance.assert_called_once()
    payload.assert_called_once()
    assert out["reservation_id"] == str(rid)


def test_admin_construction_router_mounts():
    """Smoke: module import + three read routes registered."""
    from src.api.routes import admin_construction

    paths = {getattr(r, "path", None) for r in admin_construction.router.routes}
    assert "/admin/construction/tradedocks" in paths
    assert "/admin/construction/tradedocks/{station_id}" in paths
    assert "/admin/construction/reservations/{reservation_id}" in paths
