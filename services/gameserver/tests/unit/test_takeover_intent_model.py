"""LEG-3639: pins TakeoverIntent model + f4a8b2c91e73 migration chain.

Canon: sw2102-docs/DATA_MODELS/player.md § TakeoverIntent (six status values,
three indexes including partial pending serializer per ADR-0058 A-F3).

DB-free pins follow test_region_lifecycle_schema.py — AST-parse migration
source rather than attempt a live schema-diff. Real-DB apply proof is CI
ci-schema-parity on push.
"""
from __future__ import annotations

import ast
import pathlib
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import Column, MetaData, String, Table, create_engine

from src.models.takeover_intent import TakeoverIntent, TakeoverIntentStatus

_MIGRATION_PATH = (
    pathlib.Path(__file__).resolve().parents[2]
    / "alembic"
    / "versions"
    / "f4a8b2c91e73_add_takeover_intents_table.py"
)
_VERSIONS_DIR = _MIGRATION_PATH.parent

_EXPECTED_STATUS_VALUES = {
    "pending",
    "won",
    "lost",
    "transferred",
    "failed",
    "expired",
}

_EXPECTED_COLUMNS = {
    "id",
    "region_id",
    "caller_user_id",
    "approval_url",
    "status",
    "created_at",
    "expires_at",
    "completed_at",
}


def _assigns(path: pathlib.Path) -> dict:
    tree = ast.parse(path.read_text())
    return {
        n.targets[0].id: n.value.value
        for n in tree.body
        if isinstance(n, ast.Assign)
        and isinstance(n.targets[0], ast.Name)
        and isinstance(n.value, ast.Constant)
    }


def _created_table_names(fn: ast.FunctionDef) -> set[str]:
    names = set()
    for node in ast.walk(fn):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "create_table"
            and node.args
            and isinstance(node.args[0], ast.Constant)
        ):
            names.add(node.args[0].value)
    return names


def _index_names(fn: ast.FunctionDef) -> set[str]:
    names = set()
    for node in ast.walk(fn):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "create_index"
            and node.args
            and isinstance(node.args[0], ast.Constant)
        ):
            names.add(node.args[0].value)
    return names


@pytest.mark.unit
class TestTakeoverIntentStatusCanon:
    def test_exactly_the_six_canon_members(self) -> None:
        actual = {member.value for member in TakeoverIntentStatus}
        assert actual == _EXPECTED_STATUS_VALUES
        assert len(TakeoverIntentStatus) == 6

    def test_default_is_pending(self) -> None:
        assert TakeoverIntentStatus.PENDING.value == "pending"


@pytest.mark.unit
class TestMigrationChainIntegrity:
    def test_migration_file_exists(self) -> None:
        assert _MIGRATION_PATH.is_file()

    def test_down_revision_is_the_confirmed_head(self) -> None:
        assigns = _assigns(_MIGRATION_PATH)
        assert assigns.get("down_revision") == "d8e1f4a92c60"
        assert assigns.get("revision") == "f4a8b2c91e73"

    def test_no_other_migration_also_chains_onto_the_same_parent(self) -> None:
        offenders = []
        for path in _VERSIONS_DIR.glob("*.py"):
            if path == _MIGRATION_PATH:
                continue
            if _assigns(path).get("down_revision") == "d8e1f4a92c60":
                offenders.append(path.name)
        assert offenders == []


@pytest.mark.unit
class TestMigrationTableAndIndexes:
    def test_upgrade_creates_takeover_intents_table(self) -> None:
        tree = ast.parse(_MIGRATION_PATH.read_text())
        upgrade_fn = next(
            n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "upgrade"
        )
        assert _created_table_names(upgrade_fn) == {"takeover_intents"}

    def test_upgrade_creates_expected_indexes(self) -> None:
        tree = ast.parse(_MIGRATION_PATH.read_text())
        upgrade_fn = next(
            n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "upgrade"
        )
        assert _index_names(upgrade_fn) == {
            "ix_takeover_intents_region_id_status",
            "ix_takeover_intents_expires_at",
            "ix_takeover_intents_region_id_pending",
        }

    def test_check_constraint_lists_all_six_status_values(self) -> None:
        source = _MIGRATION_PATH.read_text()
        for value in _EXPECTED_STATUS_VALUES:
            assert f"'{value}'" in source


@pytest.mark.unit
class TestTakeoverIntentModelTable:
    def test_tablename_and_column_set(self) -> None:
        assert TakeoverIntent.__tablename__ == "takeover_intents"
        column_names = {c.name for c in TakeoverIntent.__table__.columns}
        assert column_names == _EXPECTED_COLUMNS

    def test_status_default_server_and_python(self) -> None:
        col = TakeoverIntent.__table__.c.status
        assert col.server_default.arg == "pending"
        assert col.default.arg is TakeoverIntentStatus.PENDING


@pytest.mark.unit
class TestSqliteRoundTrip:
    @pytest.fixture()
    def mirror(self):
        meta = MetaData()
        table = Table(
            "takeover_intents_mirror",
            meta,
            Column("id", TakeoverIntent.__table__.c.id.type, primary_key=True),
            Column("region_id", TakeoverIntent.__table__.c.region_id.type, nullable=False),
            Column(
                "caller_user_id",
                TakeoverIntent.__table__.c.caller_user_id.type,
                nullable=False,
            ),
            Column(
                "approval_url",
                TakeoverIntent.__table__.c.approval_url.type,
                nullable=False,
            ),
            Column("status", TakeoverIntent.__table__.c.status.type, nullable=False),
            Column("expires_at", String(), nullable=False),
            Column("completed_at", String(), nullable=True),
        )
        eng = create_engine("sqlite:///:memory:")
        meta.create_all(eng)
        return eng, table

    def test_pending_intent_round_trips(self, mirror) -> None:
        eng, table = mirror
        intent_id = uuid.uuid4()
        region_id = uuid.uuid4()
        user_id = uuid.uuid4()
        expires = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        with eng.begin() as conn:
            conn.execute(
                table.insert().values(
                    id=intent_id,
                    region_id=region_id,
                    caller_user_id=user_id,
                    approval_url="https://paypal.example/approve/abc",
                    status=TakeoverIntentStatus.PENDING.value,
                    expires_at=expires,
                    completed_at=None,
                )
            )
            row = conn.execute(table.select().where(table.c.id == intent_id)).first()
        assert row.status == "pending"
        assert row.approval_url == "https://paypal.example/approve/abc"
        assert row.completed_at is None
