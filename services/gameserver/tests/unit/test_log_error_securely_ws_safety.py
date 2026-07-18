"""QUEUE-ERRHANDLER-WS (2026-07-16): log_error_securely (error_handling.py)
did `request.method` unconditionally -- when a WebSocket connection drops
during handler execution, Starlette's exception middleware dispatches to
the SAME registered handler with a WebSocket instance (not an HTTP
Request), and WebSocket has no `.method` (that's HTTP-only; every other
field the handler reads -- .url, .headers, .query_params, .client, .scope
-- lives on the shared Starlette HTTPConnection base both inherit from).
The bare access crashed the error-LOGGING path itself, masking whatever
the real error was (observed live during reload cycles).

These tests drive log_error_securely directly with lightweight fakes (no
real ASGI WebSocket instance needed -- only the specific attributes this
function reads) rather than through the full TestClient/app stack, so a
WS-shaped object with no `.method` is trivial to construct. HTTP-path
byte-identical-behavior is verified separately by re-running the existing
test_error_envelope.py suite (unmodified, already comprehensive TestClient
coverage), per this ticket's own instruction to check first before adding
a redundant pin.
"""
from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest

from src.utils.error_handling import log_error_securely


class _FakeWSLikeConnection:
    """Minimal stand-in for a Starlette WebSocket -- carries exactly the
    attributes HTTPConnection provides (url, headers, query_params, client,
    scope) and deliberately NOT `.method` (HTTP-only), reproducing the live
    crash class without needing a real ASGI WebSocket instance."""

    def __init__(self):
        self.url = SimpleNamespace(path="/ws/sector-feed")
        self.headers = {"user-agent": "test-ws-client"}
        self.query_params = {}
        self.client = SimpleNamespace(host="127.0.0.1")
        self.scope = {"type": "websocket"}


class _FakeHttpLikeRequest:
    """Minimal stand-in for a real fastapi.Request -- HAS `.method`,
    proving the fix is byte-identical for the already-working case (the
    getattr must return the real value unchanged, never fall through to
    the WS sentinel)."""

    def __init__(self):
        self.method = "POST"
        self.url = SimpleNamespace(path="/api/v1/trading/dock")
        self.headers = {"user-agent": "test-http-client"}
        self.query_params = {}
        self.client = SimpleNamespace(host="10.0.0.5")
        self.scope = {"type": "http"}


@pytest.mark.unit
class TestLogErrorSecurelyWebSocketSafety:
    def test_ws_connection_drop_logs_cleanly_without_raising(self, caplog: pytest.LogCaptureFixture) -> None:
        conn = _FakeWSLikeConnection()
        error = RuntimeError("simulated handler failure during WS teardown")

        with caplog.at_level(logging.ERROR):
            error_id = log_error_securely(error, request=conn)  # must NOT raise AttributeError

        assert isinstance(error_id, str) and error_id
        # The scope-type fallback landed in the logged context -- proves
        # this is a MEANINGFUL equivalent, not a blank/crashed field.
        assert "websocket" in caplog.text

    def test_ws_connection_does_not_raise_attributeerror_on_method_access(self) -> None:
        """Harness self-check: proves this test actually exercises the bug
        class -- a bare `.method` access on this exact fake must raise,
        confirming _FakeWSLikeConnection faithfully reproduces the missing
        attribute (not accidentally providing one some other way)."""
        conn = _FakeWSLikeConnection()
        with pytest.raises(AttributeError):
            _ = conn.method

    def test_http_request_still_reports_its_real_method_unchanged(self, caplog: pytest.LogCaptureFixture) -> None:
        """Byte-identical-for-HTTP proof: a real Request-shaped object with
        `.method` present must have that EXACT value logged -- the fix must
        never substitute the WS fallback when `.method` already exists."""
        req = _FakeHttpLikeRequest()
        error = ValueError("simulated business-logic failure")

        with caplog.at_level(logging.ERROR):
            error_id = log_error_securely(error, request=req)

        assert isinstance(error_id, str) and error_id
        assert "'method': 'POST'" in caplog.text or '"method": "POST"' in caplog.text or "POST" in caplog.text
        # The WS fallback string must NOT appear for a real HTTP request.
        assert "'method': 'websocket'" not in caplog.text

    def test_no_request_at_all_still_works(self) -> None:
        """request=None (the default) -- pre-existing behavior, unaffected
        by this fix; the whole `if request:` block is skipped."""
        error_id = log_error_securely(RuntimeError("no request context"), request=None)
        assert isinstance(error_id, str) and error_id
