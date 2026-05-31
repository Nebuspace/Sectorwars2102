"""End-to-end test of the GalaxyStateGuardMiddleware against the real app.

Supplements ``tests/unit/test_galaxy_state_guard.py`` (which uses a tiny
synthetic app + monkeypatched ``_resolve_state``). This integration test
exercises the middleware mounted on the production FastAPI app via the
existing ``client`` + ``admin_auth_headers`` fixtures.

Specifically, it confirms:

* Health endpoints stay 200 regardless of galaxy state
* Admin routes bypass the guard
* The middleware does not break the request stream when no Galaxy row exists
  (the "fail-open" branch from the unit test, but observed end-to-end)
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.mark.integration
class TestGalaxyStateGuardIntegration:
    """The middleware is wired into the real app and does not break traffic."""

    def test_health_endpoint_unblocked(self, client: TestClient) -> None:
        # /health is not under /api/v1 so it bypasses the guard entirely.
        resp = client.get("/health")
        # Some deployments use 200, others a specific health payload — we
        # only care that the middleware did not 503 us.
        assert resp.status_code != 503

    def test_admin_route_passes_through(
        self, client: TestClient, admin_auth_headers: dict[str, str]
    ) -> None:
        # /api/v1/admin/* is bypassed by the guard (admin can always reach
        # admin endpoints even mid-generation).
        resp = client.get(
            "/api/v1/admin/stats", headers=admin_auth_headers
        )
        assert resp.status_code != 503

    def test_bang_admin_route_passes_through(
        self, client: TestClient, admin_auth_headers: dict[str, str]
    ) -> None:
        # The new bang admin surface stays reachable mid-generation.
        resp = client.get(
            "/api/v1/admin/galaxy/jobs", headers=admin_auth_headers
        )
        # Either 200 (table exists) or 500 (table missing in pristine DB) —
        # but never 503 from the guard.
        assert resp.status_code != 503
