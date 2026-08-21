"""LEG-364: POST /drones/{drone_id}/deploy — thin route over deploy_drone.

DB-free: handlers called directly with AsyncMock db + patched DroneService,
mirroring test_aria_trade_cascade_route / test_admin_medal_catalog.
Service-layer deploy/cap behaviour stays in test_drone_cap_enforcement.py.
"""
from __future__ import annotations

import inspect
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException

from src.api.routes import drones as drones_routes
from src.api.routes.drones import DeployDroneRequest, deploy_drone, deploy_drones_contract
from src.models.drone import Drone


def _player(player_id=None):
    return SimpleNamespace(id=player_id or uuid4())


def _drone(*, player_id, status="idle"):
    return SimpleNamespace(id=uuid4(), player_id=player_id, status=status)


def _deployment(*, drone_id, player_id, sector_id):
    now = datetime.now(timezone.utc)
    return SimpleNamespace(
        id=uuid4(),
        drone_id=drone_id,
        player_id=player_id,
        sector_id=sector_id,
        deployed_at=now,
        recalled_at=None,
        is_active=True,
        deployment_type="defense",
        target_id=None,
        enemies_destroyed=0,
        resources_collected=0,
        damage_prevented=0,
    )


def test_openapi_exposes_single_and_batch_deploy_paths():
    """Accept: OpenAPI path POST /drones/{drone_id}/deploy; batch must remain."""
    paths = {getattr(r, "path", None) for r in drones_routes.router.routes}
    # APIRouter(prefix="/drones") — paths are prefix-qualified on the router.
    assert "/drones/{drone_id}/deploy" in paths
    assert "/drones/deploy" in paths
    # Handler still registered for batch contract
    assert callable(deploy_drones_contract)


def test_deploy_drone_route_uses_auth_and_async_session():
    sig = inspect.signature(deploy_drone)
    assert "current_player" in sig.parameters
    assert "db" in sig.parameters
    assert "request" in sig.parameters


@pytest.mark.asyncio
async def test_deploy_single_happy_path():
    player = _player()
    drone = _drone(player_id=player.id)
    sector_id = uuid4()
    deployment = _deployment(
        drone_id=drone.id, player_id=player.id, sector_id=sector_id
    )

    db = AsyncMock()
    db.get = AsyncMock(return_value=drone)

    svc = MagicMock()
    svc.deploy_drone = AsyncMock(return_value=deployment)

    request = DeployDroneRequest(sector_id=sector_id)

    with patch.object(drones_routes, "DroneService", return_value=svc):
        result = await deploy_drone(
            drone_id=drone.id,
            request=request,
            current_player=player,
            db=db,
        )

    assert result is deployment
    db.get.assert_awaited_once_with(Drone, drone.id)
    svc.deploy_drone.assert_awaited_once_with(
        drone_id=drone.id,
        sector_id=sector_id,
        deployment_type="defense",
        target_id=None,
    )


@pytest.mark.asyncio
async def test_deploy_single_passes_optional_kwargs():
    player = _player()
    drone = _drone(player_id=player.id)
    sector_id = uuid4()
    target_id = uuid4()
    deployment = _deployment(
        drone_id=drone.id, player_id=player.id, sector_id=sector_id
    )
    deployment.deployment_type = "patrol"
    deployment.target_id = target_id

    db = AsyncMock()
    db.get = AsyncMock(return_value=drone)
    svc = MagicMock()
    svc.deploy_drone = AsyncMock(return_value=deployment)

    request = DeployDroneRequest(
        sector_id=sector_id,
        deployment_type="patrol",
        target_id=target_id,
    )

    with patch.object(drones_routes, "DroneService", return_value=svc):
        await deploy_drone(
            drone_id=drone.id,
            request=request,
            current_player=player,
            db=db,
        )

    svc.deploy_drone.assert_awaited_once_with(
        drone_id=drone.id,
        sector_id=sector_id,
        deployment_type="patrol",
        target_id=target_id,
    )


@pytest.mark.asyncio
async def test_deploy_single_destroyed_maps_to_400():
    player = _player()
    drone = _drone(player_id=player.id, status="destroyed")
    db = AsyncMock()
    db.get = AsyncMock(return_value=drone)
    svc = MagicMock()
    svc.deploy_drone = AsyncMock(
        side_effect=ValueError("Cannot deploy destroyed drone")
    )

    with patch.object(drones_routes, "DroneService", return_value=svc):
        with pytest.raises(HTTPException) as exc:
            await deploy_drone(
                drone_id=drone.id,
                request=DeployDroneRequest(sector_id=uuid4()),
                current_player=player,
                db=db,
            )

    assert exc.value.status_code == 400
    assert "destroyed" in str(exc.value.detail).lower()


@pytest.mark.asyncio
async def test_deploy_single_cap_maps_to_400():
    player = _player()
    drone = _drone(player_id=player.id)
    db = AsyncMock()
    db.get = AsyncMock(return_value=drone)
    svc = MagicMock()
    svc.deploy_drone = AsyncMock(
        side_effect=ValueError("Drone cap reached for current ship")
    )

    with patch.object(drones_routes, "DroneService", return_value=svc):
        with pytest.raises(HTTPException) as exc:
            await deploy_drone(
                drone_id=drone.id,
                request=DeployDroneRequest(sector_id=uuid4()),
                current_player=player,
                db=db,
            )

    assert exc.value.status_code == 400
    assert "cap" in str(exc.value.detail).lower()


@pytest.mark.asyncio
async def test_deploy_single_not_owned_404():
    player = _player()
    other = _drone(player_id=uuid4())
    db = AsyncMock()
    db.get = AsyncMock(return_value=other)

    with pytest.raises(HTTPException) as exc:
        await deploy_drone(
            drone_id=other.id,
            request=DeployDroneRequest(sector_id=uuid4()),
            current_player=player,
            db=db,
        )

    assert exc.value.status_code == 404
