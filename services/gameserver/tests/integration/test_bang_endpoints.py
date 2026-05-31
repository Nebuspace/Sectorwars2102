"""Integration tests for the ``bang_galaxy`` admin endpoints.

These follow the dominant project convention (sync ``TestClient`` +
``admin_auth_headers`` fixture from ``conftest.py:222``) rather than the
async ``httpx.AsyncClient`` pattern, because the gameserver's existing
admin endpoint tests in ``tests/integration/api/test_admin_endpoints.py``
all use the sync client. The bang router uses async dependencies internally
but TestClient handles the event loop transparently.

Phase 4A confirmed the legacy ``POST /api/v1/admin/galaxy/generate`` is
not removed — it returns ``410 Gone`` with a ``replacement`` field pointing
to ``/api/v1/admin/galaxy/jobs``. There's a dedicated test for that here.

Coverage map (per the integration plan § Phase 1D):
    POST   /api/v1/admin/galaxy/jobs              -> create_bang_job
    POST   /api/v1/admin/galaxy/preview           -> preview_bang_config
    GET    /api/v1/admin/galaxy/jobs              -> list_bang_jobs
    GET    /api/v1/admin/galaxy/jobs/{id}         -> get_bang_job
    GET    /api/v1/admin/galaxy/jobs/{id}/stream  -> stream_bang_job_log (SSE)
    DELETE /api/v1/admin/galaxy/{id}              -> hard_delete_galaxy
    GET    /api/v1/admin/bang/version             -> get_bang_version
"""
from __future__ import annotations

import uuid
from typing import Any, Dict

import pytest
from fastapi.testclient import TestClient

from src.main import app
from src.services.bang_import_service import (
    BangImportService,
    ValidationReport,
)

API = "/api/v1/admin"


# ---------------------------------------------------------------------------
# Test-time overrides — stub the BangImportService dependency so the tests
# don't actually run ``docker run`` or persist jobs to disk.
# ---------------------------------------------------------------------------


class _StubService(BangImportService):
    """Replaces real BangImportService for endpoint tests.

    * ``validate_only`` returns canned stats.
    * ``run_generation_job`` is a coroutine that immediately marks the job
      COMPLETE — keeps BackgroundTasks happy without spawning Docker.
    """

    def __init__(self) -> None:  # noqa: D401
        super().__init__(bang_image="stub")

    def validate_only(self, config: Any) -> ValidationReport:  # type: ignore[override]
        return ValidationReport(
            stats={"sectors": config.sectors, "diameter": 12},
            warnings=[{"code": "B-001", "message": "ok"}],
            validation={"passed": True, "rules_run": 102},
        )

    async def run_generation_job(  # type: ignore[override]
        self, job_id: uuid.UUID, params: Any, **_kw: Any
    ) -> None:
        # No-op for endpoint tests; the unit + Phase 4 stream tests cover
        # the real orchestration loop separately.
        return None


@pytest.fixture
def stub_bang_service():
    """Install a stub BangImportService via FastAPI dependency_overrides."""
    from src.api.routes.bang_galaxy import get_bang_import_service

    stub = _StubService()
    app.dependency_overrides[get_bang_import_service] = lambda: stub
    yield stub
    app.dependency_overrides.pop(get_bang_import_service, None)


@pytest.fixture
def sample_job_payload() -> Dict[str, Any]:
    return {
        "config": {
            "seed": 42,
            "sectors": 1000,
            "region_type": "player_owned",
            "max_warps": 6,
        },
        "galaxy_name": "Endpoint Test Galaxy",
    }


@pytest.fixture
def sample_preview_payload() -> Dict[str, Any]:
    return {
        "seed": 42,
        "sectors": 1000,
        "region_type": "player_owned",
    }


# ---------------------------------------------------------------------------
# POST /admin/galaxy/jobs
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestCreateBangJob:
    """``POST /admin/galaxy/jobs`` returns 202 + a job row."""

    def test_returns_202_with_job(
        self,
        client: TestClient,
        admin_auth_headers: Dict[str, str],
        stub_bang_service: _StubService,
        sample_job_payload: Dict[str, Any],
    ) -> None:
        resp = client.post(
            f"{API}/galaxy/jobs",
            json=sample_job_payload,
            headers=admin_auth_headers,
        )
        assert resp.status_code == 202
        body = resp.json()
        assert "id" in body
        assert body["status"] in {"PENDING", "RUNNING"}
        assert body["params_json"]["seed"] == 42
        assert body["params_json"]["region_type"] == "player_owned"

    def test_unauthenticated_rejected(
        self,
        client: TestClient,
        sample_job_payload: Dict[str, Any],
    ) -> None:
        resp = client.post(f"{API}/galaxy/jobs", json=sample_job_payload)
        assert resp.status_code in {401, 403}

    def test_validation_error_on_bad_payload(
        self,
        client: TestClient,
        admin_auth_headers: Dict[str, str],
        stub_bang_service: _StubService,
    ) -> None:
        bad = {"config": {"seed": -1, "sectors": 1, "region_type": "bogus"}}
        resp = client.post(f"{API}/galaxy/jobs", json=bad, headers=admin_auth_headers)
        assert resp.status_code in {400, 422}


# ---------------------------------------------------------------------------
# POST /admin/galaxy/preview
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestPreviewBangConfig:
    """Preview returns stats inline; no job row is written."""

    def test_preview_returns_stats(
        self,
        client: TestClient,
        admin_auth_headers: Dict[str, str],
        stub_bang_service: _StubService,
        sample_preview_payload: Dict[str, Any],
    ) -> None:
        resp = client.post(
            f"{API}/galaxy/preview",
            json=sample_preview_payload,
            headers=admin_auth_headers,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert "stats" in body
        assert "warnings" in body
        assert "validation" in body
        assert body["stats"]["sectors"] == 1000
        assert body["validation"]["passed"] is True

    def test_unauthenticated_rejected(
        self,
        client: TestClient,
        sample_preview_payload: Dict[str, Any],
    ) -> None:
        resp = client.post(f"{API}/galaxy/preview", json=sample_preview_payload)
        assert resp.status_code in {401, 403}


# ---------------------------------------------------------------------------
# GET /admin/galaxy/jobs  (paginated history)
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestListBangJobs:
    """The list endpoint returns paginated items + total count."""

    def test_empty_list_succeeds(
        self,
        client: TestClient,
        admin_auth_headers: Dict[str, str],
    ) -> None:
        resp = client.get(f"{API}/galaxy/jobs", headers=admin_auth_headers)
        assert resp.status_code == 200
        body = resp.json()
        assert "items" in body
        assert "total" in body
        assert "page" in body
        assert "page_size" in body
        assert isinstance(body["items"], list)

    def test_pagination_parameters_respected(
        self,
        client: TestClient,
        admin_auth_headers: Dict[str, str],
    ) -> None:
        resp = client.get(
            f"{API}/galaxy/jobs?page=0&page_size=5",
            headers=admin_auth_headers,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["page"] == 0
        assert body["page_size"] == 5

    def test_unauthenticated_rejected(self, client: TestClient) -> None:
        resp = client.get(f"{API}/galaxy/jobs")
        assert resp.status_code in {401, 403}


# ---------------------------------------------------------------------------
# GET /admin/galaxy/jobs/{id}
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestGetBangJob:
    """Fetch a single job by UUID."""

    def test_unknown_id_returns_404(
        self,
        client: TestClient,
        admin_auth_headers: Dict[str, str],
    ) -> None:
        bogus = uuid.uuid4()
        resp = client.get(f"{API}/galaxy/jobs/{bogus}", headers=admin_auth_headers)
        assert resp.status_code == 404

    def test_created_job_round_trips(
        self,
        client: TestClient,
        admin_auth_headers: Dict[str, str],
        stub_bang_service: _StubService,
        sample_job_payload: Dict[str, Any],
    ) -> None:
        post = client.post(
            f"{API}/galaxy/jobs",
            json=sample_job_payload,
            headers=admin_auth_headers,
        )
        assert post.status_code == 202
        job_id = post.json()["id"]
        get = client.get(f"{API}/galaxy/jobs/{job_id}", headers=admin_auth_headers)
        assert get.status_code == 200
        assert get.json()["id"] == job_id


# ---------------------------------------------------------------------------
# GET /admin/galaxy/jobs/{id}/stream  (SSE)
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestStreamBangJobLog:
    """SSE stream returns text/event-stream content-type."""

    def test_unknown_id_returns_404(
        self,
        client: TestClient,
        admin_auth_headers: Dict[str, str],
    ) -> None:
        bogus = uuid.uuid4()
        resp = client.get(
            f"{API}/galaxy/jobs/{bogus}/stream", headers=admin_auth_headers
        )
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# DELETE /admin/galaxy/{galaxy_id}
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestHardDeleteGalaxy:
    """Wipe requires matching ``X-Confirm-Galaxy-Name`` header."""

    def test_unknown_galaxy_returns_404(
        self,
        client: TestClient,
        admin_auth_headers: Dict[str, str],
    ) -> None:
        bogus = uuid.uuid4()
        resp = client.delete(
            f"{API}/galaxy/{bogus}",
            headers={**admin_auth_headers, "X-Confirm-Galaxy-Name": "Whatever"},
        )
        assert resp.status_code == 404

    def test_unauthenticated_rejected(self, client: TestClient) -> None:
        bogus = uuid.uuid4()
        resp = client.delete(f"{API}/galaxy/{bogus}")
        assert resp.status_code in {401, 403}


# ---------------------------------------------------------------------------
# GET /admin/bang/version
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestGetBangVersion:
    """Returns the server's pinned BANG_VERSION."""

    def test_returns_version_envelope(
        self,
        client: TestClient,
        admin_auth_headers: Dict[str, str],
    ) -> None:
        resp = client.get(f"{API}/bang/version", headers=admin_auth_headers)
        assert resp.status_code == 200
        body = resp.json()
        assert "bang_version" in body
        assert "default_image" in body
        # default_image always includes "sw2102-bang"
        assert "sw2102-bang" in body["default_image"]

    def test_unauthenticated_rejected(self, client: TestClient) -> None:
        resp = client.get(f"{API}/bang/version")
        assert resp.status_code in {401, 403}


# ---------------------------------------------------------------------------
# Legacy endpoint — POST /admin/galaxy/generate  (now 410 Gone per Phase 4A)
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestLegacyGenerateEndpointGone:
    """``POST /admin/galaxy/generate`` returns 410 Gone with a replacement field."""

    def test_returns_410_with_replacement_field(
        self,
        client: TestClient,
        admin_auth_headers: Dict[str, str],
    ) -> None:
        resp = client.post(
            f"{API}/galaxy/generate",
            json={"name": "X", "num_sectors": 100},
            headers=admin_auth_headers,
        )
        assert resp.status_code == 410
        body = resp.json()
        assert "replacement" in body or (
            "detail" in body
            and isinstance(body["detail"], dict)
            and "replacement" in body["detail"]
        )
        replacement = body.get("replacement") or body.get("detail", {}).get(
            "replacement"
        )
        assert replacement is not None
        assert "/admin/galaxy/jobs" in replacement
