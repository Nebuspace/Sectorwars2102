"""WO #3 (mack gate) — real-SQLAlchemy proof for the 3-site fleet-kill lock
cluster in ``fleet_service.py`` / ``bounty_service.py``:

  Site 1 — ``bounty_service.py:717`` (``collect_bounty_share``'s hunter lock):
      plain ``.populate_existing().with_for_update()``, NO flush ahead of it.
      Safe because nothing mutates the hunter row in-session before this
      lock fires (verified below).
  Site 2 — ``fleet_service.py`` ``killed_player`` lock (~:1399 flush,
      ~:1401 ``.populate_existing().with_for_update()``): THE FOOTGUN FIX.
      ``killed_player`` was already loaded UNLOCKED via ``ship.owner`` and
      mutated in-memory by ``ShipService.destroy_ship`` — insurance payout
      (``player.credits += compensation``, ship_service.py:265) and
      escape-pod reseat (``player.current_ship_id = escape_pod.id``,
      ship_service.py:254) — on an ``autoflush=False`` session
      (core/database.py:19), BEFORE this lock. On an EMPTY-CARGO kill,
      ``CombatService._spawn_cargo_wreck`` returns early with no add/flush
      (``if not lost_cargo: return None``, combat_service.py:4790) — so
      there is NO intervening flush between destroy_ship's mutations and
      this lock. A bare ``.populate_existing()`` re-read would silently
      DISCARD both pending mutations; the WO's ``self.db.flush()``
      immediately before the lock (fleet_service.py:1399) neutralizes
      that by persisting them first.
  Site 3 — ``fleet_service.py`` grey-flag re-lock (~:1490): plain
      ``.populate_existing().with_for_update()``, NO flush ahead of it.
      Safe because, by the time this loop runs, ``collect_bounty_share``
      (Site 1) has ALREADY flushed (bounty_service.py:802) any credit
      mutation it made on this same participant row — nothing pending
      left to discard.

Real-SQLAlchemy convention (SQLite, in-memory, single engine so multiple
``Session()`` calls share one DB — mirrors test_bounty_collect_flush_
populate_existing.py / test_money_nolock_rmw_mack.py precedent exactly):
this property lives in SQLAlchemy's identity-map/refresh semantics, not in
Player-specific behavior, and the codebase's hand-rolled ``_FakeSession``
idiom (test_fleet_casualty_succession.py's own ``_FakeQuery`` has NO
identity map at all — confirmed it does not even define
``populate_existing()``, though it never currently exercises this path
since every test there stubs ``_distribute_fleet_kill_rewards`` out) cannot
distinguish "populate_existing() present" from "absent" on a same-session
same-PK re-read. Only a real engine can.

``fleet_service.Player`` / ``bounty_service.Player`` are monkeypatched to a
minimal SQLite-compatible mirror class (same reason as the precedent
files: the real ``Player`` model carries Postgres-only JSONB/ARRAY columns
that block ``Player.__table__.create()`` on SQLite) so the REAL production
methods run unmodified against it. ``with_for_update()`` is a documented
no-op on SQLite — irrelevant here, since this file is entirely about
flush-ordering + identity-map refresh semantics, not real lock acquisition
(that is the orchestrator's live-Postgres leg, same framing as every other
genuine-contention property in this codebase's real-SQLAlchemy regression
files).
"""
from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import sqlalchemy as sa
from sqlalchemy.orm import declarative_base, sessionmaker
from sqlalchemy.pool import StaticPool

from src.services import bounty_service as bs
from src.services import fleet_service as fs
from src.services.fleet_service import FleetService
from src.services.grey_flag_service import _as_aware as gfs_as_aware


# --------------------------------------------------------------------------- #
# Shared mirror schema + session factory
# --------------------------------------------------------------------------- #

def _schema():
    Base = declarative_base()

    class MirrorPlayer(Base):
        __tablename__ = "mirror_players_fleet_kill_cluster"
        id = sa.Column(sa.Integer, primary_key=True)
        credits = sa.Column(sa.Integer, nullable=False, default=0)
        # Stand-in for the real Player.current_ship_id FK — the escape-pod
        # reseat target (ship_service.py:254). An opaque integer id is
        # sufficient: nothing here dereferences it as a real Ship FK.
        current_ship_id = sa.Column(sa.Integer, nullable=True)
        personal_reputation = sa.Column(sa.Integer, nullable=False, default=0)
        grey_kind = sa.Column(sa.String, nullable=True)
        grey_until = sa.Column(sa.DateTime(timezone=True), nullable=True)
        settings = sa.Column(sa.JSON, nullable=True)

    return Base, MirrorPlayer


def _session_factory(Base) -> sessionmaker:
    # autoflush=False deliberately matches the real app Session
    # (core/database.py:19) — the entire bug this WO fixes only exists
    # because autoflush is off.
    engine = sa.create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False)


# --------------------------------------------------------------------------- #
# Site 2 — the footgun fix, isolated mechanism (mirrors fleet_service.py's
# exact two-line shape at ~:1399-1408, standalone — matches the codebase's
# established "isolate the mechanism" convention, test_bounty_collect_flush_
# populate_existing.py Part(a)).
# --------------------------------------------------------------------------- #

class TestSite2IsolatedMechanism:
    def test_flush_then_populate_existing_preserves_pending_mutation_and_sees_fresh_commit(self):
        """THE FIX, isolated: replicate fleet_service.py:1399 (``self.db.
        flush()``) immediately followed by :1401's ``.populate_existing().
        with_for_update()`` re-read on the SAME identity that was just
        mutated in-memory (mirrors destroy_ship's insurance credit +
        escape-pod reseat). Both survive. A genuinely concurrent OTHER
        session's committed change to an unrelated column is also picked
        up fresh — the dual guarantee the WO's fix provides."""
        Base, MirrorPlayer = _schema()
        SessionFactory = _session_factory(Base)

        seed = SessionFactory()
        seed.add(MirrorPlayer(id=1, credits=500, current_ship_id=10, personal_reputation=0))
        seed.commit()
        seed.close()

        S = SessionFactory()
        try:
            # ship.owner lazy-load: killed_player loaded UNLOCKED earlier in
            # this same transaction (destroy_ship's `player = ship.owner`).
            killed_player = S.query(MirrorPlayer).filter(MirrorPlayer.id == 1).first()
            assert killed_player.credits == 500

            # destroy_ship's in-memory mutations, unflushed (ship_service.py
            # :254 reseat, :265 insurance payout).
            killed_player.current_ship_id = 999  # escape-pod id
            killed_player.credits += 300  # insurance compensation

            # A genuinely concurrent, DIFFERENT session commits a real change
            # to an unrelated column on the SAME row in between.
            other = SessionFactory()
            other_row = other.query(MirrorPlayer).filter(MirrorPlayer.id == 1).first()
            other_row.personal_reputation = 77
            other.commit()
            other.close()

            # fleet_service.py:1399 + :1401, verbatim shape.
            S.flush()
            reread = (
                S.query(MirrorPlayer)
                .filter(MirrorPlayer.id == 1)
                .populate_existing()
                .with_for_update()
                .first()
            )

            assert reread is killed_player  # same identity-mapped object
            # (1) Pending mutations PRESERVED — not discarded by the locked
            # re-read, because the flush persisted them first.
            assert killed_player.credits == 800
            assert killed_player.current_ship_id == 999
            # (2) Fresh concurrent commit SEEN — populate_existing's refresh
            # picked up the other session's change, not a stale cached copy.
            assert killed_player.personal_reputation == 77
        finally:
            S.close()

    def test_counterfactual_populate_existing_without_the_flush_discards_the_insurance_payout(self):
        """THE DANGER, isolated: the SAME re-read WITHOUT the flush ahead of
        it — exactly what Site 2 would do if fleet_service.py:1399's
        ``self.db.flush()`` were removed. Proves WHY the flush has to be
        there: on an empty-cargo kill (no intervening cargo-wreck flush),
        this is the footgun the WO's fix closes."""
        Base, MirrorPlayer = _schema()
        SessionFactory = _session_factory(Base)

        seed = SessionFactory()
        seed.add(MirrorPlayer(id=1, credits=500, current_ship_id=10, personal_reputation=0))
        seed.commit()
        seed.close()

        S = SessionFactory()
        try:
            killed_player = S.query(MirrorPlayer).filter(MirrorPlayer.id == 1).first()
            killed_player.current_ship_id = 999  # escape-pod reseat, pending
            killed_player.credits += 300  # insurance payout, pending

            # NO flush() here.
            reread = (
                S.query(MirrorPlayer)
                .filter(MirrorPlayer.id == 1)
                .populate_existing()
                .with_for_update()
                .first()
            )

            assert reread is killed_player
            assert killed_player.credits == 500  # DISCARDED — the footgun
            assert killed_player.current_ship_id == 10  # DISCARDED — the footgun
        finally:
            S.close()


# --------------------------------------------------------------------------- #
# Site 2 + Site 3 — through the REAL production call graph
# (``FleetService._distribute_fleet_kill_rewards``), a real EMPTY-CARGO fleet
# kill: BountyService is stubbed (its own real-SQLAlchemy proof lives in
# TestSite1BountyHunterLockReadsFresh below and in test_bounty_collect_flush_
# populate_existing.py — not this file's concern), but GreyFlagService /
# attack_is_penalty_free / is_good_standing run FOR REAL against the mirror
# schema, so Site 3 fires through genuine production logic, not a
# reimplementation.
# --------------------------------------------------------------------------- #

class _StubBountyServiceNoBounty:
    """Every participant's kill contributes nothing (mirrors an innocent
    victim carrying no bounty) — routes _distribute_fleet_kill_rewards into
    its ``not had_bounty`` branch, which is what reaches Site 3."""

    def __init__(self, db):
        self.db = db

    def collect_bounty_share(self, *, hunter_id, target_id, num_participants, claim_player_pot):
        return {"had_bounty": False, "paid": 0, "system_paid": 0, "player_paid": 0}


class _RecordingRepService:
    """Class-level ``calls`` log (not per-instance) — _distribute_fleet_kill_
    rewards constructs its own ``PersonalReputationService(self.db)``
    internally, so the test has no handle on that specific instance; a
    shared class-level sink is the only way to observe its calls."""

    calls: list = []

    def __init__(self, db):
        self.db = db

    def adjust_reputation(self, pid, delta, reason):
        type(self).calls.append((pid, delta, reason))


class TestDistributeFleetKillRewardsEndToEnd:
    def test_empty_cargo_kill_preserves_insurance_and_reseat_and_fires_grey_flag(self, monkeypatch):
        Base, MirrorPlayer = _schema()
        SessionFactory = _session_factory(Base)
        monkeypatch.setattr(fs, "Player", MirrorPlayer)
        monkeypatch.setattr("src.services.bounty_service.BountyService", _StubBountyServiceNoBounty)
        monkeypatch.setattr(
            "src.services.personal_reputation_service.PersonalReputationService",
            _RecordingRepService,
        )
        _RecordingRepService.calls = []
        # Never silently swallow an internal exception into the log — the
        # method's outer try/except would otherwise mask a broken test
        # setup as "reward distribution quietly did nothing".
        logged_errors = []
        monkeypatch.setattr(fs.logger, "error", lambda *a, **k: logged_errors.append((a, k)))

        KILLED_ID = 1
        PARTICIPANT_ID = 2

        seed = SessionFactory()
        # Killed player: good standing (personal_reputation >= 0), NOT
        # currently grey — so attack_is_penalty_free is False for everyone
        # (Site 3's grey-SET branch is reached) and is_good_standing is True
        # (grey-flag SET requires this).
        seed.add(MirrorPlayer(
            id=KILLED_ID, credits=500, current_ship_id=10,
            personal_reputation=0, grey_kind=None, grey_until=None,
        ))
        seed.add(MirrorPlayer(id=PARTICIPANT_ID, credits=0, current_ship_id=20, personal_reputation=0))
        seed.commit()
        seed.close()

        S = SessionFactory()
        try:
            svc = FleetService(db=S)

            # ship.owner lazy-load: killed_player loaded UNLOCKED, mirrors
            # destroy_ship's `player = ship.owner` (fleet_service.py's own
            # docstring at :1359 traces this exact chain).
            killed_player = S.query(MirrorPlayer).filter(MirrorPlayer.id == KILLED_ID).first()
            assert killed_player.credits == 500

            # destroy_ship's in-memory mutations BEFORE _distribute_fleet_
            # kill_rewards is ever called (ship_service.py:254 reseat, :265
            # insurance payout) — unflushed (autoflush=False). NO cargo-wreck
            # flush intervenes on this EMPTY-CARGO kill (combat_service.py
            # :4790 early-returns before any add/flush when lost_cargo is
            # empty) — this is the exact gap Site 2's fix closes.
            killed_player.current_ship_id = 999  # escape-pod id
            killed_player.credits += 300  # insurance compensation

            killed_ship = SimpleNamespace(owner_id=KILLED_ID)
            killer_fleet = SimpleNamespace(members=[SimpleNamespace(player_id=PARTICIPANT_ID)])

            svc._distribute_fleet_kill_rewards(killed_ship, killer_fleet)

            assert logged_errors == []  # nothing silently swallowed

            # --- Site 2: insurance + reseat survived the flush+populate_existing lock ---
            assert killed_player.credits == 800
            assert killed_player.current_ship_id == 999

            # --- Site 3: grey-flag fired through the REAL production path ---
            # NOTE: SQLAlchemy's identity map holds objects WEAKLY by default;
            # `member` inside the method's grey-flag loop is a local variable
            # that goes out of scope when the method returns, so THIS query
            # may return a freshly-loaded instance rather than the exact
            # in-method object. SQLite's DATETIME(timezone=True) does not
            # round-trip tzinfo (unlike Postgres timestamptz), so a freshly-
            # loaded ``grey_until`` comes back naive — exactly the case
            # ``grey_flag_service._as_aware`` exists to defensively coerce
            # ("a defensive coercion keeps the comparisons correct even if a
            # naive value ever slips in"). Using it here mirrors production
            # and keeps the assertion correct regardless of GC timing.
            participant = S.query(MirrorPlayer).filter(MirrorPlayer.id == PARTICIPANT_ID).first()
            assert participant.grey_kind == "player_attack"
            assert participant.grey_until is not None
            assert gfs_as_aware(participant.grey_until) > datetime.now(UTC)

            # -100 attack_innocent applied via the (stubbed) rep service —
            # confirms the surrounding business logic still runs correctly
            # around the lock-cluster changes.
            assert (PARTICIPANT_ID, -100, "attack_innocent") in _RecordingRepService.calls
        finally:
            S.close()


# --------------------------------------------------------------------------- #
# Site 1 — bounty_service.py:717 hunter lock reads fresh (no flush needed:
# nothing mutates the hunter row in-session before this lock).
# --------------------------------------------------------------------------- #

class TestSite1BountyHunterLockReadsFresh:
    def test_collect_bounty_share_hunter_lock_reads_fresh_concurrent_commit(self, monkeypatch):
        Base, MirrorPlayer = _schema()
        SessionFactory = _session_factory(Base)
        monkeypatch.setattr(bs, "Player", MirrorPlayer)
        monkeypatch.setattr(bs.BountyService, "_write_claim", lambda self, **kwargs: None)

        HUNTER_ID = 1
        TARGET_ID = 2

        seed = SessionFactory()
        seed.add(MirrorPlayer(id=HUNTER_ID, credits=100, personal_reputation=0))
        # A PLAYER-PLACED bounty (not the system pot) deliberately: the
        # system-pot path's designated-member branch additionally calls
        # ``_restore_target_rep_after_system_payout`` ->
        # ``PersonalReputationService(self.db).adjust_reputation`` — a SEPARATE
        # module (``personal_reputation_service.py``) with its OWN top-level
        # ``Player`` import that this test does not monkeypatch, and is not
        # this test's concern (Site 1's lock/read-fresh mechanism is identical
        # regardless of which pot pays out). The player-placed path avoids
        # that dependency entirely.
        seed.add(MirrorPlayer(
            id=TARGET_ID, credits=0, personal_reputation=0,
            settings={"bounties": [
                {"id": "b1", "placed_by": "999", "amount": 500, "type": "player"},
            ]},
        ))
        seed.commit()
        seed.close()

        S = SessionFactory()
        try:
            # Mirrors the fleet loop having touched the hunter's identity
            # earlier in this same session/transaction (e.g. an unrelated
            # read elsewhere in the request) — establishes a cached copy in
            # the identity map BEFORE collect_bounty_share's own lock runs.
            hunter_precheck = S.query(MirrorPlayer).filter(MirrorPlayer.id == HUNTER_ID).first()
            assert hunter_precheck.credits == 100

            # A genuinely concurrent, DIFFERENT session commits a real
            # change to the hunter's balance in between.
            other = SessionFactory()
            other_row = other.query(MirrorPlayer).filter(MirrorPlayer.id == HUNTER_ID).first()
            other_row.credits = 9000
            other.commit()
            other.close()

            service = bs.BountyService(S)
            result = service.collect_bounty_share(
                hunter_id=HUNTER_ID, target_id=TARGET_ID,
                num_participants=1, claim_player_pot=True,
            )

            assert result["success"] is True
            assert result["paid"] == 500

            # Landed on top of the FRESH concurrent balance (9000), not the
            # stale identity-mapped one (100) — Site 1's populate_existing
            # closed the lost-update even with no flush of its own (there
            # was nothing pending on the hunter row to discard).
            assert hunter_precheck.credits == 9000 + 500
            assert result["new_credits"] == 9000 + 500
        finally:
            S.close()


# --------------------------------------------------------------------------- #
# Site 3 — fleet_service.py grey-flag re-lock reads fresh (isolated
# mechanism: the exact query shape at ~:1496-1502, standalone).
# --------------------------------------------------------------------------- #

class TestSite3GreyFlagRelockReadsFresh:
    def test_grey_flag_relock_reads_fresh_concurrent_commit(self):
        Base, MirrorPlayer = _schema()
        SessionFactory = _session_factory(Base)

        MEMBER_ID = 1

        seed = SessionFactory()
        seed.add(MirrorPlayer(id=MEMBER_ID, credits=100, personal_reputation=0))
        seed.commit()
        seed.close()

        S = SessionFactory()
        try:
            # Mirrors the exempt-check loop's earlier plain, UNLOCKED read of
            # this same participant (fleet_service.py ~:1459-1460:
            # `self.db.query(Player).filter(Player.id == pid).first()`).
            member_precheck = S.query(MirrorPlayer).filter(MirrorPlayer.id == MEMBER_ID).first()
            assert member_precheck.personal_reputation == 0

            # A genuinely concurrent, DIFFERENT session commits a real
            # change to the SAME row in between.
            other = SessionFactory()
            other_row = other.query(MirrorPlayer).filter(MirrorPlayer.id == MEMBER_ID).first()
            other_row.personal_reputation = -250
            other.commit()
            other.close()

            # fleet_service.py ~:1496-1502, verbatim shape.
            member = (
                S.query(MirrorPlayer)
                .filter(MirrorPlayer.id == MEMBER_ID)
                .populate_existing()
                .with_for_update()
                .first()
            )

            assert member is member_precheck  # same identity-mapped object
            assert member.personal_reputation == -250  # fresh, not stale 0
        finally:
            S.close()
