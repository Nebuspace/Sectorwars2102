"""Unit tests for the construction-event RNG wiring (WO-WIRE-CONSTRUCTION-EVENTS).

`_roll_construction_events` (construction_service.py) is the accrual-anchor
engine that catches roll_construction_event/apply_construction_event up to
`now`, once per elapsed canonical project-day -- previously implemented but
never called from _advance_station's phase-progression loop. These tests
cover the NEW cadence/anchor bookkeeping (day counting, partial-day
remainder, the catch-up cap) with a scripted fake RNG standing in for the
already-implemented event-type distribution, which is out of this WO's
scope. No DB session is used -- ConstructionReservation() is instantiated
directly (a real SQLAlchemy-mapped object, required because
flag_modified() needs `_sa_instance_state`, which a bare SimpleNamespace
lacks).
"""
from datetime import datetime, timedelta, UTC
from types import SimpleNamespace

import pytest

from src.core import game_time
from src.models.construction import ConstructionReservation
from src.services import construction_service as cs


FIXED_NOW = datetime(2102, 6, 1, 12, 0, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def real_time_scale(monkeypatch):
    monkeypatch.setattr(game_time, "GAME_TIME_SCALE", 1.0)


class FakeRng:
    """Scripted stand-in for random.Random -- deterministic fire/no-fire and,
    when firing, always resolves to a `quality_discovery` (positive/hull)
    event: random() below the fire threshold and below the 0.5 sub-branch
    split, randint() at the low end of the positive bucket, choice() takes
    the first candidate."""

    def __init__(self, fires: bool):
        self._random_value = 0.0 if fires else 0.99

    def random(self):
        return self._random_value

    def randint(self, a, b):
        return a

    def choice(self, seq):
        return seq[0]


def make_reservation(events_last_rolled_at=None):
    res = ConstructionReservation()
    res.total_cost = 40_000
    res.milestones = {}
    res.construction_events = []
    res.pending_events = []
    res.events_last_rolled_at = events_last_rolled_at
    return res


STATION = SimpleNamespace()  # unused by apply_construction_event's body


class TestFirstEligibility:
    def test_null_anchor_seeds_now_without_rolling(self):
        res = make_reservation(events_last_rolled_at=None)
        fired = cs._roll_construction_events(res, STATION, FIXED_NOW, rng=FakeRng(fires=True))
        assert fired == 0
        assert res.events_last_rolled_at == FIXED_NOW
        assert res.construction_events == []


class TestDayAccrual:
    def test_rolls_once_per_whole_elapsed_canonical_day(self):
        anchor = FIXED_NOW - timedelta(days=3)
        res = make_reservation(events_last_rolled_at=anchor)
        fired = cs._roll_construction_events(res, STATION, FIXED_NOW, rng=FakeRng(fires=True))
        assert fired == 3
        assert len(res.construction_events) == 3
        assert all(e["type"] == "quality_discovery" for e in res.construction_events)
        assert res.events_last_rolled_at == anchor + timedelta(days=3)

    def test_less_than_a_day_elapsed_rolls_nothing_and_anchor_unchanged(self):
        anchor = FIXED_NOW - timedelta(hours=23)
        res = make_reservation(events_last_rolled_at=anchor)
        fired = cs._roll_construction_events(res, STATION, FIXED_NOW, rng=FakeRng(fires=True))
        assert fired == 0
        assert res.construction_events == []
        assert res.events_last_rolled_at == anchor

    def test_partial_day_remainder_is_not_lost_or_double_counted(self):
        """1.5 elapsed days -> 1 roll, anchor advances by exactly 1 day (not
        to `now`), leaving the 12h remainder for the next call."""
        anchor = FIXED_NOW - timedelta(days=1, hours=12)
        res = make_reservation(events_last_rolled_at=anchor)
        fired = cs._roll_construction_events(res, STATION, FIXED_NOW, rng=FakeRng(fires=True))
        assert fired == 1
        assert res.events_last_rolled_at == anchor + timedelta(days=1)

        # A second call at the SAME `now`: only 12h have accrued since the
        # new anchor -- must roll nothing and must not touch the anchor again.
        second_anchor = res.events_last_rolled_at
        fired_again = cs._roll_construction_events(res, STATION, FIXED_NOW, rng=FakeRng(fires=True))
        assert fired_again == 0
        assert len(res.construction_events) == 1
        assert res.events_last_rolled_at == second_anchor

    def test_quiet_days_still_advance_the_anchor(self):
        """event_fires_today returning False must still consume the day --
        a quiet reservation must not re-roll the same days forever."""
        anchor = FIXED_NOW - timedelta(days=5)
        res = make_reservation(events_last_rolled_at=anchor)
        fired = cs._roll_construction_events(res, STATION, FIXED_NOW, rng=FakeRng(fires=False))
        assert fired == 0
        assert res.construction_events == []
        assert res.events_last_rolled_at == anchor + timedelta(days=5)


class TestCatchupCap:
    def test_cap_advances_anchor_only_by_the_capped_amount(self):
        """A reservation unread for far longer than the cap must NOT have its
        anchor jump past what was actually rolled -- otherwise the
        uncapped remainder is silently skipped instead of picked up next
        call (the defect this test pins)."""
        far_past_days = cs.EVENT_MAX_CATCHUP_DAYS + 10
        anchor = FIXED_NOW - timedelta(days=far_past_days)
        res = make_reservation(events_last_rolled_at=anchor)
        fired = cs._roll_construction_events(res, STATION, FIXED_NOW, rng=FakeRng(fires=True))
        assert fired == cs.EVENT_MAX_CATCHUP_DAYS
        assert res.events_last_rolled_at == anchor + timedelta(days=cs.EVENT_MAX_CATCHUP_DAYS)
        assert res.events_last_rolled_at < FIXED_NOW

        # The remaining 10 days must still be reachable on a follow-up call.
        fired_again = cs._roll_construction_events(res, STATION, FIXED_NOW, rng=FakeRng(fires=True))
        assert fired_again == 10
        assert res.events_last_rolled_at == FIXED_NOW
        assert len(res.construction_events) == cs.EVENT_MAX_CATCHUP_DAYS + 10


class TestGameTimeScale:
    def test_day_accrual_is_consistent_under_compression(self, monkeypatch):
        """canonical_hours_since (elapsed-day count) and scaled_deadline
        (anchor advance) must agree under a compressed scale -- both derive
        from the same GAME_TIME_SCALE, so 3 canonical days elapsed at any
        scale still yields exactly 3 rolls and a 3-canonical-day anchor
        advance."""
        monkeypatch.setattr(game_time, "GAME_TIME_SCALE", 144.0)
        wall_clock_anchor = FIXED_NOW - timedelta(minutes=30)  # 3 canonical days at scale 144
        res = make_reservation(events_last_rolled_at=wall_clock_anchor)
        fired = cs._roll_construction_events(res, STATION, FIXED_NOW, rng=FakeRng(fires=True))
        assert fired == 3
        assert res.events_last_rolled_at == wall_clock_anchor + timedelta(minutes=30)
