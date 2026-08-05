"""WO-P7-admin-multiacct-models: round-trip + enum-serialization pin for the
multi-account detection schema (ADR-0056, DATA_MODELS/gameplay.md:161-194).

Same defect class this project hit repeatedly this session (contract.py,
player_warp_knowledge.py): a bare ``Enum(PyEnum)`` column serializes the
Python member NAME by default, but migration d4c8f6a12e93 builds the two
Postgres enum TYPES from lowercase VALUES. This file proves the three things
test_contract_enum_serialization.py proved for contracts:

1. ``TestColumnSerializationMatchesMigration`` -- each enum column's
   serialized string set (``Column.type.enums``) is byte-identical to
   migration d4c8f6a12e93's own SEVERITY_VALUES/ADMIN_DECISION_VALUES
   tuples, restated literally here with a comment naming the migration.
2. ``TestSqliteRoundTrip`` -- a real round-trip against an isolated,
   in-memory SQLite table. ``MultiAccountCluster.__table__`` cannot be
   created standalone on SQLite: its ``signal_summary`` column is a
   Postgres-only ``JSONB`` type with no SQLite compiler support (confirmed
   live -- a ``CompileError``, the same class of problem
   ``posting_stations``/``ARRAY(UUID)`` caused for the contract test).
   Rather than fight that, this builds minimal *mirror* Tables containing
   only the columns relevant to the round-trip, reusing the REAL, LIVE
   ``Column.type`` objects straight off ``MultiAccountCluster.__table__``/
   ``MultiAccountFlag.__table__`` (the exact same values_callable-bearing
   Enum type instances the ORM actually uses -- not reimplementations).
   This proves a cluster row plus two member flag rows round-trip with the
   FK relationship intact, and that the raw stored severity bytes are
   lowercase.
3. ``TestNoColumnSerializesUppercaseNames`` -- the literal regression pin:
   neither of the two enum columns' serialized sets may ever contain an
   uppercase member NAME.
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import Column, ForeignKey, MetaData, Table, create_engine, text

from src.models.multi_account import (
    MultiAccountAdminDecision,
    MultiAccountCluster,
    MultiAccountFlag,
    MultiAccountSeverity,
)

# Restated literally from alembic/versions/d4c8f6a12e93_multi_account_detection_schema.py
# (the migration that built the LIVE Postgres enum TYPES) -- NOT imported,
# since alembic revision modules are not meant to be import targets from
# application/test code. That migration is additive-only and its own
# docstring says so; these tuples should never need to change independently
# of it.
MIGRATION_SEVERITY = ('hard', 'soft')
MIGRATION_ADMIN_DECISION = ('pending', 'confirmed', 'overridden', 'escalated')


def _serialized_values(column) -> list:
    """The exact strings SQLAlchemy will bind to the DB for this enum
    column -- what values_callable actually produced."""
    return list(column.type.enums)


# ---------------------------------------------------------------------------
# 1. Column serialization matches the migration's own enum tuples exactly
# ---------------------------------------------------------------------------

class TestColumnSerializationMatchesMigration:
    def test_cluster_severity_matches_migration_tuple_exactly(self):
        col = MultiAccountCluster.__table__.c.severity
        assert _serialized_values(col) == list(MIGRATION_SEVERITY)

    def test_flag_severity_matches_migration_tuple_exactly(self):
        col = MultiAccountFlag.__table__.c.severity
        assert _serialized_values(col) == list(MIGRATION_SEVERITY)

    def test_admin_decision_matches_migration_tuple_exactly(self):
        col = MultiAccountCluster.__table__.c.admin_decision
        assert _serialized_values(col) == list(MIGRATION_ADMIN_DECISION)


# ---------------------------------------------------------------------------
# 2. Real SQLite round-trip via minimal mirror tables -- proves a cluster row
# plus two member flag rows round-trip with the FK relationship intact, and
# that the raw stored bytes are lowercase. See module docstring for why a
# mirror table is used instead of MultiAccountCluster.__table__.create()
# directly (the unrelated Postgres-only JSONB signal_summary column has no
# SQLite compiler support at all).
# ---------------------------------------------------------------------------

class TestSqliteRoundTrip:
    @pytest.fixture()
    def mirror(self):
        """Minimal Tables reusing the REAL MultiAccountCluster/Flag
        Column TYPE OBJECTS (values_callable already applied) -- not
        reimplementations. signal_summary (JSONB) is omitted; it has
        nothing to do with the enum-serialization defect this test proves
        and has no SQLite compiler support at all."""
        meta = MetaData()
        cluster_table = Table(
            "multi_account_clusters_mirror", meta,
            Column("id", MultiAccountCluster.__table__.c.id.type, primary_key=True),
            Column("severity", MultiAccountCluster.__table__.c.severity.type, nullable=False),
            Column(
                "admin_decision",
                MultiAccountCluster.__table__.c.admin_decision.type,
                nullable=False,
            ),
            Column(
                "all_paid_subscribers",
                MultiAccountCluster.__table__.c.all_paid_subscribers.type,
                nullable=False,
            ),
        )
        flag_table = Table(
            "multi_account_flags_mirror", meta,
            Column("id", MultiAccountFlag.__table__.c.id.type, primary_key=True),
            Column(
                "cluster_id",
                MultiAccountFlag.__table__.c.cluster_id.type,
                ForeignKey("multi_account_clusters_mirror.id"),
                nullable=False,
            ),
            Column("signal", MultiAccountFlag.__table__.c.signal.type, nullable=False),
            Column("severity", MultiAccountFlag.__table__.c.severity.type, nullable=False),
        )
        eng = create_engine("sqlite:///:memory:")
        meta.create_all(eng)
        return eng, cluster_table, flag_table

    def test_cluster_plus_two_flags_round_trip(self, mirror):
        eng, cluster_table, flag_table = mirror
        cluster_id = uuid.uuid4()
        with eng.begin() as conn:
            conn.execute(cluster_table.insert().values(
                id=cluster_id,
                severity=MultiAccountSeverity.HARD,
                admin_decision=MultiAccountAdminDecision.PENDING,
                all_paid_subscribers=False,
            ))
            conn.execute(flag_table.insert().values(
                id=uuid.uuid4(),
                cluster_id=cluster_id,
                signal="payment_method",
                severity=MultiAccountSeverity.HARD,
            ))
            conn.execute(flag_table.insert().values(
                id=uuid.uuid4(),
                cluster_id=cluster_id,
                signal="ip_24h",
                severity=MultiAccountSeverity.SOFT,
            ))

            fetched_cluster = conn.execute(cluster_table.select()).first()
            fetched_flags = conn.execute(
                flag_table.select().where(flag_table.c.cluster_id == cluster_id)
            ).all()

        assert fetched_cluster.severity is MultiAccountSeverity.HARD
        assert fetched_cluster.admin_decision is MultiAccountAdminDecision.PENDING
        assert fetched_cluster.all_paid_subscribers is False
        assert len(fetched_flags) == 2
        assert {f.signal for f in fetched_flags} == {"payment_method", "ip_24h"}
        assert {f.severity for f in fetched_flags} == {
            MultiAccountSeverity.HARD, MultiAccountSeverity.SOFT,
        }

    def test_raw_stored_severity_bytes_are_lowercase_values_not_uppercase_names(self, mirror):
        eng, cluster_table, flag_table = mirror
        cluster_id = uuid.uuid4()
        with eng.begin() as conn:
            conn.execute(cluster_table.insert().values(
                id=cluster_id,
                severity=MultiAccountSeverity.SOFT,
                admin_decision=MultiAccountAdminDecision.CONFIRMED,
                all_paid_subscribers=True,
            ))
            # Bypasses the type's own result-processor entirely -- this is
            # what is ACTUALLY sitting in the database, byte for byte.
            raw = conn.execute(text(
                "SELECT severity, admin_decision FROM multi_account_clusters_mirror"
            )).first()

        assert raw == ("soft", "confirmed")

    def test_two_flags_same_cluster_different_signal_are_distinct_rows(self, mirror):
        """Not the UNIQUE(player_id, cluster_id, signal) constraint itself
        (that's Postgres-only DDL, proven by the migration's own
        UniqueConstraint declaration) -- confirms two flags sharing a
        cluster_id but differing signal insert as distinct rows, the normal
        (non-conflicting) case the constraint is meant to allow."""
        eng, cluster_table, flag_table = mirror
        cluster_id = uuid.uuid4()
        with eng.begin() as conn:
            conn.execute(cluster_table.insert().values(
                id=cluster_id,
                severity=MultiAccountSeverity.HARD,
                admin_decision=MultiAccountAdminDecision.PENDING,
                all_paid_subscribers=False,
            ))
            player_id = uuid.uuid4()
            conn.execute(flag_table.insert().values(
                id=uuid.uuid4(), cluster_id=cluster_id,
                signal="device_fingerprint", severity=MultiAccountSeverity.HARD,
            ))
            conn.execute(flag_table.insert().values(
                id=uuid.uuid4(), cluster_id=cluster_id,
                signal="trade_correlation", severity=MultiAccountSeverity.SOFT,
            ))
            rows = conn.execute(
                flag_table.select().where(flag_table.c.cluster_id == cluster_id)
            ).all()
        assert len(rows) == 2


# ---------------------------------------------------------------------------
# 3. The literal regression pin -- no column may ever serialize an
# uppercase member NAME.
# ---------------------------------------------------------------------------

class TestNoColumnSerializesUppercaseNames:
    """This is the exact assertion that would have caught the shipped
    defect (bare Enum(PyEnum) serializes "HARD" / "PENDING" / etc.) before
    it ever reached a real Postgres DB."""

    @pytest.mark.parametrize("table,column_name,enum_cls", [
        (MultiAccountCluster, "severity", MultiAccountSeverity),
        (MultiAccountCluster, "admin_decision", MultiAccountAdminDecision),
        (MultiAccountFlag, "severity", MultiAccountSeverity),
    ])
    def test_no_uppercase_member_name_in_serialized_set(self, table, column_name, enum_cls):
        column = table.__table__.c[column_name]
        serialized = _serialized_values(column)
        member_names = [m.name for m in enum_cls]

        for name in member_names:
            assert name not in serialized, (
                f"{table.__tablename__}.{column_name} serializes the uppercase NAME "
                f"{name!r} -- values_callable is missing or broken"
            )

        # Positive check: every serialized string IS one of the enum's
        # lowercase .value forms, in member-declaration order.
        assert serialized == [m.value for m in enum_cls]
