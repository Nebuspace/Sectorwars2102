"""Unit coverage for the ARIA data-index registry (WO-P6-aria-data-index-
registry): the `aria_data_streams` catalog table, its seeder, its read
service, and the GET /ai/data-index catalog route.

Canon: sw2102-docs/DATA_MODELS/aria-data-index.md (ADR-0092). Layers,
matching the resource-registry / ship-registry test conventions already in
this suite:
  * pure-catalog tests on ARIA_DATA_STREAMS -- no DB, no fixtures
  * enum-serialization pin -- a mirror SQLite table (payload_schema's
    JSONB column has no SQLite compiler support, same constraint documented
    in test_contract_enum_serialization.py, so a full-table create is not
    possible; the mirror carries only the two enum columns + key, reusing
    the REAL, LIVE Column.type objects straight off ARIADataStream.__table__)
  * seeder tests against the real `db` fixture (requires a live Postgres --
    not reachable from this Mac session; DB-fixture test errors here are
    baseline noise per this repo's established local-pytest convention,
    verified independently by the DB-free layers above and below)
  * service tests -- a fake AsyncSession (governance-route convention:
    `execute()` returns a MagicMock with scalar_one_or_none/scalars.all
    pre-wired, never a bare AsyncMock -- see AsyncMock nested-attr trap)
  * route test -- calls the handler directly, bypassing FastAPI DI (mirrors
    test_resource_registry.py's convention)
  * migration chain integrity -- AST-pinned, mirrors test_ship_registry.py

LANE C MISMATCH (documented here, not silently resolved): the three
memory_type string literals aria_personal_intelligence_service.py actually
writes today ("combat", "market", "exploration") do not byte-match any
registry key in this catalog. The closest is "combat" vs. this registry's
"threat.combat"; "market" and "exploration" have no ARIAPersonalMemory-
backed doc stream at all (commerce.trade routes to ARIATradingObservation,
nav.sector_visit routes to ARIAExplorationMap -- neither is
ARIAPersonalMemory). Per the WO's explicit "STOP and report the mismatch,
don't invent a mapping" instruction, aria_personal_intelligence_service.py
is untouched by this WO -- TestLaneCMemoryTypeMismatch below is the durable
regression pin for that decision, so a future change to either side doesn't
silently drift without going through review.
"""
from __future__ import annotations

import ast
import pathlib
from unittest.mock import MagicMock

import pytest
from sqlalchemy import Column, MetaData, Table, create_engine, text
from sqlalchemy.orm import Session

from src.api.routes.enhanced_ai import get_aria_data_index
from src.core.aria_data_stream_seeder import ARIA_DATA_STREAMS, seed_aria_data_streams
from src.models.aria_data_stream import (
    ARIADataStream,
    ARIADataStreamDomain,
    ARIADataStreamRetention,
)
from src.services.aria_data_index_service import ARIADataIndexService

# ---------------------------------------------------------------------------
# Pure-catalog tests -- no DB
# ---------------------------------------------------------------------------

CANON_DOMAIN_COUNTS = {
    "nav": 5, "commerce": 5, "threat": 5, "asset": 2, "social": 1, "meta": 3,
}


def test_registry_has_21_streams_across_6_domains():
    """aria-data-index.md's six domain tables total 21 rows (5+5+5+2+1+3)."""
    assert len(ARIA_DATA_STREAMS) == 21
    by_domain = {}
    for entry in ARIA_DATA_STREAMS.values():
        by_domain.setdefault(entry["domain"].value, 0)
        by_domain[entry["domain"].value] += 1
    assert by_domain == CANON_DOMAIN_COUNTS


def test_every_key_is_prefixed_by_its_own_domain():
    """Structural sanity: "nav.sector_visit"'s domain is ARIADataStreamDomain.NAV,
    etc. -- catches a copy-paste domain/key mismatch."""
    for key, entry in ARIA_DATA_STREAMS.items():
        domain_prefix = key.split(".", 1)[0]
        assert domain_prefix == entry["domain"].value, key


def test_every_entry_uses_real_enum_members_not_raw_strings():
    for key, entry in ARIA_DATA_STREAMS.items():
        assert isinstance(entry["domain"], ARIADataStreamDomain), key
        assert isinstance(entry["retention_class"], ARIADataStreamRetention), key


def test_every_payload_schema_is_a_nonempty_fields_list():
    for key, entry in ARIA_DATA_STREAMS.items():
        schema = entry["payload_schema"]
        assert set(schema.keys()) == {"fields"}, key
        assert isinstance(schema["fields"], list) and len(schema["fields"]) > 0, key
        assert all(isinstance(f, str) and f for f in schema["fields"]), key


def test_every_entry_defaults_transparency_visible_and_version_1():
    """Doc rule 3: every stream is visible unless its row says otherwise --
    the doc calls out zero exceptions among these 21 streams."""
    for key, entry in ARIA_DATA_STREAMS.items():
        assert entry["transparency_visible"] is True, key
        assert entry["version"] == 1, key


@pytest.mark.parametrize("key", ["threat.combat", "meta.dialogue", "meta.onboarding"])
def test_the_three_ariapersonalmemory_backed_streams(key):
    """Exactly these three doc streams route to ARIAPersonalMemory storage
    -- the set Lane C's mismatch report is about."""
    assert ARIA_DATA_STREAMS[key]["storage_table"] == "ARIAPersonalMemory"


def test_only_three_streams_route_to_ariapersonalmemory():
    matching = {k for k, v in ARIA_DATA_STREAMS.items() if v["storage_table"] == "ARIAPersonalMemory"}
    assert matching == {"threat.combat", "meta.dialogue", "meta.onboarding"}


def test_npc_sighting_display_name_is_not_mistitlecased():
    """Regression pin for the manual (not algorithmic) display_name
    convention -- a naive str.title() would render "Npc Sighting"."""
    assert ARIA_DATA_STREAMS["threat.npc_sighting"]["display_name"] == "NPC Sighting"


def test_commerce_trade_routes_to_observation_log_not_personal_memory():
    """commerce.trade -> ARIATradingObservation (ADR-0038), NOT
    ARIAPersonalMemory -- the fact that makes the existing "market" memory_
    type literal unmappable (see TestLaneCMemoryTypeMismatch)."""
    assert ARIA_DATA_STREAMS["commerce.trade"]["storage_table"] == "ARIATradingObservation"


def test_nav_sector_visit_routes_to_exploration_map_not_personal_memory():
    """nav.sector_visit -> ARIAExplorationMap, NOT ARIAPersonalMemory -- the
    fact that makes the existing "exploration" memory_type literal
    unmappable (see TestLaneCMemoryTypeMismatch)."""
    assert ARIA_DATA_STREAMS["nav.sector_visit"]["storage_table"] == "ARIAExplorationMap"


# ---------------------------------------------------------------------------
# Lane C: the memory_type mismatch is a durable, tested decision -- not a
# silently-dropped TODO.
# ---------------------------------------------------------------------------

class TestLaneCMemoryTypeMismatch:
    """aria_personal_intelligence_service.py's actual written memory_type
    literals ("combat"/"market"/"exploration") were left untouched by this
    WO because none of them byte-match a registry key. This pin fails (and
    demands a real decision) if either side ever drifts in a way that would
    silently resolve -- or silently break -- that gap."""

    EXISTING_WRITTEN_LITERALS = {"combat", "market", "exploration"}

    def test_no_existing_written_literal_matches_a_registry_key(self):
        assert self.EXISTING_WRITTEN_LITERALS.isdisjoint(ARIA_DATA_STREAMS.keys())

    def test_service_file_still_writes_the_three_literals_verbatim(self):
        """If this ever fails, Lane C's premise changed -- re-evaluate the
        mapping rather than assuming this pin is stale."""
        source = (
            pathlib.Path(__file__).resolve().parents[2]
            / "src" / "services" / "aria_personal_intelligence_service.py"
        ).read_text()
        assert 'memory_type="combat"' in source
        assert 'memory_type="market"' in source
        assert '"exploration"' in source  # record_exploration_memory's _create_memory call


# ---------------------------------------------------------------------------
# Enum-serialization pin -- mirror SQLite table (payload_schema's JSONB
# column has no SQLite compiler support, ruling out a full-table create;
# see test_contract_enum_serialization.py for the identical constraint).
# ---------------------------------------------------------------------------

# Restated literally from alembic/versions/26ea004450dc_aria_data_streams.py
# (the migration that builds the LIVE Postgres enum TYPES) -- NOT imported,
# since alembic revision modules are not meant to be import targets from
# application/test code.
MIGRATION_DOMAIN_VALUES = ('nav', 'commerce', 'threat', 'asset', 'social', 'meta')
MIGRATION_RETENTION_VALUES = ('permanent', 'rolling_90d', 'budget_pruned')


def _serialized_values(column) -> list:
    return list(column.type.enums)


class TestColumnSerializationMatchesMigration:
    def test_domain_matches_migration_tuple_exactly(self):
        col = ARIADataStream.__table__.c.domain
        assert _serialized_values(col) == list(MIGRATION_DOMAIN_VALUES)

    def test_retention_class_matches_migration_tuple_exactly(self):
        col = ARIADataStream.__table__.c.retention_class
        assert _serialized_values(col) == list(MIGRATION_RETENTION_VALUES)


class TestSqliteRoundTrip:
    @pytest.fixture()
    def mirror(self):
        """A minimal Table reusing the REAL ARIADataStream.__table__ enum
        Column TYPE OBJECTS (values_callable already applied), skipping the
        Postgres-only JSONB payload_schema column entirely -- unrelated to
        the enum-serialization defect class this proves."""
        meta = MetaData()
        table = Table(
            "aria_data_streams_enum_mirror", meta,
            Column("key", ARIADataStream.__table__.c.key.type, primary_key=True),
            Column("domain", ARIADataStream.__table__.c.domain.type, nullable=False),
            Column("retention_class", ARIADataStream.__table__.c.retention_class.type, nullable=False),
        )
        eng = create_engine("sqlite:///:memory:")
        meta.create_all(eng)
        return eng, table

    def test_raw_stored_strings_are_lowercase_values_not_uppercase_names(self, mirror):
        eng, table = mirror
        with eng.begin() as conn:
            conn.execute(table.insert().values(
                key="threat.combat",
                domain=ARIADataStreamDomain.THREAT,
                retention_class=ARIADataStreamRetention.BUDGET_PRUNED,
            ))
            raw = conn.execute(text(
                "SELECT domain, retention_class FROM aria_data_streams_enum_mirror"
            )).first()
        assert raw == ("threat", "budget_pruned")

    def test_core_read_back_resolves_to_the_correct_enum_members(self, mirror):
        eng, table = mirror
        with eng.begin() as conn:
            conn.execute(table.insert().values(
                key="meta.onboarding",
                domain=ARIADataStreamDomain.META,
                retention_class=ARIADataStreamRetention.PERMANENT,
            ))
            fetched = conn.execute(table.select()).first()
        assert fetched.domain is ARIADataStreamDomain.META
        assert fetched.retention_class is ARIADataStreamRetention.PERMANENT

    def test_every_domain_member_round_trips(self, mirror):
        eng, table = mirror
        with eng.begin() as conn:
            for i, domain in enumerate(ARIADataStreamDomain):
                conn.execute(table.insert().values(
                    key=f"test.key.{i}", domain=domain,
                    retention_class=ARIADataStreamRetention.PERMANENT,
                ))
            raw_domains = {
                row[0] for row in conn.execute(text(
                    "SELECT domain FROM aria_data_streams_enum_mirror"
                )).all()
            }
        assert raw_domains == {d.value for d in ARIADataStreamDomain}


class TestNoColumnSerializesUppercaseNames:
    @pytest.mark.parametrize("column_name,enum_cls", [
        ("domain", ARIADataStreamDomain),
        ("retention_class", ARIADataStreamRetention),
    ])
    def test_no_uppercase_member_name_in_serialized_set(self, column_name, enum_cls):
        column = ARIADataStream.__table__.c[column_name]
        serialized = _serialized_values(column)
        for member in enum_cls:
            assert member.name not in serialized, (
                f"{column_name} serializes the uppercase NAME {member.name!r} -- "
                f"values_callable is missing or broken"
            )
        assert serialized == [m.value for m in enum_cls]


# ---------------------------------------------------------------------------
# Seeder tests -- real DB session (requires a live Postgres; not reachable
# from this Mac session -- baseline noise here, verified independently by
# the DB-free layers around this section).
# ---------------------------------------------------------------------------

def test_seed_creates_all_21_streams(db: Session):
    processed = seed_aria_data_streams(db)
    assert processed == 21
    assert db.query(ARIADataStream).count() == 21


def test_seed_is_idempotent(db: Session):
    seed_aria_data_streams(db)
    processed_again = seed_aria_data_streams(db)
    assert processed_again == 21
    assert db.query(ARIADataStream).count() == 21


def test_seed_reconciles_a_hand_edited_row(db: Session):
    seed_aria_data_streams(db)
    row = db.query(ARIADataStream).filter(ARIADataStream.key == "threat.combat").first()
    row.display_name = "Corrupted"
    row.version = 999
    db.commit()

    seed_aria_data_streams(db)
    db.refresh(row)
    assert row.display_name == "Combat"
    assert row.version == 1


def test_seeded_row_field_shape(db: Session):
    seed_aria_data_streams(db)
    row = db.query(ARIADataStream).filter(ARIADataStream.key == "commerce.trade").first()
    assert row.domain == ARIADataStreamDomain.COMMERCE
    assert row.display_name == "Trade"
    assert row.storage_table == "ARIATradingObservation"
    assert row.retention_class == ARIADataStreamRetention.BUDGET_PRUNED
    assert row.transparency_visible is True
    assert row.payload_schema["fields"] == [
        "commodity", "action", "stations", "quantity", "price", "profit", "outcome",
    ]


# ---------------------------------------------------------------------------
# Service tests -- fake AsyncSession (governance-route convention: execute()
# returns a MagicMock with scalar_one_or_none/scalars.all pre-wired).
# ---------------------------------------------------------------------------

def _fake_row(key: str, transparency_visible: bool = True) -> ARIADataStream:
    row = ARIADataStream()
    row.key = key
    row.domain = ARIADataStreamDomain.THREAT
    row.display_name = "Combat"
    row.description = "Combat resolution involving the player"
    row.trigger_event = "Combat resolution involving the player"
    row.payload_schema = {"fields": ["outcome"]}
    row.storage_table = "ARIAPersonalMemory"
    row.retention_class = ARIADataStreamRetention.BUDGET_PRUNED
    row.transparency_visible = transparency_visible
    row.version = 1
    return row


class _FakeAsyncSession:
    """Mirrors test_governance_citizen_api.py's fake: execute() is async,
    the returned Result object is a plain MagicMock (sync scalars()/
    scalar_one_or_none() -- a bare AsyncMock() would wrongly make those
    coroutines too, per this repo's documented AsyncMock nested-attr
    trap)."""

    def __init__(self, all_rows=None, single_row=None):
        self._all_rows = all_rows or []
        self._single_row = single_row

    async def execute(self, stmt):
        result = MagicMock()
        result.scalars.return_value.all.return_value = list(self._all_rows)
        result.scalar_one_or_none.return_value = self._single_row
        return result


@pytest.mark.asyncio
async def test_list_streams_returns_all_rows():
    rows = [_fake_row("threat.combat"), _fake_row("meta.dialogue")]
    session = _FakeAsyncSession(all_rows=rows)
    service = ARIADataIndexService(session)

    result = await service.list_streams()

    assert result == rows


@pytest.mark.asyncio
async def test_get_stream_returns_the_matched_row():
    row = _fake_row("threat.combat")
    session = _FakeAsyncSession(single_row=row)
    service = ARIADataIndexService(session)

    result = await service.get_stream("threat.combat")

    assert result is row


@pytest.mark.asyncio
async def test_get_stream_returns_none_for_unknown_key():
    session = _FakeAsyncSession(single_row=None)
    service = ARIADataIndexService(session)

    result = await service.get_stream("does.not.exist")

    assert result is None


@pytest.mark.asyncio
async def test_transparency_visible_streams_passes_through_query_results():
    """The service doesn't re-filter in Python -- it trusts the query's
    WHERE clause. Wiring proof only (the fake can't verify the WHERE
    clause's *contents*, only that the method executes and returns what
    the query layer hands back)."""
    visible_rows = [_fake_row("threat.combat", transparency_visible=True)]
    session = _FakeAsyncSession(all_rows=visible_rows)
    service = ARIADataIndexService(session)

    result = await service.transparency_visible_streams()

    assert result == visible_rows


# ---------------------------------------------------------------------------
# Route test -- calls the handler directly, bypassing FastAPI DI (mirrors
# test_resource_registry.py's convention).
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_aria_data_index_route_returns_seeded_rows():
    rows = [_fake_row("threat.combat"), _fake_row("meta.dialogue")]
    session = _FakeAsyncSession(all_rows=rows)

    result = await get_aria_data_index(current_player=None, db=session)

    assert [r.key for r in result] == ["threat.combat", "meta.dialogue"]


@pytest.mark.asyncio
async def test_get_aria_data_index_route_wraps_errors_as_500():
    from fastapi import HTTPException

    class _BoomSession:
        async def execute(self, stmt):
            raise RuntimeError("db exploded")

    with pytest.raises(HTTPException) as exc_info:
        await get_aria_data_index(current_player=None, db=_BoomSession())

    assert exc_info.value.status_code == 500


# ---------------------------------------------------------------------------
# Migration chain integrity (AST-pinned, mirrors test_ship_registry.py)
# ---------------------------------------------------------------------------

_MIGRATION_PATH = (
    pathlib.Path(__file__).resolve().parents[2]
    / "alembic" / "versions" / "26ea004450dc_aria_data_streams.py"
)
_VERSIONS_DIR = _MIGRATION_PATH.parent
_CONFIRMED_PARENT_HEAD = "4c7660b879f7"


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

    def test_down_revision_is_the_confirmed_head(self) -> None:
        assigns = _assigns(_MIGRATION_PATH)
        assert assigns.get("down_revision") == _CONFIRMED_PARENT_HEAD
        assert assigns.get("revision") == "26ea004450dc"

    def test_no_other_migration_also_chains_onto_the_same_parent(self) -> None:
        """A second file with down_revision == the confirmed parent head
        would fork the history into two heads -- the durable, no-live-DB
        regression pin for "single head"."""
        offenders = []
        for path in _VERSIONS_DIR.glob("*.py"):
            if path == _MIGRATION_PATH:
                continue
            if _assigns(path).get("down_revision") == _CONFIRMED_PARENT_HEAD:
                offenders.append(path.name)
        assert offenders == []

    def test_upgrade_creates_the_aria_data_streams_table(self) -> None:
        source = _MIGRATION_PATH.read_text()
        assert "op.create_table(\n        'aria_data_streams'" in source

    def test_downgrade_drops_everything_upgrade_adds(self) -> None:
        source = _MIGRATION_PATH.read_text()
        tree = ast.parse(source)
        downgrade_fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "downgrade")
        downgrade_src = ast.get_source_segment(source, downgrade_fn) or ""
        assert "op.drop_table('aria_data_streams')" in downgrade_src
        assert "aria_data_stream_domain" in downgrade_src
        assert "aria_data_stream_retention" in downgrade_src
