"""LEG-3956 — GET /regions/takeover-eligible discovery list."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient

from src.api.routes import regions as regions_mod
from src.api.routes.regions import list_takeover_eligible_regions_route
from src.models.region import Region, RegionStatus
from src.services.region_lifecycle_service import list_takeover_eligible_regions


def _region(
    *,
    region_id: uuid.UUID,
    name: str,
    status: str,
    suspended_at: datetime | None = None,
) -> Region:
    return Region(
        id=region_id,
        name=name,
        display_name=name.replace("_", " ").title(),
        owner_id=uuid.uuid4(),
        status=status,
        suspended_at=suspended_at,
    )


class TestListTakeoverEligibleRegions:
    @pytest.mark.asyncio
    async def test_returns_only_suspended_and_grace(self):
        suspended_id = uuid.uuid4()
        grace_id = uuid.uuid4()
        suspended_at = datetime(2026, 8, 1, tzinfo=timezone.utc)
        rows = [
            _region(
                region_id=suspended_id,
                name="alpha_suspended",
                status=RegionStatus.SUSPENDED.value,
                suspended_at=suspended_at,
            ),
            _region(
                region_id=grace_id,
                name="beta_grace",
                status=RegionStatus.GRACE.value,
                suspended_at=suspended_at,
            ),
        ]

        scalars = SimpleNamespace(all=lambda: rows)
        db = AsyncMock()
        db.execute = AsyncMock(return_value=SimpleNamespace(scalars=lambda: scalars))

        result = await list_takeover_eligible_regions(db)

        assert len(result) == 2
        assert result[0] == {
            "id": str(suspended_id),
            "name": "alpha_suspended",
            "display_name": "Alpha Suspended",
            "status": RegionStatus.SUSPENDED.value,
            "suspended_at": suspended_at.isoformat(),
        }
        assert result[1]["id"] == str(grace_id)
        assert result[1]["status"] == RegionStatus.GRACE.value

    @pytest.mark.asyncio
    async def test_returns_empty_list_when_none(self):
        scalars = SimpleNamespace(all=lambda: [])
        db = AsyncMock()
        db.execute = AsyncMock(return_value=SimpleNamespace(scalars=lambda: scalars))

        assert await list_takeover_eligible_regions(db) == []


class TestTakeoverEligibleRoute:
    @pytest.mark.asyncio
    async def test_get_returns_200_json_array(self):
        payload = [
            {
                "id": str(uuid.uuid4()),
                "name": "lapsed_region",
                "display_name": "Lapsed Region",
                "status": RegionStatus.SUSPENDED.value,
                "suspended_at": "2026-08-01T00:00:00+00:00",
            }
        ]

        app = FastAPI()
        app.include_router(regions_mod.router, prefix="/api/v1")

        from src.auth.dependencies import require_auth
        from src.core.database import get_async_session

        async def _fake_auth():
            return SimpleNamespace(id=uuid.uuid4())

        async def _fake_db():
            session = AsyncMock()
            yield session

        app.dependency_overrides[require_auth] = _fake_auth
        app.dependency_overrides[get_async_session] = _fake_db

        with patch.object(
            regions_mod,
            "list_takeover_eligible_regions",
            new=AsyncMock(return_value=payload),
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.get("/api/v1/regions/takeover-eligible")

        assert response.status_code == 200
        assert response.json() == payload

    @pytest.mark.asyncio
    async def test_query_failure_is_opaque_500(self):
        secret = "secret-takeover-eligible-list-should-not-leak"
        current_user = SimpleNamespace(id=uuid.uuid4())
        db = AsyncMock()

        with patch.object(
            regions_mod,
            "list_takeover_eligible_regions",
            new=AsyncMock(side_effect=RuntimeError(secret)),
        ):
            with pytest.raises(HTTPException) as excinfo:
                await list_takeover_eligible_regions_route(
                    current_user=current_user,
                    db=db,
                )

        exc = excinfo.value
        assert exc.status_code == 500
        assert exc.detail == "Failed to list takeover-eligible regions"
        assert secret not in str(exc.detail)


def test_regions_takeover_eligible_http500_catches_have_no_detail_str_e():
    """LEG-3956 — static pin: list route 500 detail stays opaque."""
    src = Path(regions_mod.__file__).read_text(encoding="utf-8")
    assert 'detail="Failed to list takeover-eligible regions"' in src
    assert "Failed to list takeover-eligible regions: {str(e)}" not in src
