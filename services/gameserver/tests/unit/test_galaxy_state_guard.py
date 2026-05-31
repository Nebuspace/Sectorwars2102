"""Unit tests for GalaxyStateGuardMiddleware.

The middleware is tested against a freshly-built minimal FastAPI app so
the suite doesn't depend on the full gameserver router. We bypass the DB
lookup by monkey-patching the middleware's ``_resolve_state`` to return
a controlled value.
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.middleware.galaxy_state_guard import GalaxyStateGuardMiddleware


def _build_app(monkeypatch: pytest.MonkeyPatch, fake_state: str | None):
    """Build a tiny FastAPI app with the middleware and 3 probe routes."""
    app = FastAPI()
    app.add_middleware(GalaxyStateGuardMiddleware)

    @app.get("/api/v1/players/me")
    async def player_route() -> dict[str, str]:
        return {"ok": "player"}

    @app.get("/api/v1/admin/galaxy/status")
    async def admin_route() -> dict[str, str]:
        return {"ok": "admin"}

    @app.get("/health")
    async def health_route() -> dict[str, str]:
        return {"ok": "health"}

    # Monkeypatch the per-instance lookup on the live middleware. Find
    # the middleware instance via the dispatch coroutine.
    async def fake_resolve(self) -> str | None:  # noqa: D401
        return fake_state

    monkeypatch.setattr(
        GalaxyStateGuardMiddleware,
        "_resolve_state",
        fake_resolve,
    )
    return app


def test_player_route_returns_503_when_generating(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = _build_app(monkeypatch, fake_state="GENERATING")
    with TestClient(app) as client:
        resp = client.get("/api/v1/players/me")
        assert resp.status_code == 503
        body = resp.json()
        assert body["detail"] == "Galaxy is initializing"
        assert body["state"] == "GENERATING"


def test_player_route_passes_through_when_ready(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = _build_app(monkeypatch, fake_state="READY")
    with TestClient(app) as client:
        resp = client.get("/api/v1/players/me")
        assert resp.status_code == 200
        assert resp.json() == {"ok": "player"}


def test_admin_route_bypasses_guard_when_generating(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = _build_app(monkeypatch, fake_state="GENERATING")
    with TestClient(app) as client:
        resp = client.get("/api/v1/admin/galaxy/status")
        assert resp.status_code == 200
        assert resp.json() == {"ok": "admin"}


def test_admin_route_bypasses_guard_when_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = _build_app(monkeypatch, fake_state="FAILED")
    with TestClient(app) as client:
        resp = client.get("/api/v1/admin/galaxy/status")
        assert resp.status_code == 200


def test_health_endpoint_always_passes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = _build_app(monkeypatch, fake_state="GENERATING")
    with TestClient(app) as client:
        resp = client.get("/health")
        assert resp.status_code == 200


def test_fail_open_when_lookup_returns_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If the DB lookup blows up, the middleware must not break traffic."""
    app = _build_app(monkeypatch, fake_state=None)
    with TestClient(app) as client:
        resp = client.get("/api/v1/players/me")
        assert resp.status_code == 200


def test_failed_state_blocks_player_traffic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = _build_app(monkeypatch, fake_state="FAILED")
    with TestClient(app) as client:
        resp = client.get("/api/v1/players/me")
        assert resp.status_code == 503
        assert resp.json()["state"] == "FAILED"
