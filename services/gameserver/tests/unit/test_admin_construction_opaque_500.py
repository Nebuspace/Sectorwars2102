"""LEG-3694 — admin_construction list_tradedocks HTTP 500 must not echo Exception text.

Mirrors LEG-3570 admin_colonization opaque densify.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi import HTTPException

from src.api.routes import admin_construction as ac_mod
from src.api.routes.admin_construction import list_tradedocks


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
    assert exc.detail == "Failed to list tradedocks"
    assert secret not in str(exc.detail)


def test_admin_construction_list_tradedocks_http500_is_opaque():
    """LEG-3694 — static pin: list_tradedocks 500 detail stays opaque."""
    src = Path(ac_mod.__file__).read_text(encoding="utf-8")
    assert 'detail="Failed to list tradedocks"' in src
    assert 'detail=f"Failed to list tradedocks: {e}"' not in src
    assert "Failed to list tradedocks: {str(e)}" not in src
