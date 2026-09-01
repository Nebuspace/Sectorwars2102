"""Manual GC grant/revoke admin routes — LEG-3611 (DB-free + service smoke)."""

from __future__ import annotations

import inspect
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError

from src.api.routes import admin_subscriptions as routes_mod
from src.api.routes.admin_subscriptions import (
    GcMutationRequest,
    grant_galactic_citizen,
    revoke_galactic_citizen,
)

from src.auth.dependencies import get_current_user
from src.core.database import get_db
from src.main import app
from src.services.galactic_citizen_admin_service import (
    manual_grant_galactic_citizen,
    manual_revoke_galactic_citizen,
)

API_GRANT = "/api/v1/admin/players/{player_id}/galactic-citizen/grant"
API_REVOKE = "/api/v1/admin/players/{player_id}/galactic-citizen/revoke"


def _admin_user():
    return SimpleNamespace(id=uuid.uuid4(), username="subadmin", is_admin=True)


def _player_user():
    return SimpleNamespace(id=uuid.uuid4(), username="player", is_admin=False)


def _make_player(*, is_gc: bool = False):
    player = MagicMock()
    player.id = uuid.uuid4()
    player.user_id = uuid.uuid4()
    player.is_galactic_citizen = is_gc
    player.gc_lapsed_at = None
    player.gc_relocation_used_at = None
    player.user = None
    return player


def _make_user(*, tier=None, status=None):
    user = MagicMock()
    user.id = uuid.uuid4()
    user.subscription_tier = tier
    user.subscription_status = status
    user.paypal_subscription_id = None
    user.payment_failure_count = 0
    user.subscription_started_at = None
    return user


def _make_db(*, player=None, user=None, grant=True):
    db = MagicMock()

    def _query(model):
        q = MagicMock()
        name = getattr(model, "__name__", str(model))
        model_s = str(model)
        if "AdminScopeGrant" in model_s or name == "AdminScopeGrant":
            q.filter.return_value.first.return_value = (uuid.uuid4(),) if grant else None
        elif "Player" in model_s or name == "Player":
            q.filter.return_value.first.return_value = player
        elif "User" in model_s or name == "User":
            q.filter.return_value.first.return_value = user
        else:
            q.filter.return_value.first.return_value = None
        return q

    db.query.side_effect = _query
    return db


@pytest.fixture
def gc_client():
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
    def test_routes_require_subscriptions_modify_scope(self):
        from src.api.routes import admin_subscriptions as routes

        grant_src = inspect.getsource(routes.grant_galactic_citizen)
        revoke_src = inspect.getsource(routes.revoke_galactic_citizen)
        assert "require_scope(SUBSCRIPTIONS_MODIFY)" in grant_src
        assert "require_scope(SUBSCRIPTIONS_MODIFY)" in revoke_src
        assert "admin_action_attempt" in grant_src
        assert "admin_action_attempt" in revoke_src
        assert "galactic_citizen_grant" in grant_src
        assert "galactic_citizen_revoke" in revoke_src

    def test_request_requires_nonempty_reason(self):
        from src.api.routes.admin_subscriptions import GcMutationRequest

        with pytest.raises(ValidationError):
            GcMutationRequest(reason="")


class TestGrantRevokeAuthz:
    def test_grant_unauthenticated_returns_401(self, gc_client):
        pid = uuid.uuid4()
        resp = gc_client.post(
            API_GRANT.format(player_id=pid),
            json={"reason": "comp for outage"},
        )
        assert resp.status_code == 401

    def test_grant_non_admin_returns_403(self, gc_client):
        app.dependency_overrides[get_current_user] = _player_user
        pid = uuid.uuid4()
        resp = gc_client.post(
            API_GRANT.format(player_id=pid),
            json={"reason": "comp for outage"},
        )
        assert resp.status_code == 403

    def test_grant_missing_scope_returns_403(self, gc_client):
        player = _make_player(is_gc=False)
        user = _make_user()
        player.user_id = user.id
        app.dependency_overrides[get_current_user] = _admin_user
        app.dependency_overrides[get_db] = lambda: _make_db(
            player=player, user=user, grant=False
        )
        resp = gc_client.post(
            API_GRANT.format(player_id=player.id),
            json={"reason": "comp for outage"},
        )
        assert resp.status_code == 403

    def test_revoke_missing_scope_returns_403(self, gc_client):
        player = _make_player(is_gc=True)
        user = _make_user(tier="galactic_citizen", status="active")
        player.user_id = user.id
        app.dependency_overrides[get_current_user] = _admin_user
        app.dependency_overrides[get_db] = lambda: _make_db(
            player=player, user=user, grant=False
        )
        resp = gc_client.post(
            API_REVOKE.format(player_id=player.id),
            json={"reason": "chargeback"},
        )
        assert resp.status_code == 403


class TestGalacticCitizenAdminService:
    def test_grant_sets_flags_and_is_idempotent(self):
        db = MagicMock()
        player = _make_player(is_gc=False)
        user = _make_user()
        player.user_id = user.id

        def _query(model):
            q = MagicMock()
            name = getattr(model, "__name__", str(model))
            if name == "Player":
                q.filter.return_value.first.return_value = player
            elif name == "User":
                q.filter.return_value.first.return_value = user
            return q

        db.query.side_effect = _query

        first = manual_grant_galactic_citizen(db, player, reason="comp")
        assert first.changed is True
        assert player.is_galactic_citizen is True
        assert user.subscription_tier == "galactic_citizen"
        assert user.subscription_status == "manual_grant"
        assert player.gc_lapsed_at is None

        second = manual_grant_galactic_citizen(db, player, reason="comp again")
        assert second.changed is False
        assert second.already_in_target_state is True

    def test_revoke_clears_flags_and_is_idempotent(self):
        db = MagicMock()
        player = _make_player(is_gc=True)
        user = _make_user(tier="galactic_citizen", status="active")
        player.user_id = user.id

        def _query(model):
            q = MagicMock()
            name = getattr(model, "__name__", str(model))
            if name == "Player":
                q.filter.return_value.first.return_value = player
            elif name == "User":
                q.filter.return_value.first.return_value = user
            return q

        db.query.side_effect = _query

        first = manual_revoke_galactic_citizen(db, player, reason="fraud")
        assert first.changed is True
        assert player.is_galactic_citizen is False
        assert user.subscription_tier is None
        assert user.subscription_status == "manual_revoke"

        second = manual_revoke_galactic_citizen(db, player, reason="fraud again")
        assert second.changed is False
        assert second.already_in_target_state is True

    def test_grant_missing_user_raises_value_error(self):
        db = MagicMock()
        player = _make_player(is_gc=False)

        def _query(model):
            q = MagicMock()
            name = getattr(model, "__name__", str(model))
            if name == "Player":
                q.filter.return_value.first.return_value = player
            elif name == "User":
                q.filter.return_value.first.return_value = None
            return q

        db.query.side_effect = _query
        with pytest.raises(ValueError, match="Associated user not found"):
            manual_grant_galactic_citizen(db, player, reason="comp")


class TestE5WrappedRoutesIncludeGC:
    def test_gc_routes_in_e5_wrapped_set(self):
        from src.services.admin_action_attempt import E5_WRAPPED_ROUTES

        assert "POST /admin/players/{player_id}/galactic-citizen/grant" in E5_WRAPPED_ROUTES
        assert "POST /admin/players/{player_id}/galactic-citizen/revoke" in E5_WRAPPED_ROUTES


class TestGcMutationOpaque500:
    """LEG-3619 — grant/revoke HTTP 500 catches must not echo Exception text."""

    @pytest.mark.asyncio
    async def test_grant_unexpected_is_opaque_500(self):
        secret = "secret-gc-grant-should-not-leak"
        player = _make_player(is_gc=False)
        body = GcMutationRequest(reason="comp for outage")
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = player

        with patch.object(
            routes_mod, "manual_grant_galactic_citizen", side_effect=RuntimeError(secret)
        ):
            with pytest.raises(HTTPException) as excinfo:
                await grant_galactic_citizen(
                    player_id=str(player.id),
                    body=body,
                    actor=SimpleNamespace(id=uuid.uuid4()),
                    db=db,
                )

        exc = excinfo.value
        assert exc.status_code == 500
        assert exc.detail == "Failed to grant galactic citizenship"
        assert secret not in str(exc.detail)

    @pytest.mark.asyncio
    async def test_revoke_unexpected_is_opaque_500(self):
        secret = "secret-gc-revoke-should-not-leak"
        player = _make_player(is_gc=True)
        body = GcMutationRequest(reason="chargeback")
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = player

        with patch.object(
            routes_mod, "manual_revoke_galactic_citizen", side_effect=RuntimeError(secret)
        ):
            with pytest.raises(HTTPException) as excinfo:
                await revoke_galactic_citizen(
                    player_id=str(player.id),
                    body=body,
                    actor=SimpleNamespace(id=uuid.uuid4()),
                    db=db,
                )

        exc = excinfo.value
        assert exc.status_code == 500
        assert exc.detail == "Failed to revoke galactic citizenship"
        assert secret not in str(exc.detail)

    def test_admin_subscriptions_http500_catches_have_no_detail_str_e(self):
        """LEG-3619 — static pin: both HTTP 500 catch paths stay opaque."""
        src = Path(routes_mod.__file__).read_text(encoding="utf-8")
        for stable in (
            'detail="Failed to grant galactic citizenship"',
            'detail="Failed to revoke galactic citizenship"',
        ):
            assert stable in src
        assert "Failed to grant galactic citizenship: {str(e)}" not in src
        assert "Failed to revoke galactic citizenship: {str(e)}" not in src
