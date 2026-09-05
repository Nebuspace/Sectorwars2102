"""LEG-3993 — planets defense/shield routes return structured 500s."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException

from src.api.routes import planets as planets_mod
from src.api.routes.planets import (
    get_planet_defenses,
    upgrade_shield_generator,
)


def _player():
    return SimpleNamespace(id=uuid4())


@pytest.mark.asyncio
async def test_upgrade_shield_generator_boom_returns_structured_500():
    secret = "secret-shield-upgrade-should-not-leak"
    mock_service = MagicMock()
    mock_service.upgrade_shield_generator.side_effect = RuntimeError(secret)

    with patch.object(planets_mod, "PlanetaryService", return_value=mock_service):
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
async def test_get_planet_defenses_boom_returns_structured_500():
    secret = "secret-defenses-should-not-leak"
    mock_service = MagicMock()
    mock_service.get_defense_info.side_effect = RuntimeError(secret)

    with patch.object(planets_mod, "PlanetaryService", return_value=mock_service):
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


def test_planets_defense_http500_is_structured():
    """LEG-3993 — static pin: defense/shield route 500 details are structured."""
    src = Path(planets_mod.__file__).read_text(encoding="utf-8")
    for code in (
        "ERR_PLANETS_SHIELD_UPGRADE_FAILED",
        "ERR_PLANETS_DEFENSES_FETCH_FAILED",
        "ERR_PLANETS_DEFENSES_UPDATE_FAILED",
    ):
        assert code in src
    assert "route_internal_error" in src
    assert 'detail="Failed to upgrade shield generator"' not in src
    assert 'detail="Failed to fetch planet defenses"' not in src
    assert 'detail="Failed to update defenses"' not in src
