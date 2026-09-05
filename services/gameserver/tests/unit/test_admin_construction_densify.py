"""LEG-3834 — admin_construction unexpected failures return structured 500s."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

import pytest
from fastapi import HTTPException

from src.api.routes import admin_construction as ac_mod
from src.api.routes.admin_construction import (
    force_cancel_reservation,
    get_tradedock_overview,
    list_tradedocks,
)


@pytest.mark.asyncio
async def test_list_tradedocks_unexpected_returns_structured_500():
    secret = "secret-tradedock-list-should-not-leak"

    with patch.object(ac_mod.construction_service, "admin_list_tradedocks") as list_svc:
        list_svc.side_effect = RuntimeError(secret)
        with pytest.raises(HTTPException) as excinfo:
            await list_tradedocks(admin=SimpleNamespace(), db=SimpleNamespace())

    exc = excinfo.value
    assert exc.status_code == 500
    assert exc.detail == {
        "error_code": "ERR_ADMIN_CONSTRUCTION_TRADEDOCKS_LIST_FAILED",
        "detail": "Failed to list tradedocks",
    }
    assert secret not in str(exc.detail)


@pytest.mark.asyncio
async def test_get_tradedock_overview_unexpected_returns_structured_500():
    secret = "secret-tradedock-overview-should-not-leak"
    station_id = uuid4()
    db = SimpleNamespace(rollback=lambda: None, commit=lambda: None)

    with patch.object(ac_mod.construction_service, "admin_station_overview") as overview_svc:
        overview_svc.side_effect = RuntimeError(secret)
        with pytest.raises(HTTPException) as excinfo:
            await get_tradedock_overview(
                station_id=station_id,
                admin=SimpleNamespace(),
                db=db,
            )

    exc = excinfo.value
    assert exc.status_code == 500
    assert exc.detail == {
        "error_code": "ERR_ADMIN_CONSTRUCTION_TRADEDOCK_OVERVIEW_FAILED",
        "detail": "Failed to get tradedock overview",
    }
    assert secret not in str(exc.detail)


@pytest.mark.asyncio
async def test_force_cancel_reservation_unexpected_returns_structured_500():
    secret = "secret-force-cancel-should-not-leak"
    reservation_id = uuid4()
    db = SimpleNamespace(rollback=lambda: None, commit=lambda: None)

    with patch.object(ac_mod.construction_service, "admin_force_cancel") as cancel_svc:
        cancel_svc.side_effect = RuntimeError(secret)
        with pytest.raises(HTTPException) as excinfo:
            await force_cancel_reservation(
                reservation_id=reservation_id,
                admin=SimpleNamespace(id=uuid4()),
                db=db,
            )

    exc = excinfo.value
    assert exc.status_code == 500
    assert exc.detail == {
        "error_code": "ERR_ADMIN_CONSTRUCTION_FORCE_CANCEL_FAILED",
        "detail": "Failed to force-cancel reservation",
    }
    assert secret not in str(exc.detail)


def test_admin_construction_http500_catches_are_structured():
    """LEG-3834 — static pin: construction admin 500 catch paths emit error_code + detail."""
    src = Path(ac_mod.__file__).read_text(encoding="utf-8")
    for code in (
        "ERR_ADMIN_CONSTRUCTION_TRADEDOCKS_LIST_FAILED",
        "ERR_ADMIN_CONSTRUCTION_TRADEDOCK_OVERVIEW_FAILED",
        "ERR_ADMIN_CONSTRUCTION_RESERVATION_DETAIL_FAILED",
        "ERR_ADMIN_CONSTRUCTION_FORCE_CANCEL_FAILED",
    ):
        assert code in src
    assert 'detail="Failed to list tradedocks"' not in src
