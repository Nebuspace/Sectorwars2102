"""LIVE-SESSION regression for revoke last-scope → clears is_admin.

Hub-cipher / mack: MagicMock cannot see autoflush=False semantics.
This harness mirrors SessionLocal(autoflush=False) on SQLite in-memory.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.auth.admin_scopes import PLAYERS_VIEW, PLAYERS_SUSPEND, SCOPES_GRANT
from src.api.routes.admin_scopes import grant_scope_to_user, revoke_scope_from_user
from src.models.admin_scope_grant import AdminScopeGrant
from src.models.user import User


@pytest.fixture()
def live_db(monkeypatch):
    """autoflush=False session — the production failure mode."""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def _fk(dbapi_conn, _):
        dbapi_conn.execute("PRAGMA foreign_keys=ON")

    # Skip AdminActionLog (JSONB) — audit write is orthogonal to the
    # is_admin flush bug under test.  Patch the bound name in admin_scopes
    # (it imported log_admin_action into its module namespace).
    monkeypatch.setattr(
        "src.api.routes.admin_scopes.log_admin_action",
        lambda *a, **k: None,
    )

    User.__table__.create(engine, checkfirst=True)
    AdminScopeGrant.__table__.create(engine, checkfirst=True)

    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    db = Session()
    try:
        yield db
    finally:
        db.close()
        engine.dispose()


def _mk_user(db, *, admin: bool = False) -> User:
    u = User(
        id=uuid.uuid4(),
        username=f"u_{uuid.uuid4().hex[:8]}",
        email=f"{uuid.uuid4().hex[:8]}@t.local",
        is_admin=admin,
        is_active=True,
        deleted=False,
    )
    db.add(u)
    db.flush()
    return u


class TestRevokeLastScopeLiveSession:
    def test_last_scope_clears_is_admin(self, live_db):
        actor = _mk_user(live_db, admin=True)
        target = _mk_user(live_db, admin=False)
        live_db.commit()

        grant_scope_to_user(live_db, actor=actor, target=target, scope=PLAYERS_VIEW)
        live_db.commit()
        live_db.refresh(target)
        assert target.is_admin is True

        n = revoke_scope_from_user(
            live_db, actor=actor, target=target, scope=PLAYERS_VIEW
        )
        live_db.commit()
        live_db.refresh(target)
        assert n == 1
        assert target.is_admin is False

    def test_non_last_scope_keeps_is_admin(self, live_db):
        actor = _mk_user(live_db, admin=True)
        target = _mk_user(live_db, admin=False)
        live_db.commit()

        grant_scope_to_user(live_db, actor=actor, target=target, scope=PLAYERS_VIEW)
        grant_scope_to_user(live_db, actor=actor, target=target, scope=PLAYERS_SUSPEND)
        live_db.commit()
        live_db.refresh(target)
        assert target.is_admin is True

        revoke_scope_from_user(
            live_db, actor=actor, target=target, scope=PLAYERS_VIEW
        )
        live_db.commit()
        live_db.refresh(target)
        assert target.is_admin is True

    def test_last_meta_holder_refused(self, live_db):
        from fastapi import HTTPException

        actor = _mk_user(live_db, admin=True)
        # Seed actor as sole holder of SCOPES_GRANT
        live_db.add(
            AdminScopeGrant(
                id=uuid.uuid4(),
                user_id=actor.id,
                scope=SCOPES_GRANT,
                granted_by=actor.id,
            )
        )
        actor.is_admin = True
        live_db.commit()

        with pytest.raises(HTTPException) as ei:
            revoke_scope_from_user(
                live_db, actor=actor, target=actor, scope=SCOPES_GRANT
            )
        assert ei.value.status_code == 409
        assert "last system-wide holder" in ei.value.detail
