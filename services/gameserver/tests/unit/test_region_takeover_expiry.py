"""LEG-3791: region takeover slice 4 — TakeoverIntent expiry sweep."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.models.takeover_intent import TakeoverIntent, TakeoverIntentStatus
from src.services.region_lifecycle_service import (
    commit_takeover,
    sweep_expired_takeover_intents,
)


_FROZEN_NOW = datetime(2026, 9, 2, 12, 0, 0, tzinfo=timezone.utc)


def _intent(
    *,
    status: str = TakeoverIntentStatus.PENDING.value,
    expires_at: datetime,
) -> TakeoverIntent:
    return TakeoverIntent(
        id=uuid.uuid4(),
        region_id=uuid.uuid4(),
        caller_user_id=uuid.uuid4(),
        approval_url="https://paypal.example/approve",
        status=status,
        expires_at=expires_at,
    )


class _FakeSession:
    def __init__(self, intents: list[TakeoverIntent], *, now: datetime) -> None:
        self._intents = intents
        self._now = now
        self.flushed = False

    def query(self, model):
        assert model is TakeoverIntent
        outer = self

        class _Query:
            def filter(self, *args):
                return self

            def all(self):
                return [
                    intent
                    for intent in outer._intents
                    if intent.status == TakeoverIntentStatus.PENDING.value
                    and intent.expires_at < outer._now
                ]

        return _Query()

    def flush(self) -> None:
        self.flushed = True


class TestSweepExpiredTakeoverIntents:
    def test_marks_overdue_pending_intents_expired(self):
        overdue = _intent(expires_at=_FROZEN_NOW - timedelta(minutes=5))
        future = _intent(expires_at=_FROZEN_NOW + timedelta(hours=1))
        lost = _intent(
            status=TakeoverIntentStatus.LOST.value,
            expires_at=_FROZEN_NOW - timedelta(hours=2),
        )
        db = _FakeSession([overdue, future, lost], now=_FROZEN_NOW)

        result = sweep_expired_takeover_intents(db, now=_FROZEN_NOW)

        assert result == {"expired": 1}
        assert overdue.status == TakeoverIntentStatus.EXPIRED.value
        assert overdue.completed_at == _FROZEN_NOW
        assert future.status == TakeoverIntentStatus.PENDING.value
        assert future.completed_at is None
        assert lost.status == TakeoverIntentStatus.LOST.value
        assert db.flushed is True

    def test_multiple_overdue_rows_all_expired(self):
        intents = [
            _intent(expires_at=_FROZEN_NOW - timedelta(hours=2)),
            _intent(expires_at=_FROZEN_NOW - timedelta(minutes=1)),
        ]
        db = _FakeSession(intents, now=_FROZEN_NOW)

        result = sweep_expired_takeover_intents(db, now=_FROZEN_NOW)

        assert result == {"expired": 2}
        assert all(i.status == TakeoverIntentStatus.EXPIRED.value for i in intents)
        assert all(i.completed_at == _FROZEN_NOW for i in intents)

    def test_no_overdue_rows_returns_zero(self):
        db = _FakeSession(
            [_intent(expires_at=_FROZEN_NOW + timedelta(minutes=30))],
            now=_FROZEN_NOW,
        )

        result = sweep_expired_takeover_intents(db, now=_FROZEN_NOW)

        assert result == {"expired": 0}
        assert db.flushed is False


class TestCommitTakeoverExpiredIntent:
    @pytest.mark.asyncio
    async def test_late_webhook_on_expired_intent_refunds(self):
        intent_id = uuid.uuid4()
        expired_intent = _intent(
            status=TakeoverIntentStatus.EXPIRED.value,
            expires_at=_FROZEN_NOW - timedelta(hours=2),
        )
        expired_intent.id = intent_id

        db = AsyncMock()
        db.execute = AsyncMock(
            side_effect=[
                SimpleNamespace(scalar_one_or_none=lambda: expired_intent),
            ]
        )

        refund_mock = AsyncMock()
        with patch(
            "src.services.region_lifecycle_service.paypal_service.refund_subscription",
            refund_mock,
        ):
            result = await commit_takeover(
                db,
                takeover_intent_id=intent_id,
                paypal_subscription_id="sub-late",
            )

        assert result["ok"] is False
        assert result["code"] == "ERR_TAKEOVER_INTENT_EXPIRED"
        assert expired_intent.status == TakeoverIntentStatus.EXPIRED.value
        refund_mock.assert_awaited_once_with("sub-late")
