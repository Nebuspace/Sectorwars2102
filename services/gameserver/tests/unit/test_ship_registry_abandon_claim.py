"""WO-FIX-SHIP-REGISTRY-TRANSFER-SALVAGE-TRADE-ABANDON -- abandon / claim
behavioral flows (SYSTEMS/ship-registry.md "Abandonment").

DB-free: pins ship_registry_service.abandon_ship / claim_abandoned_ship's own
decision logic -- state transitions and the ERR_* rejections.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest

from src.models.player import Player
from src.models.ship import Ship, ShipType
from src.models.ship_registry import RegistryEventType
from src.services.ship_registry_service import (
    ShipRegistryError,
    abandon_ship,
    claim_abandoned_ship,
)


class _FakeQuery:
    def __init__(self, result):
        self._result = result

    def filter(self, *args, **kwargs):
        return self

    def first(self):
        return self._result


class _FakeSession:
    def __init__(self, *, original_owner_id=None):
        self._original_owner_id = original_owner_id
        self.added = []

    def query(self, entity):
        # ship_registry_original_owner_id's ShipRegistry.original_owner_id lookup.
        return _FakeQuery((self._original_owner_id,) if self._original_owner_id else None)

    def add(self, obj):
        self.added.append(obj)

    def flush(self):
        pass


def _owner():
    return Player(id=uuid.uuid4(), is_docked=True, current_port_id=uuid.uuid4(), current_ship_id=None)


def _ship(*, owner, pilot_id=None, is_abandoned=False, **overrides):
    defaults = dict(
        id=uuid.uuid4(),
        registration_number="REG-ABCD-2103",
        registered_owner_id=owner.id,
        owner_id=owner.id,
        current_pilot_id=pilot_id,
        is_abandoned=is_abandoned,
        abandoned_at=None,
        insurance=None,
        purchase_value=100_000,
        type=ShipType.LIGHT_FREIGHTER,
    )
    defaults.update(overrides)
    return Ship(**defaults)


@pytest.mark.unit
class TestAbandonShip:
    def test_rejects_non_owner(self):
        owner = _owner()
        ship = _ship(owner=owner)
        not_owner = Player(id=uuid.uuid4(), is_docked=True, current_port_id=owner.current_port_id)
        db = _FakeSession()

        with pytest.raises(ShipRegistryError) as excinfo:
            abandon_ship(db, ship=ship, owner=not_owner, port_id=owner.current_port_id)
        assert excinfo.value.code == "ERR_NOT_REGISTERED_OWNER"

    def test_rejects_already_abandoned(self):
        owner = _owner()
        ship = _ship(owner=owner, is_abandoned=True)
        db = _FakeSession()

        with pytest.raises(ShipRegistryError) as excinfo:
            abandon_ship(db, ship=ship, owner=owner, port_id=owner.current_port_id)
        assert excinfo.value.code == "ERR_ALREADY_ABANDONED"

    def test_rejects_when_not_docked_at_port(self):
        owner = _owner()
        ship = _ship(owner=owner)
        db = _FakeSession()
        other_port = uuid.uuid4()

        with pytest.raises(ShipRegistryError) as excinfo:
            abandon_ship(db, ship=ship, owner=owner, port_id=other_port)
        assert excinfo.value.code == "ERR_NOT_AT_PORT"

    def test_rejects_when_borrowed_by_someone_else(self):
        owner = _owner()
        borrower_id = uuid.uuid4()
        ship = _ship(owner=owner, pilot_id=borrower_id)
        db = _FakeSession()

        with pytest.raises(ShipRegistryError) as excinfo:
            abandon_ship(db, ship=ship, owner=owner, port_id=owner.current_port_id)
        assert excinfo.value.code == "ERR_SHIP_BORROWED"

    def test_abandons_and_clears_pilot_and_current_ship(self):
        owner = _owner()
        ship = _ship(owner=owner, pilot_id=owner.id)
        owner.current_ship_id = ship.id
        db = _FakeSession()

        result = abandon_ship(db, ship=ship, owner=owner, port_id=owner.current_port_id)

        assert ship.is_abandoned is True
        assert ship.abandoned_at is not None
        assert ship.current_pilot_id is None
        assert owner.current_ship_id is None
        assert result["is_abandoned"] is True
        assert len(db.added) == 1
        event = db.added[0]
        assert event.event_type == RegistryEventType.ABANDONED
        assert event.previous_owner_id == owner.id

    def test_owner_aboard_a_different_ship_is_untouched(self):
        owner = _owner()
        ship = _ship(owner=owner, pilot_id=None)
        other_ship_id = uuid.uuid4()
        owner.current_ship_id = other_ship_id
        db = _FakeSession()

        abandon_ship(db, ship=ship, owner=owner, port_id=owner.current_port_id)

        assert owner.current_ship_id == other_ship_id


@pytest.mark.unit
class TestClaimAbandonedShip:
    def test_rejects_not_abandoned(self):
        owner = _owner()
        ship = _ship(owner=owner, is_abandoned=False)
        claimant = Player(id=uuid.uuid4(), is_docked=True, current_port_id=owner.current_port_id)
        db = _FakeSession()

        with pytest.raises(ShipRegistryError) as excinfo:
            claim_abandoned_ship(db, ship=ship, claimant=claimant, port_id=owner.current_port_id)
        assert excinfo.value.code == "ERR_NOT_ABANDONED"

    def test_rejects_when_not_docked_at_port(self):
        owner = _owner()
        ship = _ship(owner=owner, is_abandoned=True, abandoned_at=datetime.now(timezone.utc))
        claimant = Player(id=uuid.uuid4(), is_docked=False, current_port_id=None)
        db = _FakeSession()

        with pytest.raises(ShipRegistryError) as excinfo:
            claim_abandoned_ship(db, ship=ship, claimant=claimant, port_id=owner.current_port_id)
        assert excinfo.value.code == "ERR_NOT_AT_PORT"

    def test_claim_transfers_ownership_no_fee_no_dispute(self):
        owner = _owner()
        ship = _ship(
            owner=owner, is_abandoned=True, abandoned_at=datetime.now(timezone.utc),
            insurance={"tier": "BASIC"},
        )
        claimant = Player(id=uuid.uuid4(), is_docked=True, current_port_id=owner.current_port_id)
        db = _FakeSession(original_owner_id=owner.id)

        result = claim_abandoned_ship(db, ship=ship, claimant=claimant, port_id=owner.current_port_id)

        assert ship.registered_owner_id == claimant.id
        assert ship.owner_id == claimant.id
        assert ship.is_abandoned is False
        assert ship.abandoned_at is None
        assert ship.insurance is None  # voids on ownership change
        assert result["registered_owner_id"] == str(claimant.id)
        assert len(db.added) == 1
        event = db.added[0]
        assert event.event_type == RegistryEventType.OWNERSHIP_TRANSFER
        assert event.previous_owner_id == owner.id
        assert event.new_owner_id == claimant.id
        assert event.original_owner_id == owner.id
        assert event.transfer_fee_paid == 0
