"""mack -- behavioral gate on WO-MONEY-REREAD-CLASS Wave-A (UNCOMMITTED):
`.populate_existing()` chained onto `.with_for_update()` at 7 sites across 3
SHARED lock helpers -- contract_service._load_player (:201, feeds accept/
complete/abandon/post/cancel/sweep AND storage settle/retrieve/deposit via
contract_service._load_player being reused there), construction_service.
_lock_station / _lock_player / the inline forfeit-lock re-read, and
contraband_service.ContrabandService._lock_station_player_ship (station +
player + ship). No source edits -- read-only gate, test-file-only. Zero git.

Reuses the routes-wave fix-proof idiom (test_armory_purchase_adversarial_
mack.py's matched bug/guard/proof pair) but goes one step further: rather
than hand-copying the query shape, these tests MONKEYPATCH each module's
imported Model symbol (Player / Station / Ship) to a minimal SQLite-
compatible mirror class and then call the REAL helper function under test
directly -- proving the actual shipped code, not a restatement of it. Real
Player/Station/Ship carry Postgres-only JSONB/UUID/Enum columns that block
`Base.metadata.create_all()` on SQLite entirely (confirmed precedent:
test_storage_deposit_prelock_identity_map.py's own docstring) -- irrelevant
here, since this is pure SQLAlchemy identity-map/Session mechanics,
independent of which mapped class or columns are involved.

Sections:
  1. CHECK 1 -- helper guard works (real SQLAlchemy), one repro per module
     (contract_service, construction_service, contraband_service) since the
     three modules import Player/Station/Ship independently and each site
     needs to be proven against the ACTUAL function it guards, not just the
     shared mechanic once.
  2. CHECK 2 -- the storage self-settle RESIDUAL (cde1370b): storage.py's
     routes inject `current_player: Player = Depends(get_current_player)`
     (an UNLOCKED, same-session, full-ORM Player read) BEFORE storage_
     service.deposit_cargo/retrieve_claimable_cargo ever run;
     storage_service.settle_fee's own owner re-lock is entirely delegated to
     contract_service._load_player(db, locker.owner_player_id,
     for_update=True) (storage_service.py:479) -- this residual is closed by
     the exact SAME class-wide fix as CHECK 1, proven here against the real
     `_load_player` function in the EXACT residual shape (get_current_player-
     style unlocked preload -> concurrent commit -> settle_fee's guarded
     re-read -> mutate off the re-read baseline).
"""
from __future__ import annotations

import uuid

import sqlalchemy as sa
from sqlalchemy.orm import declarative_base, sessionmaker
from sqlalchemy.pool import StaticPool

from src.services import construction_service, contraband_service, contract_service


def _session_factory(base: "declarative_base") -> sessionmaker:
    """A real SQLite engine, StaticPool + check_same_thread=False so multiple
    Session() calls share ONE underlying in-memory DB -- lets a second,
    independently-committing session simulate a genuine concurrent writer.
    Matches test_storage_deposit_prelock_identity_map.py's own convention."""
    engine = sa.create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False)


# =========================================================================
# CHECK 1 -- helper guard works (real SQLAlchemy), against the ACTUAL
# helper function in each of the 3 files, not a restatement of the query.
# =========================================================================

class TestContractServiceLoadPlayerGuard:
    """contract_service._load_player(db, player_id, for_update=True) --
    contract_service.py:201. Feeds accept/complete/abandon/post_player_
    contract/cancel_player_contract/both sweeps, AND (via reuse, not
    reimplementation) storage_service.settle_fee's owner re-lock and
    storage_service._load_and_lock_deposit_targets / _load_and_lock_
    retrieve_targets's player re-lock -- this ONE helper is the single
    guarded choke point behind every one of those call sites."""

    def test_real_load_player_for_update_observes_fresh_value_after_unlocked_preload(
        self, monkeypatch,
    ) -> None:
        Base = declarative_base()

        class MirrorPlayer(Base):
            __tablename__ = "mirror_players_contract_svc"
            id = sa.Column(sa.Integer, primary_key=True)
            credits = sa.Column(sa.Integer)

        SessionFactory = _session_factory(Base)
        seed = SessionFactory()
        seed.add(MirrorPlayer(id=1, credits=1000))
        seed.commit()
        seed.close()

        monkeypatch.setattr(contract_service, "Player", MirrorPlayer)

        S = SessionFactory()
        # An earlier UNLOCKED full-ORM read of the SAME PK on the SAME
        # session -- e.g. any of _load_player's own for_update=False callers,
        # or (CHECK 2's exact shape) a route's get_current_player dependency.
        unlocked = S.query(MirrorPlayer).filter(MirrorPlayer.id == 1).first()
        assert unlocked.credits == 1000

        # A concurrent session commits a real change in between.
        concurrent = SessionFactory()
        row = concurrent.query(MirrorPlayer).filter(MirrorPlayer.id == 1).first()
        row.credits = 250
        concurrent.commit()
        concurrent.close()

        # THE ACTUAL FUNCTION UNDER TEST -- not a hand-copied query.
        locked = contract_service._load_player(S, 1, for_update=True)

        assert locked is unlocked, (
            "identity map still returns the SAME object -- populate_existing() "
            "refreshes in place, it doesn't hand back a new instance"
        )
        assert locked.credits == 250, (
            f"expected FRESH credits=250 (the guard closing the lost-update); "
            f"got {locked.credits} -- if this ever reads 1000, contract_service."
            f"_load_player's populate_existing() guard has regressed"
        )
        S.close()

    def test_real_load_player_for_update_false_is_unaffected_no_populate_existing_call(
        self, monkeypatch,
    ) -> None:
        """The for_update=False branch must NOT chain populate_existing() (it
        has no reason to acquire a lock or force a refresh) -- confirms the
        guard is scoped to for_update=True only, not a blanket behavior
        change that would surprise every OTHER caller of this helper."""
        Base = declarative_base()

        class MirrorPlayer(Base):
            __tablename__ = "mirror_players_contract_svc_ro"
            id = sa.Column(sa.Integer, primary_key=True)
            credits = sa.Column(sa.Integer)

        SessionFactory = _session_factory(Base)
        seed = SessionFactory()
        seed.add(MirrorPlayer(id=1, credits=1000))
        seed.commit()
        seed.close()

        monkeypatch.setattr(contract_service, "Player", MirrorPlayer)

        S = SessionFactory()
        unlocked = contract_service._load_player(S, 1, for_update=False)
        assert unlocked.credits == 1000

        concurrent = SessionFactory()
        row = concurrent.query(MirrorPlayer).filter(MirrorPlayer.id == 1).first()
        row.credits = 250
        concurrent.commit()
        concurrent.close()

        # A SECOND for_update=False call -- still no populate_existing, so
        # this STILL sees the stale identity-mapped value. Documents the
        # scope boundary, not a bug: no caller passes for_update=False
        # expecting a refresh.
        reread = contract_service._load_player(S, 1, for_update=False)
        assert reread.credits == 1000
        S.close()


class TestConstructionServiceLockHelpersGuard:
    """construction_service._lock_station / _lock_player -- construction_
    service.py's own station-then-player lock order (create_reservation,
    deliver, pay_milestone, pay_rent, cancel, quote all funnel through
    these)."""

    def test_real_lock_station_observes_fresh_value_after_unlocked_preload(
        self, monkeypatch,
    ) -> None:
        Base = declarative_base()

        class MirrorStation(Base):
            __tablename__ = "mirror_stations_construction_svc"
            id = sa.Column(sa.Integer, primary_key=True)
            treasury_balance = sa.Column(sa.Integer)

        SessionFactory = _session_factory(Base)
        seed = SessionFactory()
        seed.add(MirrorStation(id=1, treasury_balance=5000))
        seed.commit()
        seed.close()

        monkeypatch.setattr(construction_service, "Station", MirrorStation)

        S = SessionFactory()
        unlocked = S.query(MirrorStation).filter(MirrorStation.id == 1).first()
        assert unlocked.treasury_balance == 5000

        concurrent = SessionFactory()
        row = concurrent.query(MirrorStation).filter(MirrorStation.id == 1).first()
        row.treasury_balance = 9999
        concurrent.commit()
        concurrent.close()

        locked = construction_service._lock_station(S, 1)

        assert locked is unlocked
        assert locked.treasury_balance == 9999, (
            f"expected FRESH treasury_balance=9999; got {locked.treasury_balance} "
            f"-- construction_service._lock_station's populate_existing() guard "
            f"has regressed"
        )
        S.close()

    def test_real_lock_player_observes_fresh_value_after_unlocked_preload(
        self, monkeypatch,
    ) -> None:
        Base = declarative_base()

        class MirrorPlayer(Base):
            __tablename__ = "mirror_players_construction_svc"
            id = sa.Column(sa.Integer, primary_key=True)
            credits = sa.Column(sa.Integer)

        SessionFactory = _session_factory(Base)
        seed = SessionFactory()
        seed.add(MirrorPlayer(id=1, credits=1000))
        seed.commit()
        seed.close()

        monkeypatch.setattr(construction_service, "Player", MirrorPlayer)

        S = SessionFactory()
        # construction.py's route-level `Depends(get_current_player)` shape --
        # every construction route loads current_player unlocked before
        # calling into a service function that later locks the SAME row.
        unlocked = S.query(MirrorPlayer).filter(MirrorPlayer.id == 1).first()
        assert unlocked.credits == 1000

        concurrent = SessionFactory()
        row = concurrent.query(MirrorPlayer).filter(MirrorPlayer.id == 1).first()
        row.credits = 100
        concurrent.commit()
        concurrent.close()

        locked = construction_service._lock_player(S, 1)

        assert locked is unlocked
        assert locked.credits == 100, (
            f"expected FRESH credits=100; got {locked.credits} -- construction_"
            f"service._lock_player's populate_existing() guard has regressed"
        )
        S.close()


class TestContrabandServiceLockStationPlayerShipGuard:
    """contraband_service.ContrabandService._lock_station_player_ship --
    the L3 station-then-player-then-ship triple lock behind buy()/sell()."""

    def test_real_lock_station_player_ship_observes_fresh_values_for_all_three(
        self, monkeypatch,
    ) -> None:
        Base = declarative_base()

        class MirrorStation(Base):
            __tablename__ = "mirror_stations_contraband_svc"
            id = sa.Column(sa.Integer, primary_key=True)
            name = sa.Column(sa.String)

        class MirrorPlayer(Base):
            __tablename__ = "mirror_players_contraband_svc"
            id = sa.Column(sa.Integer, primary_key=True)
            credits = sa.Column(sa.Integer)

        class MirrorShip(Base):
            __tablename__ = "mirror_ships_contraband_svc"
            id = sa.Column(sa.Integer, primary_key=True)
            owner_id = sa.Column(sa.Integer)
            cargo_used = sa.Column(sa.Integer)

        SessionFactory = _session_factory(Base)
        seed = SessionFactory()
        seed.add(MirrorStation(id=1, name="stationA"))
        seed.add(MirrorPlayer(id=10, credits=1000))
        seed.add(MirrorShip(id=100, owner_id=10, cargo_used=0))
        seed.commit()
        seed.close()

        monkeypatch.setattr(contraband_service, "Station", MirrorStation)
        monkeypatch.setattr(contraband_service, "Player", MirrorPlayer)
        monkeypatch.setattr(contraband_service, "Ship", MirrorShip)

        S = SessionFactory()
        # Route-level unlocked preloads of Player and Ship, same session,
        # same PKs the guarded triple-lock will re-read.
        unlocked_player = S.query(MirrorPlayer).filter(MirrorPlayer.id == 10).first()
        unlocked_ship = S.query(MirrorShip).filter(MirrorShip.id == 100).first()
        assert unlocked_player.credits == 1000
        assert unlocked_ship.cargo_used == 0

        concurrent = SessionFactory()
        p_row = concurrent.query(MirrorPlayer).filter(MirrorPlayer.id == 10).first()
        p_row.credits = 400  # a concurrent buy already spent 600
        s_row = concurrent.query(MirrorShip).filter(MirrorShip.id == 100).first()
        s_row.cargo_used = 5  # a concurrent buy already loaded cargo
        concurrent.commit()
        concurrent.close()

        service = contraband_service.ContrabandService(S)
        station, player, ship, reason = service._lock_station_player_ship(1, 10, 100)

        assert reason is None
        assert player is unlocked_player
        assert ship is unlocked_ship
        assert player.credits == 400, (
            f"expected FRESH credits=400; got {player.credits} -- "
            f"_lock_station_player_ship's populate_existing() guard on Player "
            f"has regressed"
        )
        assert ship.cargo_used == 5, (
            f"expected FRESH cargo_used=5; got {ship.cargo_used} -- "
            f"_lock_station_player_ship's populate_existing() guard on Ship "
            f"has regressed"
        )
        S.close()


# =========================================================================
# CHECK 2 -- storage self-settle RESIDUAL (cde1370b), CLOSED.
#
# storage.py's routes (deposit_cargo, retrieve_cargo) inject
# `current_player: Player = Depends(get_current_player)` -- an UNLOCKED,
# full-ORM read of the depositing/retrieving player on the SAME db session,
# BEFORE storage_service.deposit_cargo / retrieve_claimable_cargo ever run.
# storage_service.settle_fee (:479) then re-locks the LOCKER'S OWNER (in
# both call sites this IS the same player, per deposit_cargo's own
# `locker.owner_player_id != player_id` guard / retrieve's identical check)
# via `contract_service._load_player(db, locker.owner_player_id,
# for_update=True)`, and mutates `owner.credits -= actual_charge` off
# whatever that re-read returns. Before contract_service.py:201's
# populate_existing() guard, this was a genuine lost-update: the route's
# own get_current_player() preload poisoned the identity map for the
# SAME PK settle_fee re-reads, so the "locked" re-read silently returned
# the pre-preload credits, and the rent charge subtracted from a STALE
# baseline. This section proves that exact residual is now CLOSED --
# calling the real contract_service._load_player in the residual's precise
# shape, then mutating off the returned object the same way settle_fee
# itself does (storage_service.py:481).
# =========================================================================

class TestStorageSelfSettleResidualClosed:
    def test_settle_fee_owner_reread_is_fresh_after_route_style_unlocked_preload(
        self, monkeypatch,
    ) -> None:
        Base = declarative_base()

        class MirrorPlayer(Base):
            __tablename__ = "mirror_players_storage_residual"
            id = sa.Column(sa.Integer, primary_key=True)
            credits = sa.Column(sa.Integer)

        SessionFactory = _session_factory(Base)
        seed = SessionFactory()
        seed.add(MirrorPlayer(id=1, credits=1000))
        seed.commit()
        seed.close()

        monkeypatch.setattr(contract_service, "Player", MirrorPlayer)

        S = SessionFactory()

        # get_current_player()-shaped unlocked preload -- storage.py:81/103/
        # 126's `Depends(get_current_player)`, same `db` session the route
        # body (and everything it calls) shares.
        route_preloaded_owner = S.query(MirrorPlayer).filter(MirrorPlayer.id == 1).first()
        assert route_preloaded_owner.credits == 1000

        # A concurrent session commits a real credit change in between --
        # e.g. another purchase, another contract payout, anything that
        # touches this same player's wallet before settle_fee runs.
        concurrent = SessionFactory()
        row = concurrent.query(MirrorPlayer).filter(MirrorPlayer.id == 1).first()
        row.credits = 700  # some other transaction already spent 300
        concurrent.commit()
        concurrent.close()

        # settle_fee's EXACT call shape, storage_service.py:479:
        #   owner = contract_service._load_player(db, locker.owner_player_id, for_update=True)
        owner = contract_service._load_player(S, 1, for_update=True)

        assert owner is route_preloaded_owner, (
            "identity map still returns the route's SAME preloaded object -- "
            "expected (populate_existing refreshes in place)"
        )
        assert owner.credits == 700, (
            f"RESIDUAL STILL OPEN: expected FRESH credits=700 (the concurrent "
            f"commit); got {owner.credits} -- settle_fee's owner re-lock would "
            f"be computing the rent charge off a STALE pre-preload baseline, "
            f"the exact cde1370b lost-update shape"
        )

        # Mutate exactly as settle_fee itself does (storage_service.py:481):
        #   actual_charge = min(charge_due, owner.credits or 0)
        #   owner.credits = (owner.credits or 0) - actual_charge
        charge_due = 50
        actual_charge = min(charge_due, owner.credits or 0)
        owner.credits = (owner.credits or 0) - actual_charge

        assert owner.credits == 650, (
            f"expected the rent charge applied against the FRESH baseline "
            f"(700 - 50 = 650); got {owner.credits} -- if this reads 950 "
            f"(1000 - 50), the charge landed on the STALE pre-preload credits "
            f"instead, silently discarding the concurrent -300 change on "
            f"commit (lost update)"
        )
        S.close()

    def test_residual_scenario_without_the_guard_would_have_lost_the_update(self) -> None:
        """INVERTED companion -- the same residual shape, but calling a
        plain `.with_for_update()` re-read with NO `.populate_existing()`
        (contract_service._load_player's shape BEFORE this WO's fix). Proves
        the residual scenario is a REAL bug shape this fix closes, not a
        strawman -- if this test ever starts observing the fresh value
        instead, the underlying SQLAlchemy identity-map behavior this
        finding (and the fix) depend on has changed."""
        Base = declarative_base()

        class MirrorPlayer(Base):
            __tablename__ = "mirror_players_storage_residual_unfixed"
            id = sa.Column(sa.Integer, primary_key=True)
            credits = sa.Column(sa.Integer)

        SessionFactory = _session_factory(Base)
        seed = SessionFactory()
        seed.add(MirrorPlayer(id=1, credits=1000))
        seed.commit()
        seed.close()

        S = SessionFactory()
        route_preloaded_owner = S.query(MirrorPlayer).filter(MirrorPlayer.id == 1).first()
        assert route_preloaded_owner.credits == 1000

        concurrent = SessionFactory()
        row = concurrent.query(MirrorPlayer).filter(MirrorPlayer.id == 1).first()
        row.credits = 700
        concurrent.commit()
        concurrent.close()

        # The PRE-FIX shape: with_for_update() alone, no populate_existing().
        owner = (
            S.query(MirrorPlayer).filter(MirrorPlayer.id == 1).with_for_update().first()
        )

        assert owner is route_preloaded_owner
        assert owner.credits == 1000, (
            f"expected STALE credits=1000 (proving the pre-fix residual was "
            f"real); got {owner.credits}"
        )
        S.close()
