"""LEG-3835 — admin_ships unexpected failures return structured 500s."""

from __future__ import annotations

import uuid
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

from src.api.routes import admin_ships as ships_mod
from src.api.routes.admin_ships import (
    CreateShipRequest,
    EmergencyAction,
    EmergencyActionRequest,
    create_ship,
    emergency_ship_action,
    get_ships,
)
from src.models.ship import ShipType


class _BoomDB:
    def query(self, *args, **kwargs):
        raise RuntimeError("secret-ships-list-should-not-leak")


@contextmanager
def _noop_admin_action_attempt(*_args, **_kwargs):
    yield MagicMock()


@pytest.mark.asyncio
async def test_get_ships_unexpected_returns_structured_500():
    with pytest.raises(HTTPException) as excinfo:
        await get_ships(
            page=1,
            limit=50,
            status=None,
            type=None,
            owner_id=None,
            sector_id=None,
            admin=SimpleNamespace(),
            db=_BoomDB(),
        )

    exc = excinfo.value
    assert exc.status_code == 500
    assert exc.detail == {
        "error_code": "ERR_ADMIN_SHIPS_LIST_FAILED",
        "detail": "Failed to list ships",
    }
    assert "secret-ships-list-should-not-leak" not in str(exc.detail)


@pytest.mark.asyncio
async def test_emergency_ship_action_unexpected_returns_structured_500():
    secret = "secret-ship-emergency-should-not-leak"
    ship_id = uuid.uuid4()
    body = EmergencyActionRequest(action=EmergencyAction.REPAIR)

    boom_db = MagicMock()
    boom_db.query.side_effect = RuntimeError(secret)

    with patch.object(ships_mod, "admin_action_attempt", _noop_admin_action_attempt):
        with pytest.raises(HTTPException) as excinfo:
            await emergency_ship_action(
                ship_id=ship_id,
                request=body,
                admin=SimpleNamespace(id=uuid.uuid4()),
                db=boom_db,
            )

    exc = excinfo.value
    assert exc.status_code == 500
    assert exc.detail == {
        "error_code": "ERR_ADMIN_SHIPS_EMERGENCY_FAILED",
        "detail": "Failed to perform emergency ship action",
    }
    assert secret not in str(exc.detail)


@pytest.mark.asyncio
async def test_create_ship_unexpected_returns_structured_500():
    secret = "secret-ship-create-should-not-leak"
    request = CreateShipRequest(
        type=ShipType.LIGHT_FREIGHTER,
        owner_id=uuid.uuid4(),
        sector_id=uuid.uuid4(),
    )
    boom_db = MagicMock()
    boom_db.query.side_effect = RuntimeError(secret)

    with patch.object(ships_mod, "admin_action_attempt", _noop_admin_action_attempt):
        with pytest.raises(HTTPException) as excinfo:
            await create_ship(
                request=request,
                admin=SimpleNamespace(id=uuid.uuid4()),
                db=boom_db,
            )

    exc = excinfo.value
    assert exc.status_code == 500
    assert exc.detail == {
        "error_code": "ERR_ADMIN_SHIPS_CREATE_FAILED",
        "detail": "Failed to create ship",
    }
    assert secret not in str(exc.detail)


def test_admin_ships_http500_catches_are_structured():
    """LEG-3835 — static pin: ship admin 500 catch paths emit error_code + detail."""
    src = Path(ships_mod.__file__).read_text(encoding="utf-8")
    for code in (
        "ERR_ADMIN_SHIPS_LIST_FAILED",
        "ERR_ADMIN_SHIPS_EMERGENCY_FAILED",
        "ERR_ADMIN_SHIPS_FLEET_HEALTH_FAILED",
        "ERR_ADMIN_SHIPS_CREATE_FAILED",
    ):
        assert code in src
    assert 'detail="Failed to list ships"' not in src
