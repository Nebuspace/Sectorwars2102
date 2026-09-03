"""LEG-4177: pin PirateHolding.outlaw_base_id + e9b4c2a71d60 migration.

DB-free AST pins follow test_takeover_intent_model.py. Live apply proof is
CI ci-schema-parity on push. Does not implement OutlawBase→NPCBarracks
conversion.
"""
from __future__ import annotations

import ast
import pathlib

import pytest

from src.models.pirate_holding import PirateHolding, PirateHoldingTier

_MIGRATION_PATH = (
    pathlib.Path(__file__).resolve().parents[2]
    / "alembic"
    / "versions"
    / "e9b4c2a71d60_pirate_holdings_outlaw_base_id.py"
)
_VERSIONS_DIR = _MIGRATION_PATH.parent


def _assigns(path: pathlib.Path) -> dict:
    tree = ast.parse(path.read_text())
    return {
        n.targets[0].id: n.value.value
        for n in tree.body
        if isinstance(n, ast.Assign)
        and isinstance(n.targets[0], ast.Name)
        and isinstance(n.value, ast.Constant)
    }


def _upgrade_fn(path: pathlib.Path) -> ast.FunctionDef:
    tree = ast.parse(path.read_text())
    return next(
        n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "upgrade"
    )


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


def _added_column_names(fn: ast.FunctionDef) -> set[str]:
    names = set()
    for node in ast.walk(fn):
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "add_column"
        ):
            continue
        for inner in ast.walk(node):
            if (
                isinstance(inner, ast.Call)
                and isinstance(inner.func, ast.Attribute)
                and inner.func.attr == "Column"
                and inner.args
                and isinstance(inner.args[0], ast.Constant)
            ):
                names.add(inner.args[0].value)
    return names


@pytest.mark.unit
class TestMigrationChainIntegrity:
    def test_migration_file_exists(self) -> None:
        assert _MIGRATION_PATH.is_file()

    def test_down_revision_is_the_confirmed_head(self) -> None:
        assigns = _assigns(_MIGRATION_PATH)
        assert assigns.get("down_revision") == "c3e8f1a92b70"
        assert assigns.get("revision") == "e9b4c2a71d60"

    def test_no_other_migration_also_chains_onto_the_same_parent(self) -> None:
        offenders = []
        for path in _VERSIONS_DIR.glob("*.py"):
            if path == _MIGRATION_PATH:
                continue
            if _assigns(path).get("down_revision") == "c3e8f1a92b70":
                offenders.append(path.name)
        assert offenders == []


@pytest.mark.unit
class TestMigrationAddsNullableOutlawBaseFk:
    def test_upgrade_adds_outlaw_base_id_only(self) -> None:
        assert _added_column_names(_upgrade_fn(_MIGRATION_PATH)) == {"outlaw_base_id"}

    def test_upgrade_creates_unique_index(self) -> None:
        assert _index_names(_upgrade_fn(_MIGRATION_PATH)) == {
            "uq_pirate_holdings_outlaw_base_id",
        }

    def test_on_delete_set_null_and_nullable(self) -> None:
        source = _MIGRATION_PATH.read_text()
        assert 'ondelete="SET NULL"' in source
        assert "nullable=True" in source
        assert "nullable=False" not in source


@pytest.mark.unit
class TestPirateHoldingModelColumn:
    def test_outlaw_base_id_is_nullable_uuid_fk(self) -> None:
        col = PirateHolding.__table__.c.outlaw_base_id
        assert col.nullable is True
        fks = list(col.foreign_keys)
        assert len(fks) == 1
        assert fks[0].target_fullname == "outlaw_bases.id"
        assert fks[0].ondelete == "SET NULL"

    def test_unique_index_name(self) -> None:
        names = {idx.name for idx in PirateHolding.__table__.indexes}
        assert "uq_pirate_holdings_outlaw_base_id" in names
        unique = next(
            idx
            for idx in PirateHolding.__table__.indexes
            if idx.name == "uq_pirate_holdings_outlaw_base_id"
        )
        assert unique.unique is True

    def test_construct_without_outlaw_base_id(self) -> None:
        holding = PirateHolding(
            region_id=__import__("uuid").uuid4(),
            sector_id=1,
            tier=PirateHoldingTier.CAMP,
            current_strength=1.0,
        )
        assert holding.outlaw_base_id is None
