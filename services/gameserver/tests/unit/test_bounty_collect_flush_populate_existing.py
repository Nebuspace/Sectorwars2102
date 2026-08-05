"""WO-BOUNTY-COLLECT-FLUSH — real-SQLAlchemy proof for the two-part fix:

  1. ``collect_bounty`` now calls ``self.db.flush()`` immediately BEFORE its
     ``_load_two_players_for_update(...)`` call, so any pending in-memory
     mutation the caller made on the collector/target rows earlier in the
     SAME transaction (combat_service.attack_player mutates quantum-wallet
     loot, drone counts, and ship-destruction state on ``attacker``/
     ``defender`` before calling ``collect_bounty`` — on a session opened
     ``autoflush=False``, core/database.py:19) is persisted BEFORE the
     locked re-read, not silently discarded by it.
  2. ``_load_two_players_for_update`` now chains ``.populate_existing()``
     onto every ``with_for_update()`` lock query (all three lock shapes:
     the ascending-id pair and the defensive equal-id case) — mirrors
     contract_service's own ``_load_player(for_update=True)`` twin. This
     closes ``cancel_bounty``'s stale-placer lost-update (the route's
     ``get_current_player`` dependency pre-loads the placer UNLOCKED on the
     same session; without populate_existing, the later with_for_update()
     re-read returned the same stale cached object).

Why these two changes are safe TOGETHER: adding populate_existing() alone
would have been a footgun for collect_bounty specifically — a locked
re-read that REFRESHES a mapped instance's attributes from the DB row
necessarily overwrites any not-yet-flushed in-memory change on that same
instance with whatever the DB currently holds. Since combat mutates
attacker/defender in-memory before calling collect_bounty, on an
autoflush=False session, that would silently drop the combat loot. Change
(1) neutralizes exactly that risk for collect_bounty by flushing first, so
populate_existing's refresh re-reads the caller's OWN pending mutations
back rather than clobbering them — while still genuinely re-reading (not
returning stale cache) with respect to any OTHER session's concurrent
commit. cancel_bounty needs no flush of its own: verified end-to-end (see
bounty_service.py's ``cancel_bounty`` — the entry lookup / removal /
``placer.credits += refund`` sequence all run strictly AFTER its
``_load_two_players_for_update`` call; nothing is mutated on placer/target
before the lock, so there is nothing pending to discard).

Real-SQLAlchemy convention (SQLite, ``StaticPool`` + ``check_same_thread=
False`` so multiple ``Session()`` calls from one engine share ONE
underlying in-memory database — a second, independently-committing session
genuinely simulates "a concurrent transaction changed this row in
between"), mirroring test_storage_deposit_prelock_identity_map.py and
test_money_nolock_rmw_mack.py's own precedent exactly: this property lives
in SQLAlchemy's identity-map/refresh semantics, not in Player- or
StorageLocker-specific behavior, and the hand-rolled ``_FakeSession``
pattern the rest of this service's test suite uses (test_bounty_service_
nh2.py / test_bounty_dual_lock_order.py) has NO identity map at all, so it
cannot distinguish "populate_existing() present" from "absent" on a same-
session same-PK re-read — this is exactly the gap those two existing files
cannot close, per the WO.

``bounty_service.Player`` is monkeypatched to a minimal SQLite-compatible
mirror class (same reason as the two precedent files: the real ``Player``
model carries several Postgres-only JSONB/ARRAY columns that block
``Player.__table__.create()`` on SQLite entirely) so the REAL
``BountyService`` methods run unmodified against it, not a hand-copied
guess at their query shape. ``BountyService._write_claim`` is stubbed to a
no-op: the real ``BountyClaim`` model FKs into the real (Postgres-only)
``players`` table and uses ``postgresql.UUID`` columns, also incompatible
with the SQLite mirror schema here — irrelevant to what's under test
(claim provenance rows are a separate concern from the Player-row
flush/lock/refresh sequence). ``with_for_update()`` is a documented no-op
on SQLite — irrelevant here too, since this file is entirely about
flush-ordering + identity-map refresh semantics, not real lock acquisition
(that stays the live-Postgres two-connection leg's job, same framing as
every other genuine-contention property in this codebase's other
real-SQLAlchemy regression files).
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import declarative_base, sessionmaker
from sqlalchemy.pool import StaticPool

from src.services import bounty_service as bs


def _schema():
    Base = declarative_base()

    class MirrorPlayer(Base):
        __tablename__ = "mirror_players_bounty_flush"
        id = sa.Column(sa.Integer, primary_key=True)
        credits = sa.Column(sa.Integer, nullable=False)
        settings = sa.Column(sa.JSON, nullable=True)
        # Stand-in for the real Player.quantum_shards column — a field
        # collect_bounty/cancel_bounty never touch themselves, so any
        # observed change to it can ONLY have come from the pending
        # in-memory mutation under test (combat's quantum-wallet loot
        # transfer, WO description :917), never from bounty payout math.
        quantum_shards = sa.Column(sa.Integer, nullable=False, default=0)

    return Base, MirrorPlayer


def _session_factory(Base) -> sessionmaker:
    # autoflush=False deliberately matches the real app Session
    # (core/database.py:19) — the entire bug this WO fixes only exists
    # because autoflush is off; a default autoflush=True session would
    # silently persist pending mutations on every SELECT and mask exactly
    # the behavior under test.
    engine = sa.create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False)


@pytest.fixture(autouse=True)
def _stub_write_claim(monkeypatch):
    """Neutralize claim-provenance inserts — see module docstring. Not the
    concern under test; the real BountyClaim model can't run on SQLite."""
    monkeypatch.setattr(bs.BountyService, "_write_claim", lambda self, **kwargs: None)


# --------------------------------------------------------------------------- #
# Part (a): collect_bounty's pending-mutation-preservation + fresh-read fix.
# --------------------------------------------------------------------------- #

class TestCollectBountyPreservesPendingMutationAndSeesFreshCommit:
    def test_direct_populate_existing_without_a_prior_flush_discards_the_pending_mutation(
        self, monkeypatch
    ) -> None:
        """THE DANGER, isolated: calling the (now-populate_existing-carrying)
        ``_load_two_players_for_update`` helper WITHOUT collect_bounty's new
        flush() ahead of it is exactly the W5 footgun the WO calls out —
        proves WHY the flush has to be there, by showing what happens
        without it. Bypasses collect_bounty entirely (which always flushes
        now) and calls the private helper directly to isolate this
        mechanism from the rest of collect_bounty's behavior."""
        Base, MirrorPlayer = _schema()
        SessionFactory = _session_factory(Base)
        monkeypatch.setattr(bs, "Player", MirrorPlayer)

        seed = SessionFactory()
        seed.add(MirrorPlayer(id=1, credits=500, settings={}, quantum_shards=0))
        seed.add(MirrorPlayer(id=2, credits=0, settings={}, quantum_shards=0))
        seed.commit()
        seed.close()

        S = SessionFactory()
        try:
            service = bs.BountyService(S)

            # Mirrors combat_service.attack_player: attacker already loaded
            # UNLOCKED earlier in this same transaction, then mutated
            # in-memory (quantum-wallet loot) — never flushed.
            attacker = S.query(MirrorPlayer).filter(MirrorPlayer.id == 1).first()
            attacker.quantum_shards = 250  # pending, unflushed

            # NO flush() here — the counterfactual: what collect_bounty's own
            # locked re-read would do if the WO's flush() were removed.
            player_a, player_b = service._load_two_players_for_update(1, 2)

            assert player_a is attacker  # same cached identity-map object
            assert player_a.quantum_shards == 0  # DISCARDED — the footgun
        finally:
            S.close()

    def test_collect_bounty_preserves_pending_mutation_and_sees_fresh_concurrent_commit(
        self, monkeypatch
    ) -> None:
        """THE FIX, end-to-end through the real public method: an unflushed
        pending mutation on the collector (combat loot) survives
        collect_bounty's flush+populate_existing sequence intact, AND a
        genuinely concurrent, already-committed change from a DIFFERENT
        session on that same row is picked up fresh (not the stale
        pre-loaded value) — the two properties the WO's fix jointly
        guarantees."""
        Base, MirrorPlayer = _schema()
        SessionFactory = _session_factory(Base)
        monkeypatch.setattr(bs, "Player", MirrorPlayer)

        seed = SessionFactory()
        seed.add(MirrorPlayer(id=1, credits=500, settings={}, quantum_shards=0))
        seed.add(MirrorPlayer(
            id=2, credits=0, quantum_shards=0,
            settings={"bounties": [
                {"id": "b1", "placed_by": "999", "amount": 300, "type": "player"},
            ]},
        ))
        seed.commit()
        seed.close()

        S = SessionFactory()
        try:
            # Attacker (collector) pre-loaded UNLOCKED earlier in this same
            # transaction, exactly as combat_service.attack_player already
            # holds `attacker` before calling collect_bounty.
            attacker = S.query(MirrorPlayer).filter(MirrorPlayer.id == 1).first()
            assert attacker.credits == 500

            # Combat's quantum-wallet loot transfer, in-memory, unflushed
            # (autoflush=False — nothing persists this yet).
            attacker.quantum_shards = 250

            # A genuinely concurrent, DIFFERENT session commits a real
            # change to the SAME row in between — some unrelated economic
            # event landing on the attacker's credits.
            other = SessionFactory()
            other_row = other.query(MirrorPlayer).filter(MirrorPlayer.id == 1).first()
            other_row.credits = 999
            other.commit()
            other.close()

            service = bs.BountyService(S)
            result = service.collect_bounty(collector_id=1, target_id=2)

            assert result["success"] is True
            assert result["total_collected"] == 300

            # (1) Pending mutation PRESERVED — not discarded by the locked
            # re-read, because collect_bounty flushed it first.
            assert attacker.quantum_shards == 250

            # (2) Fresh concurrent commit SEEN — populate_existing's re-read
            # picked up 999 (not the stale 500 this session first loaded),
            # so the bounty payout lands on top of the REAL current balance.
            assert attacker.credits == 999 + 300
            assert result["new_credits"] == 999 + 300
        finally:
            S.close()


# --------------------------------------------------------------------------- #
# Part (b): cancel_bounty now reads the placer fresh (no flush needed — lock
# strictly precedes any mutation in cancel_bounty, verified in the module
# docstring above).
# --------------------------------------------------------------------------- #

class TestCancelBountyReadsPlacerFresh:
    def test_cancel_bounty_refunds_against_the_fresh_concurrently_committed_balance(
        self, monkeypatch
    ) -> None:
        Base, MirrorPlayer = _schema()
        SessionFactory = _session_factory(Base)
        monkeypatch.setattr(bs, "Player", MirrorPlayer)

        seed = SessionFactory()
        seed.add(MirrorPlayer(id=1, credits=1000, settings={}, quantum_shards=0))
        seed.add(MirrorPlayer(
            id=2, credits=0, quantum_shards=0,
            settings={"bounties": [
                {"id": "b1", "placed_by": "1", "amount": 200, "type": "player"},
            ]},
        ))
        seed.commit()
        seed.close()

        S = SessionFactory()
        try:
            # Mirrors the route's get_current_player dependency: placer
            # already loaded UNLOCKED on this same session before
            # cancel_bounty is called.
            placer = S.query(MirrorPlayer).filter(MirrorPlayer.id == 1).first()
            assert placer.credits == 1000

            # A different session commits a real, concurrent change to the
            # placer's balance in between (e.g. some other unrelated sale
            # settling) — the STALE value this session first loaded is 1000;
            # the TRUE current value is 5000.
            other = SessionFactory()
            other_row = other.query(MirrorPlayer).filter(MirrorPlayer.id == 1).first()
            other_row.credits = 5000
            other.commit()
            other.close()

            service = bs.BountyService(S)
            result = service.cancel_bounty(placer_id=1, bounty_id="b1", target_id=2)

            assert result["success"] is True
            assert result["refund"] == 200

            # Refund landed on top of the FRESH balance (5000), not the
            # stale identity-mapped one (1000) — proves populate_existing
            # closed the lost-update.
            assert placer.credits == 5000 + 200
            assert result["remaining_credits"] == 5000 + 200
        finally:
            S.close()
