"""LEG-3688 — admin_beacons HTTP 500 catches must not echo Exception text.

Mirrors LEG-3570 admin_colonization opaque densify.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException

from src.api.routes import admin_beacons as ab_mod
from src.api.routes.admin_beacons import clear_beacon_flag, get_flagged_beacons


@pytest.mark.asyncio
async def test_get_flagged_beacons_unexpected_is_opaque_500():
    """LEG-3688 — flagged list catch must not echo raw Exception text."""
    secret = "secret-flagged-beacons-should-not-leak"

    with patch.object(ab_mod.message_beacon_service, "list_flagged_beacons") as list_svc:
        list_svc.side_effect = RuntimeError(secret)
        with pytest.raises(HTTPException) as excinfo:
            await get_flagged_beacons(
                page=1,
                admin=SimpleNamespace(),
                db=MagicMock(),
            )

    exc = excinfo.value
    assert exc.status_code == 500
    assert exc.detail == "Failed to fetch flagged beacons"
    assert secret not in str(exc.detail)


@pytest.mark.asyncio
async def test_clear_beacon_flag_unexpected_is_opaque_500():
    """LEG-3688 — clear-flag catch must not echo raw Exception text."""
    secret = "secret-clear-flag-should-not-leak"
    beacon_id = uuid4()
    db = MagicMock()

    with patch.object(ab_mod.message_beacon_service, "clear_flag") as clear_svc:
        clear_svc.side_effect = RuntimeError(secret)
        with pytest.raises(HTTPException) as excinfo:
            await clear_beacon_flag(
                beacon_id=beacon_id,
                admin=SimpleNamespace(),
                db=db,
            )

    exc = excinfo.value
    assert exc.status_code == 500
    assert exc.detail == "Failed to clear beacon flag"
    assert secret not in str(exc.detail)
    db.rollback.assert_called_once()


def test_admin_beacons_http500_catches_have_no_detail_str_e():
    """LEG-3688 — static pin: beacon moderation 500 details stay opaque."""
    src = Path(ab_mod.__file__).read_text(encoding="utf-8")
    for stable in (
        'detail="Failed to fetch flagged beacons"',
        'detail="Failed to clear beacon flag"',
        'detail="Failed to confirm beacon abuse"',
    ):
        assert stable in src
    assert "Failed to fetch flagged beacons: {str(e)}" not in src
    assert "Failed to clear beacon flag: {str(e)}" not in src
    assert "Failed to confirm beacon abuse: {str(e)}" not in src
