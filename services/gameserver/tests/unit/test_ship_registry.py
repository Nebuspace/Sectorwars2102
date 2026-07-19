"""WO-P10-green-ship-registry-schema -- ship registry schema + auto-registration.

Real-DB proof (does the migration's create_table/add_column actually succeed
against Postgres) is NOT reachable on the Mac -- no local Postgres, and both
new columns/table declare Postgres-only UUID/JSONB/ENUM types, which rules
out a real-SQLite proof the way sibling savepoint tests in this suite use
for Postgres-type-free models. THE authoritative proof for actual schema
application is the CI ci-schema-parity gate the orchestrator watches after
this ships -- said explicitly here rather than oversold.

What IS provable locally, and pinned below:
  - the REG-XXXX-YYYY generator's format and excluded-character set
  - the before_insert/after_insert mapper events on Ship (DB-free via a
    fake Connection, capturing the compiled INSERT's bound params rather
    than needing a live table)
  - the enum-serialization pin -- event_type binds as the lowercase
    ``.value`` ("initial_registration"), never the member NAME
    ("INITIAL_REGISTRATION"), the values_callable class of bug fixed
    repeatedly elsewhere in this codebase
  - append_registry_event's row construction (DB-free, add()/flush() stub)
  - backfill_initial_registrations' idempotency and pre-existing-hull
    registration_number assignment (DB-free, a minimal fake Session that
    interprets the exact filter shapes the service issues)
  - migration chain integrity: down_revision == the confirmed head
    (d4c8f6a12e93), single head, AST-pinned like
    tests/unit/test_phantom_table_catchup.py
"""
from __future__ import annotations

import ast
import pathlib
import re
import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy import Enum as SQLEnum

from src.models.ship import Ship
from src.models.ship_registry import (
    RegistryEventType,
    ShipRegistry,
    _assign_registration_fields,
    _emit_initial_registration_event,
    generate_registration_number,
)
from src.services.ship_registry_service import (
    append_registry_event,
    backfill_initial_registrations,
)

_REG_NUMBER_RE = re.compile(r"^REG-[A-Z0-9]{4}-\d{4}$")
_EXCLUDED_CHARS = set("IO01")


# --- Registration-number generator ---------------------------------------


@pytest.mark.unit
class TestRegistrationNumberFormat:
    def test_format_matches_reg_xxxx_yyyy(self) -> None:
        for _ in range(200):
            number = generate_registration_number()
            assert _REG_NUMBER_RE.match(number), number

    def test_alnum_block_excludes_ambiguous_characters(self) -> None:
        for _ in range(200):
            number = generate_registration_number()
            block = number.split("-")[1]
            assert not (set(block) & _EXCLUDED_CHARS), block

    def test_year_block_uses_the_given_year(self) -> None:
        number = generate_registration_number(year=2107)
        assert number.endswith("-2107")


# --- Auto-registration mapper events (DB-free via a fake Connection) -----


class _FakeNoCollisionResult:
    def first(self):
        return None


class _FakeNoCollisionConnection:
    """Every registration_number lookup returns "not taken" -- exercises
    the zero-retry happy path."""

    def execute(self, stmt):
        return _FakeNoCollisionResult()


class _FakeOneCollisionConnection:
    """First lookup says "taken", every subsequent lookup says "free" --
    exercises the bounded collision-retry loop actually retrying."""

    def __init__(self):
        self.calls = 0

    def execute(self, stmt):
        self.calls += 1
        return SimpleNamespace(first=lambda: ("taken",) if self.calls == 1 else None)


class _FakeCapturingConnection:
    """Captures the compiled bound params of whatever statement is
    executed -- the DB-free proof technique for SQLAlchemy Core inserts
    (stmt.compile().params), no live table required."""

    def __init__(self):
        self.captured_params = None

    def execute(self, stmt):
        self.captured_params = stmt.compile().params
        return None


@pytest.mark.unit
class TestBeforeInsertListener:
    def test_assigns_a_well_formed_registration_number(self) -> None:
        target = Ship(id=uuid.uuid4(), owner_id=uuid.uuid4())
        assert target.registration_number is None

        _assign_registration_fields(None, _FakeNoCollisionConnection(), target)

        assert _REG_NUMBER_RE.match(target.registration_number)

    def test_backfills_registered_owner_id_from_owner_id(self) -> None:
        owner_id = uuid.uuid4()
        target = Ship(id=uuid.uuid4(), owner_id=owner_id)
        assert target.registered_owner_id is None

        _assign_registration_fields(None, _FakeNoCollisionConnection(), target)

        assert target.registered_owner_id == owner_id

    def test_npc_ship_with_no_owner_gets_no_registered_owner(self) -> None:
        """NPC-piloted hulls (owner_id NULL) must not synthesize an owner --
        registered_owner_id stays NULL, same as owner_id."""
        target = Ship(id=uuid.uuid4(), owner_id=None, is_npc=True)

        _assign_registration_fields(None, _FakeNoCollisionConnection(), target)

        assert target.registered_owner_id is None
        assert _REG_NUMBER_RE.match(target.registration_number)

    def test_does_not_overwrite_an_already_set_registration_number(self) -> None:
        target = Ship(id=uuid.uuid4(), owner_id=uuid.uuid4(), registration_number="REG-FIXD-2103")

        _assign_registration_fields(None, _FakeNoCollisionConnection(), target)

        assert target.registration_number == "REG-FIXD-2103"

    def test_does_not_overwrite_an_already_set_registered_owner_id(self) -> None:
        preset_owner = uuid.uuid4()
        different_owner = uuid.uuid4()
        target = Ship(id=uuid.uuid4(), owner_id=different_owner, registered_owner_id=preset_owner)

        _assign_registration_fields(None, _FakeNoCollisionConnection(), target)

        assert target.registered_owner_id == preset_owner

    def test_retries_on_a_collision_and_still_lands_a_valid_number(self) -> None:
        target = Ship(id=uuid.uuid4(), owner_id=uuid.uuid4())
        conn = _FakeOneCollisionConnection()

        _assign_registration_fields(None, conn, target)

        assert conn.calls >= 2, "listener must re-check after a reported collision"
        assert _REG_NUMBER_RE.match(target.registration_number)


@pytest.mark.unit
class TestAfterInsertListener:
    def test_emits_exactly_one_initial_registration_event_with_original_owner_id(self) -> None:
        owner_id = uuid.uuid4()
        target = Ship(id=uuid.uuid4(), owner_id=owner_id)
        _assign_registration_fields(None, _FakeNoCollisionConnection(), target)
        assert target.registration_number is not None

        conn = _FakeCapturingConnection()
        _emit_initial_registration_event(None, conn, target)

        params = conn.captured_params
        assert params is not None, "after_insert must execute exactly one INSERT"
        assert params["ship_id"] == target.id
        assert params["registration_number"] == target.registration_number
        assert _REG_NUMBER_RE.match(params["registration_number"])
        assert params["original_owner_id"] == owner_id
        assert params["new_owner_id"] == owner_id

    def test_event_type_binds_as_the_lowercase_value_not_the_member_name(self) -> None:
        """Enum-serialization pin: the values_callable class of bug fixed
        repeatedly in this codebase (name-vs-value). A regression here would
        bind 'INITIAL_REGISTRATION' -- which does not exist as a Postgres
        enum label -- instead of 'initial_registration'."""
        target = Ship(id=uuid.uuid4(), owner_id=uuid.uuid4())
        _assign_registration_fields(None, _FakeNoCollisionConnection(), target)

        conn = _FakeCapturingConnection()
        _emit_initial_registration_event(None, conn, target)

        assert conn.captured_params["event_type"] == "initial_registration"
        assert conn.captured_params["event_type"] == RegistryEventType.INITIAL_REGISTRATION.value
        assert conn.captured_params["event_type"] != RegistryEventType.INITIAL_REGISTRATION.name

    def test_column_declares_values_callable(self) -> None:
        """Structural pin on the Column itself, independent of the runtime
        bind-value proof above."""
        column_type = ShipRegistry.__table__.c.event_type.type
        assert isinstance(column_type, SQLEnum)
        assert column_type.values_callable is not None
        assert column_type.values_callable(RegistryEventType) == [e.value for e in RegistryEventType]


# --- append_registry_event (DB-free) --------------------------------------


class _FakeFlushSession:
    def __init__(self):
        self.added = []

    def add(self, obj):
        self.added.append(obj)

    def flush(self):
        pass


@pytest.mark.unit
class TestAppendRegistryEvent:
    def test_builds_a_row_with_the_given_fields(self) -> None:
        ship = Ship(id=uuid.uuid4(), registration_number="REG-ABCD-2103")
        owner_id = uuid.uuid4()
        claimant_id = uuid.uuid4()
        db = _FakeFlushSession()

        row = append_registry_event(
            db,
            ship=ship,
            event_type=RegistryEventType.OWNERSHIP_TRANSFER,
            previous_owner_id=owner_id,
            new_owner_id=claimant_id,
            acting_party_id=claimant_id,
            transfer_fee_paid=75000,
        )

        assert db.added == [row]
        assert row.ship_id == ship.id
        assert row.registration_number == "REG-ABCD-2103"
        assert row.event_type == RegistryEventType.OWNERSHIP_TRANSFER
        assert row.previous_owner_id == owner_id
        assert row.new_owner_id == claimant_id
        assert row.transfer_fee_paid == 75000

    def test_defaults_event_metadata_to_empty_dict(self) -> None:
        ship = Ship(id=uuid.uuid4(), registration_number="REG-ABCD-2103")
        db = _FakeFlushSession()

        row = append_registry_event(db, ship=ship, event_type=RegistryEventType.ARCHIVED)

        assert row.event_metadata == {}


# --- backfill_initial_registrations (DB-free fake Session) ---------------


class _FakeFilteredQuery:
    """Interprets exactly the two filter shapes
    backfill_initial_registrations issues: ``Ship.registration_number ==
    value`` and ``ShipRegistry.event_type == value``."""

    def __init__(self, rows, extractor):
        self._rows = rows
        self._extractor = extractor

    def filter(self, clause):
        key = clause.left.key
        value = clause.right.value
        filtered = [r for r in self._rows if getattr(r, key, None) == value]
        return _FakeFilteredQuery(filtered, self._extractor)

    def first(self):
        rows = [self._extractor(r) for r in self._rows]
        return rows[0] if rows else None

    def all(self):
        return [self._extractor(r) for r in self._rows]

    def __iter__(self):
        return iter(self.all())


class _FakeBackfillSession:
    """A minimal Session stand-in covering exactly the query/add/flush
    shapes backfill_initial_registrations + append_registry_event issue --
    not a general-purpose fake."""

    def __init__(self, ships, existing_registry_ship_ids=()):
        self.ships = list(ships)
        self.existing_registry_rows = [
            SimpleNamespace(ship_id=sid, event_type=RegistryEventType.INITIAL_REGISTRATION.value)
            for sid in existing_registry_ship_ids
        ]
        self.added = []

    def add(self, obj):
        self.added.append(obj)

    def flush(self):
        pass

    def query(self, entity):
        if entity is Ship:
            return _FakeFilteredQuery(self.ships, lambda s: s)
        if getattr(entity, "class_", None) is Ship and entity.key == "id":
            return _FakeFilteredQuery(self.ships, lambda s: s.id)
        if getattr(entity, "class_", None) is Ship and entity.key == "registration_number":
            return _FakeFilteredQuery(self.ships, lambda s: s.registration_number)
        if getattr(entity, "class_", None) is ShipRegistry and entity.key == "ship_id":
            return _FakeFilteredQuery(self.existing_registry_rows, lambda r: r)
        raise AssertionError(f"unexpected query entity in fake session: {entity!r}")


@pytest.mark.unit
class TestBackfillInitialRegistrations:
    def test_backfills_every_ship_missing_a_registration_row(self) -> None:
        owner_id = uuid.uuid4()
        ship_a = Ship(id=uuid.uuid4(), owner_id=owner_id)
        ship_b = Ship(id=uuid.uuid4(), owner_id=uuid.uuid4())
        db = _FakeBackfillSession(ships=[ship_a, ship_b])

        count = backfill_initial_registrations(db)

        assert count == 2
        assert len(db.added) == 2
        assert {row.ship_id for row in db.added} == {ship_a.id, ship_b.id}
        assert all(row.event_type == RegistryEventType.INITIAL_REGISTRATION for row in db.added)
        for ship in (ship_a, ship_b):
            assert _REG_NUMBER_RE.match(ship.registration_number)
            assert ship.registered_owner_id == ship.owner_id

    def test_is_idempotent_skips_already_registered_ships(self) -> None:
        ship_a = Ship(id=uuid.uuid4(), owner_id=uuid.uuid4(), registration_number="REG-EXST-2103")
        ship_b = Ship(id=uuid.uuid4(), owner_id=uuid.uuid4())
        db = _FakeBackfillSession(ships=[ship_a, ship_b], existing_registry_ship_ids=[ship_a.id])

        count = backfill_initial_registrations(db)

        assert count == 1
        assert db.added[0].ship_id == ship_b.id
        # Ship A's pre-existing registration number is untouched.
        assert ship_a.registration_number == "REG-EXST-2103"

    def test_preserves_an_existing_registration_number(self) -> None:
        ship = Ship(id=uuid.uuid4(), owner_id=uuid.uuid4(), registration_number="REG-KEEP-2103")
        db = _FakeBackfillSession(ships=[ship])

        backfill_initial_registrations(db)

        assert ship.registration_number == "REG-KEEP-2103"


# --- Migration chain integrity (AST-pinned, mirrors test_phantom_table_catchup.py) --


_MIGRATION_PATH = (
    pathlib.Path(__file__).resolve().parents[2]
    / "alembic" / "versions" / "4c7660b879f7_ship_registry_schema.py"
)
_VERSIONS_DIR = _MIGRATION_PATH.parent
_CONFIRMED_PARENT_HEAD = "d4c8f6a12e93"


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
        assert assigns.get("revision") == "4c7660b879f7"

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

    def test_upgrade_adds_all_eight_ship_columns(self) -> None:
        source = _MIGRATION_PATH.read_text()
        tree = ast.parse(source)
        upgrade_fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "upgrade")
        upgrade_src = ast.get_source_segment(source, upgrade_fn) or ""
        for column_name in (
            "registration_number",
            "registered_owner_id",
            "current_pilot_id",
            "stolen_status",
            "stolen_reported_at",
            "hatch_pin_code",
            "for_sale_price",
            "for_sale_listed_by_id",
        ):
            assert f"'{column_name}'" in upgrade_src, column_name

    def test_upgrade_creates_the_ship_registry_table(self) -> None:
        source = _MIGRATION_PATH.read_text()
        assert "op.create_table(\n        'ship_registry'" in source

    def test_downgrade_drops_everything_upgrade_adds(self) -> None:
        source = _MIGRATION_PATH.read_text()
        tree = ast.parse(source)
        downgrade_fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "downgrade")
        downgrade_src = ast.get_source_segment(source, downgrade_fn) or ""
        assert "op.drop_table('ship_registry')" in downgrade_src
        for column_name in (
            "registration_number",
            "registered_owner_id",
            "current_pilot_id",
            "stolen_status",
            "stolen_reported_at",
            "hatch_pin_code",
            "for_sale_price",
            "for_sale_listed_by_id",
        ):
            assert f"'{column_name}'" in downgrade_src, column_name
