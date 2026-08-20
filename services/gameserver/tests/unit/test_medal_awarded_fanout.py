"""medal_awarded personal + team + sector room fan-out (LEG-783 / medal-service.md)."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from src.services.enhanced_websocket_service import EnhancedWebSocketService
from src.services.medal_service import (
    _dispatch_medal_awarded_event,
    _medal_broadcast_to_team_allowed,
    _medal_qualifies_for_sector_broadcast,
)


class TestMedalSectorQualification:
    def test_bronze_no_sector(self):
        assert _medal_qualifies_for_sector_broadcast("bronze", "Combat") is False

    def test_silver_no_sector(self):
        assert _medal_qualifies_for_sector_broadcast("silver", "Combat") is False

    def test_gold_sector(self):
        assert _medal_qualifies_for_sector_broadcast("gold", "Combat") is True

    def test_unique_tier_sector(self):
        assert _medal_qualifies_for_sector_broadcast("unique", "Special") is True

    def test_unique_category_sector(self):
        assert _medal_qualifies_for_sector_broadcast("bronze", "UNIQUE") is True


class TestMedalTeamPrivacy:
    def test_broadcast_to_team_default_on(self):
        player = MagicMock()
        player.settings = {}
        assert _medal_broadcast_to_team_allowed(player) is True

    def test_broadcast_to_team_explicit_off(self):
        player = MagicMock()
        player.settings = {"medal_privacy": {"broadcast_to_team": False}}
        assert _medal_broadcast_to_team_allowed(player) is False


@pytest.mark.asyncio
async def test_send_medal_awarded_personal_team_sector():
    svc = EnhancedWebSocketService()
    svc.connection_manager.send_personal_message = AsyncMock()
    svc.connection_manager.broadcast_to_team = AsyncMock()
    svc.connection_manager.broadcast_to_sector = AsyncMock()

    payload = {"medal_id": "combat.quantum_cross", "medal_tier": "gold"}
    await svc.send_medal_awarded(
        "user-1",
        payload,
        team_id="team-abc",
        sector_id=42,
    )

    svc.connection_manager.send_personal_message.assert_awaited_once()
    svc.connection_manager.broadcast_to_team.assert_awaited_once()
    team_call = svc.connection_manager.broadcast_to_team.await_args
    assert team_call.args[0] == "team-abc"
    assert team_call.kwargs.get("exclude_user") == "user-1"
    assert team_call.args[1]["medal_id"] == "combat.quantum_cross"
    assert team_call.args[1]["type"] == "medal_awarded"
    sector_call = svc.connection_manager.broadcast_to_sector.await_args
    assert sector_call.args[0] == 42
    assert sector_call.kwargs.get("exclude_user") == "user-1"


@pytest.mark.asyncio
async def test_send_medal_awarded_personal_only_when_no_rooms():
    svc = EnhancedWebSocketService()
    svc.connection_manager.send_personal_message = AsyncMock()
    svc.connection_manager.broadcast_to_team = AsyncMock()
    svc.connection_manager.broadcast_to_sector = AsyncMock()

    await svc.send_medal_awarded("user-1", {"medal_id": "x", "medal_tier": "bronze"})

    svc.connection_manager.send_personal_message.assert_awaited_once()
    svc.connection_manager.broadcast_to_team.assert_not_awaited()
    svc.connection_manager.broadcast_to_sector.assert_not_awaited()


def _run_dispatch_with_loop(db, player_id, medal_id, mock_svc, catalog_entry):
    async def _inner():
        with patch(
            "src.services.enhanced_websocket_service.get_enhanced_websocket_service",
            return_value=mock_svc,
        ), patch(
            "src.services.medal_service.get_catalog_entry",
            return_value=catalog_entry,
        ):
            _dispatch_medal_awarded_event(db, player_id, medal_id, "test")
            await asyncio.sleep(0)

    asyncio.run(_inner())


def test_dispatch_medal_awarded_event_routes_rooms():
    player_id = uuid4()
    team_id = uuid4()
    user_id = uuid4()

    player = MagicMock()
    player.id = player_id
    player.user_id = user_id
    player.team_id = team_id
    player.current_sector_id = 99
    player.settings = {}

    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = player

    mock_svc = MagicMock()
    mock_svc.send_medal_awarded = AsyncMock()

    _run_dispatch_with_loop(
        db,
        player_id,
        "combat.quantum_cross",
        mock_svc,
        {
            "name": "Quantum Cross",
            "category": "Combat",
            "tier": "gold",
            "description": "d",
            "criteria": {"icon": "star"},
        },
    )

    mock_svc.send_medal_awarded.assert_awaited_once()
    call_kw = mock_svc.send_medal_awarded.await_args.kwargs
    assert call_kw["team_id"] == str(team_id)
    assert call_kw["sector_id"] == 99


def test_dispatch_skips_team_when_broadcast_disabled():
    player_id = uuid4()
    user_id = uuid4()

    player = MagicMock()
    player.id = player_id
    player.user_id = user_id
    player.team_id = uuid4()
    player.current_sector_id = 1
    player.settings = {"medal_privacy": {"broadcast_to_team": False}}

    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = player

    mock_svc = MagicMock()
    mock_svc.send_medal_awarded = AsyncMock()

    _run_dispatch_with_loop(
        db,
        player_id,
        "combat.bronze_star",
        mock_svc,
        {
            "name": "Bronze",
            "category": "Combat",
            "tier": "bronze",
            "criteria": {},
        },
    )

    mock_svc.send_medal_awarded.assert_awaited_once()
    call_kw = mock_svc.send_medal_awarded.await_args.kwargs
    assert call_kw["team_id"] is None
    assert call_kw["sector_id"] is None


def test_dispatch_personal_only_when_sector_id_missing():
    player_id = uuid4()
    user_id = uuid4()

    player = MagicMock()
    player.id = player_id
    player.user_id = user_id
    player.team_id = None
    player.current_sector_id = None
    player.settings = {}

    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = player

    mock_svc = MagicMock()
    mock_svc.send_medal_awarded = AsyncMock()

    _run_dispatch_with_loop(
        db,
        player_id,
        "combat.quantum_cross",
        mock_svc,
        {
            "name": "Gold",
            "category": "Combat",
            "tier": "gold",
            "criteria": {},
        },
    )

    mock_svc.send_medal_awarded.assert_awaited_once()
    call_kw = mock_svc.send_medal_awarded.await_args.kwargs
    assert call_kw["sector_id"] is None
