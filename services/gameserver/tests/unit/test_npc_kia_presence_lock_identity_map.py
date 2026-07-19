"""Regression pin for WO-NPC-KIA-PRESENCE (npc_spawn_service.
handle_npc_ship_destroyed) -- the last unprotected writer of the
``players_present`` JSONB (PRESENCE-TWIN gate, cipher HIGH; mack confirmed
atomicity/non-vacuousness and found a third reentrancy chain the original
comment undercounted -- folded in below). Closes the same identity-map
staleness class npc_movement_service._locked_sectors' own WO-NPC-
PRESENCE-TWIN fix already closed one level up (see
test_npc_presence_lock_identity_map.py), one level deeper: the KIA
handler's OWN Sector re-lock.

PREMISE: THREE distinct call chains reach this re-lock in the SAME
request-scoped session, each caching the target Sector row UNLOCKED
earlier before ever calling into ``handle_npc_ship_destroyed`` (exhaustive
caller trace -- see the production comment at npc_spawn_service.py
~:1224-1270 for the full enumeration):

  (a) DIRECT -- combat_service.CombatService.attack_npc_ship (~:1267) and
      .npc_attack_player (~:1814) each read this SAME Sector row UNLOCKED
      in their own body (feeding a combat-log region-snapshot / sector-
      defenses read).
  (b) POLICE-COMBAT -- npc_engagement_service._sweep_one -> _place_squad
      -> _maybe_initiate_police_combat -> npc_combat_initiation_service.
      initiate_npc_combat -> npc_attack_player.
  (c) PIRATE-AGGRESSION -- movement_service._maybe_initiate_pirate_combat
      (~:2711) -> the SAME initiate_npc_combat -> npc_attack_player.

That function's OWN ``players_present`` read-modify-write then re-locked
the SAME row with a bare ``.with_for_update()`` -- no
``.populate_existing()`` -- so a concurrent presence writer's committed
change (another player entering the sector) was invisible to the RMW,
silently clobbered by the overwrite. Exploit: a player killing an NPC in a
trafficked sector ghosts another player's presence entry (evades sector-
scan/COMMS detection).

VERIFY-FIRST finding (this campaign's own doctrine -- "no naive
``.populate_existing()`` guard survives without a whole-row +
caller-reentrancy trace"): naive-only is NOT safe. FLUSH-FIRST is
required. Trace: ``npc_engagement_service._sweep_one``'s ARRIVED branch
(per-engagement SAVEPOINT, WO-B1/B2) calls ``_place_squad`` -- which
places the squad in THIS SAME dest sector via ``npc_movement_service.
add_npc_presence``, UNFLUSHED after the last officer's iteration of
``_locked_sectors``' own internal flush-then-lock -- immediately followed,
in the SAME uncommitted transaction, by ``_maybe_initiate_police_combat``
-> ``npc_combat_initiation_service.initiate_npc_combat`` ->
``CombatService.npc_attack_player``, which reaches ``handle_npc_ship_
destroyed``'s re-lock on that SAME sector if the squad's officer loses the
fight. A bare ``.populate_existing()`` re-lock there would DISCARD the
just-placed officer's pending presence entry -- the identical squad-loop
self-clobber class of bug ``_locked_sectors``' own fix closes one level
up. Both ``attack_npc_ship`` and ``npc_attack_player``'s OWN bodies are
read-only on the Sector between their unlocked peek and this call
(confirmed: no pre-lock mutation of their own -- ``npc_attack_player``
happens to flush before reaching this call anyway via its own combat_log
add, but that is an incidental property of ONE caller's implementation,
not a guarantee); the ``_place_squad`` reentrancy trace above proves a
genuine flush-first requirement exists regardless of that coincidence.
Fix: ``db.flush()`` as the first statement of the ``if sector_id is not
None:`` locking block, before ``.populate_existing().with_for_update()``
-- mirrors ``npc_movement_service._locked_sectors``' identical precedent
for this exact bug class. Chain (c) PIRATE-AGGRESSION has NO pre-lock
Sector mutation of its own (grepped: no ``add_npc_presence`` call, or any
Sector mutation at all, anywhere in ``_maybe_initiate_pirate_combat``'s
body -- pirate rosters are static/seeded, not placed per-engagement like a
police squad) -- so the flush is covered-but-not-load-bearing there. The
fix is caller-agnostic by construction (the flush always runs, regardless
of which of the three chains reaches this point), which is exactly why it
covers all three -- and any future fourth chain -- without needing to
re-audit this site per new caller.

Sections (mirroring test_npc_presence_lock_identity_map.py / test_siege_
skim_flush_first_straggler.py's own three-part structure):

  A) Generalized real-SQLAlchemy identity-map staleness/fix mechanism
     proof against a minimal Sector mirror class (real ``Sector.
     players_present`` is Postgres-only JSONB; ``Sector.__table__.
     create()`` fails on SQLite, the same blocker documented in those two
     files). Permanent regression marker, independent of this WO's own
     code.

  B) The REAL, unmodified ``npc_spawn_service.handle_npc_ship_destroyed``,
     driven via a fake session in the SAME MagicMock-dispatch-by-model
     style test_pirate_ecosystem_wire.py already established for this
     exact function (real ``NPCCharacter``/``Sector`` model INSTANCES as
     plain data containers -- no real engine; ``NPCCharacter`` also
     carries Postgres-only UUID/JSONB columns that would block a real
     SQLite session, same blocker as Part A), extended with a small
     stateful Sector-query fake reproducing genuine identity-map refresh
     semantics -- the one property a static ``MagicMock.return_value``
     cannot model: (1) the core WO ask -- a concurrent committed presence
     write is visible to the KIA re-lock, not the stale unlocked-read
     snapshot (chains (a)/(c)'s shape); (2) chain (c) PIRATE-AGGRESSION
     specifically -- the same staleness proof, framed around a pirate NPC
     and asserting NOTHING is pending on this chain (has_pending_write
     stays False), so coverage matches the production comment's exhaustive
     three-chain enumeration; (3) the FLUSH-FIRST half -- a squad-loop-
     shaped pending same-session presence write on the SAME sector (chain
     (b)'s shape) survives the KIA re-lock rather than being discarded;
     (4) revert-probes (populate_existing / flush disabled independently)
     proving these properties are genuinely exercised by the REAL
     production control flow, not harness luck -- without the
     corresponding fix behavior, the same scenarios show the stale/
     discarded values instead.

  C) Structural AST pin: ``db.flush()`` is the first statement inside the
     ``if sector_id is not None:`` locking block, and
     ``.populate_existing()`` precedes ``.with_for_update()`` in the
     Sector query chain -- independent of Part B's mechanism proof, which
     would not notice a regression that reorders or removes the flush
     without changing this test's specific inputs.
"""
from __future__ import annotations

import ast
import inspect
import textwrap
import uuid
from contextlib import contextmanager
from unittest.mock import MagicMock

import pytest
from sqlalchemy import Column, Integer, JSON, create_engine
from sqlalchemy.orm import declarative_base, sessionmaker
from sqlalchemy.pool import StaticPool

import src.services.npc_spawn_service as npc_spawn_service_module
from src.models.npc_character import NPCArchetype, NPCCharacter, NPCStatus
from src.models.sector import Sector
from src.services import npc_spawn_service

_Base = declarative_base()


class _MirrorSector(_Base):
    """Minimal stand-in for Sector -- id + sector_id + one mutable JSON-ish
    column, just enough to exercise the identity-map mechanics under test
    (real Sector.players_present is Postgres-only JSONB -- same technique
    as test_npc_presence_lock_identity_map.py's own _MirrorSector)."""
    __tablename__ = "mirror_sectors_npc_kia_presence"
    id = Column(Integer, primary_key=True)
    sector_id = Column(Integer, nullable=False, unique=True)
    players_present = Column(JSON, nullable=False, default=list)


def _session_factory() -> sessionmaker:
    # autoflush=False deliberately matches the real app Session
    # (core/database.py:19) -- the bug (and its squad-loop reentrancy
    # variant) only exists because autoflush is off.
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    _Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False)


# --------------------------------------------------------------------------- #
# Part A -- generalized real-SQLAlchemy staleness/fix mechanism proof.
# --------------------------------------------------------------------------- #

@pytest.mark.unit
class TestSectorLockIdentityMapMechanism:
    """SQLAlchemy's own identity-map refresh semantics -- not
    npc_spawn_service code. Permanent regression marker: if this ever
    starts failing, SQLAlchemy's own semantics changed underneath the
    codebase, worth knowing independent of the production-code tests
    below."""

    def test_bare_with_for_update_after_unlocked_read_stays_stale(self) -> None:
        SessionFactory = _session_factory()
        seed = SessionFactory()
        seed.add(_MirrorSector(id=1, sector_id=9101, players_present=[]))
        seed.commit()
        seed.close()

        S = SessionFactory()
        unlocked = S.query(_MirrorSector).filter(_MirrorSector.sector_id == 9101).first()
        assert unlocked.players_present == []

        # A concurrent mover (a different player) commits a real write to
        # the SAME row from a DIFFERENT session.
        other = SessionFactory()
        other_row = other.query(_MirrorSector).filter(_MirrorSector.sector_id == 9101).first()
        other_row.players_present = [{"player_id": "concurrent-mover"}]
        other.commit()
        other.close()

        # THE BROKEN shape: bare with_for_update, no populate_existing.
        locked = (
            S.query(_MirrorSector).filter(_MirrorSector.sector_id == 9101)
            .with_for_update().first()
        )
        assert locked.players_present == []  # STALE -- the bug, not the real value
        assert locked is unlocked  # literally the same cached Python object
        S.close()

    def test_populate_existing_after_unlocked_read_sees_fresh(self) -> None:
        SessionFactory = _session_factory()
        seed = SessionFactory()
        seed.add(_MirrorSector(id=1, sector_id=9101, players_present=[]))
        seed.commit()
        seed.close()

        S = SessionFactory()
        unlocked = S.query(_MirrorSector).filter(_MirrorSector.sector_id == 9101).first()

        other = SessionFactory()
        other_row = other.query(_MirrorSector).filter(_MirrorSector.sector_id == 9101).first()
        other_row.players_present = [{"player_id": "concurrent-mover"}]
        other.commit()
        other.close()

        locked = (
            S.query(_MirrorSector).filter(_MirrorSector.sector_id == 9101)
            .populate_existing().with_for_update().first()
        )
        assert locked.players_present == [{"player_id": "concurrent-mover"}]  # FRESH
        assert locked is unlocked  # same identity-map slot, refreshed in place
        S.close()

    def test_flush_before_populate_existing_preserves_pending_mutation_and_sees_fresh_commit(
        self,
    ) -> None:
        """The FLUSH-FIRST half, generalized: a squad-loop-shaped pending
        same-session mutation on the SAME row survives a later
        populate_existing() lock re-read when a flush precedes it, while a
        genuinely concurrent DIFFERENT-session commit is still picked up
        fresh."""
        SessionFactory = _session_factory()
        seed = SessionFactory()
        seed.add(_MirrorSector(id=1, sector_id=9202, players_present=[]))
        seed.commit()
        seed.close()

        S = SessionFactory()
        row = S.query(_MirrorSector).filter(_MirrorSector.sector_id == 9202).first()
        # Mirrors _place_squad's own unflushed add_npc_presence write for
        # the last-placed officer.
        row.players_present = [{"player_id": "officer-just-placed"}]

        S.flush()  # THE FIX
        relocked = (
            S.query(_MirrorSector).filter(_MirrorSector.sector_id == 9202)
            .populate_existing().with_for_update().first()
        )
        assert relocked is row
        assert relocked.players_present == [{"player_id": "officer-just-placed"}]
        S.close()

    def test_bare_populate_existing_without_a_prior_flush_discards_the_pending_mutation(
        self,
    ) -> None:
        """Non-vacuous companion: the same pending mutation, WITHOUT a
        preceding flush, is silently reverted by populate_existing()'s
        refresh -- proving the flush (not luck) is what preserves it
        above."""
        SessionFactory = _session_factory()
        seed = SessionFactory()
        seed.add(_MirrorSector(id=1, sector_id=9202, players_present=[]))
        seed.commit()
        seed.close()

        S = SessionFactory()
        row = S.query(_MirrorSector).filter(_MirrorSector.sector_id == 9202).first()
        row.players_present = [{"player_id": "officer-just-placed"}]

        # NO flush() here -- the counterfactual.
        relocked = (
            S.query(_MirrorSector).filter(_MirrorSector.sector_id == 9202)
            .populate_existing().with_for_update().first()
        )
        assert relocked is row
        assert relocked.players_present == []  # DISCARDED -- the footgun
        S.close()


# --------------------------------------------------------------------------- #
# Part B -- REAL, unmodified npc_spawn_service.handle_npc_ship_destroyed,
# driven via a fake session extending test_pirate_ecosystem_wire.py's own
# MagicMock-dispatch-by-model convention with a stateful Sector-query fake.
# --------------------------------------------------------------------------- #

class _SectorQueryChain:
    """The query-chain object returned by ``db.query(Sector)`` -- mirrors
    the ``.filter().populate_existing().with_for_update().first()`` shape
    the production code chains, reproducing real SQLAlchemy's identity-map
    refresh semantics for exactly the property under test: ``.first()``
    always returns the SAME cached Python object (identity-map hit), and
    only refreshes its mutable columns in place when ``.populate_
    existing()`` was actually called first."""

    def __init__(self, lock: "_SectorLockFake", *, honor_populate_existing: bool):
        self._lock = lock
        self._populate_existing = False
        self._honor_populate_existing = honor_populate_existing

    def filter(self, *args, **kwargs) -> "_SectorQueryChain":
        return self

    def populate_existing(self) -> "_SectorQueryChain":
        if self._honor_populate_existing:
            self._populate_existing = True
        return self

    def with_for_update(self, *args, **kwargs) -> "_SectorQueryChain":
        return self

    def first(self) -> Sector:
        lock = self._lock
        if self._populate_existing:
            lock.cached.players_present = list(lock.committed.players_present or [])
            lock.cached.defenses = dict(lock.committed.defenses or {})
        return lock.cached


class _SectorLockFake:
    """One Sector "row" modeled as two references: ``cached`` is the
    Python object identity a caller's earlier UNLOCKED read already
    populated into the session's identity map (mirrors attack_npc_ship's
    own line-1267 / npc_attack_player's own line-1814 peek, taken by the
    TEST itself exactly as those real callers do, before calling
    handle_npc_ship_destroyed); ``committed`` is what a genuinely
    concurrent, already-committed write from a DIFFERENT session shows
    right now.

    ``has_pending_write`` gates flush(), mirroring real SQLAlchemy's own
    dirty-tracking: a flush only issues SQL (here, only syncs ``committed``
    from ``cached``) for an object THIS session actually mutated since its
    last load/flush. Defaulting it False (B1's shape -- ``cached`` is a
    read-only stale snapshot, never touched by this session) matters: a
    flush() that blindly synced committed FROM cached on every call would
    itself clobber a genuinely concurrent DIFFERENT session's committed
    write with this session's own untouched (stale) copy -- exactly the
    bug this fake must NOT reproduce as a harness artifact."""

    def __init__(
        self,
        cached: Sector,
        committed: Sector,
        *,
        honor_populate_existing: bool = True,
        has_pending_write: bool = False,
    ):
        self.cached = cached
        self.committed = committed
        self.honor_populate_existing = honor_populate_existing
        self.has_pending_write = has_pending_write

    def flush(self) -> None:
        """Real flush semantics for this fake's purposes: persists a
        genuinely PENDING in-memory mutation on ``cached`` into
        ``committed`` -- a same-session write becomes visible to a later
        populate_existing() re-read. A no-op when nothing is pending (see
        class docstring)."""
        if not self.has_pending_write:
            return
        self.committed.players_present = list(self.cached.players_present or [])
        self.committed.defenses = dict(self.cached.defenses or {})
        self.has_pending_write = False

    def query_chain(self) -> _SectorQueryChain:
        return _SectorQueryChain(self, honor_populate_existing=self.honor_populate_existing)


def _make_npc(*, archetype=NPCArchetype.LAW_ENFORCEMENT, current_sector_id=6301) -> NPCCharacter:
    """LAW_ENFORCEMENT + duty_role=None deliberately: permanently-KIA (no
    respawn-cooldown branch to model) and skips the zero-gap promotion
    query entirely (duty_role doesn't start with "primary"), so exactly
    ONE db.query(NPCCharacter) call is needed -- keeping this fake
    session's NPCCharacter dispatch a single static stub, matching
    test_pirate_ecosystem_wire.py's own make_npc()/make_db() convention
    for this exact function."""
    return NPCCharacter(
        id=uuid.uuid4(),
        name="Test Marshal",
        title="Marshal",
        faction_code="terran_federation",
        archetype=archetype,
        status=NPCStatus.ON_DUTY,
        current_sector_id=current_sector_id,
        home_region_id=None,
        ship_id=uuid.uuid4(),
        duty_role=None,
        bang_roster_ref=None,
    )


def _make_sector(*, sector_id: int, present: list) -> Sector:
    return Sector(
        sector_id=sector_id,
        region_id=uuid.uuid4(),
        defenses={},
        players_present=list(present),
    )


def _make_kia_db(*, npc: NPCCharacter, lock: _SectorLockFake, honor_flush: bool = True) -> MagicMock:
    """Sync-Session stand-in mirroring test_pirate_ecosystem_wire.py's own
    make_db() convention (db.query(...) dispatched by model identity; no
    real database touched) -- extended with ``lock``'s stateful Sector
    query chain in place of a static MagicMock return."""
    db = MagicMock()

    def _query(model, *args, **kwargs):
        if model is Sector:
            return lock.query_chain()
        chain = MagicMock()
        if model is NPCCharacter:
            chain.filter.return_value.first.return_value = npc
            chain.filter.return_value.order_by.return_value.first.return_value = None
        else:
            chain.filter.return_value.scalar.return_value = None
        return chain

    db.query.side_effect = _query
    db.add = MagicMock()
    db.flush = MagicMock(side_effect=lock.flush if honor_flush else (lambda: None))

    @contextmanager
    def _begin_nested():
        yield

    db.begin_nested = MagicMock(side_effect=_begin_nested)
    return db


@pytest.mark.unit
class TestKiaHandlerSeesFreshConcurrentPresenceWrite:
    """B1 -- the core WO ask: a concurrent, already-committed presence
    write on the SAME sector is visible to the KIA handler's own re-lock,
    not the stale unlocked-read snapshot the caller (attack_npc_ship /
    npc_attack_player) already cached."""

    def test_handler_sees_fresh_presence_not_the_stale_cached_snapshot(self) -> None:
        ship_id = uuid.uuid4()
        npc = _make_npc()
        npc.ship_id = ship_id
        npc_id_str = str(npc.id)

        # The caller's own earlier UNLOCKED read (attack_npc_ship :1267 /
        # npc_attack_player :1814) -- doesn't yet know about player-A, who
        # entered the sector after that peek.
        cached = _make_sector(
            sector_id=npc.current_sector_id,
            present=[{"player_id": npc_id_str, "is_npc": True}],
        )
        # A genuinely concurrent, DIFFERENT session's committed write.
        committed = _make_sector(
            sector_id=npc.current_sector_id,
            present=[
                {"player_id": "player-A", "is_npc": False},
                {"player_id": npc_id_str, "is_npc": True},
            ],
        )
        lock = _SectorLockFake(cached=cached, committed=committed)
        db = _make_kia_db(npc=npc, lock=lock)

        npc_spawn_service.handle_npc_ship_destroyed(db, ship_id)

        present_ids = {p["player_id"] for p in cached.players_present}
        assert present_ids == {"player-A"}, (
            "the concurrent presence write was invisible to the KIA "
            "handler's re-lock -- it computed the RMW off the stale "
            "cached snapshot and clobbered player-A's presence entry"
        )

    def test_revert_probe_without_populate_existing_loses_the_concurrent_write(self) -> None:
        """Non-vacuous companion: with populate_existing()'s refresh
        disabled on the fake (the pre-fix library shape -- a bare
        with_for_update() re-read that never syncs the identity-mapped
        object), the SAME scenario silently drops player-A's presence
        entry -- proving the harness (not luck) is what makes the
        fix-verification test above pass."""
        ship_id = uuid.uuid4()
        npc = _make_npc()
        npc.ship_id = ship_id
        npc_id_str = str(npc.id)

        cached = _make_sector(
            sector_id=npc.current_sector_id,
            present=[{"player_id": npc_id_str, "is_npc": True}],
        )
        committed = _make_sector(
            sector_id=npc.current_sector_id,
            present=[
                {"player_id": "player-A", "is_npc": False},
                {"player_id": npc_id_str, "is_npc": True},
            ],
        )
        lock = _SectorLockFake(cached=cached, committed=committed, honor_populate_existing=False)
        db = _make_kia_db(npc=npc, lock=lock)

        npc_spawn_service.handle_npc_ship_destroyed(db, ship_id)

        present_ids = {p["player_id"] for p in cached.players_present}
        assert present_ids == set(), (
            "harness sanity check failed -- without populate_existing's "
            "refresh, the handler must compute the RMW off the stale "
            "cached snapshot, losing player-A's presence entry entirely"
        )


@pytest.mark.unit
class TestKiaHandlerCoversPirateAggressionChain:
    """(c) PIRATE-AGGRESSION -- the third reentrancy path (mack gate
    finding, WO-NPC-KIA-PRESENCE follow-up): movement_service._maybe_
    initiate_pirate_combat (~:2711, the pirate_aggression sector-entry
    trigger ~:2768) -> npc_combat_initiation_service.initiate_npc_combat ->
    CombatService.npc_attack_player -> HERE, when the player kills the
    pirate that just engaged them entering the sector.

    Distinct from (b) POLICE-COMBAT: grepped -- no add_npc_presence call
    (or any Sector mutation at all) anywhere in _maybe_initiate_pirate_
    combat's body; pirate rosters are static/seeded, not placed per-
    engagement like a police squad. So this chain has NOTHING pending when
    it reaches the re-lock -- the flush is covered-but-not-load-bearing
    here, unlike (b). This test proves the OTHER half still matters on
    this chain: the movement leg's own earlier unlocked touch of this
    Sector (reading sector.defenses for pirate_patrols, or the post-
    _execute_movement-commit re-load under the session's default
    expire_on_commit) caches it in the identity map exactly like (a)'s
    antecedent, so populate_existing is still required to see a
    genuinely concurrent presence write -- the flush alone would not have
    saved this chain."""

    def test_handler_sees_fresh_presence_with_nothing_pending_on_this_chain(self) -> None:
        ship_id = uuid.uuid4()
        # HOSTILE_RAIDER -- the codebase's own pirate archetype (matches
        # test_pirate_ecosystem_wire.py's usage); sector.defenses stays {}
        # so the (unrelated) PirateKillLog feeder branch never engages.
        npc = _make_npc(archetype=NPCArchetype.HOSTILE_RAIDER)
        npc.ship_id = ship_id
        npc_id_str = str(npc.id)

        # The movement leg's own earlier unlocked touch of this Sector --
        # doesn't yet know about player-A, who entered after that peek.
        cached = _make_sector(
            sector_id=npc.current_sector_id,
            present=[{"player_id": npc_id_str, "is_npc": True}],
        )
        # A genuinely concurrent, DIFFERENT session's committed write.
        committed = _make_sector(
            sector_id=npc.current_sector_id,
            present=[
                {"player_id": "player-A", "is_npc": False},
                {"player_id": npc_id_str, "is_npc": True},
            ],
        )
        # has_pending_write defaults False -- this chain never mutates the
        # Sector before reaching the handler (the property under test).
        lock = _SectorLockFake(cached=cached, committed=committed)
        db = _make_kia_db(npc=npc, lock=lock)

        npc_spawn_service.handle_npc_ship_destroyed(db, ship_id)

        present_ids = {p["player_id"] for p in cached.players_present}
        assert present_ids == {"player-A"}, (
            "the pirate-aggression chain's concurrent presence write was "
            "invisible to the KIA handler's re-lock"
        )
        assert lock.has_pending_write is False, (
            "sanity check -- this chain has nothing pending; unlike (b) "
            "POLICE-COMBAT, the flush here is covered-but-not-load-"
            "bearing, and this test's fix-verification assertion above "
            "must be earned by populate_existing alone"
        )


@pytest.mark.unit
class TestKiaHandlerFlushFirstReentrancy:
    """B2 -- the FLUSH-FIRST half: a squad-loop-shaped pending same-
    session presence write on the SAME sector (mirrors _place_squad's
    last-officer add_npc_presence, unflushed, immediately followed by
    _maybe_initiate_police_combat -> ... -> npc_attack_player reaching
    this handler) survives the KIA re-lock instead of being discarded."""

    def test_pending_squad_officer_presence_survives_the_kia_relock(self) -> None:
        ship_id = uuid.uuid4()
        npc = _make_npc()
        npc.ship_id = ship_id
        npc_id_str = str(npc.id)

        # "cached" IS the shared sector object -- _place_squad's officer-
        # placement loop and the combat call both operate on the SAME
        # identity-mapped Python object in the SAME session, exactly as
        # _sweep_one's real call chain does.
        cached = _make_sector(
            sector_id=npc.current_sector_id,
            present=[
                {"player_id": "officer-just-placed", "is_npc": True},
                {"player_id": npc_id_str, "is_npc": True},
            ],
        )
        # "committed" -- what's actually in the DB right now: the LAST
        # officer's add_npc_presence write is still PENDING/unflushed at
        # this point (prior officers, flushed by earlier iterations of
        # _locked_sectors, already made it to the DB).
        committed = _make_sector(
            sector_id=npc.current_sector_id,
            present=[{"player_id": npc_id_str, "is_npc": True}],
        )
        lock = _SectorLockFake(cached=cached, committed=committed, has_pending_write=True)
        db = _make_kia_db(npc=npc, lock=lock)

        npc_spawn_service.handle_npc_ship_destroyed(db, ship_id)

        present_ids = {p["player_id"] for p in cached.players_present}
        assert present_ids == {"officer-just-placed"}, (
            "the just-placed officer's pending presence write was "
            "discarded by the KIA handler's populate_existing() re-lock "
            "-- the flush-first fix is missing"
        )

    def test_revert_probe_without_the_hoisted_flush_discards_the_officer(self) -> None:
        """Non-vacuous companion: with the handler's own db.flush()
        disabled (simulating the pre-fix shape -- populate_existing
        present, no preceding flush), the SAME reentrant sequence
        silently reverts the officer's pending presence write, proving
        the harness (not luck) is what makes the fix-verification test
        above pass."""
        ship_id = uuid.uuid4()
        npc = _make_npc()
        npc.ship_id = ship_id
        npc_id_str = str(npc.id)

        cached = _make_sector(
            sector_id=npc.current_sector_id,
            present=[
                {"player_id": "officer-just-placed", "is_npc": True},
                {"player_id": npc_id_str, "is_npc": True},
            ],
        )
        committed = _make_sector(
            sector_id=npc.current_sector_id,
            present=[{"player_id": npc_id_str, "is_npc": True}],
        )
        lock = _SectorLockFake(cached=cached, committed=committed, has_pending_write=True)
        db = _make_kia_db(npc=npc, lock=lock, honor_flush=False)

        npc_spawn_service.handle_npc_ship_destroyed(db, ship_id)

        present_ids = {p["player_id"] for p in cached.players_present}
        assert present_ids == set(), (
            "harness sanity check failed -- without the hoisted flush, "
            "the KIA re-lock's populate_existing() must revert the "
            "pending officer presence write, proving the self-clobber "
            "bug this WO fixes"
        )


# --------------------------------------------------------------------------- #
# Part C -- structural AST pin.
# --------------------------------------------------------------------------- #

@pytest.mark.unit
class TestHandleNpcShipDestroyedStructuralWiring:
    def _lock_guard(self) -> ast.If:
        source = textwrap.dedent(
            inspect.getsource(npc_spawn_service_module.handle_npc_ship_destroyed)
        )
        tree = ast.parse(source)
        func = tree.body[0]
        assert isinstance(func, ast.FunctionDef)
        assert func.name == "handle_npc_ship_destroyed"

        # The function has TWO top-level `if sector_id is not None:`
        # blocks (this locking block, and the later NPCDeathLog write) --
        # disambiguate on the one whose body actually queries Sector,
        # rather than relying on source order.
        for stmt in func.body:
            if (
                isinstance(stmt, ast.If)
                and isinstance(stmt.test, ast.Compare)
                and isinstance(stmt.test.left, ast.Name)
                and stmt.test.left.id == "sector_id"
                and "db.query(Sector)" in ast.unparse(stmt)
            ):
                return stmt
        raise AssertionError(
            "could not locate the `if sector_id is not None:` Sector-"
            "locking block in handle_npc_ship_destroyed"
        )

    def test_flush_is_the_first_statement_inside_the_lock_guard(self) -> None:
        lock_guard = self._lock_guard()

        def dotted(node: ast.AST) -> str:
            parts: list[str] = []
            while isinstance(node, ast.Attribute):
                parts.append(node.attr)
                node = node.value
            if isinstance(node, ast.Name):
                parts.append(node.id)
            return ".".join(reversed(parts))

        first_stmt = lock_guard.body[0]
        got = (
            dotted(first_stmt.value.func)
            if isinstance(first_stmt, ast.Expr) and isinstance(first_stmt.value, ast.Call)
            else None
        )
        assert got == "db.flush", (
            "the first statement inside the `if sector_id is not None:` "
            "Sector-locking block must be a bare db.flush() -- it "
            "persists any pending pre-lock Sector mutation (this call's "
            "own or an earlier same-session caller's, e.g. npc_engagement_"
            "service._place_squad's squad-loop presence write) so the "
            f"populate_existing() re-read below observes it. Got: {got!r}"
        )

    def test_populate_existing_precedes_with_for_update_in_the_sector_query_chain(self) -> None:
        # Strip the function's own docstring first -- it prose-mentions
        # neither term today, but this mirrors the sibling precedent
        # files' defensive convention (a docstring mentioning both terms
        # would otherwise pollute a raw substring search with the wrong
        # occurrence).
        lock_guard = self._lock_guard()
        code_only = "\n".join(ast.unparse(stmt) for stmt in lock_guard.body)

        assert ".populate_existing()" in code_only
        assert ".with_for_update()" in code_only
        assert code_only.index(".populate_existing()") < code_only.index(".with_for_update()")
