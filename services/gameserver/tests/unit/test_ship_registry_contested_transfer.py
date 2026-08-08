"""WO-BUILD-SHIP-REGISTRY-CONTESTED-TRANSFER-SALVAGE-CLAIM -- contested
registration-transfer / salvage-claim (SYSTEMS/ship-registry.md "Legal
ownership transfer").

DB-free: pins ship_registry_service.file_transfer_claim /
approve_transfer_claim / _complete_transfer_claim's own decision logic --
state transitions and the ERR_* rejections -- plus the cancel-on-stolen-
report hook inside report_stolen (fee refund + pending-transfer clear).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from src.models.bounty_claim import BountyClaim, BountyClaimStatus
from src.models.player import Player
from src.models.ship import Ship, ShipSpecification, ShipType
from src.models.ship_registry import RegistryEventType
from src.services import ship_registry_service as svc
from src.services.ship_registry_service import (
    ShipRegistryError,
    approve_transfer_claim,
    file_transfer_claim,
    report_stolen,
)


class _FakeQuery:
    def __init__(self, result):
        self._result = result

    def filter(self, *args, **kwargs):
        return self

    def populate_existing(self):
        return self

    def with_for_update(self):
        return self

    def first(self):
        return self._result


class _FakeSession:
    """Routes db.query(Entity) to a canned result per entity class, mirroring
    the sibling test files' convention. ``player_by_id`` lets a test hand
    back a specific Player row for BOTH the report_stolen thief-recompute
    lookup and the transfer-claim-cancel claimant-refund lookup -- both are
    plain ``db.query(Player)...with_for_update().first()`` calls with no
    distinguishing shape at the fake-session boundary, same limitation the
    sibling ``_FakeSession`` in test_ship_registry_behaviors.py already has
    (single canned Player result, not id-dispatched)."""

    def __init__(self, *, ship_spec=None, player=None, original_owner_id=None):
        self._ship_spec = ship_spec
        self._player = player
        self._original_owner_id = original_owner_id
        self.added = []

    def query(self, entity):
        if entity is ShipSpecification:
            return _FakeQuery(self._ship_spec)
        if entity is Player:
            return _FakeQuery(self._player)
        if entity is BountyClaim:
            return _FakeQuery(None)
        if entity is Ship.id:
            return _FakeQuery(None)
        # ship_registry_original_owner_id's ShipRegistry.original_owner_id lookup.
        return _FakeQuery((self._original_owner_id,) if self._original_owner_id else None)

    def add(self, obj):
        self.added.append(obj)

    def flush(self):
        pass


class _FakeBountyService:
    """report_stolen's ADR-0055 with_bounty path constructs a BountyService;
    these tests use no_bounty / no-pilot filings so it's never instantiated,
    but the fixture below still patches it out defensively to match the
    sibling test file's discipline."""

    last_instance = None

    def __init__(self, db):
        self.db = db
        _FakeBountyService.last_instance = self

    def place_bounty(self, *args, **kwargs):
        return {"success": True, "bounty_id": "bounty-ref-1"}

    def cancel_bounty(self, *args, **kwargs):
        return {"success": True, "refund": 0}


@pytest.fixture(autouse=True)
def _patch_bounty_service(monkeypatch):
    monkeypatch.setattr(svc, "BountyService", _FakeBountyService)
    _FakeBountyService.last_instance = None
    yield


def _owner():
    return Player(id=uuid.uuid4(), credits=1_000_000)


def _claimant(*, credits=1_000_000, docked=True, port_id=None):
    return Player(id=uuid.uuid4(), credits=credits, is_docked=docked, current_port_id=port_id)


def _ship(*, owner, pilot_id=None, purchase_value=100_000, **overrides):
    defaults = dict(
        id=uuid.uuid4(),
        registration_number="REG-ABCD-2103",
        registered_owner_id=owner.id,
        owner_id=owner.id,
        current_pilot_id=pilot_id,
        current_pilot_since=None,
        stolen_status=None,
        pending_transfer_claimant_id=None,
        pending_transfer_requested_at=None,
        pending_transfer_deadline=None,
        pending_transfer_fee_paid=None,
        pending_transfer_port_id=None,
        insurance=None,
        purchase_value=purchase_value,
        type=ShipType.LIGHT_FREIGHTER,
    )
    defaults.update(overrides)
    return Ship(**defaults)


@pytest.mark.unit
class TestFileTransferClaim:
    def test_rejects_already_owner(self):
        owner = _owner()
        ship = _ship(owner=owner, current_pilot_id=None)
        db = _FakeSession()

        with pytest.raises(ShipRegistryError) as excinfo:
            file_transfer_claim(db, ship=ship, claimant=owner, port_id=uuid.uuid4())
        assert excinfo.value.code == "ERR_ALREADY_OWNER"

    def test_rejects_already_pending(self):
        owner = _owner()
        port_id = uuid.uuid4()
        ship = _ship(owner=owner, pending_transfer_claimant_id=uuid.uuid4())
        claimant = _claimant(port_id=port_id)
        db = _FakeSession()

        with pytest.raises(ShipRegistryError) as excinfo:
            file_transfer_claim(db, ship=ship, claimant=claimant, port_id=port_id)
        assert excinfo.value.code == "ERR_TRANSFER_ALREADY_PENDING"

    def test_rejects_when_not_docked_at_port(self):
        owner = _owner()
        ship = _ship(owner=owner)
        claimant = _claimant(docked=False, port_id=None)
        db = _FakeSession()

        with pytest.raises(ShipRegistryError) as excinfo:
            file_transfer_claim(db, ship=ship, claimant=claimant, port_id=uuid.uuid4())
        assert excinfo.value.code == "ERR_NOT_AT_PORT"

    def test_rejects_stolen_ship(self):
        owner = _owner()
        port_id = uuid.uuid4()
        ship = _ship(owner=owner, stolen_status=True)
        claimant = _claimant(port_id=port_id)
        db = _FakeSession()

        with pytest.raises(ShipRegistryError) as excinfo:
            file_transfer_claim(db, ship=ship, claimant=claimant, port_id=port_id)
        assert excinfo.value.code == "ERR_SHIP_STOLEN"

    def test_rejects_when_borrowed_less_than_1_hour(self):
        owner = _owner()
        port_id = uuid.uuid4()
        claimant = _claimant(port_id=port_id)
        ship = _ship(
            owner=owner,
            pilot_id=claimant.id,
            current_pilot_since=datetime.now(timezone.utc) - timedelta(minutes=30),
        )
        db = _FakeSession()

        with pytest.raises(ShipRegistryError) as excinfo:
            file_transfer_claim(db, ship=ship, claimant=claimant, port_id=port_id)
        assert excinfo.value.code == "ERR_NOT_ELIGIBLE_FOR_TRANSFER"

    def test_rejects_when_piloted_by_someone_other_than_claimant(self):
        owner = _owner()
        port_id = uuid.uuid4()
        claimant = _claimant(port_id=port_id)
        other_pilot_id = uuid.uuid4()
        ship = _ship(owner=owner, pilot_id=other_pilot_id)
        db = _FakeSession()

        with pytest.raises(ShipRegistryError) as excinfo:
            file_transfer_claim(db, ship=ship, claimant=claimant, port_id=port_id)
        assert excinfo.value.code == "ERR_NOT_ELIGIBLE_FOR_TRANSFER"

    def test_rejects_insufficient_credits(self):
        owner = _owner()
        port_id = uuid.uuid4()
        claimant = _claimant(credits=1000, port_id=port_id)  # 30% of 100_000 = 30_000
        ship = _ship(owner=owner)  # Drifting, eligible
        db = _FakeSession()

        with pytest.raises(ShipRegistryError) as excinfo:
            file_transfer_claim(db, ship=ship, claimant=claimant, port_id=port_id)
        assert excinfo.value.code == "ERR_INSUFFICIENT_CREDITS"

    def test_drifting_ship_files_successfully_and_charges_fee(self):
        owner = _owner()
        port_id = uuid.uuid4()
        claimant = _claimant(credits=1_000_000, port_id=port_id)
        ship = _ship(owner=owner, purchase_value=100_000)  # Drifting
        db = _FakeSession()

        result = file_transfer_claim(db, ship=ship, claimant=claimant, port_id=port_id)

        assert claimant.credits == 1_000_000 - 30_000
        assert ship.pending_transfer_claimant_id == claimant.id
        assert ship.pending_transfer_fee_paid == 30_000
        assert ship.pending_transfer_port_id == port_id
        assert ship.pending_transfer_requested_at is not None
        assert ship.pending_transfer_deadline == ship.pending_transfer_requested_at + timedelta(hours=24)
        assert result["fee_paid"] == 30_000
        assert result["dispute_deadline"] == ship.pending_transfer_deadline.isoformat()
        # Filing itself does NOT append a ShipRegistry row (ownership hasn't
        # changed yet -- only completion/cancellation append).
        assert db.added == []

    def test_borrowed_over_1_hour_files_successfully(self):
        owner = _owner()
        port_id = uuid.uuid4()
        claimant = _claimant(port_id=port_id)
        ship = _ship(
            owner=owner,
            pilot_id=claimant.id,
            current_pilot_since=datetime.now(timezone.utc) - timedelta(hours=2),
        )
        db = _FakeSession()

        result = file_transfer_claim(db, ship=ship, claimant=claimant, port_id=port_id)

        assert ship.pending_transfer_claimant_id == claimant.id
        assert result["fee_paid"] == 30_000


@pytest.mark.unit
class TestApproveTransferClaim:
    def test_rejects_non_owner(self):
        owner = _owner()
        claimant_id = uuid.uuid4()
        ship = _ship(owner=owner, pending_transfer_claimant_id=claimant_id)
        not_owner = Player(id=uuid.uuid4())
        db = _FakeSession()

        with pytest.raises(ShipRegistryError) as excinfo:
            approve_transfer_claim(db, ship=ship, owner=not_owner)
        assert excinfo.value.code == "ERR_NOT_REGISTERED_OWNER"

    def test_rejects_no_pending_transfer(self):
        owner = _owner()
        ship = _ship(owner=owner, pending_transfer_claimant_id=None)
        db = _FakeSession()

        with pytest.raises(ShipRegistryError) as excinfo:
            approve_transfer_claim(db, ship=ship, owner=owner)
        assert excinfo.value.code == "ERR_NO_PENDING_TRANSFER"

    def test_approve_completes_transfer_and_keeps_fee(self):
        owner = _owner()
        claimant_id = uuid.uuid4()
        port_id = uuid.uuid4()
        now = datetime.now(timezone.utc)
        ship = _ship(
            owner=owner,
            pending_transfer_claimant_id=claimant_id,
            pending_transfer_requested_at=now,
            pending_transfer_deadline=now + timedelta(hours=24),
            pending_transfer_fee_paid=30_000,
            pending_transfer_port_id=port_id,
            insurance={"tier": "BASIC"},
        )
        db = _FakeSession(original_owner_id=owner.id)

        result = approve_transfer_claim(db, ship=ship, owner=owner)

        assert ship.registered_owner_id == claimant_id
        assert ship.owner_id == claimant_id
        assert ship.insurance is None  # voids on ownership change
        assert ship.pending_transfer_claimant_id is None
        assert ship.pending_transfer_requested_at is None
        assert ship.pending_transfer_deadline is None
        assert ship.pending_transfer_fee_paid is None
        assert ship.pending_transfer_port_id is None
        assert result["registered_owner_id"] == str(claimant_id)
        assert result["fee_paid"] == 30_000
        assert result["via"] == "owner_approved"
        assert len(db.added) == 1
        event = db.added[0]
        assert event.event_type == RegistryEventType.OWNERSHIP_TRANSFER
        assert event.new_owner_id == claimant_id
        assert event.previous_owner_id == owner.id
        assert event.transfer_fee_paid == 30_000
        assert event.event_metadata["via"] == "owner_approved"


@pytest.mark.unit
class TestCancelOnStolenReport:
    def test_stolen_report_cancels_pending_transfer_and_refunds_fee(self):
        owner = _owner()
        claimant = Player(id=uuid.uuid4(), credits=500_000)
        port_id = uuid.uuid4()
        now = datetime.now(timezone.utc)
        ship = _ship(
            owner=owner,
            pilot_id=None,  # Drifting stolen report -- no thief, no bounty path
            pending_transfer_claimant_id=claimant.id,
            pending_transfer_requested_at=now,
            pending_transfer_deadline=now + timedelta(hours=24),
            pending_transfer_fee_paid=30_000,
            pending_transfer_port_id=port_id,
        )
        db = _FakeSession(player=claimant)

        result = report_stolen(db, ship=ship, owner=owner, recovery_mode="no_bounty")

        assert claimant.credits == 530_000
        assert ship.pending_transfer_claimant_id is None
        assert ship.pending_transfer_requested_at is None
        assert ship.pending_transfer_deadline is None
        assert ship.pending_transfer_fee_paid is None
        assert ship.pending_transfer_port_id is None
        assert ship.stolen_status is True
        assert result["cancelled_transfer_claim"] is True

    def test_stolen_report_with_no_pending_transfer_is_unaffected(self):
        owner = _owner()
        ship = _ship(owner=owner, pilot_id=None)
        db = _FakeSession()

        result = report_stolen(db, ship=ship, owner=owner, recovery_mode="no_bounty")

        assert result["cancelled_transfer_claim"] is False
