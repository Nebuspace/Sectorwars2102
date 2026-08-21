"""LEG-768 — GET /ai/market-intelligence/{station_id} read path.

Handler and serializer called directly (FastAPI DI bypassed), mirroring
test_aria_trade_cascade_route.py convention.
"""
from __future__ import annotations

import os
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

from cryptography.fernet import Fernet

# DB-free: set required Settings env before any src import (conftest not used).
os.environ.setdefault("ENVIRONMENT", "testing")
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://u:p@localhost:5432/db")
os.environ.setdefault("JWT_SECRET", "test_jwt_secret_at_least_32_characters_long")
os.environ.setdefault("ADMIN_USERNAME", "admin")
os.environ.setdefault("ADMIN_PASSWORD", "test_admin_password_12plus")
os.environ.setdefault("ARIA_ENCRYPTION_KEY", Fernet.generate_key().decode())

import pytest
from fastapi import HTTPException

from src.api.routes import enhanced_ai
from src.models.aria_personal_intelligence import ARIAMarketIntelligence
from src.models.player import Player
from src.models.station import Station
from src.services.aria_personal_intelligence_service import ARIAPersonalIntelligenceService


def _intel(data_points: int, commodity: str = "Equipment") -> ARIAMarketIntelligence:
    return ARIAMarketIntelligence(
        id=uuid.uuid4(),
        player_id=uuid.uuid4(),
        station_id=uuid.uuid4(),
        sector_id=uuid.uuid4(),
        commodity=commodity,
        data_points=data_points,
        average_price=1240.0,
        price_volatility=50.0,
        next_prediction=1250.0,
        prediction_confidence=0.82,
    )


def test_market_intelligence_to_read_model_honest_empty_under_five():
    svc = ARIAPersonalIntelligenceService()
    row = svc.market_intelligence_to_read_model(_intel(3))

    assert row["commodity"] == "Equipment"
    assert row["observation_count"] == 3
    assert row["average_price"] is None
    assert row["price_band"] is None
    assert row["next_prediction"] is None
    assert row["prediction_confidence"] is None


def test_market_intelligence_to_read_model_returns_prediction_at_five_plus():
    svc = ARIAPersonalIntelligenceService()
    row = svc.market_intelligence_to_read_model(_intel(8))

    assert row["observation_count"] == 8
    assert row["average_price"] == 1240.0
    assert row["price_band"] == 50.0
    assert row["next_prediction"] == 1250.0
    assert row["prediction_confidence"] == 0.82


@pytest.mark.asyncio
async def test_get_aria_market_intelligence_returns_owner_rows_when_docked():
    station_id = str(uuid.uuid4())
    player = Player(id=uuid.uuid4(), is_docked=True, current_port_id=station_id)
    player.current_sector_id = uuid.uuid4()
    station = Station(id=station_id, sector_id=player.current_sector_id)

    db = AsyncMock()
    db.get = AsyncMock(return_value=station)

    items = [
        {
            "commodity": "Equipment",
            "observation_count": 8,
            "average_price": 1240.0,
            "price_band": 50.0,
            "next_prediction": 1250.0,
            "prediction_confidence": 0.82,
        }
    ]
    svc = MagicMock()
    svc.list_market_intelligence_at_station = AsyncMock(return_value=items)

    with patch(
        "src.services.trading_service.TradingService.can_player_trade",
        return_value=(True, "OK"),
    ), patch(
        "src.services.aria_personal_intelligence_service.get_aria_intelligence_service",
        return_value=svc,
    ):
        result = await enhanced_ai.get_aria_market_intelligence(
            station_id=station_id,
            commodity=None,
            current_player=player,
            db=db,
        )

    assert result["station_id"] == station_id
    assert result["items"] == items
    svc.list_market_intelligence_at_station.assert_awaited_once_with(
        str(player.id), station_id, db, commodity=None,
    )


@pytest.mark.asyncio
async def test_get_aria_market_intelligence_forbidden_when_not_docked():
    station_id = str(uuid.uuid4())
    player = Player(id=uuid.uuid4(), is_docked=False, current_port_id=None)
    station = Station(id=station_id, sector_id=uuid.uuid4())
    db = AsyncMock()
    db.get = AsyncMock(return_value=station)

    with pytest.raises(HTTPException) as exc:
        await enhanced_ai.get_aria_market_intelligence(
            station_id=station_id,
            commodity=None,
            current_player=player,
            db=db,
        )

    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_get_aria_market_intelligence_forbidden_at_other_players_station():
    """Docked elsewhere — cannot read another station's intel (403, not leak)."""
    docked_at = str(uuid.uuid4())
    other_station = str(uuid.uuid4())
    player = Player(id=uuid.uuid4(), is_docked=True, current_port_id=docked_at)
    player.current_sector_id = uuid.uuid4()
    station = Station(id=other_station, sector_id=player.current_sector_id)
    db = AsyncMock()
    db.get = AsyncMock(return_value=station)

    with pytest.raises(HTTPException) as exc:
        await enhanced_ai.get_aria_market_intelligence(
            station_id=other_station,
            commodity=None,
            current_player=player,
            db=db,
        )

    assert exc.value.status_code == 403
    assert "docked at this station" in exc.value.detail


@pytest.mark.asyncio
async def test_get_aria_market_intelligence_not_found_for_missing_station():
    player = Player(id=uuid.uuid4(), is_docked=True, current_port_id=str(uuid.uuid4()))
    db = AsyncMock()
    db.get = AsyncMock(return_value=None)

    with pytest.raises(HTTPException) as exc:
        await enhanced_ai.get_aria_market_intelligence(
            station_id=str(uuid.uuid4()),
            commodity=None,
            current_player=player,
            db=db,
        )

    assert exc.value.status_code == 404
