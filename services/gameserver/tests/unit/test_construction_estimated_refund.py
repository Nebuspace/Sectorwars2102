"""WO-API-PHASE1 Lane B (B7) -- construction_service.status_payload's
estimated_refund advisory field.

The server exposes the authoritative cancel-refund formula (cancel_refund())
as a READ-ONLY advisory field on the reservation status payload so the
client no longer hand-rolls its own 0.7/0.5 formula. cancel() remains the
sole authoritative writer at commit time -- this field is DRY (same fn, same
inputs) and purely informational.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

from src.services import construction_service as cs


_NOW = datetime(2102, 6, 1, 12, 0, 0, tzinfo=UTC)
SCOUT_COST = cs.SHIP_BUILD_SPECS["SCOUT_SHIP"]["total_cost"]  # 40,000


class _NoQuerySession:
    """status_payload() must be read-only for a non-'queued' reservation --
    any db.query() call here would mean it started touching rows it doesn't
    need for this state, so we fail loud rather than fake a query result."""

    def __init__(self) -> None:
        self.flush_calls = 0
        self.commit_calls = 0

    def query(self, *args: Any, **kwargs: Any) -> Any:
        raise AssertionError("status_payload() queried the DB for a non-queued reservation")

    def flush(self) -> None:
        self.flush_calls += 1

    def commit(self) -> None:
        self.commit_calls += 1


def _reservation(**overrides: Any) -> SimpleNamespace:
    """hold_active by default -- the one state status_payload can compute
    with zero DB queries (no queue-position lookup, no phase/rent block)."""
    deposit = cs.milestone_amounts(SCOUT_COST)["deposit"]
    keel = cs.milestone_amounts(SCOUT_COST)["keel_laid"]
    base = dict(
        id=uuid.uuid4(), station_id=uuid.uuid4(), ship_type="SCOUT_SHIP", ship_name=None,
        state="hold_active", total_cost=SCOUT_COST,
        deposit_paid=deposit, credits_paid=deposit + keel,
        queue_bonus_credit=0, priority_bumps_count=0, uses_specialized_slip=False,
        milestones={"deposit": True, "keel_laid": True, "hull_complete": False, "final": False},
        resources_required={}, resources_delivered={},
        created_at=_NOW - timedelta(hours=1), updated_at=_NOW - timedelta(hours=1),
        phase_deadline=None, hold_expires_at=_NOW + timedelta(hours=24), claim_expires_at=None,
        rent_paid_until=None,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


class TestEstimatedRefundField:
    def test_matches_cancel_refund_before_hull_complete(self):
        credits_paid = 15_000
        reservation = _reservation(credits_paid=credits_paid, milestones={
            "deposit": True, "keel_laid": True, "hull_complete": False, "final": False,
        })
        db = _NoQuerySession()

        payload = cs.status_payload(db, reservation, now=_NOW)

        assert payload["estimated_refund"] == cs.cancel_refund(credits_paid, False)
        assert payload["estimated_refund"] == int(credits_paid * 0.5)

    def test_matches_cancel_refund_after_hull_complete(self):
        credits_paid = 30_000
        reservation = _reservation(credits_paid=credits_paid, milestones={
            "deposit": True, "keel_laid": True, "hull_complete": True, "final": False,
        })
        db = _NoQuerySession()

        payload = cs.status_payload(db, reservation, now=_NOW)

        assert payload["estimated_refund"] == cs.cancel_refund(credits_paid, True)
        assert payload["estimated_refund"] == int(credits_paid * 0.7)

    def test_dry_matches_actual_cancel_refund_for_the_same_reservation(self):
        """The advisory value on the READ payload must equal what cancel()
        would actually pay out for this exact reservation -- same fn, same
        inputs, no parallel formula."""
        reservation = _reservation(credits_paid=22_500, milestones={
            "deposit": True, "keel_laid": True, "hull_complete": True, "final": False,
        })
        db = _NoQuerySession()

        payload = cs.status_payload(db, reservation, now=_NOW)
        authoritative = cs.cancel_refund(
            reservation.credits_paid, bool((reservation.milestones or {}).get("hull_complete"))
        )

        assert payload["estimated_refund"] == authoritative

    def test_defaults_to_zero_when_nothing_paid(self):
        reservation = _reservation(credits_paid=0, milestones={
            "deposit": False, "keel_laid": False, "hull_complete": False, "final": False,
        })
        db = _NoQuerySession()

        payload = cs.status_payload(db, reservation, now=_NOW)

        assert payload["estimated_refund"] == 0

    def test_none_credits_paid_treated_as_zero(self):
        """Guard against a None credits_paid the same way cancel() does via
        `reservation.credits_paid or 0` -- a fresh reservation with nothing
        recorded yet must not raise a TypeError computing the advisory."""
        reservation = _reservation(credits_paid=None)
        db = _NoQuerySession()

        payload = cs.status_payload(db, reservation, now=_NOW)

        assert payload["estimated_refund"] == 0

    def test_read_only_no_query_no_flush_no_commit_no_mutation(self):
        reservation = _reservation()
        before = dict(vars(reservation))
        db = _NoQuerySession()

        cs.status_payload(db, reservation, now=_NOW)

        assert db.flush_calls == 0
        assert db.commit_calls == 0
        # Every attribute on the reservation is byte-identical after the call
        # (status_payload's db.query() branches, if any fired, would have
        # raised via _NoQuerySession -- this is the belt to that suspenders).
        assert dict(vars(reservation)) == before
