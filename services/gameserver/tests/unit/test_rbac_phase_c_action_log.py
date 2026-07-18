"""Unit tests for RBAC Phase C — AdminActionLog helper + mutation wiring.

DB-free (+ one sqlite atomicity harness).  Run with the same env harness
as test_rbac_phase_a2.py.
"""

from __future__ import annotations

import inspect
import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.auth.admin_scopes import GALAXY_MANAGE, MULTI_ACCOUNT_REVIEW, PLAYERS_VIEW
from src.models.admin_action_log import AdminActionLog
from src.services.admin_action_log_service import log_admin_action


def test_log_admin_action_adds_row_without_committing():
    db = MagicMock()
    actor = SimpleNamespace(id=uuid.uuid4())

    log_admin_action(
        db,
        actor=actor,
        scope_used=GALAXY_MANAGE,
        action="planet_update",
        target_type="planet",
        target_id="planet-1",
        payload={"updated_fields": ["name"]},
    )

    db.add.assert_called_once()
    row = db.add.call_args[0][0]
    assert isinstance(row, AdminActionLog)
    assert row.admin_user_id == actor.id
    assert row.scope_used == GALAXY_MANAGE
    assert row.action == "planet_update"
    assert row.target_type == "planet"
    assert row.target_id == "planet-1"
    assert row.payload_snapshot == {"updated_fields": ["name"]}
    assert row.result == "success"
    db.commit.assert_not_called()
    db.rollback.assert_not_called()


def test_admin_scopes_routes_use_attempt_helper():
    """E-5: grant/revoke routes own logging via admin_action_attempt (no _log_action)."""
    from src.api.routes import admin_scopes as mod

    assert not hasattr(mod, "_log_action")
    grant_src = inspect.getsource(mod.grant_scope)
    revoke_src = inspect.getsource(mod.revoke_scope)
    assert "admin_action_attempt" in grant_src
    assert "attempt.succeed" in grant_src
    assert "admin_action_attempt" in revoke_src
    assert "attempt.succeed" in revoke_src


def test_helper_is_single_constructor_source():
    """ACCEPT #1: log_admin_action is the only app-code AdminActionLog constructor."""
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[2] / "src"
    offenders = []
    for path in root.rglob("*.py"):
        if path.name == "admin_action_log.py":
            continue
        text_src = path.read_text()
        if "AdminActionLog(" in text_src and "admin_action_log_service" not in str(path):
            # Allow the service module itself
            if path.name != "admin_action_log_service.py":
                offenders.append(str(path.relative_to(root)))
    assert offenders == [], f"AdminActionLog( outside helper: {offenders}"


def _assert_log_before_commit(fn, action: str):
    src = inspect.getsource(fn)
    assert "log_admin_action" in src
    assert f'action="{action}"' in src
    assert src.index("log_admin_action") < src.index("db.commit()")


def test_update_planet_logs_before_commit():
    from src.api.routes import admin_comprehensive as mod

    _assert_log_before_commit(mod.update_planet, "planet_update")
    assert "actor=current_admin" in inspect.getsource(mod.update_planet)


def test_delete_planet_logs_before_commit():
    from src.api.routes import admin_comprehensive as mod

    _assert_log_before_commit(mod.delete_planet, "planet_delete")
    assert "actor=current_admin" in inspect.getsource(mod.delete_planet)


def test_decide_cluster_logs_before_commit():
    from src.api.routes import admin_multi_account as mod

    _assert_log_before_commit(mod.decide_cluster, "multi_account_decide")
    src = inspect.getsource(mod.decide_cluster)
    assert "actor=admin" in src
    assert "MULTI_ACCOUNT_REVIEW" in src


def test_no_secrets_in_c0_payloads():
    """ACCEPT #5: C0 payload keys are non-secret field names only."""
    from src.api.routes import admin_comprehensive as mod
    from src.api.routes import admin_multi_account as ma

    for fn in (mod.update_planet, mod.delete_planet, ma.decide_cluster):
        src = inspect.getsource(fn)
        blob = src.lower()
        for banned in ("password", "token", "secret", "credential", "authorization"):
            # Allow the word only outside the log_admin_action call region
            log_region = src[src.index("log_admin_action") : src.index("db.commit()")]
            assert banned not in log_region.lower(), f"{fn.__name__}: {banned} in payload"


def test_append_only_trigger_still_in_migration():
    """ACCEPT #3: append-only DB trigger remains in the foundation migration."""
    from pathlib import Path

    mig = next(
        Path(__file__).resolve().parents[2].joinpath("alembic/versions").glob(
            "e2a7f3c8b5d1*.py"
        )
    )
    text_src = mig.read_text()
    assert "trg_admin_action_logs_append_only" in text_src
    assert "admin_action_logs_append_only" in text_src


def test_scopes_grant_helper_returns_outcome_without_logging():
    """E-5: grant helper returns ScopeMutationOutcome; route logs via attempt."""
    from src.api.routes.admin_scopes import grant_scope_to_user

    target = SimpleNamespace(id=uuid.uuid4(), is_admin=False)
    locked = SimpleNamespace(id=target.id, is_admin=False)
    actor = SimpleNamespace(id=uuid.uuid4())
    db = MagicMock()

    grant_q = MagicMock()
    grant_q.filter.return_value.with_for_update.return_value.first.return_value = None
    nested = MagicMock()
    nested.__enter__ = MagicMock(return_value=None)
    nested.__exit__ = MagicMock(return_value=False)
    db.begin_nested.return_value = nested

    user_q = MagicMock()
    user_q.filter.return_value.with_for_update.return_value.one.return_value = locked

    def query_side(model):
        name = getattr(model, "__name__", str(model))
        if name == "User":
            return user_q
        return grant_q

    db.query.side_effect = query_side

    outcome = grant_scope_to_user(db, actor=actor, target=target, scope=PLAYERS_VIEW)
    assert outcome.action == "scope_grant"
    assert outcome.payload == {"scope": PLAYERS_VIEW}
    db.add.assert_called()  # AdminScopeGrant row


@pytest.fixture()
def log_db():
    """SQLite session for same-txn atomicity (ACCEPT #2). JSONB→JSON via compile."""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def _fk(dbapi_conn, _):
        dbapi_conn.execute("PRAGMA foreign_keys=ON")

    # Minimal users + admin_action_logs (JSONB stored as TEXT/JSON on sqlite).
    with engine.begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE users ("
                "id CHAR(32) PRIMARY KEY, username VARCHAR(50), "
                "is_admin BOOLEAN DEFAULT 0)"
            )
        )
        conn.execute(
            text(
                "CREATE TABLE admin_action_logs ("
                "id CHAR(32) PRIMARY KEY,"
                "admin_user_id CHAR(32),"
                "scope_used VARCHAR(120),"
                "action VARCHAR(200) NOT NULL,"
                "target_type VARCHAR(100),"
                "target_id VARCHAR(255),"
                "payload_snapshot TEXT,"
                "result VARCHAR(50),"
                "failure_reason TEXT,"
                "reviewed_by CHAR(32),"
                "reviewed_at TIMESTAMP,"
                "at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
            )
        )

    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    db = Session()
    try:
        yield db
    finally:
        db.close()
        engine.dispose()


class TestSameTxnAtomicity:
    """ACCEPT #2: log commits with mutation; rollback leaves zero orphan rows."""

    def test_commit_persists_log_row(self, log_db):
        actor_id = uuid.uuid4()
        actor = SimpleNamespace(id=actor_id)
        log_admin_action(
            log_db,
            actor=actor,
            scope_used=GALAXY_MANAGE,
            action="planet_update",
            target_type="planet",
            target_id="p1",
            payload={"updated_fields": ["name"]},
        )
        log_db.commit()
        n = log_db.execute(text("SELECT COUNT(*) FROM admin_action_logs")).scalar()
        assert n == 1
        row = log_db.execute(
            text("SELECT action, scope_used, target_type, target_id FROM admin_action_logs")
        ).one()
        assert row == ("planet_update", GALAXY_MANAGE, "planet", "p1")

    def test_rollback_leaves_zero_orphan_logs(self, log_db):
        actor = SimpleNamespace(id=uuid.uuid4())
        log_admin_action(
            log_db,
            actor=actor,
            scope_used=MULTI_ACCOUNT_REVIEW,
            action="multi_account_decide",
            target_type="multi_account_cluster",
            target_id="c1",
            payload={"decision": "confirmed"},
        )
        log_db.rollback()
        n = log_db.execute(text("SELECT COUNT(*) FROM admin_action_logs")).scalar()
        assert n == 0
