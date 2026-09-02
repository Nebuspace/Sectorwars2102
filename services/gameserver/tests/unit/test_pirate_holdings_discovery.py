"""LEG-4109 — GET pirate-holdings discovery (sector list + by-id)."""
from __future__ import annotations

import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient

from src.api.routes import pirate_holdings as ph_mod
from src.api.routes.pirate_holdings import (
    ERR_PIRATE_HOLDINGS_GET_FAILED,
    ERR_PIRATE_HOLDINGS_LIST_FAILED,
    get_pirate_holding,
    list_pirate_holdings,
)
from src.auth.dependencies import get_current_player, get_current_user
from src.core.database import get_db
from src.models.pirate_holding import PirateHolding, PirateHoldingTier


def _holding(*, holding_id=None, sector_id=42, tier=PirateHoldingTier.OUTPOST):
    return SimpleNamespace(
        id=holding_id or uuid.uuid4(),
        sector_id=sector_id,
        tier=tier,
    )


class _FakeQuery:
    def __init__(self, rows):
        self._rows = rows

    def filter(self, *args, **kwargs):
        return self

    def all(self):
        return list(self._rows)

    def first(self):
        return self._rows[0] if self._rows else None


class _FakeSession:
    def __init__(self, rows):
        self._rows = rows

    def query(self, model):
        return _FakeQuery(self._rows)


class TestListPirateHoldings:
    @pytest.mark.asyncio
    async def test_returns_holdings_for_sector(self):
        hid = uuid.uuid4()
        holding = _holding(holding_id=hid, sector_id=42, tier=PirateHoldingTier.CAMP)
        db = MagicMock()
        q = MagicMock()
        db.query.return_value = q
        q.filter.return_value = q
        q.all.return_value = [holding]
        user = SimpleNamespace(id=uuid.uuid4())
        player = SimpleNamespace(id=uuid.uuid4())

        result = await list_pirate_holdings(
            sector_id=42,
            db=db,
            current_user=user,
            current_player=player,
        )

        db.query.assert_called_once_with(PirateHolding)
        q.filter.assert_called_once()
        filt = q.filter.call_args[0][0]
        assert filt.left.key == "sector_id"
        assert filt.right.value == 42
        assert result == [
            {"id": str(hid), "tier": "CAMP", "sector_id": 42},
        ]

    @pytest.mark.asyncio
    async def test_empty_list_when_none(self):
        db = _FakeSession([])
        result = await list_pirate_holdings(
            sector_id=99,
            db=db,
            current_user=SimpleNamespace(id=uuid.uuid4()),
            current_player=SimpleNamespace(id=uuid.uuid4()),
        )
        assert result == []

    @pytest.mark.asyncio
    async def test_list_boom_is_opaque_500(self):
        secret = "secret-pirate-holdings-list-should-not-leak"
        db = MagicMock()
        db.query.side_effect = RuntimeError(secret)

        with pytest.raises(HTTPException) as excinfo:
            await list_pirate_holdings(
                sector_id=1,
                db=db,
                current_user=SimpleNamespace(id=uuid.uuid4()),
                current_player=SimpleNamespace(id=uuid.uuid4()),
            )

        exc = excinfo.value
        assert exc.status_code == 500
        assert exc.detail == {
            "error_code": ERR_PIRATE_HOLDINGS_LIST_FAILED,
            "detail": "Failed to list pirate holdings",
        }
        assert secret not in str(exc.detail)


class TestGetPirateHolding:
    @pytest.mark.asyncio
    async def test_returns_id_tier_sector(self):
        hid = uuid.uuid4()
        holding = _holding(
            holding_id=hid, sector_id=7, tier=PirateHoldingTier.STRONGHOLD
        )
        db = _FakeSession([holding])

        result = await get_pirate_holding(
            holding_id=str(hid),
            db=db,
            current_user=SimpleNamespace(id=uuid.uuid4()),
            current_player=SimpleNamespace(id=uuid.uuid4()),
        )

        assert result == {
            "id": str(hid),
            "tier": "STRONGHOLD",
            "sector_id": 7,
        }

    @pytest.mark.asyncio
    async def test_unknown_id_404(self):
        db = _FakeSession([])
        with pytest.raises(HTTPException) as excinfo:
            await get_pirate_holding(
                holding_id=str(uuid.uuid4()),
                db=db,
                current_user=SimpleNamespace(id=uuid.uuid4()),
                current_player=SimpleNamespace(id=uuid.uuid4()),
            )
        assert excinfo.value.status_code == 404
        assert excinfo.value.detail == "Pirate holding not found"

    @pytest.mark.asyncio
    async def test_invalid_uuid_404(self):
        db = _FakeSession([])
        with pytest.raises(HTTPException) as excinfo:
            await get_pirate_holding(
                holding_id="not-a-uuid",
                db=db,
                current_user=SimpleNamespace(id=uuid.uuid4()),
                current_player=SimpleNamespace(id=uuid.uuid4()),
            )
        assert excinfo.value.status_code == 404
        assert excinfo.value.detail == "Pirate holding not found"

    @pytest.mark.asyncio
    async def test_get_boom_is_opaque_500(self):
        secret = "secret-pirate-holdings-get-should-not-leak"
        db = MagicMock()
        db.query.side_effect = RuntimeError(secret)
        hid = str(uuid.uuid4())

        with pytest.raises(HTTPException) as excinfo:
            await get_pirate_holding(
                holding_id=hid,
                db=db,
                current_user=SimpleNamespace(id=uuid.uuid4()),
                current_player=SimpleNamespace(id=uuid.uuid4()),
            )

        exc = excinfo.value
        assert exc.status_code == 500
        assert exc.detail == {
            "error_code": ERR_PIRATE_HOLDINGS_GET_FAILED,
            "detail": "Failed to get pirate holding",
        }
        assert secret not in str(exc.detail)


class TestDiscoveryAuthAndHttp:
    @pytest.mark.asyncio
    async def test_list_requires_auth(self):
        app = FastAPI()
        app.include_router(ph_mod.router, prefix="/api/v1")

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get(
                "/api/v1/pirate-holdings", params={"sector_id": 1}
            )

        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_get_requires_auth(self):
        app = FastAPI()
        app.include_router(ph_mod.router, prefix="/api/v1")

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get(f"/api/v1/pirate-holdings/{uuid.uuid4()}")

        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_list_http_happy_path(self):
        hid = uuid.uuid4()
        holding = _holding(
            holding_id=hid, sector_id=42, tier=PirateHoldingTier.OUTPOST
        )
        payload = [{"id": str(hid), "tier": "OUTPOST", "sector_id": 42}]

        app = FastAPI()
        app.include_router(ph_mod.router, prefix="/api/v1")

        async def _fake_user():
            return SimpleNamespace(id=uuid.uuid4())

        async def _fake_player():
            return SimpleNamespace(id=uuid.uuid4())

        def _db_with_rows():
            yield _FakeSession([holding])

        app.dependency_overrides[get_current_user] = _fake_user
        app.dependency_overrides[get_current_player] = _fake_player
        app.dependency_overrides[get_db] = _db_with_rows

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get(
                "/api/v1/pirate-holdings", params={"sector_id": 42}
            )

        assert response.status_code == 200
        assert response.json() == payload


def test_discovery_http500_is_structured():
    """Static pin: discovery routes use structured opaque 500s."""
    src = Path(ph_mod.__file__).read_text(encoding="utf-8")
    assert ERR_PIRATE_HOLDINGS_LIST_FAILED in src
    assert ERR_PIRATE_HOLDINGS_GET_FAILED in src
    assert "route_internal_error" in src
    assert 'detail="Failed to list pirate holdings"' not in src
    assert 'detail="Failed to get pirate holding"' not in src
