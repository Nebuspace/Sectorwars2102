"""LEG-3764: region GC-subscription takeover slice 2.

Pins ``region_lifecycle_service.execute_takeover`` (advisory lock + Region
``SELECT FOR UPDATE`` before intent creation) and the POST route contract.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from src.api.routes.regions import router as regions_router
from src.models.region import Region, RegionStatus
from src.models.takeover_intent import TakeoverIntent, TakeoverIntentStatus
from src.services.region_lifecycle_service import (
    ERR_GALACTIC_CITIZEN_REQUIRED,
    ERR_ONE_REGION_PER_OWNER,
    ERR_REGION_NOT_AVAILABLE_FOR_TAKEOVER,
    ERR_REGION_NOT_FOUND,
    execute_takeover,
)


def _utc(**delta) -> datetime:
    return datetime.now(timezone.utc) + timedelta(**delta)


def _make_region(*, region_id: uuid.UUID, status: str = RegionStatus.SUSPENDED.value) -> Region:
    return Region(
        id=region_id,
        name="frontier_alpha",
        display_name="Frontier Alpha",
        owner_id=uuid.uuid4(),
        status=status,
    )


class _RecordingSelect:
    """Minimal stand-in for a SQLAlchemy select() chain."""

    def __init__(self, *, with_for_update: bool = False) -> None:
        self.with_for_update_called = with_for_update
        self._entity = None

    def where(self, *args, **kwargs):
        return self

    def with_for_update(self, *args, **kwargs):
        self.with_for_update_called = True
        return self

    def limit(self, *args, **kwargs):
        return self


class TestExecuteTakeover:
    @pytest.fixture
    def ids(self):
        return {
            "region": uuid.uuid4(),
            "caller": uuid.uuid4(),
        }

    @pytest.mark.asyncio
    async def test_acquires_region_row_lock_before_creating_intent(self, ids):
        region = _make_region(region_id=ids["region"])
        region_select = _RecordingSelect()
        player = SimpleNamespace(is_galactic_citizen=True)

        db = AsyncMock()
        db.execute = AsyncMock(
            side_effect=[
                SimpleNamespace(),  # advisory lock
                SimpleNamespace(scalar_one_or_none=lambda: region),  # region FOR UPDATE
                SimpleNamespace(scalar_one_or_none=lambda: player),  # player
                SimpleNamespace(scalar_one_or_none=lambda: None),  # owned region
                SimpleNamespace(scalar_one_or_none=lambda: None),  # pending intent
            ]
        )

        paypal_payload = {
            "id": "sub-123",
            "links": [{"rel": "approve", "href": "https://paypal.example/approve"}],
        }

        with patch(
            "src.services.region_lifecycle_service.select",
            side_effect=lambda *args, **kwargs: region_select,
        ), patch(
            "src.services.region_lifecycle_service.paypal_service.create_regional_ownership_subscription",
            new=AsyncMock(return_value=paypal_payload),
        ) as paypal_mock:
            result = await execute_takeover(
                db,
                region_id=ids["region"],
                caller_user_id=ids["caller"],
                return_url="https://app.example/success",
                cancel_url="https://app.example/cancel",
            )

        assert result["ok"] is True
        assert region_select.with_for_update_called is True
        db.add.assert_called_once()
        intent = db.add.call_args.args[0]
        assert isinstance(intent, TakeoverIntent)
        assert intent.region_id == ids["region"]
        assert intent.caller_user_id == ids["caller"]
        assert intent.status == TakeoverIntentStatus.PENDING.value
        assert intent.approval_url == "https://paypal.example/approve"
        paypal_mock.assert_awaited_once()
        assert paypal_mock.await_args.kwargs.get("takeover_intent_id") == str(intent.id)
        assert db.flush.await_count >= 2

    @pytest.mark.asyncio
    async def test_rejects_non_takeover_eligible_status(self, ids):
        region = _make_region(region_id=ids["region"], status=RegionStatus.ACTIVE.value)
        db = AsyncMock()
        db.execute = AsyncMock(
            side_effect=[
                SimpleNamespace(),
                SimpleNamespace(scalar_one_or_none=lambda: region),
            ]
        )

        result = await execute_takeover(
            db,
            region_id=ids["region"],
            caller_user_id=ids["caller"],
            return_url="https://app.example/success",
            cancel_url="https://app.example/cancel",
        )

        assert result == {"ok": False, "code": ERR_REGION_NOT_AVAILABLE_FOR_TAKEOVER}
        db.add.assert_not_called()

    @pytest.mark.asyncio
    async def test_requires_galactic_citizen(self, ids):
        region = _make_region(region_id=ids["region"])
        player = SimpleNamespace(is_galactic_citizen=False)
        db = AsyncMock()
        db.execute = AsyncMock(
            side_effect=[
                SimpleNamespace(),
                SimpleNamespace(scalar_one_or_none=lambda: region),
                SimpleNamespace(scalar_one_or_none=lambda: player),
            ]
        )

        result = await execute_takeover(
            db,
            region_id=ids["region"],
            caller_user_id=ids["caller"],
            return_url="https://app.example/success",
            cancel_url="https://app.example/cancel",
        )

        assert result == {"ok": False, "code": ERR_GALACTIC_CITIZEN_REQUIRED}

    @pytest.mark.asyncio
    async def test_rejects_existing_region_owner(self, ids):
        region = _make_region(region_id=ids["region"])
        player = SimpleNamespace(is_galactic_citizen=True)
        db = AsyncMock()
        db.execute = AsyncMock(
            side_effect=[
                SimpleNamespace(),
                SimpleNamespace(scalar_one_or_none=lambda: region),
                SimpleNamespace(scalar_one_or_none=lambda: player),
                SimpleNamespace(scalar_one_or_none=lambda: uuid.uuid4()),
            ]
        )

        result = await execute_takeover(
            db,
            region_id=ids["region"],
            caller_user_id=ids["caller"],
            return_url="https://app.example/success",
            cancel_url="https://app.example/cancel",
        )

        assert result == {"ok": False, "code": ERR_ONE_REGION_PER_OWNER}

    @pytest.mark.asyncio
    async def test_region_not_found(self, ids):
        db = AsyncMock()
        db.execute = AsyncMock(
            side_effect=[
                SimpleNamespace(),
                SimpleNamespace(scalar_one_or_none=lambda: None),
            ]
        )

        result = await execute_takeover(
            db,
            region_id=ids["region"],
            caller_user_id=ids["caller"],
            return_url="https://app.example/success",
            cancel_url="https://app.example/cancel",
        )

        assert result == {"ok": False, "code": ERR_REGION_NOT_FOUND}


class TestTakeoverRoute:
    @pytest.mark.asyncio
    async def test_post_returns_201_with_takeover_intent_json(self):
        region_id = uuid.uuid4()
        caller_id = uuid.uuid4()
        intent_payload = {
            "id": str(uuid.uuid4()),
            "region_id": str(region_id),
            "caller_user_id": str(caller_id),
            "approval_url": "https://paypal.example/approve",
            "status": TakeoverIntentStatus.PENDING.value,
            "created_at": _utc().isoformat(),
            "expires_at": _utc(hours=1).isoformat(),
            "completed_at": None,
        }

        app = FastAPI()
        app.include_router(regions_router, prefix="/api/v1")

        from src.auth.dependencies import require_auth
        from src.core.database import get_async_session

        async def _fake_auth():
            return SimpleNamespace(id=caller_id)

        async def _fake_db():
            session = AsyncMock()
            yield session

        app.dependency_overrides[require_auth] = _fake_auth
        app.dependency_overrides[get_async_session] = _fake_db

        with patch(
            "src.api.routes.regions.execute_takeover",
            new=AsyncMock(
                return_value={
                    "ok": True,
                    "takeover_intent": intent_payload,
                }
            ),
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.post(f"/api/v1/regions/{region_id}/takeover", json={})

        assert response.status_code == 201
        assert response.json() == intent_payload
