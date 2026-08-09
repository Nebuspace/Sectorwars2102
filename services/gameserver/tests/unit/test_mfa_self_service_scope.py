"""Unit tests for WO-ESCALATE-MFA-SELF-SERVICE-GATED-BEHIND-ADMIN-SCOPE.

DB-free: no live Postgres. Routes are exercised as direct coroutine calls
(mirrors tests/unit/test_auth_login_mfa_consistency.py) with db/MFAService
mocked, plus signature/dependency introspection of the mounted router.

Run with GAMESERVER_CI_DB_FREE=1, ENVIRONMENT=testing, DATABASE_URL,
JWT_SECRET, ADMIN_USERNAME/ADMIN_PASSWORD, ARIA_ENCRYPTION_KEY -- see
tests/conftest.py.

    pytest tests/unit/test_mfa_self_service_scope.py -v
"""

from __future__ import annotations

import asyncio
import inspect
import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from src.api.routes import mfa as mfa_routes
from src.auth.dependencies import get_current_user


def _run(coro):
    return asyncio.run(coro)


def _make_user(is_admin=False):
    return SimpleNamespace(
        id=uuid.uuid4(), username="commander", is_admin=is_admin, mfa_secret=None
    )


def _fake_request():
    req = MagicMock()
    req.client.host = "127.0.0.1"
    req.headers.get.return_value = "pytest-agent"
    return req


ROUTE_FUNCS = [
    mfa_routes.generate_mfa_secret,
    mfa_routes.verify_mfa_setup,
    mfa_routes.check_mfa_code,
    mfa_routes.get_mfa_status,
    mfa_routes.disable_mfa,
    mfa_routes.get_backup_codes,
    mfa_routes.regenerate_backup_codes,
    mfa_routes.get_mfa_attempts,
]


class TestNoAdminScopeGate:
    """(a) A plain authenticated player can reach every MFA self-service route."""

    @pytest.mark.parametrize("fn", ROUTE_FUNCS, ids=lambda f: f.__name__)
    def test_current_user_dep_is_plain_authentication_not_a_scope_gate(self, fn):
        dep = inspect.signature(fn).parameters["current_user"].default
        assert dep.dependency is get_current_user, (
            f"{fn.__name__} must gate on plain authentication, not an admin scope"
        )

    def test_module_no_longer_imports_admin_scope(self):
        src = inspect.getsource(mfa_routes)
        assert "require_scope" not in src
        assert "PLAYERS_VIEW" not in src

    def test_non_admin_player_can_generate_own_secret(self):
        user = _make_user(is_admin=False)
        with patch.object(mfa_routes, "MFAService") as svc:
            svc.return_value.generate_secret.return_value = (
                "stub-value", "otpauth://x", "data:image/png;base64,x",
            )
            resp = _run(mfa_routes.generate_mfa_secret(current_user=user, db=MagicMock()))
        assert resp.secret == "stub-value"  # noqa: S105 - mock return, not a credential
        svc.return_value.generate_secret.assert_called_once_with(str(user.id))

    def test_non_admin_player_can_disable_own_mfa(self):
        user = _make_user(is_admin=False)
        with patch.object(mfa_routes, "MFAService") as svc:
            svc.return_value.disable_mfa.return_value = True
            _run(mfa_routes.disable_mfa(current_user=user, db=MagicMock()))
        svc.return_value.disable_mfa.assert_called_once_with(str(user.id))


class TestCrossPlayerAccessDenied:
    """(b) No route can be pointed at another player's MFA record.

    Enforcement is structural: no route accepts a target-user parameter, and
    every route passes ``current_user.id`` -- the JWT subject -- to MFAService.
    A caller therefore has no input channel through which to name a victim.
    """

    @pytest.mark.parametrize("fn", ROUTE_FUNCS, ids=lambda f: f.__name__)
    def test_route_accepts_no_target_user_parameter(self, fn):
        params = set(inspect.signature(fn).parameters)
        forbidden = {"user_id", "player_id", "target_user_id", "target_player_id", "username"}
        assert not (params & forbidden), (
            f"{fn.__name__} exposes a target-user parameter -- cross-player MFA access"
        )

    @pytest.mark.parametrize("fn", ROUTE_FUNCS, ids=lambda f: f.__name__)
    def test_no_request_body_model_carries_a_target_user(self, fn):
        for p in inspect.signature(fn).parameters.values():
            ann = p.annotation
            fields = getattr(ann, "model_fields", None)
            if not fields:
                continue
            forbidden = {"user_id", "player_id", "target_user_id", "username"}
            assert not (set(fields) & forbidden), (
                f"{fn.__name__} body model {ann.__name__} carries a target-user field"
            )

    def test_victim_id_in_body_cannot_redirect_the_operation(self):
        """An attacker smuggling a victim id still only touches their own record."""
        attacker = _make_user(is_admin=False)
        victim_id = uuid.uuid4()
        req = mfa_routes.MFAVerifyRequest(code="123456")
        # Smuggle a victim id onto the request object; the route must ignore it.
        object.__setattr__(req, "__dict__", {**req.__dict__, "user_id": str(victim_id)})

        with patch.object(mfa_routes, "MFAService") as svc:
            svc.return_value.verify_setup.return_value = {"success": True, "message": "ok", "backup_codes": []}
            _run(mfa_routes.verify_mfa_setup(request=req, current_user=attacker, db=MagicMock()))

        called_id = svc.return_value.verify_setup.call_args[0][0]
        assert called_id == str(attacker.id)
        assert called_id != str(victim_id)

    @pytest.mark.parametrize(
        "fn,kwargs,svc_method",
        [
            (mfa_routes.generate_mfa_secret, {}, "generate_secret"),
            (mfa_routes.get_mfa_status, {}, "is_mfa_enabled"),
            (mfa_routes.disable_mfa, {}, "disable_mfa"),
            (mfa_routes.get_backup_codes, {}, "get_backup_codes"),
            (mfa_routes.regenerate_backup_codes, {}, "regenerate_backup_codes"),
        ],
        ids=lambda v: v if isinstance(v, str) else "",
    )
    def test_every_route_acts_on_the_jwt_subject(self, fn, kwargs, svc_method):
        user = _make_user()
        with patch.object(mfa_routes, "MFAService") as svc:
            svc.return_value.generate_secret.return_value = ("S", "otpauth://x", "data:image/png;base64,x")
            svc.return_value.get_backup_codes.return_value = ["code"]
            svc.return_value.regenerate_backup_codes.return_value = ["code"]
            svc.return_value.is_mfa_enabled.return_value = True
            svc.return_value.disable_mfa.return_value = True
            _run(fn(current_user=user, db=MagicMock(), **kwargs))
        assert getattr(svc.return_value, svc_method).call_args[0][0] == str(user.id)


class TestAdminUnchanged:
    """(c) An admin (PLAYERS_VIEW holder) manages MFA exactly as before.

    These endpoints have always been self-targeting: PLAYERS_VIEW gated the
    CALLER, it never granted management of another player's record. An admin
    therefore retains precisely the capability they had -- managing their own
    MFA -- and loses nothing.
    """

    def test_admin_can_still_generate_and_disable_own_mfa(self):
        admin = _make_user(is_admin=True)
        with patch.object(mfa_routes, "MFAService") as svc:
            svc.return_value.generate_secret.return_value = ("S", "u", "q")
            svc.return_value.disable_mfa.return_value = True
            _run(mfa_routes.generate_mfa_secret(current_user=admin, db=MagicMock()))
            _run(mfa_routes.disable_mfa(current_user=admin, db=MagicMock()))
        svc.return_value.generate_secret.assert_called_once_with(str(admin.id))
        svc.return_value.disable_mfa.assert_called_once_with(str(admin.id))

    def test_admin_can_still_read_own_attempts(self):
        admin = _make_user(is_admin=True)
        with patch.object(mfa_routes, "MFAService") as svc:
            svc.return_value.get_recent_attempts.return_value = [{"success": True}]
            resp = _run(mfa_routes.get_mfa_attempts(hours=24, current_user=admin, db=MagicMock()))
        assert resp.total == 1
        svc.return_value.get_recent_attempts.assert_called_once_with(str(admin.id), 24)


class TestVerificationNotWeakened:
    """The TOTP/backup-code verification path itself is untouched."""

    def test_check_route_still_delegates_to_verify_code_with_client_metadata(self):
        user = _make_user()
        with patch.object(mfa_routes, "MFAService") as svc:
            svc.return_value.verify_code.return_value = False
            resp = _run(mfa_routes.check_mfa_code(
                request=mfa_routes.MFACheckRequest(code="000000"),
                request_obj=_fake_request(), current_user=user, db=MagicMock(),
            ))
        assert resp.valid is False
        args, kwargs = svc.return_value.verify_code.call_args
        assert args[0] == str(user.id)
        assert args[1] == "000000"
        assert kwargs["ip_address"] == "127.0.0.1"

    def test_invalid_setup_code_is_not_reported_as_success(self):
        user = _make_user()
        with patch.object(mfa_routes, "MFAService") as svc:
            svc.return_value.verify_setup.return_value = {"success": False, "message": "Invalid code"}
            resp = _run(mfa_routes.verify_mfa_setup(
                request=mfa_routes.MFAVerifyRequest(code="999999"), current_user=user, db=MagicMock(),
            ))
        assert resp.success is False
        assert resp.backup_codes is None
