"""ADR-0054 X-D3 -- GC-lapse 7-day liquidation window webhook proof.

Proves the exact behavior change in paypal_service._handle_subscription_
cancelled (instant is_galactic_citizen=False -> gc_lapsed_at stamp, citizen
flag left True through the grace) plus the re-subscription/renewal paths that
clear the lapse anchor + renew the one-time emergency-relocation grant.
Mirrors test_paypal_region_lock.py's AsyncMock/SimpleNamespace house style.
"""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.services import paypal_service as svc


def _select_result(scalar_value):
    result = AsyncMock()
    result.scalar_one_or_none = lambda: scalar_value
    return result


@pytest.mark.asyncio
async def test_cancellation_stamps_gc_lapsed_at_not_instant_flip():
    """The cancellation handler must NOT instantly flip is_galactic_citizen
    False -- it stamps gc_lapsed_at and leaves the flag alone (True stays
    True through the 7-day grace)."""
    player = SimpleNamespace(
        id="player-1",
        is_galactic_citizen=True,
        gc_lapsed_at=None,
        user=SimpleNamespace(paypal_subscription_id="sub-abc"),
    )
    session = AsyncMock()
    # First execute() = the Region lookup (no region match), second = the
    # Player/User join lookup (matches this player).
    session.execute.side_effect = [_select_result(None), _select_result(player)]

    svc_instance = SimpleNamespace()
    await svc.PayPalService._handle_subscription_cancelled(
        svc_instance, session, {"id": "sub-abc"},
    )

    assert player.is_galactic_citizen is True, (
        "is_galactic_citizen must stay True through the 7-day grace window"
    )
    assert player.gc_lapsed_at is not None, "gc_lapsed_at must be stamped"
    assert player.user.paypal_subscription_id is None


@pytest.mark.asyncio
async def test_reactivation_clears_gc_lapsed_at_and_relocation_grant():
    """Re-subscription (a fresh subscription_id) must clear any in-flight
    lapse window and renew the one-time emergency-relocation grant."""
    player = SimpleNamespace(
        id="player-1",
        is_galactic_citizen=False,
        gc_lapsed_at=datetime(2026, 8, 1, tzinfo=timezone.utc),
        gc_relocation_used_at=datetime(2026, 8, 2, tzinfo=timezone.utc),
        user=SimpleNamespace(
            paypal_subscription_id=None,
            subscription_tier=None,
            subscription_status=None,
            subscription_started_at=None,
            subscription_expires_at=None,
        ),
    )
    session = AsyncMock()
    session.execute.return_value = _select_result(player)

    # _next_expiry() is a real instance method (not touched by this WO) --
    # needs a real PayPalService, not a bare SimpleNamespace stand-in.
    svc_instance = svc.PayPalService.__new__(svc.PayPalService)
    await svc.PayPalService._activate_galactic_citizenship(
        svc_instance, session, "user-1", "sub-new", {"billing_info": {}},
    )

    assert player.is_galactic_citizen is True
    assert player.gc_lapsed_at is None
    assert player.gc_relocation_used_at is None


@pytest.mark.asyncio
async def test_renewal_clears_gc_lapsed_at_and_relocation_grant():
    """A recurring-payment renewal (same subscription_id) must also clear an
    in-flight lapse window -- a player who lapses, then pays before the 7
    days run out via a normal renewal event (not a fresh subscription),
    still recovers."""
    player = SimpleNamespace(
        id="player-1",
        is_galactic_citizen=False,
        gc_lapsed_at=datetime(2026, 8, 1, tzinfo=timezone.utc),
        gc_relocation_used_at=datetime(2026, 8, 2, tzinfo=timezone.utc),
        user=SimpleNamespace(subscription_status=None, subscription_expires_at=None),
    )
    session = AsyncMock()
    session.execute.return_value = _select_result(player)

    svc_instance = svc.PayPalService.__new__(svc.PayPalService)
    await svc.PayPalService._handle_subscription_renewed(
        svc_instance, session, {"id": "sub-abc", "billing_info": {}},
    )

    assert player.is_galactic_citizen is True
    assert player.gc_lapsed_at is None
    assert player.gc_relocation_used_at is None


@pytest.mark.asyncio
async def test_cancellation_of_region_subscription_untouched_by_gc_lapse():
    """A region-owner subscription cancellation must still hit the region
    branch, entirely unaffected by the GC-lapse change (it's a totally
    different `if region:` branch)."""
    region = SimpleNamespace(status="active", paypal_subscription_id="sub-region", name="frontier_alpha")
    session = AsyncMock()
    session.execute.return_value = _select_result(region)

    svc_instance = SimpleNamespace()
    await svc.PayPalService._handle_subscription_cancelled(
        svc_instance, session, {"id": "sub-region"},
    )

    assert region.status == "suspended"
    assert region.paypal_subscription_id is None
    # Only one execute() call (the region lookup) -- the player branch never runs.
    assert session.execute.await_count == 1
