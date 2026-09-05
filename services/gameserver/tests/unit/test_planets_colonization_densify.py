"""LEG-3995 — planets colonization routes return structured 500s."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException

from src.api.routes import planets as planets_mod
from src.api.routes.planets import (
    allocate_colonists,
    get_planet_details,
    upgrade_building,
)


def _player():
    return SimpleNamespace(id=uuid4())


@pytest.mark.asyncio
async def test_get_planet_details_boom_returns_structured_500():
    secret = "secret-details-should-not-leak"
    mock_service = MagicMock()
    mock_service.get_planet_details.side_effect = RuntimeError(secret)

    with patch.object(planets_mod, "PlanetaryService", return_value=mock_service):
        with pytest.raises(HTTPException) as excinfo:
            await get_planet_details(
                planetId=str(uuid4()),
                player=_player(),
                db=MagicMock(),
            )

    exc = excinfo.value
    assert exc.status_code == 500
    assert exc.detail == {
        "error_code": "ERR_PLANETS_DETAILS_FETCH_FAILED",
        "detail": "Failed to fetch planet details",
    }
    assert secret not in str(exc.detail)


@pytest.mark.asyncio
async def test_allocate_colonists_boom_returns_structured_500():
    secret = "secret-allocate-should-not-leak"
    mock_service = MagicMock()
    mock_service.allocate_colonists.side_effect = RuntimeError(secret)
    allocation = SimpleNamespace(fuel=1, organics=2, equipment=3)

    with patch.object(planets_mod, "PlanetaryService", return_value=mock_service):
        with pytest.raises(HTTPException) as excinfo:
            await allocate_colonists(
                planetId=str(uuid4()),
                allocation=allocation,
                player=_player(),
                db=MagicMock(),
            )

    exc = excinfo.value
    assert exc.status_code == 500
    assert exc.detail == {
        "error_code": "ERR_PLANETS_ALLOCATE_FAILED",
        "detail": "Failed to allocate colonists",
    }
    assert secret not in str(exc.detail)


@pytest.mark.asyncio
async def test_upgrade_building_boom_returns_structured_500():
    secret = "secret-building-upgrade-should-not-leak"
    mock_service = MagicMock()
    mock_service.upgrade_building.side_effect = RuntimeError(secret)
    request = SimpleNamespace(buildingType="mine", targetLevel=2)

    with patch.object(planets_mod, "PlanetaryService", return_value=mock_service):
        with pytest.raises(HTTPException) as excinfo:
            await upgrade_building(
                planetId=str(uuid4()),
                request=request,
                player=_player(),
                db=MagicMock(),
            )

    exc = excinfo.value
    assert exc.status_code == 500
    assert exc.detail == {
        "error_code": "ERR_PLANETS_BUILDING_UPGRADE_FAILED",
        "detail": "Failed to upgrade building",
    }
    assert secret not in str(exc.detail)


def test_planets_colonization_http500_is_structured():
    """LEG-3995 — static pin: colonization route 500 details are structured."""
    src = Path(planets_mod.__file__).read_text(encoding="utf-8")
    for code in (
        "ERR_PLANETS_DETAILS_FETCH_FAILED",
        "ERR_PLANETS_ALLOCATE_FAILED",
        "ERR_PLANETS_BUILDING_UPGRADE_FAILED",
    ):
        assert code in src
    assert "route_internal_error" in src
    assert 'detail="Failed to fetch planet details"' not in src
    assert 'detail="Failed to allocate colonists"' not in src
    assert 'detail="Failed to upgrade building"' not in src
