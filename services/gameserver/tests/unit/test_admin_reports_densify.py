"""LEG-3836 — admin_reports unexpected failures return structured 500s."""

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


def test_generate_report_unexpected_returns_structured_500():
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
    assert exc.detail == {
        "error_code": "ERR_ADMIN_REPORTS_GENERATE_FAILED",
        "detail": "Failed to generate report",
    }
    assert "secret-reports-query-should-not-leak" not in str(exc.detail)


def test_export_data_unexpected_returns_structured_500():
    with pytest.raises(HTTPException) as excinfo:
        export_data(
            dataset="players",
            format="json",
            admin=SimpleNamespace(),
            db=_BoomDB(),
        )

    exc = excinfo.value
    assert exc.status_code == 500
    assert exc.detail == {
        "error_code": "ERR_ADMIN_REPORTS_EXPORT_FAILED",
        "detail": "Failed to export analytics data",
    }
    assert "secret-reports-query-should-not-leak" not in str(exc.detail)


def test_get_performance_metrics_unexpected_returns_structured_500():
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
    assert exc.detail == {
        "error_code": "ERR_ADMIN_REPORTS_PERFORMANCE_FAILED",
        "detail": "Failed to fetch performance metrics",
    }
    assert "secret-performance-should-not-leak" not in str(exc.detail)


def test_admin_reports_http500_catches_are_structured():
    """LEG-3836 — static pin: report admin 500 catch paths emit error_code + detail."""
    src = Path(ar_mod.__file__).read_text(encoding="utf-8")
    for code in (
        "ERR_ADMIN_REPORTS_GENERATE_FAILED",
        "ERR_ADMIN_REPORTS_EXPORT_FAILED",
        "ERR_ADMIN_REPORTS_PERFORMANCE_FAILED",
    ):
        assert code in src
    assert 'detail="Failed to generate report"' not in src
