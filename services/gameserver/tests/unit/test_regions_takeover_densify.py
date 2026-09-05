"""LEG-4044 — regions takeover routes return structured 500s."""

from __future__ import annotations

import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from src.api.routes import regions as regions_mod
from src.api.routes.regions import (
    RegionTakeoverRequest,
    list_takeover_eligible_regions_route,
    takeover_region,
)


@pytest.mark.asyncio
async def test_list_takeover_eligible_boom_returns_structured_500():
    secret = "secret-eligible-list-should-not-leak"
    current_user = SimpleNamespace(id=uuid.uuid4())

    with patch.object(
        regions_mod,
        "list_takeover_eligible_regions",
        new=AsyncMock(side_effect=RuntimeError(secret)),
    ):
        with pytest.raises(HTTPException) as excinfo:
            await list_takeover_eligible_regions_route(
                current_user=current_user,
                db=MagicMock(),
            )

    exc = excinfo.value
    assert exc.status_code == 500
    assert exc.detail == {
        "error_code": "ERR_REGIONS_TAKEOVER_ELIGIBLE_LIST_FAILED",
        "detail": "Failed to list takeover-eligible regions",
    }
    assert secret not in str(exc.detail)


@pytest.mark.asyncio
async def test_takeover_region_boom_returns_structured_500():
    secret = "secret-region-takeover-should-not-leak"
    region_id = uuid.uuid4()
    current_user = SimpleNamespace(id=uuid.uuid4())
    db = AsyncMock()

    with patch.object(
        regions_mod,
        "execute_takeover",
        new=AsyncMock(side_effect=RuntimeError(secret)),
    ):
        with pytest.raises(HTTPException) as excinfo:
            await takeover_region(
                region_id=region_id,
                body=RegionTakeoverRequest(),
                current_user=current_user,
                db=db,
            )

    exc = excinfo.value
    assert exc.status_code == 500
    assert exc.detail == {
        "error_code": "ERR_REGIONS_TAKEOVER_BEGIN_FAILED",
        "detail": "Failed to begin region takeover",
    }
    assert secret not in str(exc.detail)


def test_regions_takeover_http500_is_structured():
    """LEG-4044 — static pin: takeover route 500 details are structured."""
    src = Path(regions_mod.__file__).read_text(encoding="utf-8")
    for code in (
        "ERR_REGIONS_TAKEOVER_ELIGIBLE_LIST_FAILED",
        "ERR_REGIONS_TAKEOVER_BEGIN_FAILED",
    ):
        assert code in src
    assert "route_internal_error" in src
    assert 'detail="Failed to list takeover-eligible regions"' not in src
    assert 'detail="Failed to begin region takeover"' not in src
