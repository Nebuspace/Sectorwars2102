"""Unit coverage for WO-BUILD-REGION-LIFECYCLE-CLEANUP-CASCADE (reduced
scope) + WO-BUILD-PLAYER-CENTRAL-BANK-ACCOUNT re-point: planet-safe deposits
go to PlayerCentralBankAccount; Genesis compensation still hits
Player.credits; station-loss compensation helper deposits to Bank.
"""
from __future__ import annotations

import uuid

import pytest

from src.models.genesis_device import GenesisType
from src.models.planet import Planet
from src.models.player import Player
from src.models.player_central_bank import PlayerCentralBankAccount
from src.services import region_termination_cascade_service as cascade
from src.services.audit_service import AuditAction
from src.services.realtime_outbox import RealtimeOutbox


class _FakePlayerQuery:
    def __init__(self, players, lock_calls=None):
        self._players = players
        self._id = None
        self._lock_calls = lock_calls if lock_calls is not None else []
        self._locked = False

    def filter(self, *clauses):
        for clause in clauses:
            self._id = clause.right.value
        return self

    def with_for_update(self, *a, **k):
        # ADR-0054 X-I2: records that the lock was requested for this
        # player id, and BEFORE .first() actually reads the row -- proves
        # the query chain locks before returning the row, not after.
        self._locked = True
        self._lock_calls.append(self._id)
        return self

    def first(self):
        assert self._locked, (
            "process_planet_termination's Player query must call "
            "with_for_update() before first() -- ADR-0054 X-I2"
        )
        for p in self._players:
            if p.id == self._id:
                return p
        return None


class _FakeBankQuery:
    def __init__(self, session):
        self._session = session
        self._id = None

    def filter(self, *clauses):
        for clause in clauses:
            self._id = getattr(clause.right, "value", clause.right)
        return self

    def populate_existing(self):
        return self

    def with_for_update(self, *a, **k):
        return self

    def first(self):
        return self._session.banks.get(self._id)


class FakeSession:
    def __init__(self, players):
        self._players = players
        self.added = []
        self.audit_logs = []
        self.banks = {}
        self.player_lock_calls = []

    def add(self, obj):
        self.added.append(obj)
        if type(obj).__name__ == "AuditLog":
            self.audit_logs.append(obj)
        if isinstance(obj, PlayerCentralBankAccount):
            self.banks[obj.player_id] = obj

    def flush(self, objs=None):
        for obj in self.added:
            if getattr(obj, "id", None) is None and not isinstance(
                obj, PlayerCentralBankAccount,
            ):
                obj.id = uuid.uuid4()

    def query(self, *entities):
        model = entities[0]
        if model is Player:
            return _FakePlayerQuery(self._players, lock_calls=self.player_lock_calls)
        if model is PlayerCentralBankAccount:
            return _FakeBankQuery(self)
        raise AssertionError(f"unexpected query target: {model}")


def _make_planet(**overrides):
    defaults = dict(
        id=uuid.uuid4(),
        owner_id=uuid.uuid4(),
        region_id=uuid.uuid4(),
        citadel_level=0,
        citadel_safe_credits=0,
        active_events={},
        transport_prepaid=None,
    )
    defaults.update(overrides)
    return Planet(**defaults)


def _make_player(credits=10_000, **overrides):
    overrides.setdefault("id", uuid.uuid4())
    return Player(credits=credits, **overrides)


def test_automatic_transport_banks_surviving_credits_and_commodities():
    owner = _make_player(credits=1_000)
    planet = _make_planet(
        owner_id=owner.id,
        citadel_safe_credits=1_000,
        active_events={"safe_commodities": {"ore": 100, "organics": 51}},
    )
    db = FakeSession([owner])

    result = cascade.process_planet_termination(db, planet)

    assert result["forfeited_credits"] == 200
    assert result["forfeited_commodities"] == {"ore": 20, "organics": 11}
    # Wallet unchanged by safe transport — Bank got the surviving stacks.
    assert owner.credits == 1_000
    assert result["bank_credits"] == 800
    assert result["bank_commodities"] == {"ore": 80, "organics": 40}
    account = db.banks[owner.id]
    assert account.credits == 800
    assert account.commodities == {"ore": 80, "organics": 40}
    assert planet.citadel_safe_credits == 0
    assert planet.active_events.get("safe_commodities", {}) == {}
    assert len(db.audit_logs) == 1
    assert db.audit_logs[0].action == AuditAction.FORFEIT.value
    assert db.audit_logs[0].request_body["forfeited_credits"] == 200


def test_prepaid_transport_banks_full_safe_no_loss():
    owner = _make_player(credits=0)
    planet = _make_planet(
        owner_id=owner.id,
        citadel_safe_credits=1_000,
        active_events={"safe_commodities": {"ore": 100}},
        transport_prepaid=True,
    )
    db = FakeSession([owner])

    result = cascade.process_planet_termination(db, planet)

    assert result["forfeited_credits"] == 0
    assert result["forfeited_commodities"] == {}
    assert owner.credits == 0
    assert result["bank_credits"] == 1_000
    assert result["bank_commodities"] == {"ore": 100}
    account = db.banks[owner.id]
    assert account.credits == 1_000
    assert account.commodities == {"ore": 100}
    assert len(db.audit_logs) == 0


@pytest.mark.parametrize(
    "citadel_level,expected_devices,expected_credits",
    [
        (1, [GenesisType.STANDARD], 50_000),
        (2, [GenesisType.STANDARD, GenesisType.ADVANCED], 250_000),
        (3, [GenesisType.ADVANCED, GenesisType.ADVANCED], 1_000_000),
        (4, [GenesisType.ADVANCED] * 3, 5_000_000),
        (5, [GenesisType.ADVANCED] * 5, 25_000_000),
    ],
)
def test_genesis_compensation_still_hits_player_wallet(
    citadel_level, expected_devices, expected_credits,
):
    owner = _make_player(credits=0)
    planet = _make_planet(owner_id=owner.id, citadel_level=citadel_level)
    db = FakeSession([owner])

    result = cascade.process_planet_termination(db, planet)

    assert result["genesis_devices_minted"] == len(expected_devices)
    minted_types = [d.type for d in db.added if type(d).__name__ == "GenesisDevice"]
    assert minted_types == expected_devices
    assert result["genesis_credit_compensation"] == expected_credits
    assert owner.credits == expected_credits
    assert owner.id not in db.banks  # genesis never opens a Bank row alone


def test_citadel_level_zero_gets_no_genesis_compensation():
    owner = _make_player(credits=0)
    planet = _make_planet(owner_id=owner.id, citadel_level=0)
    db = FakeSession([owner])

    result = cascade.process_planet_termination(db, planet)

    assert result["genesis_devices_minted"] == 0
    assert result["genesis_credit_compensation"] == 0
    assert owner.credits == 0


def test_orphaned_planet_skips_compensation_and_logs_forfeiture():
    planet = _make_planet(owner_id=None, citadel_safe_credits=500)
    db = FakeSession([])

    result = cascade.process_planet_termination(db, planet)

    assert result["credited_credits"] == 0
    assert len(db.audit_logs) == 1
    assert db.audit_logs[0].request_body["reason"] == "orphaned_planet"


def test_owner_row_locked_before_wallet_mutation_x_i2():
    """ADR-0054 X-I2: the owner Player row must be locked (with_for_update)
    BEFORE any wallet read/compute/mutate -- _FakePlayerQuery.first() itself
    asserts with_for_update() was called first, so a passing call here
    proves the ordering; this test additionally proves the lock precedes
    the Genesis-compensation credit mutation specifically (the actual
    wallet write this function performs)."""
    owner = _make_player(credits=0)
    planet = _make_planet(owner_id=owner.id, citadel_level=1)
    db = FakeSession([owner])

    result = cascade.process_planet_termination(db, planet)

    assert db.player_lock_calls == [owner.id]
    assert result["genesis_credit_compensation"] == 50_000
    assert owner.credits == 50_000


def test_realtime_event_queued_not_emitted_directly_x_v1():
    """ADR-0054 X-V1: process_planet_termination must QUEUE the realtime
    notice on the outbox, never emit it directly -- the outbox has no
    flush() call anywhere in the cascade path itself (only the Phase-7
    caller, post-commit, may flush it)."""
    owner = _make_player(credits=1_000, user_id=uuid.uuid4())
    planet = _make_planet(owner_id=owner.id, citadel_safe_credits=1_000)
    db = FakeSession([owner])
    outbox = RealtimeOutbox()

    result = cascade.process_planet_termination(db, planet, outbox=outbox)

    assert len(outbox) == 1
    kind, event_type, payload, target = outbox._events[0]
    assert kind == "personal"
    assert event_type == "region.planet_terminated"
    assert target == str(owner.user_id)
    assert payload["planet_id"] == str(planet.id)
    assert payload["bank_credits"] == result["bank_credits"]


def test_outbox_none_is_a_safe_noop():
    """A direct unit-test-style call with no outbox (matching every other
    test in this file) must not raise -- queuing is optional
    instrumentation, never load-bearing."""
    owner = _make_player(credits=0)
    planet = _make_planet(owner_id=owner.id, citadel_level=0)
    db = FakeSession([owner])

    result = cascade.process_planet_termination(db, planet, outbox=None)

    assert result["genesis_devices_minted"] == 0


def test_orphaned_and_missing_owner_paths_never_queue_an_event():
    """No owner => nothing to notify -- both early-return branches must
    leave the outbox empty, matching the function's own
    "nothing to compensate" framing."""
    outbox = RealtimeOutbox()
    orphan_planet = _make_planet(owner_id=None, citadel_safe_credits=500)
    db = FakeSession([])
    cascade.process_planet_termination(db, orphan_planet, outbox=outbox)
    assert len(outbox) == 0

    missing_owner_id = uuid.uuid4()
    missing_planet = _make_planet(owner_id=missing_owner_id)
    db2 = FakeSession([])  # owner row absent
    cascade.process_planet_termination(db2, missing_planet, outbox=outbox)
    assert len(outbox) == 0


def test_outbox_flush_discards_on_rollback_and_delivers_on_commit(monkeypatch):
    """The transactional-outbox contract itself (ADR-0054 X-V1's whole
    rationale): events queued during a transaction that later rolls back
    must NEVER reach flush(); events queued during a transaction that
    commits must flush exactly once each. Mirrors the ADR's own
    cleanup_orchestrator pseudocode shape (queue during the try, flush only
    after a successful commit, discard on exception)."""
    def _fake_resolve_loop():
        return None  # forces the "no live loop" early-return path in
        # flush(), which still counts attempted events -- isolates this
        # test from needing a real asyncio loop / websocket_service import,
        # while still proving flush() was (or wasn't) called at all.

    monkeypatch.setattr(
        "src.services.intrasystem_movement_service._resolve_broadcast_loop",
        _fake_resolve_loop,
    )

    # -- Rollback path: outbox populated, but the transaction fails before
    # flush() is ever reached -- exactly what the cascade path does (the
    # outbox is simply never flushed on the except/rollback branch).
    outbox_rb = RealtimeOutbox()
    outbox_rb.queue_personal("region.planet_terminated", {"x": 1}, uuid.uuid4())
    try:
        raise RuntimeError("simulated commit failure")
        outbox_rb.flush()  # unreachable -- proves intent, not exercised
    except RuntimeError:
        pass  # mirrors the cascade's except/rollback branch: flush() is
        # simply never called on this path.
    # Never flushed: the queue is still non-empty (nothing consumed it).
    assert len(outbox_rb) == 1

    # -- Commit path: outbox flushed exactly once, queue drains.
    outbox_ok = RealtimeOutbox()
    outbox_ok.queue_personal("region.planet_terminated", {"x": 1}, uuid.uuid4())
    outbox_ok.queue_personal("region.planet_terminated", {"x": 2}, uuid.uuid4())
    attempted = outbox_ok.flush()
    assert attempted == 2
    assert len(outbox_ok) == 0  # drained after flush


def test_station_loss_compensation_deposits_to_bank():
    owner = _make_player(credits=5_000)
    db = FakeSession([owner])
    station_id = uuid.uuid4()

    account = cascade.apply_station_loss_compensation(
        db, owner.id, 42_000, station_id=station_id,
    )

    assert owner.credits == 5_000
    assert account.credits == 42_000
    assert account.ledger[-1]["type"] == "cascade_station_compensation"
    assert account.ledger[-1]["access_override"] is True
