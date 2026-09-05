"""LEG-46 — exploration suggestions + combat advice service wiring."""
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from src.api.routes import enhanced_ai
from src.services.aria_personal_intelligence_service import ARIAPersonalIntelligenceService


@pytest.mark.asyncio
async def test_exploration_suggestions_route_delegates_to_service():
    db = AsyncMock()
    player = MagicMock()
    player.id = uuid4()
    payload = {
        "suggestions": [
            {
                "kind": "repeat_visit",
                "sector_id": "sector-1",
                "sector_number": 42,
                "sector_name": "Auriga",
                "visit_count": 5,
                "trade_opportunity_score": 0.8,
                "summary": "Sector 42 (Auriga) — visited 5 times with strong trade signals.",
            },
        ],
        "empty_message": None,
    }
    svc = MagicMock()
    svc.get_exploration_suggestions = AsyncMock(return_value=payload)

    with patch(
        "src.services.aria_personal_intelligence_service.get_aria_intelligence_service",
        return_value=svc,
    ):
        result = await enhanced_ai.get_exploration_suggestions(
            current_player=player, db=db,
        )

    assert len(result["suggestions"]) == 1
    assert result["suggestions"][0]["kind"] == "repeat_visit"
    svc.get_exploration_suggestions.assert_awaited_once_with(str(player.id), db)
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_combat_advice_route_passes_ship_types():
    db = AsyncMock()
    player = MagicMock()
    player.id = uuid4()
    ship = MagicMock()
    ship.type.value = "DEFENDER"
    player.current_ship = ship
    payload = {
        "has_history": True,
        "opponent_ship_type": "CARGO_HAULER",
        "summary": "You've fought Cargo Hauler 2 times: 1 win, 1 loss.",
        "weapon_suggestion": "Ship-type matchup favours you",
        "encounters": 2,
        "wins": 1,
        "losses": 1,
    }
    svc = MagicMock()
    svc.get_combat_advice = AsyncMock(return_value=payload)

    with patch(
        "src.services.aria_personal_intelligence_service.get_aria_intelligence_service",
        return_value=svc,
    ):
        result = await enhanced_ai.get_combat_advice(
            opponent_ship_type="CARGO_HAULER",
            current_player=player,
            db=db,
        )

    assert result["has_history"] is True
    svc.get_combat_advice.assert_awaited_once_with(
        str(player.id), "CARGO_HAULER", "DEFENDER", db,
    )


@pytest.mark.asyncio
async def test_get_exploration_suggestions_empty_map():
    service = ARIAPersonalIntelligenceService()
    db = AsyncMock()
    service._get_explored_sectors = AsyncMock(return_value=[])

    result = await service.get_exploration_suggestions("player-1", db)

    assert result["suggestions"] == []
    assert "no exploration data" in result["empty_message"].lower()


@pytest.mark.asyncio
async def test_get_combat_advice_no_history_includes_matchup_hint():
    service = ARIAPersonalIntelligenceService()
    db = AsyncMock()
    service.recall_memories = AsyncMock(return_value=[])

    result = await service.get_combat_advice(
        "player-1", "CARGO_HAULER", "DEFENDER", db,
    )

    assert result["has_history"] is False
    assert result["encounters"] == 0
    assert result["weapon_suggestion"] is not None
    assert "matchup favours you" in result["weapon_suggestion"].lower()
