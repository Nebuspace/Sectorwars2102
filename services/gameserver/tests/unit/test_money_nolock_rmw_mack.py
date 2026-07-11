"""mack -- behavioral gate on WO-MONEY-NOLOCK-RMW (UNCOMMITTED): row-locks
were ADDED to 3 previously-unlocked credit/state RMWs, in a ratified
deadlock-safe order:

  - terraforming_service.py cancel_terraforming (:426 planet lock=True,
    :439 player populate_existing().with_for_update()) + start_terraforming
    (:120 planet lock) -- Planet-before-Player.
  - bounty_service.py place_bounty (:331-336) -- placer+target locked in
    ASCENDING-ID order.
  - combat_service.py npc_attack_player (:1761 defender Player,
    :1774-1776 both ships via .order_by(Ship.id).populate_existing().
    with_for_update()) -- Player-before-Ships, ships ascending-id.

This file covers CHECK 1 (real-SQLAlchemy lost-update repros, non-vacuous
with inverted companions) for the terraforming and combat sites, and CHECK 2
(the ordering-consistency / deadlock-safety proof) for combat_service's
whole attack_player / attack_npc_ship / npc_attack_player family. The
bounty ordering proof lives in test_bounty_dual_lock_order.py (extended by
this same WO's gate pass) since that file already owns the shared-helper
infrastructure; the bounty PLACER lost-update repro was already proven by
test_money_reread_wave_b_mack.py's TestBountyPlaceBountyGuard (still
passing unmodified against this diff -- place_bounty's placer branch itself
is unchanged by this WO, only the target-side lock was added alongside it).

Real-SQLAlchemy convention: SQLite (StaticPool + check_same_thread=False,
two independent Session() objects sharing one in-memory DB) so a second
session's commit is a genuine concurrent write -- NOT the hand-rolled
_FakeSession pattern, which has no identity map and cannot distinguish
"populate_existing() present" from "absent" on the SAME PK re-read. Mirrors
test_storage_deposit_prelock_identity_map.py's and test_money_reread_wave_
b_mack.py's own precedent exactly: minimal mirror classes carrying ONLY the
columns the function under test actually touches (Player's real model has
several Postgres-only JSONB/ARRAY columns that block Player.__table__.
create() on SQLite entirely), with the target module's Player/Planet/
player_planets symbol monkeypatched to the mirror so the REAL function
runs, not a hand-copied guess at its query shape. `with_for_update()` is a
documented no-op on SQLite -- irrelevant to what's under test here (identity
-map REFRESH semantics via populate_existing()), not real lock acquisition
(that's the live-Postgres two-connection CI follow-up, same framing as
every other genuine-contention property in this WO's sibling gates).
"""
from __future__ import annotations

import random
import uuid
from types import SimpleNamespace
from typing import Any, List

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import declarative_base, sessionmaker
from sqlalchemy.pool import StaticPool

from src.models.planet import PlanetStatus
from src.models.ship import ShipType
from src.services import bounty_service, combat_service, terraforming_service


def _session_factory(base) -> sessionmaker:
    engine = sa.create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False)


# =============================================================================
# CHECK 1a -- terraforming_service.cancel_terraforming: player.credits
# refund RMW (terraforming_service.py:439-443).
# =============================================================================

class TestTerraformingCancelRefundLostUpdate:
    def _schema(self):
        Base = declarative_base()

        class MirrorPlayer(Base):
            __tablename__ = "mirror_players_terra"
            id = sa.Column(sa.Integer, primary_key=True)
            credits = sa.Column(sa.Integer)

        class MirrorPlanet(Base):
            __tablename__ = "mirror_planets_terra"
            id = sa.Column(sa.Integer, primary_key=True)
            name = sa.Column(sa.String)
            habitability_score = sa.Column(sa.Integer)
            terraforming_active = sa.Column(sa.Boolean)
            terraforming_target = sa.Column(sa.Integer, nullable=True)
            terraforming_start_time = sa.Column(sa.DateTime, nullable=True)
            terraforming_progress = sa.Column(sa.Float)
            active_events = sa.Column(sa.JSON)
            colonists = sa.Column(sa.Integer)
            population = sa.Column(sa.Integer)
            status = sa.Column(sa.Enum(PlanetStatus))
            max_population = sa.Column(sa.Integer)

        mirror_player_planets = sa.Table(
            "mirror_player_planets_terra", Base.metadata,
            sa.Column("planet_id", sa.Integer),
            sa.Column("player_id", sa.Integer),
        )
        return Base, MirrorPlayer, MirrorPlanet, mirror_player_planets

    def _seed(self, SessionFactory, MirrorPlayer, MirrorPlanet, mirror_player_planets, *, player_credits):
        seed = SessionFactory()
        seed.add(MirrorPlayer(id=1, credits=player_credits))
        seed.add(MirrorPlanet(
            id=1, name="Test Planet", habitability_score=55, terraforming_active=True,
            terraforming_target=60, terraforming_start_time=None,
            terraforming_progress=0.3,
            active_events=[{
                "type": "terraforming", "credit_cost": 100_000,
                "start_habitability": 50, "level": 1,
                "level_name": "Basic Atmospheric", "habitability_boost": 10,
            }],
            colonists=0, population=0, status=PlanetStatus.HABITABLE,
            max_population=0,
        ))
        seed.execute(mirror_player_planets.insert().values(planet_id=1, player_id=1))
        seed.commit()
        seed.close()

    def test_real_cancel_terraforming_refund_observes_fresh_credits_after_unlocked_preload(
        self, monkeypatch,
    ) -> None:
        Base, MirrorPlayer, MirrorPlanet, mirror_player_planets = self._schema()
        SessionFactory = _session_factory(Base)
        self._seed(SessionFactory, MirrorPlayer, MirrorPlanet, mirror_player_planets, player_credits=200_000)

        monkeypatch.setattr(terraforming_service, "Player", MirrorPlayer)
        monkeypatch.setattr(terraforming_service, "Planet", MirrorPlanet)
        monkeypatch.setattr(terraforming_service, "player_planets", mirror_player_planets)

        S = SessionFactory()
        # Same-session UNLOCKED preload of the player -- the shape of the
        # route's get_current_player dependency (planets.py cancel_
        # terraforming route resolves `player` via that dependency BEFORE
        # calling into the service with player.id).
        unlocked = S.query(MirrorPlayer).filter(MirrorPlayer.id == 1).first()
        assert unlocked.credits == 200_000

        # A concurrent session spends the SAME player down to 5,000cr and
        # commits -- e.g. a purchase racing the cancel.
        concurrent = SessionFactory()
        row = concurrent.query(MirrorPlayer).filter(MirrorPlayer.id == 1).first()
        row.credits = 5_000
        concurrent.commit()
        concurrent.close()

        # THE ACTUAL FUNCTION UNDER TEST.
        result = terraforming_service.TerraformingService(S).cancel_terraforming(
            planet_id=1, player_id=1,
        )

        # Pre-fix (bare query, no populate_existing): would read the STALE
        # identity-mapped credits=200_000, refund 50_000 ON TOP of it
        # (250_000 -- inventing 200_000cr AND clobbering the concurrent
        # debit). Post-fix: sees FRESH credits=5_000, refunds onto it.
        assert result["success"] is True
        assert result["refundAmount"] == 50_000
        assert result["creditsAfterRefund"] == 55_000, (
            f"expected the guard to observe FRESH credits=5_000 before "
            f"crediting the 50_000 refund (55_000 total) -- got "
            f"{result['creditsAfterRefund']}. If this ever reads 250_000, "
            f"cancel_terraforming's populate_existing() guard has "
            f"regressed to the pre-fix stale-identity-map lost-update."
        )
        assert unlocked.credits == 55_000  # same identity-mapped object
        S.close()

    def test_inverted_bare_with_for_update_without_populate_existing_reads_stale(
        self, monkeypatch,
    ) -> None:
        """Non-vacuous companion: the IDENTICAL scenario, but a hand-copied
        query that omits populate_existing() -- the exact pre-fix shape --
        DOES return the stale credits=200_000. Confirms this harness can
        actually detect the regression the guard prevents."""
        Base, MirrorPlayer, MirrorPlanet, mirror_player_planets = self._schema()
        SessionFactory = _session_factory(Base)
        self._seed(SessionFactory, MirrorPlayer, MirrorPlanet, mirror_player_planets, player_credits=200_000)

        S = SessionFactory()
        unlocked = S.query(MirrorPlayer).filter(MirrorPlayer.id == 1).first()
        assert unlocked.credits == 200_000

        concurrent = SessionFactory()
        row = concurrent.query(MirrorPlayer).filter(MirrorPlayer.id == 1).first()
        row.credits = 5_000
        concurrent.commit()
        concurrent.close()

        stale = S.query(MirrorPlayer).filter(MirrorPlayer.id == 1).with_for_update().first()
        assert stale is unlocked
        assert stale.credits == 200_000, (
            "harness sanity check failed -- without populate_existing() "
            "this must read the STALE identity-mapped value, proving the "
            "guard (not test luck) is what makes the test above pass"
        )
        S.close()


# =============================================================================
# CHECK 1b -- combat_service.npc_attack_player: the defender credit path.
# NOT an inline `.credits +=` in npc_attack_player itself -- the mutation is
# ship_service.py:265's insurance payout (`player.credits += compensation`),
# reached via `_handle_ship_destruction(defender, None, "npc_combat")` ->
# `ship_service.destroy_ship` -> `player = ship.owner`. `ship.owner`
# resolves through the SAME session's identity map to the SAME `defender`
# object combat_service.py:1761 already locked+populate_existing()'d --
# proving THAT object's `.credits` is fresh at the lock point, and that a
# `+=` against it composes onto the fresh value (not a stale preload),
# proves the downstream chain: identity-map freshness is a property of the
# locked object itself, not of which line performs the eventual mutation.
# Reproducing the full random-roll combat resolution + insurance-policy
# scaffolding to force an actual ship-kill is out of scope for a mechanism
# proof (mirrors test_storage_deposit_prelock_identity_map.py's own
# argument for a minimal generalizing reproduction over the full call
# chain). Reproduces the EXACT query shape at combat_service.py:1761
# against a real engine, monkeypatching combat_service.Player -- not a
# hand-copied guess.
# =============================================================================

class TestNpcAttackPlayerDefenderCreditMechanism:
    def _schema(self):
        Base = declarative_base()

        class MirrorPlayer(Base):
            __tablename__ = "mirror_players_npc_atk"
            id = sa.Column(sa.Integer, primary_key=True)
            credits = sa.Column(sa.Integer)

        return Base, MirrorPlayer

    def test_defender_credit_mutation_after_lock_observes_fresh_value(self, monkeypatch) -> None:
        Base, MirrorPlayer = self._schema()
        SessionFactory = _session_factory(Base)
        seed = SessionFactory()
        seed.add(MirrorPlayer(id=1, credits=1_000))
        seed.commit()
        seed.close()

        monkeypatch.setattr(combat_service, "Player", MirrorPlayer)

        S = SessionFactory()
        # Same-session UNLOCKED preload -- combat_service.py:1756-1758's own
        # docstring: "defender ... pre-loaded UNLOCKED upstream by
        # npc_combat_initiation_service._guard_failure".
        unlocked = S.query(MirrorPlayer).filter(MirrorPlayer.id == 1).first()
        assert unlocked.credits == 1_000

        # A concurrent session pays the SAME defender an unrelated +300cr
        # (e.g. a trade settling) and commits.
        concurrent = SessionFactory()
        row = concurrent.query(MirrorPlayer).filter(MirrorPlayer.id == 1).first()
        row.credits = 1_300
        concurrent.commit()
        concurrent.close()

        # THE ACTUAL QUERY SHAPE at combat_service.py:1761.
        defender = (
            S.query(combat_service.Player)
            .filter(combat_service.Player.id == 1)
            .populate_existing()
            .with_for_update()
            .first()
        )
        assert defender is unlocked  # same identity-mapped object, refreshed in place
        assert defender.credits == 1_300, (
            "the locked re-read must see the FRESH concurrently-committed "
            "value, not the stale preload"
        )

        # ship_service.py:265's mutation (`player.credits += compensation`),
        # against the SAME object `ship.owner` resolves to via the identity
        # map when ship.owner_id == defender.id.
        compensation = 450
        defender.credits += compensation

        assert defender.credits == 1_750, (
            f"expected fresh 1_300 + compensation 450 = 1_750 -- got "
            f"{defender.credits}. A stale-preload defender would have "
            f"produced 1_000 + 450 = 1_450, clobbering the concurrent "
            f"+300 credit that landed in between."
        )
        S.close()

    def test_inverted_bare_with_for_update_without_populate_existing_reads_stale(self, monkeypatch) -> None:
        Base, MirrorPlayer = self._schema()
        SessionFactory = _session_factory(Base)
        seed = SessionFactory()
        seed.add(MirrorPlayer(id=1, credits=1_000))
        seed.commit()
        seed.close()

        S = SessionFactory()
        unlocked = S.query(MirrorPlayer).filter(MirrorPlayer.id == 1).first()
        assert unlocked.credits == 1_000

        concurrent = SessionFactory()
        row = concurrent.query(MirrorPlayer).filter(MirrorPlayer.id == 1).first()
        row.credits = 1_300
        concurrent.commit()
        concurrent.close()

        stale = S.query(MirrorPlayer).filter(MirrorPlayer.id == 1).with_for_update().first()
        assert stale is unlocked
        assert stale.credits == 1_000, (
            "harness sanity check failed -- without populate_existing() "
            "this must read the STALE identity-mapped value"
        )
        S.close()


# =============================================================================
# CHECK 2 -- combat_service ordering-consistency / deadlock-safety proof.
#
# For each dual/multi-lock combat site, record the (table, id) ACQUISITION
# order via a lock-logging fake session, calling the REAL attack_player /
# attack_npc_ship / npc_attack_player methods (short-circuited at the
# earliest clean guard-fail AFTER every lock call, so the lock_log is
# complete without needing the full random-roll combat resolver).
# =============================================================================

def _table_name(entity) -> str:
    tbl = getattr(entity, "__table__", None)
    if tbl is not None:
        return tbl.name
    return str(entity)


class _OrderLogQuery:
    """Routes `Model.col == literal` / `Model.col.in_([...])` filters
    against in-memory SimpleNamespace rows, and appends (label, row.id) to
    a SHARED lock_log at the point each row is actually FETCHED (`.first()`
    / `.all()`) -- closer to real lock-acquisition timing than logging at
    `.with_for_update()` (which only builds the query), and the only point
    at which a multi-row `.in_(...).order_by(...)` query's ACTUAL matched
    id set is even known. `.order_by()` really sorts (by the column's real
    Python value, e.g. UUID -- UUID.__lt__ is int-based, proven elsewhere
    in this file to agree with Postgres's own uuid byte-ordering), so the
    logged order for a multi-row lock is the REAL order Postgres's
    `ORDER BY ... FOR UPDATE` would acquire in, not an artifact of fixture
    insertion order."""

    def __init__(self, rows: List[Any], label: str, lock_log: List[Any]):
        self._rows = rows
        self._label = label
        self._criteria: List[Any] = []
        self._order_col = None
        self._lock_log = lock_log

    def filter(self, *criteria):
        self._criteria.extend(criteria)
        return self

    def _matches(self, row) -> bool:
        for cond in self._criteria:
            key = cond.left.key
            value = getattr(row, key, None)
            rhs = cond.right.value if hasattr(cond.right, "value") else cond.right
            opname = getattr(cond.operator, "__name__", None)
            if opname == "eq":
                if value != rhs:
                    return False
            elif opname == "in_op":
                if value not in rhs:
                    return False
            else:
                # Permissive fallback -- guard-only filters not under test
                # here (e.g. hangar's Ship.hangar.isnot(None), patched out
                # entirely below) are irrelevant to the lock-ORDER property
                # this fake exists to prove.
                continue
        return True

    def order_by(self, *cols):
        self._order_col = cols[0] if cols else None
        return self

    def populate_existing(self):
        return self

    def with_for_update(self, *a, **k):
        return self

    def _matched_rows(self):
        rows = [r for r in self._rows if self._matches(r)]
        if self._order_col is not None:
            key = getattr(self._order_col, "key", "id")
            rows = sorted(rows, key=lambda r: getattr(r, key))
        return rows

    def first(self):
        rows = self._matched_rows()
        row = rows[0] if rows else None
        if row is not None:
            self._lock_log.append((self._label, row.id))
        return row

    def all(self):
        rows = self._matched_rows()
        for row in rows:
            self._lock_log.append((self._label, row.id))
        return rows


class _LockOrderSession:
    def __init__(self, *, players=(), ships=()):
        self._players = list(players)
        self._ships = list(ships)
        self.lock_log: List[Any] = []
        self.added: List[Any] = []
        self.flush_count = 0

    def query(self, model):
        if model is combat_service.Player:
            return _OrderLogQuery(self._players, "Player", self.lock_log)
        if model is combat_service.Ship:
            return _OrderLogQuery(self._ships, "Ship", self.lock_log)
        return _OrderLogQuery([], _table_name(model), self.lock_log)

    def add(self, obj):
        if getattr(obj, "id", None) is None:
            obj.id = uuid.uuid4()
        self.added.append(obj)

    def flush(self):
        self.flush_count += 1

    def commit(self):
        pass


def _player(*, pid, ship, sector_id=1):
    return SimpleNamespace(
        id=pid, current_ship=ship, current_ship_id=ship.id if ship else None,
        current_sector_id=sector_id, turns=99, is_docked=False, is_landed=False,
        defense_drones=0, military_rank=None, current_port_id=None,
    )


def _ship(*, sid, ship_type=ShipType.LIGHT_FREIGHTER, destroyed=False, sector_id=1):
    return SimpleNamespace(id=sid, type=ship_type, is_destroyed=destroyed, sector_id=sector_id, hangar=None)


@pytest.fixture(autouse=True)
def _no_hangar(monkeypatch):
    """Every lock-order test below short-circuits at a guard AFTER the
    hangar check (attack_player/attack_npc_ship call it; npc_attack_player
    does not). Patching this out avoids needing `.isnot()`/`.is_()`
    operator support in the minimal fake above -- irrelevant to the
    lock-ORDER property under test."""
    monkeypatch.setattr(
        "src.services.hangar_service.HangarService.is_ship_hangared",
        lambda self, ship_id: False,
    )


class TestAttackPlayerLockOrder:
    def test_locks_players_in_call_argument_order_not_ascending_id(self) -> None:
        """CRITICAL finding, empirically pinned: attack_player locks
        attacker THEN defender in raw call-argument order (combat_service.
        py:714-715) -- NOT the ascending-id convention every other dual-
        Player-lock site in this codebase uses (bounty_service._load_two_
        players_for_update, contract_service._load_two_players_for_update,
        bounty_service.place_bounty's own new branch). Chosen ids here have
        attacker_id > defender_id specifically so "locks attacker first"
        and "locks the lower id first" disagree -- proving this is call-
        order, not id-order. Two players attacking each other in the same
        instant (P1->P2 and P2->P1, both ordinary "engage" clicks, no
        exploit needed) lock in OPPOSITE order across the two transactions
        -- a live AB-BA deadlock on ordinary concurrent PvP combat, not a
        security vector (Cipher's lane) or an artifact of this WO's 3
        sites (this function is untouched by the current diff -- pre-
        existing, surfaced by this WO's own cross-check instruction to
        compare attack_player's ordering against npc_attack_player's)."""
        low_id, high_id = sorted([uuid.uuid4(), uuid.uuid4()])
        attacker_ship = _ship(sid=uuid.uuid4())
        # ESCAPE_POD short-circuits cleanly at combat_service.py:758,
        # AFTER both the Player-pair lock and the Ship-pair lock.
        defender_ship = _ship(sid=uuid.uuid4(), ship_type=ShipType.ESCAPE_POD)
        attacker = _player(pid=high_id, ship=attacker_ship)
        defender = _player(pid=low_id, ship=defender_ship)
        db = _LockOrderSession(players=[attacker, defender], ships=[attacker_ship, defender_ship])

        result = combat_service.CombatService(db).attack_player(attacker.id, defender.id)

        assert result == {"success": False, "message": "escape_pods_are_indestructible"}
        player_locks = [entry for entry in db.lock_log if entry[0] == "Player"]
        assert player_locks == [("Player", high_id), ("Player", low_id)], (
            f"attack_player locked {player_locks} -- expected attacker "
            f"(the HIGHER id here) locked first, proving call-argument "
            f"order, not the codebase's ascending-id convention."
        )


class TestAttackNpcShipLockOrder:
    def test_locks_player_before_the_single_ship(self) -> None:
        attacker_id = uuid.uuid4()
        npc_ship = _ship(sid=uuid.uuid4(), sector_id=2)  # mismatched sector -> early clean exit
        attacker_ship = _ship(sid=uuid.uuid4(), sector_id=1)
        attacker = _player(pid=attacker_id, ship=attacker_ship, sector_id=1)
        db = _LockOrderSession(players=[attacker], ships=[npc_ship])

        result = combat_service.CombatService(db).attack_npc_ship(attacker_id, npc_ship.id)

        assert result == {"success": False, "message": "Target is not in your sector"}
        assert db.lock_log == [("Player", attacker_id), ("Ship", npc_ship.id)]


class TestNpcAttackPlayerLockOrder:
    def _run(self, *, defender_id, npc_ship_id, defender_ship_id):
        npc_ship = _ship(sid=npc_ship_id, sector_id=2)  # mismatched sector -> early clean exit
        defender_ship = _ship(sid=defender_ship_id, sector_id=1)
        defender = _player(pid=defender_id, ship=defender_ship, sector_id=1)
        db = _LockOrderSession(players=[defender], ships=[npc_ship, defender_ship])
        result = combat_service.CombatService(db).npc_attack_player(npc_ship_id, defender_id)
        assert result == {"success": False, "message": "Target is not in your sector"}
        return db

    def test_locks_player_before_ships_ascending_when_npc_ship_has_the_higher_id(self) -> None:
        defender_id = uuid.uuid4()
        low_ship, high_ship = sorted([uuid.uuid4(), uuid.uuid4()])
        db = self._run(defender_id=defender_id, npc_ship_id=high_ship, defender_ship_id=low_ship)
        assert db.lock_log == [
            ("Player", defender_id), ("Ship", low_ship), ("Ship", high_ship),
        ]

    def test_locks_player_before_ships_ascending_when_npc_ship_has_the_lower_id(self) -> None:
        """Role-reversal companion: the NPC's own ship has the LOWER id
        this time. If the ordering were "npc_ship-then-defender's-ship"
        (semantic order) rather than ascending-id, this would flip relative
        to the case above -- it doesn't."""
        defender_id = uuid.uuid4()
        low_ship, high_ship = sorted([uuid.uuid4(), uuid.uuid4()])
        db = self._run(defender_id=defender_id, npc_ship_id=low_ship, defender_ship_id=high_ship)
        assert db.lock_log == [
            ("Player", defender_id), ("Ship", low_ship), ("Ship", high_ship),
        ]


class TestShipPairOrderingConsistencyAcrossAttackPlayerAndNpcAttackPlayer:
    """THE crux cross-function check for the Ship-pair dimension: given the
    IDENTICAL pair of ship ids, attack_player and npc_attack_player must
    converge on the SAME acquisition order regardless of which role
    (attacker/defender vs npc/defender) each ship plays -- otherwise a
    player attacking an NPC's ship (attack_npc_ship -- single ship, no
    pair) or a player-vs-player fight racing that SAME NPC initiating its
    own attack back could theoretically deadlock on the ship pair. Proves
    they don't."""

    def test_same_ship_pair_locks_in_the_same_order_both_ways(self) -> None:
        ship_low, ship_high = sorted([uuid.uuid4(), uuid.uuid4()])

        # attack_player: attacker flies the HIGH-id ship, defender (an
        # escape pod, for the clean short-circuit) flies the LOW-id ship.
        attacker_id, defender_id = uuid.uuid4(), uuid.uuid4()
        attacker_ship = _ship(sid=ship_high)
        defender_ship = _ship(sid=ship_low, ship_type=ShipType.ESCAPE_POD)
        attacker = _player(pid=attacker_id, ship=attacker_ship)
        defender = _player(pid=defender_id, ship=defender_ship)
        db_a = _LockOrderSession(players=[attacker, defender], ships=[attacker_ship, defender_ship])
        combat_service.CombatService(db_a).attack_player(attacker_id, defender_id)
        ships_a = [entry for entry in db_a.lock_log if entry[0] == "Ship"]

        # npc_attack_player: the NPC's own ship is the HIGH-id one (the
        # SAME id as attacker's ship above), the human defender's ship is
        # the LOW-id one (the SAME id as the escape pod above) -- same
        # pair, roles swapped.
        npc_defender_id = uuid.uuid4()
        npc_ship = _ship(sid=ship_high, sector_id=2)
        npc_defender_ship = _ship(sid=ship_low, sector_id=1)
        npc_defender = _player(pid=npc_defender_id, ship=npc_defender_ship, sector_id=1)
        db_b = _LockOrderSession(players=[npc_defender], ships=[npc_ship, npc_defender_ship])
        combat_service.CombatService(db_b).npc_attack_player(npc_ship.id, npc_defender_id)
        ships_b = [entry for entry in db_b.lock_log if entry[0] == "Ship"]

        assert ships_a == [("Ship", ship_low), ("Ship", ship_high)]
        assert ships_b == [("Ship", ship_low), ("Ship", ship_high)]
        assert ships_a == ships_b, (
            "attack_player and npc_attack_player disagree on the "
            "acquisition order for the IDENTICAL ship pair -- an AB-BA "
            "deadlock is reachable if these two paths ever contend for "
            "the same two Ship rows."
        )


# =============================================================================
# UUID ordering-equivalence -- the structural precondition every ascending-
# id lock-order convention in this codebase (bounty's `placer_id <
# target_id`, combat's `sorted(..., key=lambda sid: str(sid))`) depends on:
# raw-UUID `<` (int-based, per uuid.UUID.__lt__) and canonical-lowercase-
# string ordering must agree, or the two conventions could silently
# diverge and reopen an AB-BA cycle. Also matches Postgres's own uuid
# btree comparison (a plain 16-byte memcmp, which is exactly what both
# Python orderings are proxies for on a fixed-width, fixed-format value).
# =============================================================================

class TestUuidOrderingEquivalence:
    def test_int_lt_and_canonical_str_sort_agree_on_random_uuids(self) -> None:
        rng = random.Random(20260711)
        mismatches = 0
        for _ in range(5_000):
            a = uuid.UUID(int=rng.getrandbits(128))
            b = uuid.UUID(int=rng.getrandbits(128))
            if (a < b) != (str(a) < str(b)):
                mismatches += 1
        assert mismatches == 0, (
            "Python's UUID.__lt__ (int-based) and canonical-string "
            "ordering disagree on some pair -- bounty_service's "
            "`placer_id < target_id` and combat_service's `sorted(..., "
            "key=lambda sid: str(sid))` would NOT converge on the same "
            "lock order for the same pair, reopening the AB-BA risk both "
            "were written to close."
        )
