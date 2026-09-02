"""LEG-3831 — gc_lapse.py emergency relocation must not echo Exception text on 500s.

Mirrors LEG-3817 expeditions / LEG-3829 regional_governance opaque densify family.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

from src.api.routes import gc_lapse as gc_lapse_mod
from src.api.routes.gc_lapse import (
    GCEmergencyRelocationRequest,
    gc_emergency_relocation,
)


def _player():
    return SimpleNamespace(id=uuid.uuid4())


@pytest.mark.asyncio
async def test_gc_emergency_relocation_unexpected_is_opaque_500():
    secret = "secret-emergency-relocation-should-not-leak"
    request = GCEmergencyRelocationRequest(asset_type="planet", asset_id=uuid.uuid4())
    db = MagicMock()

    with patch.object(
        gc_lapse_mod.gc_lapse_service,
        "emergency_relocate",
        side_effect=RuntimeError(secret),
    ):
        with pytest.raises(HTTPException) as excinfo:
            await gc_emergency_relocation(request=request, player=_player(), db=db)

    exc = excinfo.value
    assert exc.status_code == 500
    assert exc.detail == "Failed to perform emergency relocation"
    assert secret not in str(exc.detail)
    db.rollback.assert_called_once()


def test_gc_lapse_emergency_relocation_http500_is_opaque():
    """LEG-3831 — static pin: emergency relocation 500 detail stays opaque."""
    src = Path(gc_lapse_mod.__file__).read_text(encoding="utf-8")
    assert 'detail="Failed to perform emergency relocation"' in src
    assert "Failed to perform emergency relocation: {str(e)}" not in src
    block = src.split("async def gc_emergency_relocation")[1]
    assert "except Exception:\n        db.rollback()\n        raise\n" not in block
