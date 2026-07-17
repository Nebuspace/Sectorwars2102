"""RBAC Phase C1/C2 — HIGH_IMPACT wiring + true E2E same-txn atomicity.

ACCEPT carry-forward #1 from hub-cipher C0 gate: a real mutation table where
forced rollback leaves ZERO of BOTH the mutation row AND its log row.
"""

from __future__ import annotations

import inspect
import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy import Column, Integer, String, create_engine, event, text
from sqlalchemy.orm import declarative_base, sessionmaker
from sqlalchemy.pool import StaticPool

from src.auth.admin_scopes import (
    DISPUTES_RESOLVE,
    GALAXY_MANAGE,
    PLAYERS_ADJUST_CREDITS,
    SHIPS_MANAGE,
)
from src.services.admin_action_log_service import log_admin_action

Base = declarative_base()


class CreditLedgerRow(Base):
    """Stand-in for player.credits mutation (no full Player model required)."""

    __tablename__ = "c1_credit_ledger"

    id = Column(String(36), primary_key=True)
    credits = Column(Integer, nullable=False, default=0)


@pytest.fixture()
def e2e_db():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def _fk(dbapi_conn, _):
        dbapi_conn.execute("PRAGMA foreign_keys=ON")

    CreditLedgerRow.__table__.create(engine, checkfirst=True)
    with engine.begin() as conn:
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


def _apply_credit_update(db, *, actor, player_id: str, new_credits: int) -> None:
    """Mirrors the admin credit-update pattern: mutate + log, caller commits."""
    row = db.query(CreditLedgerRow).filter(CreditLedgerRow.id == player_id).one()
    row.credits = new_credits
    log_admin_action(
        db,
        actor=actor,
        scope_used=PLAYERS_ADJUST_CREDITS,
        action="player_update",
        target_type="player",
        target_id=player_id,
        payload={"credits_set": True, "credits": new_credits},
    )


class TestCreditsMutateLogAtomicity:
    def test_commit_persists_mutation_and_log(self, e2e_db):
        player_id = str(uuid.uuid4())
        actor = SimpleNamespace(id=uuid.uuid4())
        e2e_db.add(CreditLedgerRow(id=player_id, credits=100))
        e2e_db.commit()

        _apply_credit_update(e2e_db, actor=actor, player_id=player_id, new_credits=999)
        e2e_db.commit()

        credits = e2e_db.execute(
            text("SELECT credits FROM c1_credit_ledger WHERE id=:id"),
            {"id": player_id},
        ).scalar()
        logs = e2e_db.execute(text("SELECT COUNT(*) FROM admin_action_logs")).scalar()
        assert credits == 999
        assert logs == 1

    def test_rollback_clears_mutation_and_log(self, e2e_db):
        """Hub carry-forward #1: no phantom audit if credit change rolls back."""
        player_id = str(uuid.uuid4())
        actor = SimpleNamespace(id=uuid.uuid4())
        e2e_db.add(CreditLedgerRow(id=player_id, credits=100))
        e2e_db.commit()

        _apply_credit_update(e2e_db, actor=actor, player_id=player_id, new_credits=999)
        e2e_db.rollback()

        credits = e2e_db.execute(
            text("SELECT credits FROM c1_credit_ledger WHERE id=:id"),
            {"id": player_id},
        ).scalar()
        logs = e2e_db.execute(text("SELECT COUNT(*) FROM admin_action_logs")).scalar()
        assert credits == 100
        assert logs == 0


def _assert_logged(fn, *, action: str, scope_const: str):
    src = inspect.getsource(fn)
    assert "log_admin_action" in src, fn.__name__
    assert f'action="{action}"' in src, fn.__name__
    assert scope_const in src, fn.__name__
    assert src.index("log_admin_action") < src.index("db.commit()"), fn.__name__


def test_c1_high_impact_routes_log_before_commit():
    from src.api.routes import admin as admin_mod
    from src.api.routes import admin_comprehensive as comp
    from src.api.routes import admin_contract_disputes as disputes
    from src.api.routes import admin_ships as ships

    cases = [
        (admin_mod.clear_all_galaxy_data, "galaxy_clear", "GALAXY_MANAGE"),
        (admin_mod.update_player, "player_update", "PLAYERS_ADJUST_CREDITS"),
        (admin_mod.fix_galaxy_statistics, "galaxy_fix_statistics", "GALAXY_MANAGE"),
        (admin_mod.create_warp_tunnel, "warp_tunnel_create", "GALAXY_MANAGE"),
        (admin_mod.update_port, "port_update", "GALAXY_MANAGE"),
        (comp.update_player, "player_update", "PLAYERS_ADJUST_CREDITS"),
        (comp.create_ship, "ship_create", "SHIPS_MANAGE"),
        (comp.update_ship, "ship_update", "SHIPS_MANAGE"),
        (comp.delete_ship, "ship_delete", "SHIPS_MANAGE"),
        (comp.teleport_ship, "ship_teleport", "SHIPS_MANAGE"),
        (comp.update_sector, "sector_update", "GALAXY_MANAGE"),
        (comp.delete_port, "port_delete", "GALAXY_MANAGE"),
        (comp.delete_warp_tunnel, "warp_tunnel_delete", "GALAXY_MANAGE"),
        (ships.emergency_ship_action, "ship_emergency", "SHIPS_MANAGE"),
        (ships.delete_ship, "ship_delete", "SHIPS_MANAGE"),
        (disputes.resolve_contract_dispute, "contract_dispute_resolve", "DISPUTES_RESOLVE"),
    ]
    for fn, action, scope in cases:
        _assert_logged(fn, action=action, scope_const=scope)


def test_reason_bounded():
    from src.api.routes.admin_multi_account import ClusterDecisionRequest

    schema = ClusterDecisionRequest.model_json_schema()
    props = schema["properties"]["reason"]
    # Optional[str] → anyOf [string, null]; maxLength sits on the string branch.
    branches = props.get("anyOf") or [props]
    assert any(b.get("maxLength") == 2000 for b in branches)


def test_scopes_still_single_writer():
    """No new AdminActionLog( constructors outside the helper."""
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[2] / "src"
    offenders = []
    for path in root.rglob("*.py"):
        if path.name in {"admin_action_log.py", "admin_action_log_service.py"}:
            continue
        if "AdminActionLog(" in path.read_text():
            offenders.append(str(path.relative_to(root)))
    assert offenders == []


def test_comprehensive_update_player_logs_before_reputation_service():
    """Cipher MEDIUM: textual order vs final commit is blind to intervening
    FactionService commits — require log BEFORE await update_reputation."""
    from src.api.routes import admin_comprehensive as comp

    src = inspect.getsource(comp.update_player)
    assert "log_admin_action" in src
    assert src.index("log_admin_action") < src.index(
        "await faction_service.update_reputation"
    )
    assert src.index("log_admin_action") < src.index("db.commit()")


class TestInterveningServiceCommitAtomicity:
    """Real-route property for credits+reputation combined edit (hub HIGH).

    Simulates FactionService's internal commit then a post-commit failure
    (WS throw). Log must already be in-session so the intervening commit
    persists credit+audit together; a later rollback is a no-op.
    """

    def test_log_before_intervening_commit_survives_later_rollback(self, e2e_db):
        player_id = str(uuid.uuid4())
        actor = SimpleNamespace(id=uuid.uuid4())
        e2e_db.add(CreditLedgerRow(id=player_id, credits=100))
        e2e_db.commit()

        row = e2e_db.query(CreditLedgerRow).filter(CreditLedgerRow.id == player_id).one()
        row.credits = 999
        log_admin_action(
            e2e_db,
            actor=actor,
            scope_used=PLAYERS_ADJUST_CREDITS,
            action="player_update",
            target_type="player",
            target_id=player_id,
            payload={"credits_set": True, "reputation_keys": ["Terran Federation"]},
        )
        e2e_db.commit()  # intervening FactionService commit
        e2e_db.rollback()  # WS throw → route except

        credits = e2e_db.execute(
            text("SELECT credits FROM c1_credit_ledger WHERE id=:id"),
            {"id": player_id},
        ).scalar()
        logs = e2e_db.execute(text("SELECT COUNT(*) FROM admin_action_logs")).scalar()
        assert credits == 999
        assert logs == 1

    def test_log_after_intervening_commit_loses_audit_on_rollback(self, e2e_db):
        """Illustrative model of the PRE-FIX failure mode (NOT a route regression
        guard — does not drive admin_comprehensive.update_player). The genuine
        regression guard is test_comprehensive_update_player_logs_before_reputation_service.
        """
        player_id = str(uuid.uuid4())
        actor = SimpleNamespace(id=uuid.uuid4())
        e2e_db.add(CreditLedgerRow(id=player_id, credits=100))
        e2e_db.commit()

        row = e2e_db.query(CreditLedgerRow).filter(CreditLedgerRow.id == player_id).one()
        row.credits = 999
        e2e_db.commit()  # intervening commit BEFORE log (the bug)
        log_admin_action(
            e2e_db,
            actor=actor,
            scope_used=PLAYERS_ADJUST_CREDITS,
            action="player_update",
            target_type="player",
            target_id=player_id,
            payload={"credits_set": True},
        )
        e2e_db.rollback()

        credits = e2e_db.execute(
            text("SELECT credits FROM c1_credit_ledger WHERE id=:id"),
            {"id": player_id},
        ).scalar()
        logs = e2e_db.execute(text("SELECT COUNT(*) FROM admin_action_logs")).scalar()
        assert credits == 999
        assert logs == 0
