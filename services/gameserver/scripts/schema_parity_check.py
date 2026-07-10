#!/usr/bin/env python3
"""Schema parity check: SQLAlchemy model registry vs. a live, migrated database.

WHY THIS TOOL EXISTS (WO-QTI-PHANTOM-SCHEMA)
---------------------------------------------
For most of this project's history, dev databases were bootstrapped with
``Base.metadata.create_all()`` (still the ``lifespan`` startup path in
``src/main.py`` today -- it runs *in addition to* Alembic, not instead of
it). ``create_all`` silently ADDS whatever the current models declare, so a
long-lived dev DB quietly healed drift between the SQLAlchemy models and the
Alembic-migration-built schema that a genuinely fresh deploy gets instead.
Two confirmed, live examples at the time this tool was written:

  * ``market_prices.port_id`` (migration) vs. ``MarketPrice.station_id``
    (model) -- same story already known for ``aria_market_intelligence``.
    Any query written against the model's ``station_id`` attribute 500s on
    a fresh-migrated DB with "column market_prices.station_id does not
    exist", while a create_all'd dev DB never notices because create_all
    added the column under whichever name the model happened to have that
    day.
  * ``warp_layer`` / ``contract_status`` / ``enhanced_market_transactions.
    transaction_type`` -- a plain ``sa.Enum(SomePyEnum)`` column with no
    ``values_callable`` serializes the Python member *NAME* (e.g.
    ``"POSTED"``), but the Alembic-built Postgres enum type only contains
    the lowercase *VALUES* (e.g. ``"posted"``) declared in the model's
    ``class SomeEnum(enum.Enum): POSTED = "posted"``. Every INSERT/UPDATE
    against a fresh DB then 500s with "invalid input value for enum". A
    create_all'd DB never notices because SQLAlchemy derives the enum DDL
    from the *same* Python-side resolution it uses for writes, so the two
    can never disagree there.

This script is read-only and self-contained: it reflects a live database
via SQLAlchemy's ``Inspector`` (and, for enums, the inspector's own
reflected Postgres ``ENUM`` type, which SQLAlchemy already fills in from
``pg_catalog`` -- no hand-rolled SQL against ``pg_type``/``pg_enum`` needed)
and diffs it against ``Base.metadata`` built from the app's real model
registry. It never writes to the target database.

MODEL REGISTRY LOADING -- why ``import src.main`` and not ``import
src.models``
--------------------------------------------------------------------------
Both plausible "import everything" entry points at HEAD turned out to be
incomplete:

  * ``alembic/env.py`` hand-imports a short list of models (its own
    comment claims "Import all models to ensure they're all registered",
    but the list is a small subset -- User/Player/Ship/Planet/Station and a
    handful more, not the other ~50 model modules).
  * ``src/models/__init__.py`` is much more complete but is ALSO missing
    three modules, verified by diffing its import list against
    ``ls src/models/*.py``: ``contract.py``, ``combat.py`` and
    ``player_analytics.py``. Critically, ``contract.py`` owns
    ``ContractStatus`` -- one of the two enums this very tool exists to
    catch -- so building ``Base.metadata`` from ``src.models`` alone would
    make the tool blind to its own motivating defect.

``src/main.py`` (via ``src.api.api.api_router``) transitively imports every
route module, and every route module imports the model classes it actually
queries -- ``contracts.py``/``trading.py`` import ``src.models.contract``,
``combat_service.py`` imports ``src.models.combat``, etc. So importing
``src.main`` is the same "complete registry" strategy ``tests/conftest.py``
already relies on (``from src.main import app as actual_app``), and it is
provably more complete than either of the two hand-rolled lists above.

Importing ``src.main`` does NOT touch a database: ``Base.metadata.
create_all()`` and the admin/faction bootstrap both live inside the
``lifespan`` async context manager (``@asynccontextmanager``), which only
executes when a real ASGI server enters it (uvicorn, or a TestClient with
lifespan support) -- plain module import never calls it.

``src/core/config.py``'s ``Settings`` model still eagerly validates
``DATABASE_URL``/``JWT_SECRET``/``ADMIN_USERNAME``/``ADMIN_PASSWORD`` at
import time with no defaults (raises ``ValueError``/``ValidationError`` if
absent), exactly like it does for the pytest suite. ``ensure_dummy_settings_
env()`` below sets safe dummy values for those four vars via ``os.environ.
setdefault`` -- filling gaps only, never overriding a real environment --
purely so the import succeeds. These dummy values are ENTIRELY separate
from the actual database this tool inspects: the live target URL always
comes from ``--database-url`` or the real ``DATABASE_URL`` env var, read
BEFORE any dummy value is applied, and is used to build an independent
SQLAlchemy engine that ``Settings`` never sees.

USAGE
-----
    python scripts/schema_parity_check.py --database-url postgresql://...
    python scripts/schema_parity_check.py                 # uses $DATABASE_URL
    python scripts/schema_parity_check.py --json           # machine-readable
    python scripts/schema_parity_check.py --schema public

EXIT CODES
----------
    0  -- ran cleanly, zero confirmed breaks.
    1  -- ran cleanly, found at least one CONFIRMED break: a model table
          missing from the DB, a model column missing from the DB, or an
          enum label the ORM can send that the live Postgres enum type
          lacks. Nullable/coarse-type-class mismatches and "unmapped in
          DB" findings are reported but do NOT flip this to 1 -- they are
          informational (may be legitimate: manually-added columns,
          alembic_version, etc).
    2  -- could not run at all (no database URL given, model registry
          failed to import, or the target database was unreachable).
"""

import argparse
import dataclasses
import json
import os
import pathlib
import sys
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Set

import sqlalchemy as sa
from sqlalchemy import inspect as sa_inspect
from sqlalchemy import types as sqltypes
from sqlalchemy.dialects import postgresql as pg_types
from sqlalchemy.engine import Engine, make_url

# --------------------------------------------------------------------------
# Dummy settings environment -- see module docstring's "MODEL REGISTRY
# LOADING" section. setdefault only: never overrides a real environment.
# --------------------------------------------------------------------------

_DUMMY_ENV_DEFAULTS = {
    "DATABASE_URL": "postgresql://schema-parity-check:unused@localhost:5432/unused",
    "JWT_SECRET": "schema-parity-check-dummy-jwt-secret-not-a-real-secret-32c",
    "ADMIN_USERNAME": "schema-parity-check",
    "ADMIN_PASSWORD": "schema-parity-check-dummy-password",
}


def ensure_dummy_settings_env() -> None:
    """Fill in the four env vars ``src.core.config.Settings`` requires with
    no default, but only where the real environment doesn't already have
    them. This exists purely to let ``import src.main`` succeed; it has no
    bearing on which database this tool actually inspects (see
    ``main()``, which reads the real target URL before this is called)."""
    for key, value in _DUMMY_ENV_DEFAULTS.items():
        os.environ.setdefault(key, value)


# --------------------------------------------------------------------------
# Coarse type bucketing -- shared by model-side Column.type and live-side
# reflected inspector column types, so the comparison never trips on
# dialect noise like VARCHAR(50) vs VARCHAR(120) or NUMERIC(10,2) vs
# NUMERIC(12,4).
# --------------------------------------------------------------------------


def coarse_type_bucket(type_obj: Any) -> str:
    """Bucket a SQLAlchemy TypeEngine instance into a small set of coarse
    categories.

    IMPORTANT ORDERING: ``sqlalchemy.Enum`` (and the reflected postgresql
    ``ENUM``) both subclass ``String`` in SQLAlchemy's own type hierarchy,
    so the ENUM check below MUST run before the STRING check, or every
    enum column would silently bucket as STRING and a model-Enum-vs-live-
    VARCHAR drift would go undetected.
    """
    if isinstance(type_obj, (pg_types.ENUM, sqltypes.Enum)):
        return "ENUM"
    if isinstance(type_obj, (pg_types.JSONB, pg_types.JSON, sqltypes.JSON)):
        return "JSON"
    if isinstance(type_obj, (pg_types.ARRAY, sqltypes.ARRAY)):
        return "ARRAY"
    if isinstance(type_obj, (pg_types.UUID, sqltypes.Uuid)):
        return "UUID"
    if isinstance(type_obj, sqltypes.Boolean):
        return "BOOLEAN"
    if isinstance(type_obj, (sqltypes.DateTime, sqltypes.Date, sqltypes.Time)):
        return "DATETIME"
    if isinstance(type_obj, sqltypes.Integer):
        return "INTEGER"
    if isinstance(type_obj, sqltypes.Numeric):
        return "NUMERIC"
    if isinstance(type_obj, (sqltypes.String, sqltypes.Text)):
        return "STRING"
    return f"OTHER:{type(type_obj).__name__}"


# --------------------------------------------------------------------------
# ORM-side enum label resolution -- the critical computation.
# --------------------------------------------------------------------------


def orm_enum_labels(enum_type: sa.Enum) -> List[str]:
    """The exact label strings SQLAlchemy will bind when writing this Enum
    column -- i.e. what the live Postgres enum type must contain for every
    write to succeed. Three cases, in SQLAlchemy's own resolution order:

    1. ``values_callable`` supplied -> its output (normally
       ``[m.value for m in enum_class]``). This is the FIX pattern (see
       ``src/models/player_warp_knowledge.py``).
    2. No ``values_callable``, backed by a real PEP-435 ``enum.Enum``
       class -> the member NAMES (SQLAlchemy's default). This is the exact
       defect class this tool exists to catch -- ``warp_layer`` (fixed),
       ``contract_status`` and ``enhanced_market_transactions.
       transaction_type`` (both unfixed at the time this tool was written)
       all hit this.
    3. A bare string-list Enum (no backing Python enum class) -> those
       strings verbatim.
    """
    if enum_type.values_callable is not None and enum_type.enum_class is not None:
        return list(enum_type.values_callable(enum_type.enum_class))
    if enum_type.enum_class is not None:
        return [member.name for member in enum_type.enum_class]
    return list(enum_type.enums)


# --------------------------------------------------------------------------
# Data structures
# --------------------------------------------------------------------------


@dataclass
class ColumnInfo:
    name: str
    nullable: bool
    type_bucket: str
    enum_name: Optional[str] = None
    enum_labels: Optional[List[str]] = None  # set only for Enum-typed columns


@dataclass
class ModelSchema:
    tables: Dict[str, Dict[str, ColumnInfo]]


@dataclass
class LiveSchema:
    tables: Dict[str, Dict[str, ColumnInfo]]


@dataclass
class TableDiff:
    missing_in_db: List[str]  # confirmed break
    unmapped_in_db: List[str]  # informational -- may be legitimate


@dataclass
class ColumnDiff:
    table: str
    missing_in_db: List[str]  # confirmed break
    unmapped_in_db: List[str]  # informational -- may be legitimate
    nullable_mismatches: List[Dict[str, Any]]  # informational
    type_mismatches: List[Dict[str, Any]]  # informational


@dataclass
class EnumDiff:
    table: str
    column: str
    enum_name: Optional[str]
    orm_only_labels: List[str]  # confirmed break -- ORM can send, DB rejects
    db_only_labels: List[str]  # informational -- DB allows, ORM never sends


@dataclass
class ParityReport:
    table_diff: TableDiff
    column_diffs: List[ColumnDiff]
    enum_diffs: List[EnumDiff]

    @property
    def confirmed_breaks(self) -> List[str]:
        breaks: List[str] = []
        for t in self.table_diff.missing_in_db:
            breaks.append(f"MISSING TABLE (model declares, DB lacks): {t}")
        for cd in self.column_diffs:
            for c in cd.missing_in_db:
                breaks.append(f"MISSING COLUMN (model declares, DB lacks): {cd.table}.{c}")
        for ed in self.enum_diffs:
            if ed.orm_only_labels:
                breaks.append(
                    f"ENUM SERIALIZATION BREAK: {ed.table}.{ed.column} "
                    f"(pg type {ed.enum_name!r}) -- ORM can send {ed.orm_only_labels} "
                    f"but the live enum type doesn't have them"
                )
        return breaks


# --------------------------------------------------------------------------
# Model-side extraction (pure -- operates on an already-populated MetaData)
# --------------------------------------------------------------------------


def build_model_schema(metadata: sa.MetaData) -> ModelSchema:
    tables: Dict[str, Dict[str, ColumnInfo]] = {}
    for table_name, table in metadata.tables.items():
        cols: Dict[str, ColumnInfo] = {}
        for col in table.columns:
            enum_name = None
            enum_labels = None
            if isinstance(col.type, sqltypes.Enum):
                enum_name = col.type.name
                enum_labels = orm_enum_labels(col.type)
            cols[col.name] = ColumnInfo(
                name=col.name,
                nullable=bool(col.nullable),
                type_bucket=coarse_type_bucket(col.type),
                enum_name=enum_name,
                enum_labels=enum_labels,
            )
        tables[table_name] = cols
    return ModelSchema(tables=tables)


def load_model_schema() -> ModelSchema:
    """Import the app's full model registry and build a ModelSchema from
    ``Base.metadata``. See the module docstring's "MODEL REGISTRY LOADING"
    section for why this imports ``src.main`` rather than ``src.models``."""
    repo_root = pathlib.Path(__file__).resolve().parents[1]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))
    ensure_dummy_settings_env()
    import src.main  # noqa: F401  (side effect: every router's model imports populate Base.metadata)
    from src.core.database import Base

    return build_model_schema(Base.metadata)


# --------------------------------------------------------------------------
# Live-side extraction (I/O -- talks to the real database via inspector)
# --------------------------------------------------------------------------


def build_live_schema(engine: Engine, schema: Optional[str] = None) -> LiveSchema:
    inspector = sa_inspect(engine)
    tables: Dict[str, Dict[str, ColumnInfo]] = {}
    for table_name in inspector.get_table_names(schema=schema):
        cols: Dict[str, ColumnInfo] = {}
        for col in inspector.get_columns(table_name, schema=schema):
            type_obj = col["type"]
            enum_name = None
            enum_labels = None
            if isinstance(type_obj, (pg_types.ENUM, sqltypes.Enum)) and getattr(type_obj, "enums", None):
                enum_name = getattr(type_obj, "name", None)
                enum_labels = list(type_obj.enums)
            cols[col["name"]] = ColumnInfo(
                name=col["name"],
                nullable=bool(col["nullable"]),
                type_bucket=coarse_type_bucket(type_obj),
                enum_name=enum_name,
                enum_labels=enum_labels,
            )
        tables[table_name] = cols
    return LiveSchema(tables=tables)


# --------------------------------------------------------------------------
# Diffing (pure -- operates on ModelSchema/LiveSchema-shaped data only, no
# I/O, so it's directly unit-testable with fabricated data).
# --------------------------------------------------------------------------


def diff_tables(model_tables: Set[str], live_tables: Set[str]) -> TableDiff:
    return TableDiff(
        missing_in_db=sorted(model_tables - live_tables),
        unmapped_in_db=sorted(live_tables - model_tables),
    )


def diff_columns(
    table: str,
    model_cols: Dict[str, ColumnInfo],
    live_cols: Dict[str, ColumnInfo],
) -> Optional[ColumnDiff]:
    model_names = set(model_cols)
    live_names = set(live_cols)
    missing_in_db = sorted(model_names - live_names)
    unmapped_in_db = sorted(live_names - model_names)
    nullable_mismatches: List[Dict[str, Any]] = []
    type_mismatches: List[Dict[str, Any]] = []
    for name in sorted(model_names & live_names):
        model_col = model_cols[name]
        live_col = live_cols[name]
        if model_col.nullable != live_col.nullable:
            nullable_mismatches.append(
                {"column": name, "model_nullable": model_col.nullable, "db_nullable": live_col.nullable}
            )
        if model_col.type_bucket != live_col.type_bucket:
            type_mismatches.append(
                {"column": name, "model_type": model_col.type_bucket, "db_type": live_col.type_bucket}
            )
    if not (missing_in_db or unmapped_in_db or nullable_mismatches or type_mismatches):
        return None
    return ColumnDiff(
        table=table,
        missing_in_db=missing_in_db,
        unmapped_in_db=unmapped_in_db,
        nullable_mismatches=nullable_mismatches,
        type_mismatches=type_mismatches,
    )


def diff_enum_labels(
    table: str,
    column: str,
    enum_name: Optional[str],
    orm_labels: List[str],
    db_labels: List[str],
) -> Optional[EnumDiff]:
    orm_set, db_set = set(orm_labels), set(db_labels)
    orm_only = sorted(orm_set - db_set)
    db_only = sorted(db_set - orm_set)
    if not orm_only and not db_only:
        return None
    return EnumDiff(table=table, column=column, enum_name=enum_name, orm_only_labels=orm_only, db_only_labels=db_only)


def diff_schemas(model_schema: ModelSchema, live_schema: LiveSchema) -> ParityReport:
    table_diff = diff_tables(set(model_schema.tables), set(live_schema.tables))
    column_diffs: List[ColumnDiff] = []
    enum_diffs: List[EnumDiff] = []
    shared_tables = sorted(set(model_schema.tables) & set(live_schema.tables))
    for table in shared_tables:
        model_cols = model_schema.tables[table]
        live_cols = live_schema.tables[table]
        col_diff = diff_columns(table, model_cols, live_cols)
        if col_diff is not None:
            column_diffs.append(col_diff)
        for col_name, col_info in sorted(model_cols.items()):
            if col_info.enum_labels is None:
                continue
            live_col = live_cols.get(col_name)
            if live_col is None or live_col.enum_labels is None:
                # Missing column / type-bucket mismatch already flagged above.
                continue
            enum_diff = diff_enum_labels(
                table, col_name, col_info.enum_name, col_info.enum_labels, live_col.enum_labels
            )
            if enum_diff is not None:
                enum_diffs.append(enum_diff)
    return ParityReport(table_diff=table_diff, column_diffs=column_diffs, enum_diffs=enum_diffs)


# --------------------------------------------------------------------------
# Reporting
# --------------------------------------------------------------------------


def _print_table_section(report: ParityReport) -> None:
    print("\n-- Tables --")
    if report.table_diff.missing_in_db:
        print("  MISSING FROM DB (model declares, migration never created):")
        for t in report.table_diff.missing_in_db:
            print(f"    - {t}")
    if report.table_diff.unmapped_in_db:
        print("  Unmapped in model metadata (may be legitimate -- e.g. alembic_version):")
        for t in report.table_diff.unmapped_in_db:
            print(f"    - {t}")
    if not report.table_diff.missing_in_db and not report.table_diff.unmapped_in_db:
        print("  clean -- every model table exists in the DB")


def _print_column_section(report: ParityReport) -> None:
    print("\n-- Columns --")
    if not report.column_diffs:
        print("  clean -- no column drift on any shared table")
        return
    for col_diff in report.column_diffs:
        print(f"  {col_diff.table}:")
        for c in col_diff.missing_in_db:
            print(f"    MISSING COLUMN (model declares, DB lacks): {c}")
        for c in col_diff.unmapped_in_db:
            print(f"    unmapped DB column (not in model, may be legitimate): {c}")
        for m in col_diff.nullable_mismatches:
            print(f"    nullable mismatch: {m['column']} (model={m['model_nullable']}, db={m['db_nullable']})")
        for m in col_diff.type_mismatches:
            print(f"    type-class mismatch: {m['column']} (model={m['model_type']}, db={m['db_type']})")


def _print_enum_section(report: ParityReport) -> None:
    print("\n-- Enums --")
    if not report.enum_diffs:
        print("  clean -- every mapped Enum column's ORM-sendable labels are present in the live PG type")
        return
    for enum_diff in report.enum_diffs:
        print(f"  {enum_diff.table}.{enum_diff.column} (pg type {enum_diff.enum_name!r}):")
        if enum_diff.orm_only_labels:
            print(f"    CONFIRMED BREAK -- ORM can send these but the live type lacks them: "
                  f"{enum_diff.orm_only_labels}")
            print("      fix: add values_callable=lambda obj: [e.value for e in obj] to the Enum(...) column")
        if enum_diff.db_only_labels:
            print(f"    informational -- live type has these but the ORM never sends them: "
                  f"{enum_diff.db_only_labels}")


def print_human_report(report: ParityReport, target_label: str = "") -> None:
    print("=" * 78)
    print(f"Schema Parity Report{f' -- {target_label}' if target_label else ''}")
    print("=" * 78)

    _print_table_section(report)
    _print_column_section(report)
    _print_enum_section(report)

    breaks = report.confirmed_breaks
    print("\n" + "=" * 78)
    if breaks:
        print(f"RESULT: {len(breaks)} CONFIRMED BREAK(S)")
        for b in breaks:
            print(f"  - {b}")
    else:
        print("RESULT: clean -- zero confirmed breaks")
    print("=" * 78)


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Diff the SQLAlchemy model registry against a live, migrated database. "
            "Read-only -- never writes to the target database."
        )
    )
    parser.add_argument(
        "--database-url",
        dest="database_url",
        default=None,
        help="Target Postgres URL to inspect. Falls back to the DATABASE_URL env var.",
    )
    parser.add_argument(
        "--schema",
        default=None,
        help="Postgres schema to inspect (default: driver default, typically 'public').",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON instead of the human-readable report.",
    )
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)

    target_url = args.database_url or os.environ.get("DATABASE_URL")
    if not target_url:
        print(
            "ERROR: no database URL provided. Pass --database-url or set the DATABASE_URL env var.",
            file=sys.stderr,
        )
        return 2

    try:
        safe_url = make_url(target_url).render_as_string(hide_password=True)
    except Exception:
        safe_url = "<unparseable URL>"

    try:
        model_schema = load_model_schema()
    except Exception as exc:
        print(f"ERROR: failed to import the app's model registry: {exc}", file=sys.stderr)
        return 2

    engine: Optional[Engine] = None
    try:
        engine = sa.create_engine(target_url)
        live_schema = build_live_schema(engine, schema=args.schema)
    except Exception as exc:
        print(f"ERROR: could not connect to {safe_url}: {exc}", file=sys.stderr)
        return 2
    finally:
        if engine is not None:
            engine.dispose()

    report = diff_schemas(model_schema, live_schema)

    if args.json:
        print(json.dumps(dataclasses.asdict(report), indent=2, default=str))
    else:
        print_human_report(report, target_label=safe_url)

    return 1 if report.confirmed_breaks else 0


if __name__ == "__main__":
    sys.exit(main())
