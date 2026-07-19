"""Unit coverage for the RouteOptimizationRun retention sweep
(WO-OPS-ROUTE-RUNS-RETENTION).

route_optimization_runs (route_optimizer.py / ai.py's
``_record_optimization_run``) is written on every SUCCESSFUL player
route-optimize call with no cap and no prune job -- the authoring spec
(WO-SB-RO2) deliberately deferred this: "a prune job is out of scope --
flag retention policy to DECISIONS". This file proves
``prune_route_optimization_runs`` deletes a row only when it is BOTH older
than ``ROUTE_RUNS_RETENTION_DAYS`` AND beyond that player's newest
``ROUTE_RUNS_RETENTION_MAX_PER_PLAYER`` rows -- either bound alone protects
a row.

No live DB is used. Per the codebase's mock-only unit-test convention (see
test_movement_drone_encounters.py's FakeDroneQuery), ``FakeRouteRunQuery``
interprets the REAL SQLAlchemy filter()/order_by()/distinct()/delete()
clauses the SUT builds against a live, mutable in-memory row list -- so
exclusion/inclusion is exercised for real, not merely asserted by
inspection of call args.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta

import pytest
from sqlalchemy.sql import operators

from src.services.scheduler import presence_helpers
from src.services.npc_scheduler_service import prune_route_optimization_runs

NOW = datetime(2026, 7, 8, 12, 0, 0)


# --------------------------------------------------------------------------- #
# In-memory fake session -- interprets the SUT's real filter conditions
# --------------------------------------------------------------------------- #

class FakeRow:
    """Stand-in for a RouteOptimizationRun row -- only the columns the
    sweep actually reads/filters on."""

    def __init__(self, player_id, created_at, run_id=None):
        self.id = run_id or uuid.uuid4()
        self.player_id = player_id
        self.created_at = created_at


def _condition_matches(row, condition):
    column = condition.left.key
    actual = getattr(row, column)
    op = condition.operator
    if op is operators.lt:
        return actual < condition.right.value
    if op is operators.eq:
        return actual == condition.right.value
    if op is operators.in_op:
        return actual in condition.right.value
    raise AssertionError(f"unhandled operator {op!r} on column {column!r}")


class FakeRouteRunQuery:
    """In-memory stand-in for db.query(RouteOptimizationRun, ...): filter()/
    order_by()/distinct() record the real SQLAlchemy clauses the SUT
    constructs; all()/delete() apply them against a SHARED mutable row list
    (``store``) -- so a delete() made mid-sweep is visible to the next
    query built off the same fake session, mirroring one open transaction.
    """

    def __init__(self, store, columns):
        self._store = store
        self._columns = columns
        self._conditions: tuple = ()
        self._order = None
        self._distinct = False

    def filter(self, *conditions):
        self._conditions = self._conditions + conditions
        return self

    def order_by(self, clause):
        self._order = clause
        return self

    def distinct(self):
        self._distinct = True
        return self

    def _matching(self):
        return [
            r for r in self._store
            if all(_condition_matches(r, c) for c in self._conditions)
        ]

    def _project(self, rows):
        # db.query(RouteOptimizationRun) -> whole row objects.
        if len(self._columns) == 1 and isinstance(self._columns[0], type):
            return list(rows)
        # db.query(RouteOptimizationRun.col, ...) -> row tuples.
        return [tuple(getattr(r, c.key) for c in self._columns) for r in rows]

    def all(self):
        rows = self._matching()
        if self._order is not None:
            key = self._order.element.key
            reverse = self._order.modifier is operators.desc_op
            rows = sorted(rows, key=lambda r: getattr(r, key), reverse=reverse)
        projected = self._project(rows)
        if self._distinct:
            seen, out = [], []
            for v in projected:
                if v not in seen:
                    seen.append(v)
                    out.append(v)
            return out
        return projected

    def delete(self, synchronize_session=False):
        victims = self._matching()
        for v in victims:
            self._store.remove(v)
        return len(victims)


class FakeSession:
    """Minimal db double: every .query() call returns a fresh
    FakeRouteRunQuery bound to the SAME shared row list."""

    def __init__(self, rows):
        self.store = list(rows)

    def query(self, *columns):
        return FakeRouteRunQuery(self.store, columns)


def _surviving_ids(session):
    return {r.id for r in session.store}


@pytest.fixture(autouse=True)
def _small_policy(monkeypatch):
    """Pin the sweep to small, deterministic policy numbers for the test
    scenarios below, rather than the shipped defaults (30 days / 200 rows)
    -- the sweep reads these as bare module globals at call time, so
    monkeypatching the live module attribute is genuinely load-bearing (see
    the falsifiability test at the bottom of this file)."""
    monkeypatch.setattr(presence_helpers, "ROUTE_RUNS_RETENTION_DAYS", 10)
    monkeypatch.setattr(presence_helpers, "ROUTE_RUNS_RETENTION_MAX_PER_PLAYER", 2)


# --------------------------------------------------------------------------- #
# Core bound logic
# --------------------------------------------------------------------------- #

class TestPruneBounds:
    def test_old_row_beyond_cap_is_pruned(self):
        """Player with 3 rows (cap=2): the 3rd-ranked row is both older
        than the 10-day window and beyond the per-player cap -> pruned. The
        two newest survive (young)."""
        player = uuid.uuid4()
        rows = [
            FakeRow(player, NOW - timedelta(days=1)),   # rank 1, young
            FakeRow(player, NOW - timedelta(days=2)),   # rank 2, young
            FakeRow(player, NOW - timedelta(days=20)),  # rank 3, old
        ]
        session = FakeSession(rows)

        deleted = prune_route_optimization_runs(session, now=NOW)

        assert deleted == 1
        surviving = _surviving_ids(session)
        assert rows[0].id in surviving
        assert rows[1].id in surviving
        assert rows[2].id not in surviving

    def test_old_row_within_cap_survives_despite_age(self):
        """Player with 3 rows (cap=2): rank-2 row is older than the 10-day
        window but still WITHIN the per-player cap -> survives. Only the
        rank-3 row (old AND beyond cap) is pruned. Proves the K-rule
        protects a row even for a player who HAS exceeded K total rows, not
        just the trivial ``total <= K`` case."""
        player = uuid.uuid4()
        rank1 = FakeRow(player, NOW - timedelta(days=1))    # young
        rank2 = FakeRow(player, NOW - timedelta(days=15))   # old, but rank<=cap
        rank3 = FakeRow(player, NOW - timedelta(days=25))   # old, beyond cap
        session = FakeSession([rank1, rank2, rank3])

        deleted = prune_route_optimization_runs(session, now=NOW)

        assert deleted == 1
        surviving = _surviving_ids(session)
        assert rank1.id in surviving
        assert rank2.id in surviving  # old but within cap -- survives
        assert rank3.id not in surviving

    def test_newest_k_survive_regardless_of_age_when_total_under_cap(self):
        """Player whose TOTAL row count never exceeds the cap: even a very
        old row survives, since it's always within the player's newest K."""
        player = uuid.uuid4()
        only_row = FakeRow(player, NOW - timedelta(days=400))
        session = FakeSession([only_row])

        deleted = prune_route_optimization_runs(session, now=NOW)

        assert deleted == 0
        assert only_row.id in _surviving_ids(session)

    def test_rows_inside_both_bounds_are_untouched(self):
        """Young rows that are also within the cap are never candidates at
        all -- untouched regardless of how many other players are pruned
        in the same pass."""
        player_pruned = uuid.uuid4()
        player_safe = uuid.uuid4()
        pruned_candidate = FakeRow(player_pruned, NOW - timedelta(days=30))
        rows = [
            FakeRow(player_pruned, NOW - timedelta(days=1)),
            FakeRow(player_pruned, NOW - timedelta(days=2)),
            pruned_candidate,
            FakeRow(player_safe, NOW - timedelta(days=1)),
            FakeRow(player_safe, NOW - timedelta(days=3)),
        ]
        session = FakeSession(rows)

        deleted = prune_route_optimization_runs(session, now=NOW)

        assert deleted == 1
        surviving = _surviving_ids(session)
        assert pruned_candidate.id not in surviving
        for r in rows:
            if r is not pruned_candidate:
                assert r.id in surviving

    def test_no_stale_rows_is_a_clean_no_op(self):
        """A table with nothing older than the window costs the cheap
        DISTINCT pre-filter and nothing else -- zero deletions."""
        player = uuid.uuid4()
        rows = [FakeRow(player, NOW - timedelta(days=1))]
        session = FakeSession(rows)

        deleted = prune_route_optimization_runs(session, now=NOW)

        assert deleted == 0
        assert len(session.store) == 1


# --------------------------------------------------------------------------- #
# Idempotency
# --------------------------------------------------------------------------- #

class TestIdempotency:
    def test_second_run_deletes_nothing(self):
        player = uuid.uuid4()
        rows = [
            FakeRow(player, NOW - timedelta(days=1)),
            FakeRow(player, NOW - timedelta(days=2)),
            FakeRow(player, NOW - timedelta(days=20)),
        ]
        session = FakeSession(rows)

        first_pass = prune_route_optimization_runs(session, now=NOW)
        assert first_pass == 1

        second_pass = prune_route_optimization_runs(session, now=NOW)
        assert second_pass == 0
        assert len(session.store) == 2


# --------------------------------------------------------------------------- #
# Batching
# --------------------------------------------------------------------------- #

class TestBatching:
    def test_deletes_span_multiple_batches(self):
        """Five distinct players each contribute exactly one prunable row
        (each player has two younger siblings pushing their old row to
        rank 3, beyond cap=2); with batch_size=2 the sweep must apply three
        separate DELETE batches (2 + 2 + 1) and still remove every eligible
        row."""
        rows = []
        expected_pruned = []
        for _ in range(5):
            player = uuid.uuid4()
            old = FakeRow(player, NOW - timedelta(days=20))
            rows.append(FakeRow(player, NOW - timedelta(days=1)))
            rows.append(FakeRow(player, NOW - timedelta(days=2)))
            rows.append(old)
            expected_pruned.append(old.id)
        session = FakeSession(rows)

        deleted = prune_route_optimization_runs(session, now=NOW, batch_size=2)

        assert deleted == 5
        surviving = _surviving_ids(session)
        for pruned_id in expected_pruned:
            assert pruned_id not in surviving
        assert len(session.store) == 10


# --------------------------------------------------------------------------- #
# Falsifiability -- proves the pins are load-bearing against the NAMED
# constants, not a hardcoded/tautological expectation.
# --------------------------------------------------------------------------- #

class TestFalsifiability:
    def test_shrinking_retention_days_prunes_a_previously_protected_row(self, monkeypatch):
        """A row protected ONLY by youth (rank beyond the cap, but inside
        the 10-day window) survives at the default fixture policy. Shrink
        ROUTE_RUNS_RETENTION_DAYS to 0 (same live module global the SUT
        reads) and the SAME row becomes eligible -- proving the age check
        genuinely gates on the named constant rather than being dead code
        or a hardcoded literal."""
        player = uuid.uuid4()
        rank1 = FakeRow(player, NOW - timedelta(hours=1))
        rank2 = FakeRow(player, NOW - timedelta(hours=2))
        # rank 3 (oldest of the three, beyond cap=2), but only 3 hours old
        # -- protected by the 10-day window at the fixture's default policy.
        rank3_young_but_beyond_cap = FakeRow(player, NOW - timedelta(hours=3))
        session = FakeSession([rank1, rank2, rank3_young_but_beyond_cap])

        deleted_under_10_day_window = prune_route_optimization_runs(session, now=NOW)
        assert deleted_under_10_day_window == 0
        assert rank3_young_but_beyond_cap.id in _surviving_ids(session)

        monkeypatch.setattr(presence_helpers, "ROUTE_RUNS_RETENTION_DAYS", 0)

        deleted_under_0_day_window = prune_route_optimization_runs(session, now=NOW)
        assert deleted_under_0_day_window == 1
        assert rank3_young_but_beyond_cap.id not in _surviving_ids(session)

    def test_shrinking_max_per_player_prunes_a_previously_protected_row(self, monkeypatch):
        """A row protected ONLY by the per-player cap (old, but rank <=
        cap) survives at the fixture's cap=2. Shrink
        ROUTE_RUNS_RETENTION_MAX_PER_PLAYER to 1 and the SAME row becomes
        eligible -- proving the cap check genuinely gates on the named
        constant."""
        player = uuid.uuid4()
        rank1 = FakeRow(player, NOW - timedelta(days=1))
        # rank 2, old (beyond the 10-day window), protected by cap=2.
        rank2_old_but_within_cap = FakeRow(player, NOW - timedelta(days=15))
        session = FakeSession([rank1, rank2_old_but_within_cap])

        deleted_under_cap_2 = prune_route_optimization_runs(session, now=NOW)
        assert deleted_under_cap_2 == 0
        assert rank2_old_but_within_cap.id in _surviving_ids(session)

        monkeypatch.setattr(presence_helpers, "ROUTE_RUNS_RETENTION_MAX_PER_PLAYER", 1)

        deleted_under_cap_1 = prune_route_optimization_runs(session, now=NOW)
        assert deleted_under_cap_1 == 1
        assert rank2_old_but_within_cap.id not in _surviving_ids(session)
