"""Unit tests for RBAC Phase B-lead — writers + grant/revoke helpers.

DB-free.  Run with the same env harness as test_rbac_phase_a2.py.
"""

from __future__ import annotations

import inspect
import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock, call

import pytest
from fastapi import HTTPException

from src.auth.admin_scopes import META_SCOPES, PLAYERS_VIEW, SCOPES_GRANT
from src.api.routes.admin_scopes import grant_scope_to_user, revoke_scope_from_user
from src.auth import admin as auth_admin


def _user(admin: bool = True):
    return SimpleNamespace(id=uuid.uuid4(), is_admin=admin)


def _lock_chain(db: MagicMock, locked_user):
    """Wire ``db.query(User).filter().with_for_update().one()`` → locked_user."""
    user_q = MagicMock()
    user_q.filter.return_value.with_for_update.return_value.one.return_value = locked_user
    return user_q


class TestCreateDefaultAdminBLead:
    def test_existence_check_is_username_only_not_is_admin_sql(self):
        """Cipher MEDIUM: must not use User.is_admin==True (EXISTS) for existence."""
        src = inspect.getsource(auth_admin.create_default_admin)
        assert "User.username == admin_username" in src
        assert "User.is_admin == True" not in src
        assert "_ensure_meta_scope_grants" in src

    def test_ensure_meta_grants_inserts_missing_only(self):
        db = MagicMock()
        user = SimpleNamespace(id=uuid.uuid4())
        present = list(META_SCOPES)[:2]

        def first_side_effect():
            scope_calls = getattr(first_side_effect, "n", 0)
            first_side_effect.n = scope_calls + 1
            current = list(META_SCOPES)[scope_calls]
            if current in present:
                return (uuid.uuid4(),)
            return None

        db.query.return_value.filter.return_value.first.side_effect = first_side_effect
        inserted = auth_admin._ensure_meta_scope_grants(db, user)
        assert inserted == 1
        assert db.add.call_count == 1


class TestGrantRevokeHelpers:
    def test_grant_sets_is_admin_and_logs(self):
        target = _user(admin=False)
        locked = _user(admin=False)
        locked.id = target.id
        db = MagicMock()

        # query(User) → lock; query(AdminScopeGrant) → no existing
        grant_q = MagicMock()
        grant_q.filter.return_value.with_for_update.return_value.first.return_value = None
        nested = MagicMock()
        nested.__enter__ = MagicMock(return_value=None)
        nested.__exit__ = MagicMock(return_value=False)
        db.begin_nested.return_value = nested

        def query_side(model):
            name = getattr(model, "__name__", str(model))
            if name == "User":
                return _lock_chain(db, locked)
            return grant_q

        db.query.side_effect = query_side
        actor = _user()

        row = grant_scope_to_user(db, actor=actor, target=target, scope=PLAYERS_VIEW)
        assert locked.is_admin is True
        assert target.is_admin is True
        assert row.scope == PLAYERS_VIEW
        db.flush.assert_called()

    def test_grant_noop_when_already_active(self):
        existing = SimpleNamespace(id=uuid.uuid4(), scope=PLAYERS_VIEW)
        target = _user(admin=True)
        locked = _user(admin=True)
        locked.id = target.id
        db = MagicMock()
        grant_q = MagicMock()
        grant_q.filter.return_value.with_for_update.return_value.first.return_value = existing

        def query_side(model):
            name = getattr(model, "__name__", str(model))
            if name == "User":
                return _lock_chain(db, locked)
            return grant_q

        db.query.side_effect = query_side
        actor = _user()
        row = grant_scope_to_user(db, actor=actor, target=target, scope=PLAYERS_VIEW)
        assert row is existing

    def test_grant_unknown_scope_400(self):
        db = MagicMock()
        with pytest.raises(HTTPException) as ei:
            grant_scope_to_user(
                db, actor=_user(), target=_user(False), scope="admin.nope"
            )
        assert ei.value.status_code == 400

    def test_revoke_flushes_before_remaining_count(self):
        """Mack CRITICAL: autoflush=False — flush before count or is_admin sticks."""
        target = _user(admin=True)
        locked = _user(admin=True)
        locked.id = target.id
        row = SimpleNamespace(id=uuid.uuid4(), revoked_at=None, revoked_by=None)
        db = MagicMock()
        order: list[str] = []

        grant_filter = MagicMock()
        grant_filter.with_for_update.return_value.all.return_value = [row]

        def count_side():
            order.append("count")
            return 0

        remaining_filter = MagicMock()
        remaining_filter.count.side_effect = count_side

        def flush_side():
            order.append("flush")

        db.flush.side_effect = flush_side

        calls = {"n": 0}

        def query_side(model):
            name = getattr(model, "__name__", str(model))
            if name == "User":
                return _lock_chain(db, locked)
            # First AdminScopeGrant query = lock+all; second = remaining count
            calls["n"] += 1
            q = MagicMock()
            if calls["n"] == 1:
                q.filter.return_value = grant_filter
            else:
                q.filter.return_value = remaining_filter
            return q

        db.query.side_effect = query_side
        n = revoke_scope_from_user(
            db, actor=_user(), target=target, scope=PLAYERS_VIEW
        )
        assert n == 1
        assert order == ["flush", "count"]
        assert locked.is_admin is False
        assert target.is_admin is False
        assert row.revoked_at is not None

    def test_revoke_non_last_leaves_is_admin(self):
        target = _user(admin=True)
        locked = _user(admin=True)
        locked.id = target.id
        row = SimpleNamespace(id=uuid.uuid4(), revoked_at=None, revoked_by=None)
        db = MagicMock()
        grant_filter = MagicMock()
        grant_filter.with_for_update.return_value.all.return_value = [row]
        remaining_filter = MagicMock()
        remaining_filter.count.return_value = 1  # another scope still active
        calls = {"n": 0}

        def query_side(model):
            name = getattr(model, "__name__", str(model))
            if name == "User":
                return _lock_chain(db, locked)
            calls["n"] += 1
            q = MagicMock()
            q.filter.return_value = grant_filter if calls["n"] == 1 else remaining_filter
            return q

        db.query.side_effect = query_side
        revoke_scope_from_user(db, actor=_user(), target=target, scope=PLAYERS_VIEW)
        assert locked.is_admin is True
        assert target.is_admin is True


class TestCreateAdminRouteRetired:
    def test_handler_raises_410(self):
        from src.api.routes import users as users_mod
        src = inspect.getsource(users_mod.create_admin_user)
        assert "HTTP_410_GONE" in src
        assert "retired" in src.lower()

    def test_revoke_source_contains_flush(self):
        from src.api.routes import admin_scopes as mod
        src = inspect.getsource(mod.revoke_scope_from_user)
        assert "db.flush()" in src
        assert "with_for_update" in src
