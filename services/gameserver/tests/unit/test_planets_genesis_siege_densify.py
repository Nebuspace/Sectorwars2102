"""LEG-3998 — planets genesis deploy + siege-status routes return structured 500s."""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

os.environ.setdefault("ENVIRONMENT", "testing")
os.environ.setdefault("DATABASE_URL", "postgresql://ci:ci@127.0.0.1:5432/ci")
os.environ.setdefault("JWT_SECRET", "ci-test-jwt-secret-not-used-32chars!!")
os.environ.setdefault("ADMIN_USERNAME", "ci-admin-user")
os.environ.setdefault("ADMIN_PASSWORD", "ci-admin-pass-12")
os.environ.setdefault(
    "ARIA_ENCRYPTION_KEY", "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
)

import pytest
from fastapi import HTTPException

from src.api.routes import planets as planets_mod
from src.api.routes.planets import (
    GenesisDeployRequest,
    deploy_genesis_device,
    get_siege_status,
)


def _player():
    return SimpleNamespace(id=uuid4())


@pytest.mark.asyncio
async def test_deploy_genesis_device_boom_returns_structured_500():
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
    assert exc.detail == {
        "error_code": "ERR_PLANETS_GENESIS_DEPLOY_FAILED",
        "detail": "Failed to deploy genesis device",
    }
    assert secret not in str(exc.detail)


@pytest.mark.asyncio
async def test_get_siege_status_boom_returns_structured_500():
    secret = "secret-siege-status-should-not-leak"
    mock_service = MagicMock()
    mock_service.get_siege_status.side_effect = RuntimeError(secret)

    with patch.object(planets_mod, "PlanetaryService", return_value=mock_service):
        with pytest.raises(HTTPException) as excinfo:
            await get_siege_status(
                planetId=str(uuid4()),
                player=_player(),
                db=MagicMock(),
            )

    exc = excinfo.value
    assert exc.status_code == 500
    assert exc.detail == {
        "error_code": "ERR_PLANETS_SIEGE_STATUS_FETCH_FAILED",
        "detail": "Failed to fetch siege status",
    }
    assert secret not in str(exc.detail)


def test_planets_genesis_siege_http500_is_structured():
    """LEG-3998 — static pin: genesis/siege route 500 details are structured."""
    src = Path(planets_mod.__file__).read_text(encoding="utf-8")
    for code in (
        "ERR_PLANETS_GENESIS_DEPLOY_FAILED",
        "ERR_PLANETS_SIEGE_STATUS_FETCH_FAILED",
    ):
        assert code in src
    assert "route_internal_error" in src
    assert 'detail="Failed to deploy genesis device"' not in src
