"""WO-FIX-SHIP-REGISTRY-TRANSFER-SALVAGE-TRADE-ABANDON -- abandon / claim
behavioral flows (SYSTEMS/ship-registry.md "Abandonment").

DB-free: pins ship_registry_service.abandon_ship / claim_abandoned_ship's own
decision logic -- state transitions and the ERR_* rejections.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from src.models.player import Player
from src.models.ship import Ship, ShipType, ShipStatus
from src.models.ship_registry import RegistryEventType
from src.services.ship_registry_service import (
    ShipRegistryError,
    abandon_ship,
    claim_abandoned_ship,
    archive_expired_abandoned_ships,
    ABANDONMENT_ARCHIVE_DAYS,
)


class _FakeQuery:
    def __init__(self, result):
        self._result = result

    def filter(self, *args, **kwargs):
        return self

    def first(self):
        return self._result

    def all(self):
        if isinstance(self._result, list):
            return self._result
        return []


class _FakeSession:
    def __init__(self, *, original_owner_id=None, ships=None, players=None):
        self._original_owner_id = original_owner_id
        self._ships = ships or []
        self._players = {p.id: p for p in (players or [])}
        self.added = []

    def query(self, entity):
        # ship_registry_original_owner_id's ShipRegistry.original_owner_id lookup.
        name = getattr(entity, "__name__", None) or getattr(
            getattr(entity, "class_", None), "__name__", None
        )
        # entity may be a column expression (ShipRegistry.original_owner_id) —
        # abandon/claim path uses first() with a tuple result.
        if entity is Ship or name == "Ship":
            return _FakeQuery(self._ships)
        if entity is Player or name == "Player":
            # .filter(Player.id == x).first() — return matching player if any
            return _PlayerQuery(self._players)
        return _FakeQuery((self._original_owner_id,) if self._original_owner_id else None)

    def add(self, obj):
        self.added.append(obj)

    def flush(self):
        pass


class _PlayerQuery:
    def __init__(self, players_by_id):
        self._players = players_by_id
        self._id = None

    def filter(self, *args, **kwargs):
        # Best-effort: if the first clause looks like equality against a UUID, use it.
        for a in args:
            right = getattr(a, "right", None)
            val = getattr(right, "value", None) if right is not None else None
            if val is not None:
                self._id = val
                break
        return self

    def first(self):
        if self._id is None:
            return next(iter(self._players.values()), None)
        return self._players.get(self._id)


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


@pytest.mark.unit
class TestArchiveExpiredAbandonedShips:
    def test_archives_past_cutoff_and_skips_fresh(self, monkeypatch):
        owner = _owner()
        now = datetime.now(timezone.utc)
        stale = _ship(
            owner=owner,
            is_abandoned=True,
            abandoned_at=now - timedelta(days=ABANDONMENT_ARCHIVE_DAYS + 1),
            is_destroyed=False,
            is_active=True,
            status=ShipStatus.DOCKED,
            cargo={"contents": {"ore": 5}},
        )
        fresh = _ship(
            owner=owner,
            is_abandoned=True,
            abandoned_at=now - timedelta(days=1),
            is_destroyed=False,
            is_active=True,
            status=ShipStatus.DOCKED,
        )
        # Fake query returns both; service filters in SQL — for FakeSession we
        # pre-filter to what a real query would return (past cutoff only).
        db = _FakeSession(
            original_owner_id=owner.id,
            ships=[stale],
            players=[owner],
        )

        spawned = []

        class _FakeCombat:
            def __init__(self, _db):
                pass

            def _spawn_cargo_wreck(self, ship, cause, original_owner, killer):
                spawned.append((ship.id, cause))
                return None

        monkeypatch.setattr(
            "src.services.combat_service.CombatService", _FakeCombat,
        )

        count = archive_expired_abandoned_ships(db, now=now)

        assert count == 1
        assert stale.is_destroyed is True
        assert stale.is_abandoned is False
        assert stale.status == ShipStatus.DESTROYED
        assert stale.destruction_cause == "abandonment_expired"
        assert stale.cargo == {"contents": {}}
        assert spawned == [(stale.id, "abandonment_expired")]
        assert any(e.event_type == RegistryEventType.ARCHIVED for e in db.added)
        # fresh never queried
        assert fresh.is_destroyed is False

    def test_noop_when_none_due(self, monkeypatch):
        db = _FakeSession(ships=[])
        monkeypatch.setattr(
            "src.services.combat_service.CombatService",
            lambda db: (_ for _ in ()).throw(AssertionError("should not spawn")),
        )
        assert archive_expired_abandoned_ships(db) == 0
