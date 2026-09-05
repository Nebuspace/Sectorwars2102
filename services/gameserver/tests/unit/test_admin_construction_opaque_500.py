"""LEG-3694 / LEG-3714 — admin_construction HTTP 500 must not echo Exception text.

Mirrors LEG-3570 admin_colonization opaque densify.
"""

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
async def test_list_tradedocks_unexpected_is_opaque_500():
    """LEG-3694 — tradedock list catch must not echo raw Exception text."""
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
async def test_get_tradedock_overview_unexpected_is_opaque_500():
    """LEG-3714 — tradedock overview catch must not echo raw Exception text."""
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
async def test_force_cancel_reservation_unexpected_is_opaque_500():
    """LEG-3714 — force-cancel catch must not echo raw Exception text."""
    secret = "secret-force-cancel-should-not-leak"
    reservation_id = uuid4()
    db = SimpleNamespace(rollback=lambda: None, commit=lambda: None)

    with patch.object(ac_mod.construction_service, "admin_force_cancel") as cancel_svc:
        cancel_svc.side_effect = RuntimeError(secret)
        with pytest.raises(HTTPException) as excinfo:
            await force_cancel_reservation(
                reservation_id=reservation_id,
                admin=SimpleNamespace(),
                db=db,
            )

    exc = excinfo.value
    assert exc.status_code == 500
    assert exc.detail == {
        "error_code": "ERR_ADMIN_CONSTRUCTION_FORCE_CANCEL_FAILED",
        "detail": "Failed to force-cancel reservation",
    }
    assert secret not in str(exc.detail)


def test_admin_construction_list_tradedocks_http500_is_opaque():
    """LEG-3694 — static pin: list_tradedocks 500 detail stays opaque."""
    src = Path(ac_mod.__file__).read_text(encoding="utf-8")
    assert "ERR_ADMIN_CONSTRUCTION_TRADEDOCKS_LIST_FAILED" in src
    assert 'detail=f"Failed to list tradedocks: {e}"' not in src
    assert "Failed to list tradedocks: {str(e)}" not in src


def test_admin_construction_overview_detail_force_cancel_http500_is_opaque():
    """LEG-3714 — static pin: overview/detail/force-cancel 500 details stay opaque."""
    src = Path(ac_mod.__file__).read_text(encoding="utf-8")
    for code in (
        "ERR_ADMIN_CONSTRUCTION_TRADEDOCK_OVERVIEW_FAILED",
        "ERR_ADMIN_CONSTRUCTION_RESERVATION_DETAIL_FAILED",
        "ERR_ADMIN_CONSTRUCTION_FORCE_CANCEL_FAILED",
    ):
        assert code in src
