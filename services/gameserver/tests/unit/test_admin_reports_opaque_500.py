"""LEG-3703 — admin_reports generate/export/performance must not echo Exception text."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

from src.api.routes import admin_reports as ar_mod
from src.api.routes.admin_reports import (
    ReportTemplate,
    export_data,
    generate_report,
    get_performance_metrics,
)


class _BoomDB:
    def query(self, *args, **kwargs):
        raise RuntimeError("secret-reports-query-should-not-leak")

    def execute(self, *args, **kwargs):
        raise RuntimeError("secret-reports-query-should-not-leak")


class _BoomTimeRangeMap:
    def __contains__(self, item):
        return True

    def __getitem__(self, key):
        raise RuntimeError("secret-performance-should-not-leak")


def test_generate_report_unexpected_is_opaque_500():
    template = ReportTemplate(
        id="tpl-test",
        name="Test",
        description="test",
        metrics=["player_total_count"],
    )
    with pytest.raises(HTTPException) as excinfo:
        generate_report(
            template=template,
            admin=SimpleNamespace(),
            db=_BoomDB(),
        )

    exc = excinfo.value
    assert exc.status_code == 500
    assert exc.detail == "Failed to generate report"
    assert "secret-reports-query-should-not-leak" not in str(exc.detail)


def test_export_data_unexpected_is_opaque_500():
    with pytest.raises(HTTPException) as excinfo:
        export_data(
            dataset="players",
            format="json",
            admin=SimpleNamespace(),
            db=_BoomDB(),
        )

    exc = excinfo.value
    assert exc.status_code == 500
    assert exc.detail == "Failed to export analytics data"
    assert "secret-reports-query-should-not-leak" not in str(exc.detail)


def test_get_performance_metrics_unexpected_is_opaque_500():
    secret = "secret-performance-should-not-leak"
    with patch.object(ar_mod, "_TIME_RANGE_HOURS", _BoomTimeRangeMap()):
        with pytest.raises(HTTPException) as excinfo:
            get_performance_metrics(
                timeRange="24h",
                admin=SimpleNamespace(),
                db=MagicMock(),
            )

    exc = excinfo.value
    assert exc.status_code == 500
    assert exc.detail == "Failed to fetch performance metrics"
    assert secret not in str(exc.detail)


def test_admin_reports_http500_catches_have_no_detail_str_e():
    """LEG-3703 — static pin: HTTP 500 catch paths stay opaque."""
    src = Path(ar_mod.__file__).read_text(encoding="utf-8")
    for stable in (
        'detail="Failed to generate report"',
        'detail="Failed to export analytics data"',
        'detail="Failed to fetch performance metrics"',
    ):
        assert stable in src
    assert "Failed to generate report: {str(e)}" not in src
    assert "Failed to export analytics data: {str(e)}" not in src
    assert "Failed to fetch performance metrics: {str(e)}" not in src
