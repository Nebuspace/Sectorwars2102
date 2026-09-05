"""Admin region transfer-ownership routes — LEG-DEC-500 / LEG-3207 (DB-free)."""

from __future__ import annotations

import inspect
import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from src.auth.admin_scopes import REGIONS_TRANSFER_OWNERSHIP
from src.auth.dependencies import get_current_user
from src.core.database import get_db
from src.main import app
from src.models.region import RegionStatus, RegionType
from src.services.region_lifecycle_service import AdminRegionTransferOwnershipError

API = "/api/v1/admin/regions"


def _admin_user():
    return SimpleNamespace(id=uuid.uuid4(), username="regadmin", is_admin=True)


def _player_user():
    return SimpleNamespace(id=uuid.uuid4(), username="player", is_admin=False)


def _make_region(name: str = "TestRegion-Alpha", owner_id=None):
    region = MagicMock()
    region.id = uuid.uuid4()
    region.name = name
    region.display_name = "Test Region Alpha"
    region.region_type = RegionType.PLAYER_OWNED.value
    region.status = RegionStatus.ACTIVE.value
    region.cleanup_completed_at = None
    region.owner_id = owner_id or uuid.uuid4()
    return region


def _make_db(*, region=None, new_owner=None, grant=True):
    db = MagicMock()

    def _query(model):
        q = MagicMock()
        name = getattr(model, "__name__", str(model))
        model_s = str(model)
        if "AdminScopeGrant" in model_s or name == "AdminScopeGrant":
            q.filter.return_value.first.return_value = (uuid.uuid4(),) if grant else None
        elif "Region" in model_s or name == "Region":
            q.filter.return_value.with_for_update.return_value.first.return_value = region
            q.filter.return_value.first.return_value = region
        elif "User" in model_s or name == "User":
            q.filter.return_value.first.return_value = new_owner
        else:
            q.filter.return_value.first.return_value = None
        return q

    db.query.side_effect = _query
    return db


@pytest.fixture
def transfer_client():
    return TestClient(app, base_url="http://localhost")


@pytest.fixture(autouse=True)
def _isolate_overrides():
    saved_user = app.dependency_overrides.get(get_current_user)
    saved_db = app.dependency_overrides.get(get_db)
    yield
    for key, saved in ((get_current_user, saved_user), (get_db, saved_db)):
        if saved is not None:
            app.dependency_overrides[key] = saved
        else:
            app.dependency_overrides.pop(key, None)


class TestRouteWiring:
    def test_route_requires_regions_transfer_ownership_scope(self):
        from src.api.routes import admin as admin_routes

        post_src = inspect.getsource(admin_routes.post_region_transfer_ownership)
        assert "require_scope(REGIONS_TRANSFER_OWNERSHIP)" in post_src
        assert "admin_action_attempt" in post_src
        assert "region_transfer_ownership" in post_src


class TestPostRegionTransferOwnershipAuthz:
    def test_unauthenticated_returns_401(self, transfer_client):
        rid = uuid.uuid4()
        resp = transfer_client.post(
            f"{API}/{rid}/transfer-ownership",
            json={"newOwnerId": str(uuid.uuid4()), "reason": "recovery"},
        )
        assert resp.status_code == 401

    def test_non_admin_returns_403(self, transfer_client):
        app.dependency_overrides[get_current_user] = _player_user
        rid = uuid.uuid4()
        resp = transfer_client.post(
            f"{API}/{rid}/transfer-ownership",
            json={"newOwnerId": str(uuid.uuid4()), "reason": "recovery"},
        )
        assert resp.status_code == 403

    def test_missing_scope_grant_returns_403(self, transfer_client):
        app.dependency_overrides[get_current_user] = _admin_user
        app.dependency_overrides[get_db] = lambda: _make_db(
            region=_make_region(),
            new_owner=SimpleNamespace(id=uuid.uuid4()),
            grant=False,
        )
        rid = uuid.uuid4()
        resp = transfer_client.post(
            f"{API}/{rid}/transfer-ownership",
            json={"newOwnerId": str(uuid.uuid4()), "reason": "recovery"},
        )
        assert resp.status_code == 403


class TestAdminExecuteRegionOwnershipTransfer:
    def test_system_region_rejected(self):
        from src.services import region_lifecycle_service as rls

        db = MagicMock()
        region = MagicMock()
        region.region_type = RegionType.CENTRAL_NEXUS.value
        region.cleanup_completed_at = None
        db.query.return_value.filter.return_value.with_for_update.return_value.first.return_value = region

        with pytest.raises(AdminRegionTransferOwnershipError) as exc:
            rls.admin_execute_region_ownership_transfer(db, uuid.uuid4(), uuid.uuid4())
        assert exc.value.code == "system_region"

    def test_same_owner_rejected(self):
        from src.services import region_lifecycle_service as rls

        owner_id = uuid.uuid4()
        db = MagicMock()
        region = MagicMock()
        region.id = uuid.uuid4()
        region.region_type = RegionType.PLAYER_OWNED.value
        region.cleanup_completed_at = None
        region.owner_id = owner_id
        new_owner = SimpleNamespace(id=owner_id)

        def _query(model):
            q = MagicMock()
            name = getattr(model, "__name__", str(model))
            if name == "Region":
                q.filter.return_value.with_for_update.return_value.first.return_value = region
            elif name == "User":
                q.filter.return_value.first.return_value = new_owner
            return q

        db.query.side_effect = _query

        with pytest.raises(AdminRegionTransferOwnershipError) as exc:
            rls.admin_execute_region_ownership_transfer(db, region.id, owner_id)
        assert exc.value.code == "same_owner"

    @patch("src.api.routes.admin.admin_execute_region_ownership_transfer")
    def test_success_returns_transfer_payload(self, mock_transfer, transfer_client):
        region = _make_region()
        new_owner_id = uuid.uuid4()
        mock_transfer.return_value = {
            "regionId": str(region.id),
            "regionName": region.name,
            "oldOwnerId": str(region.owner_id),
            "newOwnerId": str(new_owner_id),
        }

        app.dependency_overrides[get_current_user] = _admin_user
        app.dependency_overrides[get_db] = lambda: _make_db(region=region, new_owner=SimpleNamespace(id=new_owner_id))

        resp = transfer_client.post(
            f"{API}/{region.id}/transfer-ownership",
            json={"newOwnerId": str(new_owner_id), "reason": "account recovery"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["regionId"] == str(region.id)
        assert body["newOwnerId"] == str(new_owner_id)
