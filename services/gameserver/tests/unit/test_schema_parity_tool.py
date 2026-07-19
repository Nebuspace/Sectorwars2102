"""Unit tests for scripts/schema_parity_check.py's LOGIC -- no live Postgres.

Everything here exercises pure functions against fabricated data: synthetic
SQLAlchemy Enum columns for the label-resolution computation, and
hand-built ModelSchema/LiveSchema-shaped ColumnInfo dicts for the diff-set
logic. The live-DB run (against a fresh-migrated database) is the
orchestrator's job on heimdall -- these tests only prove the tool computes
the right expectations.

The module under test is loaded by file path (not `import scripts.
schema_parity_check`) because scripts/ isn't a package and pytest's import
mode shouldn't be relied on to make it one.
"""

import enum
import importlib.util
import io
import json
import os
import pathlib
import sys
from contextlib import redirect_stdout

import pytest
import sqlalchemy as sa

_SCRIPT_PATH = pathlib.Path(__file__).resolve().parents[2] / "scripts" / "schema_parity_check.py"
_spec = importlib.util.spec_from_file_location("schema_parity_check", _SCRIPT_PATH)
spc = importlib.util.module_from_spec(_spec)
sys.modules["schema_parity_check"] = spc
_spec.loader.exec_module(spc)


# --------------------------------------------------------------------------
# orm_enum_labels -- the label-resolution computation the whole tool hinges on
# --------------------------------------------------------------------------


class _ContractStatus(enum.Enum):
    """Shaped like the real, currently-unfixed src/models/contract.py enum."""

    POSTED = "posted"
    ACCEPTED = "accepted"
    IN_TRANSIT = "in_transit"


class TestOrmEnumLabels:
    def test_plain_pyenum_sends_member_names(self):
        """No values_callable -> SQLAlchemy's default: the member NAMES.
        This is the exact defect class WO-QTI-PHANTOM-SCHEMA exists to
        catch (warp_layer/contract_status/transaction_type all hit this)."""
        col_type = sa.Enum(_ContractStatus, name="contract_status")
        assert spc.orm_enum_labels(col_type) == ["POSTED", "ACCEPTED", "IN_TRANSIT"]

    def test_values_callable_sends_mapped_values(self):
        """The fix pattern: values_callable resolves to member.value strings."""
        col_type = sa.Enum(
            _ContractStatus,
            name="contract_status",
            values_callable=lambda obj: [e.value for e in obj],
        )
        assert spc.orm_enum_labels(col_type) == ["posted", "accepted", "in_transit"]

    def test_string_list_enum_sends_the_strings_verbatim(self):
        col_type = sa.Enum("alpha", "beta", "gamma", name="greek")
        assert spc.orm_enum_labels(col_type) == ["alpha", "beta", "gamma"]


# --------------------------------------------------------------------------
# coarse_type_bucket
# --------------------------------------------------------------------------


class TestCoarseTypeBucket:
    def test_enum_buckets_as_enum_not_string(self):
        """Regression pin: sqlalchemy.Enum subclasses String in SQLAlchemy's
        own type hierarchy. If the ENUM isinstance check ever moves below
        the STRING check, every enum column silently misreads as STRING and
        a model-Enum-vs-live-VARCHAR drift goes undetected."""
        assert spc.coarse_type_bucket(sa.Enum(_ContractStatus, name="x")) == "ENUM"

    def test_uuid(self):
        from sqlalchemy.dialects.postgresql import UUID

        assert spc.coarse_type_bucket(UUID(as_uuid=True)) == "UUID"

    def test_jsonb(self):
        from sqlalchemy.dialects.postgresql import JSONB

        assert spc.coarse_type_bucket(JSONB()) == "JSON"

    def test_array(self):
        from sqlalchemy.dialects.postgresql import ARRAY

        assert spc.coarse_type_bucket(ARRAY(sa.Integer)) == "ARRAY"

    def test_boolean(self):
        assert spc.coarse_type_bucket(sa.Boolean()) == "BOOLEAN"

    def test_datetime_and_date(self):
        assert spc.coarse_type_bucket(sa.DateTime(timezone=True)) == "DATETIME"
        assert spc.coarse_type_bucket(sa.Date()) == "DATETIME"

    def test_integer_variants(self):
        assert spc.coarse_type_bucket(sa.Integer()) == "INTEGER"
        assert spc.coarse_type_bucket(sa.BigInteger()) == "INTEGER"
        assert spc.coarse_type_bucket(sa.SmallInteger()) == "INTEGER"

    def test_numeric_and_float_share_a_bucket(self):
        assert spc.coarse_type_bucket(sa.Numeric(10, 2)) == "NUMERIC"
        assert spc.coarse_type_bucket(sa.Float()) == "NUMERIC"

    def test_string_lengths_are_ignored(self):
        """Don't drown in dialect noise: VARCHAR(50) and VARCHAR(500) must
        bucket identically."""
        assert spc.coarse_type_bucket(sa.String(50)) == spc.coarse_type_bucket(sa.String(500)) == "STRING"
        assert spc.coarse_type_bucket(sa.Text()) == "STRING"

    def test_unknown_type_falls_back_to_other_with_class_name(self):
        class Weird(sa.types.TypeEngine):
            pass

        assert spc.coarse_type_bucket(Weird()) == "OTHER:Weird"


# --------------------------------------------------------------------------
# diff_tables
# --------------------------------------------------------------------------


class TestDiffTables:
    def test_missing_and_unmapped_and_shared(self):
        model_tables = {"players", "ships", "market_prices"}
        live_tables = {"players", "ships", "alembic_version"}
        result = spc.diff_tables(model_tables, live_tables)
        assert result.missing_in_db == ["market_prices"]
        assert result.unmapped_in_db == ["alembic_version"]

    def test_fully_shared_is_clean(self):
        result = spc.diff_tables({"players"}, {"players"})
        assert result.missing_in_db == []
        assert result.unmapped_in_db == []


# --------------------------------------------------------------------------
# diff_columns -- fabricated inspector-style ColumnInfo dicts
# --------------------------------------------------------------------------


def _col(name, nullable=True, type_bucket="STRING", **kw):
    return spc.ColumnInfo(name=name, nullable=nullable, type_bucket=type_bucket, **kw)


class TestDiffColumns:
    def test_clean_table_returns_none(self):
        model_cols = {"id": _col("id", nullable=False, type_bucket="UUID")}
        live_cols = {"id": _col("id", nullable=False, type_bucket="UUID")}
        assert spc.diff_columns("players", model_cols, live_cols) is None

    def test_market_prices_station_id_vs_port_id_regression(self):
        """Real, confirmed HEAD defect: MarketPrice.station_id (model) vs.
        market_prices.port_id (live migration) -- the same class of bug
        already fixed once for aria_market_intelligence."""
        model_cols = {
            "id": _col("id", nullable=False, type_bucket="UUID"),
            "station_id": _col("station_id", nullable=False, type_bucket="UUID"),
            "commodity": _col("commodity", nullable=False, type_bucket="STRING"),
        }
        live_cols = {
            "id": _col("id", nullable=False, type_bucket="UUID"),
            "port_id": _col("port_id", nullable=False, type_bucket="UUID"),
            "commodity": _col("commodity", nullable=False, type_bucket="STRING"),
        }
        result = spc.diff_columns("market_prices", model_cols, live_cols)
        assert result is not None
        assert result.missing_in_db == ["station_id"]
        assert result.unmapped_in_db == ["port_id"]
        assert result.nullable_mismatches == []
        assert result.type_mismatches == []

    def test_nullable_mismatch(self):
        model_cols = {"note": _col("note", nullable=False)}
        live_cols = {"note": _col("note", nullable=True)}
        result = spc.diff_columns("t", model_cols, live_cols)
        assert result is not None
        assert result.nullable_mismatches == [{"column": "note", "model_nullable": False, "db_nullable": True}]
        assert result.missing_in_db == []
        assert result.unmapped_in_db == []

    def test_type_class_mismatch(self):
        model_cols = {"amount": _col("amount", type_bucket="UUID")}
        live_cols = {"amount": _col("amount", type_bucket="INTEGER")}
        result = spc.diff_columns("t", model_cols, live_cols)
        assert result is not None
        assert result.type_mismatches == [{"column": "amount", "model_type": "UUID", "db_type": "INTEGER"}]


# --------------------------------------------------------------------------
# diff_enum_labels
# --------------------------------------------------------------------------


class TestDiffEnumLabels:
    def test_clean_match_returns_none(self):
        assert spc.diff_enum_labels("t", "c", "e", ["a", "b"], ["a", "b"]) is None

    def test_contract_status_name_vs_value_regression(self):
        """Real, confirmed HEAD defect: contract.py's ContractStatus has no
        values_callable, so the ORM sends member NAMES while the migration-
        built PG enum only holds lowercase VALUES."""
        orm_labels = ["POSTED", "ACCEPTED", "IN_TRANSIT"]
        db_labels = ["posted", "accepted", "in_transit"]
        result = spc.diff_enum_labels("contracts", "status", "contract_status", orm_labels, db_labels)
        assert result is not None
        assert result.orm_only_labels == ["ACCEPTED", "IN_TRANSIT", "POSTED"]
        assert result.db_only_labels == ["accepted", "in_transit", "posted"]

    def test_db_only_labels_are_informational_not_a_break(self):
        """DB has an extra legacy label the ORM never sends -- reported, but
        not a confirmed break on its own."""
        result = spc.diff_enum_labels("t", "c", "e", ["a"], ["a", "legacy_value"])
        assert result is not None
        assert result.orm_only_labels == []
        assert result.db_only_labels == ["legacy_value"]


# --------------------------------------------------------------------------
# diff_schemas -- end-to-end wiring + confirmed_breaks aggregation
# --------------------------------------------------------------------------


class TestDiffSchemasAndConfirmedBreaks:
    def _build(self):
        model_schema = spc.ModelSchema(
            tables={
                "players": {"id": _col("id", nullable=False, type_bucket="UUID")},
                "market_prices": {
                    "id": _col("id", nullable=False, type_bucket="UUID"),
                    "station_id": _col("station_id", nullable=False, type_bucket="UUID"),
                },
                "contracts": {
                    "id": _col("id", nullable=False, type_bucket="UUID"),
                    "status": _col(
                        "status",
                        nullable=False,
                        type_bucket="ENUM",
                        enum_name="contract_status",
                        enum_labels=["POSTED", "ACCEPTED"],
                    ),
                },
                "phantom_table": {"id": _col("id", nullable=False, type_bucket="UUID")},
            }
        )
        live_schema = spc.LiveSchema(
            tables={
                "players": {"id": _col("id", nullable=False, type_bucket="UUID")},
                "market_prices": {
                    "id": _col("id", nullable=False, type_bucket="UUID"),
                    "port_id": _col("port_id", nullable=False, type_bucket="UUID"),
                },
                "contracts": {
                    "id": _col("id", nullable=False, type_bucket="UUID"),
                    "status": _col(
                        "status",
                        nullable=False,
                        type_bucket="ENUM",
                        enum_name="contract_status",
                        enum_labels=["posted", "accepted"],
                    ),
                },
                "alembic_version": {"version_num": _col("version_num", nullable=False, type_bucket="STRING")},
            }
        )
        return model_schema, live_schema

    def test_full_report_shape(self):
        model_schema, live_schema = self._build()
        report = spc.diff_schemas(model_schema, live_schema)

        assert report.table_diff.missing_in_db == ["phantom_table"]
        assert report.table_diff.unmapped_in_db == ["alembic_version"]

        by_table = {cd.table: cd for cd in report.column_diffs}
        assert by_table["market_prices"].missing_in_db == ["station_id"]
        assert by_table["market_prices"].unmapped_in_db == ["port_id"]

        assert len(report.enum_diffs) == 1
        assert report.enum_diffs[0].table == "contracts"
        assert report.enum_diffs[0].orm_only_labels == ["ACCEPTED", "POSTED"]

    def test_confirmed_breaks_includes_only_gating_categories(self):
        model_schema, live_schema = self._build()
        report = spc.diff_schemas(model_schema, live_schema)
        breaks = report.confirmed_breaks

        joined = "\n".join(breaks)
        assert "phantom_table" in joined
        assert "market_prices.station_id" in joined
        assert "contracts.status" in joined
        # unmapped-in-db (port_id / alembic_version) must NOT gate the exit code
        assert "port_id" not in joined
        assert "alembic_version" not in joined

    def test_clean_schemas_produce_zero_confirmed_breaks(self):
        cols = {"id": _col("id", nullable=False, type_bucket="UUID")}
        model_schema = spc.ModelSchema(tables={"players": dict(cols)})
        live_schema = spc.LiveSchema(tables={"players": dict(cols)})
        report = spc.diff_schemas(model_schema, live_schema)
        assert report.confirmed_breaks == []


# --------------------------------------------------------------------------
# ensure_dummy_settings_env -- setdefault-only behavior
# --------------------------------------------------------------------------


class TestEnsureDummySettingsEnv:
    def test_fills_missing_vars(self, monkeypatch):
        for key in spc._DUMMY_ENV_DEFAULTS:
            monkeypatch.delenv(key, raising=False)
        spc.ensure_dummy_settings_env()
        for key, value in spc._DUMMY_ENV_DEFAULTS.items():
            assert os.environ[key] == value

    def test_never_overrides_a_real_value(self, monkeypatch):
        placeholder = "test-placeholder-value-must-survive-untouched"  # noqa: S105 (test literal, not a real secret)
        monkeypatch.setenv("JWT_SECRET", placeholder)
        spc.ensure_dummy_settings_env()
        assert os.environ["JWT_SECRET"] == placeholder


# --------------------------------------------------------------------------
# CLI argument parsing
# --------------------------------------------------------------------------


class TestParseArgs:
    def test_defaults(self):
        args = spc.parse_args([])
        assert args.database_url is None
        assert args.schema is None
        assert args.json is False

    def test_flags(self):
        args = spc.parse_args(["--database-url", "postgresql://x", "--schema", "public", "--json"])
        assert args.database_url == "postgresql://x"
        assert args.schema == "public"
        assert args.json is True


# --------------------------------------------------------------------------
# main() -- CLI orchestration, fully mocked so no network/DB access happens
# --------------------------------------------------------------------------


class TestMain:
    def test_no_url_returns_2(self, monkeypatch, capsys):
        monkeypatch.delenv("DATABASE_URL", raising=False)
        rc = spc.main([])
        assert rc == 2
        assert "no database URL" in capsys.readouterr().err

    def test_model_import_failure_returns_2(self, monkeypatch, capsys):
        def _boom():
            raise RuntimeError("simulated import failure")

        monkeypatch.setattr(spc, "load_model_schema", _boom)
        rc = spc.main(["--database-url", "postgresql://fake:fake@localhost:5432/fake"])
        assert rc == 2
        assert "failed to import" in capsys.readouterr().err

    def test_connection_failure_returns_2(self, monkeypatch, capsys):
        monkeypatch.setattr(spc, "load_model_schema", lambda: spc.ModelSchema(tables={}))

        def _boom(*args, **kwargs):
            raise RuntimeError("simulated connection failure")

        monkeypatch.setattr(spc, "build_live_schema", _boom)
        rc = spc.main(["--database-url", "postgresql://fake:fake@localhost:5432/fake"])
        assert rc == 2
        err = capsys.readouterr().err
        assert "could not connect" in err
        # password must never be echoed back in the error message
        assert "fake:fake" not in err

    def test_clean_report_returns_0(self, monkeypatch, capsys):
        monkeypatch.setattr(spc, "load_model_schema", lambda: spc.ModelSchema(tables={"players": {}}))
        monkeypatch.setattr(spc, "build_live_schema", lambda *a, **k: spc.LiveSchema(tables={"players": {}}))
        rc = spc.main(["--database-url", "postgresql://fake:fake@localhost:5432/fake"])
        assert rc == 0
        assert "zero confirmed breaks" in capsys.readouterr().out

    def test_report_with_breaks_returns_1(self, monkeypatch):
        monkeypatch.setattr(spc, "load_model_schema", lambda: spc.ModelSchema(tables={"missing_table": {}}))
        monkeypatch.setattr(spc, "build_live_schema", lambda *a, **k: spc.LiveSchema(tables={}))
        rc = spc.main(["--database-url", "postgresql://fake:fake@localhost:5432/fake"])
        assert rc == 1

    def test_json_output_is_valid_json(self, monkeypatch, capsys):
        monkeypatch.setattr(spc, "load_model_schema", lambda: spc.ModelSchema(tables={"players": {}}))
        monkeypatch.setattr(spc, "build_live_schema", lambda *a, **k: spc.LiveSchema(tables={"players": {}}))
        rc = spc.main(["--database-url", "postgresql://fake:fake@localhost:5432/fake", "--json"])
        assert rc == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["table_diff"]["missing_in_db"] == []
        assert payload["column_diffs"] == []
        assert payload["enum_diffs"] == []


# --------------------------------------------------------------------------
# print_human_report -- smoke test that it doesn't crash on a populated report
# --------------------------------------------------------------------------


def test_print_human_report_smoke():
    report = spc.ParityReport(
        table_diff=spc.TableDiff(missing_in_db=["ghost"], unmapped_in_db=["extra"]),
        column_diffs=[
            spc.ColumnDiff(
                table="t",
                missing_in_db=["a"],
                unmapped_in_db=["b"],
                nullable_mismatches=[{"column": "c", "model_nullable": True, "db_nullable": False}],
                type_mismatches=[{"column": "d", "model_type": "UUID", "db_type": "INTEGER"}],
            )
        ],
        enum_diffs=[
            spc.EnumDiff(
                table="contracts",
                column="status",
                enum_name="contract_status",
                orm_only_labels=["POSTED"],
                db_only_labels=["legacy"],
            )
        ],
    )
    buf = io.StringIO()
    with redirect_stdout(buf):
        spc.print_human_report(report, target_label="postgresql://x/y")
    output = buf.getvalue()
    assert "CONFIRMED BREAK" in output
    assert "ghost" in output
    assert "contract_status" in output


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
