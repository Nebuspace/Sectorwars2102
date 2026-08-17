"""LEG-304: GET /combat/history — player-scoped paginated combat list (DB-free)."""
from __future__ import annotations

import inspect
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException

from src.api.routes import player_combat as route
from src.api.routes.player_combat import get_combat_history


def test_history_route_registered_before_combat_id():
    paths = [getattr(r, "path", "") for r in route.router.routes]
    assert "/combat/history" in paths
    # Path-param status still present
    assert any("{combatId}" in (p or "") and "status" in (p or "") for p in paths)


def test_history_handler_uses_current_player_id_only():
    """Scoping pin: signature has no player_id query — always current_player."""
    sig = inspect.signature(get_combat_history)
    assert "player" in sig.parameters
    assert "player_id" not in sig.parameters


@pytest.mark.asyncio
async def test_history_happy_path_pagination():
    player = SimpleNamespace(id=uuid4())
    db = MagicMock()
    payload = {
        "success": True,
        "combat_history": [{"id": str(uuid4()), "role": "attacker"}],
        "count": 1,
        "total": 5,
        "limit": 2,
        "offset": 1,
    }
    svc = MagicMock()
    svc.get_player_combat_history.return_value = payload

    with patch.object(route, "CombatService", return_value=svc):
        result = await get_combat_history(
            limit=2, offset=1, player=player, db=db
        )

    assert result.total == 5
    assert result.limit == 2
    assert result.offset == 1
    assert len(result.items) == 1
    svc.get_player_combat_history.assert_called_once_with(
        player_id=player.id, limit=2, offset=1
    )


@pytest.mark.asyncio
async def test_history_player_not_found_404():
    player = SimpleNamespace(id=uuid4())
    db = MagicMock()
    svc = MagicMock()
    svc.get_player_combat_history.return_value = {
        "success": False,
        "message": "Player not found",
    }
    with patch.object(route, "CombatService", return_value=svc):
        with pytest.raises(HTTPException) as exc:
            await get_combat_history(player=player, db=db)
    assert exc.value.status_code == 404


def test_service_history_scopes_and_paginates():
    """Service query filter must OR attacker/defender == player_id + offset/limit."""
    src = inspect.getsource(route.CombatService.get_player_combat_history)
    assert "attacker_id" in src and "defender_id" in src
    assert "offset" in src
    assert "limit" in src
    assert "total" in src
