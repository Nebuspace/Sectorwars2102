"""LEG-4047 — route_optimizer optimize/history routes return structured 500s."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from src.api.routes import route_optimizer as ro_mod
from src.api.routes.route_optimizer import (
    RouteOptimizeRequest,
    get_route_history,
    optimize_route,
)


@pytest.mark.asyncio
async def test_optimize_route_boom_returns_structured_500():
    secret = "secret-optimize-route-should-not-leak"
    request = RouteOptimizeRequest(
        start_sector_id="1",
        objective="shortest",
        end_sector_id="2",
    )
    player = SimpleNamespace(id="player-1")
    db = AsyncMock()

    with patch.object(ro_mod, "RouteOptimizer") as mock_cls:
        mock_opt = mock_cls.return_value
        mock_opt.find_shortest_path = AsyncMock(side_effect=RuntimeError(secret))
        with pytest.raises(HTTPException) as excinfo:
            await optimize_route(request, player, db)

    exc = excinfo.value
    assert exc.status_code == 500
    assert exc.detail == {
        "error_code": "ERR_ROUTES_OPTIMIZE_FAILED",
        "detail": "Failed to optimize route",
    }
    assert secret not in str(exc.detail)


@pytest.mark.asyncio
async def test_get_route_history_boom_returns_structured_500():
    secret = "secret-route-history-should-not-leak"
    player = SimpleNamespace(id="player-1")
    db = AsyncMock()
    db.execute = AsyncMock(side_effect=RuntimeError(secret))

    with pytest.raises(HTTPException) as excinfo:
        await get_route_history(10, player, db)

    exc = excinfo.value
    assert exc.status_code == 500
    assert exc.detail == {
        "error_code": "ERR_ROUTES_HISTORY_FETCH_FAILED",
        "detail": "Failed to retrieve route history",
    }
    assert secret not in str(exc.detail)


def test_route_optimizer_http500_is_structured():
    src = Path(ro_mod.__file__).read_text(encoding="utf-8")
    for code in ("ERR_ROUTES_OPTIMIZE_FAILED", "ERR_ROUTES_HISTORY_FETCH_FAILED"):
        assert code in src
    assert "route_internal_error" in src
    assert 'detail="Failed to optimize route"' not in src
    assert 'detail="Failed to retrieve route history"' not in src
