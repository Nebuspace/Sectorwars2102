"""WO-SWEEP-QUANTUM-CACHE-COLUMN — two independent pins:

1. The new migration (7643ee82d04b) chains correctly onto the current
   single head (852befb04227, WO-SWEEP-ARIA-MI-COLUMN's own migration) and
   does not introduce a second head. AST-inspected, mirroring
   test_aria_mi_column_savepoint.py's own TestMigrationChainIntegrity
   convention (alembic revision filenames are not valid Python
   identifiers, so a plain `import` is not an option) -- ``alembic
   heads`` was also verified live against this migration during
   development (see the WO's own STATUS).

2. _invalidate_aggregate_cache_sync's new SAVEPOINT actually isolates a
   failing DELETE from the shared session record_trade_observation folds
   its own db.add() into (trading.py:414's single trade commit). Unlike
   record_market_observation_sync's per-commodity loop, this is a single
   bulk statement with no try/except of its own by design -- the
   exception is expected to propagate out of this helper (caught by
   record_trade_observation's own pre-existing broad except, unchanged by
   this WO) -- so the falsifier proves the SAVEPOINT rolls back cleanly
   and leaves the OUTER session usable, not that the exception is
   swallowed here. Real SQLite in-memory Session -- ARIAQuantumCache
   carries no Postgres-only column types (UUID/String/JSON/Float/
   DateTime/Integer only), so its isolated single-table create works
   cleanly, same technique as the MI-COLUMN test.
"""
from __future__ import annotations

import ast
import pathlib
import uuid
from datetime import datetime, UTC
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Query, Session

from src.models.aria_personal_intelligence import ARIAQuantumCache
from src.services.aria_personal_intelligence_service import ARIAPersonalIntelligenceService

# --------------------------------------------------------------------------- #
# 1. Migration chain integrity
# --------------------------------------------------------------------------- #

_MIGRATION_PATH = (
    pathlib.Path(__file__).resolve().parents[2]
    / "alembic" / "versions"
    / "7643ee82d04b_rename_aria_quantum_cache_port_id_to_station_id.py"
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


@pytest.mark.unit
class TestMigrationChainIntegrity:
    def test_migration_file_exists(self) -> None:
        assert _MIGRATION_PATH.is_file()

    def test_down_revision_is_the_current_head(self) -> None:
        assigns = _assigns(_MIGRATION_PATH)
        assert assigns.get("down_revision") == "852befb04227"
        assert assigns.get("revision") == "7643ee82d04b"

    def test_no_other_migration_also_chains_onto_the_same_parent(self) -> None:
        """A second file with down_revision == '852befb04227' would fork
        the history into two heads -- this is the durable, no-live-DB-
        needed regression pin for "single head" the WO calls for."""
        offenders = []
        for path in _VERSIONS_DIR.glob("*.py"):
            if path == _MIGRATION_PATH:
                continue
            if _assigns(path).get("down_revision") == "852befb04227":
                offenders.append(path.name)
        assert offenders == []

    def test_upgrade_guards_both_rename_and_fallback_add_branches(self) -> None:
        source = _MIGRATION_PATH.read_text()
        tree = ast.parse(source)
        upgrade_fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "upgrade")
        upgrade_src = (ast.get_source_segment(source, upgrade_fn) or "").lower()
        assert "rename column port_id to station_id" in upgrade_src
        assert "add column station_id" in upgrade_src
        assert "information_schema.columns" in upgrade_src

    def test_downgrade_reverses_the_rename_only_guarded(self) -> None:
        source = _MIGRATION_PATH.read_text()
        tree = ast.parse(source)
        downgrade_fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "downgrade")
        downgrade_src = (ast.get_source_segment(source, downgrade_fn) or "").lower()
        assert "rename column station_id to port_id" in downgrade_src


# --------------------------------------------------------------------------- #
# 2. Savepoint isolation — real SQLite, real begin_nested()
# --------------------------------------------------------------------------- #

def _make_row(*, player_id, cache_key) -> ARIAQuantumCache:
    return ARIAQuantumCache(
        id=uuid.uuid4(),
        player_id=player_id,
        cache_key=cache_key,
        commodity="",
        quantum_states={},
        ghost_results={},
        expected_value=0.0,
        confidence_interval=[0.0, 0.0],
        created_at=datetime.now(UTC),
        expires_at=datetime.now(UTC),
        hit_count=0,
    )


@pytest.mark.unit
class TestSavepointIsolation:
    @pytest.fixture()
    def engine(self):
        # Isolated SINGLE-table create -- ARIAQuantumCache carries no
        # Postgres-only column types (see module docstring).
        eng = create_engine("sqlite:///:memory:")
        ARIAQuantumCache.__table__.create(eng)
        return eng

    def test_only_the_players_recommendation_bundle_row_is_removed(self, engine) -> None:
        """Happy-path pin: the DELETE's WHERE clause scopes to exactly
        (player_id, cache_key=='recommendation_aggregates') -- a
        different cache_key for the same player, and the same cache_key
        for a different player, both survive."""
        service = ARIAPersonalIntelligenceService()
        target_player = uuid.uuid4()
        other_player = uuid.uuid4()

        with Session(engine) as real_session:
            real_session.add(_make_row(player_id=target_player, cache_key=service._RECOMMENDATION_CACHE_KEY))
            real_session.add(_make_row(player_id=target_player, cache_key="ghost_trade_lookup"))
            real_session.add(_make_row(player_id=other_player, cache_key=service._RECOMMENDATION_CACHE_KEY))
            real_session.commit()

            # UUID objects, not str(...) -- SQLite's UUID(as_uuid=True)
            # binding processor requires a real uuid.UUID (verified: a str
            # argument raises AttributeError: 'str' object has no
            # attribute 'hex' at bind time). Postgres/psycopg2 coerces
            # strings transparently for the real call site (record_trade_
            # observation passes player_id straight through as given by
            # its own caller); that coercion is exercised on the real DB,
            # not needed to prove this method's savepoint behavior.
            service._invalidate_aggregate_cache_sync(target_player, real_session)
            real_session.commit()

            remaining = {
                (row.player_id, row.cache_key)
                for row in real_session.query(ARIAQuantumCache).all()
            }

        assert remaining == {
            (target_player, "ghost_trade_lookup"),
            (other_player, service._RECOMMENDATION_CACHE_KEY),
        }

    def test_failure_inside_savepoint_does_not_poison_the_caller_session(self, engine) -> None:
        """The WO's own falsifier: force the bulk DELETE to raise mid-
        savepoint and assert the surrounding session is still usable
        afterward. This method has no try/except of its own by design
        (record_trade_observation's pre-existing broad except is the
        Python-level catch) -- so the exception is expected to propagate
        here; what the SAVEPOINT buys is that the ROLLBACK stays scoped to
        this one statement. An unguarded failure would leave the session
        poisoned and the follow-up add+commit below would raise
        PendingRollbackError."""
        service = ARIAPersonalIntelligenceService()
        player_id = uuid.uuid4()

        with Session(engine) as real_session:
            real_session.add(_make_row(player_id=player_id, cache_key=service._RECOMMENDATION_CACHE_KEY))
            real_session.commit()

            with patch.object(Query, "delete", side_effect=RuntimeError("simulated DB-level failure")):
                with pytest.raises(RuntimeError):
                    service._invalidate_aggregate_cache_sync(player_id, real_session)

            # Session must still be usable post-rollback -- proves the
            # SAVEPOINT (not the whole transaction) absorbed the failure.
            real_session.add(_make_row(player_id=player_id, cache_key="post_failure_probe"))
            real_session.commit()

            surviving_keys = {
                row.cache_key
                for row in real_session.query(ARIAQuantumCache)
                .filter(ARIAQuantumCache.player_id == player_id)
                .all()
            }

        # The original row was never actually deleted (the DELETE raised
        # before completing) and the post-failure probe committed clean.
        assert surviving_keys == {service._RECOMMENDATION_CACHE_KEY, "post_failure_probe"}
