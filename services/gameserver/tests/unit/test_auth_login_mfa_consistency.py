"""Unit tests for WO-FIX-MFA-BYPASS-LOGIN-ROUTES.

DB-free: no live Postgres. All routes/helper are exercised as direct
coroutine calls (mirrors tests/unit/test_rbac_phase_a2.py's pattern) with
db/authenticate_*/MFAService/create_tokens/_track_player_login mocked or
monkeypatched — this proves the ROUTING/ENFORCEMENT logic, not persistence.

Run with GAMESERVER_CI_DB_FREE=1, ENVIRONMENT=testing, and a DATABASE_URL set
to any syntactically-valid Postgres DSN (never dialed -- DB-free), plus
JWT_SECRET (32+ chars), ADMIN_USERNAME/ADMIN_PASSWORD, and ARIA_ENCRYPTION_KEY
(a valid Fernet key) -- see tests/conftest.py's own startup checks for the
exact validation each of those must satisfy.

    pytest tests/unit/test_auth_login_mfa_consistency.py -v
"""

from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock, AsyncMock, patch

import pytest
from fastapi import HTTPException

from src.api.routes import auth as auth_routes
from src.schemas.auth import LoginForm


def _run(coro):
    return asyncio.run(coro)


def _make_user():
    return SimpleNamespace(id=uuid.uuid4(), username="commander", is_admin=False)


def _fake_request():
    req = MagicMock()
    req.client.host = "127.0.0.1"
    req.headers.get.return_value = "pytest-agent"
    return req


class TestMfaGateAndMintTokensHelper:
    """Exercise the shared helper directly — the single enforcement chokepoint."""

    def test_no_mfa_mints_tokens(self):
        db = MagicMock()
        user = _make_user()
        with patch.object(auth_routes, "MFAService") as mfa_cls, \
             patch.object(auth_routes, "create_tokens", return_value=("access-tok", "refresh-tok")) as create_tok, \
             patch.object(auth_routes, "_track_player_login", new=AsyncMock(return_value={"welcome_back": None, "gc_lapse_notice": None})):
            mfa_cls.return_value.is_mfa_enabled.return_value = False
            result = _run(auth_routes._mfa_gate_and_mint_tokens(db, user, None, _fake_request()))

        assert result["access_token"] == "access-tok"
        assert result["refresh_token"] == "refresh-tok"
        assert result["requires_mfa"] is False
        assert result["mfa_enabled"] is False
        create_tok.assert_called_once()

    def test_mfa_enabled_no_code_returns_requires_mfa_no_tokens(self):
        db = MagicMock()
        user = _make_user()
        with patch.object(auth_routes, "MFAService") as mfa_cls, \
             patch.object(auth_routes, "create_tokens") as create_tok:
            mfa_cls.return_value.is_mfa_enabled.return_value = True
            result = _run(auth_routes._mfa_gate_and_mint_tokens(db, user, None, _fake_request()))

        assert result["requires_mfa"] is True
        assert result["mfa_enabled"] is True
        assert result["access_token"] == ""
        assert result["refresh_token"] == ""
        create_tok.assert_not_called()

    def test_mfa_enabled_invalid_code_raises_401_no_tokens(self):
        db = MagicMock()
        user = _make_user()
        with patch.object(auth_routes, "MFAService") as mfa_cls, \
             patch.object(auth_routes, "create_tokens") as create_tok:
            mfa_cls.return_value.is_mfa_enabled.return_value = True
            mfa_cls.return_value.verify_code.return_value = False
            with pytest.raises(HTTPException) as exc_info:
                _run(auth_routes._mfa_gate_and_mint_tokens(db, user, "000000", _fake_request()))

        assert exc_info.value.status_code == 401
        assert exc_info.value.detail == "Invalid MFA code"
        create_tok.assert_not_called()

    def test_mfa_enabled_valid_code_mints_tokens(self):
        db = MagicMock()
        user = _make_user()
        with patch.object(auth_routes, "MFAService") as mfa_cls, \
             patch.object(auth_routes, "create_tokens", return_value=("access-tok", "refresh-tok")) as create_tok, \
             patch.object(auth_routes, "_track_player_login", new=AsyncMock(return_value={"welcome_back": None, "gc_lapse_notice": None})):
            mfa_cls.return_value.is_mfa_enabled.return_value = True
            mfa_cls.return_value.verify_code.return_value = True
            result = _run(auth_routes._mfa_gate_and_mint_tokens(db, user, "123456", _fake_request()))

        assert result["requires_mfa"] is False
        assert result["mfa_enabled"] is True
        assert result["access_token"] == "access-tok"
        create_tok.assert_called_once()


class TestLoginJsonRouteMfaEnforcement:
    """POST /auth/login/json — was completely unchecked before this WO."""

    def test_mfa_enabled_account_rejected_without_code(self):
        db = MagicMock()
        user = _make_user()
        with patch.object(auth_routes, "authenticate_admin", return_value=user), \
             patch.object(auth_routes, "MFAService") as mfa_cls, \
             patch.object(auth_routes, "create_tokens") as create_tok:
            mfa_cls.return_value.is_mfa_enabled.return_value = True
            result = _run(auth_routes.login_json(
                LoginForm(username="commander", password="pw"),
                _fake_request(), db,
            ))

        assert result["requires_mfa"] is True
        assert result["access_token"] == ""
        create_tok.assert_not_called()

    def test_mfa_enabled_account_accepted_with_valid_code(self):
        db = MagicMock()
        user = _make_user()
        with patch.object(auth_routes, "authenticate_admin", return_value=user), \
             patch.object(auth_routes, "MFAService") as mfa_cls, \
             patch.object(auth_routes, "create_tokens", return_value=("access-tok", "refresh-tok")), \
             patch.object(auth_routes, "_track_player_login", new=AsyncMock(return_value={"welcome_back": None, "gc_lapse_notice": None})):
            mfa_cls.return_value.is_mfa_enabled.return_value = True
            mfa_cls.return_value.verify_code.return_value = True
            result = _run(auth_routes.login_json(
                LoginForm(username="commander", password="pw", mfa_code="123456"),
                _fake_request(), db,
            ))

        assert result["access_token"] == "access-tok"
        assert result["requires_mfa"] is False

    def test_mfa_enabled_account_rejected_with_invalid_code(self):
        db = MagicMock()
        user = _make_user()
        with patch.object(auth_routes, "authenticate_admin", return_value=user), \
             patch.object(auth_routes, "MFAService") as mfa_cls:
            mfa_cls.return_value.is_mfa_enabled.return_value = True
            mfa_cls.return_value.verify_code.return_value = False
            with pytest.raises(HTTPException) as exc_info:
                _run(auth_routes.login_json(
                    LoginForm(username="commander", password="pw", mfa_code="000000"),
                    _fake_request(), db,
                ))
        assert exc_info.value.status_code == 401

    def test_non_mfa_account_logs_in_unchanged(self):
        """Regression: the common case (no MFA) must be byte-identical to before."""
        db = MagicMock()
        user = _make_user()
        with patch.object(auth_routes, "authenticate_admin", return_value=user), \
             patch.object(auth_routes, "MFAService") as mfa_cls, \
             patch.object(auth_routes, "create_tokens", return_value=("access-tok", "refresh-tok")), \
             patch.object(auth_routes, "_track_player_login", new=AsyncMock(return_value={"welcome_back": None, "gc_lapse_notice": None})):
            mfa_cls.return_value.is_mfa_enabled.return_value = False
            result = _run(auth_routes.login_json(
                LoginForm(username="commander", password="pw"),
                _fake_request(), db,
            ))

        assert result["access_token"] == "access-tok"
        assert result["refresh_token"] == "refresh-tok"
        assert result["user_id"] == str(user.id)

    def test_wrong_password_still_401s_before_mfa_check(self):
        db = MagicMock()
        with patch.object(auth_routes, "authenticate_admin", return_value=None), \
             patch("src.services.user_service.authenticate_player", return_value=None), \
             patch.object(auth_routes, "MFAService") as mfa_cls:
            with pytest.raises(HTTPException) as exc_info:
                _run(auth_routes.login_json(
                    LoginForm(username="commander", password="wrong"),
                    _fake_request(), db,
                ))
        assert exc_info.value.status_code == 401
        mfa_cls.assert_not_called()


class TestLoginFormRouteMfaEnforcement:
    """POST /auth/login (OAuth2PasswordRequestForm) — was completely unchecked before this WO."""

    def _form_data(self, username="commander", password="pw"):
        return SimpleNamespace(username=username, password=password)

    def test_mfa_enabled_account_rejected_without_code(self):
        db = MagicMock()
        user = _make_user()
        with patch.object(auth_routes, "authenticate_admin", return_value=user), \
             patch.object(auth_routes, "MFAService") as mfa_cls, \
             patch.object(auth_routes, "create_tokens") as create_tok:
            mfa_cls.return_value.is_mfa_enabled.return_value = True
            result = _run(auth_routes.login(
                _fake_request(), self._form_data(), None, db,
            ))

        assert result["requires_mfa"] is True
        create_tok.assert_not_called()

    def test_mfa_enabled_account_accepted_with_valid_code(self):
        db = MagicMock()
        user = _make_user()
        with patch.object(auth_routes, "authenticate_admin", return_value=user), \
             patch.object(auth_routes, "MFAService") as mfa_cls, \
             patch.object(auth_routes, "create_tokens", return_value=("access-tok", "refresh-tok")), \
             patch.object(auth_routes, "_track_player_login", new=AsyncMock(return_value={"welcome_back": None, "gc_lapse_notice": None})):
            mfa_cls.return_value.is_mfa_enabled.return_value = True
            mfa_cls.return_value.verify_code.return_value = True
            result = _run(auth_routes.login(
                _fake_request(), self._form_data(), "123456", db,
            ))

        assert result["access_token"] == "access-tok"

    def test_non_mfa_account_logs_in_unchanged(self):
        db = MagicMock()
        user = _make_user()
        with patch.object(auth_routes, "authenticate_admin", return_value=user), \
             patch.object(auth_routes, "MFAService") as mfa_cls, \
             patch.object(auth_routes, "create_tokens", return_value=("access-tok", "refresh-tok")), \
             patch.object(auth_routes, "_track_player_login", new=AsyncMock(return_value={"welcome_back": None, "gc_lapse_notice": None})):
            mfa_cls.return_value.is_mfa_enabled.return_value = False
            result = _run(auth_routes.login(
                _fake_request(), self._form_data(), None, db,
            ))

        assert result["access_token"] == "access-tok"
        assert result["requires_mfa"] is False


class TestLoginDirectUnchanged:
    """Regression: login_direct's own behavior must be byte-identical post-refactor."""

    def test_mfa_required_response_shape_unchanged(self):
        db = MagicMock()
        user = _make_user()
        with patch.object(auth_routes, "authenticate_admin", return_value=user), \
             patch.object(auth_routes, "MFAService") as mfa_cls:
            mfa_cls.return_value.is_mfa_enabled.return_value = True
            result = _run(auth_routes.login_direct(
                LoginForm(username="commander", password="pw"),
                _fake_request(), db,
            ))
        assert result == {
            "access_token": "",
            "refresh_token": "",
            "token_type": "bearer",
            "user_id": str(user.id),
            "requires_mfa": True,
            "mfa_enabled": True,
        }

    def test_wrong_credentials_401_detail_unchanged(self):
        """login_direct's 401 has NO WWW-Authenticate header, unlike /login —
        this must remain true after extracting the shared helper."""
        db = MagicMock()
        with patch.object(auth_routes, "authenticate_admin", return_value=None), \
             patch("src.services.user_service.authenticate_player", return_value=None):
            with pytest.raises(HTTPException) as exc_info:
                _run(auth_routes.login_direct(
                    LoginForm(username="commander", password="wrong"),
                    _fake_request(), db,
                ))
        assert exc_info.value.status_code == 401
        assert exc_info.value.detail == "Incorrect username or password"
        assert not exc_info.value.headers


class TestPlayerLoginRoutesWired:
    """player/login + player/login/json (dead beyond e2e utils) are wired
    through the same helper for consistency (point 4 of the WO)."""

    def test_player_login_json_mfa_enabled_requires_code(self):
        db = MagicMock()
        user = _make_user()
        with patch.object(auth_routes, "authenticate_player", return_value=user), \
             patch.object(auth_routes, "MFAService") as mfa_cls:
            mfa_cls.return_value.is_mfa_enabled.return_value = True
            result = _run(auth_routes.player_login_json(
                LoginForm(username="commander", password="pw"),
                _fake_request(), db,
            ))
        assert result["requires_mfa"] is True

    def test_player_login_form_mfa_enabled_requires_code(self):
        db = MagicMock()
        user = _make_user()
        form_data = SimpleNamespace(username="commander", password="pw")
        with patch.object(auth_routes, "authenticate_player", return_value=user), \
             patch.object(auth_routes, "MFAService") as mfa_cls:
            mfa_cls.return_value.is_mfa_enabled.return_value = True
            result = _run(auth_routes.player_login(
                _fake_request(), form_data, None, db,
            ))
        assert result["requires_mfa"] is True
