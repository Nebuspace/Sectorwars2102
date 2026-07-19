"""RBAC E-5 — real-session money-path commit-boundary (hub-cipher WRAP-WAVE-2 gate).

MagicMock cannot see a broken helper-owns-commit on a credit mutation.
This harness uses a real SQLite Session (autoflush=False) + AdminActionLog
table, driving ``admin_action_attempt`` exactly as wrapped money routes do.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from sqlalchemy import Column, Integer, String, create_engine, event, text
from sqlalchemy.orm import declarative_base, sessionmaker
from sqlalchemy.pool import StaticPool

from src.auth.admin_scopes import PLAYERS_ADJUST_CREDITS
from src.models.admin_action_log import AdminActionLog
from src.services.admin_action_attempt import admin_action_attempt

Base = declarative_base()


class CreditLedgerRow(Base):
    """Stand-in for player.credits — real Session mutation, not MagicMock."""

    __tablename__ = "e5_credit_ledger"

    id = Column(String(36), primary_key=True)
    credits = Column(Integer, nullable=False, default=0)


@pytest.fixture()
def money_db():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def _fk(dbapi_conn, _):
        # AdminActionLog.admin_user_id FK → users; money-path test doesn't
        # need a full User row — disable FK enforcement (same class of
        # stand-in harness as c1 credit ledger).
        dbapi_conn.execute("PRAGMA foreign_keys=OFF")

    # SQLite cannot render PG JSONB — create the ledger table the ORM expects
    # with TEXT for payload (same pattern as test_rbac_phase_c1_action_log).
    from sqlalchemy.dialects.postgresql import JSONB
    from sqlalchemy.ext.compiler import compiles

    @compiles(JSONB, "sqlite")
    def _sqlite_jsonb(_type, _compiler, **_kw):
        return "TEXT"

    CreditLedgerRow.__table__.create(engine, checkfirst=True)
    AdminActionLog.__table__.create(engine, checkfirst=True)

    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    db = Session()
    try:
        yield db
    finally:
        db.close()
        engine.dispose()


class TestE5MoneyPathCommitBoundary:
    def test_succeed_persists_credit_mutation_and_success_log(self, money_db):
        player_id = str(uuid.uuid4())
        actor = SimpleNamespace(id=uuid.uuid4())
        money_db.add(CreditLedgerRow(id=player_id, credits=100))
        money_db.commit()

        with admin_action_attempt(
            money_db,
            actor=actor,
            scope_used=PLAYERS_ADJUST_CREDITS,
            action="player_update",
            target_type="player",
            target_id=player_id,
            payload={"credits": 999},
        ) as attempt:
            row = money_db.query(CreditLedgerRow).filter_by(id=player_id).one()
            row.credits = 999
            attempt.succeed(payload={"credits": 999})

        credits = money_db.execute(
            text("SELECT credits FROM e5_credit_ledger WHERE id=:id"),
            {"id": player_id},
        ).scalar()
        assert credits == 999
        log_row = money_db.query(AdminActionLog).one()
        assert log_row.result == "success"
        assert log_row.scope_used == PLAYERS_ADJUST_CREDITS
        assert log_row.action == "player_update"

    def test_forced_commit_failure_rolls_back_mutation_and_writes_failed_row(
        self, money_db
    ):
        player_id = str(uuid.uuid4())
        actor = SimpleNamespace(id=uuid.uuid4())
        money_db.add(CreditLedgerRow(id=player_id, credits=100))
        money_db.commit()

        real_commit = money_db.commit
        calls = {"n": 0}

        def flaky_commit():
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("forced commit failure")
            return real_commit()

        with patch.object(money_db, "commit", side_effect=flaky_commit):
            with pytest.raises(RuntimeError, match="forced commit failure"):
                with admin_action_attempt(
                    money_db,
                    actor=actor,
                    scope_used=PLAYERS_ADJUST_CREDITS,
                    action="player_update",
                    target_type="player",
                    target_id=player_id,
                    payload={"credits": 999},
                ) as attempt:
                    row = (
                        money_db.query(CreditLedgerRow)
                        .filter_by(id=player_id)
                        .one()
                    )
                    row.credits = 999
                    attempt.succeed(payload={"credits": 999})

        credits = money_db.execute(
            text("SELECT credits FROM e5_credit_ledger WHERE id=:id"),
            {"id": player_id},
        ).scalar()
        assert credits == 100, "credit mutation must roll back with failed commit"
        logs = money_db.query(AdminActionLog).all()
        assert len(logs) == 1
        assert logs[0].result == "failed"
        assert logs[0].scope_used == PLAYERS_ADJUST_CREDITS
