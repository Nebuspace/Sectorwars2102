"""Unit tests for RBAC Phase B-lead — writers + grant/revoke helpers.

DB-free.  Run with the same env harness as test_rbac_phase_a2.py.
"""

from __future__ import annotations

import inspect
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, call, patch

import pytest
from fastapi import HTTPException

from src.auth.admin_scopes import META_SCOPES, PLAYERS_VIEW, SCOPES_GRANT
from src.api.routes.admin_scopes import grant_scope_to_user, revoke_scope_from_user
from src.auth import admin as auth_admin


class TestCreateDefaultAdminBLead:
    def test_existence_check_is_username_only_not_is_admin_sql(self):
        """Cipher MEDIUM: must not use User.is_admin==True (EXISTS) for existence."""
        src = inspect.getsource(auth_admin.create_default_admin)
        assert "User.username == admin_username" in src
        # Must NOT filter on is_admin in the existence query (boot-loop).
        assert "User.is_admin == True" not in src
        assert "_ensure_meta_scope_grants" in src

    def test_ensure_meta_grants_inserts_missing_only(self):
        db = MagicMock()
        user = SimpleNamespace(id=uuid.uuid4())
        # First two scopes already present; third missing → one insert.
        present = list(META_SCOPES)[:2]
        missing = list(META_SCOPES)[2:]

        def first_side_effect():
            # Called once per scope in META_SCOPES order
            scope_calls = getattr(first_side_effect, "n", 0)
            first_side_effect.n = scope_calls + 1
            scopes_ordered = list(META_SCOPES)
            current = scopes_ordered[scope_calls]
            if current in present:
                return (uuid.uuid4(),)
            return None

        db.query.return_value.filter.return_value.first.side_effect = first_side_effect
        inserted = auth_admin._ensure_meta_scope_grants(db, user)
        assert inserted == len(missing)
        assert db.add.call_count == len(missing)


class TestGrantRevokeHelpers:
    def test_grant_sets_is_admin_and_logs(self):
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = None
        actor = SimpleNamespace(id=uuid.uuid4())
        target = SimpleNamespace(id=uuid.uuid4(), is_admin=False)

        row = grant_scope_to_user(db, actor=actor, target=target, scope=PLAYERS_VIEW)
        assert target.is_admin is True
        assert db.add.call_count >= 2  # grant + action log
        assert row.scope == PLAYERS_VIEW

    def test_grant_noop_when_already_active(self):
        existing = SimpleNamespace(id=uuid.uuid4(), scope=PLAYERS_VIEW)
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = existing
        actor = SimpleNamespace(id=uuid.uuid4())
        target = SimpleNamespace(id=uuid.uuid4(), is_admin=True)

        row = grant_scope_to_user(db, actor=actor, target=target, scope=PLAYERS_VIEW)
        assert row is existing

    def test_grant_unknown_scope_400(self):
        db = MagicMock()
        actor = SimpleNamespace(id=uuid.uuid4())
        target = SimpleNamespace(id=uuid.uuid4(), is_admin=False)
        with pytest.raises(HTTPException) as ei:
            grant_scope_to_user(db, actor=actor, target=target, scope="admin.nope")
        assert ei.value.status_code == 400

    def test_revoke_bulk_clears_is_admin_when_last(self):
        row = SimpleNamespace(revoked_at=None, revoked_by=None)
        db = MagicMock()
        # First query chain: .filter().all() → active rows for this scope
        q = MagicMock()
        q.all.return_value = [row]
        db.query.return_value.filter.return_value = q
        # After revoke, remaining-active count query
        # The helper does a second db.query(...).filter(...).count()
        # Re-bind: make count return 0
        db.query.return_value.filter.return_value.count.return_value = 0
        # But .all() must still work on the first call — use side_effect factory

        def filter_side_effect(*_a, **_k):
            m = MagicMock()
            m.all.return_value = [row]
            m.count.return_value = 0
            return m

        db.query.return_value.filter.side_effect = filter_side_effect

        actor = SimpleNamespace(id=uuid.uuid4())
        target = SimpleNamespace(id=uuid.uuid4(), is_admin=True)
        n = revoke_scope_from_user(db, actor=actor, target=target, scope=PLAYERS_VIEW)
        assert n == 1
        assert row.revoked_at is not None
        assert target.is_admin is False


class TestCreateAdminRouteRetired:
    def test_handler_raises_410(self):
        from src.api.routes import users as users_mod
        src = inspect.getsource(users_mod.create_admin_user)
        assert "410" in src or "HTTP_410_GONE" in src
        assert "retired" in src.lower()
