"""LEG-3831 — refining.py HTTP 500 catches must not echo Exception text.

Mirrors LEG-3817 expeditions / LEG-3829 regional_governance opaque densify family.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

from src.api.routes import refining as refining_mod
from src.api.routes.refining import (
    refine_crystal,
    refine_lumen_collect,
    refine_lumen_start,
)


def _player():
    return SimpleNamespace(id=uuid.uuid4())


@pytest.mark.asyncio
async def test_refine_crystal_unexpected_is_opaque_500():
    secret = "secret-refine-crystal-should-not-leak"
    db = MagicMock()

    with patch.object(
        refining_mod.refining_service,
        "refine",
        side_effect=RuntimeError(secret),
    ):
        with pytest.raises(HTTPException) as excinfo:
            await refine_crystal(request=None, player=_player(), db=db)

    exc = excinfo.value
    assert exc.status_code == 500
    assert exc.detail == "Failed to refine crystal"
    assert secret not in str(exc.detail)
    db.rollback.assert_called_once()


@pytest.mark.asyncio
async def test_refine_lumen_start_unexpected_is_opaque_500():
    secret = "secret-refine-lumen-start-should-not-leak"
    db = MagicMock()

    with patch.object(
        refining_mod.refining_service,
        "start_lumen_refine",
        side_effect=RuntimeError(secret),
    ):
        with pytest.raises(HTTPException) as excinfo:
            await refine_lumen_start(request=None, player=_player(), db=db)

    exc = excinfo.value
    assert exc.status_code == 500
    assert exc.detail == "Failed to start lumen refine"
    assert secret not in str(exc.detail)
    db.rollback.assert_called_once()


@pytest.mark.asyncio
async def test_refine_lumen_collect_unexpected_is_opaque_500():
    secret = "secret-refine-lumen-collect-should-not-leak"
    db = MagicMock()

    with patch.object(
        refining_mod.refining_service,
        "collect_lumen_refine",
        side_effect=RuntimeError(secret),
    ):
        with pytest.raises(HTTPException) as excinfo:
            await refine_lumen_collect(request=None, player=_player(), db=db)

    exc = excinfo.value
    assert exc.status_code == 500
    assert exc.detail == "Failed to collect lumen refine"
    assert secret not in str(exc.detail)
    db.rollback.assert_called_once()


def test_refining_http500_catches_have_no_bare_reraise():
    """LEG-3831 — static pin: refine handlers use opaque HTTPException on unexpected errors."""
    src = Path(refining_mod.__file__).read_text(encoding="utf-8")
    for stable in (
        'detail="Failed to refine crystal"',
        'detail="Failed to start lumen refine"',
        'detail="Failed to collect lumen refine"',
    ):
        assert stable in src
    assert "Failed to refine crystal: {str(e)}" not in src
    assert "Failed to start lumen refine: {str(e)}" not in src
    assert "Failed to collect lumen refine: {str(e)}" not in src
    for handler in (
        "async def refine_crystal",
        "async def refine_lumen_start",
        "async def refine_lumen_collect",
    ):
        block = src.split(handler)[1].split("@router.")[0]
        assert "except Exception:\n        db.rollback()\n        raise\n" not in block
