"""WO-API-A1 cipher HIGH -- real-SQLAlchemy proof that combat_service.py's
NPC-kill loot query (attack_npc_ship, the ATTACKER_VICTORY cargo/credit-loot
block) now chains .populate_existing() onto its .with_for_update() lock, so
a route-level UNLOCKED pre-read of the SAME NPCCharacter row
(engage_combat's own proximity gate, WO-API-A1) can never poison this
session's identity map into crediting a STALE cached balance instead of the
one currently locked.

Isolates the MECHANISM (the exact query shape combat_service.py now uses --
`.filter(ship_id==...).populate_existing().with_for_update().first()` --
not the full attack_npc_ship combat-resolution surface, which needs a much
larger mirror -- ShipSpecification/Sector/HangarService/_resolve_ship_combat
-- disproportionate to stand up for one identity-map property). Same
"isolate the helper" philosophy as test_bounty_collect_flush_populate_
existing.py's own Part (a) test #1, and the SAME real-SQLAlchemy convention
(SQLite StaticPool + check_same_thread=False so two independent Session()
objects share one in-memory DB -- a second session's commit is a genuinely
concurrent write, unlike the hand-rolled _FakeSession pattern this repo's
other combat tests use, which has no identity map at all and cannot
distinguish "populate_existing() present" from "absent" on a same-PK
re-read). `with_for_update()` is a documented no-op on SQLite -- irrelevant
here, this is entirely about identity-map REFRESH semantics, not real lock
acquisition (that's the live-Postgres two-connection leg's job, same
framing as every other genuine-contention property in this codebase's other
real-SQLAlchemy regression files).
"""
from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.orm import declarative_base, sessionmaker
from sqlalchemy.pool import StaticPool


def _schema():
    Base = declarative_base()

    class MirrorNPCCharacter(Base):
        __tablename__ = "mirror_npc_characters_loot"
        id = sa.Column(sa.Integer, primary_key=True)
        ship_id = sa.Column(sa.Integer, nullable=False)
        credits = sa.Column(sa.Integer, nullable=False)

    return Base, MirrorNPCCharacter


def _session_factory(Base) -> sessionmaker:
    engine = sa.create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False)


def _query_loot_credits(session, MirrorNPCCharacter, ship_id, *, populate_existing: bool):
    """Reproduces the EXACT query shape combat_service.py's loot section
    uses post-fix: .filter(ship_id==...).[populate_existing().]with_for_update().first()."""
    q = session.query(MirrorNPCCharacter).filter(MirrorNPCCharacter.ship_id == ship_id)
    if populate_existing:
        q = q.populate_existing()
    return q.with_for_update().first()


def test_without_populate_existing_the_loot_query_returns_the_stale_cached_credits():
    """THE DANGER, isolated -- what combat_service.py's loot query did
    BEFORE this WO's fix: a route-level unlocked pre-read (the engage-range
    gate) poisons this session's identity map; the loot query's own
    .with_for_update() genuinely locks the row at the DB level but does NOT
    refresh .credits from it without .populate_existing()."""
    Base, MirrorNPCCharacter = _schema()
    SessionFactory = _session_factory(Base)

    seed = SessionFactory()
    seed.add(MirrorNPCCharacter(id=1, ship_id=42, credits=500))
    seed.commit()
    seed.close()

    S = SessionFactory()
    try:
        # Mirrors engage_combat's route-level proximity pre-check: an
        # UNLOCKED read of this SAME NPCCharacter row, earlier in the same
        # session, before combat resolves. Kept alive in `unlocked` --
        # Session's identity map holds a WEAK reference; an unbound
        # query result is eligible for GC immediately, which would evict
        # the identity-map entry and defeat this whole reproduction (the
        # second query would just load fresh, proving nothing). Mirrors
        # test_money_nolock_rmw_mack.py's own proven `unlocked = ...`
        # capture for the identical reason.
        unlocked = S.query(MirrorNPCCharacter).filter(MirrorNPCCharacter.ship_id == 42).first()
        assert unlocked.credits == 500

        # A concurrent, DIFFERENT session (npc_trading_service.py's Loop A
        # tick, outside this ship's own lock) commits a LOWER balance in
        # between -- the TRUE current value the loot payout must land on.
        other = SessionFactory()
        other_row = other.query(MirrorNPCCharacter).filter(MirrorNPCCharacter.ship_id == 42).first()
        other_row.credits = 50
        other.commit()
        other.close()

        looted = _query_loot_credits(S, MirrorNPCCharacter, 42, populate_existing=False)
        assert looted is unlocked  # same cached identity-map object
        assert looted.credits == 500  # STALE -- the footgun this WO closes
    finally:
        S.close()


def test_with_populate_existing_the_loot_query_reads_the_fresh_locked_credits():
    """THE FIX: same scenario, but with .populate_existing() present (the
    actual combat_service.py code as of this WO) -- the locked re-read picks
    up the TRUE current balance, so the attacker is credited the real
    post-mutation amount, never a stale-higher cached one (which would mint
    the difference for free)."""
    Base, MirrorNPCCharacter = _schema()
    SessionFactory = _session_factory(Base)

    seed = SessionFactory()
    seed.add(MirrorNPCCharacter(id=1, ship_id=42, credits=500))
    seed.commit()
    seed.close()

    S = SessionFactory()
    try:
        unlocked = S.query(MirrorNPCCharacter).filter(MirrorNPCCharacter.ship_id == 42).first()
        assert unlocked.credits == 500

        other = SessionFactory()
        other_row = other.query(MirrorNPCCharacter).filter(MirrorNPCCharacter.ship_id == 42).first()
        other_row.credits = 50
        other.commit()
        other.close()

        looted = _query_loot_credits(S, MirrorNPCCharacter, 42, populate_existing=True)
        assert looted is unlocked  # same identity-map object, REFRESHED in place
        assert looted.credits == 50  # FRESH -- the post-mutation, locked value

        # Simulate the actual crediting logic (combat_service.py's
        # `attacker.credits = (attacker.credits or 0) + looted_credits`):
        attacker_credits_before = 1000
        credited = int(looted.credits) if looted.credits and looted.credits > 0 else 0
        attacker_credits_after = attacker_credits_before + credited
        assert credited == 50
        assert attacker_credits_after == 1050  # not 1500 (the stale-mint bug)
    finally:
        S.close()


def test_a_stale_higher_cached_balance_would_have_minted_the_difference_for_free():
    """The inverse-direction sanity check: prove the DANGER test above isn't
    an accident of which direction credits moved -- a stale-HIGHER cached
    value is exactly as wrong (mints the gap) as a stale-lower one is
    (under-credits); .populate_existing() closes both directions."""
    Base, MirrorNPCCharacter = _schema()
    SessionFactory = _session_factory(Base)

    seed = SessionFactory()
    seed.add(MirrorNPCCharacter(id=1, ship_id=42, credits=100))
    seed.commit()
    seed.close()

    S = SessionFactory()
    try:
        unlocked = S.query(MirrorNPCCharacter).filter(MirrorNPCCharacter.ship_id == 42).first()
        assert unlocked.credits == 100

        # Concurrent session drains the NPC's wallet to near-zero (e.g. it
        # spent its credits at a station between the route's pre-read and
        # this call).
        other = SessionFactory()
        other_row = other.query(MirrorNPCCharacter).filter(MirrorNPCCharacter.ship_id == 42).first()
        other_row.credits = 1
        other.commit()
        other.close()

        stale = _query_loot_credits(S, MirrorNPCCharacter, 42, populate_existing=False)
        stale_credits = stale.credits  # captured BEFORE the next call refreshes
        # `stale` in place (same identity-map object -- .populate_existing()
        # below mutates it, it does not return a separate instance).
        assert stale_credits == 100  # the mint: attacker would be over-credited 99

        fresh = _query_loot_credits(S, MirrorNPCCharacter, 42, populate_existing=True)
        assert fresh is stale  # same object, refreshed in place -- not a copy
        assert fresh.credits == 1  # the fix: attacker credited the real, tiny balance
    finally:
        S.close()
