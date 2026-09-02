"""LEG-3726 — multi-account review routes must not echo Exception text."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException

from src.api.routes import admin_multi_account as mod
from src.api.routes.admin_multi_account import (
    ClusterDecisionRequest,
    decide_cluster,
    list_clusters,
)
from src.models.multi_account import MultiAccountAdminDecision


class _BoomDB:
    def query(self, *args, **kwargs):
        raise RuntimeError("secret-multi-account-list-should-not-leak")


def test_list_clusters_boom_is_opaque_500():
    with pytest.raises(HTTPException) as excinfo:
        list_clusters(
            decision=None,
            admin=SimpleNamespace(),
            db=_BoomDB(),
        )

    exc = excinfo.value
    assert exc.status_code == 500
    assert exc.detail == "Failed to fetch multi-account clusters"
    assert "secret-multi-account-list-should-not-leak" not in str(exc.detail)


def test_decide_cluster_commit_boom_is_opaque_500():
    secret = "secret-multi-account-decide-should-not-leak"
    cluster_id = uuid4()
    cluster = SimpleNamespace(
        id=cluster_id,
        admin_decision=MultiAccountAdminDecision.PENDING,
        flags=[],
        signal_summary="sig",
        severity=None,
        all_paid_subscribers=False,
        admin_decision_reason=None,
        admin_decision_at=None,
        admin_decision_by=None,
        created_at=None,
        updated_at=None,
    )

    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = cluster
    db.commit.side_effect = RuntimeError(secret)

    body = ClusterDecisionRequest(decision="confirmed", reason="test")
    with patch.object(mod, "log_admin_action"):
        with pytest.raises(HTTPException) as excinfo:
            decide_cluster(
                cluster_id=str(cluster_id),
                body=body,
                admin=SimpleNamespace(id=uuid4()),
                db=db,
            )

    exc = excinfo.value
    assert exc.status_code == 500
    assert exc.detail == "Failed to record multi-account decision"
    assert secret not in str(exc.detail)


def test_admin_multi_account_http500_is_opaque():
    """LEG-3726 — static pin: multi-account route 500 details stay opaque."""
    src = Path(mod.__file__).read_text(encoding="utf-8")
    assert 'detail="Failed to fetch multi-account clusters"' in src
    assert 'detail="Failed to fetch multi-account cluster"' in src
    assert 'detail="Failed to record multi-account decision"' in src
