"""LEG-3837 — admin_factions unexpected failures return structured 500s."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException

from src.api.routes import admin_factions as af_mod
from src.api.routes.admin_factions import (
    FactionCreateRequest,
    FactionUpdateRequest,
    create_faction,
    list_all_factions,
    update_faction,
)
from src.models.faction import FactionType


class _BoomDB:
    def query(self, *args, **kwargs):
        raise RuntimeError("secret-faction-query-should-not-leak")


@pytest.mark.asyncio
async def test_list_all_factions_unexpected_returns_structured_500():
    secret = "secret-faction-list-should-not-leak"
    with patch.object(af_mod, "FactionService") as svc_cls:
        svc_cls.return_value.get_all_factions = AsyncMock(
            side_effect=RuntimeError(secret)
        )
        with pytest.raises(HTTPException) as excinfo:
            await list_all_factions(admin_user=SimpleNamespace(), db=MagicMock())

    exc = excinfo.value
    assert exc.status_code == 500
    assert exc.detail == {
        "error_code": "ERR_ADMIN_FACTIONS_LIST_FAILED",
        "detail": "Failed to list factions",
    }
    assert secret not in str(exc.detail)


@pytest.mark.asyncio
async def test_create_faction_unexpected_returns_structured_500():
    request = FactionCreateRequest(name="Test Faction", faction_type=FactionType.MERCHANTS)
    with pytest.raises(HTTPException) as excinfo:
        await create_faction(
            request=request,
            admin_user=SimpleNamespace(id=1),
            db=_BoomDB(),
        )

    exc = excinfo.value
    assert exc.status_code == 500
    assert exc.detail == {
        "error_code": "ERR_ADMIN_FACTIONS_CREATE_FAILED",
        "detail": "Failed to create faction",
    }
    assert "secret-faction-query-should-not-leak" not in str(exc.detail)


@pytest.mark.asyncio
async def test_update_faction_unexpected_returns_structured_500():
    secret = "secret-faction-update-should-not-leak"
    faction_id = uuid4()
    with patch.object(af_mod, "FactionService") as svc_cls:
        svc_cls.return_value.get_faction_by_id = AsyncMock(
            side_effect=RuntimeError(secret)
        )
        with pytest.raises(HTTPException) as excinfo:
            await update_faction(
                faction_id=faction_id,
                request=FactionUpdateRequest(name="Renamed"),
                admin_user=SimpleNamespace(id=1),
                db=MagicMock(),
            )

    exc = excinfo.value
    assert exc.status_code == 500
    assert exc.detail == {
        "error_code": "ERR_ADMIN_FACTIONS_UPDATE_FAILED",
        "detail": "Failed to update faction",
    }
    assert secret not in str(exc.detail)


def test_admin_factions_http500_catches_are_structured():
    """LEG-3837 — static pin: faction admin 500 catch paths emit error_code + detail."""
    src = Path(af_mod.__file__).read_text(encoding="utf-8")
    for code in (
        "ERR_ADMIN_FACTIONS_LIST_FAILED",
        "ERR_ADMIN_FACTIONS_CREATE_FAILED",
        "ERR_ADMIN_FACTIONS_UPDATE_FAILED",
        "ERR_ADMIN_FACTIONS_TERRITORY_UPDATE_FAILED",
        "ERR_ADMIN_FACTIONS_REPUTATION_UPDATE_FAILED",
    ):
        assert code in src
    assert 'detail="Failed to list factions"' not in src
