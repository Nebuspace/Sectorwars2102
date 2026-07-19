"""WO-SWEEP-ARIA-MI-COLUMN — two independent pins:

1. The new migration (852befb04227) chains correctly onto the current
   single head (4299dadf325b) and does not introduce a second head. AST-
   inspected (mirrors test_formation_knowledge.py's own
   TestMigrationAdditiveOnly convention) rather than importing the
   revision module directly — alembic revision filenames are not valid
   Python identifiers (leading digit), so a plain `import` is not an
   option; ``alembic heads``/``alembic history`` were also verified live
   against this migration during development (see the WO's own STATUS).

2. record_market_observation_sync's per-commodity SAVEPOINT actually
   isolates a failing write: a real SQLite in-memory Session backs
   ARIAMarketIntelligence specifically (SQLite supports begin_nested/
   SAVEPOINT — the WO's own suggested approach; ARIAMarketIntelligence
   carries no Postgres-only column types, so its isolated single-table
   create works cleanly). Player/Station are NOT also created in this
   SQLite engine -- both carry genuine Postgres-only types (Player.
   reputation is a postgresql.JSONB column; verified live,
   Player.__table__.create() against sqlite:///:memory: raises
   UnsupportedCompilationError) -- so a thin _HybridSession wrapper serves
   Player/Station queries from fixed in-memory rows while delegating
   every ARIAMarketIntelligence query, add(), begin_nested(), flush(), and
   commit() straight through to the real SQLite Session.
"""
from __future__ import annotations

import ast
import pathlib
import uuid
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from src.models.aria_personal_intelligence import ARIAMarketIntelligence
from src.models.player import Player
from src.models.station import Station
from src.services.aria_personal_intelligence_service import ARIAPersonalIntelligenceService


class _FixedFirstQuery:
    """db.query(Model).filter(...).first() -- always returns the same
    fixed row regardless of the filter clause (this hybrid session only
    ever needs ONE Player and ONE Station per test)."""

    def __init__(self, row):
        self._row = row

    def filter(self, *conditions):
        return self

    def first(self):
        return self._row


class _HybridSession:
    """Serves Player/Station queries from fixed rows (see module
    docstring for why their real tables can't live in this SQLite engine);
    everything else — ARIAMarketIntelligence queries, add(), begin_nested(),
    flush(), commit() — delegates straight through to the real underlying
    SQLite Session, so record_market_observation_sync's actual SAVEPOINT
    behavior is exercised for real, not faked."""

    def __init__(self, real_session, *, player, station):
        self._real = real_session
        self._player = player
        self._station = station

    def query(self, model):
        if model is Player:
            return _FixedFirstQuery(self._player)
        if model is Station:
            return _FixedFirstQuery(self._station)
        return self._real.query(model)

    def add(self, obj):
        self._real.add(obj)

    def begin_nested(self):
        return self._real.begin_nested()

    def flush(self):
        self._real.flush()

    def commit(self):
        self._real.commit()

# --------------------------------------------------------------------------- #
# 1. Migration chain integrity
# --------------------------------------------------------------------------- #

_MIGRATION_PATH = (
    pathlib.Path(__file__).resolve().parents[2]
    / "alembic" / "versions"
    / "852befb04227_rename_aria_market_intelligence_port_id_to_station_id.py"
)
_VERSIONS_DIR = _MIGRATION_PATH.parent


@pytest.mark.unit
class TestMigrationChainIntegrity:
    def test_migration_file_exists(self) -> None:
        assert _MIGRATION_PATH.is_file()

    def _assigns(self) -> dict:
        source = _MIGRATION_PATH.read_text()
        tree = ast.parse(source)
        return {
            n.targets[0].id: n.value.value
            for n in tree.body
            if isinstance(n, ast.Assign)
            and isinstance(n.targets[0], ast.Name)
            and isinstance(n.value, ast.Constant)
        }

    def test_down_revision_is_the_current_head(self) -> None:
        assigns = self._assigns()
        assert assigns.get("down_revision") == "4299dadf325b"
        assert assigns.get("revision") == "852befb04227"

    def test_no_other_migration_also_chains_onto_the_same_parent(self) -> None:
        """A second file with down_revision == '4299dadf325b' would fork
        the history into two heads — this is the durable, no-live-DB-
        needed regression pin for "single head" the WO calls for."""
        offenders = []
        for path in _VERSIONS_DIR.glob("*.py"):
            if path == _MIGRATION_PATH:
                continue
            tree = ast.parse(path.read_text())
            assigns = {
                n.targets[0].id: n.value.value
                for n in tree.body
                if isinstance(n, ast.Assign)
                and isinstance(n.targets[0], ast.Name)
                and isinstance(n.value, ast.Constant)
            }
            if assigns.get("down_revision") == "4299dadf325b":
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
        # Constraint left alone on the rename path — only conditionally
        # added on the defensive "neither column" fallback.
        assert "uq_player_port_commodity" in upgrade_src

    def test_downgrade_reverses_the_rename_only_guarded(self) -> None:
        source = _MIGRATION_PATH.read_text()
        tree = ast.parse(source)
        downgrade_fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "downgrade")
        downgrade_src = (ast.get_source_segment(source, downgrade_fn) or "").lower()
        assert "rename column station_id to port_id" in downgrade_src


# --------------------------------------------------------------------------- #
# 2. Savepoint isolation — real SQLite, real begin_nested()
# --------------------------------------------------------------------------- #

@pytest.mark.unit
class TestSavepointIsolation:
    @pytest.fixture()
    def engine(self):
        # Isolated SINGLE-table create -- ARIAMarketIntelligence carries no
        # Postgres-only column types (see module docstring for why Player/
        # Station can't join it in this same SQLite engine).
        eng = create_engine("sqlite:///:memory:")
        ARIAMarketIntelligence.__table__.create(eng)
        return eng

    def _hybrid_db(self, real_session, *, player_id, station_id, sector_uuid):
        # SimpleNamespace, not real Player/Station ORM instances -- neither
        # object is ever written to a real table in this test (the hybrid
        # session serves them as fixed in-memory rows), and Player.username
        # is a read-only computed property (not a settable Column), so a
        # real Player(...) construction isn't viable here anyway. Only the
        # attributes _validate_player_at_port_sync / record_market_
        # observation_sync actually read are needed.
        player = SimpleNamespace(
            id=player_id, is_docked=True, current_sector_id=5,
        )
        station = SimpleNamespace(
            id=station_id, sector_id=5, sector_uuid=sector_uuid,
        )
        return _HybridSession(real_session, player=player, station=station)

    def test_one_failing_commodity_never_poisons_the_others_or_the_outer_commit(self, engine):
        """The WO's own falsifier: simulate a failing MI write for ONE
        commodity in a multi-commodity station-visit payload, and assert
        (a) the OTHER commodities still got recorded, (b) the outer
        session is still usable afterward — no PendingRollbackError on the
        caller's own subsequent commit (trading.py's dock/market-view
        route folds this into its single request commit)."""
        player_id = uuid.uuid4()
        station_id = uuid.uuid4()
        sector_uuid = uuid.uuid4()

        with Session(engine) as real_session:
            db = self._hybrid_db(
                real_session, player_id=player_id, station_id=station_id,
                sector_uuid=sector_uuid,
            )

            service = ARIAPersonalIntelligenceService()
            market_prices = [
                {"commodity": "ORE", "price": 10.0, "quantity": 5},
                {"commodity": "FUEL", "price": 20.0, "quantity": 3},
                {"commodity": "ORGANICS", "price": 30.0, "quantity": 1},
            ]

            # Simulate a DB-level failure isolated to the SECOND commodity
            # (FUEL) — _calculate_intelligence_quality is the last thing
            # record_market_observation_sync calls before db.flush() inside
            # each commodity's savepoint, a clean, deterministic injection
            # point that doesn't require engineering a real SQL constraint
            # violation to prove the SAME isolation contract.
            real_calc = service._calculate_intelligence_quality
            call_count = {"n": 0}

            def _boom_on_second_call(*args, **kwargs):
                call_count["n"] += 1
                if call_count["n"] == 2:
                    raise RuntimeError("simulated DB-level failure for this commodity")
                return real_calc(*args, **kwargs)

            with patch.object(
                service, "_calculate_intelligence_quality", side_effect=_boom_on_second_call,
            ):
                # UUID objects, not str(...) -- record_market_observation_sync's
                # own real callers (trading.py) pass strings, which Postgres/
                # psycopg2 coerce transparently for a UUID column; SQLite's
                # generic UUID(as_uuid=True) binding processor requires a real
                # uuid.UUID object (verified: a str argument here raises
                # AttributeError: 'str' object has no attribute 'hex' at bind
                # time). The str-vs-UUID coercion itself is exercised
                # elsewhere (test_aria_market_observation.py, against the
                # fake session that mirrors the real string-typed call
                # convention) -- this test's own job is proving SAVEPOINT
                # isolation, which is agnostic to that distinction.
                service.record_market_observation_sync(
                    player_id, station_id, market_prices, db,
                )

            # The outer transaction must still be usable -- this is the
            # falsifier: an unguarded failure would leave the session
            # poisoned and THIS commit would raise PendingRollbackError.
            db.commit()

            recorded = {
                row.commodity
                for row in real_session.query(ARIAMarketIntelligence)
                .filter(ARIAMarketIntelligence.player_id == player_id)
                .all()
            }

        assert recorded == {"ORE", "ORGANICS"}  # FUEL (2nd call) rolled back
        assert call_count["n"] == 3  # all three commodities were attempted

    def test_all_commodities_succeed_when_nothing_fails(self, engine):
        """Companion happy-path pin — the savepoint wrapping introduces no
        behavior change when nothing raises."""
        player_id = uuid.uuid4()
        station_id = uuid.uuid4()
        sector_uuid = uuid.uuid4()

        with Session(engine) as real_session:
            db = self._hybrid_db(
                real_session, player_id=player_id, station_id=station_id,
                sector_uuid=sector_uuid,
            )
            service = ARIAPersonalIntelligenceService()
            # UUID objects, not str(...) -- see the sibling test's comment.
            service.record_market_observation_sync(
                player_id, station_id,
                [
                    {"commodity": "ORE", "price": 10.0, "quantity": 5},
                    {"commodity": "FUEL", "price": 20.0, "quantity": 3},
                ],
                db,
            )
            db.commit()

            recorded = {
                row.commodity
                for row in real_session.query(ARIAMarketIntelligence)
                .filter(ARIAMarketIntelligence.player_id == player_id)
                .all()
            }

        assert recorded == {"ORE", "FUEL"}
