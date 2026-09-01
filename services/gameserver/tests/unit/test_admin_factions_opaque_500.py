"""LEG-3705 — admin_factions CRUD routes must not echo Exception text."""

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
    ReputationUpdateRequest,
    TerritoryUpdateRequest,
    create_faction,
    list_all_factions,
    update_faction,
    update_faction_territory,
    update_player_reputation,
)
from src.models.faction import FactionType


class _BoomDB:
    def query(self, *args, **kwargs):
        raise RuntimeError("secret-faction-query-should-not-leak")


@pytest.mark.asyncio
async def test_list_all_factions_unexpected_is_opaque_500():
    secret = "secret-faction-list-should-not-leak"
    with patch.object(af_mod, "FactionService") as svc_cls:
        svc_cls.return_value.get_all_factions = AsyncMock(
            side_effect=RuntimeError(secret)
        )
        with pytest.raises(HTTPException) as excinfo:
            await list_all_factions(admin_user=SimpleNamespace(), db=MagicMock())

    exc = excinfo.value
    assert exc.status_code == 500
    assert exc.detail == "Failed to list factions"
    assert secret not in str(exc.detail)


@pytest.mark.asyncio
async def test_create_faction_unexpected_is_opaque_500():
    request = FactionCreateRequest(name="Test Faction", faction_type=FactionType.MERCHANTS)
    with pytest.raises(HTTPException) as excinfo:
        await create_faction(
            request=request,
            admin_user=SimpleNamespace(id=1),
            db=_BoomDB(),
        )

    exc = excinfo.value
    assert exc.status_code == 500
    assert exc.detail == "Failed to create faction"
    assert "secret-faction-query-should-not-leak" not in str(exc.detail)


@pytest.mark.asyncio
async def test_update_faction_unexpected_is_opaque_500():
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
    assert exc.detail == "Failed to update faction"
    assert secret not in str(exc.detail)


@pytest.mark.asyncio
async def test_update_faction_territory_unexpected_is_opaque_500():
    secret = "secret-faction-territory-should-not-leak"
    faction_id = uuid4()
    with patch.object(af_mod, "FactionService") as svc_cls:
        svc_cls.return_value.update_faction_territory = AsyncMock(
            side_effect=RuntimeError(secret)
        )
        with pytest.raises(HTTPException) as excinfo:
            await update_faction_territory(
                faction_id=faction_id,
                request=TerritoryUpdateRequest(sector_ids=[]),
                admin_user=SimpleNamespace(id=1),
                db=MagicMock(),
            )

    exc = excinfo.value
    assert exc.status_code == 500
    assert exc.detail == "Failed to update faction territory"
    assert secret not in str(exc.detail)


@pytest.mark.asyncio
async def test_update_player_reputation_unexpected_is_opaque_500():
    secret = "secret-faction-reputation-should-not-leak"
    faction_id = uuid4()
    with patch.object(af_mod, "FactionService") as svc_cls:
        svc_cls.return_value.get_faction_by_id = AsyncMock(
            side_effect=RuntimeError(secret)
        )
        with pytest.raises(HTTPException) as excinfo:
            await update_player_reputation(
                faction_id=faction_id,
                request=ReputationUpdateRequest(player_id=str(uuid4()), change=1),
                admin_user=SimpleNamespace(id=1),
                db=MagicMock(),
            )

    exc = excinfo.value
    assert exc.status_code == 500
    assert exc.detail == "Failed to update player reputation"
    assert secret not in str(exc.detail)


def test_admin_factions_http500_catches_have_no_detail_str_e():
    """LEG-3705 — static pin: HTTP 500 catch paths stay opaque."""
    src = Path(af_mod.__file__).read_text(encoding="utf-8")
    for stable in (
        'detail="Failed to list factions"',
        'detail="Failed to create faction"',
        'detail="Failed to update faction"',
        'detail="Failed to update faction territory"',
        'detail="Failed to update player reputation"',
    ):
        assert stable in src
    assert "Failed to list factions: {str(e)}" not in src
    assert "Failed to create faction: {str(e)}" not in src
    assert "Failed to update faction: {str(e)}" not in src
    assert "Failed to update faction territory: {str(e)}" not in src
    assert "Failed to update player reputation: {str(e)}" not in src
