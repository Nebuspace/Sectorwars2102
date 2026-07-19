"""WO-GWQ-STRANDING-2 lane C -- fuel_delivery_service.deliver_fuel: the paid
counterpart to the free Escape Pod. KERNEL only (no request/board/escrow --
see the module's own docstring for the verified WO-ECON-CONTRACT-1-KERNEL
dependency note): a same-sector, immediate, mutually-consenting fuel-for-
credits handoff between two players' ships.

DB-free: hand-built fakes, mirrors test_stranding_recovery.py's / test_
escape_pod_service.py's established conventions. `_FakeQuery` uses a `seq`
list for the Player dispatch since deliver_fuel queries Player TWICE
(deliverer, then recipient) with DIFFERENT expected rows in a fixed order.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any, List, Optional

import pytest

from src.models.player import Player
from src.models.ship import Ship, ShipStatus, ShipType
from src.services import fuel_delivery_service
from src.services.fuel_delivery_service import FuelDeliveryError


class _FakeQuery:
    def __init__(self, *, seq: Optional[List[Any]] = None):
        self._seq = list(seq) if seq is not None else []

    def filter(self, *a, **k) -> "_FakeQuery":
        return self

    def populate_existing(self) -> "_FakeQuery":
        return self

    def with_for_update(self, *a, **k) -> "_FakeQuery":
        return self

    def first(self) -> Any:
        return self._seq.pop(0) if self._seq else None


class _FakeSession:
    """Player is queried TWICE per call (deliverer, then recipient) --
    `player_seq` supplies both rows in that fixed order. The cursor lives on
    the SESSION (popped here, once, per query() call) -- a fresh _FakeQuery
    re-copying the pristine source list on every call would hand back the
    SAME (first) row to both queries instead of advancing."""

    def __init__(self, *, player_seq: List[Any]):
        self._player_seq = list(player_seq)
        self.flush_calls = 0

    def query(self, *entities: Any) -> _FakeQuery:
        assert entities and entities[0] is Player, f"unexpected query for {entities!r}"
        row = self._player_seq.pop(0) if self._player_seq else None
        return _FakeQuery(seq=[row] if row is not None else [])

    def flush(self) -> None:
        self.flush_calls += 1

    def commit(self) -> None:
        raise AssertionError("service functions are flush-only -- the route commits")


def _fake_ship(**overrides: Any) -> SimpleNamespace:
    """SimpleNamespace stand-in -- fine for the REJECTION-path tests (this
    module never calls flag_modified before a validation failure)."""
    base = dict(
        id=uuid.uuid4(),
        type=ShipType.LIGHT_FREIGHTER,
        status=ShipStatus.IN_SPACE,
        is_destroyed=False,
        sector_id=1,
        cargo={"capacity": 100, "used": 0, "contents": {}},
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _real_ship(**overrides: Any) -> Ship:
    """A REAL ORM instance -- deliver_fuel calls flag_modified(ship,
    "cargo") on BOTH ships once a delivery actually succeeds, which
    requires `_sa_instance_state` (mirrors test_stranding_recovery.py's own
    `_real_ship` precedent). Used only by the SUCCESSFUL-delivery tests."""
    base = dict(
        id=uuid.uuid4(),
        type=ShipType.LIGHT_FREIGHTER,
        status=ShipStatus.IN_SPACE,
        is_destroyed=False,
        sector_id=1,
        cargo={"capacity": 100, "used": 0, "contents": {}},
    )
    base.update(overrides)
    return Ship(**base)


def _fake_player(**overrides: Any) -> SimpleNamespace:
    base = dict(
        id=uuid.uuid4(), current_sector_id=1, current_ship=None, credits=1000,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


@pytest.mark.unit
class TestDeliverFuel:
    def test_successful_delivery_moves_fuel_and_payment_atomically(self) -> None:
        deliverer_ship = _real_ship(cargo={"capacity": 1000, "used": 500, "contents": {"fuel": 500}})
        recipient_ship = _real_ship(cargo={"capacity": 100, "used": 0, "contents": {}})
        deliverer = _fake_player(current_ship=deliverer_ship, credits=100, current_sector_id=7)
        recipient = _fake_player(current_ship=recipient_ship, credits=1000, current_sector_id=7)
        db = _FakeSession(player_seq=[deliverer, recipient])

        result = fuel_delivery_service.deliver_fuel(
            db, deliverer.id, recipient.id, fuel_amount=60, payment_credits=300,
        )

        assert result["outcome"] == "fuel_delivered"
        assert result["fuel_delivered"] == 60
        assert result["payment_credits"] == 300
        assert deliverer_ship.cargo["contents"]["fuel"] == 500 - 60
        assert recipient_ship.cargo["contents"]["fuel"] == 60
        assert recipient_ship.cargo["used"] == 60
        assert deliverer.credits == 100 + 300
        assert recipient.credits == 1000 - 300
        assert db.flush_calls == 1

    def test_different_sectors_rejected(self) -> None:
        deliverer_ship = _fake_ship(cargo={"contents": {"fuel": 500}})
        recipient_ship = _fake_ship()
        deliverer = _fake_player(current_ship=deliverer_ship, current_sector_id=1)
        recipient = _fake_player(current_ship=recipient_ship, current_sector_id=2)
        db = _FakeSession(player_seq=[deliverer, recipient])

        with pytest.raises(FuelDeliveryError, match="delivery_requires_same_sector"):
            fuel_delivery_service.deliver_fuel(db, deliverer.id, recipient.id, fuel_amount=10, payment_credits=0)

    def test_insufficient_deliverer_fuel_rejected(self) -> None:
        deliverer_ship = _fake_ship(cargo={"contents": {"fuel": 5}})
        recipient_ship = _fake_ship()
        deliverer = _fake_player(current_ship=deliverer_ship)
        recipient = _fake_player(current_ship=recipient_ship)
        db = _FakeSession(player_seq=[deliverer, recipient])

        with pytest.raises(FuelDeliveryError, match="insufficient_fuel_cargo"):
            fuel_delivery_service.deliver_fuel(db, deliverer.id, recipient.id, fuel_amount=10, payment_credits=0)

        # nothing mutated on the failed attempt
        assert deliverer_ship.cargo["contents"]["fuel"] == 5

    def test_insufficient_recipient_credits_rejected(self) -> None:
        deliverer_ship = _fake_ship(cargo={"contents": {"fuel": 500}})
        recipient_ship = _fake_ship()
        deliverer = _fake_player(current_ship=deliverer_ship)
        recipient = _fake_player(current_ship=recipient_ship, credits=10)
        db = _FakeSession(player_seq=[deliverer, recipient])

        with pytest.raises(FuelDeliveryError, match="insufficient_credits"):
            fuel_delivery_service.deliver_fuel(db, deliverer.id, recipient.id, fuel_amount=10, payment_credits=300)

        assert deliverer_ship.cargo["contents"]["fuel"] == 500  # untouched

    def test_recipient_cargo_capacity_exceeded_rejected(self) -> None:
        deliverer_ship = _fake_ship(cargo={"capacity": 200, "used": 500, "contents": {"fuel": 500}})
        recipient_ship = _fake_ship(cargo={"capacity": 50, "used": 45, "contents": {"organics": 45}})
        deliverer = _fake_player(current_ship=deliverer_ship)
        recipient = _fake_player(current_ship=recipient_ship)
        db = _FakeSession(player_seq=[deliverer, recipient])

        with pytest.raises(FuelDeliveryError, match="insufficient_cargo_space"):
            fuel_delivery_service.deliver_fuel(db, deliverer.id, recipient.id, fuel_amount=10, payment_credits=0)

        assert deliverer_ship.cargo["contents"]["fuel"] == 500  # untouched -- rejected before any transfer

    def test_delivering_to_self_rejected(self) -> None:
        player = _fake_player()
        db = _FakeSession(player_seq=[])
        with pytest.raises(FuelDeliveryError, match="yourself"):
            fuel_delivery_service.deliver_fuel(db, player.id, player.id, fuel_amount=10, payment_credits=0)

    def test_zero_or_negative_fuel_amount_rejected(self) -> None:
        db = _FakeSession(player_seq=[])
        with pytest.raises(FuelDeliveryError, match="fuel_amount must be positive"):
            fuel_delivery_service.deliver_fuel(db, uuid.uuid4(), uuid.uuid4(), fuel_amount=0, payment_credits=0)

    def test_negative_payment_rejected(self) -> None:
        db = _FakeSession(player_seq=[])
        with pytest.raises(FuelDeliveryError, match="payment_credits cannot be negative"):
            fuel_delivery_service.deliver_fuel(db, uuid.uuid4(), uuid.uuid4(), fuel_amount=10, payment_credits=-5)

    def test_zero_payment_is_a_valid_gift(self) -> None:
        """payment_credits=0 is explicitly allowed -- a player can gift fuel
        for free; only a negative payment is rejected."""
        deliverer_ship = _real_ship(cargo={"capacity": 1000, "used": 500, "contents": {"fuel": 500}})
        recipient_ship = _real_ship()
        deliverer = _fake_player(current_ship=deliverer_ship, credits=100)
        recipient = _fake_player(current_ship=recipient_ship, credits=50)
        db = _FakeSession(player_seq=[deliverer, recipient])

        result = fuel_delivery_service.deliver_fuel(db, deliverer.id, recipient.id, fuel_amount=10, payment_credits=0)

        assert result["payment_credits"] == 0
        assert deliverer.credits == 100
        assert recipient.credits == 50

    def test_deliverer_ship_destroyed_rejected(self) -> None:
        deliverer_ship = _fake_ship(is_destroyed=True)
        recipient_ship = _fake_ship()
        deliverer = _fake_player(current_ship=deliverer_ship)
        recipient = _fake_player(current_ship=recipient_ship)
        db = _FakeSession(player_seq=[deliverer, recipient])

        with pytest.raises(FuelDeliveryError, match="Deliverer has no active ship"):
            fuel_delivery_service.deliver_fuel(db, deliverer.id, recipient.id, fuel_amount=10, payment_credits=0)
