"""WO-P8-region-lifecycle-schema -- pins the RegionStatus 7-state canon
set (DATA_MODELS/galaxy.md:89, SYSTEMS/region-lifecycle.md:669,790) and
the b7e4a29f1c68 lifecycle-columns migration chain, DB-free (no local
Postgres on the Mac). Convention follows test_phantom_table_catchup.py:
AST-parse migration source for revision metadata + column set rather than
attempt a live schema-diff.

Real-DB apply proof (does ALTER TABLE regions ADD COLUMN ... actually
succeed against Postgres) is NOT reachable locally -- the CI
ci-schema-parity gate is the authoritative apply proof on push.
"""
from __future__ import annotations

import ast
import pathlib

import pytest

from src.models.region import RegionStatus

_MIGRATION_PATH = (
    pathlib.Path(__file__).resolve().parents[2]
    / "alembic" / "versions"
    / "b7e4a29f1c68_region_lifecycle_columns.py"
)
_VERSIONS_DIR = _MIGRATION_PATH.parent

_EXPECTED_STATUS_VALUES = {
    "active",
    "suspended",
    "grace",
    "terminated",
    "pending",
    "generation_corrupt",
    "attachment_pending",
}

_EXPECTED_NEW_COLUMNS = {
    "suspended_at",
    "terminated_at",
    "scheduled_hard_delete_at",
    "generation_seed",
    "generation_phase_checksums",
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


def _added_column_names(fn: ast.FunctionDef) -> set:
    names = set()
    for node in ast.walk(fn):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "add_column"
        ):
            # op.add_column('regions', sa.Column('suspended_at', ...))
            for arg in node.args:
                if (
                    isinstance(arg, ast.Call)
                    and isinstance(arg.func, ast.Attribute)
                    and arg.func.attr == "Column"
                    and arg.args
                    and isinstance(arg.args[0], ast.Constant)
                ):
                    names.add(arg.args[0].value)
    return names


def _dropped_column_names(fn: ast.FunctionDef) -> set:
    names = set()
    for node in ast.walk(fn):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "drop_column"
        ):
            # op.drop_column('regions', 'suspended_at')
            if len(node.args) >= 2 and isinstance(node.args[1], ast.Constant):
                names.add(node.args[1].value)
    return names


@pytest.mark.unit
class TestRegionStatusSevenStateCanon:
    def test_exactly_the_seven_canon_members(self) -> None:
        actual = {member.value for member in RegionStatus}
        assert actual == _EXPECTED_STATUS_VALUES
        assert len(RegionStatus) == 7

    def test_attachment_pending_present(self) -> None:
        # galaxy.md:89, region-lifecycle.md:669,790 (Phase 14 retry
        # exhaustion) -- the member the original brief omitted.
        assert RegionStatus.ATTACHMENT_PENDING.value == "attachment_pending"

    def test_original_four_states_still_present(self) -> None:
        assert RegionStatus.ACTIVE.value == "active"
        assert RegionStatus.SUSPENDED.value == "suspended"
        assert RegionStatus.TERMINATED.value == "terminated"
        assert RegionStatus.PENDING.value == "pending"


@pytest.mark.unit
class TestMigrationChainIntegrity:
    def test_migration_file_exists(self) -> None:
        assert _MIGRATION_PATH.is_file()

    def test_down_revision_is_the_confirmed_head(self) -> None:
        assigns = _assigns(_MIGRATION_PATH)
        assert assigns.get("down_revision") == "9f1e216e2321"
        assert assigns.get("revision") == "b7e4a29f1c68"

    def test_no_other_migration_also_chains_onto_the_same_parent(self) -> None:
        """A second file with down_revision == '9f1e216e2321' would fork
        the history into two heads -- the durable, no-live-DB-needed
        regression pin for "single head"."""
        offenders = []
        for path in _VERSIONS_DIR.glob("*.py"):
            if path == _MIGRATION_PATH:
                continue
            if _assigns(path).get("down_revision") == "9f1e216e2321":
                offenders.append(path.name)
        assert offenders == []


@pytest.mark.unit
class TestMigrationColumnSet:
    def test_upgrade_adds_exactly_the_five_expected_columns(self) -> None:
        tree = ast.parse(_MIGRATION_PATH.read_text())
        upgrade_fn = next(
            n for n in tree.body
            if isinstance(n, ast.FunctionDef) and n.name == "upgrade"
        )
        assert _added_column_names(upgrade_fn) == _EXPECTED_NEW_COLUMNS

    def test_downgrade_drops_exactly_the_five_expected_columns(self) -> None:
        tree = ast.parse(_MIGRATION_PATH.read_text())
        downgrade_fn = next(
            n for n in tree.body
            if isinstance(n, ast.FunctionDef) and n.name == "downgrade"
        )
        assert _dropped_column_names(downgrade_fn) == _EXPECTED_NEW_COLUMNS

    def test_generation_seed_is_not_null_false(self) -> None:
        """Additive-only guard: generation_seed must ship nullable despite
        canon marking it NOT NULL (galaxy.md:93) -- a NOT NULL add against
        existing region rows with no seed on record would be destructive."""
        source = _MIGRATION_PATH.read_text()
        tree = ast.parse(source)
        upgrade_fn = next(
            n for n in tree.body
            if isinstance(n, ast.FunctionDef) and n.name == "upgrade"
        )
        upgrade_src = ast.get_source_segment(source, upgrade_fn) or ""
        # crude but effective: the generation_seed add_column call must be
        # followed by nullable=True before the next add_column call.
        seed_idx = upgrade_src.index("generation_seed")
        next_nullable_idx = upgrade_src.index("nullable=", seed_idx)
        segment = upgrade_src[next_nullable_idx:next_nullable_idx + len("nullable=True")]
        assert segment == "nullable=True"
