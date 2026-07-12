"""Regression pin for WO-MONEY-STRAGGLER-FLUSHFIRST (planetary sites).

``PlanetaryService._skim_siege_stockpiles`` (planetary_service.py ~:1746) is
re-entered once per APPLIED siege turn from ``_apply_siege_turn``, which
itself is called in a tight catch-up loop by ``advance_siege`` (~:1891-1892)
whenever a besieged planet is read (or swept) after more than one siege turn
has elapsed since the last advance -- e.g. a planet nobody checked on for a
few days accrues several turns at once. On a session opened
``autoflush=False`` (core/database.py:19), iteration N's ``ship.cargo``
mutation (~:1842-1845) is NOT persisted before iteration N+1 re-enters this
same function.

Two locked reads in this function needed ``.populate_existing()`` (matching
the sibling shipped pattern at planetary_service.py:1310/1375/2068 -- an
already-identity-mapped Player/Ship must be refreshed under lock, not
silently served stale) -- BOTH sites turned out to need the SAME single
``self.db.flush()``, hoisted to run once before either lock (both gates,
mack + cipher, independently found the Player-site residual below; the
flush originally sat only before the Ship query):

* Site A -- besieger Player (~:1792): NOT naive-safe, despite ``besieger``
  being only READ (``current_ship_id``) inside THIS function.
  ``combat_service.attack_planet`` locks the SAME attacker Player row
  (:2081), mutates it IN-MEMORY UNFLUSHED (``spend_turns`` :2131 ->
  turns/lifetime_turns_spent; ``_regen_turns`` :2116;
  ``attacker.attack_drones -=`` :2163), THEN calls ``settle(planet,
  db=self.db)`` (:2174, capture branch) -- which cascades
  ``_step3_power_siege`` -> ``advance_siege`` -> ``_apply_siege_turn`` ->
  HERE. In the canonical besiege-then-capture combo
  (``planet.siege_attacker_id == attacker.id``, gated at
  combat_service.py:3952-3956 for ``siege_vulnerable``), ``besieger``
  resolves to the SAME identity-mapped ``attacker`` object as the caller's
  -- a bare ``.populate_existing()`` would DISCARD the turn-spend/
  drone-loss/regen: a repeatable free-assault lost-update. Near-unreachable
  via the sole live route today (the ``check_and_update_siege`` pre-hook
  drains ``pending`` to 0 first) -- a coincidental mitigation, not a
  structural guarantee.

* Site B -- besieger Ship (~:1826 lock, formerly its own flush at what was
  ~:1815): NOT naive-safe. A bare ``.populate_existing()`` here would
  refresh the WHOLE row from the DB on iteration N+1's lock, discarding
  iteration N's unflushed ``ship.cargo`` write -- a conservation break (the
  planet is debited by both iterations, but the ship is only credited for
  the second).

Fix: a SINGLE ``self.db.flush()`` immediately before the Player lock (after
the ``if not wanted: return {}`` guard) covers BOTH -- it persists any
pending mutation on the attacker/besieger Player row before Site A's lock,
AND (since ``_skim_siege_stockpiles`` is re-entered once per siege turn by
``advance_siege``'s loop) persists the PRIOR iteration's unflushed ship
cargo before Site B's lock. One flush, both locks, same call chain --
mirrors bounty_service.collect_bounty's WO-BOUNTY-COLLECT-FLUSH precedent
and combat_service.py:2513's turret_count-mutation salvage-lock precedent
for the identical class of bug.

Part A is a generalized, siege-agnostic mechanism proof (mirrors
test_movement_presence_lock_identity_map.py's own Part A / test_bounty_
collect_flush_populate_existing.py's ``test_direct_populate_existing_
without_a_prior_flush_discards_the_pending_mutation``): the identity-map
discard/refresh semantics under test are a property of SQLAlchemy's own
Session/Query machinery, not of Ship's specific columns.

Part B drives the REAL, unmodified ``PlanetaryService.advance_siege`` /
``_apply_siege_turn`` / ``_skim_siege_stockpiles`` chain through a genuine
2-turn catch-up (mack's accepted discard-repro seed), proving Site B
(Ship-cargo conservation across the unflushed inter-iteration gap).

Part C drives the REAL, unmodified ``PlanetaryService._skim_siege_
stockpiles`` through the Site A reentrancy scenario: an attacker Player
already locked + mutated (unflushed) in the SAME session BEFORE the skim
runs, with ``planet.siege_attacker_id == attacker.id`` (the
besiege-then-capture combo) so the skim's own Player lock resolves to the
SAME identity-mapped object -- proving the caller's pending turn-spend/
drone-loss survives, not just the Ship-side cargo.

All three parts run against real SQLite mirror classes for Player and Ship
(the real models carry Postgres-only UUID/JSONB columns that block
``Model.__table__.create()`` on SQLite -- same blocker documented in
test_storage_deposit_prelock_identity_map.py and test_bounty_collect_flush_
populate_existing.py). ``planet`` itself stays a plain ``SimpleNamespace``
(never queried/locked inside this function -- matches
test_siege_stockpile_skim.py's own ``make_planet()`` convention): the
planet row is already held by the caller's settle()/read lock per the
function's own docstring.

``with_for_update()`` is a documented no-op on SQLite -- irrelevant here,
since this file is entirely about flush-ordering + identity-map refresh
semantics (that's a real-Postgres-only property, proven separately by the
codebase's live two-connection contention tests).
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import declarative_base, sessionmaker
from sqlalchemy.pool import StaticPool

from src.core.game_time import GAME_TIME_SCALE
from src.services import planetary_service as ps
from src.services.planetary_service import (
    PlanetaryService,
    SIEGE_STOCKPILE_SKIM_FRACTION,
    SIEGE_TURN_HOURS,
    SIEGE_TURNS_THRESHOLD,
)


# --------------------------------------------------------------------------- #
# Part A -- generalized real-SQLAlchemy discard/fix mechanism proof.
# --------------------------------------------------------------------------- #

def _mirror_ship_schema():
    Base = declarative_base()

    class MirrorShip(Base):
        __tablename__ = "mirror_ships_siege_flush"
        id = sa.Column(sa.Integer, primary_key=True)
        cargo = sa.Column(sa.JSON, nullable=False, default=dict)
        # A column S's own pending mutation never touches -- lets the "fresh
        # concurrent commit" half of the fix-verification test prove a
        # genuine refresh happened, without colliding with S's own pending
        # `cargo` write (which a concurrent write to `cargo` itself would --
        # correctly -- lose to S's flush, same as any whole-column JSON RMW;
        # not the mechanism under test here).
        hull_class = sa.Column(sa.String, nullable=True, default="raw")

    return Base, MirrorShip


def _session_factory(Base) -> sessionmaker:
    # autoflush=False deliberately matches the real app Session
    # (core/database.py:19) -- the bug this WO fixes only exists because
    # autoflush is off; a default autoflush=True session would silently
    # persist the pending mutation on every SELECT and mask the mechanism.
    engine = sa.create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False)


class TestShipCargoIdentityMapDiscardMechanism:
    def test_bare_populate_existing_without_a_prior_flush_discards_pending_cargo(self) -> None:
        """THE DANGER, isolated: an unlocked full-ORM read of the ship
        (mirrors iteration N's initial load), an in-memory pending mutation
        on it (mirrors iteration N's plunder, unflushed), then a bare
        ``.populate_existing().with_for_update()`` re-read of the SAME PK in
        the SAME session -- the shape iteration N+1's lock re-read would
        have WITHOUT the WO's flush -- silently reverts the pending write."""
        Base, MirrorShip = _mirror_ship_schema()
        SessionFactory = _session_factory(Base)

        seed = SessionFactory()
        seed.add(MirrorShip(id=1, cargo={"capacity": 1000, "used": 0, "contents": {}}))
        seed.commit()
        seed.close()

        S = SessionFactory()
        try:
            ship = S.query(MirrorShip).filter(MirrorShip.id == 1).first()
            assert ship.cargo["contents"] == {}

            # Iteration N's plunder -- in-memory, unflushed (mirrors
            # _skim_siege_stockpiles' ship.cargo reassignment at ~:1842-1844).
            ship.cargo = {"capacity": 1000, "used": 50, "contents": {"ore": 50}}

            # NO flush() here -- the counterfactual: a bare populate_existing
            # lock re-read of the SAME row in the SAME transaction.
            relocked = (
                S.query(MirrorShip)
                .filter(MirrorShip.id == 1)
                .populate_existing()
                .with_for_update()
                .first()
            )

            assert relocked is ship  # same identity-map slot
            assert ship.cargo["contents"] == {}  # DISCARDED -- the footgun
        finally:
            S.close()

    def test_flush_before_populate_existing_preserves_pending_cargo_and_sees_fresh_commit(
        self,
    ) -> None:
        """THE FIX: flushing immediately before the populate_existing lock
        re-read persists the pending mutation first, so the refresh reads it
        back rather than clobbering it -- while a genuinely concurrent,
        already-committed change from a DIFFERENT session on the SAME row is
        still picked up fresh (not a stale pre-loaded value)."""
        Base, MirrorShip = _mirror_ship_schema()
        SessionFactory = _session_factory(Base)

        seed = SessionFactory()
        seed.add(MirrorShip(
            id=1, cargo={"capacity": 1000, "used": 0, "contents": {}}, hull_class="raw",
        ))
        seed.commit()
        seed.close()

        S = SessionFactory()
        try:
            ship = S.query(MirrorShip).filter(MirrorShip.id == 1).first()
            ship.cargo = {"capacity": 1000, "used": 50, "contents": {"ore": 50}}

            # A genuinely concurrent, DIFFERENT session commits a real change
            # to the SAME row's `hull_class` -- a column S's own pending
            # mutation never touches (see the schema comment above for why
            # `cargo` itself isn't reused for this half of the proof).
            other = SessionFactory()
            other_row = other.query(MirrorShip).filter(MirrorShip.id == 1).first()
            other_row.hull_class = "cruiser"
            other.commit()
            other.close()

            S.flush()  # THE FIX
            relocked = (
                S.query(MirrorShip)
                .filter(MirrorShip.id == 1)
                .populate_existing()
                .with_for_update()
                .first()
            )

            assert relocked is ship
            # (1) Pending mutation PRESERVED -- flush persisted it first.
            assert ship.cargo["contents"] == {"ore": 50}
            # (2) Fresh concurrent commit SEEN -- proves this is a genuine
            # refresh, not just "flush made the discard moot by coincidence."
            assert ship.hull_class == "cruiser"
        finally:
            S.close()


# --------------------------------------------------------------------------- #
# Part B -- real PlanetaryService.advance_siege 2-turn catch-up, mack's
# accepted discard-repro seed.
# --------------------------------------------------------------------------- #

def _mirror_player_ship_schema():
    Base = declarative_base()

    class MirrorPlayer(Base):
        __tablename__ = "mirror_players_siege_flush"
        id = sa.Column(sa.Integer, primary_key=True)
        current_ship_id = sa.Column(sa.Integer, nullable=True)
        # Part C only -- stand-ins for the real Player columns
        # combat_service.attack_planet mutates in-memory before its
        # settle() call (spend_turns/_regen_turns/attack_drones -=). Part B
        # (Ship-side, 2-turn catch-up) never touches these.
        turns = sa.Column(sa.Integer, nullable=False, default=0)
        lifetime_turns_spent = sa.Column(sa.Integer, nullable=False, default=0)
        attack_drones = sa.Column(sa.Integer, nullable=False, default=0)

    class MirrorShip(Base):
        __tablename__ = "mirror_ships_siege_flush_b"
        id = sa.Column(sa.Integer, primary_key=True)
        cargo = sa.Column(sa.JSON, nullable=False, default=dict)

    return Base, MirrorPlayer, MirrorShip


def _siege_session_factory(Base) -> sessionmaker:
    engine = sa.create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False)


def _anchor_for_elapsed_turns(turns: float) -> datetime:
    """A UTC ``siege_started_at`` whose canonical-hours gap to "now" is
    exactly ``turns * SIEGE_TURN_HOURS``, regardless of the live
    GAME_TIME_SCALE (mirrors test_shield_regen_sr1.py's own
    ``_anchor_for_canonical_hours`` convention). Not frozen -- the ~72-96
    canonical-hour window this test needs (elapsed_turns == 3, comfortably
    mid-window at 3.5 turns = 84 hours) leaves a wide wall-clock margin even
    at a high GAME_TIME_SCALE; test_structures_spine.py's own siege-anchor
    tests rely on the same live-clock convention."""
    canonical_hours = turns * SIEGE_TURN_HOURS
    return datetime.now(UTC) - timedelta(hours=canonical_hours / GAME_TIME_SCALE)


def _make_planet(*, attacker_id) -> SimpleNamespace:
    """SimpleNamespace stand-in -- _skim_siege_stockpiles/advance_siege never
    query or lock the planet themselves (it's already held by the caller's
    settle()/read lock per the function's own docstring), matching
    test_siege_stockpile_skim.py's own make_planet() convention."""
    return SimpleNamespace(
        id=uuid.uuid4(),
        name="Besieged Colony",
        siege_attacker_id=attacker_id,
        under_siege=True,
        siege_started_at=_anchor_for_elapsed_turns(3.5),
        siege_turns=SIEGE_TURNS_THRESHOLD + 1,  # applied_turns == 1 -> pending == 2
        morale=100,
        defense_level=0,
        fuel_ore=1000,
        organics=800,
        equipment=600,
    )


@pytest.fixture(autouse=True)
def _mirror_models(monkeypatch):
    Base, MirrorPlayer, MirrorShip = _mirror_player_ship_schema()
    monkeypatch.setattr(ps, "Player", MirrorPlayer)
    monkeypatch.setattr(ps, "Ship", MirrorShip)
    return Base, MirrorPlayer, MirrorShip


class TestAdvanceSiegeTwoTurnCatchUpConservesPlunder:
    def _seed(self, SessionFactory, MirrorPlayer, MirrorShip):
        seed = SessionFactory()
        seed.add(MirrorShip(id=1, cargo={"capacity": 1_000_000, "used": 0, "contents": {}}))
        seed.add(MirrorPlayer(id=1, current_ship_id=1))
        seed.commit()
        seed.close()

    def test_two_applied_turns_conserve_plunder_across_the_unflushed_gap(
        self, _mirror_models,
    ) -> None:
        """THE FIX, end-to-end through the real catch-up loop: with pending
        == 2 (mack's accepted seed), the ship's final cargo reflects the SUM
        of BOTH iterations' skims, and the planet's stockpile debit exactly
        matches what the ship gained -- conservation holds across the
        unflushed inter-iteration gap."""
        Base, MirrorPlayer, MirrorShip = _mirror_models
        SessionFactory = _siege_session_factory(Base)
        self._seed(SessionFactory, MirrorPlayer, MirrorShip)

        S = SessionFactory()
        try:
            service = PlanetaryService(db=S)
            attacker_id = 1
            planet = _make_planet(attacker_id=attacker_id)

            # Independently derive the expected two-iteration skim from the
            # same formula _skim_siege_stockpiles uses, rather than hard-
            # coding numbers that would silently decouple from the fraction.
            stock = {"fuel_ore": 1000, "organics": 800, "equipment": 600}
            key_for_col = {"fuel_ore": "ore", "organics": "organics", "equipment": "equipment"}
            expected_moved: dict[str, int] = {}
            for _turn in range(2):
                for col, key in key_for_col.items():
                    take = int(stock[col] * SIEGE_STOCKPILE_SKIM_FRACTION)
                    stock[col] -= take
                    expected_moved[key] = expected_moved.get(key, 0) + take

            changed = service.advance_siege(planet)
            assert changed is True

            ship = S.query(MirrorShip).filter(MirrorShip.id == 1).first()
            contents = ship.cargo["contents"]

            assert contents == expected_moved

            # Conservation: planet debit across both turns == ship credit.
            assert planet.fuel_ore == 1000 - (expected_moved["ore"])
            # organics/equipment columns map 1:1 by name to their cargo key.
            debited_total = (
                (1000 - planet.fuel_ore)
                + (800 - planet.organics)
                + (600 - planet.equipment)
            )
            assert debited_total == sum(expected_moved.values())
            assert sum(contents.values()) == debited_total
        finally:
            S.close()

    def test_naive_populate_without_flush_would_only_reflect_the_second_turn(
        self, _mirror_models, monkeypatch,
    ) -> None:
        """Non-vacuous companion / bug reproduction (the discard-repro seed
        the WO calls out explicitly): with the Ship-lock's flush disabled
        (simulating the pre-fix shape -- populate_existing present, no
        preceding flush), the SAME 2-turn catch-up leaves ship.cargo
        reflecting ONLY the second iteration's skim, while the planet was
        still debited for BOTH -- a conservation break, proving the harness
        (not luck) is what makes the fix-verification test above pass.

        Disabling only THIS call is safe: grep confirms
        planetary_service.py has exactly one production db.flush() site
        reachable from this call chain -- this one (the sibling flush()
        calls at ~:1986/:2042/:2138 live in the shield/building-upgrade
        settle paths, never invoked by advance_siege/_apply_siege_turn/
        _skim_siege_stockpiles)."""
        Base, MirrorPlayer, MirrorShip = _mirror_models
        SessionFactory = _siege_session_factory(Base)
        self._seed(SessionFactory, MirrorPlayer, MirrorShip)

        S = SessionFactory()
        try:
            # Disable exactly the one flush _skim_siege_stockpiles calls,
            # simulating the WO's "naive-populate (no flush)" counterfactual
            # without editing production source.
            monkeypatch.setattr(S, "flush", lambda *a, **k: None)

            service = PlanetaryService(db=S)
            attacker_id = 1
            planet = _make_planet(attacker_id=attacker_id)

            stock = {"fuel_ore": 1000, "organics": 800, "equipment": 600}
            key_for_col = {"fuel_ore": "ore", "organics": "organics", "equipment": "equipment"}
            turn2_only: dict[str, int] = {}
            for turn in range(2):
                for col, key in key_for_col.items():
                    take = int(stock[col] * SIEGE_STOCKPILE_SKIM_FRACTION)
                    stock[col] -= take
                    if turn == 1:
                        turn2_only[key] = take

            service.advance_siege(planet)

            ship = S.query(MirrorShip).filter(MirrorShip.id == 1).first()
            contents = ship.cargo["contents"]

            # The bug: ship only reflects the SECOND turn's skim.
            assert contents == turn2_only
            # The planet was still debited for BOTH turns -- conservation
            # break: what left the planet no longer equals what the ship
            # received.
            debited_total = (
                (1000 - planet.fuel_ore)
                + (800 - planet.organics)
                + (600 - planet.equipment)
            )
            assert debited_total > sum(contents.values()), (
                "harness sanity check failed -- without the flush, the "
                "ship must under-report what the planet was debited, "
                "proving the conservation break this WO fixes"
            )
        finally:
            S.close()


# --------------------------------------------------------------------------- #
# Part C -- Site A reentrancy: combat_service.attack_planet's own attacker
# lock resolves to the SAME identity-mapped Player as _skim_siege_
# stockpiles' besieger lock, in the besiege-then-capture combo.
# --------------------------------------------------------------------------- #

class TestBesiegerPlayerReentrancySurvivesTheHoistedFlush:
    """``planet.siege_attacker_id == attacker.id`` -- the canonical
    besiege-then-capture combo gated at combat_service.py:3952-3956 for
    ``siege_vulnerable``. Mirrors attack_planet's own sequence: lock the
    attacker Player (:2081), mutate it UNFLUSHED (spend_turns/_regen_turns/
    attack_drones -=), then call the skim (standing in for settle()'s
    _step3_power_siege -> advance_siege -> _apply_siege_turn cascade,
    :2174) in the SAME session -- a "minimal slice" of attack_planet, same
    justification as test_movement_presence_lock_identity_map.py's own Part
    C (driving _execute_movement fully is impractical)."""

    def _seed(self, SessionFactory, MirrorPlayer, MirrorShip):
        seed = SessionFactory()
        seed.add(MirrorShip(id=1, cargo={"capacity": 1_000_000, "used": 0, "contents": {}}))
        seed.add(MirrorPlayer(
            id=1, current_ship_id=1, turns=100, lifetime_turns_spent=50, attack_drones=10,
        ))
        seed.commit()
        seed.close()

    def _lock_and_mutate_attacker(self, S, MirrorPlayer):
        """Mirrors attack_planet's own attacker lock (:2081) + its three
        unflushed in-memory mutations (spend_turns :2131, _regen_turns
        :2116, attacker.attack_drones -= :2163) before it calls settle()."""
        attacker = (
            S.query(MirrorPlayer)
            .filter(MirrorPlayer.id == 1)
            .populate_existing()
            .with_for_update()
            .first()
        )
        attacker.turns -= 3               # spend_turns
        attacker.lifetime_turns_spent += 3  # spend_turns
        attacker.attack_drones -= 4       # combat drone loss
        return attacker

    def test_flush_before_the_player_lock_preserves_the_callers_pending_mutation(
        self, _mirror_models,
    ) -> None:
        """THE FIX: the hoisted flush (now the FIRST statement in
        _skim_siege_stockpiles after the wanted-stock guard) persists the
        caller's pending attacker mutation before the Player lock's
        populate_existing re-read, so the turn-spend/drone-loss survives --
        no free assault."""
        Base, MirrorPlayer, MirrorShip = _mirror_models
        SessionFactory = _siege_session_factory(Base)
        self._seed(SessionFactory, MirrorPlayer, MirrorShip)

        S = SessionFactory()
        try:
            attacker = self._lock_and_mutate_attacker(S, MirrorPlayer)

            # The besiege-then-capture combo: the skim's besieger IS the
            # SAME Player row attack_planet already holds locked+mutated.
            # _skim_siege_stockpiles is called directly (not via advance_
            # siege), so planet.siege_turns is irrelevant here -- only
            # siege_attacker_id + plunderable stock matter.
            planet = _make_planet(attacker_id=attacker.id)

            service = PlanetaryService(db=S)
            moved = service._skim_siege_stockpiles(planet)
            assert moved  # sanity: the skim actually ran (stock was plunderable)

            # The pending caller mutation survived the Player lock's
            # populate_existing refresh -- same object, values intact.
            assert attacker.turns == 97
            assert attacker.lifetime_turns_spent == 53
            assert attacker.attack_drones == 6
        finally:
            S.close()

    def test_bare_populate_without_the_hoisted_flush_discards_the_free_assault(
        self, _mirror_models, monkeypatch,
    ) -> None:
        """Non-vacuous companion / bug reproduction: with the hoisted flush
        disabled (simulating the pre-fix -- or a Site-A-only fix that never
        hoisted it -- shape), the SAME reentrant sequence silently reverts
        the caller's turn-spend and drone-loss back to their pre-attack
        values -- a repeatable free assault, exactly the residual both
        gates independently found."""
        Base, MirrorPlayer, MirrorShip = _mirror_models
        SessionFactory = _siege_session_factory(Base)
        self._seed(SessionFactory, MirrorPlayer, MirrorShip)

        S = SessionFactory()
        try:
            monkeypatch.setattr(S, "flush", lambda *a, **k: None)

            attacker = self._lock_and_mutate_attacker(S, MirrorPlayer)
            assert (attacker.turns, attacker.lifetime_turns_spent, attacker.attack_drones) == (
                97, 53, 6,
            )

            planet = _make_planet(attacker_id=attacker.id)

            service = PlanetaryService(db=S)
            moved = service._skim_siege_stockpiles(planet)
            assert moved

            # The bug: the Player lock's bare populate_existing() reverted
            # the pending mutation back to the last-flushed (pre-attack)
            # values -- a free assault (turns/drones never actually spent).
            assert attacker.turns == 100, (
                "harness sanity check failed -- without the hoisted flush, "
                "the Player lock's populate_existing() must revert the "
                "pending turn-spend, proving the free-assault bug this "
                "WO fixes"
            )
            assert attacker.lifetime_turns_spent == 50
            assert attacker.attack_drones == 10
        finally:
            S.close()
