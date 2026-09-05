"""LEG-3843 — admin_beacons unexpected failures return structured 500s."""

from __future__ import annotations

import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

from src.api.routes import admin_beacons as ab_mod
from src.api.routes.admin_beacons import (
    clear_beacon_flag,
    confirm_beacon_abuse,
    get_flagged_beacons,
)


@pytest.mark.asyncio
async def test_get_flagged_beacons_unexpected_returns_structured_500():
    secret = "secret-beacons-list-should-not-leak"
    with patch.object(
        ab_mod.message_beacon_service,
        "list_flagged_beacons",
        side_effect=RuntimeError(secret),
    ):
        with pytest.raises(HTTPException) as excinfo:
            await get_flagged_beacons(
                page=1,
                admin=SimpleNamespace(),
                db=MagicMock(),
            )

    exc = excinfo.value
    assert exc.status_code == 500
    assert exc.detail == {
        "error_code": "ERR_ADMIN_BEACONS_LIST_FAILED",
        "detail": "Failed to fetch flagged beacons",
    }
    assert secret not in str(exc.detail)


@pytest.mark.asyncio
async def test_clear_beacon_flag_unexpected_returns_structured_500():
    secret = "secret-beacons-clear-should-not-leak"
    beacon_id = uuid.uuid4()
    db = MagicMock()
    with patch.object(
        ab_mod.message_beacon_service,
        "clear_flag",
        side_effect=RuntimeError(secret),
    ):
        with pytest.raises(HTTPException) as excinfo:
            await clear_beacon_flag(
                beacon_id=beacon_id,
                admin=SimpleNamespace(),
                db=db,
            )

    exc = excinfo.value
    assert exc.status_code == 500
    assert exc.detail == {
        "error_code": "ERR_ADMIN_BEACONS_CLEAR_FLAG_FAILED",
        "detail": "Failed to clear beacon flag",
    }
    assert secret not in str(exc.detail)
    db.rollback.assert_called_once()


@pytest.mark.asyncio
async def test_confirm_beacon_abuse_unexpected_returns_structured_500():
    secret = "secret-beacons-confirm-should-not-leak"
    beacon_id = uuid.uuid4()
    db = MagicMock()
    with patch.object(
        ab_mod.message_beacon_service,
        "confirm_abuse",
        side_effect=RuntimeError(secret),
    ):
        with pytest.raises(HTTPException) as excinfo:
            await confirm_beacon_abuse(
                beacon_id=beacon_id,
                admin=SimpleNamespace(),
                db=db,
            )

    exc = excinfo.value
    assert exc.status_code == 500
    assert exc.detail == {
        "error_code": "ERR_ADMIN_BEACONS_CONFIRM_ABUSE_FAILED",
        "detail": "Failed to confirm beacon abuse",
    }
    assert secret not in str(exc.detail)
    db.rollback.assert_called_once()


def test_admin_beacons_http500_catches_are_structured():
    """LEG-3843 — static pin: beacon admin 500 catch paths emit error_code + detail."""
    src = Path(ab_mod.__file__).read_text(encoding="utf-8")
    for code in (
        "ERR_ADMIN_BEACONS_LIST_FAILED",
        "ERR_ADMIN_BEACONS_CLEAR_FLAG_FAILED",
        "ERR_ADMIN_BEACONS_CONFIRM_ABUSE_FAILED",
    ):
        assert code in src
    assert "route_internal_error" in src
    assert 'detail="Failed to fetch flagged beacons"' not in src
    assert 'detail="Failed to clear beacon flag"' not in src
    assert 'detail="Failed to confirm beacon abuse"' not in src
