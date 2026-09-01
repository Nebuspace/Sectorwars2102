"""LEG-3693/3709 — admin_ships routes HTTP 500 must not echo Exception text.

Mirrors LEG-3570 admin_colonization opaque densify.
"""

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
async def test_get_ships_unexpected_is_opaque_500():
    """LEG-3693 — ship list catch must not echo raw Exception text."""
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
    assert exc.detail == "Failed to list ships"
    assert "secret-ships-list-should-not-leak" not in str(exc.detail)


@pytest.mark.asyncio
async def test_emergency_ship_action_unexpected_is_opaque_500():
    """LEG-3693 — emergency action catch must not echo raw Exception text."""
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
    assert exc.detail == "Failed to perform emergency ship action"
    assert secret not in str(exc.detail)


@pytest.mark.asyncio
async def test_create_ship_unexpected_is_opaque_500():
    """LEG-3709 — create ship catch must not echo raw Exception text."""
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
    assert exc.detail == "Failed to create ship"
    assert secret not in str(exc.detail)


def test_admin_ships_http500_catches_have_no_detail_str_e():
    """LEG-3693/3709 — static pin: opaque 500 details stay non-interpolated."""
    src = Path(ships_mod.__file__).read_text(encoding="utf-8")
    for stable in (
        'detail="Failed to list ships"',
        'detail="Failed to perform emergency ship action"',
        'detail="Failed to fetch fleet health report"',
        'detail="Failed to create ship"',
    ):
        assert stable in src
    assert "Failed to list ships: {str(e)}" not in src
    assert "Failed to perform emergency ship action: {str(e)}" not in src
    assert "Failed to fetch fleet health report: {str(e)}" not in src
    assert "Failed to create ship: {str(e)}" not in src
