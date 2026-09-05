"""Admin region terminate routes — LEG-DEC-103 / LEG-3205 (DB-free)."""

from __future__ import annotations

import inspect
import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from src.auth.admin_scopes import REGIONS_TERMINATE
from src.auth.dependencies import get_current_user
from src.core.database import get_db
from src.main import app
from src.models.region import RegionType, RegionStatus
from src.services.region_lifecycle_service import AdminRegionTerminationError

API = "/api/v1/admin/regions"


def _admin_user():
    return SimpleNamespace(id=uuid.uuid4(), username="regadmin", is_admin=True)


def _player_user():
    return SimpleNamespace(id=uuid.uuid4(), username="player", is_admin=False)


def _make_region(name: str = "TestRegion-Alpha"):
    region = MagicMock()
    region.id = uuid.uuid4()
    region.name = name
    region.display_name = "Test Region Alpha"
    region.region_type = RegionType.PLAYER_OWNED.value
    region.status = RegionStatus.ACTIVE.value
    region.cleanup_completed_at = None
    return region


def _make_db(*, region=None, grant=True):
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
            q.filter.return_value.count.return_value = 0
        else:
            q.filter.return_value.count.return_value = 0
            q.filter.return_value.distinct.return_value.count.return_value = 0
            q.filter.return_value.all.return_value = []
            q.join.return_value.filter.return_value.filter.return_value.count.return_value = 0
            q.join.return_value.filter.return_value.filter.return_value.distinct.return_value.count.return_value = 0
        return q

    db.query.side_effect = _query
    return db


@pytest.fixture
def terminate_client():
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
    def test_routes_require_regions_terminate_scope(self):
        from src.api.routes import admin as admin_routes

        preview_src = inspect.getsource(admin_routes.get_region_terminate_preview)
        post_src = inspect.getsource(admin_routes.post_region_terminate)
        assert "require_scope(REGIONS_TERMINATE)" in preview_src
        assert "require_scope(REGIONS_TERMINATE)" in post_src
        assert "admin_action_attempt" in post_src
        assert "region_terminate" in post_src


class TestPostRegionTerminateAuthz:
    def test_unauthenticated_returns_401(self, terminate_client):
        rid = uuid.uuid4()
        resp = terminate_client.post(
            f"{API}/{rid}/terminate",
            json={"reason": "policy violation"},
            headers={"X-Confirm-Region-Name": "Any"},
        )
        assert resp.status_code == 401

    def test_non_admin_returns_403(self, terminate_client):
        app.dependency_overrides[get_current_user] = _player_user
        rid = uuid.uuid4()
        resp = terminate_client.post(
            f"{API}/{rid}/terminate",
            json={"reason": "policy violation"},
            headers={"X-Confirm-Region-Name": "Any"},
        )
        assert resp.status_code == 403

    def test_missing_scope_grant_returns_403(self, terminate_client):
        app.dependency_overrides[get_current_user] = _admin_user
        app.dependency_overrides[get_db] = lambda: _make_db(region=_make_region(), grant=False)
        rid = uuid.uuid4()
        resp = terminate_client.post(
            f"{API}/{rid}/terminate",
            json={"reason": "policy violation"},
            headers={"X-Confirm-Region-Name": "TestRegion-Alpha"},
        )
        assert resp.status_code == 403


class TestPostRegionTerminateConfirmation:
    def test_missing_confirm_header_returns_422(self, terminate_client):
        region = _make_region()
        app.dependency_overrides[get_current_user] = _admin_user
        app.dependency_overrides[get_db] = lambda: _make_db(region=region)
        resp = terminate_client.post(
            f"{API}/{region.id}/terminate",
            json={"reason": "nonpayment"},
        )
        assert resp.status_code == 422
        body = resp.json()
        detail = body.get("detail", body)
        assert "X-Confirm-Region-Name" in str(detail)

    def test_wrong_confirm_name_returns_422(self, terminate_client):
        region = _make_region()
        app.dependency_overrides[get_current_user] = _admin_user
        app.dependency_overrides[get_db] = lambda: _make_db(region=region)
        resp = terminate_client.post(
            f"{API}/{region.id}/terminate",
            json={"reason": "nonpayment"},
            headers={"X-Confirm-Region-Name": "Wrong-Name"},
        )
        assert resp.status_code == 422


class TestAdminExecuteRegionTermination:
    def test_system_region_rejected(self):
        from src.services import region_lifecycle_service as rls

        db = MagicMock()
        region = MagicMock()
        region.region_type = RegionType.CENTRAL_NEXUS.value
        region.cleanup_completed_at = None
        db.query.return_value.filter.return_value.with_for_update.return_value.first.return_value = region

        with pytest.raises(AdminRegionTerminationError) as exc:
            rls.admin_execute_region_termination(db, uuid.uuid4())
        assert exc.value.code == "system_region"

    @patch("src.services.region_lifecycle_service.process_planet_termination")
    def test_cascade_failure_propagates_for_rollback(self, mock_planet):
        from src.services import region_lifecycle_service as rls

        mock_planet.side_effect = RuntimeError("cascade blew up")
        db = MagicMock()
        region = MagicMock()
        region.id = uuid.uuid4()
        region.region_type = RegionType.PLAYER_OWNED.value
        region.cleanup_completed_at = None
        planet = MagicMock()
        db.query.return_value.filter.return_value.with_for_update.return_value.first.return_value = region
        db.query.return_value.filter.return_value.all.return_value = [planet]

        with pytest.raises(RuntimeError, match="cascade blew up"):
            rls.admin_execute_region_termination(db, region.id)
