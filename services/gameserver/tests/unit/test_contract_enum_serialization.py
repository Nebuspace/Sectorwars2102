"""WO-SWEEP-CONTRACT-ENUM: regression pin for the contract enum
name-vs-value serialization defect (P1 -- same defect class as
WO-SWEEP-WARPLAYER-ENUM, confirmed live by that WO's census).

PREMISE: a plain SQLAlchemy ``Enum(PyEnum)`` column serializes the Python
member NAME by default (e.g. ``ContractIssuerType.NPC`` -> the string
``"NPC"``), but migration ``1aab831e9008`` built all six Postgres enum TYPES
from lowercase VALUES (``'npc'``, ``'posted'``, ``'held'``, ...). Every
write and every enum-compared read on ``contracts`` fails against a real
(values-built) Postgres DB with ``invalid input value for enum`` -- this is
what was silently killing the contract-expire sweep every tick.

Fix: ``values_callable=lambda obj: [e.value for e in obj]`` added to all six
enum columns (``issuer_type``, ``contract_type``, ``status``,
``escrow_state``, ``dispute_resolution``, ``insurance_coverage_tier``),
matching WO-SWEEP-WARPLAYER-ENUM's fix and the established pattern already
used elsewhere (``bounty_claim.py``, ``faction.py``).

This file proves the same three things test_warp_enum_serialization.py did,
with one adaptation:

1. ``TestColumnSerializationMatchesMigration`` -- each column's serialized
   string set (``Column.type.enums``) is byte-identical to migration
   1aab831e9008's own ISSUER_TYPE_VALUES/CONTRACT_TYPE_VALUES/STATUS_VALUES/
   ESCROW_STATE_VALUES/DISPUTE_RESOLUTION_VALUES/INSURANCE_TIER_VALUES
   tuples, restated literally here with a comment naming the migration.
2. ``TestSqliteRoundTrip`` -- a REAL round-trip against an isolated,
   in-memory SQLite table. Unlike PlayerWarpKnowledge, ``Contract.__table__``
   cannot be created standalone on SQLite: its ``posting_stations`` column
   is a Postgres-only ``ARRAY(UUID)`` type with zero SQLite compiler
   support (a ``CompileError``, confirmed live) -- unrelated to enums,
   entirely a different Postgres-only-type problem. Rather than fight that,
   this builds a minimal *mirror* ``Table`` containing only an id column
   plus the six enum columns, reusing the REAL, LIVE ``Column.type`` objects
   straight off ``Contract.__table__.c.<name>`` (the exact same
   values_callable-bearing Enum type instances the ORM actually uses -- not
   reimplementations). A Core-level insert + textual SELECT proves the raw
   stored bytes are lowercase; a Core-level ``select()`` proves round-trip
   type coercion still resolves back to the correct enum member. This is
   not a full-table proof (FKs, indexes, and other Postgres-only columns
   are absent) but it is a full proof of the actual enum-serialization
   defect, which lives entirely in the six ``Column.type`` objects and
   nothing else about the table's shape.
3. ``TestNoColumnSerializesUppercaseNames`` -- the literal regression pin:
   none of the six columns' serialized sets may ever contain an uppercase
   member NAME.
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import Column, MetaData, Table, create_engine, text

from src.models.contract import (
    Contract,
    ContractDisputeResolution,
    ContractEscrowState,
    ContractInsuranceCoverageTier,
    ContractIssuerType,
    ContractStatus,
    ContractType,
)

# Restated literally from alembic/versions/1aab831e9008_add_contracts_table.py
# (the migration that built the LIVE Postgres enum TYPES) -- NOT imported,
# since alembic revision modules are not meant to be import targets from
# application/test code. That migration is additive-only and its own
# docstring says so; these tuples should never need to change independently
# of it.
MIGRATION_ISSUER_TYPE = ('npc', 'player')
MIGRATION_CONTRACT_TYPE = (
    'cargo_delivery', 'bulk_procurement', 'express_delivery',
    'hazardous_transport', 'refugee_transport', 'acquisition_bounty', 'escort',
)
MIGRATION_STATUS = (
    'posted', 'accepted', 'in_progress', 'partial_fulfilled',
    'completed', 'cancelled', 'disputed', 'expired',
)
MIGRATION_ESCROW_STATE = ('held', 'released', 'disputed', 'refunding')
MIGRATION_DISPUTE_RESOLUTION = ('full_payout', 'partial_payout', 'refund', 'split', 'penalty')
MIGRATION_INSURANCE_TIER = ('basic', 'standard', 'hazard')


def _serialized_values(column) -> list:
    """The exact strings SQLAlchemy will bind to the DB for this enum
    column -- what values_callable actually produced."""
    return list(column.type.enums)


# ---------------------------------------------------------------------------
# 1. Column serialization matches the migration's own enum tuples exactly
# ---------------------------------------------------------------------------

class TestColumnSerializationMatchesMigration:
    def test_issuer_type_matches_migration_tuple_exactly(self):
        col = Contract.__table__.c.issuer_type
        assert _serialized_values(col) == list(MIGRATION_ISSUER_TYPE)

    def test_contract_type_matches_migration_tuple_exactly(self):
        col = Contract.__table__.c.contract_type
        assert _serialized_values(col) == list(MIGRATION_CONTRACT_TYPE)

    def test_status_matches_migration_tuple_exactly(self):
        col = Contract.__table__.c.status
        assert _serialized_values(col) == list(MIGRATION_STATUS)

    def test_escrow_state_matches_migration_tuple_exactly(self):
        col = Contract.__table__.c.escrow_state
        assert _serialized_values(col) == list(MIGRATION_ESCROW_STATE)

    def test_dispute_resolution_matches_migration_tuple_exactly(self):
        col = Contract.__table__.c.dispute_resolution
        assert _serialized_values(col) == list(MIGRATION_DISPUTE_RESOLUTION)

    def test_insurance_coverage_tier_matches_migration_tuple_exactly(self):
        col = Contract.__table__.c.insurance_coverage_tier
        assert _serialized_values(col) == list(MIGRATION_INSURANCE_TIER)


# ---------------------------------------------------------------------------
# 2. Real SQLite round-trip via a minimal mirror table -- proves the actual
# bytes are lowercase, and that Core-level read-back still works. See the
# module docstring for why a mirror table is used instead of
# Contract.__table__.create() directly (the unrelated ARRAY(UUID)
# posting_stations column has no SQLite compiler support at all).
# ---------------------------------------------------------------------------

class TestSqliteRoundTrip:
    @pytest.fixture()
    def mirror(self):
        """A minimal Table reusing the REAL Contract.__table__ enum Column
        TYPE OBJECTS (values_callable already applied) -- not
        reimplementations. Only the six enum columns matter for this
        defect; every other column on the real table is Postgres-only
        schema noise (FKs, ARRAY, etc.) that has nothing to do with enum
        serialization."""
        meta = MetaData()
        table = Table(
            "contracts_enum_mirror", meta,
            Column("id", Contract.__table__.c.id.type, primary_key=True),
            Column("issuer_type", Contract.__table__.c.issuer_type.type, nullable=False),
            Column("contract_type", Contract.__table__.c.contract_type.type, nullable=False),
            Column("status", Contract.__table__.c.status.type, nullable=False),
            Column("escrow_state", Contract.__table__.c.escrow_state.type, nullable=False),
            Column("dispute_resolution", Contract.__table__.c.dispute_resolution.type, nullable=True),
            Column("insurance_coverage_tier", Contract.__table__.c.insurance_coverage_tier.type, nullable=True),
        )
        eng = create_engine("sqlite:///:memory:")
        meta.create_all(eng)
        return eng, table

    def test_raw_stored_strings_are_lowercase_values_not_uppercase_names(self, mirror):
        eng, table = mirror
        row_id = uuid.uuid4()
        with eng.begin() as conn:
            conn.execute(table.insert().values(
                id=row_id,
                issuer_type=ContractIssuerType.NPC,
                contract_type=ContractType.CARGO_DELIVERY,
                status=ContractStatus.POSTED,
                escrow_state=ContractEscrowState.HELD,
                dispute_resolution=ContractDisputeResolution.FULL_PAYOUT,
                insurance_coverage_tier=ContractInsuranceCoverageTier.BASIC,
            ))

            # Bypasses the type's own result-processor entirely -- this is
            # what is ACTUALLY sitting in the database, byte for byte.
            raw = conn.execute(text(
                "SELECT issuer_type, contract_type, status, escrow_state, "
                "dispute_resolution, insurance_coverage_tier "
                "FROM contracts_enum_mirror"
            )).first()

        assert raw == (
            "npc", "cargo_delivery", "posted", "held", "full_payout", "basic",
        )

    def test_core_read_back_resolves_to_the_correct_enum_members(self, mirror):
        eng, table = mirror
        row_id = uuid.uuid4()
        with eng.begin() as conn:
            conn.execute(table.insert().values(
                id=row_id,
                issuer_type=ContractIssuerType.PLAYER,
                contract_type=ContractType.ESCORT,
                status=ContractStatus.IN_PROGRESS,
                escrow_state=ContractEscrowState.RELEASED,
                dispute_resolution=ContractDisputeResolution.SPLIT,
                insurance_coverage_tier=ContractInsuranceCoverageTier.HAZARD,
            ))
            fetched = conn.execute(table.select()).first()

        assert fetched.issuer_type is ContractIssuerType.PLAYER
        assert fetched.contract_type is ContractType.ESCORT
        assert fetched.status is ContractStatus.IN_PROGRESS
        assert fetched.escrow_state is ContractEscrowState.RELEASED
        assert fetched.dispute_resolution is ContractDisputeResolution.SPLIT
        assert fetched.insurance_coverage_tier is ContractInsuranceCoverageTier.HAZARD

    def test_every_contract_status_member_round_trips(self, mirror):
        """Every ContractStatus member (not just POSTED/IN_PROGRESS) must
        round-trip cleanly -- a positive check per enum value, matching the
        WO-SWEEP-WARPLAYER-ENUM precedent's full-membership sweep."""
        eng, table = mirror
        with eng.begin() as conn:
            for status in ContractStatus:
                conn.execute(table.insert().values(
                    id=uuid.uuid4(),
                    issuer_type=ContractIssuerType.NPC,
                    contract_type=ContractType.CARGO_DELIVERY,
                    status=status,
                    escrow_state=ContractEscrowState.HELD,
                ))

            raw_statuses = {
                row[0] for row in conn.execute(text(
                    "SELECT status FROM contracts_enum_mirror"
                )).all()
            }
        assert raw_statuses == {status.value for status in ContractStatus}


# ---------------------------------------------------------------------------
# 3. The literal regression pin -- no column may ever serialize an
# uppercase member NAME.
# ---------------------------------------------------------------------------

class TestNoColumnSerializesUppercaseNames:
    """This is the exact assertion that would have caught the shipped
    defect (bare Enum(PyEnum) serializes "NPC" / "POSTED" / "HELD" / etc.)
    before it ever reached a real Postgres DB."""

    @pytest.mark.parametrize("column_name,enum_cls", [
        ("issuer_type", ContractIssuerType),
        ("contract_type", ContractType),
        ("status", ContractStatus),
        ("escrow_state", ContractEscrowState),
        ("dispute_resolution", ContractDisputeResolution),
        ("insurance_coverage_tier", ContractInsuranceCoverageTier),
    ])
    def test_no_uppercase_member_name_in_serialized_set(self, column_name, enum_cls):
        column = Contract.__table__.c[column_name]
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
