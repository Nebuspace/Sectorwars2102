"""WO-P7-ADMIN-RATE-LIMIT-TIERS — canon admin REST rate-limit tiers.

Canon: sw2102-docs/OPERATIONS/admin-ui.md § Admin REST rate limits
  Read 100/min · Write 30/min · Bulk 10/min · Reports 5/hr.

Proves:
  - classify_admin_tier rules
  - tier 429s (write / bulk / reports)
  - two admins behind one IP get independent budgets
  - write burst 429 while reads continue (separate tier buckets)
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from starlette.requests import Request

from src.api.middleware.security import (
    ADMIN_TIER_BULK,
    ADMIN_TIER_LIMITS,
    ADMIN_TIER_READ,
    ADMIN_TIER_REPORTS,
    ADMIN_TIER_WRITE,
    RateLimitingMiddleware,
    classify_admin_tier,
    peek_request_user_id,
)


# ---------------------------------------------------------------------------
# Classifier
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "method,path,expected",
    [
        ("GET", "/api/v1/admin/players", ADMIN_TIER_READ),
        ("HEAD", "/api/v1/admin/stats", ADMIN_TIER_READ),
        ("POST", "/api/v1/admin/players", ADMIN_TIER_WRITE),
        ("PATCH", "/api/v1/admin/players/1", ADMIN_TIER_WRITE),
        ("DELETE", "/api/v1/admin/players/1", ADMIN_TIER_WRITE),
        ("PUT", "/api/v1/admin/config", ADMIN_TIER_WRITE),
        ("POST", "/api/v1/admin/markets/bulk", ADMIN_TIER_BULK),
        ("POST", "/api/v1/admin/messages/batch", ADMIN_TIER_BULK),
        ("DELETE", "/api/v1/admin/assets/mass", ADMIN_TIER_BULK),
        ("GET", "/api/v1/admin/reports/economy", ADMIN_TIER_REPORTS),
        ("POST", "/api/v1/admin/export/players", ADMIN_TIER_REPORTS),
        ("GET", "/api/v1/admin/exports/csv", ADMIN_TIER_REPORTS),
        ("GET", "/api/v1/players", None),
        ("POST", "/api/v1/auth/login", None),
    ],
)
def test_classify_admin_tier(method, path, expected):
    assert classify_admin_tier(method, path) == expected


def test_canon_tier_limits():
    assert ADMIN_TIER_LIMITS[ADMIN_TIER_READ] == (100, 60)
    assert ADMIN_TIER_LIMITS[ADMIN_TIER_WRITE] == (30, 60)
    assert ADMIN_TIER_LIMITS[ADMIN_TIER_BULK] == (10, 60)
    assert ADMIN_TIER_LIMITS[ADMIN_TIER_REPORTS] == (5, 3600)


# ---------------------------------------------------------------------------
# Middleware helpers
# ---------------------------------------------------------------------------


def _make_request(
    method: str,
    path: str,
    *,
    user_id: str | None = None,
    authorization: str | None = None,
    client_host: str = "10.0.0.1",
) -> MagicMock:
    request = MagicMock(spec=Request)
    request.method = method
    request.url.path = path

    def _hdr_get(key, default=None):
        lk = key.lower()
        if lk == "authorization":
            return authorization if authorization is not None else default
        if lk == "user-agent":
            return "pytest-agent"
        return default

    headers = MagicMock()
    headers.get = _hdr_get
    request.headers = headers
    request.client = SimpleNamespace(host=client_host)
    request.state = SimpleNamespace()
    if user_id is not None:
        request.state.user_id = user_id
    return request


def _middleware() -> RateLimitingMiddleware:
    return RateLimitingMiddleware(app=MagicMock())


async def _passthrough(_request):
    resp = MagicMock()
    resp.headers = {}
    return resp


@pytest.mark.asyncio
async def test_write_tier_429_after_limit():
    mw = _middleware()
    limit, _ = ADMIN_TIER_LIMITS[ADMIN_TIER_WRITE]
    request = _make_request("POST", "/api/v1/admin/players", user_id="admin-a")

    for _ in range(limit):
        resp = await mw.dispatch(request, _passthrough)
        assert getattr(resp, "status_code", 200) != 429

    blocked = await mw.dispatch(request, _passthrough)
    assert blocked.status_code == 429
    assert blocked.headers["Retry-After"]
    body = blocked.body
    # JSONResponse stores body as bytes
    import json

    payload = json.loads(body)
    assert payload["tier"] == ADMIN_TIER_WRITE


@pytest.mark.asyncio
async def test_bulk_tier_429_on_eleventh():
    mw = _middleware()
    limit, _ = ADMIN_TIER_LIMITS[ADMIN_TIER_BULK]
    assert limit == 10
    request = _make_request(
        "POST", "/api/v1/admin/markets/bulk", user_id="admin-a"
    )

    for _ in range(limit):
        resp = await mw.dispatch(request, _passthrough)
        assert getattr(resp, "status_code", 200) != 429

    blocked = await mw.dispatch(request, _passthrough)
    assert blocked.status_code == 429
    import json

    assert json.loads(blocked.body)["tier"] == ADMIN_TIER_BULK


@pytest.mark.asyncio
async def test_reports_tier_429_on_sixth():
    mw = _middleware()
    limit, window = ADMIN_TIER_LIMITS[ADMIN_TIER_REPORTS]
    assert limit == 5
    assert window == 3600
    request = _make_request(
        "GET", "/api/v1/admin/reports/economy", user_id="admin-a"
    )

    for _ in range(limit):
        resp = await mw.dispatch(request, _passthrough)
        assert getattr(resp, "status_code", 200) != 429

    blocked = await mw.dispatch(request, _passthrough)
    assert blocked.status_code == 429
    import json

    assert json.loads(blocked.body)["tier"] == ADMIN_TIER_REPORTS


@pytest.mark.asyncio
async def test_write_burst_429_reads_still_allowed():
    """Write bucket exhaustion must not starve the independent read bucket."""
    mw = _middleware()
    write_limit, _ = ADMIN_TIER_LIMITS[ADMIN_TIER_WRITE]
    write_req = _make_request("POST", "/api/v1/admin/players", user_id="admin-a")
    read_req = _make_request("GET", "/api/v1/admin/players", user_id="admin-a")

    for _ in range(write_limit):
        await mw.dispatch(write_req, _passthrough)
    blocked = await mw.dispatch(write_req, _passthrough)
    assert blocked.status_code == 429

    read_ok = await mw.dispatch(read_req, _passthrough)
    assert getattr(read_ok, "status_code", 200) != 429
    assert read_ok.headers.get("X-RateLimit-Tier") == ADMIN_TIER_READ


@pytest.mark.asyncio
async def test_two_admins_independent_budgets_same_ip():
    mw = _middleware()
    write_limit, _ = ADMIN_TIER_LIMITS[ADMIN_TIER_WRITE]
    admin_a = _make_request(
        "POST",
        "/api/v1/admin/players",
        user_id="admin-a",
        client_host="10.0.0.99",
    )
    admin_b = _make_request(
        "POST",
        "/api/v1/admin/players",
        user_id="admin-b",
        client_host="10.0.0.99",
    )

    for _ in range(write_limit):
        resp = await mw.dispatch(admin_a, _passthrough)
        assert getattr(resp, "status_code", 200) != 429

    blocked_a = await mw.dispatch(admin_a, _passthrough)
    assert blocked_a.status_code == 429

    # Admin B still has a full budget behind the same IP.
    for _ in range(write_limit):
        resp = await mw.dispatch(admin_b, _passthrough)
        assert getattr(resp, "status_code", 200) != 429

    blocked_b = await mw.dispatch(admin_b, _passthrough)
    assert blocked_b.status_code == 429


def test_peek_prefers_request_state_user_id():
    request = _make_request("GET", "/api/v1/admin/x", user_id="from-state")
    assert peek_request_user_id(request) == "from-state"


def test_peek_jwt_sub_when_state_empty():
    import sys

    request = _make_request(
        "GET",
        "/api/v1/admin/x",
        authorization="Bearer fake.jwt.token",
    )
    fake_jwt = MagicMock()
    fake_jwt.decode_token = MagicMock(return_value={"sub": "jwt-admin-1"})
    with patch.dict(sys.modules, {"src.auth.jwt": fake_jwt}):
        assert peek_request_user_id(request) == "jwt-admin-1"


def test_peek_returns_none_on_bad_token():
    import sys

    request = _make_request(
        "GET",
        "/api/v1/admin/x",
        authorization="Bearer bad",
    )
    fake_jwt = MagicMock()
    fake_jwt.decode_token = MagicMock(side_effect=Exception("nope"))
    with patch.dict(sys.modules, {"src.auth.jwt": fake_jwt}):
        assert peek_request_user_id(request) is None
