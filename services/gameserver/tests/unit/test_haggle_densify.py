"""LEG-3913 densify — structured route_internal_error 500 densify.

LEG-3815 — haggle.py HTTP 500 catches return structured Exception text.

Mirrors LEG-3805 messages / LEG-3604 trading structured densify.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

from src.api.routes import haggle as haggle_mod
from src.api.routes.haggle import (
    HaggleOfferRequest,
    HaggleOpenRequest,
    haggle_status,
    open_haggle,
    submit_offer,
)


def _player():
    return SimpleNamespace(id=uuid.uuid4())


def _station():
    return SimpleNamespace(id=uuid.uuid4(), sector_id=1)


@pytest.mark.asyncio
async def test_open_haggle_unexpected_returns_structured_500():
    secret = "secret-open-haggle-should-not-leak"
    body = HaggleOpenRequest(
        station_id=str(uuid.uuid4()),
        commodity="ore",
        side="buy",
        quantity=10,
    )
    db = MagicMock()

    with patch.object(haggle_mod, "_station_or_404", return_value=_station()), \
         patch.object(haggle_mod, "_require_docked_here"), \
         patch.object(
             haggle_mod.HaggleService,
             "open_session",
             side_effect=RuntimeError(secret),
         ):
        with pytest.raises(HTTPException) as excinfo:
            await open_haggle(
                body=body,
                db=db,
                current_user=SimpleNamespace(id=uuid.uuid4()),
                current_player=_player(),
            )

    exc = excinfo.value
    assert exc.status_code == 500
    assert exc.detail == {
            "error_code": "ERR_HAGGLE_OPEN_FAILED",
            "detail": "Failed to open haggle session",
        }
    assert secret not in str(exc.detail)
    db.rollback.assert_called_once()


@pytest.mark.asyncio
async def test_submit_offer_unexpected_returns_structured_500():
    secret = "secret-submit-offer-should-not-leak"
    body = HaggleOfferRequest(
        station_id=str(uuid.uuid4()),
        commodity="ore",
        side="buy",
        offer=90.0,
    )
    db = MagicMock()

    with patch.object(haggle_mod, "_station_or_404", return_value=_station()), \
         patch.object(haggle_mod, "_require_docked_here"), \
         patch.object(
             haggle_mod.HaggleService,
             "submit_offer",
             side_effect=RuntimeError(secret),
         ):
        with pytest.raises(HTTPException) as excinfo:
            await submit_offer(
                body=body,
                db=db,
                current_user=SimpleNamespace(id=uuid.uuid4()),
                current_player=_player(),
            )

    exc = excinfo.value
    assert exc.status_code == 500
    assert exc.detail == {
            "error_code": "ERR_HAGGLE_OFFER_FAILED",
            "detail": "Failed to submit offer",
        }
    assert secret not in str(exc.detail)
    db.rollback.assert_called_once()


@pytest.mark.asyncio
async def test_haggle_status_unexpected_returns_structured_500():
    secret = "secret-haggle-status-should-not-leak"
    station_id = str(uuid.uuid4())

    with patch.object(haggle_mod, "_station_or_404", return_value=_station()), \
         patch.object(
             haggle_mod.HaggleService,
             "get_status",
             side_effect=RuntimeError(secret),
         ):
        with pytest.raises(HTTPException) as excinfo:
            await haggle_status(
                station_id=station_id,
                commodity="ore",
                side="buy",
                db=MagicMock(),
                current_user=SimpleNamespace(id=uuid.uuid4()),
                current_player=_player(),
            )

    exc = excinfo.value
    assert exc.status_code == 500
    assert exc.detail == {
            "error_code": "ERR_HAGGLE_STATUS_FAILED",
            "detail": "Failed to read haggle status",
        }
    assert secret not in str(exc.detail)


def test_haggle_http500_catches_have_no_detail_str_e():
    """LEG-3815 — static pin: all three HTTP 500 catch paths stay opaque."""
    src = Path(haggle_mod.__file__).read_text(encoding="utf-8")
    assert "ERR_HAGGLE_OPEN_FAILED" in src
    assert "ERR_HAGGLE_OFFER_FAILED" in src
    assert "ERR_HAGGLE_STATUS_FAILED" in src
    assert "route_internal_error" in src
    assert "Failed to open haggle session: {str(e)}" not in src
    assert "Failed to submit offer: {str(e)}" not in src
    assert "Failed to read haggle status: {str(e)}" not in src
