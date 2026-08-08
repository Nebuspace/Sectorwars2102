"""Unit coverage for central_bank_service deposit helpers
(WO-BUILD-PLAYER-CENTRAL-BANK-ACCOUNT).
"""
from __future__ import annotations

import uuid

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


def test_pay_station_loss_compensation_entry_type():
    db = FakeSession()
    player_id = uuid.uuid4()
    station_id = uuid.uuid4()

    account = bank.pay_station_loss_compensation(
        db, player_id, 99, station_id=station_id,
    )

    assert account.credits == 99
    assert account.ledger[-1]["type"] == bank.ENTRY_CASCADE_STATION_COMPENSATION
