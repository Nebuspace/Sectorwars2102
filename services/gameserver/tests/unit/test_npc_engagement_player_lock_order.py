"""WO-NPC-LOCK-ORDER-BATCH (primary fix) -- ``npc_engagement_service.
_sweep_one``'s ARRIVED-transition branches now lock the offender Player
row BEFORE ``_place_squad`` acquires the squad officer's Ship/Sector
locks, closing a real AB-BA deadlock.

Before this fix, the observed acquisition order on this path was
Ship (``_place_squad``, :431) -> Sector (``_place_squad`` ->
``npc_movement_service._locked_sectors``) -> Player (``npc_attack_player``,
combat_service.py:1795) -- reversed against the documented "Player ->
Station -> Ship -> NPCCharacter -> Sector" convention
(npc_movement_service.py:24-25) and against combat_service's own
Player-first order (attack_player, attack_npc_ship, npc_attack_player all
lock Player before any Ship). A concurrent offender-initiated
``combat_service.attack_npc_ship`` (Player-first, then wants that same
officer Ship) -- or a concurrent ``movement_service.move_player_to_sector``
(Player-first, then wants that same Sector) -- could AB-BA-deadlock
against this path. Independently found by cipher during the
WO-NPC-KIA-PRESENCE gate (2026-07-11, LOW-confidence/informational).

Fix: ``_lock_offender_player`` re-locks the Player row (flush-first,
mirroring ``_locked_sectors``' own precedent) immediately before
``_place_squad`` runs, on BOTH ARRIVED-transition branches -- (d) no-
officer-grace and (a) turn-counter watcher. No other file changed;
``_place_squad``/``npc_attack_player``'s own bodies are untouched.

DB-free fake-session convention (mirrors test_declare_war_lock_order.py's
lock-log spy + test_npc_kia_presence_lock_identity_map.py's real-ORM-
instance / single-row-per-model query-chain style): real transient
Player/NPCCharacter/Ship/Sector ORM instances (``flag_modified`` inside
``add_npc_presence``/``remove_npc_presence`` needs a genuine mapped Sector
instance, not a stand-in) held one-per-model in a fake session that
records every ``.with_for_update()`` acquisition, in call order, as
``(model_name, id)`` -- the REAL production code path runs unmodified
through ``_sweep_one`` -> ``_place_squad`` -> ``npc_movement_service.
_locked_sectors``; only ``jurisdiction_of`` and
``_maybe_initiate_police_combat`` are patched at the module boundary
(their own correctness is out of scope here and already covered by
test_police_engagement_dispatch.py / test_npc_initiated_entry.py).

Deadlock-freedom under real concurrent Postgres transactions cannot be
demonstrated by a single-threaded fake -- that stays the orchestrator's
live-Postgres leg. What IS fully provable here is the ORDERING property
itself: the necessary precondition for that guarantee.

Mack-gate follow-up (same WO, re-gated): the primary fix refreshed
``player`` via ``populate_existing()`` inside ``_lock_offender_player``
but left the sibling ``current_sector`` local (captured UNLOCKED earlier
in ``_sweep_one``) stale -- a player who moved during the lock-wait left
``_maybe_initiate_police_combat`` holding a FRESH player paired with a
STALE sector, spuriously mismatching ``_guard_failure``'s
``defender.current_sector_id != sector.sector_id`` guard and silently
no-opping "attacks first" (WO-CMB-NPC-INITIATED-1) even though
``_place_squad`` had already placed the squad at the correct fresh
sector. Fixed via a new ``_current_sector_of`` helper, re-called right
after ``_lock_offender_player`` succeeds on both ARRIVED branches.
``TestCurrentSectorStaysCoherentWithLockedPlayer`` pins this.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, UTC
from unittest.mock import patch

import src.services.npc_engagement_service as svc
from src.models.npc_character import NPCArchetype, NPCCharacter, NPCStatus
from src.models.pending_engagement import EngagementStatus, PendingEngagement
from src.models.player import Player
from src.models.sector import Sector
from src.models.ship import Ship, ShipType

SECTOR_NUM = 4200
NEW_SECTOR_NUM = 4300  # the "moved during the lock-wait" destination


# --------------------------------------------------------------------------- #
# DB-free fake session -- records (model, id) lock-acquisition order
# --------------------------------------------------------------------------- #

class _RecordingQuery:
    """Query stand-in recording every ``.with_for_update()`` acquisition,
    in call order, as ``(model_name, row_id)`` into the SHARED
    ``lock_log`` -- the property the order-pin tests prove.

    Two row-resolution modes:
      * single-row (``sector_rows=None``): ``.filter()`` is a no-op
        passthrough -- mirrors test_npc_kia_presence_lock_identity_map.py's
        ``_SectorQueryChain.filter()`` convention (used by Player/
        NPCCharacter/Ship, which this fixture only ever seeds ONE row of).
      * keyed (``sector_rows={sector_id: Sector, ...}``): ``.filter()``
        decodes the real ``Sector.sector_id == <value>`` binary
        expression (same ``.right.value`` technique as
        test_npc_initiated_entry.py's ``_FakeQuery._matches`` /
        test_declare_war_lock_order.py's ``_FakeTeamQuery.filter``) and
        resolves against the matching row -- needed for the player-moved-
        under-lock race test below, where the SAME query shape must
        return a DIFFERENT Sector row depending on ``player.
        current_sector_id`` at call time.

    ``vanish_on_lock=True`` makes ``.first()`` return None ONLY after
    ``.with_for_update()`` was called (simulates the row disappearing
    under lock). ``mutate_on_lock``, if given, is invoked (no args)
    the moment ``.with_for_update()`` fires -- BEFORE the row is
    resolved/logged -- simulating a concurrent committed write becoming
    visible under the lock (mirrors real ``populate_existing()``
    refresh semantics for this fixture's purposes)."""

    def __init__(self, row, model_name, lock_log, id_attr="id", *,
                 vanish_on_lock=False, mutate_on_lock=None, sector_rows=None):
        self._row = row
        self._model_name = model_name
        self._lock_log = lock_log
        self._id_attr = id_attr
        self._vanish_on_lock = vanish_on_lock
        self._mutate_on_lock = mutate_on_lock
        self._sector_rows = sector_rows
        self._locked = False
        self._filter_key = None

    def filter(self, *criteria):
        if self._sector_rows is not None and criteria:
            rhs = getattr(criteria[0], "right", None)
            self._filter_key = rhs.value if hasattr(rhs, "value") else rhs
        return self

    def populate_existing(self):
        return self

    def order_by(self, *a, **k):
        return self

    def _resolve(self):
        if self._sector_rows is not None:
            return self._sector_rows.get(self._filter_key)
        return self._row

    def with_for_update(self, *a, **k):
        if self._mutate_on_lock is not None:
            self._mutate_on_lock()
        row = self._resolve()
        if row is not None:
            self._lock_log.append((self._model_name, getattr(row, self._id_attr)))
        self._locked = True
        return self

    def first(self):
        if self._vanish_on_lock and self._locked:
            return None
        return self._resolve()


class _FakeSession:
    def __init__(self, *, player, npc, ship, sector=None, sector_rows=None,
                 vanish_player_on_lock=False, mutate_player_on_lock=None):
        self._player = player
        self._npc = npc
        self._ship = ship
        # Sector always resolves keyed-by-sector_id under the hood (a
        # single-sector fixture is just a 1-entry dict) -- unifies the
        # simple order-pin tests with the moving-player race test below
        # without a second query-chain implementation.
        self._sector_rows = (
            sector_rows if sector_rows is not None
            else ({sector.sector_id: sector} if sector is not None else {})
        )
        self._vanish_player_on_lock = vanish_player_on_lock
        self._mutate_player_on_lock = mutate_player_on_lock
        self.lock_log: list = []
        self.flush_count = 0

    def query(self, model, *a, **k):
        if model is Player:
            return _RecordingQuery(
                self._player, "Player", self.lock_log, id_attr="id",
                vanish_on_lock=self._vanish_player_on_lock,
                mutate_on_lock=self._mutate_player_on_lock,
            )
        if model is NPCCharacter:
            return _RecordingQuery(self._npc, "NPCCharacter", self.lock_log, id_attr="id")
        if model is Ship:
            return _RecordingQuery(self._ship, "Ship", self.lock_log, id_attr="id")
        if model is Sector:
            return _RecordingQuery(
                None, "Sector", self.lock_log, id_attr="sector_id",
                sector_rows=self._sector_rows,
            )
        raise AssertionError(f"unexpected query target: {model!r}")

    def flush(self):
        self.flush_count += 1


# --------------------------------------------------------------------------- #
# Fixture builders
# --------------------------------------------------------------------------- #

def _make_player(*, lifetime_turns_spent=0, personal_reputation=0) -> Player:
    return Player(
        id=uuid.uuid4(),
        current_sector_id=SECTOR_NUM,
        lifetime_turns_spent=lifetime_turns_spent,
        personal_reputation=personal_reputation,
    )


def _make_ship() -> Ship:
    return Ship(
        id=uuid.uuid4(), name="Federation Marshal Interdictor",
        type=ShipType.FAST_COURIER, is_destroyed=False, sector_id=SECTOR_NUM,
        current_value=0,
    )


def _make_npc(ship: Ship, *, status=NPCStatus.ENGAGED_PENDING_ARRIVAL) -> NPCCharacter:
    return NPCCharacter(
        id=uuid.uuid4(), name="Vance", title="Marshal",
        faction_code="terran_federation", archetype=NPCArchetype.LAW_ENFORCEMENT,
        status=status, current_sector_id=SECTOR_NUM, ship_id=ship.id,
        home_region_id=None,
    )


def _make_sector(sector_id: int = SECTOR_NUM) -> Sector:
    return Sector(sector_id=sector_id, region_id=None, players_present=[], defenses={})


def _make_engagement(player: Player, **overrides) -> PendingEngagement:
    now = datetime.now(UTC)
    defaults = dict(
        id=uuid.uuid4(),
        player_id=player.id,
        offense_type="wanted_status",
        jurisdiction=svc.SENTINEL,  # hardcodes _federation_squad_size's
        # skip in branch (d) -- squad size/captain come from the
        # SENTINEL literal (4, False), not player.personal_reputation.
        offense_sector_id=SECTOR_NUM,
        region_id=None,  # bypasses the (b) region_id match entirely
        npc_squad_ids=[],
        offense_at_turn_count=0,
        arrival_turn_threshold=None,
        status=EngagementStatus.PENDING,
        arrival_sector_id=None,
        grace_expires_at=None,
        expires_at=now + timedelta(hours=24),
        resolved_at=None,
    )
    defaults.update(overrides)
    return PendingEngagement(**defaults)


# --------------------------------------------------------------------------- #
# Order-pin: Player locked BEFORE the squad's Ship/Sector locks
# --------------------------------------------------------------------------- #

class TestOffenderPlayerLockOrder:
    def test_turn_counter_watcher_locks_player_before_ship_and_sector(self) -> None:
        """Branch (a): the squad was already picked in an earlier sweep
        pass (npc_squad_ids populated, officer already
        ENGAGED_PENDING_ARRIVAL) -- this pass only crosses the turn
        threshold and places it."""
        player = _make_player(lifetime_turns_spent=10)
        ship = _make_ship()
        npc = _make_npc(ship)
        sector = _make_sector()
        engagement = _make_engagement(
            player, npc_squad_ids=[str(npc.id)], arrival_turn_threshold=5,
        )
        db = _FakeSession(player=player, npc=npc, ship=ship, sector=sector)

        with patch.object(svc, "jurisdiction_of", return_value=svc.SENTINEL), \
             patch.object(svc, "_maybe_initiate_police_combat", return_value=[]) as mock_combat:
            events = svc._sweep_one(db, engagement, datetime.now(UTC))

        assert db.lock_log == [
            ("Player", player.id),
            ("Ship", ship.id),
            ("Sector", sector.sector_id),
        ]
        assert engagement.status == EngagementStatus.ARRIVED
        assert engagement.arrival_sector_id == SECTOR_NUM
        assert any(e["type"] == "npc_engaged" for e in events)
        mock_combat.assert_called_once()

    def test_no_officer_grace_locks_player_before_ship_and_sector(self) -> None:
        """Branch (d): the grace window just closed with no squad picked
        yet -- ``_pick_squad`` is patched (its own hop-distance routing
        correctness is proven by test_police_engagement_dispatch.py /
        the integration suite, out of scope here) so this test isolates
        the lock-order property alone."""
        player = _make_player()
        ship = _make_ship()
        npc = _make_npc(ship, status=NPCStatus.ON_DUTY)  # _pick_squad's
        # own real candidates start ON_DUTY; _sweep_one flips it to
        # ENGAGED_PENDING_ARRIVAL itself before _place_squad runs.
        sector = _make_sector()
        engagement = _make_engagement(
            player, grace_expires_at=datetime.now(UTC) - timedelta(seconds=1),
        )
        db = _FakeSession(player=player, npc=npc, ship=ship, sector=sector)

        with patch.object(svc, "jurisdiction_of", return_value=svc.SENTINEL), \
             patch.object(svc, "_pick_squad", return_value=[npc]), \
             patch.object(svc, "_maybe_initiate_police_combat", return_value=[]) as mock_combat:
            events = svc._sweep_one(db, engagement, datetime.now(UTC))

        assert db.lock_log == [
            ("Player", player.id),
            ("Ship", ship.id),
            ("Sector", sector.sector_id),
        ]
        assert engagement.npc_squad_ids == [str(npc.id)]
        assert engagement.status == EngagementStatus.ARRIVED
        assert any(e["type"] == "npc_engaged" for e in events)
        mock_combat.assert_called_once()

    def test_player_vanished_under_lock_skips_squad_placement(self) -> None:
        """Defensive-None path: the (exceedingly unlikely) race where the
        Player row is gone by the time the lock is taken -- mirrors
        ``handle_npc_ship_destroyed``'s own ``if sector is not None``
        no-op-on-vanished-row idiom. _place_squad must never run (no
        Ship/Sector lock attempted), and the squad-status mutations
        already applied in-memory for this pass are the only state
        change (no crash, no orphan cleanup attempted -- documented as
        an accepted, pre-existing-pattern edge case)."""
        player = _make_player(lifetime_turns_spent=10)
        ship = _make_ship()
        npc = _make_npc(ship)
        sector = _make_sector()
        engagement = _make_engagement(
            player, npc_squad_ids=[str(npc.id)], arrival_turn_threshold=5,
        )
        db = _FakeSession(
            player=player, npc=npc, ship=ship, sector=sector,
            vanish_player_on_lock=True,
        )

        with patch.object(svc, "jurisdiction_of", return_value=svc.SENTINEL), \
             patch.object(svc, "_maybe_initiate_police_combat") as mock_combat:
            events = svc._sweep_one(db, engagement, datetime.now(UTC))

        assert db.lock_log == [("Player", player.id)]  # locked, then vanished
        assert events == []
        assert engagement.status == EngagementStatus.PENDING  # unchanged
        mock_combat.assert_not_called()  # _place_squad, and everything
        # after it, never ran


# --------------------------------------------------------------------------- #
# mack-gate follow-up: current_sector must stay COHERENT with the
# post-lock-refreshed player, even when the player genuinely moved during
# the with_for_update() lock-wait -- the exact concurrent
# move_player_to_sector race this WO defends against. Before this follow-
# up fix, _lock_offender_player refreshed `player` but the sibling
# `current_sector` local (captured UNLOCKED earlier) was NOT refreshed,
# so _maybe_initiate_police_combat received a FRESH player paired with a
# STALE sector -- _guard_failure's `defender.current_sector_id !=
# sector.sector_id` check would then spuriously mismatch and "attacks
# first" would silently no-op, even though _place_squad (using
# player.current_sector_id directly) already placed the squad at the
# correct, fresh sector.
# --------------------------------------------------------------------------- #

class TestCurrentSectorStaysCoherentWithLockedPlayer:
    def test_player_moved_during_lock_wait_yields_a_fresh_matching_sector(self) -> None:
        """Simulates a concurrent, already-committed move_player_to_sector
        becoming visible the moment _lock_offender_player's
        with_for_update() takes hold (mirrors real populate_existing()
        refresh semantics for this fixture's purposes -- see
        _RecordingQuery's docstring). Both the OLD sector (read once,
        unlocked, at the top of _sweep_one's (b) jurisdiction check) and
        the NEW sector (the player's actual post-lock location) are
        seeded, so the fake can resolve either depending on WHEN the
        query fires -- exactly the property that distinguishes a stale
        snapshot from a fresh one."""
        player = _make_player(lifetime_turns_spent=10)
        ship = _make_ship()
        npc = _make_npc(ship)
        npc.current_sector_id = NEW_SECTOR_NUM  # officer is already
        # positioned at what becomes the fresh/new sector -- keeps
        # _locked_sectors' dedup to ONE Sector lock, isolating this test
        # to the current_sector-coherence property alone.
        old_sector = _make_sector(sector_id=SECTOR_NUM)
        new_sector = _make_sector(sector_id=NEW_SECTOR_NUM)
        engagement = _make_engagement(
            player, npc_squad_ids=[str(npc.id)], arrival_turn_threshold=5,
        )

        def _simulate_concurrent_move() -> None:
            player.current_sector_id = NEW_SECTOR_NUM

        db = _FakeSession(
            player=player, npc=npc, ship=ship,
            sector_rows={SECTOR_NUM: old_sector, NEW_SECTOR_NUM: new_sector},
            mutate_player_on_lock=_simulate_concurrent_move,
        )

        with patch.object(svc, "jurisdiction_of", return_value=svc.SENTINEL), \
             patch.object(svc, "_maybe_initiate_police_combat", return_value=[]) as mock_combat:
            svc._sweep_one(db, engagement, datetime.now(UTC))

        # The AB-BA order-pin still holds -- refreshing a local doesn't
        # move the lock.
        assert db.lock_log == [
            ("Player", player.id),
            ("Ship", ship.id),
            ("Sector", NEW_SECTOR_NUM),
        ]

        mock_combat.assert_called_once()
        _db_arg, _engagement_arg, called_player, called_sector = mock_combat.call_args.args
        assert called_player.current_sector_id == NEW_SECTOR_NUM
        assert called_sector is new_sector
        assert called_sector.sector_id == called_player.current_sector_id  # the
        # exact invariant _guard_failure's `defender.current_sector_id !=
        # sector.sector_id` guard depends on
        assert called_sector.sector_id != SECTOR_NUM  # not the stale snapshot

        # _place_squad itself was already correct pre-fix (it reads
        # player.current_sector_id directly) -- confirm it placed the
        # squad at the fresh sector too, so the pair is coherent end to
        # end, not just at the call boundary.
        assert engagement.arrival_sector_id == NEW_SECTOR_NUM


# --------------------------------------------------------------------------- #
# Recovery-gap assessment (documented, not asserted as behavior -- see the
# STATUS report: this fix PREVENTS the traced cycle, it does not touch the
# _maybe_initiate_police_combat exception-swallow / sp.rollback() recovery
# path, which stays exactly as fragile/untested as before for any
# DIFFERENT deadlock).
# --------------------------------------------------------------------------- #
