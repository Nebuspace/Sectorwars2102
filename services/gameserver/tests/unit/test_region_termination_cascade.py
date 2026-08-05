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


class _FakePlayerQuery:
    def __init__(self, players):
        self._players = players
        self._id = None

    def filter(self, *clauses):
        for clause in clauses:
            self._id = clause.right.value
        return self

    def first(self):
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
            return _FakePlayerQuery(self._players)
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
