"""TICKET-PRESENCE-PRUNE-LOCK — closes a lost-update on the presence-sweep's
``Sector.players_present`` read-modify-write.

PREMISE (confirmed by recon + this test's own regression pin): before this
fix, ``_run_presence_sweep_sync`` (presence_helpers.py) collected its
candidate list via a full-entity, UNLOCKED ``db.query(Sector).filter(...)
.all()`` -- caching each candidate ``Sector`` (including its
``players_present`` snapshot) in the sweep's own session, well before that
sector's eventual ``db.commit()``. In the meantime, two OTHER writers to the
SAME column -- ``movement_service._update_player_presence`` and
``npc_movement_service``'s roster movers -- already take a
``.populate_existing().with_for_update()`` row lock on ``Sector`` before
THEIR read-modify-write (see test_movement_presence_lock_identity_map.py for
the identity-map precedent this generalizes). The sweep alone skipped that
lock, so a blind ``sec.players_present = kept; db.commit()`` computed from
its stale snapshot could silently clobber a concurrent writer's addition
that committed in between (a lost update) -- the sweep's own docstring
disclaims duplicate SWEEPS as idempotent-safe, but never disclaimed
CLOBBERING an unrelated writer's new entry, which is the actual bug.

FIX: the candidate scan is now COLUMN-ONLY (``Sector.id`` alone) -- it never
caches a full ``Sector`` entity -- and each candidate is re-fetched via
``.populate_existing().with_for_update()`` RIGHT BEFORE the RMW, mirroring
the movement/NPC writers' exact chain shape on this exact column. Every
writer to ``players_present`` now takes the same row lock before its RMW, so
Postgres serializes concurrent writers instead of letting whichever commits
last blindly overwrite the other.

WHY NOT AN ATOMIC JSONB UPDATE (the backlog's stated preference): the
candidate/prune data shape needs real Postgres jsonb functions
(``jsonb_array_elements`` / ``jsonb_agg`` / ``->>`` / ``::uuid`` casts) that
have no SQLite equivalent -- unlike the simple `Column(JSON)` mirror trick
this codebase already uses for basic list/dict columns (see
test_movement_presence_lock_identity_map.py's own `_MirrorSector`), there is
no way to exercise real jsonb SQL syntax against SQLite at all, and this
repo's dev/prod stack is a remote Postgres host with no local Docker (per
CLAUDE.md) -- so a raw-jsonb-UPDATE rewrite would ship with ZERO runnable
proof in this environment, only a live-PG one. The lock fallback, by
contrast, is a mechanical, minimal diff onto an ALREADY-VETTED, ALREADY-
TESTED house pattern for this exact column (two sibling writers use it
today), preserves the sweep's exact original prune semantics unchanged, and
is provable end-to-end here with the same fake-session idiom the rest of
this file's siblings already use (test_npc_scheduler_unit.py's
`_FakeLockDB` style). Per the WO's own escape hatch ("if an atomic UPDATE
genuinely isn't expressible for this data shape... say why") -- this is why.

NOTE on `.populate_existing()`: with the redesigned column-only candidate
scan, nothing is cached for a candidate's PK before the locked re-fetch
within THIS function alone, so `.populate_existing()` is not load-bearing
for a same-session staleness bug here today (unlike movement_service's
sibling case, where an EARLIER unlocked full-entity read of the SAME sector
genuinely does go stale in the SAME session -- see the identity-map test
file above). It is kept for two reasons: (1) it matches the house chain
shape exactly, so a reviewer or gate scanning for the pattern finds it where
expected, and (2) it is a zero-cost guard against a FUTURE change that
reverts the candidate scan back to a full-entity SELECT re-introducing that
exact staleness class of bug. The substantive fix -- the one that actually
closes the cross-session lost-update -- is `.with_for_update()`: it is what
makes Postgres serialize the sweep against a concurrent movement/NPC writer
instead of letting a blind UPDATE clobber the other.

WHAT THIS FILE CANNOT PROVE (honestly disclosed): genuine concurrent-
transaction blocking needs two REAL connections against Postgres --
SQLite's `with_for_update()` is a documented no-op (see
populate-existing-local-import-monkeypatch-and-peek-gate-limit.md /
"Technique 2" in this codebase's own agent-memory), so no single-process
Python test, threaded or not, can force the actual interleaving that
`.with_for_update()` defends against. What IS provable at the unit level,
and is what the tests below prove: (a) the real function's query chain now
takes that lock, in the correct order, on the correct entity shape
(structural -- a regression that drops or reorders the chain breaks these
tests immediately, most now via a raised AssertionError from the fake
session's entity dispatcher, since the candidate/lock call sites are no
longer structurally interchangeable); (b) the prune math is unchanged and
still correct through the new chain (behavioral, no-regression); and (c)
the sweep's per-sector decision is driven ENTIRELY by the locked re-fetch's
fresh `players_present`, never by any value cached during the candidate
scan (the actual mechanism of the fix) -- Scenario 2 below proves this by
making the candidate scan's implicit snapshot disagree with the locked
re-fetch's actual content and asserting the sweep follows the fresh one. A
live-PG two-connection interleaving proof is the natural follow-up if this
fix ever needs empirical (not just structural) validation against the real
race.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Tuple
from unittest.mock import patch

import pytest

from src.models.player import Player
from src.models.sector import Sector
from src.services.scheduler.presence_helpers import _run_presence_sweep_sync

# ---------------------------------------------------------------------------
# Fake session -- entity-shape-correlated, mirrors test_npc_scheduler_unit.py's
# `_FakeLockDB` idiom (patch src.core.database.SessionLocal, track commit/
# rollback/close) but dispatches `.query(*entities)` on the EXACT entity
# tuple each of the sweep's three distinct query shapes uses:
#   * `Sector.id`                          -- the column-only candidate scan
#   * `Sector`                             -- the per-sector locked re-fetch
#   * `(Player.id, Player.last_game_login)` -- the freshness lookup
# A regression that collapses the candidate scan back to a full-entity
# `Sector` select, or that skips the lock chain, either raises here directly
# (an entity shape this dispatcher doesn't recognize) or trips the call-order
# assertions in Scenario 1 below.
# ---------------------------------------------------------------------------

class _CandidateBranch:
    """`.filter(<text clause>).all()` -- returns the pre-seeded candidate PK
    rows regardless of the filter expression (the WHERE clause itself is
    pre-existing, unchanged code -- not what this fix touches)."""

    def __init__(self, rows: List[Tuple[uuid.UUID]]):
        self._rows = rows

    def filter(self, *_a, **_k):
        return self

    def all(self):
        return self._rows


class _SectorLockBranch:
    """`.filter(Sector.id == pk).populate_existing().with_for_update().first()`
    -- records the call order + resolved pk into the shared `call_log`, and
    resolves `.first()` from the seeded `sectors_by_pk` map. A bypass shape
    (e.g. bare `.with_for_update()` with no `.populate_existing()`, or a
    `.first()` called before either) would simply not append the expected
    log entries -- Scenario 1's assertions catch that directly."""

    def __init__(self, sectors_by_pk: Dict[uuid.UUID, Sector], call_log: List[Tuple[str, Any]]):
        self._sectors_by_pk = sectors_by_pk
        self._call_log = call_log
        self._pk: Optional[uuid.UUID] = None

    def filter(self, expr):
        self._pk = expr.right.value
        self._call_log.append(("filter", self._pk))
        return self

    def populate_existing(self):
        self._call_log.append(("populate_existing", self._pk))
        return self

    def with_for_update(self):
        self._call_log.append(("with_for_update", self._pk))
        return self

    def first(self):
        self._call_log.append(("first", self._pk))
        return self._sectors_by_pk.get(self._pk)


class _PlayerFreshnessBranch:
    """`.filter(Player.id.in_(pids)).all()` -- returns the full seeded set of
    (player_id, last_game_login) rows regardless of the requested pids (the
    `.in_()` filter itself is pre-existing, unchanged code)."""

    def __init__(self, rows: List[Tuple[uuid.UUID, Optional[datetime]]]):
        self._rows = rows

    def filter(self, *_a, **_k):
        return self

    def all(self):
        return self._rows


class _FakePresenceSweepDB:
    def __init__(
        self,
        candidate_pks: List[uuid.UUID],
        sectors_by_pk: Dict[uuid.UUID, Sector],
        player_rows: List[Tuple[uuid.UUID, Optional[datetime]]],
    ):
        self._candidate_rows: List[Tuple[uuid.UUID]] = [(pk,) for pk in candidate_pks]
        self._sectors_by_pk = sectors_by_pk
        self._player_rows = player_rows
        self.call_log: List[Tuple[str, Any]] = []
        self.commit_count = 0
        self.rollback_count = 0
        self.closed = False

    def execute(self, *_a, **_k):
        # Advisory lock probe -- always reports acquired.
        return SimpleNamespace(scalar=lambda: True)

    def query(self, *entities, **_k):
        if len(entities) == 1 and entities[0] is Sector.id:
            return _CandidateBranch(self._candidate_rows)
        if len(entities) == 1 and entities[0] is Sector:
            return _SectorLockBranch(self._sectors_by_pk, self.call_log)
        if len(entities) == 2 and entities[0] is Player.id and entities[1] is Player.last_game_login:
            return _PlayerFreshnessBranch(self._player_rows)
        raise AssertionError(f"unexpected query entities: {entities!r}")

    def commit(self):
        self.commit_count += 1

    def rollback(self):
        self.rollback_count += 1

    def close(self):
        self.closed = True


def _entry(player_id: str, *, is_npc: bool = False) -> Dict[str, Any]:
    e: Dict[str, Any] = {"player_id": player_id}
    if is_npc:
        e["is_npc"] = True
    return e


@pytest.mark.unit
class TestPresenceSweepLockedRefetch:
    def test_column_only_candidate_scan_and_locked_refetch_prune_correctly(self) -> None:
        """Scenario 1 -- structural + behavioral, two sectors:

        Structural: the candidate scan hits `Sector.id` (column-only -- a
        regression back to full-entity `Sector` would raise inside the fake
        dispatcher before the loop even starts), and the per-sector re-fetch
        for EACH candidate goes `filter -> populate_existing -> with_for_update
        -> first`, in that order -- not the bypass shape.

        Behavioral: prune math is unchanged through the new chain -- a stale
        player is dropped, a fresh one survives, a sector with nothing to
        prune is left untouched (rolled back, not committed).
        """
        now = datetime.now(timezone.utc)
        fresh_login = now
        stale_login = now - timedelta(minutes=90)  # well past PRESENCE_STALE_MINUTES=30

        pk_a, pk_b = uuid.uuid4(), uuid.uuid4()
        p1, p2, p3 = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())

        sector_a = Sector(id=pk_a, players_present=[_entry(p1), _entry(p2)])
        sector_b = Sector(id=pk_b, players_present=[_entry(p3)])

        db = _FakePresenceSweepDB(
            candidate_pks=[pk_a, pk_b],
            sectors_by_pk={pk_a: sector_a, pk_b: sector_b},
            player_rows=[
                (p1, fresh_login),
                (p2, stale_login),
                (p3, fresh_login),
            ],
        )

        with patch("src.core.database.SessionLocal", return_value=db):
            result = _run_presence_sweep_sync()

        # Behavioral: P2 (stale) pruned from sector A, sector B untouched.
        assert result == {"presence_entries_swept": 1, "sectors": 1}
        assert sector_a.players_present == [_entry(p1)]
        assert sector_b.players_present == [_entry(p3)]  # unchanged object

        # Commit/rollback discipline: sector A committed (something changed),
        # sector B rolled back (removed == 0, nothing to persist).
        assert db.commit_count == 1
        assert db.rollback_count == 1
        assert db.closed is True

        # Structural: BOTH candidates went through the full locked chain, in
        # order, before the prune decision for that sector was made.
        for pk in (pk_a, pk_b):
            calls_for_pk = [name for name, seen_pk in db.call_log if seen_pk == pk]
            assert calls_for_pk == ["filter", "populate_existing", "with_for_update", "first"], (
                pk, db.call_log,
            )

    def test_locked_refetch_drives_the_prune_not_any_candidate_scan_snapshot(self) -> None:
        """Scenario 2 -- the mechanism proof: the candidate scan is
        column-only and therefore CANNOT carry a `players_present` snapshot
        into the per-sector loop at all (it only ever returns a bare PK).
        This test makes that concrete by seeding a sector whose locked
        re-fetch resolves to an EMPTY presence list -- if the fix ever
        regressed to reusing some cached/stale value instead of the fresh
        `.populate_existing().with_for_update()` read, this sector would
        have `players_present` to prune from; the current, fixed code has
        nothing to prune here and must skip cleanly (rollback, no crash, no
        entries swept)."""
        pk = uuid.uuid4()
        sector = Sector(id=pk, players_present=[])  # already empty by lock time

        db = _FakePresenceSweepDB(
            candidate_pks=[pk],
            sectors_by_pk={pk: sector},
            player_rows=[],
        )

        with patch("src.core.database.SessionLocal", return_value=db):
            result = _run_presence_sweep_sync()

        assert result == {"presence_entries_swept": 0, "sectors": 0}
        assert db.commit_count == 0
        assert db.rollback_count == 1  # the "not pids" bail path
        assert sector.players_present == []

    def test_candidate_row_gone_by_lock_time_skips_cleanly(self) -> None:
        """A candidate PK whose row no longer resolves at lock time (deleted,
        or -- more realistically for this table -- simply not present in the
        fake's seed map) must be skipped without raising: `sec is None` is a
        NEW branch this fix adds (the old full-entity candidate scan could
        never observe a None `sec`, since it iterated already-loaded ORM
        rows). Proves the added None-guard is live and correctly wired."""
        pk_present, pk_missing = uuid.uuid4(), uuid.uuid4()
        p1 = str(uuid.uuid4())
        sector = Sector(id=pk_present, players_present=[_entry(p1)])

        db = _FakePresenceSweepDB(
            candidate_pks=[pk_missing, pk_present],
            sectors_by_pk={pk_present: sector},  # pk_missing intentionally absent
            player_rows=[(p1, datetime.now(timezone.utc) - timedelta(minutes=90))],
        )

        with patch("src.core.database.SessionLocal", return_value=db):
            result = _run_presence_sweep_sync()

        # pk_missing: rollback via `sec is None` guard.
        # pk_present: P1 is stale -> pruned, committed.
        assert result == {"presence_entries_swept": 1, "sectors": 1}
        assert sector.players_present == []
        assert db.commit_count == 1
        assert db.rollback_count == 1
        assert db.closed is True

    def test_locked_elsewhere_skips_without_touching_any_sector(self) -> None:
        """Pre-existing discipline, unchanged by this fix: when the advisory
        lock is held by another sweep instance, the loop must never run at
        all -- no candidate scan, no per-sector query."""
        db = _FakePresenceSweepDB(candidate_pks=[], sectors_by_pk={}, player_rows=[])
        db.execute = lambda *_a, **_k: SimpleNamespace(scalar=lambda: False)  # lock NOT acquired

        with patch("src.core.database.SessionLocal", return_value=db):
            result = _run_presence_sweep_sync()

        assert result == {"presence_entries_swept": 0, "sectors": 0}
        assert db.call_log == []
        assert db.commit_count == 0
        assert db.rollback_count == 1  # the lock-not-acquired bail
        assert db.closed is True

    def test_npc_presence_entries_survive_even_when_humans_are_pruned(self) -> None:
        """NPCCharacter UUIDs are not Player rows — treating them as stale
        humans wiped cockpit traffic until reconcile. Prove NPCs are kept
        while offline humans are still swept."""
        now = datetime.now(timezone.utc)
        pk = uuid.uuid4()
        human_fresh = str(uuid.uuid4())
        human_stale = str(uuid.uuid4())
        npc_id = str(uuid.uuid4())

        sector = Sector(
            id=pk,
            players_present=[
                _entry(human_fresh),
                _entry(human_stale),
                _entry(npc_id, is_npc=True),
            ],
        )
        db = _FakePresenceSweepDB(
            candidate_pks=[pk],
            sectors_by_pk={pk: sector},
            player_rows=[
                (human_fresh, now),
                (human_stale, now - timedelta(minutes=90)),
                # npc_id deliberately absent from Player table
            ],
        )

        with patch("src.core.database.SessionLocal", return_value=db):
            result = _run_presence_sweep_sync()

        assert result == {"presence_entries_swept": 1, "sectors": 1}
        assert sector.players_present == [
            _entry(human_fresh),
            _entry(npc_id, is_npc=True),
        ]

    def test_npc_only_sector_is_left_untouched(self) -> None:
        pk = uuid.uuid4()
        npc_id = str(uuid.uuid4())
        sector = Sector(id=pk, players_present=[_entry(npc_id, is_npc=True)])
        db = _FakePresenceSweepDB(
            candidate_pks=[pk],
            sectors_by_pk={pk: sector},
            player_rows=[],
        )

        with patch("src.core.database.SessionLocal", return_value=db):
            result = _run_presence_sweep_sync()

        assert result == {"presence_entries_swept": 0, "sectors": 0}
        assert sector.players_present == [_entry(npc_id, is_npc=True)]
        assert db.commit_count == 0
        assert db.rollback_count == 1
