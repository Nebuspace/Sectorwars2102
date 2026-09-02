"""LEG-3628 — admin.py HTTP 500 catches must not echo Exception text.

Mirrors LEG-3605 admin_economy / LEG-3582 admin_comprehensive opaque densify.
"""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

from src.api.routes import admin as admin_mod
from src.api.routes.admin import (
    QuickEventCreateRequest,
    create_game_event,
    get_all_regions,
    get_all_stations,
    get_all_users,
    update_player,
)


class _BoomDB:
    def query(self, *args, **kwargs):
        raise RuntimeError("secret-admin-query-should-not-leak")


@contextmanager
def _noop_admin_action_attempt(*_args, **_kwargs):
    yield MagicMock()


@pytest.mark.asyncio
async def test_update_player_unexpected_is_opaque_500():
    """update_player catch must not echo raw Exception text."""
    with patch.object(admin_mod, "admin_action_attempt", _noop_admin_action_attempt):
        with pytest.raises(HTTPException) as excinfo:
            await update_player(
                player_id="player-1",
                update_data={"credits": 100},
                current_admin=SimpleNamespace(id="admin-1", username="admin"),
                db=_BoomDB(),
            )

    exc = excinfo.value
    assert exc.status_code == 500
    assert exc.detail == "Failed to update player"
    assert "secret-admin-query-should-not-leak" not in str(exc.detail)


@pytest.mark.asyncio
async def test_get_all_stations_unexpected_is_opaque_500():
    """get_all_stations catch must not echo raw Exception text."""
    with pytest.raises(HTTPException) as excinfo:
        await get_all_stations(
            current_admin=SimpleNamespace(),
            db=_BoomDB(),
        )

    exc = excinfo.value
    assert exc.status_code == 500
    assert exc.detail == "Failed to fetch stations"
    assert "secret-admin-query-should-not-leak" not in str(exc.detail)


@pytest.mark.asyncio
async def test_create_game_event_unexpected_is_opaque_500():
    """create_game_event catch must not echo raw Exception text."""
    secret = "secret-game-event-should-not-leak"
    request = QuickEventCreateRequest(
        title="Test Event",
        description="desc",
        event_type="economic",
        duration_hours=1,
    )

    with patch.object(admin_mod, "admin_action_attempt", _noop_admin_action_attempt):
        with patch.object(admin_mod, "GameEvent") as event_cls:
            event_cls.side_effect = RuntimeError(secret)
            with pytest.raises(HTTPException) as excinfo:
                await create_game_event(
                    event_data=request,
                    current_admin=SimpleNamespace(id="admin-1", username="admin"),
                    db=MagicMock(),
                )

    exc = excinfo.value
    assert exc.status_code == 500
    assert exc.detail == "Failed to create game event"
    assert secret not in str(exc.detail)


def test_admin_http500_catches_have_no_detail_str_e():
    """LEG-3628 — static pin: representative admin.py 500 details stay opaque."""
    src = Path(admin_mod.__file__).read_text(encoding="utf-8")
    for stable in (
        'detail="Failed to update player"',
        'detail="Failed to fetch stations"',
        'detail="Failed to create warp tunnel"',
        'detail="Failed to clear galaxy data"',
        'detail="Failed to fix galaxy statistics"',
        'detail="Failed to update port"',
        'detail="Failed to create game event"',
        'detail="Failed to fetch game event"',
        'detail="Failed to update game event"',
        'detail="Failed to activate game event"',
        'detail="Failed to deactivate game event"',
        'detail="Failed to delete game event"',
    ):
        assert stable in src
    assert "Failed to update player: {str(e)}" not in src
    assert "Failed to fetch stations: {str(e)}" not in src
    assert "Failed to create game event: {str(e)}" not in src


@pytest.mark.asyncio
async def test_get_all_users_unexpected_is_opaque_500():
    """get_all_users catch must not echo raw Exception text."""
    with pytest.raises(HTTPException) as excinfo:
        await get_all_users(
            current_admin=SimpleNamespace(),
            db=_BoomDB(),
        )

    exc = excinfo.value
    assert exc.status_code == 500
    assert exc.detail == "Failed to fetch users"
    assert "secret-admin-query-should-not-leak" not in str(exc.detail)


@pytest.mark.asyncio
async def test_get_all_regions_unexpected_is_opaque_500():
    """get_all_regions catch must not echo raw Exception text."""
    with pytest.raises(HTTPException) as excinfo:
        await get_all_regions(
            current_admin=SimpleNamespace(),
            db=_BoomDB(),
        )

    exc = excinfo.value
    assert exc.status_code == 500
    assert exc.detail == "Failed to fetch regions"
    assert "secret-admin-query-should-not-leak" not in str(exc.detail)


def test_admin_users_regions_http500_catches_have_no_detail_str_e():
    """LEG-3734 — static pin: users/regions list 500 details stay opaque."""
    src = Path(admin_mod.__file__).read_text(encoding="utf-8")
    assert 'detail="Failed to fetch users"' in src
    assert 'detail="Failed to fetch regions"' in src
    assert "Failed to fetch users: {str(e)}" not in src
    assert "Failed to fetch regions: {str(e)}" not in src
