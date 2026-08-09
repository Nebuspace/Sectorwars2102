"""Failed-payment suspension proof (WO-ESCALATE-PAYPAL-FAILED-PAYMENT-NO-SUSPENSION).

Before this WO, paypal_service._handle_payment_failed only logged: a subscriber
whose payment failed kept full access forever. These tests pin the new behavior
-- a consecutive-failure counter, suspension at
``PAYMENT_FAILURE_SUSPEND_THRESHOLD``, and full restoration on the next success
-- and pin the negative half of the ruling: suspension is access denial ONLY,
never cancellation or refund.

Mirrors test_paypal_gc_lapse.py's AsyncMock/SimpleNamespace house style.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.services import paypal_service as svc

THRESHOLD = svc.PAYMENT_FAILURE_SUSPEND_THRESHOLD


def _select_result(scalar_value):
    result = AsyncMock()
    result.scalar_one_or_none = lambda: scalar_value
    return result


def _citizen(failures=0, status="active", is_citizen=True):
    return SimpleNamespace(
        id="player-1",
        is_galactic_citizen=is_citizen,
        gc_lapsed_at=None,
        gc_relocation_used_at=None,
        user=SimpleNamespace(
            paypal_subscription_id="sub-abc",
            subscription_status=status,
            subscription_expires_at=None,
            payment_failure_count=failures,
        ),
    )


def _citizen_session(player, calls):
    """Session whose Region lookup misses and Player lookup hits, `calls` times."""
    session = AsyncMock()
    session.execute.side_effect = [
        r for _ in range(calls) for r in (_select_result(None), _select_result(player))
    ]
    return session


async def _fail(player, times=1):
    session = _citizen_session(player, times)
    inst = SimpleNamespace()
    for _ in range(times):
        await svc.PayPalService._handle_payment_failed(
            inst, session, {"billing_agreement_id": "sub-abc"},
        )
    return session


@pytest.mark.asyncio
async def test_single_failure_increments_counter_without_suspending():
    player = _citizen()
    await _fail(player)

    assert player.user.payment_failure_count == 1
    assert player.is_galactic_citizen is True, "one failure must not deny access"
    assert player.user.subscription_status == "active"


@pytest.mark.asyncio
async def test_below_threshold_never_suspends():
    player = _citizen()
    await _fail(player, times=THRESHOLD - 1)

    assert player.user.payment_failure_count == THRESHOLD - 1
    assert player.is_galactic_citizen is True
    assert player.user.subscription_status == "active"


@pytest.mark.asyncio
async def test_threshold_failure_suspends_access():
    player = _citizen()
    await _fail(player, times=THRESHOLD)

    assert player.user.payment_failure_count == THRESHOLD
    assert player.is_galactic_citizen is False, "access must be denied at threshold"
    assert player.user.subscription_status == "suspended"


@pytest.mark.asyncio
async def test_null_legacy_counter_coerces_to_zero():
    """Rows predating the additive migration read NULL, not 0."""
    player = _citizen(failures=None)
    await _fail(player)

    assert player.user.payment_failure_count == 1


@pytest.mark.asyncio
async def test_success_after_suspension_resets_counter_and_restores_access():
    player = _citizen(failures=THRESHOLD, status="suspended", is_citizen=False)
    session = _citizen_session(player, 1)

    inst = svc.PayPalService.__new__(svc.PayPalService)
    await svc.PayPalService._handle_payment_completed(
        inst, session, {"billing_agreement_id": "sub-abc", "amount": {"total": "5.00"}},
    )

    assert player.user.payment_failure_count == 0, "counter must reset on success"
    assert player.is_galactic_citizen is True, "access must be restored on success"
    assert player.user.subscription_status == "active"


@pytest.mark.asyncio
async def test_renewal_resets_failure_counter():
    player = _citizen(failures=THRESHOLD - 1)
    session = AsyncMock()
    session.execute.return_value = _select_result(player)

    inst = svc.PayPalService.__new__(svc.PayPalService)
    await svc.PayPalService._handle_subscription_renewed(
        inst, session, {"id": "sub-abc", "billing_info": {}},
    )

    assert player.user.payment_failure_count == 0
    assert player.is_galactic_citizen is True


@pytest.mark.asyncio
async def test_recovered_streak_does_not_carry_into_later_failures():
    """A success mid-streak resets, so the next failure starts from 1 -- the
    counter is CONSECUTIVE failures, not lifetime failures."""
    player = _citizen()
    await _fail(player, times=THRESHOLD - 1)

    inst = svc.PayPalService.__new__(svc.PayPalService)
    await svc.PayPalService._handle_payment_completed(
        inst, _citizen_session(player, 1),
        {"billing_agreement_id": "sub-abc", "amount": {"total": "5.00"}},
    )
    await _fail(player)

    assert player.user.payment_failure_count == 1
    assert player.is_galactic_citizen is True


@pytest.mark.asyncio
async def test_region_subscription_suspends_and_restores():
    region = SimpleNamespace(
        name="frontier_alpha", status="active",
        subscription_status="active", payment_failure_count=0,
        paypal_subscription_id="sub-region",
    )
    session = AsyncMock()
    session.execute.return_value = _select_result(region)

    inst = SimpleNamespace()
    for _ in range(THRESHOLD):
        await svc.PayPalService._handle_payment_failed(
            inst, session, {"billing_agreement_id": "sub-region"},
        )

    assert region.payment_failure_count == THRESHOLD
    assert region.status == "suspended"

    real = svc.PayPalService.__new__(svc.PayPalService)
    await svc.PayPalService._handle_payment_completed(
        real, session, {"billing_agreement_id": "sub-region", "amount": {"total": "25.00"}},
    )

    assert region.payment_failure_count == 0
    assert region.status == "active"
    assert region.subscription_status == "active"


@pytest.mark.asyncio
async def test_suspension_never_cancels_or_refunds():
    """The ruling forbids auto-cancellation and auto-refund: the failure path
    must make ZERO PayPal API calls (no cancel/suspend/refund endpoint hits)."""
    player = _citizen()
    session = _citizen_session(player, THRESHOLD)

    inst = SimpleNamespace()
    inst._make_request = AsyncMock(side_effect=AssertionError("no PayPal API call allowed"))
    inst.cancel_subscription = AsyncMock(side_effect=AssertionError("no cancellation allowed"))
    inst.suspend_subscription = AsyncMock(side_effect=AssertionError("no provider suspend allowed"))

    for _ in range(THRESHOLD):
        await svc.PayPalService._handle_payment_failed(
            inst, session, {"billing_agreement_id": "sub-abc"},
        )

    assert player.is_galactic_citizen is False
    inst._make_request.assert_not_awaited()
    inst.cancel_subscription.assert_not_awaited()
    inst.suspend_subscription.assert_not_awaited()
    # And the subscription id itself is left intact -- nothing detached the sub.
    assert player.user.paypal_subscription_id == "sub-abc"


@pytest.mark.asyncio
async def test_unknown_subscription_id_is_a_noop():
    session = AsyncMock()
    session.execute.side_effect = [_select_result(None), _select_result(None)]

    await svc.PayPalService._handle_payment_failed(
        SimpleNamespace(), session, {"billing_agreement_id": "sub-nobody"},
    )
    assert session.execute.await_count == 2


@pytest.mark.asyncio
async def test_missing_subscription_id_is_a_noop():
    session = AsyncMock()
    await svc.PayPalService._handle_payment_failed(SimpleNamespace(), session, {})
    session.execute.assert_not_awaited()
