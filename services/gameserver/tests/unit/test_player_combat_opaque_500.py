"""LEG-3825 — player_combat engage must not echo Exception text on 500s.

Mirrors LEG-3818 translation / LEG-3815 haggle opaque densify family.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

from src.api.routes import player_combat as player_combat_mod
from src.api.routes.player_combat import CombatEngageRequest, engage_combat


def _player():
    return SimpleNamespace(id=uuid.uuid4(), current_sector_id=1)


@pytest.mark.asyncio
async def test_engage_combat_unexpected_is_opaque_500():
    """engage_combat catch must not echo raw Exception text."""
    secret = "secret-engage-should-not-leak"
    request = CombatEngageRequest(
        targetType="port",
        targetId=str(uuid.uuid4()),
    )
    db = MagicMock()

    with patch.object(
        player_combat_mod.CombatService,
        "attack_port",
        side_effect=RuntimeError(secret),
    ):
        with pytest.raises(HTTPException) as excinfo:
            await engage_combat(request=request, player=_player(), db=db)

    exc = excinfo.value
    assert exc.status_code == 500
    assert exc.detail == {
        "error_code": "ERR_PLAYER_COMBAT_ENGAGE_FAILED",
        "detail": "Failed to engage in combat",
    }
    assert secret not in str(exc.detail)
    db.rollback.assert_called_once()


def test_player_combat_engage_http500_catch_has_no_detail_str_e():
    """LEG-3825 — static pin: engage HTTP 500 catch path stays opaque."""
    src = Path(player_combat_mod.__file__).read_text(encoding="utf-8")
    assert "ERR_PLAYER_COMBAT_ENGAGE_FAILED" in src
    assert "route_internal_error" in src
    assert "Failed to engage in combat: {str(e)}" not in src
    assert "detail=str(e)" not in src.split("async def engage_combat")[1].split(
        "@router.get(\"/{combatId}/status\""
    )[0]
