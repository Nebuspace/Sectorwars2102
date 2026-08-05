"""WO-FIX-MESSAGE-BEACON-RACE-REGRESSION-TEST -- regression proof for the
two races message_beacon_service.py:146-186's per-sector advisory lock
(``_lock_sector`` / ``_SECTOR_LOCK_BASE``) fixes per its own docstring:

  (a) FIFO cap breach -- concurrent deploys each see only their own insert
      under READ COMMITTED, both skip displacement, the cap silently
      breaches.
  (b) deploy/salvage/read_once/sweep last-writer-wins -- concurrent
      Sector.message_beacons denorm rebuilds clobber each other.

Real Postgres advisory-lock blocking (``pg_advisory_xact_lock``) can't be
exercised from a DB-free unit test (no live Postgres reachable from this
Mac -- see CLAUDE.md Execution Environment; conftest.py's TEST_DATABASE_URL
needs a live .env this checkout doesn't have). This test instead builds a
REAL two-thread race with REAL wall-clock concurrency (genuine OS threads,
a barrier forcing simultaneous entry into the read-decide-write window),
against an in-process fake store that faithfully models READ COMMITTED
cross-session invisibility (a session sees committed rows + only its OWN
uncommitted writes, never a concurrent session's uncommitted writes, until
that session "commits"). ``_lock_sector`` itself is monkeypatched to a real
``threading.Lock``-backed per-(region,sector) registry -- this is the exact
same blocking-mutual-exclusion contract ``pg_advisory_xact_lock`` provides,
just in-process. Each test runs BOTH variants:

  * WITH the lock (module code unmodified) -- proves the invariant holds,
    AND that the internal race-forcing barrier genuinely timed out (the
    second thread never reached the synchronization point while the first
    held the lock -- i.e. true serialization occurred, not coincidental
    non-interleaving).
  * WITHOUT the lock (``_lock_sector`` patched to a no-op, reproducing the
    pre-fix code path the module's own docstring describes) -- proves the
    invariant BREAKS, i.e. the test is falsifiable: it would have failed
    before this fix existed.
"""
from __future__ import annotations

import threading
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

import pytest

from src.models.message_beacon import MessageBeacon
from src.models.multi_account import MultiAccountFlag
from src.models.player import Player
from src.models.region import Region
from src.models.sector import Sector as SectorModel
from src.models.ship import Ship, ShipType
from src.services import message_beacon_service as svc


# --- WHERE-clause interpreter (matches test_message_beacon_deploy.py /
# test_message_beacon_lifecycle.py's own convention -- each test file owns
# its fake) --------------------------------------------------------------- #

def _match(row: Any, cond: Any) -> bool:
    col_name = cond.left.key
    row_val = getattr(row, col_name, None)
    op_name = getattr(cond.operator, "__name__", None)
    if op_name == "eq":
        return row_val == cond.right.value
    if op_name == "lt":
        return row_val is not None and row_val < cond.right.value
    if op_name == "ge":
        return row_val is not None and row_val >= cond.right.value
    if op_name == "is_not":
        return row_val is not None
    raise NotImplementedError(f"unsupported operator {cond.operator!r}")


class _FakeQuery:
    def __init__(self, rows: List[Any], criteria: Optional[List[Any]] = None) -> None:
        self._rows = rows
        self._criteria = criteria or []

    def filter(self, *conditions: Any) -> "_FakeQuery":
        return _FakeQuery(self._rows, self._criteria + list(conditions))

    def populate_existing(self) -> "_FakeQuery":
        return self

    def with_for_update(self) -> "_FakeQuery":
        return self

    def order_by(self, *args: Any) -> "_FakeQuery":
        ordered = sorted(self._matching(), key=lambda r: r.deployed_at)
        return _FakeQuery(ordered, [])

    def _matching(self) -> List[Any]:
        return [row for row in self._rows if all(_match(row, c) for c in self._criteria)]

    def first(self) -> Any:
        m = self._matching()
        return m[0] if m else None

    def all(self) -> List[Any]:
        return self._matching()


class _FakeCountQuery:
    def __init__(self, rows: List[Any], criteria: Optional[List[Any]] = None) -> None:
        self._rows = rows
        self._criteria = criteria or []

    def filter(self, *conditions: Any) -> "_FakeCountQuery":
        return _FakeCountQuery(self._rows, self._criteria + list(conditions))

    def scalar(self) -> int:
        return sum(1 for row in self._rows if all(_match(row, c) for c in self._criteria))


class _BarrieredQuery(_FakeQuery):
    """Identical to _FakeQuery except ``order_by`` -- the first call site
    hit by EITHER _apply_sector_cap or _rebuild_sector_denorm in every
    entry point this module races -- fires the session's one-shot entry
    barrier before continuing. This is the deliberate worst-case timing:
    both racers reach their read-decide step at the exact same instant,
    exactly the window the advisory lock exists to serialize."""

    def __init__(self, rows: List[Any], session: "_RacySession", criteria: Optional[List[Any]] = None) -> None:
        super().__init__(rows, criteria)
        self._session = session

    def filter(self, *conditions: Any) -> "_BarrieredQuery":
        return _BarrieredQuery(self._rows, self._session, self._criteria + list(conditions))

    def order_by(self, *args: Any) -> "_BarrieredQuery":
        session = self._session
        if session.entry_barrier is not None and not session.barrier_used:
            session.barrier_used = True
            try:
                session.entry_barrier.wait(timeout=1.0)
                session.barrier_reached = True
            except threading.BrokenBarrierError:
                # No partner arrived in time -- the lock genuinely blocked
                # the other thread before it could reach this point. This
                # IS the proof of real serialization, not a test failure.
                pass
        ordered = sorted(self._matching(), key=lambda r: r.deployed_at)
        return _BarrieredQuery(ordered, session, [])


class _BeaconStore:
    """The shared 'committed' beacon table -- models READ COMMITTED
    cross-session invisibility. A _RacySession reads committed rows PLUS
    its own uncommitted add/delete, never another session's pending
    writes, until that session calls commit()."""

    def __init__(self, *seed: MessageBeacon) -> None:
        self._guard = threading.Lock()
        self.committed: Dict[uuid.UUID, MessageBeacon] = {b.id: b for b in seed}

    def snapshot(self) -> List[MessageBeacon]:
        with self._guard:
            return list(self.committed.values())

    def commit(self, added: List[MessageBeacon], deleted_ids: set) -> None:
        with self._guard:
            for b in added:
                self.committed[b.id] = b
            for bid in deleted_ids:
                self.committed.pop(bid, None)


class _SectorLockRegistry:
    """Real threading.Lock per (region_id, sector_id) key -- the exact same
    blocking-mutual-exclusion contract pg_advisory_xact_lock provides for
    the real thing, in-process."""

    def __init__(self) -> None:
        self._guard = threading.Lock()
        self._locks: Dict[Any, threading.Lock] = {}

    def _get(self, key: Any) -> threading.Lock:
        with self._guard:
            lock = self._locks.get(key)
            if lock is None:
                lock = threading.Lock()
                self._locks[key] = lock
            return lock

    def acquire(self, key: Any) -> None:
        self._get(key).acquire()

    def release(self, key: Any) -> None:
        self._get(key).release()


class _RacySession:
    """One per 'connection'. entry_barrier is shared across the two racing
    sessions in a single test -- see _BarrieredQuery."""

    def __init__(
        self, store: _BeaconStore, *, players, ships, sectors, regions,
        entry_barrier: Optional[threading.Barrier] = None,
    ) -> None:
        self.store = store
        self.players = list(players)
        self.ships = list(ships)
        self.sectors = list(sectors)
        self.regions = list(regions)
        self.pending_add: List[MessageBeacon] = []
        self.pending_delete_ids: set = set()
        self.entry_barrier = entry_barrier
        self.barrier_used = False
        self.barrier_reached = False

    def _beacon_rows(self) -> List[MessageBeacon]:
        rows = {b.id: b for b in self.store.snapshot()}
        for b in self.pending_add:
            rows[b.id] = b
        for bid in self.pending_delete_ids:
            rows.pop(bid, None)
        return list(rows.values())

    def query(self, *entities: Any) -> Any:
        head = entities[0]
        if getattr(head, "name", None) == "count":
            return _FakeCountQuery(self._beacon_rows())
        if head is Player:
            return _FakeQuery(self.players)
        if head is Ship:
            return _FakeQuery(self.ships)
        if head is SectorModel:
            return _FakeQuery(self.sectors)
        if head is Region:
            return _FakeQuery(self.regions)
        if head is MessageBeacon:
            return _BarrieredQuery(self._beacon_rows(), self)
        if head is MultiAccountFlag:
            return _FakeQuery([])
        raise AssertionError(f"unexpected query for {entities!r}")

    def add(self, obj: Any) -> None:
        if isinstance(obj, MessageBeacon):
            self.pending_add.append(obj)

    def delete(self, obj: Any) -> None:
        self.pending_delete_ids.add(obj.id)
        self.pending_add = [b for b in self.pending_add if b.id != obj.id]

    def flush(self) -> None:
        pass

    def execute(self, statement: Any, params: Optional[dict] = None) -> Any:
        # sweep_expired()'s raw conditional DELETE -- re-verify expiry <
        # now against the current view (committed + own pending) exactly
        # like the real WHERE clause would, so the sweep's own
        # revived-by-a-concurrent-recharge skip path stays exercisable.
        text = str(statement)
        if "DELETE FROM message_beacons" in text:
            params = params or {}
            bid, now = params.get("id"), params.get("now")
            row = next((b for b in self._beacon_rows() if b.id == bid), None)
            if row is not None and row.expiry is not None and row.expiry < now:
                self.pending_delete_ids.add(bid)
                self.pending_add = [b for b in self.pending_add if b.id != bid]
                return SimpleNamespace(rowcount=1)
            return SimpleNamespace(rowcount=0)
        return SimpleNamespace(scalar=lambda: True, rowcount=0)

    def begin_nested(self):
        @contextmanager
        def _cm():
            yield
        return _cm()

    def commit_to_store(self) -> None:
        self.store.commit(self.pending_add, self.pending_delete_ids)


class _FakeSecurityService:
    def validate_input(self, text, player_id, session_id, skip_sql_injection=False, skip_xss=False, seed_from=None):
        return True, []

    def sanitize_input(self, text: str) -> str:
        return text.strip()


@pytest.fixture(autouse=True)
def _safe_security(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(svc, "get_security_service", lambda: _FakeSecurityService())


def _make_player(**overrides: Any) -> SimpleNamespace:
    player_id = overrides.pop("id", None) or uuid.uuid4()
    ship_id = overrides.pop("current_ship_id", None) or uuid.uuid4()
    ship = overrides.pop("current_ship", None)
    if ship is None:
        ship = Ship(
            id=ship_id, name="Test Freighter", type=ShipType.LIGHT_FREIGHTER,
            sector_id=42, is_destroyed=False, owner_id=player_id,
            cargo={"capacity": 100, "used": 10, "contents": {"equipment": 5}},
        )
    base = dict(
        id=player_id, username="Racer", nickname=None, credits=10000, turns=1000,
        personal_reputation=0, is_docked=False, current_sector_id=42,
        current_ship_id=ship_id, current_ship=ship, settings={},
        max_turns=1000, last_turn_regeneration=datetime.now(UTC), lifetime_turns_spent=0,
        created_at=datetime.now(UTC) - timedelta(days=30),
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _make_region(**overrides: Any) -> SimpleNamespace:
    base = dict(id=uuid.uuid4(), trade_bonuses={})
    base.update(overrides)
    return SimpleNamespace(**base)


def _make_sector(region: SimpleNamespace, **overrides: Any) -> SectorModel:
    base = dict(
        id=uuid.uuid4(), sector_id=42, region_id=region.id, is_nexus_protected=False,
        message_beacons=None, name="Test Sector", x_coord=0, y_coord=0,
    )
    base.update(overrides)
    return SectorModel(**base)


def _make_beacon(region: SimpleNamespace, sector: SectorModel, **overrides: Any) -> MessageBeacon:
    now = datetime.now(UTC)
    base = dict(
        id=uuid.uuid4(), region_id=region.id, sector_id=sector.sector_id,
        deployer_player_id=uuid.uuid4(), deployer_nickname_at_deploy="Existing",
        message="pre-existing beacon", expiry=now + timedelta(days=30), read_once=False,
        read_count=0, deployed_at=now - timedelta(days=1), last_read_at=None,
        charge_expires_at=now + timedelta(days=29), last_charged_at=now - timedelta(days=1),
    )
    base.update(overrides)
    return MessageBeacon(**base)


def _run_pair(target_a, target_b) -> None:
    t_a = threading.Thread(target=target_a)
    t_b = threading.Thread(target=target_b)
    t_a.start()
    t_b.start()
    t_a.join(timeout=5)
    t_b.join(timeout=5)
    assert not t_a.is_alive(), "racer A deadlocked / did not finish"
    assert not t_b.is_alive(), "racer B deadlocked / did not finish"


# --------------------------------------------------------------------------- #
# (a) FIFO cap breach: two concurrent deploys into a cap=1 sector.
# --------------------------------------------------------------------------- #

class TestSectorCapRace:
    def _run(self, *, use_lock: bool) -> _BeaconStore:
        region = _make_region(trade_bonuses={svc.REGION_BEACON_CAP_KEY: 1})
        sector = _make_sector(region)
        store = _BeaconStore()
        registry = _SectorLockRegistry()
        barrier = threading.Barrier(2)

        def _fake_lock_sector(db, region_id, sector_id):
            if use_lock:
                registry.acquire((region_id, sector_id))

        errors: List[Optional[Exception]] = [None, None]
        sessions: List[Optional[_RacySession]] = [None, None]

        def _worker(idx: int, message: str) -> None:
            player = _make_player()
            session = _RacySession(
                store, players=[player], ships=[player.current_ship],
                sectors=[sector], regions=[region], entry_barrier=barrier,
            )
            sessions[idx] = session
            try:
                svc.deploy(session, player.id, sector.sector_id, message)
                session.commit_to_store()
            except Exception as e:  # pragma: no cover -- surfaced via assert below
                errors[idx] = e
            finally:
                if use_lock:
                    registry.release((region.id, sector.sector_id))

        import pytest as _pytest
        mp = _pytest.MonkeyPatch()
        mp.setattr(svc, "_lock_sector", _fake_lock_sector)
        try:
            _run_pair(
                lambda: _worker(0, "Racer A was here."),
                lambda: _worker(1, "Racer B was here."),
            )
        finally:
            mp.undo()

        assert errors == [None, None], f"unexpected exception(s): {errors}"
        if use_lock:
            # Proves genuine serialization: the second racer's session
            # never reached the barrier's synchronization point while the
            # first held the lock (BrokenBarrierError/timeout swallowed,
            # barrier_reached stays False on whichever one was blocked).
            assert not (sessions[0].barrier_reached and sessions[1].barrier_reached), (
                "both racers reached the race window simultaneously -- the "
                "lock did not actually serialize them"
            )
        else:
            # Proves the race window was genuinely forced open: both
            # racers reached their read-decide step at the same instant.
            assert sessions[0].barrier_reached and sessions[1].barrier_reached
        return store

    def test_cap_holds_with_the_advisory_lock(self) -> None:
        store = self._run(use_lock=True)
        assert len(store.committed) == 1, (
            f"sector cap=1 breached: {len(store.committed)} beacons committed"
        )

    def test_cap_breaches_without_the_advisory_lock(self) -> None:
        """Falsifiability check: reproduces the pre-fix code path
        (_lock_sector never called) and proves the described race is
        REAL -- both concurrent deploys see only their own insert, both
        skip FIFO displacement, the cap=1 sector ends up holding 2."""
        store = self._run(use_lock=False)
        assert len(store.committed) == 2, (
            "expected the race to breach the cap (this is the FALSIFIABILITY "
            "check proving the test isn't vacuous) but got "
            f"{len(store.committed)} -- the race didn't manifest as expected"
        )


# --------------------------------------------------------------------------- #
# (b) last-writer-wins: deploy() racing salvage() on the SAME sector.
# --------------------------------------------------------------------------- #

class TestDenormLastWriterWinsRace:
    def _run(self, *, use_lock: bool):
        region = _make_region()
        sector = _make_sector(region)
        existing_owner = _make_player()
        existing = _make_beacon(region, sector, deployer_player_id=existing_owner.id)
        store = _BeaconStore(existing)
        registry = _SectorLockRegistry()
        barrier = threading.Barrier(2)

        def _fake_lock_sector(db, region_id, sector_id):
            if use_lock:
                registry.acquire((region_id, sector_id))

        errors: List[Optional[Exception]] = [None, None]
        sessions: List[Optional[_RacySession]] = [None, None]
        deployed_beacon_id: List[Optional[uuid.UUID]] = [None]

        def _salvager() -> None:
            player = _make_player(current_sector_id=sector.sector_id)
            session = _RacySession(
                store, players=[player], ships=[player.current_ship],
                sectors=[sector], regions=[region], entry_barrier=barrier,
            )
            sessions[0] = session
            try:
                svc.salvage(session, existing.id, player.id)
                session.commit_to_store()
            except Exception as e:  # pragma: no cover
                errors[0] = e
            finally:
                if use_lock:
                    registry.release((region.id, sector.sector_id))

        def _deployer() -> None:
            player = _make_player(current_sector_id=sector.sector_id)
            session = _RacySession(
                store, players=[player], ships=[player.current_ship],
                sectors=[sector], regions=[region], entry_barrier=barrier,
            )
            sessions[1] = session
            try:
                result = svc.deploy(session, player.id, sector.sector_id, "New beacon.")
                deployed_beacon_id[0] = uuid.UUID(result["id"])
                session.commit_to_store()
            except Exception as e:  # pragma: no cover
                errors[1] = e
            finally:
                if use_lock:
                    registry.release((region.id, sector.sector_id))

        import pytest as _pytest
        mp = _pytest.MonkeyPatch()
        mp.setattr(svc, "_lock_sector", _fake_lock_sector)
        try:
            _run_pair(_salvager, _deployer)
        finally:
            mp.undo()

        assert errors == [None, None], f"unexpected exception(s): {errors}"
        if use_lock:
            assert not (sessions[0].barrier_reached and sessions[1].barrier_reached), (
                "both racers reached the race window simultaneously -- the "
                "lock did not actually serialize them"
            )
        else:
            assert sessions[0].barrier_reached and sessions[1].barrier_reached
        return store, sector, deployed_beacon_id[0]

    def test_denorm_reflects_both_changes_with_the_advisory_lock(self) -> None:
        store, sector, new_id = self._run(use_lock=True)
        # Ground truth: existing beacon salvaged (gone), new beacon present.
        assert set(store.committed.keys()) == {new_id}
        # The JSONB denorm must match that ground truth exactly -- neither
        # write's rebuild is allowed to clobber the other's.
        denorm_ids = {b["id"] for b in sector.message_beacons}
        assert denorm_ids == {str(new_id)}, (
            f"denorm diverged from committed rows: denorm={denorm_ids}, "
            f"committed={set(str(k) for k in store.committed)}"
        )

    def test_denorm_can_diverge_without_the_advisory_lock(self) -> None:
        """Falsifiability check: reproduces the pre-fix code path and
        proves the last-writer-wins race is REAL -- whichever racer's
        denorm rebuild commits last overwrites Sector.message_beacons with
        a snapshot that doesn't reflect the OTHER racer's change, even
        though the underlying rows end up correct."""
        store, sector, new_id = self._run(use_lock=False)
        # Ground truth is unaffected by the race (row-level ops are still
        # individually atomic in this fake) -- only the denorm view drifts.
        assert set(store.committed.keys()) == {new_id}
        denorm_ids = {b["id"] for b in sector.message_beacons}
        assert denorm_ids != {str(new_id)}, (
            "expected the denorm to diverge from the committed rows (this is "
            "the FALSIFIABILITY check proving the test isn't vacuous) but the "
            f"race didn't manifest: denorm={denorm_ids}"
        )
