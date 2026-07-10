"""WO-QTI-PHANTOM-TABLE-CATCHUP -- 9f1e216e2321 makes the migration chain
authoritative for the 8 tables that previously existed ONLY via startup
Base.metadata.create_all (docking.py's DockingSlipOccupancy/
DockingQueueEntry, port_ownership.py's StationListing/PurchaseOffer/
TakeoverCampaign, player_analytics.py's PlayerSession/
PlayerAnalyticsSnapshot/PlayerActivity).

Real-DB table-creation proof (does create_all(tables=[...]) actually
succeed against Postgres) is NOT reachable on the Mac -- no local Postgres,
and several of the 8 tables (TakeoverCampaign.monthly_history, PlayerSession.
sectors_visited, PlayerAnalyticsSnapshot's four JSONB columns, PlayerActivity.
items_involved/activity_metadata) declare Postgres-only JSONB columns, which
also rules out a real-SQLite proof the way sibling savepoint tests in this
suite use for Postgres-only-type-free models (UnsupportedCompilationError,
same class that blocks Player.__table__.create() on SQLite elsewhere in this
suite). THE authoritative proof for actual table creation is the CI
ci-schema-parity gate the orchestrator watches after this ships -- said
explicitly here rather than oversold. What IS provable locally, and pinned
below: the migration chains correctly onto the current single head, and its
upgrade()/downgrade() reference EXACTLY the 8 target tables -- not 9, and
specifically not player_re_engagement_queue (which already has its own
migration, c9f2e7a41d83, and must stay excluded).
"""
from __future__ import annotations

import ast
import importlib.util
import pathlib
import types

import pytest

_MIGRATION_PATH = (
    pathlib.Path(__file__).resolve().parents[2]
    / "alembic" / "versions"
    / "9f1e216e2321_phantom_table_catchup_docking_port_.py"
)
_VERSIONS_DIR = _MIGRATION_PATH.parent

_EXPECTED_TABLE_NAMES = {
    "docking_slip_occupancies",
    "docking_queue_entries",
    "station_listings",
    "station_purchase_offers",
    "station_takeover_campaigns",
    "player_sessions",
    "player_analytics_snapshots",
    "player_activities",
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


def _load_migration_module() -> types.ModuleType:
    spec = importlib.util.spec_from_file_location(
        "phantom_table_catchup_9f1e216e2321", _MIGRATION_PATH,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.unit
class TestMigrationChainIntegrity:
    def test_migration_file_exists(self) -> None:
        assert _MIGRATION_PATH.is_file()

    def test_down_revision_is_the_current_head(self) -> None:
        assigns = _assigns(_MIGRATION_PATH)
        assert assigns.get("down_revision") == "7643ee82d04b"
        assert assigns.get("revision") == "9f1e216e2321"

    def test_no_other_migration_also_chains_onto_the_same_parent(self) -> None:
        """A second file with down_revision == '7643ee82d04b' would fork
        the history into two heads -- the durable, no-live-DB-needed
        regression pin for "single head"."""
        offenders = []
        for path in _VERSIONS_DIR.glob("*.py"):
            if path == _MIGRATION_PATH:
                continue
            if _assigns(path).get("down_revision") == "7643ee82d04b":
                offenders.append(path.name)
        assert offenders == []


@pytest.mark.unit
class TestCatchupTableSet:
    def test_exactly_the_eight_target_tables_no_more_no_fewer(self) -> None:
        module = _load_migration_module()
        actual_names = {t.name for t in module._CATCHUP_TABLES}
        assert actual_names == _EXPECTED_TABLE_NAMES
        assert len(module._CATCHUP_TABLES) == 8

    def test_player_re_engagement_queue_is_never_referenced(self) -> None:
        """player_re_engagement_queue already has its own migration
        (c9f2e7a41d83) -- this migration must not create or drop it, and
        must not even import its model class."""
        module = _load_migration_module()
        actual_names = {t.name for t in module._CATCHUP_TABLES}
        assert "player_re_engagement_queue" not in actual_names
        assert not hasattr(module, "PlayerReEngagement")

    def test_no_native_enum_columns_among_the_catchup_tables(self) -> None:
        """Census pin: none of the 8 tables declare a SQLAlchemy Enum
        column (every status-like field is a plain String), so create_all
        has no PG enum-type dependency to resolve for this batch --
        verified structurally, not just asserted in the docstring."""
        from sqlalchemy import Enum as SAEnum

        module = _load_migration_module()
        for table in module._CATCHUP_TABLES:
            for column in table.columns:
                assert not isinstance(column.type, SAEnum), (
                    f"{table.name}.{column.name} is a native Enum column -- "
                    "the migration's docstring claims none exist"
                )


@pytest.mark.unit
class TestUpgradeDowngradeShape:
    def test_upgrade_uses_create_all_with_checkfirst_true(self) -> None:
        source = _MIGRATION_PATH.read_text()
        tree = ast.parse(source)
        upgrade_fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "upgrade")
        upgrade_src = ast.get_source_segment(source, upgrade_fn) or ""
        assert "Base.metadata.create_all(" in upgrade_src
        assert "checkfirst=True" in upgrade_src
        assert "tables=_CATCHUP_TABLES" in upgrade_src

    def test_downgrade_uses_drop_all_with_checkfirst_true(self) -> None:
        source = _MIGRATION_PATH.read_text()
        tree = ast.parse(source)
        downgrade_fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "downgrade")
        downgrade_src = ast.get_source_segment(source, downgrade_fn) or ""
        assert "Base.metadata.drop_all(" in downgrade_src
        assert "checkfirst=True" in downgrade_src
        assert "tables=_CATCHUP_TABLES" in downgrade_src

    def test_upgrade_and_downgrade_never_touch_player_re_engagement_queue_by_name(self) -> None:
        """Scoped to imports + upgrade()/downgrade() source only -- the
        module's own docstring legitimately NAMES player_re_engagement_queue/
        PlayerReEngagement to document the exclusion, so a whole-file
        substring check would false-fail on the documentation itself."""
        source = _MIGRATION_PATH.read_text()
        tree = ast.parse(source)
        import_lines = [
            ast.get_source_segment(source, n) or ""
            for n in tree.body
            if isinstance(n, (ast.Import, ast.ImportFrom))
        ]
        upgrade_fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "upgrade")
        downgrade_fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "downgrade")
        code_src = "\n".join(import_lines) + (ast.get_source_segment(source, upgrade_fn) or "") + (
            ast.get_source_segment(source, downgrade_fn) or ""
        )
        assert "player_re_engagement_queue" not in code_src
        assert "PlayerReEngagement" not in code_src
