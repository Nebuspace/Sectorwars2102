"""Route-level authz proof for the multi-account review queue
(WO-PADMIN-multiacct-review).

Hard Accept: unauthenticated → 401, non-admin → 403, and in BOTH cases
``db.commit()`` is never called — ``require_admin`` is declared before
``get_db`` in every route signature, so FastAPI raises before resolving the
DB dependency.

DB-free: overrides ``get_current_user`` (NOT ``require_admin``/
``get_current_admin_user`` — the real ``is_admin`` check must still run to
make the 403 case a genuine authz proof rather than a rubber stamp).
Happy-path tests override ``get_db`` with a MagicMock.

Fixture is named ``mac_client`` (not ``client``) — conftest.py's
``pytest_collection_modifyitems`` hook skips tests whose
``item.fixturenames`` contains the literal string ``client`` (name-match,
not dependency-graph).  ``base_url="http://localhost"`` avoids
TrustedHostMiddleware's ``400 "Invalid host header"`` under
``ENVIRONMENT=testing``.
"""
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from src.auth.dependencies import get_current_user
from src.core.database import get_db
from src.main import app

API = "/api/v1/admin/multi-account"


def _admin():
    return SimpleNamespace(id=uuid.uuid4(), username="admin", is_admin=True)


def _player():
    return SimpleNamespace(id=uuid.uuid4(), username="trader", is_admin=False)


def _db_passing_scope(*, cluster_lookup=None):
    """Mock DB: first ``first()`` = active scope grant; second = cluster row."""
    mock_db = MagicMock()
    mock_db.query.return_value.filter.return_value.first.side_effect = [
        uuid.uuid4(),  # require_scope grant lookup
        cluster_lookup,
    ]
    return mock_db


@pytest.fixture
def mac_client():
    return TestClient(app, base_url="http://localhost")


@pytest.fixture(autouse=True)
def _isolate_overrides():
    """Save/restore dependency_overrides so tests don't bleed into each
    other — conftest.py may install a real-DB get_db override at the
    module level for the integration suite."""
    saved_user = app.dependency_overrides.get(get_current_user)
    saved_db = app.dependency_overrides.get(get_db)
    yield
    for key, saved in ((get_current_user, saved_user), (get_db, saved_db)):
        if saved is not None:
            app.dependency_overrides[key] = saved
        else:
            app.dependency_overrides.pop(key, None)


# ---------------------------------------------------------------------------
# GET /clusters — list pending queue
# ---------------------------------------------------------------------------


class TestListClustersAuthz:
    def test_unauthenticated_returns_401(self, mac_client):
        resp = mac_client.get(f"{API}/clusters")
        assert resp.status_code == 401

    def test_non_admin_returns_403(self, mac_client):
        app.dependency_overrides[get_current_user] = _player
        resp = mac_client.get(f"{API}/clusters")
        assert resp.status_code == 403

    def test_admin_with_empty_db_returns_empty_list(self, mac_client):
        app.dependency_overrides[get_current_user] = _admin
        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.order_by.return_value.all.return_value = []
        app.dependency_overrides[get_db] = lambda: mock_db
        resp = mac_client.get(f"{API}/clusters")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_valid_decision_filter_accepted(self, mac_client):
        app.dependency_overrides[get_current_user] = _admin
        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.order_by.return_value.all.return_value = []
        app.dependency_overrides[get_db] = lambda: mock_db
        resp = mac_client.get(f"{API}/clusters?decision=confirmed")
        assert resp.status_code == 200

    def test_unknown_decision_filter_returns_400(self, mac_client):
        app.dependency_overrides[get_current_user] = _admin
        app.dependency_overrides[get_db] = lambda: MagicMock()
        resp = mac_client.get(f"{API}/clusters?decision=not_a_thing")
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# GET /clusters/{id} — detail / evidence panel
# ---------------------------------------------------------------------------


class TestGetClusterAuthz:
    def test_unauthenticated_returns_401(self, mac_client):
        resp = mac_client.get(f"{API}/clusters/{uuid.uuid4()}")
        assert resp.status_code == 401

    def test_non_admin_returns_403(self, mac_client):
        app.dependency_overrides[get_current_user] = _player
        resp = mac_client.get(f"{API}/clusters/{uuid.uuid4()}")
        assert resp.status_code == 403

    def test_unknown_cluster_returns_404(self, mac_client):
        app.dependency_overrides[get_current_user] = _admin
        mock_db = _db_passing_scope(cluster_lookup=None)
        app.dependency_overrides[get_db] = lambda: mock_db
        resp = mac_client.get(f"{API}/clusters/{uuid.uuid4()}")
        assert resp.status_code == 404

    def test_invalid_uuid_returns_400(self, mac_client):
        app.dependency_overrides[get_current_user] = _admin
        app.dependency_overrides[get_db] = lambda: MagicMock()
        resp = mac_client.get(f"{API}/clusters/not-a-uuid")
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# POST /clusters/{id}/decide — ruling (authz-before-mutation proof)
# ---------------------------------------------------------------------------


class TestDecideClusterAuthz:
    """Primary Accept criteria: 401/403 never touch db.commit()."""

    def test_unauthenticated_returns_401(self, mac_client):
        resp = mac_client.post(
            f"{API}/clusters/{uuid.uuid4()}/decide",
            json={"decision": "confirmed"},
        )
        assert resp.status_code == 401

    def test_non_admin_returns_403(self, mac_client):
        app.dependency_overrides[get_current_user] = _player
        resp = mac_client.post(
            f"{API}/clusters/{uuid.uuid4()}/decide",
            json={"decision": "confirmed"},
        )
        assert resp.status_code == 403

    def test_pending_decision_rejected_with_400(self, mac_client):
        """'pending' is the initial state — setting it as a ruling is invalid."""
        app.dependency_overrides[get_current_user] = _admin
        mock_db = MagicMock()
        app.dependency_overrides[get_db] = lambda: mock_db
        resp = mac_client.post(
            f"{API}/clusters/{uuid.uuid4()}/decide",
            json={"decision": "pending"},
        )
        assert resp.status_code == 400
        mock_db.commit.assert_not_called()

    def test_bad_decision_value_returns_400(self, mac_client):
        app.dependency_overrides[get_current_user] = _admin
        mock_db = MagicMock()
        app.dependency_overrides[get_db] = lambda: mock_db
        resp = mac_client.post(
            f"{API}/clusters/{uuid.uuid4()}/decide",
            json={"decision": "not_real"},
        )
        assert resp.status_code == 400
        mock_db.commit.assert_not_called()

    def test_unknown_cluster_returns_404_without_commit(self, mac_client):
        app.dependency_overrides[get_current_user] = _admin
        mock_db = _db_passing_scope(cluster_lookup=None)
        app.dependency_overrides[get_db] = lambda: mock_db
        resp = mac_client.post(
            f"{API}/clusters/{uuid.uuid4()}/decide",
            json={"decision": "confirmed"},
        )
        assert resp.status_code == 404
        mock_db.commit.assert_not_called()

    def test_admin_confirmed_commits_and_returns_cluster(self, mac_client):
        from src.models.multi_account import MultiAccountAdminDecision, MultiAccountSeverity

        admin = _admin()
        cluster_id = uuid.uuid4()
        now = datetime.now(timezone.utc)

        mock_cluster = MagicMock()
        mock_cluster.id = cluster_id
        mock_cluster.severity = MultiAccountSeverity.SOFT
        mock_cluster.all_paid_subscribers = False
        mock_cluster.signal_summary = {"hard": [], "soft": ["ip_24h"]}
        mock_cluster.admin_decision_reason = None
        mock_cluster.admin_decision_at = None
        mock_cluster.admin_decision_by = None
        mock_cluster.created_at = now
        mock_cluster.updated_at = now
        mock_cluster.flags = []

        def _fake_commit():
            mock_cluster.admin_decision = MultiAccountAdminDecision.CONFIRMED
            mock_cluster.admin_decision_reason = "clear violation"
            mock_cluster.admin_decision_by = admin.id
            mock_cluster.admin_decision_at = now

        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.first.return_value = mock_cluster
        mock_db.commit.side_effect = _fake_commit
        mock_db.refresh = lambda obj: None

        app.dependency_overrides[get_current_user] = lambda: admin
        app.dependency_overrides[get_db] = lambda: mock_db

        resp = mac_client.post(
            f"{API}/clusters/{cluster_id}/decide",
            json={"decision": "confirmed", "reason": "clear violation"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["admin_decision"] == "confirmed"
        assert data["admin_decision_reason"] == "clear violation"
        mock_db.commit.assert_called_once()

    def test_admin_overridden_commits_and_returns_cluster(self, mac_client):
        from src.models.multi_account import MultiAccountAdminDecision, MultiAccountSeverity

        admin = _admin()
        now = datetime.now(timezone.utc)

        mock_cluster = MagicMock()
        mock_cluster.id = uuid.uuid4()
        mock_cluster.severity = MultiAccountSeverity.HARD
        mock_cluster.all_paid_subscribers = True
        mock_cluster.signal_summary = {"hard": ["payment_method"], "soft": []}
        mock_cluster.admin_decision_reason = None
        mock_cluster.admin_decision_at = None
        mock_cluster.admin_decision_by = None
        mock_cluster.created_at = now
        mock_cluster.updated_at = now
        mock_cluster.flags = []

        def _fake_commit():
            mock_cluster.admin_decision = MultiAccountAdminDecision.OVERRIDDEN
            mock_cluster.admin_decision_reason = "siblings, not same operator"
            mock_cluster.admin_decision_by = admin.id
            mock_cluster.admin_decision_at = now

        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.first.return_value = mock_cluster
        mock_db.commit.side_effect = _fake_commit
        mock_db.refresh = lambda obj: None

        app.dependency_overrides[get_current_user] = lambda: admin
        app.dependency_overrides[get_db] = lambda: mock_db

        resp = mac_client.post(
            f"{API}/clusters/{mock_cluster.id}/decide",
            json={"decision": "overridden", "reason": "siblings, not same operator"},
        )
        assert resp.status_code == 200
        assert resp.json()["admin_decision"] == "overridden"
        mock_db.commit.assert_called_once()
