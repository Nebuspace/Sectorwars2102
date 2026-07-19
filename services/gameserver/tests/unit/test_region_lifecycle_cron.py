"""Unit coverage for WO-P8-region-lifecycle-cron: ``advance_to_grace`` /
``advance_to_terminated`` / ``dispatch_terminated_cleanup``
(``region_lifecycle_service.py``) and their Phase-7 day-gate wrapper
(``_run_region_lifecycle_advance_gated`` in ``economy_governance_
sweeps.py``).

DB-free fake session, mirroring ``test_treasury_reconciliation.py``'s
convention exactly: interprets the REAL SQLAlchemy WHERE-clause / bulk-
UPDATE-``.values()`` shapes the SUT builds against in-memory rows, never a
scripted mock.

CANON PIN: region-lifecycle.md's state diagram + transition-trigger table
+ pseudocode say 7 / 30 / 7 days (suspended->grace / suspended->terminated
/ terminated->hard-delete, the middle one measured from the ORIGINAL
suspended_at, not reset on entering grace) -- NOT the 8/31/7 this WO's own
brief cited. Built against the DOCUMENTED numbers per docs-win; the
boundary tests below (exactly 7 days triggers, 6 days does not) are what
actually pins WHICH number is live, since the brief's own literal examples
(8d/31d) happen to clear either candidate threshold and can't discriminate
between them on their own.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, UTC

import pytest
from sqlalchemy.sql import operators

from src.models.galaxy import Galaxy
from src.models.region import Region, RegionStatus
from src.services import region_lifecycle_service
from src.services.scheduler import economy_governance_sweeps
from src.services.scheduler._common import _REGION_LIFECYCLE_STATE_KEY
from src.services.scheduler.economy_governance_sweeps import (
    _run_region_lifecycle_advance_gated,
)

_NOW = datetime(2026, 6, 15, 12, 0, tzinfo=UTC)


# --------------------------------------------------------------------------- #
# In-memory fake rows / session
# --------------------------------------------------------------------------- #

class FakeRegionRow:
    def __init__(self, *, region_id=None, name="Test Region",
                 status=RegionStatus.SUSPENDED, suspended_at=None,
                 terminated_at=None, scheduled_hard_delete_at=None):
        self.id = region_id or uuid.uuid4()
        self.name = name
        self.status = status
        self.suspended_at = suspended_at
        self.terminated_at = terminated_at
        self.scheduled_hard_delete_at = scheduled_hard_delete_at


class _FakeResult:
    def __init__(self, rowcount: int) -> None:
        self.rowcount = rowcount


def _condition_matches(row, condition) -> bool:
    column = condition.left.key
    actual = getattr(row, column)
    op = condition.operator
    if op is operators.eq:
        return actual == condition.right.value
    if op is operators.is_not:
        return actual is not None
    if op is operators.le:
        return actual is not None and actual <= condition.right.value
    raise AssertionError(f"unhandled operator {op!r} on column {column!r}")


class _FakeRegionReadQuery:
    """db.query(Region.id, Region.name).filter(...).all() --
    dispatch_terminated_cleanup's read-only discovery query."""

    def __init__(self, store, session):
        self._store = store
        self._session = session
        self._conditions: tuple = ()

    def filter(self, *conditions):
        self._conditions = self._conditions + conditions
        return self

    def all(self):
        self._session.queries += 1
        return [
            (r.id, r.name) for r in self._store
            if all(_condition_matches(r, c) for c in self._conditions)
        ]


class _FakeGalaxyQuery:
    def __init__(self, galaxy, session):
        self._galaxy = galaxy
        self._session = session

    def order_by(self, *args, **kwargs):
        return self

    def first(self):
        self._session.queries += 1
        return self._galaxy


def _fresh_galaxy(state=None):
    """A REAL (unpersisted) Galaxy instance -- the gated wrapper calls the
    genuine flag_modified(galaxy, "state"), which requires actual
    SQLAlchemy instance-state (see test_treasury_reconciliation.py's
    identical reasoning)."""
    return Galaxy(state=state if state is not None else {}, created_at=datetime(2020, 1, 1))


class FakeRegionLifecycleSession:
    """Dispatches db.query(*cols) by owning mapped class; db.execute(stmt)
    interprets a real Core update() statement's _where_criteria/_values
    against the in-memory store, exactly like contract_service.py's own
    test fakes do for its bulk-UPDATE sweeps."""

    def __init__(self, *, regions=(), galaxy=None):
        self.regions = list(regions)
        self.galaxy = galaxy
        self.queries = 0
        self.executes = 0
        self.commits = 0

    def query(self, *cols):
        owner = getattr(cols[0], "class_", cols[0])
        if owner is Region:
            return _FakeRegionReadQuery(self.regions, self)
        if owner is Galaxy:
            return _FakeGalaxyQuery(self.galaxy, self)
        raise AssertionError(f"unexpected query owner {owner!r}")

    def execute(self, stmt):
        self.executes += 1
        values = {col.name: bind.value for col, bind in stmt._values.items()}
        matched = 0
        for row in self.regions:
            if all(_condition_matches(row, c) for c in stmt._where_criteria):
                for k, v in values.items():
                    setattr(row, k, v)
                matched += 1
        return _FakeResult(matched)

    def commit(self):
        self.commits += 1

    def rollback(self):
        pass


# --------------------------------------------------------------------------- #
# Canon-number pin
# --------------------------------------------------------------------------- #

class TestCanonDayNumberConstants:
    """Pins the DOCUMENTED numbers (region-lifecycle.md), not this WO's
    own brief (which cited 8/31/7) -- see module docstring."""

    def test_suspended_to_grace_is_7_days(self) -> None:
        assert region_lifecycle_service.SUSPENDED_TO_GRACE_DAYS == 7

    def test_suspended_to_terminated_is_30_days(self) -> None:
        assert region_lifecycle_service.SUSPENDED_TO_TERMINATED_DAYS == 30

    def test_terminated_to_hard_delete_is_7_days(self) -> None:
        assert region_lifecycle_service.TERMINATED_TO_HARD_DELETE_DAYS == 7


# --------------------------------------------------------------------------- #
# advance_to_grace
# --------------------------------------------------------------------------- #

class TestAdvanceToGrace:
    def test_wo_proof_example_8_days_advances(self) -> None:
        """The WO's own literal proof point -- clears either 7 or 8-day
        candidate threshold, so this alone can't prove WHICH is live (see
        the boundary tests below for that)."""
        region = FakeRegionRow(
            status=RegionStatus.SUSPENDED, suspended_at=_NOW - timedelta(days=8),
        )
        session = FakeRegionLifecycleSession(regions=[region])

        result = region_lifecycle_service.advance_to_grace(session, now=_NOW)

        assert result == {"advanced_to_grace": 1}
        assert region.status == RegionStatus.GRACE

    def test_exactly_7_days_advances_boundary_pin(self) -> None:
        """Discriminates 7 (documented) from 8 (WO brief) -- a region
        suspended exactly 7 days ago must already advance."""
        region = FakeRegionRow(
            status=RegionStatus.SUSPENDED, suspended_at=_NOW - timedelta(days=7),
        )
        session = FakeRegionLifecycleSession(regions=[region])

        region_lifecycle_service.advance_to_grace(session, now=_NOW)

        assert region.status == RegionStatus.GRACE

    def test_6_days_does_not_advance(self) -> None:
        region = FakeRegionRow(
            status=RegionStatus.SUSPENDED, suspended_at=_NOW - timedelta(days=6),
        )
        session = FakeRegionLifecycleSession(regions=[region])

        result = region_lifecycle_service.advance_to_grace(session, now=_NOW)

        assert result == {"advanced_to_grace": 0}
        assert region.status == RegionStatus.SUSPENDED

    def test_active_region_untouched(self) -> None:
        region = FakeRegionRow(status=RegionStatus.ACTIVE, suspended_at=None)
        session = FakeRegionLifecycleSession(regions=[region])

        result = region_lifecycle_service.advance_to_grace(session, now=_NOW)

        assert result == {"advanced_to_grace": 0}
        assert region.status == RegionStatus.ACTIVE

    def test_null_suspended_at_never_raises_or_matches(self) -> None:
        """A SUSPENDED region with no suspended_at recorded (a data
        anomaly) must be skipped, not crash the comparison."""
        region = FakeRegionRow(status=RegionStatus.SUSPENDED, suspended_at=None)
        session = FakeRegionLifecycleSession(regions=[region])

        result = region_lifecycle_service.advance_to_grace(session, now=_NOW)

        assert result == {"advanced_to_grace": 0}
        assert region.status == RegionStatus.SUSPENDED

    def test_multiple_regions_only_due_ones_advance(self) -> None:
        due = FakeRegionRow(status=RegionStatus.SUSPENDED, suspended_at=_NOW - timedelta(days=10))
        not_due = FakeRegionRow(status=RegionStatus.SUSPENDED, suspended_at=_NOW - timedelta(days=1))
        session = FakeRegionLifecycleSession(regions=[due, not_due])

        result = region_lifecycle_service.advance_to_grace(session, now=_NOW)

        assert result == {"advanced_to_grace": 1}
        assert due.status == RegionStatus.GRACE
        assert not_due.status == RegionStatus.SUSPENDED


# --------------------------------------------------------------------------- #
# advance_to_terminated
# --------------------------------------------------------------------------- #

class TestAdvanceToTerminated:
    def test_wo_proof_example_31_days_terminates_with_timestamps_set(self) -> None:
        region = FakeRegionRow(
            status=RegionStatus.GRACE, suspended_at=_NOW - timedelta(days=31),
        )
        session = FakeRegionLifecycleSession(regions=[region])

        result = region_lifecycle_service.advance_to_terminated(session, now=_NOW)

        assert result == {"advanced_to_terminated": 1}
        assert region.status == RegionStatus.TERMINATED
        assert region.terminated_at == _NOW
        assert region.scheduled_hard_delete_at == _NOW + timedelta(days=7)

    def test_exactly_30_days_terminates_boundary_pin(self) -> None:
        """Discriminates 30 (documented) from 31 (WO brief)."""
        region = FakeRegionRow(
            status=RegionStatus.GRACE, suspended_at=_NOW - timedelta(days=30),
        )
        session = FakeRegionLifecycleSession(regions=[region])

        region_lifecycle_service.advance_to_terminated(session, now=_NOW)

        assert region.status == RegionStatus.TERMINATED

    def test_29_days_does_not_terminate(self) -> None:
        region = FakeRegionRow(
            status=RegionStatus.GRACE, suspended_at=_NOW - timedelta(days=29),
        )
        session = FakeRegionLifecycleSession(regions=[region])

        result = region_lifecycle_service.advance_to_terminated(session, now=_NOW)

        assert result == {"advanced_to_terminated": 0}
        assert region.status == RegionStatus.GRACE
        assert region.terminated_at is None
        assert region.scheduled_hard_delete_at is None

    def test_suspended_status_region_never_terminates_directly(self) -> None:
        """The 30-day clock only fires from GRACE -- a still-SUSPENDED
        region (even one whose suspended_at is very old, e.g. the cron
        never ran) must go through advance_to_grace first; this function
        alone never skips straight to terminated."""
        region = FakeRegionRow(
            status=RegionStatus.SUSPENDED, suspended_at=_NOW - timedelta(days=60),
        )
        session = FakeRegionLifecycleSession(regions=[region])

        result = region_lifecycle_service.advance_to_terminated(session, now=_NOW)

        assert result == {"advanced_to_terminated": 0}
        assert region.status == RegionStatus.SUSPENDED


# --------------------------------------------------------------------------- #
# dispatch_terminated_cleanup -- discovery only, never writes
# --------------------------------------------------------------------------- #

class TestDispatchTerminatedCleanup:
    def test_eligible_region_counted_never_mutated(self) -> None:
        region = FakeRegionRow(
            status=RegionStatus.TERMINATED,
            scheduled_hard_delete_at=_NOW - timedelta(hours=1),
        )
        session = FakeRegionLifecycleSession(regions=[region])

        result = region_lifecycle_service.dispatch_terminated_cleanup(session, now=_NOW)

        assert result == {"cleanup_eligible": 1}
        assert region.status == RegionStatus.TERMINATED  # untouched
        assert session.executes == 0  # read-only -- no UPDATE issued

    def test_not_yet_due_region_excluded(self) -> None:
        region = FakeRegionRow(
            status=RegionStatus.TERMINATED,
            scheduled_hard_delete_at=_NOW + timedelta(hours=1),
        )
        session = FakeRegionLifecycleSession(regions=[region])

        result = region_lifecycle_service.dispatch_terminated_cleanup(session, now=_NOW)

        assert result == {"cleanup_eligible": 0}

    def test_non_terminated_region_never_counted(self) -> None:
        region = FakeRegionRow(
            status=RegionStatus.GRACE,
            scheduled_hard_delete_at=_NOW - timedelta(days=100),  # nonsensical but defensive
        )
        session = FakeRegionLifecycleSession(regions=[region])

        result = region_lifecycle_service.dispatch_terminated_cleanup(session, now=_NOW)

        assert result == {"cleanup_eligible": 0}


# --------------------------------------------------------------------------- #
# Phase 7 gated wrapper -- day-gate + sequencing
# --------------------------------------------------------------------------- #

class TestRegionLifecycleAdvanceGated:
    @pytest.fixture(autouse=True)
    def _pin_canonical_day(self, monkeypatch):
        self.day = 999000
        monkeypatch.setattr(
            economy_governance_sweeps, "canonical_day_number", lambda *a, **k: self.day
        )

    def test_advances_both_transitions_and_stamps_the_anchor(self) -> None:
        grace_candidate = FakeRegionRow(
            status=RegionStatus.SUSPENDED, suspended_at=datetime.now(UTC) - timedelta(days=10),
        )
        terminate_candidate = FakeRegionRow(
            status=RegionStatus.GRACE, suspended_at=datetime.now(UTC) - timedelta(days=40),
        )
        galaxy = _fresh_galaxy(state={})
        session = FakeRegionLifecycleSession(
            regions=[grace_candidate, terminate_candidate], galaxy=galaxy,
        )

        result = _run_region_lifecycle_advance_gated(session)

        assert result["region_lifecycle_skipped"] is False
        assert result["advanced_to_grace"] == 1
        assert result["advanced_to_terminated"] == 1
        assert grace_candidate.status == RegionStatus.GRACE
        assert terminate_candidate.status == RegionStatus.TERMINATED
        assert galaxy.state[_REGION_LIFECYCLE_STATE_KEY] == self.day

    def test_overdue_region_catches_up_through_both_transitions_in_one_pass(self) -> None:
        """A region suspended 40 days ago that the cron never got to
        (e.g. server was down) must not get stuck waiting an extra day in
        grace -- advance_to_terminated runs in the SAME call, right after
        advance_to_grace flips it, so it catches up immediately."""
        now = datetime.now(UTC)
        overdue = FakeRegionRow(status=RegionStatus.SUSPENDED, suspended_at=now - timedelta(days=40))
        galaxy = _fresh_galaxy(state={})
        session = FakeRegionLifecycleSession(regions=[overdue], galaxy=galaxy)

        result = _run_region_lifecycle_advance_gated(session)

        # Both fire in this one pass: advance_to_grace flips SUSPENDED->
        # GRACE and counts it (40 days clears the 7-day threshold), then
        # advance_to_terminated immediately re-evaluates the now-GRACE
        # region against the 30-day threshold (also cleared) and flips it
        # again -- no extra tick needed to notice the region is doubly
        # overdue.
        assert result["advanced_to_grace"] == 1
        assert result["advanced_to_terminated"] == 1
        assert overdue.status == RegionStatus.TERMINATED

    def test_second_run_same_day_is_a_noop(self) -> None:
        region = FakeRegionRow(
            status=RegionStatus.SUSPENDED, suspended_at=datetime.now(UTC) - timedelta(days=10),
        )
        galaxy = _fresh_galaxy(state={})
        session = FakeRegionLifecycleSession(regions=[region], galaxy=galaxy)

        first = _run_region_lifecycle_advance_gated(session)
        assert first["region_lifecycle_skipped"] is False
        assert region.status == RegionStatus.GRACE

        second = _run_region_lifecycle_advance_gated(session)
        assert second["region_lifecycle_skipped"] is True
        assert second["advanced_to_grace"] == 0
        assert second["advanced_to_terminated"] == 0

    def test_falsifiability_new_canonical_day_runs_again(self) -> None:
        region = FakeRegionRow(
            status=RegionStatus.SUSPENDED, suspended_at=datetime.now(UTC) - timedelta(days=10),
        )
        galaxy = _fresh_galaxy(state={})
        session = FakeRegionLifecycleSession(regions=[region], galaxy=galaxy)

        first = _run_region_lifecycle_advance_gated(session)
        assert first["region_lifecycle_skipped"] is False

        same_day = _run_region_lifecycle_advance_gated(session)
        assert same_day["region_lifecycle_skipped"] is True

        self.day += 1
        next_day = _run_region_lifecycle_advance_gated(session)
        assert next_day["region_lifecycle_skipped"] is False
        assert galaxy.state[_REGION_LIFECYCLE_STATE_KEY] == self.day
