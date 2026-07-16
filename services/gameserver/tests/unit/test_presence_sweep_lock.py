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
from src.services.scheduler.presence_helpers import (
    _heal_candidates_query,
    _removal_candidate_scan_query,
    _removal_freshness_lookup_query,
    _removal_locked_refetch_query,
    _run_presence_sweep_sync,
)

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
    (the removal loop's lock key) OR
    `.filter(Sector.sector_id == sid).populate_existing().with_for_update()
    .first()` (P0-FIX-SWEEP-HEAL's own lock key, a DIFFERENT column on the
    SAME entity) -- records the call order + resolved key into the shared
    `call_log`, and resolves `.first()` from whichever seeded map matches
    the filtered column (`expr.left.key` -- "id" vs "sector_id"). A bypass
    shape (e.g. bare `.with_for_update()` with no `.populate_existing()`, or
    a `.first()` called before either) would simply not append the expected
    log entries -- Scenario 1's assertions catch that directly."""

    def __init__(
        self,
        sectors_by_pk: Dict[uuid.UUID, Sector],
        call_log: List[Tuple[str, Any]],
        sectors_by_sector_id: Optional[Dict[int, Sector]] = None,
    ):
        self._sectors_by_pk = sectors_by_pk
        self._sectors_by_sector_id = sectors_by_sector_id or {}
        self._call_log = call_log
        self._pk: Optional[Any] = None
        self._key_col: str = "id"

    def filter(self, expr):
        self._key_col = expr.left.key
        self._pk = expr.right.value
        self._call_log.append(("filter", self._key_col, self._pk))
        return self

    def populate_existing(self):
        self._call_log.append(("populate_existing", self._key_col, self._pk))
        return self

    def with_for_update(self):
        self._call_log.append(("with_for_update", self._key_col, self._pk))
        return self

    def first(self):
        self._call_log.append(("first", self._key_col, self._pk))
        if self._key_col == "sector_id":
            return self._sectors_by_sector_id.get(self._pk)
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


class _FreshPlayersForHealBranch:
    """P0-FIX-SWEEP-HEAL's own candidate query --
    `.query(Player.id, Player.current_sector_id,
    Player.display_name_expr(User.username), Player.current_ship_id,
    Player.team_id, Player.intrasystem_pose, Player.last_game_login)
    .join(User, Player.user_id == User.id)
    .filter(current_sector_id.isnot(None)).all()` --
    returns the full seeded set regardless of the requested join/filter
    (both are pre-existing-shape, unchanged-by-this-fake code; freshness is
    decided in PYTHON by _is_presence_fresh, not a SQL WHERE clause -- seed
    the 7th element per row to exercise that; the 3rd element is a plain
    seeded username string standing in for what `display_name_expr` would
    resolve to -- this fake never runs real SQLAlchemy coercion, see
    TestHealQueryRealSQLAlchemy below for the test that does). This fake
    only proves the SWEEP's own reconciliation logic given a candidate set.

    2026-07-16 crash fix (Player.username is a Python @property, not a
    Column -- it cannot appear in a real `.query()` column list; SQLAlchemy
    raises ArgumentError at query-BUILD time, live-confirmed): the real
    query now joins User and selects `Player.display_name_expr(User.username)`
    in its place, hence the added no-op `.join()` below."""

    def __init__(self, rows: List[Tuple[Any, ...]]):
        self._rows = rows

    def join(self, *_a, **_k):
        return self

    def outerjoin(self, *_a, **_k):
        return self

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
        sectors_by_sector_id: Optional[Dict[int, Sector]] = None,
        fresh_players_for_heal: Optional[List[Tuple[Any, ...]]] = None,
        raise_on_heal_query: bool = False,
    ):
        self._candidate_rows: List[Tuple[uuid.UUID]] = [(pk,) for pk in candidate_pks]
        self._sectors_by_pk = sectors_by_pk
        self._sectors_by_sector_id = sectors_by_sector_id or {}
        self._player_rows = player_rows
        self._fresh_players_for_heal = fresh_players_for_heal or []
        # 2026-07-16 hardening: simulates a construction-time crash in the
        # heal candidate query (exactly what the live Player.username bug
        # did) -- proves _run_presence_sweep_sync's heal-phase exception
        # isolation actually degrades gracefully instead of aborting the
        # whole sweep. See TestHealPhaseExceptionIsolation below.
        self._raise_on_heal_query = raise_on_heal_query
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
            return _SectorLockBranch(self._sectors_by_pk, self.call_log, self._sectors_by_sector_id)
        # entities[1] used to be the bare Player.last_game_login Column; it
        # is now func.greatest(Player.last_activity_at, Player.
        # last_game_login) (QUEUE-LIVENESS-SIGNAL, 2026-07-16) -- an
        # expression, not the same object, so this dispatches on shape
        # (2-tuple keyed by Player.id) rather than the 2nd column's identity.
        if len(entities) == 2 and entities[0] is Player.id:
            return _PlayerFreshnessBranch(self._player_rows)
        # QUEUE-HEAL-ENTRY-SHAPE (2026-07-16): grew from 7 to 9 entities --
        # trailing Ship.name/Ship.type added via an outer join so the heal
        # pass can stop hardcoding ship_name/ship_type to "None".
        if len(entities) == 9 and entities[0] is Player.id and entities[1] is Player.current_sector_id:
            if self._raise_on_heal_query:
                raise RuntimeError("simulated heal-candidate-query construction crash")
            return _FreshPlayersForHealBranch(self._fresh_players_for_heal)
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


# A last_game_login well within PRESENCE_STALE_MINUTES of "now" -- fresh by
# construction for every TestPresenceSweepHeal seed below (the heal pass's
# own freshness gate, _is_presence_fresh, is exercised directly by
# test_stale_player_is_not_healed_and_no_flapping_across_the_same_tick).
_FRESH_LOGIN = datetime.now(timezone.utc)


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
        assert result == {
            "presence_entries_swept": 1, "sectors": 1,
            "presence_entries_healed": 0, "heal_sectors": 0,
        }
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
            calls_for_pk = [name for name, _key_col, seen_pk in db.call_log if seen_pk == pk]
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

        assert result == {
            "presence_entries_swept": 0, "sectors": 0,
            "presence_entries_healed": 0, "heal_sectors": 0,
        }
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
        assert result == {
            "presence_entries_swept": 1, "sectors": 1,
            "presence_entries_healed": 0, "heal_sectors": 0,
        }
        assert sector.players_present == []
        assert db.commit_count == 1
        assert db.rollback_count == 1
        assert db.closed is True

    def test_locked_elsewhere_skips_without_touching_any_sector(self) -> None:
        """Pre-existing discipline, unchanged by this fix: when the advisory
        lock is held by another sweep instance, the loop must never run at
        all -- no candidate scan, no per-sector query. This is the ONE bail
        path that returns BEFORE either the removal or the P0-FIX-SWEEP-HEAL
        loop, so its result dict keeps the original 2-key shape."""
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

        assert result == {
            "presence_entries_swept": 1, "sectors": 1,
            "presence_entries_healed": 0, "heal_sectors": 0,
        }
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

        assert result == {
            "presence_entries_swept": 0, "sectors": 0,
            "presence_entries_healed": 0, "heal_sectors": 0,
        }
        assert sector.players_present == [_entry(npc_id, is_npc=True)]
        assert db.commit_count == 0
        assert db.rollback_count == 1


@pytest.mark.unit
class TestPresenceSweepHeal:
    """P0-FIX-SWEEP-HEAL (Max two-seat repro, 2026-07-16): reconciles MISSING
    or pose-less HUMAN presence entries from Player.current_sector_id, in
    ADDITION to (never instead of) the removal pass above -- a completely
    separate candidate set and lock key (Sector.sector_id, not Sector.id),
    deliberately not folded into the removal loop so its already-tested
    NPC-preservation logic (311115e1) stays untouched (see
    test_npc_entries_untouched_by_heal_pass / test_heal_and_removal_coexist
    below for direct proof of that non-interference)."""

    def test_heals_a_missing_human_entry_with_pose(self) -> None:
        """Live repro #2: a diagnostic teleport bypassed presence entirely
        -- the sector had ZERO entries, not even the human's own. Also
        exercises QUEUE-HEAL-ENTRY-SHAPE's live bug directly: the ORIGINAL
        repro had a correct ship_id but null ship_name/ship_type -- seeded
        here with real ship data to prove the fix populates both."""
        sid = 42
        sector = Sector(id=uuid.uuid4(), sector_id=sid, players_present=[])
        pid = uuid.uuid4()
        ship_id = uuid.uuid4()
        ship_type = SimpleNamespace(name="LIGHT_FREIGHTER")  # stands in for the ShipType enum member
        pose = {"x_pct": 10.0, "y_pct": 20.0, "heading_deg": 0.0, "phase": "idle", "burning": False, "leg": None}

        db = _FakePresenceSweepDB(
            candidate_pks=[],  # nothing for the removal loop -- presence starts empty
            sectors_by_pk={},
            player_rows=[],
            sectors_by_sector_id={sid: sector},
            fresh_players_for_heal=[
                (pid, sid, "sweepclean", ship_id, None, pose, _FRESH_LOGIN, "Nomad", ship_type),
            ],
        )

        with patch("src.core.database.SessionLocal", return_value=db):
            result = _run_presence_sweep_sync()

        assert result["presence_entries_healed"] == 1
        assert result["heal_sectors"] == 1
        assert len(sector.players_present) == 1
        entry = sector.players_present[0]
        assert entry["player_id"] == str(pid)
        assert entry["username"] == "sweepclean"
        assert entry.get("is_npc") is None  # never marked as NPC
        assert entry["pose"]["x_pct"] == 10.0
        assert entry["pose"]["phase"] == "idle"
        # QUEUE-HEAL-ENTRY-SHAPE: ship_id was already correct pre-fix; the
        # bug was ship_name/ship_type hardcoded to "None" despite the join
        # data being available -- both must now be populated.
        assert entry["ship_id"] == str(ship_id)
        assert entry["ship_name"] == "Nomad"
        assert entry["ship_type"] == "LIGHT_FREIGHTER"

    def test_completes_an_existing_pose_less_human_entry(self) -> None:
        """Live repro #1: ensure_player_pose's lazy create-on-GET never
        mirrors -- the entry EXISTS but has no `pose` key at all."""
        sid = 7
        pid = uuid.uuid4()
        existing = {
            "player_id": str(pid), "username": "Shouden", "ship_id": None,
            "ship_name": "None", "ship_type": "None", "team_id": None,
            "arrived_at": "2026-07-16T00:00:00+00:00",
        }
        sector = Sector(id=uuid.uuid4(), sector_id=sid, players_present=[existing])
        pose = {"x_pct": 55.0, "y_pct": 33.0, "heading_deg": 90.0, "phase": "idle", "burning": False, "leg": None}

        db = _FakePresenceSweepDB(
            candidate_pks=[],
            sectors_by_pk={},
            player_rows=[],
            sectors_by_sector_id={sid: sector},
            fresh_players_for_heal=[(pid, sid, "Shouden", None, None, pose, _FRESH_LOGIN, None, None)],
        )

        with patch("src.core.database.SessionLocal", return_value=db):
            result = _run_presence_sweep_sync()

        assert result["presence_entries_healed"] == 1
        assert len(sector.players_present) == 1  # completed IN PLACE, not duplicated
        assert sector.players_present[0]["pose"]["x_pct"] == 55.0
        assert sector.players_present[0]["username"] == "Shouden"  # other fields preserved

    def test_npc_entries_untouched_by_heal_pass(self) -> None:
        sid = 9
        npc_id = str(uuid.uuid4())
        sector = Sector(id=uuid.uuid4(), sector_id=sid, players_present=[_entry(npc_id, is_npc=True)])

        db = _FakePresenceSweepDB(
            candidate_pks=[],
            sectors_by_pk={},
            player_rows=[],
            sectors_by_sector_id={sid: sector},
            fresh_players_for_heal=[],  # no active human players in this sector
        )

        with patch("src.core.database.SessionLocal", return_value=db):
            result = _run_presence_sweep_sync()

        assert result["presence_entries_healed"] == 0
        assert sector.players_present == [_entry(npc_id, is_npc=True)]

    def test_pose_less_npc_entry_is_not_touched_by_the_heal_pass_even_if_a_human_shares_the_sector(self) -> None:
        """The heal pass's by_pid index is built from non-NPC entries only
        -- an NPC entry must never be matched/overwritten by a human's own
        heal, even when both are present in the same sector."""
        sid = 11
        npc_id = str(uuid.uuid4())
        pid = uuid.uuid4()
        sector = Sector(id=uuid.uuid4(), sector_id=sid, players_present=[_entry(npc_id, is_npc=True)])
        pose = {"x_pct": 5.0, "y_pct": 6.0, "heading_deg": 0.0, "phase": "idle", "burning": False, "leg": None}

        db = _FakePresenceSweepDB(
            candidate_pks=[],
            sectors_by_pk={},
            player_rows=[],
            sectors_by_sector_id={sid: sector},
            fresh_players_for_heal=[(pid, sid, "NewArrival", None, None, pose, _FRESH_LOGIN, None, None)],
        )

        with patch("src.core.database.SessionLocal", return_value=db):
            result = _run_presence_sweep_sync()

        assert result["presence_entries_healed"] == 1
        assert sector.players_present[0] == _entry(npc_id, is_npc=True)  # NPC entry byte-identical
        assert sector.players_present[1]["player_id"] == str(pid)

    def test_heal_and_removal_coexist_in_the_same_sweep_run(self) -> None:
        """Integration proof: one sweep pass both prunes a stale human from
        sector A (removal loop, untouched logic) AND heals a missing human
        in sector B (new heal loop) -- the two loops don't interfere."""
        now = datetime.now(timezone.utc)
        pk_a = uuid.uuid4()
        sid_b = 55
        sector_a = Sector(id=pk_a, players_present=[_entry(str(uuid.uuid4()))])
        stale_human = sector_a.players_present[0]["player_id"]
        sector_b = Sector(id=uuid.uuid4(), sector_id=sid_b, players_present=[])
        missing_pid = uuid.uuid4()
        pose = {"x_pct": 1.0, "y_pct": 2.0, "heading_deg": 0.0, "phase": "idle", "burning": False, "leg": None}

        db = _FakePresenceSweepDB(
            candidate_pks=[pk_a],
            sectors_by_pk={pk_a: sector_a},
            player_rows=[(stale_human, now - timedelta(minutes=90))],
            sectors_by_sector_id={sid_b: sector_b},
            fresh_players_for_heal=[(missing_pid, sid_b, "New Player", None, None, pose, now, None, None)],
        )

        with patch("src.core.database.SessionLocal", return_value=db):
            result = _run_presence_sweep_sync()

        assert result == {
            "presence_entries_swept": 1, "sectors": 1,
            "presence_entries_healed": 1, "heal_sectors": 1,
        }
        assert sector_a.players_present == []
        assert len(sector_b.players_present) == 1
        assert sector_b.players_present[0]["player_id"] == str(missing_pid)

    def test_stale_player_is_not_healed_and_no_flapping_across_the_same_tick(self) -> None:
        """Hub-ruled invariant (2026-07-16): heal and prune consume the SAME
        _is_presence_fresh predicate, so within ONE sweep tick a stale
        player's entry ends up ABSENT (pruned by the removal pass, never
        re-created by the heal pass) and a fresh player sharing the SAME
        sector ends up present EXACTLY ONCE (pose completed by the heal
        pass, not duplicated) -- no oscillation between the two passes."""
        now = datetime.now(timezone.utc)
        stale_login = now - timedelta(minutes=90)
        sid = 88
        pk = uuid.uuid4()
        fresh_pid = uuid.uuid4()
        stale_pid = uuid.uuid4()
        fresh_pose = {"x_pct": 3.0, "y_pct": 4.0, "heading_deg": 0.0, "phase": "idle", "burning": False, "leg": None}

        # Both players already have an entry in this ONE sector -- the fresh
        # one's is missing its pose key, the stale one's is otherwise
        # complete but stale. The SAME Sector object is registered under
        # BOTH lock keys (id for the removal pass, sector_id for the heal
        # pass) so mutations from one pass are visible to the other within
        # this single tick, exactly as the real shared row would be.
        sector = Sector(
            id=pk, sector_id=sid,
            players_present=[_entry(str(fresh_pid)), _entry(str(stale_pid))],
        )

        db = _FakePresenceSweepDB(
            candidate_pks=[pk],
            sectors_by_pk={pk: sector},
            player_rows=[(str(fresh_pid), now), (str(stale_pid), stale_login)],
            sectors_by_sector_id={sid: sector},
            fresh_players_for_heal=[
                (fresh_pid, sid, "fresh-player", None, None, fresh_pose, now, None, None),
                (stale_pid, sid, "stale-player", None, None, fresh_pose, stale_login, None, None),
            ],
        )

        with patch("src.core.database.SessionLocal", return_value=db):
            result = _run_presence_sweep_sync()

        remaining_ids = [e["player_id"] for e in sector.players_present]
        assert remaining_ids.count(str(fresh_pid)) == 1  # present exactly once
        assert str(stale_pid) not in remaining_ids  # absent -- pruned, never re-healed
        assert sector.players_present[0]["pose"]["x_pct"] == 3.0
        assert result["presence_entries_swept"] == 1  # stale_pid pruned
        assert result["presence_entries_healed"] == 1  # fresh_pid's pose completed


@pytest.mark.unit
class TestHealQueryRealSQLAlchemyCoercion:
    """2026-07-16 live crash (Max, direct invocation on the deployed host):
    ``sqlalchemy.exc.ArgumentError: Column expression, FROM clause, or other
    columns clause element expected, got <property object ...>`` at
    coercions.py:696 -- ``_heal_missing_or_poseless_presence_sync``'s
    candidate query selected ``Player.username`` as a column, but
    ``username`` is a plain Python ``@property`` on ``Player``, not a
    mapped Column: it resolves fine on an already-loaded instance but
    cannot appear in a ``session.query(...)`` column list. Every
    ``_FakePresenceSweepDB``-backed test above (115/115 green at the time)
    never caught this: the fake's ``.query()`` dispatcher pattern-matches
    `db.query(*entities)` calls by entity-TUPLE IDENTITY, so it happily
    accepted ``Player.username`` as a valid dispatch key without ever
    routing through real SQLAlchemy's column-expression coercion --
    meaning EVERY sweep run was crashing live (no pruning, no healing)
    despite full green here. That is a structural blind spot of the
    FakeSession idiom used throughout this file, not a one-off gap.

    This class closes it: ``_heal_candidates_query`` (the query-
    construction half of the heal pass, split out from
    ``_heal_missing_or_poseless_presence_sync`` specifically for this test)
    is called against a REAL ``sqlalchemy.orm.Session`` bound to an
    in-memory SQLite engine -- no ``SessionLocal`` patch, no entity-shape
    dispatcher. Coercion fires at query-BUILD time (before any ``.all()``/
    execute), so no table creation is needed to catch this class of bug --
    the first test below reproduces the ORIGINAL crash verbatim (bare
    ``Player.username`` in the column list) to prove this harness actually
    detects it, and the second proves the shipped fix (``Player.
    display_name_expr(User.username)`` + ``.join(User, ...)``) builds
    clean. `.all()`/table creation is deliberately NOT attempted here:
    ``Player`` carries Postgres-only ``UUID``/``JSONB``/``ARRAY`` columns
    that fail SQLite DDL (the same blocker already documented against
    Player in test_storage_deposit_prelock_identity_map.py and against
    Sector in test_movement_presence_lock_identity_map.py) -- construction-
    time coercion is exactly where this bug lives and exactly where this
    proof stops, consistent with that established codebase precedent."""

    @staticmethod
    def _real_session():
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker

        engine = create_engine("sqlite://")
        return sessionmaker(bind=engine)()

    def test_bare_property_column_reproduces_the_live_crash(self) -> None:
        """Harness self-check: proves this real-SQLAlchemy proof actually
        catches the bug class -- the ORIGINAL (pre-fix) query shape must
        raise ArgumentError at construction, not merely at execution."""
        from sqlalchemy.exc import ArgumentError

        db = self._real_session()
        try:
            with pytest.raises(ArgumentError):
                db.query(
                    Player.id, Player.current_sector_id, Player.username,
                    Player.current_ship_id, Player.team_id,
                    Player.intrasystem_pose, Player.last_game_login,
                ).filter(Player.current_sector_id.isnot(None))
        finally:
            db.close()

    def test_heal_candidates_query_builds_clean_against_real_sqlalchemy(self) -> None:
        """The shipped fix: constructing the REAL heal candidate query
        (imported from production code, not a copy) against a real Session
        must not raise -- and must compile to SQL that actually joins
        users, selects a coalesce expression for the display name in place
        of the bare property, selects a greatest() expression for the
        liveness signal (QUEUE-LIVENESS-SIGNAL, 2026-07-16 -- GREATEST over
        COALESCE so a fresh re-login always wins over a stale activity
        touch, see _is_presence_fresh's own doc-comment), and OUTER-joins
        ships for the name/type columns (QUEUE-HEAL-ENTRY-SHAPE, 2026-07-16
        -- outer, not inner, so a candidate with no current ship still
        surfaces with NULL name/type rather than being dropped).
        Construction-only (no execute), so SQLite's lack of a native
        GREATEST never surfaces here -- this dev stack is Postgres-only."""
        db = self._real_session()
        try:
            query = _heal_candidates_query(db)
            compiled = str(query)  # forces full compilation, not just construction
        finally:
            db.close()

        assert "JOIN users" in compiled
        assert "coalesce" in compiled.lower()
        assert "greatest" in compiled.lower()
        assert "LEFT OUTER JOIN ships" in compiled
        assert "players.current_sector_id IS NOT NULL" in compiled

    def test_removal_loop_queries_build_clean_against_real_sqlalchemy(self) -> None:
        """Same real-engine build/compile norm applied to the removal
        pass's three query-construction sites (2026-07-16 crash-fix DoD
        hardening: 'every ORM query-construction path in the code touched
        gets a real-engine test', not just the property-as-column one that
        actually crashed). None of these select a @property -- `Sector.id`,
        `Sector` (full entity), `Player.id`/`Player.last_game_login` are
        all real mapped Columns -- so this is a construction-safety floor,
        not a bug repro."""
        db = self._real_session()
        try:
            candidate_sql = str(_removal_candidate_scan_query(db))
            refetch_sql = str(_removal_locked_refetch_query(db, uuid.uuid4()))
            freshness_sql = str(_removal_freshness_lookup_query(db, [uuid.uuid4()]))
        finally:
            db.close()

        assert "sectors" in candidate_sql
        assert "jsonb_array_length" in candidate_sql
        assert "sectors" in refetch_sql
        assert "players" in freshness_sql
        assert "greatest" in freshness_sql.lower()  # QUEUE-LIVENESS-SIGNAL swap
        assert "IN" in freshness_sql.upper()


@pytest.mark.unit
class TestHealPhaseExceptionIsolation:
    """2026-07-16 crash-fix DoD hardening (hub-added): 'a crash in ANY
    phase must never leave prune-applied-heal-skipped' -- the exact live
    incident (17:28/17:5x, Max direct invocation): the removal/prune pass
    committed its per-sector work, THEN the heal candidate query crashed
    UNCAUGHT, aborting the whole `_run_presence_sweep_sync` call with no
    logged result for that tick.

    Fix: (1) heal now runs BEFORE the removal/prune pass (own doc-comment
    on `_run_presence_sweep_sync`, 'ORDERING'), and (2) heal's own
    candidate-query construction is wrapped in a try/except that degrades
    to '0 healed, 0 sectors, logged' instead of propagating (own
    doc-comment on `_heal_missing_or_poseless_presence_sync`). Together
    these mean a heal-phase crash can no longer prevent the prune pass from
    running in the SAME tick -- proven below via `raise_on_heal_query`,
    which reproduces the exact failure MODE (an exception raised at
    `db.query(...)` construction time for the heal candidate shape),
    independent of which specific bug causes it."""

    def test_heal_phase_crash_does_not_abort_the_removal_prune_pass(self) -> None:
        now = datetime.now(timezone.utc)
        stale_login = now - timedelta(minutes=90)
        pk = uuid.uuid4()
        stale_pid = str(uuid.uuid4())
        sector = Sector(id=pk, players_present=[_entry(stale_pid)])

        db = _FakePresenceSweepDB(
            candidate_pks=[pk],
            sectors_by_pk={pk: sector},
            player_rows=[(stale_pid, stale_login)],
            raise_on_heal_query=True,
        )

        with patch("src.core.database.SessionLocal", return_value=db):
            result = _run_presence_sweep_sync()  # must NOT raise

        # The removal/prune pass still ran and did its job even though the
        # heal phase crashed at construction time -- the exact "prune-
        # applied-heal-skipped" state is now REVERSED in severity: heal is
        # cleanly skipped/logged (0, not corrupted), prune still completes.
        assert result["presence_entries_swept"] == 1
        assert result["sectors"] == 1
        assert result["presence_entries_healed"] == 0
        assert result["heal_sectors"] == 0
        assert sector.players_present == []  # stale entry still pruned
        assert db.closed is True

    def test_heal_phase_crash_is_rolled_back_cleanly_before_prune_starts(self) -> None:
        """Structural half: the heal-phase crash triggers a db.rollback()
        (own doc-comment on _heal_missing_or_poseless_presence_sync) BEFORE
        the removal pass's own candidate scan runs -- proves the session
        isn't left in a poisoned/uncommitted state that could corrupt the
        prune pass's own transaction handling."""
        db = _FakePresenceSweepDB(
            candidate_pks=[],
            sectors_by_pk={},
            player_rows=[],
            raise_on_heal_query=True,
        )

        with patch("src.core.database.SessionLocal", return_value=db):
            result = _run_presence_sweep_sync()

        assert result == {
            "presence_entries_swept": 0, "sectors": 0,
            "presence_entries_healed": 0, "heal_sectors": 0,
        }
        # One rollback from the heal-phase crash handler; the (empty)
        # removal pass then finds nothing to prune and never commits.
        assert db.rollback_count == 1
        assert db.commit_count == 0
