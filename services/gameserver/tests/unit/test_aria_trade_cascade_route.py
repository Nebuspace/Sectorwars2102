"""WO-PULL-ARIA-CASCADE-ENTRYPOINT — POST /ai/trade-cascade wiring.

Pins the API entry point onto plan_trade_cascade. Handler called directly
(FastAPI DI bypassed), mirroring test_aria_data_index route convention.
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.api.routes import enhanced_ai


@pytest.mark.asyncio
async def test_plan_trade_cascade_route_returns_service_plan():
    request = enhanced_ai.TradeCascadeRequest(
        start_sector_id="sector-a",
        target_profit=1000.0,
        max_jumps=5,
    )
    db = AsyncMock()
    plan = {
        "cascade_id": "cascade_test",
        "player_id": "player-1",
        "total_profit": 1500.0,
        "total_jumps": 2,
        "profit_per_jump": 750.0,
        "confidence": 0.8,
        "steps": [],
    }
    svc = MagicMock()
    svc.plan_trade_cascade = AsyncMock(return_value=plan)

    with patch(
        "src.services.aria_personal_intelligence_service.get_aria_intelligence_service",
        return_value=svc,
    ):
        result = await enhanced_ai.plan_trade_cascade(
            request=request, player_id="player-1", db=db,
        )

    assert result["cascade_id"] == "cascade_test"
    svc.plan_trade_cascade.assert_awaited_once_with(
        "player-1", "sector-a", 1000.0, 5, db,
    )
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_plan_trade_cascade_route_maps_none_to_exploration_refusal():
    request = enhanced_ai.TradeCascadeRequest(
        start_sector_id="sector-a",
        target_profit=1000.0,
        max_jumps=3,
    )
    db = AsyncMock()
    svc = MagicMock()
    svc.plan_trade_cascade = AsyncMock(return_value=None)

    with patch(
        "src.services.aria_personal_intelligence_service.get_aria_intelligence_service",
        return_value=svc,
    ):
        result = await enhanced_ai.plan_trade_cascade(
            request=request, player_id="player-1", db=db,
        )

    assert result["error"] == "no_exploration_map"
    assert "Explore more" in result["message"]


@pytest.mark.asyncio
async def test_plan_trade_cascade_route_passes_through_service_error_payload():
    request = enhanced_ai.TradeCascadeRequest(
        start_sector_id="sector-a",
        target_profit=500.0,
        max_jumps=4,
    )
    db = AsyncMock()
    svc = MagicMock()
    svc.plan_trade_cascade = AsyncMock(return_value={
        "error": "insufficient_exploration",
        "message": "Explore more sectors to plan trade routes",
        "explored_sectors": 1,
    })

    with patch(
        "src.services.aria_personal_intelligence_service.get_aria_intelligence_service",
        return_value=svc,
    ):
        result = await enhanced_ai.plan_trade_cascade(
            request=request, player_id="player-1", db=db,
        )

    assert result["error"] == "insufficient_exploration"
    assert result["explored_sectors"] == 1
