"""
Unit tests for admin_reports.py — validates response contracts against the
shapes expected by the admin-ui TypeScript components.

DB-free: a lightweight _FakeDB fakes just enough SQLAlchemy Query/execute API
to drive the route functions directly without an actual Postgres connection.
"""
import pytest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from src.api.routes.admin_reports import (
    _METRIC_CATALOG,
    _BUILTIN_TEMPLATES,
    _compute_metric,
    get_report_metrics,
    get_report_templates,
    generate_report,
    export_data,
    get_performance_metrics,
    ReportTemplate,
    ReportFilter,
)


# ---------------------------------------------------------------------------
# Minimal DB fake
# ---------------------------------------------------------------------------

class _FakeQuery:
    """Chains filter / join / label / order_by / limit / group_by / all /
    scalar — returns the value supplied at construction for scalar/all."""

    def __init__(self, scalar_value=0, all_value=None):
        self._scalar = scalar_value
        self._all = all_value or []

    # chain no-ops
    def filter(self, *a, **kw): return self
    def join(self, *a, **kw): return self
    def label(self, *a, **kw): return self
    def order_by(self, *a, **kw): return self
    def limit(self, *a, **kw): return self
    def group_by(self, *a, **kw): return self

    def scalar(self): return self._scalar
    def all(self): return self._all


class _FakeDB:
    def query(self, *args, **kwargs):
        return _FakeQuery(scalar_value=5, all_value=[])

    def execute(self, stmt):
        # Return a fake result with zero rows / safe defaults
        result = MagicMock()
        result.fetchone.return_value = (0,)
        result.fetchall.return_value = []
        return result


_FAKE_ADMIN = SimpleNamespace(id="admin-1", username="admin", is_admin=True)
_DB = _FakeDB()


# ---------------------------------------------------------------------------
# Metric catalog shape
# ---------------------------------------------------------------------------

def test_metric_catalog_structure():
    """Every metric in the catalog must have the 5 required fields the UI reads."""
    required = {"id", "name", "category", "dataType", "aggregations", "description"}
    for m in _METRIC_CATALOG:
        missing = required - set(m.keys())
        assert not missing, f"Metric {m.get('id')} missing fields: {missing}"


def test_builtin_templates_structure():
    """Every built-in template must have the 7 required fields."""
    required = {"id", "name", "description", "metrics", "filters", "groupBy", "sortBy", "visualization"}
    for t in _BUILTIN_TEMPLATES:
        missing = required - set(t.keys())
        assert not missing, f"Template {t.get('id')} missing fields: {missing}"
        assert isinstance(t["metrics"], list)


# ---------------------------------------------------------------------------
# GET /admin/reports/metrics
# ---------------------------------------------------------------------------

def test_get_report_metrics_shape():
    result = get_report_metrics(admin=_FAKE_ADMIN, db=_DB)
    assert "metrics" in result
    assert isinstance(result["metrics"], list)
    assert len(result["metrics"]) > 0


# ---------------------------------------------------------------------------
# GET /admin/reports/templates
# ---------------------------------------------------------------------------

def test_get_report_templates_shape():
    result = get_report_templates(admin=_FAKE_ADMIN, db=_DB)
    assert "templates" in result
    assert isinstance(result["templates"], list)
    assert len(result["templates"]) > 0


# ---------------------------------------------------------------------------
# POST /admin/reports/generate
# ---------------------------------------------------------------------------

def test_generate_report_result_shape():
    tmpl = ReportTemplate(
        id="test-1",
        name="Test Report",
        description="A test",
        metrics=["player_total_count", "market_total_transactions"],
        filters=[],
        groupBy=[],
        sortBy=[],
        visualization="table",
    )
    result = generate_report(template=tmpl, admin=_FAKE_ADMIN, db=_DB)
    assert result.id.startswith("report-")
    assert result.name == "Test Report"
    assert "generatedAt" in result.model_dump()
    assert "player_total_count" in result.data
    assert "market_total_transactions" in result.data


def test_generate_report_empty_metrics_raises():
    from fastapi import HTTPException
    tmpl = ReportTemplate(
        id="bad",
        name="Bad",
        description="",
        metrics=[],
        filters=[],
        groupBy=[],
        sortBy=[],
        visualization="table",
    )
    with pytest.raises(HTTPException) as exc:
        generate_report(template=tmpl, admin=_FAKE_ADMIN, db=_DB)
    assert exc.value.status_code == 400


def test_generate_report_unknown_metric_returns_zero():
    tmpl = ReportTemplate(
        id="unk",
        name="Unknown Metric Test",
        description="",
        metrics=["nonexistent_metric_xyz"],
        filters=[],
        groupBy=[],
        sortBy=[],
        visualization="table",
    )
    result = generate_report(template=tmpl, admin=_FAKE_ADMIN, db=_DB)
    assert result.data["nonexistent_metric_xyz"] == 0


# ---------------------------------------------------------------------------
# GET /admin/analytics/export — format validation
# ---------------------------------------------------------------------------

def test_export_invalid_format_raises():
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc:
        export_data(dataset="players", format="excel", admin=_FAKE_ADMIN, db=_DB)
    assert exc.value.status_code == 400


def test_export_invalid_dataset_raises():
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc:
        export_data(dataset="bogus_dataset", format="json", admin=_FAKE_ADMIN, db=_DB)
    assert exc.value.status_code == 400


def test_export_json_performance_redirect():
    """'performance' dataset returns a JSON response with a redirect note."""
    resp = export_data(dataset="performance", format="json", admin=_FAKE_ADMIN, db=_DB)
    assert resp.media_type == "application/json"
    assert b"performance/metrics" in resp.body


def test_export_players_json_empty_db():
    """Players export with empty DB returns JSON with count=0."""
    import json
    resp = export_data(dataset="players", format="json", admin=_FAKE_ADMIN, db=_DB)
    assert resp.media_type == "application/json"
    payload = json.loads(resp.body)
    assert payload["dataset"] == "players"
    assert "count" in payload
    assert "rows" in payload


def test_export_economy_csv_empty_db():
    """Economy export with empty DB returns a CSV content-type response."""
    resp = export_data(dataset="economy", format="csv", admin=_FAKE_ADMIN, db=_DB)
    assert "text/csv" in resp.media_type


# ---------------------------------------------------------------------------
# GET /admin/performance/metrics
# ---------------------------------------------------------------------------

def test_performance_metrics_shape():
    result = get_performance_metrics(timeRange="24h", admin=_FAKE_ADMIN, db=_DB)
    for section in ("system", "database", "application", "historical", "suggestions"):
        assert section in result, f"Missing section: {section}"

    sys_keys = {"serverLoad", "memoryUsage", "diskUsage", "networkLatency",
                "activeConnections", "requestsPerSecond", "errorRate", "uptime"}
    assert sys_keys == set(result["system"].keys())

    db_keys = {"queryTime", "activeQueries", "slowQueries", "connectionPool", "cacheHitRate"}
    assert db_keys == set(result["database"].keys())

    app_keys = {"responseTime", "throughput", "errorCount", "successRate", "endpoints"}
    assert app_keys == set(result["application"].keys())

    hist_keys = {"timestamps", "serverLoad", "responseTime", "errorRate"}
    assert hist_keys == set(result["historical"].keys())
    assert len(result["historical"]["timestamps"]) == 12

    assert isinstance(result["suggestions"], list)
    for sug in result["suggestions"]:
        for key in ("id", "title", "description", "impact", "effort", "category", "estimatedImprovement"):
            assert key in sug, f"Suggestion missing field: {key}"


def test_performance_metrics_bad_time_range_defaults_24h():
    """An unknown timeRange falls back to 24h gracefully."""
    result = get_performance_metrics(timeRange="999y", admin=_FAKE_ADMIN, db=_DB)
    assert "system" in result
    assert len(result["historical"]["timestamps"]) == 12
