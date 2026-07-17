"""Route-level authz proof for Tier-2 admin contract-dispute arbitration
(WO-CONTRACT-6-DISPUTE-T2-ADMIN).

HARD Accept (the NPC-pool unbounded-mint barrier this WO exists to prove):
unauthenticated -> 401, authenticated non-admin -> 403, and in BOTH cases
`resolve_dispute` (the credit-moving service call) is asserted NEVER called.
`contract_service.resolve_dispute` is authz-FREE by design -- the route
(`admin: User = Depends(require_admin)`, declared before `db`) is the ONLY
gate, so these tests prove that gate actually blocks the call, not just that
it exists.

DB-free: overrides `get_current_user` (NOT `require_admin`/
`get_current_admin_user` -- leaving the real `is_admin` check running is
what makes the 403 case a genuine authz proof rather than a rubber stamp)
and, for the happy path, `get_db`. Because `admin` is resolved before `db`
in every route signature here, the unauthenticated/non-admin cases never
reach `get_db` at all -- no real Postgres connection is attempted.
"""
import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from src.auth.dependencies import get_current_user
from src.core.database import get_db
from src.main import app

API = "/api/v1/admin/contracts"


def _admin_user():
    return SimpleNamespace(id=uuid.uuid4(), username="admin", is_admin=True)


def _player_user():
    return SimpleNamespace(id=uuid.uuid4(), username="trader", is_admin=False)


@pytest.fixture
def dispute_client():
    # WO-PINFRA-CI-PYTEST-LANE's DB-free CI lane skips any test whose
    # `item.fixturenames` contains the literal name "client" (conftest.py's
    # own real-DB fixture, name-matched not dependency-graph-matched) -- this
    # fixture is genuinely DB-free (plain `TestClient(app)`, no conftest
    # `db`/`client` anywhere in its closure) but is deliberately NOT named
    # `client` so that heuristic doesn't false-positive-skip it.
    #
    # base_url="http://localhost": conftest.py forces ENVIRONMENT=testing,
    # which flips main.py's TrustedHostMiddleware to its non-dev allowlist
    # (["localhost", "*.app.github.dev", "*.repl.co"]). TestClient's own
    # default base_url ("http://testserver") sends a Host header that
    # matches none of those and gets a blanket 400 "Invalid host header"
    # before any route/auth code ever runs -- not specific to this route.
    return TestClient(app, base_url="http://localhost")


@pytest.fixture(autouse=True)
def _isolate_overrides():
    """Save/restore whatever was in dependency_overrides before this test --
    conftest.py installs a module-level get_db override for the DB-backed
    suites; blind-popping it would leak into later tests in the same run."""
    saved_user = app.dependency_overrides.get(get_current_user)
    saved_db = app.dependency_overrides.get(get_db)
    yield
    for key, saved in ((get_current_user, saved_user), (get_db, saved_db)):
        if saved is not None:
            app.dependency_overrides[key] = saved
        else:
            app.dependency_overrides.pop(key, None)


class TestResolveDisputeAuthz:
    """POST /admin/contracts/{id}/resolve-dispute -- authz-before-mutation."""

    def test_unauthenticated_returns_401_and_never_calls_resolve_dispute(self, dispute_client):
        with patch("src.api.routes.admin_contract_disputes.resolve_dispute") as mock_resolve:
            resp = dispute_client.post(
                f"{API}/{uuid.uuid4()}/resolve-dispute",
                json={"outcome": "full_payout"},
            )
        assert resp.status_code == 401
        mock_resolve.assert_not_called()

    def test_non_admin_returns_403_and_never_calls_resolve_dispute(self, dispute_client):
        app.dependency_overrides[get_current_user] = _player_user
        with patch("src.api.routes.admin_contract_disputes.resolve_dispute") as mock_resolve:
            resp = dispute_client.post(
                f"{API}/{uuid.uuid4()}/resolve-dispute",
                json={"outcome": "full_payout"},
            )
        assert resp.status_code == 403
        mock_resolve.assert_not_called()

    def test_admin_override_calls_resolve_dispute_with_admin_id_and_outcome(self, dispute_client):
        admin = _admin_user()
        contract_id = uuid.uuid4()
        app.dependency_overrides[get_current_user] = lambda: admin
        app.dependency_overrides[get_db] = lambda: MagicMock()
        with patch("src.api.routes.admin_contract_disputes.resolve_dispute") as mock_resolve:
            mock_resolve.return_value = {
                "id": str(contract_id),
                "status": "completed",
                "dispute_resolution": "full_payout",
                "amount_to_acceptor": 1000,
            }
            resp = dispute_client.post(
                f"{API}/{contract_id}/resolve-dispute",
                json={"outcome": "full_payout", "notes": "evidence checks out"},
            )
        assert resp.status_code == 200
        assert resp.json()["dispute_resolution"] == "full_payout"
        mock_resolve.assert_called_once()
        call_args, call_kwargs = mock_resolve.call_args
        assert call_args[1] == contract_id
        assert call_args[2] == admin.id
        assert call_kwargs["outcome"].value == "full_payout"
        assert call_kwargs["notes"] == "evidence checks out"

    def test_unknown_outcome_rejected_before_service_call(self, dispute_client):
        app.dependency_overrides[get_current_user] = _admin_user
        app.dependency_overrides[get_db] = lambda: MagicMock()
        with patch("src.api.routes.admin_contract_disputes.resolve_dispute") as mock_resolve:
            resp = dispute_client.post(
                f"{API}/{uuid.uuid4()}/resolve-dispute",
                json={"outcome": "not_a_real_outcome"},
            )
        assert resp.status_code == 400
        mock_resolve.assert_not_called()


class TestListDisputedContractsAuthz:
    """GET /admin/contracts/disputes -- same authz gate, read side."""

    def test_unauthenticated_returns_401(self, dispute_client):
        resp = dispute_client.get(f"{API}/disputes")
        assert resp.status_code == 401

    def test_non_admin_returns_403(self, dispute_client):
        app.dependency_overrides[get_current_user] = _player_user
        resp = dispute_client.get(f"{API}/disputes")
        assert resp.status_code == 403


class TestGetDisputedContractAuthz:
    """GET /admin/contracts/{id} -- evidence-panel detail, same gate."""

    def test_unauthenticated_returns_401(self, dispute_client):
        resp = dispute_client.get(f"{API}/{uuid.uuid4()}")
        assert resp.status_code == 401

    def test_non_admin_returns_403(self, dispute_client):
        app.dependency_overrides[get_current_user] = _player_user
        resp = dispute_client.get(f"{API}/{uuid.uuid4()}")
        assert resp.status_code == 403
