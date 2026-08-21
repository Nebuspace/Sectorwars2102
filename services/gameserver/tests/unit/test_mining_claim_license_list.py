"""LEG-435 — owner-scoped GET claim-license list (active + recently expired)."""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock

from src.api.routes import mining as mining_routes
from src.models.claim_license import ClaimLicense
from src.services.mining_service import (
    LICENSE_DURATION_HOURS,
    RECENTLY_EXPIRED_LICENSE_HOURS,
    MiningService,
)


class _FixedDateTime(datetime):
    """Subclass so sqlalchemy/ISO helpers still work; utcnow is pinned."""

    _now = datetime(2026, 8, 17, 15, 0, 0)

    @classmethod
    def utcnow(cls):
        return cls._now


def _license(
    *,
    player_id: uuid.UUID,
    sector_number: int,
    expires_at: datetime,
    purchased_at: datetime | None = None,
    cost_paid_cr: int = 1500,
    region_id: uuid.UUID | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        player_id=player_id,
        region_id=region_id or uuid.uuid4(),
        sector_number=sector_number,
        expires_at=expires_at,
        purchased_at=purchased_at or (expires_at - timedelta(hours=24)),
        cost_paid_cr=cost_paid_cr,
    )


def _mock_db(rows: list) -> MagicMock:
    db = MagicMock()
    q = MagicMock()
    q.filter.return_value = q
    q.order_by.return_value = q
    q.all.return_value = rows
    db.query.return_value = q
    return db


def test_recently_expired_window_matches_license_duration():
    assert RECENTLY_EXPIRED_LICENSE_HOURS == LICENSE_DURATION_HOURS == 24


def test_list_empty_ok(monkeypatch):
    monkeypatch.setattr("src.services.mining_service.datetime", _FixedDateTime)
    player_id = uuid.uuid4()
    svc = MiningService(db=_mock_db([]))
    result = svc.list_player_licenses(player_id)
    assert result["items"] == []
    assert result["total"] == 0
    assert result["recently_expired_window_hours"] == 24


def test_list_includes_active_and_recently_expired(monkeypatch):
    monkeypatch.setattr("src.services.mining_service.datetime", _FixedDateTime)
    now = _FixedDateTime._now
    player_id = uuid.uuid4()
    active = _license(
        player_id=player_id,
        sector_number=10,
        expires_at=now + timedelta(hours=6),
    )
    recent = _license(
        player_id=player_id,
        sector_number=11,
        expires_at=now - timedelta(hours=12),
    )
    svc = MiningService(db=_mock_db([active, recent]))
    result = svc.list_player_licenses(player_id)

    assert result["total"] == 2
    by_sector = {item["sector_number"]: item for item in result["items"]}
    assert by_sector[10]["is_active"] is True
    assert by_sector[11]["is_active"] is False
    assert by_sector[10]["id"] == str(active.id)
    assert by_sector[11]["cost_paid_cr"] == 1500
    assert by_sector[10]["region_id"] == str(active.region_id)
    assert by_sector[10]["purchased_at"] is not None
    assert by_sector[10]["expires_at"] is not None


def test_list_sql_filters_owner_and_cutoff(monkeypatch):
    monkeypatch.setattr("src.services.mining_service.datetime", _FixedDateTime)
    player_id = uuid.uuid4()
    db = _mock_db([])
    MiningService(db=db).list_player_licenses(player_id)

    db.query.assert_called_once_with(ClaimLicense)
    assert db.query.return_value.filter.called
    filter_args = db.query.return_value.filter.call_args[0]
    assert len(filter_args) == 2


def test_list_route_delegates_to_service(monkeypatch):
    player = SimpleNamespace(id=uuid.uuid4())
    db = MagicMock()
    expected = {"items": [], "total": 0, "recently_expired_window_hours": 24}

    class _Svc:
        def __init__(self, _db):
            pass

        def list_player_licenses(self, player_id):
            assert player_id == player.id
            return expected

    monkeypatch.setattr(mining_routes, "MiningService", _Svc)
    result = asyncio.run(mining_routes.list_claim_licenses(player=player, db=db))
    assert result == expected


def test_get_licenses_route_registered():
    get_paths = set()
    post_paths = set()
    for route in mining_routes.router.routes:
        methods = getattr(route, "methods", None) or set()
        path = getattr(route, "path", None)
        if path is None:
            continue
        if "GET" in methods:
            get_paths.add(path)
        if "POST" in methods:
            post_paths.add(path)
    assert "/mining/licenses" in get_paths
    assert "/mining/license" in post_paths
