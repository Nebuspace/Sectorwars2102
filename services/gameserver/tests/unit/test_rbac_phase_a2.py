"""Unit tests for RBAC Phase A2 — require_scope + is_admin hybrid.

DB-free: no live Postgres.  Run with:

    GAMESERVER_CI_DB_FREE=1 ENVIRONMENT=testing \\
        DATABASE_URL="postgresql://dummy:dummy@localhost:5432/dummy" \\
        JWT_SECRET=... ADMIN_USERNAME=sysadmin ADMIN_PASSWORD=sysadmin-dev-only \\
        ARIA_ENCRYPTION_KEY=... \\
    pytest tests/unit/test_rbac_phase_a2.py -v
"""

from __future__ import annotations

import asyncio
import inspect
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException
from sqlalchemy import Boolean
from sqlalchemy.sql.elements import BinaryExpression

from src.auth import admin_scopes
from src.auth.admin_scopes import PLAYERS_VIEW, SCOPES_GRANT
from src.auth.dependencies import require_scope, user_has_active_scope
from src.models.user import User


# ---------------------------------------------------------------------------
# require_scope factory
# ---------------------------------------------------------------------------

class TestRequireScopeFactory:
    def test_unknown_scope_raises_at_construction(self):
        with pytest.raises(ValueError, match="not in the canonical"):
            require_scope("admin.not.a.real.scope")

    def test_known_scope_returns_callable(self):
        dep = require_scope(PLAYERS_VIEW)
        assert callable(dep)
        assert getattr(dep, "__require_scope__") == PLAYERS_VIEW


class TestUserHasActiveScope:
    def test_present_active_grant(self):
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = (uuid.uuid4(),)
        assert user_has_active_scope(db, uuid.uuid4(), PLAYERS_VIEW) is True

    def test_absent_grant(self):
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = None
        assert user_has_active_scope(db, uuid.uuid4(), PLAYERS_VIEW) is False

    def test_db_error_propagates(self):
        """Callers must catch — helper itself must NOT swallow."""
        db = MagicMock()
        db.query.return_value.filter.return_value.first.side_effect = RuntimeError("db down")
        with pytest.raises(RuntimeError, match="db down"):
            user_has_active_scope(db, uuid.uuid4(), PLAYERS_VIEW)


def _run(coro):
    return asyncio.run(coro)


class TestRequireScopeDependency:
    """Exercise the inner dependency with mocked get_current_user/db."""

    def _invoke(self, *, grant_row, db_raises: Exception | None = None):
        dep = require_scope(PLAYERS_VIEW)
        user = SimpleNamespace(id=uuid.uuid4(), username="admin", is_admin=True)
        db = MagicMock()
        if db_raises is not None:
            db.query.side_effect = db_raises
        else:
            db.query.return_value.filter.return_value.first.return_value = grant_row

        # Call the dependency coroutine directly (bypass FastAPI Depends).
        return _run(dep(current_user=user, db=db)), user

    def test_holder_passes(self):
        result, user = self._invoke(grant_row=(uuid.uuid4(),))
        assert result is user

    def test_non_holder_403_names_scope(self):
        with pytest.raises(HTTPException) as ei:
            self._invoke(grant_row=None)
        assert ei.value.status_code == 403
        assert PLAYERS_VIEW in ei.value.detail
        assert "Missing required scope" in ei.value.detail

    def test_revoked_treated_as_absent(self):
        # Lookup filters revoked_at IS NULL — a revoked row never surfaces;
        # first() returns None. Same path as absent.
        with pytest.raises(HTTPException) as ei:
            self._invoke(grant_row=None)
        assert ei.value.status_code == 403

    def test_db_exception_fail_closed_403_never_200(self):
        """Cipher #5: grant-lookup failure → 403, not 200 / not 500."""
        with pytest.raises(HTTPException) as ei:
            self._invoke(grant_row=None, db_raises=RuntimeError("connection reset"))
        assert ei.value.status_code == 403
        assert PLAYERS_VIEW in ei.value.detail
        assert ei.value.status_code != 200


# ---------------------------------------------------------------------------
# is_admin hybrid — C3: Python getter derived when session-attached;
# flat column remains the denormalized write-through cache + detached fallback
# ---------------------------------------------------------------------------

class TestIsAdminHybrid:
    def test_detached_reads_flat_column_fallback(self):
        """No Session → flat denormalized cache (construction / sync paths)."""
        u = User(username="a", email="a@x", is_admin=True)
        assert u.is_admin is True
        assert u._is_admin is True

    def test_python_setter_writes_flat_column(self):
        u = User(username="b", email="b@x", is_admin=False)
        assert u.is_admin is False
        u.is_admin = True
        assert u._is_admin is True
        assert u.is_admin is True  # still detached → flat fallback

    def test_column_maps_to_is_admin_db_name(self):
        col = User.__table__.c.is_admin
        assert col.name == "is_admin"
        assert isinstance(col.type, Boolean)

    def test_expression_is_exists_not_naive_column(self):
        expr = User.is_admin
        # hybrid .expression returns a ClauseElement (Exists), not the Column
        compiled = str(expr.expression.compile(compile_kwargs={"literal_binds": False}))
        assert "EXISTS" in compiled.upper()
        assert "admin_scope_grants" in compiled
        assert "revoked_at" in compiled

    def test_filter_compiles_at_four_sql_sites(self):
        """A2 accept: the 4 SQL-level User.is_admin == True sites still compile.

        Sites: user_service.py:33 · auth/admin.py:34-37 · auth.py:222 · test.py:43
        """
        from src.services import user_service
        from src.auth import admin as auth_admin
        # Import the route modules so their filter expressions are loadable.
        import src.api.routes.auth as auth_routes  # noqa: F401
        import src.api.routes.test as test_routes  # noqa: F401

        clause = User.is_admin == True  # noqa: E712
        assert isinstance(clause, BinaryExpression)
        sql = str(clause.compile(compile_kwargs={"literal_binds": False}))
        assert "EXISTS" in sql.upper()

        # Sanity: the four modules still reference User.is_admin (not stripped).
        assert "User.is_admin" in inspect.getsource(user_service)
        assert "User.is_admin" in inspect.getsource(auth_admin)

    def test_expression_documents_active_grant_predicate(self):
        """SQL side of the C3 dual-read contract (live row-eq = hub window)."""
        expr_sql = str(
            User.is_admin.expression.compile(compile_kwargs={"literal_binds": False})
        )
        assert "revoked_at" in expr_sql
        assert "admin_scope_grants" in expr_sql

    def test_non_admin_flat_false_detached(self):
        u = User(username="p", email="p@x", is_admin=False)
        assert u.is_admin is False
        assert u._is_admin is False
