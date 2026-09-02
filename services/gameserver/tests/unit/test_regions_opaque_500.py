"""LEG-3796 / LEG-4044 — regions.py takeover HTTP 500 catches must not echo Exception text.

Structured densify (LEG-4044) keeps opacity while adding {error_code, detail}.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from src.api.routes import regions as regions_mod
from src.api.routes.regions import RegionTakeoverRequest, takeover_region


@pytest.mark.asyncio
async def test_takeover_region_execute_takeover_boom_is_opaque_500():
    """Outer takeover_region catch must not echo raw execute_takeover Exception text."""
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


@pytest.mark.asyncio
async def test_takeover_region_commit_failure_is_opaque_500():
    """db.commit failure must not echo raw Exception text."""
    secret = "secret-region-takeover-commit-should-not-leak"
    region_id = uuid.uuid4()
    current_user = SimpleNamespace(id=uuid.uuid4())
    db = AsyncMock()
    db.commit = AsyncMock(side_effect=RuntimeError(secret))

    with patch.object(
        regions_mod,
        "execute_takeover",
        new=AsyncMock(
            return_value={
                "ok": True,
                "takeover_intent": {"id": str(uuid.uuid4())},
            }
        ),
    ):
        with pytest.raises(HTTPException) as excinfo:
            await takeover_region(
                region_id=region_id,
                body=None,
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


def test_regions_takeover_http500_catches_have_no_detail_str_e():
    """LEG-3796/4044 — static pin: takeover 500 detail stays opaque + structured."""
    src = Path(regions_mod.__file__).read_text(encoding="utf-8")
    assert "ERR_REGIONS_TAKEOVER_BEGIN_FAILED" in src
    assert "route_internal_error" in src
    assert "Failed to begin region takeover: {str(e)}" not in src
