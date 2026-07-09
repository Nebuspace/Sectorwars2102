"""Unit coverage for the ADR-0059 N-I4 daily treasury reconciliation sweep
(WO-REGOV-TREASURY-RECON).

RegionalTreasuryEntry's own docstring (region.py) names the invariant this
verifies: SUM(RegionalTreasuryEntry.delta) == Region.treasury_balance for
every ACTIVE region. Before this WO, nothing ever checked it. This file
proves ``reconcile_region_treasuries`` (the pure, session-injectable
aggregate) and ``_run_treasury_reconciliation_gated`` (its Phase-4-style
Galaxy.state day-anchor wrapper, both now Phase 6 of
``_run_governance_sweep_sync``) against a live, mutable in-memory fake --
never a real DB.

No live DB is used. Per the codebase's mock-only unit-test convention (see
test_route_runs_retention.py's FakeRouteRunQuery / test_available_moves_
query_count.py's _FakeQuery), the fake session below interprets the REAL
SQLAlchemy filter()/group_by() clauses the SUT builds against in-memory row
stores, and a shared counter increments on every terminal call (.all() /
.first()) that would be a real DB round trip -- proving the reconciliation
issues a FIXED, small number of queries regardless of how many regions or
ledger rows exist (test_available_moves_query_count.py's counting-session
convention).
"""
from __future__ import annotations

import uuid
from datetime import datetime
from unittest.mock import MagicMock

import pytest
from sqlalchemy.sql import operators

import src.services.npc_scheduler_service as npc_scheduler_service
from src.models.galaxy import Galaxy
from src.models.region import Region, RegionStatus, RegionalTreasuryEntry
from src.services.npc_scheduler_service import (
    _TREASURY_RECON_STATE_KEY,
    _run_treasury_reconciliation_gated,
    reconcile_region_treasuries,
)


# --------------------------------------------------------------------------- #
# In-memory fake rows -- only the columns the sweep actually reads
# --------------------------------------------------------------------------- #

class FakeRegionRow:
    def __init__(self, region_id=None, treasury_balance=0, status=RegionStatus.ACTIVE):
        self.id = region_id or uuid.uuid4()
        self.treasury_balance = treasury_balance
        self.status = status


class FakeLedgerRow:
    def __init__(self, region_id, delta):
        self.region_id = region_id
        self.delta = delta


def _fresh_galaxy(state=None):
    """A REAL (unpersisted) Galaxy instance -- ``_run_treasury_reconciliation_
    gated`` calls the genuine ``flag_modified(galaxy, "state")``, which
    requires actual SQLAlchemy instance-state; a plain fake object doesn't
    have it (see test_drone_scalar_canon.py's identical reasoning for using
    a real, unpersisted model rather than a stub)."""
    return Galaxy(state=state if state is not None else {}, created_at=datetime(2020, 1, 1))


def _condition_matches(row, condition) -> bool:
    column = condition.left.key
    actual = getattr(row, column)
    op = condition.operator
    if op is operators.eq:
        return actual == condition.right.value
    raise AssertionError(f"unhandled operator {op!r} on column {column!r}")


# --------------------------------------------------------------------------- #
# In-memory fake session -- interprets the SUT's real query shapes; a
# terminal call (.all() / .first()) is the falsifiable "real round trip"
# metric, mirroring test_available_moves_query_count.py's _FakeQuery.
# --------------------------------------------------------------------------- #

class _FakeLedgerAggregateQuery:
    """db.query(RegionalTreasuryEntry.region_id, func.sum(...delta))
    .group_by(RegionalTreasuryEntry.region_id).all() -- groups the REAL
    in-memory ledger rows by the group_by column and sums their real
    ``delta`` values, so a region with zero ledger rows genuinely never
    appears in the result at all (not a canned SUM=NULL row)."""

    def __init__(self, store, session):
        self._store = store
        self._session = session
        self._group_col = None

    def group_by(self, col):
        self._group_col = col
        return self

    def all(self):
        self._session.queries += 1
        assert self._group_col is not None, "aggregate must be grouped"
        key = self._group_col.key
        totals: dict = {}
        for row in self._store:
            totals[getattr(row, key)] = totals.get(getattr(row, key), 0) + row.delta
        return list(totals.items())


class _FakeActiveRegionQuery:
    """db.query(Region.id, Region.treasury_balance).filter(...).all()."""

    def __init__(self, store, session):
        self._store = store
        self._session = session
        self._conditions: tuple = ()

    def filter(self, *conditions):
        self._conditions = self._conditions + conditions
        return self

    def all(self):
        self._session.queries += 1
        rows = [r for r in self._store if all(_condition_matches(r, c) for c in self._conditions)]
        return [(r.id, r.treasury_balance) for r in rows]


class _FakeGalaxyQuery:
    """db.query(Galaxy).order_by(Galaxy.created_at.asc()).first()."""

    def __init__(self, galaxy, session):
        self._galaxy = galaxy
        self._session = session

    def order_by(self, *args, **kwargs):
        return self

    def first(self):
        self._session.queries += 1
        return self._galaxy


class FakeReconSession:
    """Minimal db double: dispatches db.query(*cols) by the owning mapped
    class of the first column/entity (a real SQLAlchemy InstrumentedAttribute
    exposes ``.class_``; a bare mapped class like ``Galaxy`` has none, so it
    IS the owner). ``queries`` counts every real round trip; ``add``/
    ``update`` are hard failures -- this sweep must never write."""

    def __init__(self, *, ledger_rows=(), region_rows=(), galaxy=None):
        self.queries = 0
        self.commits = 0
        self.ledger_rows = list(ledger_rows)
        self.region_rows = list(region_rows)
        self.galaxy = galaxy

    def query(self, *cols):
        owner = getattr(cols[0], "class_", cols[0])
        if owner is RegionalTreasuryEntry:
            return _FakeLedgerAggregateQuery(self.ledger_rows, self)
        if owner is Region:
            return _FakeActiveRegionQuery(self.region_rows, self)
        if owner is Galaxy:
            return _FakeGalaxyQuery(self.galaxy, self)
        raise AssertionError(f"unexpected query owner {owner!r}")

    def add(self, *args, **kwargs):
        raise AssertionError("reconcile_region_treasuries must never db.add()")

    def commit(self):
        self.commits += 1

    def rollback(self):
        pass


# --------------------------------------------------------------------------- #
# Core aggregate: matched / drift / empty-ledger
# --------------------------------------------------------------------------- #

class TestReconcileRegionTreasuries:
    def test_matched_ledger_no_alert_and_zero_writes(self, monkeypatch):
        region = FakeRegionRow(treasury_balance=500)
        ledger = [
            FakeLedgerRow(region.id, 300),
            FakeLedgerRow(region.id, 200),
        ]
        session = FakeReconSession(ledger_rows=ledger, region_rows=[region])
        error_mock = MagicMock()
        monkeypatch.setattr(npc_scheduler_service.logger, "error", error_mock)

        result = reconcile_region_treasuries(session)

        assert result == {"checked": 1, "mismatched": 0}
        error_mock.assert_not_called()
        assert session.commits == 0
        assert region.treasury_balance == 500  # untouched

    def test_injected_drift_logs_exactly_once_with_both_figures(self, monkeypatch):
        region = FakeRegionRow(treasury_balance=500)
        ledger = [FakeLedgerRow(region.id, 300)]  # sums to 300, balance says 500
        session = FakeReconSession(ledger_rows=ledger, region_rows=[region])
        error_mock = MagicMock()
        monkeypatch.setattr(npc_scheduler_service.logger, "error", error_mock)

        result = reconcile_region_treasuries(session)

        assert result == {"checked": 1, "mismatched": 1}
        error_mock.assert_called_once()
        args = error_mock.call_args.args
        # (format string, region_id, ledger_sum, treasury_balance, drift)
        assert args[1] == region.id
        assert args[2] == 300
        assert args[3] == 500
        assert args[4] == 200  # drift = balance - ledger_sum
        assert region.treasury_balance == 500  # never corrected

    def test_empty_ledger_region_compares_as_zero_without_crash(self, monkeypatch):
        """A region with NO ledger rows at all never appears in the grouped
        aggregate's result -- proves the lookup default (0) stands in for a
        SQL NULL rather than raising, both when it matches (balance=0) and
        when it doesn't (balance!=0, still a real mismatch)."""
        matched = FakeRegionRow(treasury_balance=0)
        drifted = FakeRegionRow(treasury_balance=75)
        session = FakeReconSession(ledger_rows=[], region_rows=[matched, drifted])
        error_mock = MagicMock()
        monkeypatch.setattr(npc_scheduler_service.logger, "error", error_mock)

        result = reconcile_region_treasuries(session)

        assert result == {"checked": 2, "mismatched": 1}
        error_mock.assert_called_once()
        assert error_mock.call_args.args[1] == drifted.id

    def test_inactive_region_is_excluded_even_when_mismatched(self):
        suspended = FakeRegionRow(treasury_balance=999, status=RegionStatus.SUSPENDED)
        ledger = [FakeLedgerRow(suspended.id, 1)]  # wildly mismatched, but not ACTIVE
        session = FakeReconSession(ledger_rows=ledger, region_rows=[suspended])

        result = reconcile_region_treasuries(session)

        assert result == {"checked": 0, "mismatched": 0}


# --------------------------------------------------------------------------- #
# Query-count independence -- the aggregate is a FIXED number of queries
# --------------------------------------------------------------------------- #

class TestQueryCountIndependence:
    def test_aggregate_issues_a_fixed_query_count_regardless_of_scale(self):
        """One grouped ledger SUM + one filtered active-region fetch = 2
        round trips, whether there is 1 region/1 ledger row or 10 regions/50
        ledger rows -- proves the reconciliation is NOT per-region/per-entry
        (no N+1)."""
        small_region = FakeRegionRow(treasury_balance=10)
        small_session = FakeReconSession(
            ledger_rows=[FakeLedgerRow(small_region.id, 10)],
            region_rows=[small_region],
        )

        large_regions = [FakeRegionRow(treasury_balance=100) for _ in range(10)]
        large_ledger = []
        for r in large_regions:
            for _ in range(5):
                large_ledger.append(FakeLedgerRow(r.id, 20))
        large_session = FakeReconSession(ledger_rows=large_ledger, region_rows=large_regions)

        reconcile_region_treasuries(small_session)
        reconcile_region_treasuries(large_session)

        assert small_session.queries == large_session.queries
        assert small_session.queries == 2


# --------------------------------------------------------------------------- #
# Day-gate -- mirrors Phase 4's Galaxy.state anchor discipline
# --------------------------------------------------------------------------- #

class TestDayGate:
    @pytest.fixture(autouse=True)
    def _pin_canonical_day(self, monkeypatch):
        """The gate reads canonical_day_number() with NO args (real
        datetime.now(UTC), same discipline as Phase 4 -- see that phase's
        own comment on why). Pin it so the day-gate is deterministic instead
        of racing the real wall clock."""
        self.day = 999000
        monkeypatch.setattr(npc_scheduler_service, "canonical_day_number", lambda *a, **k: self.day)

    def test_second_run_same_day_is_a_noop(self, monkeypatch):
        region = FakeRegionRow(treasury_balance=500)
        ledger = [FakeLedgerRow(region.id, 300)]  # mismatched -- would alert if it ran
        galaxy = _fresh_galaxy(state={})
        session = FakeReconSession(ledger_rows=ledger, region_rows=[region], galaxy=galaxy)
        error_mock = MagicMock()
        monkeypatch.setattr(npc_scheduler_service.logger, "error", error_mock)

        first = _run_treasury_reconciliation_gated(session)
        assert first["treasury_recon_skipped"] is False
        assert first["treasury_checked"] == 1
        assert first["treasury_mismatched"] == 1
        assert error_mock.call_count == 1
        assert galaxy.state[_TREASURY_RECON_STATE_KEY] == self.day

        error_mock.reset_mock()
        second = _run_treasury_reconciliation_gated(session)

        assert second["treasury_recon_skipped"] is True
        assert second["treasury_checked"] == 0
        assert second["treasury_mismatched"] == 0
        error_mock.assert_not_called()  # the reconciliation genuinely did not re-run

    def test_falsifiability_new_canonical_day_runs_again(self, monkeypatch):
        """Proves the gate is keyed on the NAMED state key / day number,
        not permanently latched -- advancing the pinned day re-arms it."""
        region = FakeRegionRow(treasury_balance=500)
        ledger = [FakeLedgerRow(region.id, 300)]
        galaxy = _fresh_galaxy(state={})
        session = FakeReconSession(ledger_rows=ledger, region_rows=[region], galaxy=galaxy)

        first = _run_treasury_reconciliation_gated(session)
        assert first["treasury_recon_skipped"] is False

        same_day = _run_treasury_reconciliation_gated(session)
        assert same_day["treasury_recon_skipped"] is True

        self.day += 1  # advance the pinned canonical day
        next_day = _run_treasury_reconciliation_gated(session)
        assert next_day["treasury_recon_skipped"] is False
        assert next_day["treasury_checked"] == 1
        assert galaxy.state[_TREASURY_RECON_STATE_KEY] == self.day
