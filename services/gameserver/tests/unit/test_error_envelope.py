"""Unit tests for the consolidated error-envelope stack (WO-QTI-ERROR-STACK).

Builds a minimal FastAPI app wired through setup_error_handling and drives
it via TestClient — DB-free, no real gameserver router, no DB engine.
Confirms every handled 500-class + 422 path emits the documented envelope
{error, message, error_id, timestamp} + X-Error-ID header (sw2102-docs
FINDINGS.md, 2026-06-14 entry the player-client's apiRequest fallback was
built against), that 422 responses carry field-level locations but never
echo submitted payload values, and that 2xx paths are untouched.
"""
from __future__ import annotations

import pathlib

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy.exc import SQLAlchemyError

from src.utils.error_handling import setup_error_handling

ENVELOPE_FIELDS = {"error", "message", "error_id", "timestamp"}

# Distinctive enough that its presence anywhere in a response body
# unambiguously proves the raw request payload leaked through.
SENTINEL = "sw-qti-error-stack-sentinel-9f3c2a"


class _Body(BaseModel):
    name: str
    count: int


def _build_app() -> FastAPI:
    app = FastAPI()

    @app.get("/ok")
    async def ok_route() -> dict:
        return {"status": "fine"}

    @app.get("/boom/http")
    async def boom_http() -> None:
        raise HTTPException(status_code=404, detail="not found")

    @app.get("/boom/sqlalchemy")
    async def boom_sqlalchemy() -> None:
        raise SQLAlchemyError("simulated database failure")

    @app.get("/boom/generic")
    async def boom_generic() -> None:
        raise RuntimeError("simulated unexpected failure")

    @app.post("/validate")
    async def validate_route(body: _Body) -> dict:
        return {"status": "accepted"}

    setup_error_handling(app)
    # Hermetic: don't let the ambient DEBUG env var (read by settings
    # inside setup_error_handling) change which message branch fires.
    app.state.debug = False
    return app


@pytest.fixture()
def client() -> TestClient:
    # raise_server_exceptions=False: the bare-Exception route is handled by
    # setup_error_handling's Exception handler, but Starlette's
    # ServerErrorMiddleware always re-raises after sending the response
    # (so servers/loggers see it) -- we want the sent response, not the
    # re-raise, to inspect the envelope.
    return TestClient(_build_app(), raise_server_exceptions=False)


def _assert_envelope(body: dict, resp) -> None:
    assert ENVELOPE_FIELDS <= body.keys()
    assert body["error"] is True
    assert isinstance(body["message"], str) and body["message"]
    assert isinstance(body["error_id"], str) and body["error_id"]
    assert isinstance(body["timestamp"], str) and body["timestamp"]
    assert resp.headers.get("x-error-id") == body["error_id"]


def test_sqlalchemy_error_returns_enveloped_500_with_error_id(client: TestClient) -> None:
    resp = client.get("/boom/sqlalchemy")
    assert resp.status_code == 500
    body = resp.json()
    _assert_envelope(body, resp)
    # Sanitized, stable message -- never the raw exception text.
    assert body["message"] == "Database operation failed"
    assert "simulated database failure" not in resp.text


def test_generic_exception_returns_enveloped_500(client: TestClient) -> None:
    resp = client.get("/boom/generic")
    assert resp.status_code == 500
    body = resp.json()
    _assert_envelope(body, resp)
    assert body["message"] == "An unexpected error occurred"
    assert "simulated unexpected failure" not in resp.text


def test_http_exception_returns_enveloped_response(client: TestClient) -> None:
    resp = client.get("/boom/http")
    assert resp.status_code == 404
    body = resp.json()
    _assert_envelope(body, resp)
    assert body["message"] == "not found"


def test_validation_error_has_field_locations_no_payload_echo(client: TestClient) -> None:
    # `count` must be an int; send the sentinel as a non-coercible string so
    # we can assert it never round-trips into the response.
    resp = client.post("/validate", json={"name": "ok", "count": SENTINEL})
    assert resp.status_code == 422
    body = resp.json()
    _assert_envelope(body, resp)

    assert "validation_errors" in body
    assert len(body["validation_errors"]) >= 1
    for entry in body["validation_errors"]:
        assert set(entry.keys()) == {"field", "message", "type"}
        assert entry["field"]
        assert entry["type"]

    # The submitted sentinel must never appear anywhere in the response --
    # not in validation_errors, not in message, not via any other key.
    assert SENTINEL not in resp.text


def test_passing_route_is_untouched(client: TestClient) -> None:
    resp = client.get("/ok")
    assert resp.status_code == 200
    body = resp.json()
    assert body == {"status": "fine"}
    assert not ENVELOPE_FIELDS & body.keys()


def test_no_decorator_exception_handlers_remain_in_main() -> None:
    """Regression guard for the handler-stack consolidation: main.py must
    not re-introduce a competing @app.exception_handler stack now that
    setup_error_handling owns every registration."""
    main_py = pathlib.Path(__file__).resolve().parents[2] / "src" / "main.py"
    source = main_py.read_text()
    assert "@app.exception_handler" not in source
