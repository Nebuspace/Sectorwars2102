"""LEG-3794 — planets route cluster must not echo Exception text on 500s."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException

from src.api.routes import planets as mod
from src.api.routes.planets import (
    BuildingUpgradeRequest,
    DefenseUpdateRequest,
    GenesisDeployRequest,
    PlanetResourceAllocation,
    allocate_colonists,
    deploy_genesis_device,
    get_planet_defenses,
    get_planet_details,
    update_defenses,
    upgrade_building,
    upgrade_shield_generator,
)


def _player():
    return SimpleNamespace(id=uuid4())


@pytest.mark.asyncio
async def test_upgrade_shield_generator_boom_is_opaque_500():
    secret = "secret-shield-upgrade-should-not-leak"
    mock_service = MagicMock()
    mock_service.upgrade_shield_generator.side_effect = RuntimeError(secret)

    with patch.object(mod, "PlanetaryService", return_value=mock_service):
        with pytest.raises(HTTPException) as excinfo:
            await upgrade_shield_generator(
                planet_id=str(uuid4()),
                player=_player(),
                db=MagicMock(),
            )

    exc = excinfo.value
    assert exc.status_code == 500
    assert exc.detail == {
        "error_code": "ERR_PLANETS_SHIELD_UPGRADE_FAILED",
        "detail": "Failed to upgrade shield generator",
    }
    assert secret not in str(exc.detail)


@pytest.mark.asyncio
async def test_get_planet_defenses_boom_is_opaque_500():
    secret = "secret-defenses-should-not-leak"
    mock_service = MagicMock()
    mock_service.get_defense_info.side_effect = RuntimeError(secret)

    with patch.object(mod, "PlanetaryService", return_value=mock_service):
        with pytest.raises(HTTPException) as excinfo:
            await get_planet_defenses(
                planet_id=str(uuid4()),
                player=_player(),
                db=MagicMock(),
            )

    exc = excinfo.value
    assert exc.status_code == 500
    assert exc.detail == {
        "error_code": "ERR_PLANETS_DEFENSES_FETCH_FAILED",
        "detail": "Failed to fetch planet defenses",
    }
    assert secret not in str(exc.detail)


@pytest.mark.asyncio
async def test_get_planet_details_boom_is_opaque_500():
    secret = "secret-planet-details-should-not-leak"
    mock_service = MagicMock()
    mock_service.get_planet_details.side_effect = RuntimeError(secret)

    with patch.object(mod, "PlanetaryService", return_value=mock_service):
        with pytest.raises(HTTPException) as excinfo:
            await get_planet_details(
                planetId=str(uuid4()),
                player=_player(),
                db=MagicMock(),
            )

    exc = excinfo.value
    assert exc.status_code == 500
    assert exc.detail == "Failed to fetch planet details"
    assert secret not in str(exc.detail)


@pytest.mark.asyncio
async def test_allocate_colonists_boom_is_opaque_500():
    secret = "secret-allocate-colonists-should-not-leak"
    mock_service = MagicMock()
    mock_service.allocate_colonists.side_effect = RuntimeError(secret)
    allocation = PlanetResourceAllocation(fuel=1, organics=1, equipment=1)

    with patch.object(mod, "PlanetaryService", return_value=mock_service):
        with pytest.raises(HTTPException) as excinfo:
            await allocate_colonists(
                planetId=str(uuid4()),
                allocation=allocation,
                player=_player(),
                db=MagicMock(),
            )

    exc = excinfo.value
    assert exc.status_code == 500
    assert exc.detail == "Failed to allocate colonists"
    assert secret not in str(exc.detail)


@pytest.mark.asyncio
async def test_upgrade_building_boom_is_opaque_500():
    secret = "secret-upgrade-building-should-not-leak"
    mock_service = MagicMock()
    mock_service.upgrade_building.side_effect = RuntimeError(secret)
    request = BuildingUpgradeRequest(buildingType="factory", targetLevel=2)

    with patch.object(mod, "PlanetaryService", return_value=mock_service):
        with pytest.raises(HTTPException) as excinfo:
            await upgrade_building(
                planetId=str(uuid4()),
                request=request,
                player=_player(),
                db=MagicMock(),
            )

    exc = excinfo.value
    assert exc.status_code == 500
    assert exc.detail == "Failed to upgrade building"
    assert secret not in str(exc.detail)


@pytest.mark.asyncio
async def test_update_defenses_boom_is_opaque_500():
    secret = "secret-update-defenses-should-not-leak"
    mock_service = MagicMock()
    mock_service.update_defenses.side_effect = RuntimeError(secret)
    request = DefenseUpdateRequest(turrets=1, shields=0, fighters=0)

    with patch.object(mod, "PlanetaryService", return_value=mock_service):
        with pytest.raises(HTTPException) as excinfo:
            await update_defenses(
                planetId=str(uuid4()),
                request=request,
                player=_player(),
                db=MagicMock(),
            )

    exc = excinfo.value
    assert exc.status_code == 500
    assert exc.detail == {
        "error_code": "ERR_PLANETS_DEFENSES_UPDATE_FAILED",
        "detail": "Failed to update defenses",
    }
    assert secret not in str(exc.detail)


@pytest.mark.asyncio
async def test_deploy_genesis_device_boom_is_opaque_500():
    secret = "secret-genesis-deploy-should-not-leak"
    mock_service = MagicMock()
    mock_service.deploy_genesis_device.side_effect = RuntimeError(secret)
    request = GenesisDeployRequest(sectorId="42", planetName="TestColony")

    with patch(
        "src.services.genesis_service.GenesisService", return_value=mock_service
    ):
        with pytest.raises(HTTPException) as excinfo:
            await deploy_genesis_device(
                request=request,
                player=_player(),
                db=MagicMock(),
            )

    exc = excinfo.value
    assert exc.status_code == 500
    assert exc.detail == "Failed to deploy genesis device"
    assert secret not in str(exc.detail)


def test_planets_http500_is_opaque():
    """LEG-3794 — static pin: planets route 500 details stay opaque."""
    src = Path(mod.__file__).read_text(encoding="utf-8")
    opaque_details = [
        'detail="Failed to fetch planet details"',
        'detail="Failed to allocate colonists"',
        'detail="Failed to upgrade building"',
        'detail="Failed to deploy genesis device"',
    ]
    structured_codes = [
        "ERR_PLANETS_SHIELD_UPGRADE_FAILED",
        "ERR_PLANETS_DEFENSES_FETCH_FAILED",
        "ERR_PLANETS_DEFENSES_UPDATE_FAILED",
    ]
    for needle in opaque_details:
        assert needle in src
    for code in structured_codes:
        assert code in src
    assert "Failed to upgrade shield generator: {str(e)}" not in src
    assert "Failed to fetch planet defenses: {str(e)}" not in src
    assert "Failed to fetch planet details: {str(e)}" not in src
    assert "Failed to allocate colonists: {str(e)}" not in src
    assert "Failed to upgrade building: {str(e)}" not in src
    assert "Failed to update defenses: {str(e)}" not in src
    assert "Failed to deploy genesis device: {str(e)}" not in src
