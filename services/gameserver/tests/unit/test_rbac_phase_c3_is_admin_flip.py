"""RBAC Phase C3 — derived ``User.is_admin`` Python getter (HOLD-commit).

HANDOFF 2026-07-17T21:48:21Z: build the getter → EXISTS(active grant) + a
test asserting ``derived == flat`` on a seeded admin set. Hub gates statically
→ HOLD commit → live ``derived==flat`` on stage PG at the deploy window →
THEN commit+deploy.

No migration: physical ``users.is_admin`` stays as denormalized cache.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.models.admin_scope_grant import AdminScopeGrant
from src.models.user import User


@pytest.fixture()
def c3_db():
    """Minimal SQLite session with users + admin_scope_grants only."""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def _fk(dbapi_conn, _):
        dbapi_conn.execute("PRAGMA foreign_keys=OFF")

    # Create only the two tables C3 needs (avoid full metadata PG types).
    User.__table__.create(engine, checkfirst=True)
    AdminScopeGrant.__table__.create(engine, checkfirst=True)

    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    db = Session()
    try:
        yield db
    finally:
        db.close()
        engine.dispose()


def _seed_user(db, *, username: str, flat_admin: bool) -> User:
    u = User(
        id=uuid.uuid4(),
        username=username,
        email=f"{username}@c3.test",
        is_admin=flat_admin,
        is_active=True,
        deleted=False,
    )
    db.add(u)
    db.flush()
    return u


def _seed_grant(db, user: User, scope: str = "admin.scopes.grant") -> AdminScopeGrant:
    g = AdminScopeGrant(
        id=uuid.uuid4(),
        user_id=user.id,
        scope=scope,
        granted_by=user.id,
    )
    db.add(g)
    db.flush()
    return g


class TestC3DerivedIsAdmin:
    def test_seeded_admin_derived_equals_flat(self, c3_db):
        """Properly seeded admin: flat True + active grant → derived True."""
        admin = _seed_user(c3_db, username="seeded_admin", flat_admin=True)
        _seed_grant(c3_db, admin)
        c3_db.commit()

        loaded = c3_db.query(User).filter(User.id == admin.id).one()
        assert loaded._is_admin is True
        assert loaded.is_admin is True  # session-attached → EXISTS

    def test_phantom_flat_admin_without_grant_is_not_admin(self, c3_db):
        """C3 closes phantom-admin: flat True, zero active grants → False."""
        phantom = _seed_user(c3_db, username="phantom", flat_admin=True)
        c3_db.commit()

        loaded = c3_db.query(User).filter(User.id == phantom.id).one()
        assert loaded._is_admin is True
        assert loaded.is_admin is False

    def test_grant_without_flat_still_admin(self, c3_db):
        """Derived wins: active grant + flat False → is_admin True."""
        u = _seed_user(c3_db, username="grant_only", flat_admin=False)
        _seed_grant(c3_db, u)
        c3_db.commit()

        loaded = c3_db.query(User).filter(User.id == u.id).one()
        assert loaded._is_admin is False
        assert loaded.is_admin is True

    def test_revoked_grant_not_admin(self, c3_db):
        u = _seed_user(c3_db, username="revoked", flat_admin=True)
        g = _seed_grant(c3_db, u)
        g.revoked_at = datetime.now(timezone.utc)
        c3_db.flush()
        c3_db.commit()

        loaded = c3_db.query(User).filter(User.id == u.id).one()
        assert loaded.is_admin is False

    def test_multi_admin_seed_set_derived_equals_flat(self, c3_db):
        """HANDOFF: derived == flat on a seeded admin *set*."""
        seeded = []
        for i in range(3):
            u = _seed_user(c3_db, username=f"admin_{i}", flat_admin=True)
            _seed_grant(c3_db, u, scope=f"players.view")
            seeded.append(u.id)
        player = _seed_user(c3_db, username="player", flat_admin=False)
        c3_db.commit()

        for uid in seeded:
            u = c3_db.query(User).filter(User.id == uid).one()
            assert u.is_admin is True
            assert u._is_admin is True
            assert u.is_admin == u._is_admin

        p = c3_db.query(User).filter(User.id == player.id).one()
        assert p.is_admin is False
        assert p._is_admin is False
        assert p.is_admin == p._is_admin
