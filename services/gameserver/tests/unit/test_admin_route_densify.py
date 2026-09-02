"""LEG-3930 — admin.py unexpected failures return structured 500s."""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

from src.api.routes import admin as admin_mod
from src.api.routes.admin import (
    get_all_regions,
    get_all_stations,
    get_all_users,
    get_game_event_detail,
    update_player,
)


class _BoomDB:
    def query(self, *args, **kwargs):
        raise RuntimeError("secret-admin-route-should-not-leak")


@contextmanager
def _noop_admin_action_attempt(*_args, **_kwargs):
    yield MagicMock()


@pytest.mark.asyncio
async def test_get_all_users_unexpected_returns_structured_500():
    with pytest.raises(HTTPException) as excinfo:
        await get_all_users(current_admin=SimpleNamespace(), db=_BoomDB())

    exc = excinfo.value
    assert exc.status_code == 500
    assert exc.detail == {
        "error_code": "ERR_ADMIN_USERS_LIST_FAILED",
        "detail": "Failed to fetch users",
    }
    assert "secret-admin-route-should-not-leak" not in str(exc.detail)


@pytest.mark.asyncio
async def test_get_all_regions_unexpected_returns_structured_500():
    with pytest.raises(HTTPException) as excinfo:
        await get_all_regions(current_admin=SimpleNamespace(), db=_BoomDB())

    exc = excinfo.value
    assert exc.status_code == 500
    assert exc.detail == {
        "error_code": "ERR_ADMIN_REGIONS_LIST_FAILED",
        "detail": "Failed to fetch regions",
    }
    assert "secret-admin-route-should-not-leak" not in str(exc.detail)


@pytest.mark.asyncio
async def test_get_all_stations_unexpected_returns_structured_500():
    with pytest.raises(HTTPException) as excinfo:
        await get_all_stations(
            limit=100,
            offset=0,
            search=None,
            current_admin=SimpleNamespace(),
            db=_BoomDB(),
        )

    exc = excinfo.value
    assert exc.status_code == 500
    assert exc.detail == {
        "error_code": "ERR_ADMIN_STATIONS_LIST_FAILED",
        "detail": "Failed to fetch stations",
    }
    assert "secret-admin-route-should-not-leak" not in str(exc.detail)


@pytest.mark.asyncio
async def test_update_player_unexpected_returns_structured_500():
    secret = "secret-admin-player-update-should-not-leak"
    boom_db = MagicMock()
    boom_db.query.side_effect = RuntimeError(secret)

    with patch.object(admin_mod, "admin_action_attempt", _noop_admin_action_attempt):
        with pytest.raises(HTTPException) as excinfo:
            await update_player(
                player_id="00000000-0000-0000-0000-000000000001",
                update_data={"is_active": True},
                current_admin=SimpleNamespace(id="admin-1"),
                db=boom_db,
            )

    exc = excinfo.value
    assert exc.status_code == 500
    assert exc.detail == {
        "error_code": "ERR_ADMIN_PLAYER_UPDATE_FAILED",
        "detail": "Failed to update player",
    }
    assert secret not in str(exc.detail)


@pytest.mark.asyncio
async def test_get_game_event_detail_unexpected_returns_structured_500():
    with pytest.raises(HTTPException) as excinfo:
        await get_game_event_detail(
            event_id="00000000-0000-0000-0000-000000000099",
            current_admin=SimpleNamespace(),
            db=_BoomDB(),
        )

    exc = excinfo.value
    assert exc.status_code == 500
    assert exc.detail == {
        "error_code": "ERR_ADMIN_GAME_EVENT_GET_FAILED",
        "detail": "Failed to fetch game event",
    }
    assert "secret-admin-route-should-not-leak" not in str(exc.detail)


def test_admin_route_http500_catches_are_structured():
    """LEG-3930 — static pin: five catch paths emit error_code + detail."""
    src = Path(admin_mod.__file__).read_text(encoding="utf-8")
    for code in (
        "ERR_ADMIN_USERS_LIST_FAILED",
        "ERR_ADMIN_REGIONS_LIST_FAILED",
        "ERR_ADMIN_PLAYER_UPDATE_FAILED",
        "ERR_ADMIN_STATIONS_LIST_FAILED",
        "ERR_ADMIN_GAME_EVENT_GET_FAILED",
    ):
        assert code in src
    assert "route_internal_error" in src
    assert 'HTTPException(status_code=500, detail="Failed to fetch users")' not in src
    assert 'HTTPException(status_code=500, detail="Failed to fetch regions")' not in src
    assert 'HTTPException(status_code=500, detail="Failed to update player")' not in src
    assert 'HTTPException(status_code=500, detail="Failed to fetch stations")' not in src
    assert 'HTTPException(status_code=500, detail="Failed to fetch game event")' not in src
