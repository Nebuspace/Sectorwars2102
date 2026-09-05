"""LEG-3686 / LEG-4104 — admin_formations place_gold_bubble HTTP 500 must not echo Exception text.

LEG-4104 densified the catch to structured route_internal_error; expectations
match that shape (same class of update as planets opaque_500 after colonization densify).
"""

from __future__ import annotations

import uuid
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

from src.api.routes import admin_formations as af_mod
from src.api.routes.admin_formations import PlaceGoldBubbleRequest, place_gold_bubble_route
from src.services.special_formation_service import GOLD_BUBBLE_INTERIOR_SIZE_MIN


@contextmanager
def _noop_admin_action_attempt(*_args, **_kwargs):
    yield MagicMock()


@pytest.mark.asyncio
async def test_place_gold_bubble_unexpected_is_opaque_500():
    """place_gold_bubble catch must not echo raw Exception text."""
    secret = "secret-gold-bubble-should-not-leak"
    region_id = uuid.uuid4()
    gateways = [uuid.uuid4()]
    interior = [uuid.uuid4() for _ in range(GOLD_BUBBLE_INTERIOR_SIZE_MIN)]
    body = PlaceGoldBubbleRequest(
        gateway_sector_ids=gateways,
        interior_sector_ids=interior,
        isolate_warps=False,
    )

    # Module alias `place_gold_bubble` is the async route; force sync MagicMock
    # so patch does not auto-AsyncMock and swallow the boom path.
    place_svc = MagicMock(side_effect=RuntimeError(secret))
    with patch.object(af_mod, "admin_action_attempt", _noop_admin_action_attempt):
        with patch.object(af_mod, "place_gold_bubble", new=place_svc):
            with pytest.raises(HTTPException) as excinfo:
                await place_gold_bubble_route(
                    region_id=region_id,
                    body=body,
                    current_admin=SimpleNamespace(id=uuid.uuid4()),
                    db=MagicMock(),
                )

    exc = excinfo.value
    assert exc.status_code == 500
    assert exc.detail == {
        "error_code": "ERR_ADMIN_FORMATIONS_GOLD_BUBBLE_PLACE_FAILED",
        "detail": "Failed to place Gold Bubble",
    }
    assert secret not in str(exc.detail)


def test_admin_formations_place_gold_bubble_http500_is_opaque():
    """LEG-3686/LEG-4104 — static pin: place_gold_bubble 500 is structured opaque."""
    src = Path(af_mod.__file__).read_text(encoding="utf-8")
    assert "ERR_ADMIN_FORMATIONS_GOLD_BUBBLE_PLACE_FAILED" in src
    assert "route_internal_error" in src
    assert 'detail="Failed to place Gold Bubble"' not in src
    assert 'detail=f"Failed to place Gold Bubble: {e}"' not in src
