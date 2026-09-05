"""LEG-3817 — expeditions.py HTTP 500 catches must not echo Exception text.

Mirrors LEG-3815 haggle / LEG-3812 fleets opaque densify.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

from src.api.routes import expeditions as expeditions_mod
from src.api.routes.expeditions import (
    LaunchExpeditionRequest,
    launch_expedition,
    reroll_expedition,
)


def _player():
    return SimpleNamespace(id=uuid.uuid4())


def _planet():
    return SimpleNamespace(id=uuid.uuid4())


def _prior_expedition():
    return SimpleNamespace(
        id=uuid.uuid4(),
        planet_id=uuid.uuid4(),
        ship_id=None,
    )


@pytest.mark.asyncio
async def test_launch_expedition_unexpected_is_opaque_500():
    secret = "secret-launch-expedition-should-not-leak"
    body = LaunchExpeditionRequest(planet_id=uuid.uuid4())
    db = MagicMock()

    with patch.object(expeditions_mod, "_get_owned_planet", return_value=_planet()), \
         patch.object(expeditions_mod.first_login_service, "get_first_colony_expedition_overrides", return_value={}), \
         patch.object(
             expeditions_mod.expedition_service,
             "roll_expedition",
             side_effect=RuntimeError(secret),
         ):
        with pytest.raises(HTTPException) as excinfo:
            await launch_expedition(request=body, player=_player(), db=db)

    exc = excinfo.value
    assert exc.status_code == 500
    assert exc.detail == {
        "error_code": "ERR_EXPEDITIONS_LAUNCH_FAILED",
        "detail": "Failed to launch expedition",
    }
    assert secret not in str(exc.detail)
    db.rollback.assert_called_once()


@pytest.mark.asyncio
async def test_reroll_expedition_unexpected_is_opaque_500():
    secret = "secret-reroll-expedition-should-not-leak"
    prior = _prior_expedition()
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = prior

    with patch.object(expeditions_mod, "_get_owned_planet", return_value=_planet()), \
         patch.object(
             expeditions_mod.expedition_service,
             "reroll_expedition",
             side_effect=RuntimeError(secret),
         ):
        with pytest.raises(HTTPException) as excinfo:
            await reroll_expedition(expedition_id=prior.id, player=_player(), db=db)

    exc = excinfo.value
    assert exc.status_code == 500
    assert exc.detail == {
        "error_code": "ERR_EXPEDITIONS_REROLL_FAILED",
        "detail": "Failed to reroll expedition",
    }
    assert secret not in str(exc.detail)
    db.rollback.assert_called_once()


def test_expeditions_http500_catches_have_no_bare_reraise():
    """LEG-3817 — static pin: launch/reroll use opaque HTTPException on unexpected errors."""
    src = Path(expeditions_mod.__file__).read_text(encoding="utf-8")
    for code in (
        "ERR_EXPEDITIONS_LAUNCH_FAILED",
        "ERR_EXPEDITIONS_REROLL_FAILED",
    ):
        assert code in src
    assert "route_internal_error" in src
    assert 'Failed to launch expedition: {str(e)}' not in src
    assert 'Failed to reroll expedition: {str(e)}' not in src
    # No bare re-raise after except Exception in launch/reroll handlers.
    launch_block = src.split("async def launch_expedition")[1].split("async def expedition_status")[0]
    reroll_block = src.split("async def reroll_expedition")[1]
    assert "except Exception:\n        db.rollback()\n        raise\n" not in launch_block
    assert "except Exception:\n        db.rollback()\n        raise\n" not in reroll_block
