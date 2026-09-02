"""LEG-3775: region takeover slice 3 — PayPal commit_takeover callback."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.models.region import Region, RegionStatus
from src.models.takeover_intent import TakeoverIntent, TakeoverIntentStatus
from src.services.paypal_service import PayPalService
from src.services.region_lifecycle_service import (
    ERR_REGION_NOT_AVAILABLE_FOR_TAKEOVER,
    commit_takeover,
)


def _utc(**kwargs) -> datetime:
    return datetime.now(timezone.utc) + timedelta(**kwargs)


def _region(*, region_id: uuid.UUID, status: str = RegionStatus.SUSPENDED.value) -> Region:
    return Region(
        id=region_id,
        name="frontier_alpha",
        display_name="Frontier Alpha",
        owner_id=uuid.uuid4(),
        status=status,
    )


def _intent(
    *,
    intent_id: uuid.UUID,
    region_id: uuid.UUID,
    caller_id: uuid.UUID,
    status: str = TakeoverIntentStatus.PENDING.value,
) -> TakeoverIntent:
    return TakeoverIntent(
        id=intent_id,
        region_id=region_id,
        caller_user_id=caller_id,
        approval_url="https://paypal.example/approve",
        status=status,
        expires_at=_utc(hours=1),
    )


class TestCommitTakeover:
    @pytest.fixture
    def ids(self):
        return {
            "region": uuid.uuid4(),
            "winner": uuid.uuid4(),
            "loser": uuid.uuid4(),
            "winner_intent": uuid.uuid4(),
            "loser_intent": uuid.uuid4(),
        }

    @pytest.mark.asyncio
    async def test_winner_flips_region_owner_under_lock(self, ids):
        region = _region(region_id=ids["region"])
        winner_intent = _intent(
            intent_id=ids["winner_intent"],
            region_id=ids["region"],
            caller_id=ids["winner"],
        )

        db = AsyncMock()
        db.execute = AsyncMock(
            side_effect=[
                SimpleNamespace(scalar_one_or_none=lambda: winner_intent),  # intent FOR UPDATE
                SimpleNamespace(),  # advisory lock
                SimpleNamespace(scalar_one_or_none=lambda: region),  # region FOR UPDATE
                SimpleNamespace(scalar_one_or_none=lambda: None),  # no prior transferred
                SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: [])),  # losers
            ]
        )

        with patch(
            "src.services.region_lifecycle_service.paypal_service.refund_subscription",
            new=AsyncMock(),
        ):
            result = await commit_takeover(
                db,
                takeover_intent_id=ids["winner_intent"],
                paypal_subscription_id="sub-winner",
            )

        assert result["ok"] is True
        assert region.owner_id == ids["winner"]
        assert region.paypal_subscription_id == "sub-winner"
        assert region.status == RegionStatus.ACTIVE.value
        assert region.suspended_at is None
        assert winner_intent.status == TakeoverIntentStatus.TRANSFERRED.value
        assert winner_intent.completed_at is not None

    @pytest.mark.asyncio
    async def test_loser_refunded_when_region_already_transferred(self, ids):
        region = _region(region_id=ids["region"], status=RegionStatus.ACTIVE.value)
        loser_intent = _intent(
            intent_id=ids["loser_intent"],
            region_id=ids["region"],
            caller_id=ids["loser"],
        )

        db = AsyncMock()
        db.execute = AsyncMock(
            side_effect=[
                SimpleNamespace(scalar_one_or_none=lambda: loser_intent),
                SimpleNamespace(),
                SimpleNamespace(scalar_one_or_none=lambda: region),
                SimpleNamespace(scalar_one_or_none=lambda: ids["winner_intent"]),
            ]
        )

        refund_mock = AsyncMock()
        with patch(
            "src.services.region_lifecycle_service.paypal_service.refund_subscription",
            refund_mock,
        ):
            result = await commit_takeover(
                db,
                takeover_intent_id=ids["loser_intent"],
                paypal_subscription_id="sub-loser",
            )

        assert result["ok"] is False
        assert result["code"] == "ERR_TAKEOVER_LOST"
        assert loser_intent.status == TakeoverIntentStatus.LOST.value
        refund_mock.assert_awaited_once_with("sub-loser")

    @pytest.mark.asyncio
    async def test_region_no_longer_eligible_refunds_and_marks_lost(self, ids):
        region = _region(region_id=ids["region"], status=RegionStatus.TERMINATED.value)
        intent = _intent(
            intent_id=ids["winner_intent"],
            region_id=ids["region"],
            caller_id=ids["winner"],
        )

        db = AsyncMock()
        db.execute = AsyncMock(
            side_effect=[
                SimpleNamespace(scalar_one_or_none=lambda: intent),
                SimpleNamespace(),
                SimpleNamespace(scalar_one_or_none=lambda: region),
                SimpleNamespace(scalar_one_or_none=lambda: None),
            ]
        )

        refund_mock = AsyncMock()
        with patch(
            "src.services.region_lifecycle_service.paypal_service.refund_subscription",
            refund_mock,
        ):
            result = await commit_takeover(
                db,
                takeover_intent_id=ids["winner_intent"],
                paypal_subscription_id="sub-late",
            )

        assert result["ok"] is False
        assert result["code"] == ERR_REGION_NOT_AVAILABLE_FOR_TAKEOVER
        assert intent.status == TakeoverIntentStatus.LOST.value
        refund_mock.assert_awaited_once_with("sub-late")

    @pytest.mark.asyncio
    async def test_concurrent_losers_marked_lost_in_winner_transaction(self, ids):
        region = _region(region_id=ids["region"])
        winner_intent = _intent(
            intent_id=ids["winner_intent"],
            region_id=ids["region"],
            caller_id=ids["winner"],
        )
        loser_intent = _intent(
            intent_id=ids["loser_intent"],
            region_id=ids["region"],
            caller_id=ids["loser"],
        )

        db = AsyncMock()
        db.execute = AsyncMock(
            side_effect=[
                SimpleNamespace(scalar_one_or_none=lambda: winner_intent),
                SimpleNamespace(),
                SimpleNamespace(scalar_one_or_none=lambda: region),
                SimpleNamespace(scalar_one_or_none=lambda: None),
                SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: [loser_intent])),
            ]
        )

        with patch(
            "src.services.region_lifecycle_service.paypal_service.refund_subscription",
            new=AsyncMock(),
        ):
            result = await commit_takeover(
                db,
                takeover_intent_id=ids["winner_intent"],
                paypal_subscription_id="sub-winner",
            )

        assert result["ok"] is True
        assert loser_intent.status == TakeoverIntentStatus.LOST.value
        assert loser_intent.completed_at is not None


class TestPayPalActivatedRouting:
    @pytest.mark.asyncio
    async def test_region_takeover_custom_id_routes_to_commit_takeover(self):
        intent_id = uuid.uuid4()
        session = AsyncMock()
        resource = {
            "id": "sub-activated",
            "custom_id": f"region_takeover_{intent_id}",
        }

        commit_mock = AsyncMock(return_value={"ok": True})
        with patch(
            "src.services.region_lifecycle_service.commit_takeover",
            commit_mock,
        ):
            svc = PayPalService.__new__(PayPalService)
            await PayPalService._handle_subscription_activated(svc, session, resource)

        commit_mock.assert_awaited_once_with(
            session,
            takeover_intent_id=str(intent_id),
            paypal_subscription_id="sub-activated",
        )
