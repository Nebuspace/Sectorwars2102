"""Unit coverage for central_bank_service deposit helpers
(WO-BUILD-PLAYER-CENTRAL-BANK-ACCOUNT) + deepen: zero-amount paths,
online probe, credit_wallet_or_bank routing.
"""
from __future__ import annotations

import types
import uuid
from unittest.mock import MagicMock

import pytest

from src.models.player_central_bank import PlayerCentralBankAccount
from src.services import central_bank_service as bank


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
    def __init__(self):
        self.added = []
        self.banks = {}

    def add(self, obj):
        self.added.append(obj)
        if isinstance(obj, PlayerCentralBankAccount):
            self.banks[obj.player_id] = obj

    def flush(self, objs=None):
        pass

    def query(self, *entities):
        assert entities[0] is PlayerCentralBankAccount
        return _FakeBankQuery(self)


def _player(credits: int = 0):
    return types.SimpleNamespace(id=uuid.uuid4(), credits=credits)


def test_deposit_credits_creates_account_and_ledger():
    db = FakeSession()
    player_id = uuid.uuid4()

    account = bank.deposit_credits(
        db,
        player_id,
        1_500,
        entry_type=bank.ENTRY_CASCADE_SAFE_TRANSFER,
        source="planet:test",
        notes="unit",
    )

    assert account.credits == 1_500
    assert account.ledger[-1]["type"] == bank.ENTRY_CASCADE_SAFE_TRANSFER
    assert account.ledger[-1]["access_override"] is True
    assert account.ledger[-1]["amount"] == 1_500


def test_deposit_credits_zero_amount_no_ledger():
    db = FakeSession()
    player_id = uuid.uuid4()

    account = bank.deposit_credits(
        db, player_id, 0,
        entry_type=bank.ENTRY_CASCADE_SAFE_TRANSFER, source="t",
    )

    assert account.credits == 0
    assert account.ledger == []


def test_deposit_commodities_merges_stacks():
    db = FakeSession()
    player_id = uuid.uuid4()

    bank.deposit_commodities(
        db, player_id, {"ore": 10},
        entry_type=bank.ENTRY_CASCADE_SAFE_TRANSFER, source="t",
    )
    account = bank.deposit_commodities(
        db, player_id, {"ore": 5, "organics": 3},
        entry_type=bank.ENTRY_CASCADE_SAFE_TRANSFER, source="t",
    )

    assert account.commodities == {"ore": 15, "organics": 3}


def test_deposit_commodities_empty_or_nonpositive_no_ledger():
    db = FakeSession()
    player_id = uuid.uuid4()

    account = bank.deposit_commodities(
        db, player_id, {"ore": 0, "fuel": -2},
        entry_type=bank.ENTRY_CASCADE_SAFE_TRANSFER, source="t",
    )

    assert account.commodities == {}
    assert account.ledger == []


def test_pay_station_loss_compensation_entry_type():
    db = FakeSession()
    player_id = uuid.uuid4()
    station_id = uuid.uuid4()

    account = bank.pay_station_loss_compensation(
        db, player_id, 99, station_id=station_id,
    )

    assert account.credits == 99
    assert account.ledger[-1]["type"] == bank.ENTRY_CASCADE_STATION_COMPENSATION
    assert account.ledger[-1]["source"] == f"station:{station_id}"


def test_pay_station_loss_compensation_without_station_id():
    db = FakeSession()
    player_id = uuid.uuid4()

    account = bank.pay_station_loss_compensation(db, player_id, 50)

    assert account.credits == 50
    assert account.ledger[-1]["source"] == "station_loss"


def test_credit_wallet_or_bank_zero_amount_returns_wallet():
    db = FakeSession()
    player = _player(credits=10)

    where = bank.credit_wallet_or_bank(
        db, player, 0,
        entry_type=bank.ENTRY_CASCADE_SAFE_TRANSFER, source="t",
    )

    assert where == "wallet"
    assert player.credits == 10
    assert db.banks == {}


def test_credit_wallet_or_bank_online_credits_wallet(monkeypatch):
    db = FakeSession()
    player = _player(credits=100)
    monkeypatch.setattr(bank, "is_player_online_sync", lambda _pid: True)

    where = bank.credit_wallet_or_bank(
        db, player, 25,
        entry_type=bank.ENTRY_CASCADE_SAFE_TRANSFER, source="t",
    )

    assert where == "wallet"
    assert player.credits == 125
    assert db.banks == {}


@pytest.mark.parametrize("online", [False, None])
def test_credit_wallet_or_bank_offline_or_unknown_goes_to_bank(monkeypatch, online):
    db = FakeSession()
    player = _player(credits=100)
    monkeypatch.setattr(bank, "is_player_online_sync", lambda _pid: online)

    where = bank.credit_wallet_or_bank(
        db, player, 40,
        entry_type=bank.ENTRY_WARP_GATE_CASCADE_REFUND, source="gate",
    )

    assert where == "bank"
    assert player.credits == 100
    assert db.banks[player.id].credits == 40
    assert db.banks[player.id].ledger[-1]["type"] == bank.ENTRY_WARP_GATE_CASCADE_REFUND


def test_is_player_online_sync_no_client(monkeypatch):
    monkeypatch.setattr(
        "src.services.redis_service.redis_service",
        types.SimpleNamespace(sync_redis=None),
        raising=False,
    )
    # Force re-import path: patch where the function looks it up
    fake_mod = types.ModuleType("src.services.redis_service")
    fake_mod.redis_service = types.SimpleNamespace(sync_redis=None)
    monkeypatch.setitem(
        __import__("sys").modules, "src.services.redis_service", fake_mod,
    )

    assert bank.is_player_online_sync(uuid.uuid4()) is None


def test_is_player_online_sync_true_false_and_exception(monkeypatch):
    client = MagicMock()
    fake_mod = types.ModuleType("src.services.redis_service")
    fake_mod.redis_service = types.SimpleNamespace(sync_redis=client)
    monkeypatch.setitem(
        __import__("sys").modules, "src.services.redis_service", fake_mod,
    )

    pid = uuid.uuid4()
    client.get.return_value = b"1"
    assert bank.is_player_online_sync(pid) is True

    client.get.return_value = None
    assert bank.is_player_online_sync(pid) is False

    client.get.side_effect = RuntimeError("redis down")
    assert bank.is_player_online_sync(pid) is None
