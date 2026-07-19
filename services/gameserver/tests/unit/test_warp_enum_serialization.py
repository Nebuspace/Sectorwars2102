"""WO-SWEEP-WARPLAYER-ENUM: regression pin for the warp-knowledge enum
name-vs-value serialization defect (P1 gameplay blocker).

PREMISE (confirmed live via the orchestrator's fresh-stage sweep + a local
repro against a real SQLite-backed table before this fix): a plain
SQLAlchemy ``Enum(PyEnum)`` column serializes the Python member NAME by
default (e.g. ``WarpLayer.WARP_TUNNELS`` -> the string ``"WARP_TUNNELS"``),
but migration ``f1a4d7b2c9e3`` built the three Postgres enum TYPES from the
lowercase VALUES (``'sector_warps'``, ``'warp_tunnels'``, ...). Every write
and every enum-compared read on ``player_warp_knowledge`` failed against a
real (values-built) Postgres DB with ``invalid input value for enum`` --
POST /player/move/{id} -> 500 on a fresh stage DB. A ``create_all``-era dev
DB (name-built, matching whatever the model said at the time) masked this
indefinitely -- a phantom-defect class specific to hand-authored migrations
whose enum type predates a later model change.

Fix: ``values_callable=lambda obj: [e.value for e in obj]`` added to all
three enum columns (``warp_layer``, ``visibility_state``, ``revealed_via``),
matching the established pattern already used elsewhere in this codebase
(``bounty_claim.py``, ``faction.py``). Python-side member names are
completely unchanged -- every existing call site keeps working.

This file proves three things:

1. ``TestColumnSerializationMatchesMigration`` -- each column's *serialized
   string set* (``Column.type.enums``, what ``values_callable`` actually
   produced) is byte-identical to migration f1a4d7b2c9e3's own
   WARP_LAYER/WARP_VISIBILITY_STATE/WARP_REVEALED_VIA tuples, restated
   literally here (not imported -- alembic revision modules are not import
   targets from application/test code) with a comment naming the migration.
2. ``TestSqliteRoundTrip`` -- a REAL round-trip against an isolated, in-
   memory SQLite table (``PlayerWarpKnowledge.__table__.create(engine)``,
   not ``Base.metadata.create_all`` -- the shared declarative base pulls in
   every model in the app, many with Postgres-only types unrelated to this
   table): insert a row via the ORM, then read the RAW stored strings back
   with a textual SELECT (bypassing the ORM's own enum coercion entirely),
   proving the actual bytes that would hit a database are lowercase
   values -- and separately confirms ORM read-back still resolves to the
   correct enum member (the fix must not break normal application code).
3. ``TestNoColumnSerializesUppercaseNames`` -- the literal structural
   regression pin: none of the three columns' serialized sets may ever
   contain an uppercase member NAME. This is the exact assertion that
   would have caught the shipped defect before it ever reached a real DB.
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from src.models.player_warp_knowledge import (
    PlayerWarpKnowledge,
    WarpLayer,
    WarpRevealedVia,
    WarpVisibilityState,
)

# Restated literally from alembic/versions/f1a4d7b2c9e3_player_warp_knowledge.py
# (the migration that built the LIVE Postgres enum TYPES) -- NOT imported,
# since alembic revision modules are not meant to be import targets from
# application/test code. That migration is additive-only and its own
# docstring says so; these tuples should never need to change independently
# of it.
MIGRATION_WARP_LAYER = ('sector_warps', 'warp_tunnels')
MIGRATION_WARP_VISIBILITY_STATE = ('hidden', 'revealed', 'traversed')
MIGRATION_WARP_REVEALED_VIA = ('scan', 'traversal_attempt', 'corp_share', 'aria_inference')


def _serialized_values(column) -> list:
    """The exact strings SQLAlchemy will bind to the DB for this enum
    column -- what values_callable actually produced."""
    return list(column.type.enums)


# ---------------------------------------------------------------------------
# 1. Column serialization matches the migration's own enum tuples exactly
# ---------------------------------------------------------------------------

class TestColumnSerializationMatchesMigration:
    def test_warp_layer_matches_migration_tuple_exactly(self):
        col = PlayerWarpKnowledge.__table__.c.warp_layer
        assert _serialized_values(col) == list(MIGRATION_WARP_LAYER)

    def test_visibility_state_matches_migration_tuple_exactly(self):
        col = PlayerWarpKnowledge.__table__.c.visibility_state
        assert _serialized_values(col) == list(MIGRATION_WARP_VISIBILITY_STATE)

    def test_revealed_via_matches_migration_tuple_exactly(self):
        col = PlayerWarpKnowledge.__table__.c.revealed_via
        assert _serialized_values(col) == list(MIGRATION_WARP_REVEALED_VIA)


# ---------------------------------------------------------------------------
# 2. Real SQLite round-trip -- proves the actual bytes are lowercase, and
# that ORM read-back still works.
# ---------------------------------------------------------------------------

class TestSqliteRoundTrip:
    @pytest.fixture()
    def engine(self):
        # An isolated single-table create (NOT Base.metadata.create_all --
        # that would attempt every model in the shared declarative base,
        # many with Postgres-only types this table doesn't need). Verified
        # live: PlayerWarpKnowledge.__table__.create() against sqlite:///:memory:
        # succeeds cleanly (SQLite has no native UUID/ENUM types, but
        # SQLAlchemy's generic column-type fallback handles both).
        eng = create_engine("sqlite:///:memory:")
        PlayerWarpKnowledge.__table__.create(eng)
        return eng

    def test_raw_stored_strings_are_lowercase_values_not_uppercase_names(self, engine):
        row_id = uuid.uuid4()
        with Session(engine) as session:
            session.add(PlayerWarpKnowledge(
                id=row_id, player_id=uuid.uuid4(), warp_layer=WarpLayer.WARP_TUNNELS,
                warp_id=uuid.uuid4(), visibility_state=WarpVisibilityState.TRAVERSED,
                revealed_via=WarpRevealedVia.TRAVERSAL_ATTEMPT,
            ))
            session.commit()

            # Bypasses the ORM's own enum coercion entirely -- this is what
            # is ACTUALLY sitting in the database, byte for byte.
            raw = session.execute(text(
                "SELECT warp_layer, visibility_state, revealed_via FROM player_warp_knowledge"
            )).first()

        assert raw == ("warp_tunnels", "traversed", "traversal_attempt")

    def test_orm_read_back_resolves_to_the_correct_enum_members(self, engine):
        row_id = uuid.uuid4()
        with Session(engine) as session:
            session.add(PlayerWarpKnowledge(
                id=row_id, player_id=uuid.uuid4(), warp_layer=WarpLayer.SECTOR_WARPS,
                warp_id=uuid.uuid4(), visibility_state=WarpVisibilityState.REVEALED,
                revealed_via=WarpRevealedVia.CORP_SHARE,
            ))
            session.commit()
            session.expire_all()

            fetched = session.get(PlayerWarpKnowledge, row_id)

        assert fetched.warp_layer is WarpLayer.SECTOR_WARPS
        assert fetched.visibility_state is WarpVisibilityState.REVEALED
        assert fetched.revealed_via is WarpRevealedVia.CORP_SHARE

    def test_every_warp_layer_member_round_trips(self, engine):
        """Every WarpLayer member (not just the one exercised above) must
        round-trip cleanly -- a positive check per enum value."""
        with Session(engine) as session:
            for layer in WarpLayer:
                session.add(PlayerWarpKnowledge(
                    id=uuid.uuid4(), player_id=uuid.uuid4(), warp_layer=layer,
                    warp_id=uuid.uuid4(), visibility_state=WarpVisibilityState.REVEALED,
                    revealed_via=WarpRevealedVia.SCAN,
                ))
            session.commit()

            raw_layers = {
                row[0] for row in session.execute(text("SELECT warp_layer FROM player_warp_knowledge")).all()
            }
        assert raw_layers == {layer.value for layer in WarpLayer}


# ---------------------------------------------------------------------------
# 3. The literal regression pin -- no column may ever serialize an
# uppercase member NAME.
# ---------------------------------------------------------------------------

class TestNoColumnSerializesUppercaseNames:
    """This is the exact assertion that would have caught the shipped
    defect (bare Enum(PyEnum) serializes "WARP_TUNNELS" /
    "TRAVERSAL_ATTEMPT" / etc.) before it ever reached a real Postgres DB."""

    @pytest.mark.parametrize("column_name,enum_cls", [
        ("warp_layer", WarpLayer),
        ("visibility_state", WarpVisibilityState),
        ("revealed_via", WarpRevealedVia),
    ])
    def test_no_uppercase_member_name_in_serialized_set(self, column_name, enum_cls):
        column = PlayerWarpKnowledge.__table__.c[column_name]
        serialized = _serialized_values(column)
        member_names = [m.name for m in enum_cls]

        for name in member_names:
            assert name not in serialized, (
                f"{column_name} serializes the uppercase NAME {name!r} -- "
                f"values_callable is missing or broken"
            )

        # Positive check: every serialized string IS one of the enum's
        # lowercase .value forms, in member-declaration order.
        assert serialized == [m.value for m in enum_cls]
