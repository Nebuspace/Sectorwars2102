"""WO-FIX-SHIP-REGISTRY-BEHAVIORAL-ROUTES -- report-stolen / retract-stolen-
report behavioral flows (SYSTEMS/ship-registry.md "Reporting a ship stolen").

DB-free: BountyService is monkeypatched (its own locking/credit-transfer
logic is proven separately in tests/unit for bounty_service). These tests
pin ship_registry_service.report_stolen / retract_stolen_report's own
decision logic -- state transitions, the ERR_* rejections, and the two
named anti-collusion rules this WO wires:
  - ADR-0049 SK9: retract rejected with ERR_BOUNTY_ALREADY_COLLECTED once a
    kill has fired against the auto-placed bounty.
  - ADR-0055 S-F1 (filing half): report_stolen rejected with
    ERR_THIEF_IS_TEAM_MATE when filer and current pilot share a team.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from src.models.bounty_claim import BountyClaim, BountyClaimStatus
from src.models.player import Player
from src.models.ship import Ship, ShipSpecification, ShipType
from src.services import ship_registry_service as svc
from src.services.ship_registry_service import (
    ShipRegistryError,
    report_stolen,
    retract_stolen_report,
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
    """Routes db.query(Entity) to a canned result per entity class -- not a
    general-purpose fake, mirrors this file's sibling test's own convention.

    ``other_stolen_pilot_id`` feeds ``wanted_service._is_piloting_stolen_ship``
    (queries ``Ship.id``, an InstrumentedAttribute, not the ``Ship`` class --
    matched separately below): None means "not piloting any stolen ship" for
    every player, matching every existing test's implicit expectation."""

    def __init__(self, *, ship_spec=None, thief=None, bounty_claim=None, still_piloting_stolen=False):
        self._ship_spec = ship_spec
        self._thief = thief
        self._bounty_claim = bounty_claim
        self._still_piloting_stolen = still_piloting_stolen
        self.added = []

    def query(self, entity):
        if entity is ShipSpecification:
            return _FakeQuery(self._ship_spec)
        if entity is Player:
            return _FakeQuery(self._thief)
        if entity is BountyClaim:
            return _FakeQuery(self._bounty_claim)
        if entity is Ship.id:
            # wanted_service._is_piloting_stolen_ship's live-query hook.
            return _FakeQuery(uuid.uuid4() if self._still_piloting_stolen else None)
        raise AssertionError(f"unexpected query entity in fake session: {entity!r}")

    def add(self, obj):
        self.added.append(obj)

    def flush(self):
        pass


class _FakeBountyService:
    """Captures place_bounty/cancel_bounty calls; each test sets the return
    value it wants via the instance created inside report_stolen/
    retract_stolen_report (monkeypatch swaps the class, then the test reads
    back the single instance recorded on the class)."""

    last_instance = None

    def __init__(self, db):
        self.db = db
        self.place_calls = []
        self.cancel_calls = []
        self.place_result = {"success": True, "bounty_id": "bounty-ref-1"}
        self.cancel_result = {"success": True, "refund": 0}
        _FakeBountyService.last_instance = self

    def place_bounty(self, placer_id, target_id, amount, duration_days=None, fee_pct=None):
        self.place_calls.append(
            {"placer_id": placer_id, "target_id": target_id, "amount": amount, "fee_pct": fee_pct}
        )
        return self.place_result

    def cancel_bounty(self, placer_id, bounty_id, target_id, refund_pct=1.0):
        self.cancel_calls.append(
            {"placer_id": placer_id, "bounty_id": bounty_id, "target_id": target_id, "refund_pct": refund_pct}
        )
        return self.cancel_result


@pytest.fixture(autouse=True)
def _patch_bounty_service(monkeypatch):
    monkeypatch.setattr(svc, "BountyService", _FakeBountyService)
    _FakeBountyService.last_instance = None
    yield


def _owner(team_id=None):
    return Player(id=uuid.uuid4(), team_id=team_id, credits=1_000_000)


def _ship(*, owner, pilot_id=None, purchase_value=100_000, **overrides):
    defaults = dict(
        id=uuid.uuid4(),
        registration_number="REG-ABCD-2103",
        registered_owner_id=owner.id,
        current_pilot_id=pilot_id,
        stolen_status=None,
        stolen_reported_at=None,
        stolen_recovery_mode=None,
        stolen_thief_id=None,
        stolen_bounty_ref=None,
        retract_grace_processed=False,
        purchase_value=purchase_value,
        type=ShipType.LIGHT_FREIGHTER,
    )
    defaults.update(overrides)
    return Ship(**defaults)


@pytest.mark.unit
class TestReportStolen:
    def test_rejects_non_owner(self):
        owner = _owner()
        ship = _ship(owner=owner)
        not_owner = Player(id=uuid.uuid4())
        db = _FakeSession()

        with pytest.raises(ShipRegistryError) as excinfo:
            report_stolen(db, ship=ship, owner=not_owner)
        assert excinfo.value.code == "ERR_NOT_REGISTERED_OWNER"
        assert ship.stolen_status is not True

    def test_rejects_already_stolen(self):
        owner = _owner()
        ship = _ship(owner=owner, stolen_status=True)
        db = _FakeSession()

        with pytest.raises(ShipRegistryError) as excinfo:
            report_stolen(db, ship=ship, owner=owner)
        assert excinfo.value.code == "ERR_ALREADY_STOLEN"

    def test_blocks_team_mate_thief(self):
        team_id = uuid.uuid4()
        owner = _owner(team_id=team_id)
        thief_id = uuid.uuid4()
        thief = Player(id=thief_id, team_id=team_id)
        ship = _ship(owner=owner, pilot_id=thief_id)
        db = _FakeSession(thief=thief)

        with pytest.raises(ShipRegistryError) as excinfo:
            report_stolen(db, ship=ship, owner=owner)
        assert excinfo.value.code == "ERR_THIEF_IS_TEAM_MATE"
        # Filing rejected entirely -- no bounty placed, no state mutated.
        assert ship.stolen_status is not True
        assert _FakeBountyService.last_instance is None

    def test_no_bounty_mode_sets_flags_and_never_calls_place_bounty(self):
        owner = _owner()
        thief_id = uuid.uuid4()
        ship = _ship(owner=owner, pilot_id=thief_id)
        db = _FakeSession(thief=Player(id=thief_id, team_id=None))

        result = report_stolen(db, ship=ship, owner=owner, recovery_mode="no_bounty")

        assert ship.stolen_status is True
        assert ship.stolen_recovery_mode == "no_bounty"
        assert ship.stolen_thief_id == thief_id
        assert ship.stolen_bounty_ref is None
        assert result["bounty_id"] is None
        assert _FakeBountyService.last_instance is None

    def test_with_bounty_mode_places_a_zero_fee_bounty_for_half_ship_value(self):
        owner = _owner()
        thief_id = uuid.uuid4()
        ship = _ship(owner=owner, pilot_id=thief_id, purchase_value=200_000)
        db = _FakeSession(thief=Player(id=thief_id, team_id=None))

        result = report_stolen(db, ship=ship, owner=owner, recovery_mode="with_bounty")

        bs = _FakeBountyService.last_instance
        assert bs is not None
        assert len(bs.place_calls) == 1
        call = bs.place_calls[0]
        assert call["placer_id"] == owner.id
        assert call["target_id"] == thief_id
        assert call["amount"] == 100_000  # 50% of purchase_value
        assert call["fee_pct"] == 0.0
        assert ship.stolen_bounty_ref == "bounty-ref-1"
        assert result["bounty_id"] == "bounty-ref-1"
        assert ship.stolen_status is True

    def test_insufficient_credits_raises_and_leaves_ship_unstolen(self):
        owner = _owner()
        thief_id = uuid.uuid4()
        ship = _ship(owner=owner, pilot_id=thief_id)
        db = _FakeSession(thief=Player(id=thief_id, team_id=None))

        # Patch the fake to report a failed placement (mirrors place_bounty's
        # real insufficient-credits response shape).
        real_init = _FakeBountyService.__init__

        def _init(self, db_):
            real_init(self, db_)
            self.place_result = {"success": False, "message": "Need 100010 credits (100000 + 10 fee), have 5"}

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(_FakeBountyService, "__init__", _init)
            with pytest.raises(ShipRegistryError) as excinfo:
                report_stolen(db, ship=ship, owner=owner, recovery_mode="with_bounty")

        assert excinfo.value.code == "ERR_INSUFFICIENT_CREDITS_FOR_AUTO_BOUNTY"
        assert ship.stolen_status is not True
        assert ship.stolen_reported_at is None

    def test_defaults_recovery_mode_from_insurable_spec(self):
        owner = _owner()
        ship = _ship(owner=owner, pilot_id=None)
        spec = ShipSpecification(type=ShipType.LIGHT_FREIGHTER, insurable=False)
        db = _FakeSession(ship_spec=spec)

        result = report_stolen(db, ship=ship, owner=owner, recovery_mode=None)

        assert result["recovery_mode"] == "no_bounty"

    def test_rejects_invalid_recovery_mode(self):
        owner = _owner()
        ship = _ship(owner=owner)
        db = _FakeSession()

        with pytest.raises(ShipRegistryError) as excinfo:
            report_stolen(db, ship=ship, owner=owner, recovery_mode="bogus")
        assert excinfo.value.code == "ERR_INVALID_RECOVERY_MODE"


@pytest.mark.unit
class TestRetractStolenReport:
    def _stolen_ship(self, *, owner, thief_id, reported_at, bounty_ref="bounty-ref-1"):
        return _ship(
            owner=owner,
            pilot_id=thief_id,
            stolen_status=True,
            stolen_reported_at=reported_at,
            stolen_recovery_mode="with_bounty",
            stolen_thief_id=thief_id,
            stolen_bounty_ref=bounty_ref,
        )

    def test_rejects_non_owner(self):
        owner = _owner()
        thief_id = uuid.uuid4()
        ship = self._stolen_ship(owner=owner, thief_id=thief_id, reported_at=datetime.now(timezone.utc))
        not_owner = Player(id=uuid.uuid4())
        db = _FakeSession()

        with pytest.raises(ShipRegistryError) as excinfo:
            retract_stolen_report(db, ship=ship, owner=not_owner)
        assert excinfo.value.code == "ERR_NOT_REGISTERED_OWNER"

    def test_rejects_not_stolen(self):
        owner = _owner()
        ship = _ship(owner=owner, stolen_status=None)
        db = _FakeSession()

        with pytest.raises(ShipRegistryError) as excinfo:
            retract_stolen_report(db, ship=ship, owner=owner)
        assert excinfo.value.code == "ERR_NOT_STOLEN"

    def test_blocked_once_bounty_already_collected(self):
        """ADR-0049 SK9: a PAID BountyClaim on the thief, claimed after the
        report was filed, blocks retraction outright -- no refund, no state
        mutation, checked before any refund logic runs."""
        owner = _owner()
        thief_id = uuid.uuid4()
        reported_at = datetime.now(timezone.utc) - timedelta(hours=1)
        ship = self._stolen_ship(owner=owner, thief_id=thief_id, reported_at=reported_at)
        claim = BountyClaim(
            id=uuid.uuid4(), claimant_id=uuid.uuid4(), target_id=thief_id,
            amount=50_000, status=BountyClaimStatus.PAID,
        )
        db = _FakeSession(bounty_claim=claim)

        with pytest.raises(ShipRegistryError) as excinfo:
            retract_stolen_report(db, ship=ship, owner=owner)
        assert excinfo.value.code == "ERR_BOUNTY_ALREADY_COLLECTED"
        assert ship.stolen_status is True  # unchanged
        assert _FakeBountyService.last_instance is None  # cancel_bounty never reached

    def test_within_24h_refunds_75_percent(self):
        owner = _owner()
        thief_id = uuid.uuid4()
        reported_at = datetime.now(timezone.utc) - timedelta(hours=1)
        ship = self._stolen_ship(owner=owner, thief_id=thief_id, reported_at=reported_at)
        db = _FakeSession(bounty_claim=None)

        result = retract_stolen_report(db, ship=ship, owner=owner)

        bs = _FakeBountyService.last_instance
        assert bs.cancel_calls[0]["refund_pct"] == 0.75
        assert bs.cancel_calls[0]["bounty_id"] == "bounty-ref-1"
        assert bs.cancel_calls[0]["target_id"] == thief_id
        assert ship.stolen_status is False
        assert ship.stolen_bounty_ref is None
        assert ship.stolen_thief_id is None
        assert result["stolen_status"] is False

    def test_after_24h_refunds_nothing(self):
        owner = _owner()
        thief_id = uuid.uuid4()
        reported_at = datetime.now(timezone.utc) - timedelta(hours=25)
        ship = self._stolen_ship(owner=owner, thief_id=thief_id, reported_at=reported_at)
        db = _FakeSession(bounty_claim=None)

        retract_stolen_report(db, ship=ship, owner=owner)

        bs = _FakeBountyService.last_instance
        assert bs.cancel_calls[0]["refund_pct"] == 0.0

    def test_retract_grace_processed_forces_zero_refund_even_within_24h(self):
        """ADR-0053 WR10: once the set-once flag has fired, the standard
        no-refund-after-24h rule applies even if the 24h boundary math alone
        would still read as within-grace (defensive -- the flag is the
        source of truth once set)."""
        owner = _owner()
        thief_id = uuid.uuid4()
        reported_at = datetime.now(timezone.utc) - timedelta(minutes=5)
        ship = self._stolen_ship(owner=owner, thief_id=thief_id, reported_at=reported_at)
        ship.retract_grace_processed = True
        db = _FakeSession(bounty_claim=None)

        retract_stolen_report(db, ship=ship, owner=owner)

        bs = _FakeBountyService.last_instance
        assert bs.cancel_calls[0]["refund_pct"] == 0.0

    def test_no_bounty_ref_skips_cancel_call(self):
        owner = _owner()
        thief_id = uuid.uuid4()
        ship = self._stolen_ship(
            owner=owner, thief_id=thief_id, reported_at=datetime.now(timezone.utc), bounty_ref=None,
        )
        db = _FakeSession(bounty_claim=None)

        result = retract_stolen_report(db, ship=ship, owner=owner)

        assert _FakeBountyService.last_instance is None
        assert result["refund"] == 0
        assert ship.stolen_status is False


@pytest.mark.unit
class TestWantedTriggerWiring:
    """WO-BUILD-WANTED-TRIGGERS-STOLEN-SHIP-LOW-REP: ranking.md's stolen-
    ship Wanted trigger, fired via ``wanted_service.recompute_is_wanted``
    from inside ``report_stolen``/``retract_stolen_report``."""

    def test_report_stolen_flips_thief_is_wanted(self):
        owner = _owner()
        thief_id = uuid.uuid4()
        thief = Player(id=thief_id, team_id=None, is_wanted=False, personal_reputation=0)
        ship = _ship(owner=owner, pilot_id=thief_id)
        # still_piloting_stolen=True: mirrors real-DB autoflush -- the
        # in-flight `ship.stolen_status = True` mutation just above (still
        # pending, not yet flushed) is visible to recompute_is_wanted's own
        # query in the same session (autoflush=True by default), so the
        # ship this thief is piloting right now IS found as stolen.
        db = _FakeSession(thief=thief, still_piloting_stolen=True)

        report_stolen(db, ship=ship, owner=owner, recovery_mode="no_bounty")

        assert thief.is_wanted is True
        assert thief.wanted_declared_at is not None
        # Not the timer-based trigger -- wanted_until stays untouched (None).
        assert thief.wanted_until is None

    def test_report_stolen_returns_server_authoritative_retract_grace_deadline(self):
        """WR10: the client must never duplicate STOLEN_RETRACT_GRACE (24h)
        itself -- the deadline is server-computed and returned so a ticker
        can render a countdown without hardcoding the grace duration."""
        owner = _owner()
        ship = _ship(owner=owner, pilot_id=None)
        db = _FakeSession()

        result = report_stolen(db, ship=ship, owner=owner, recovery_mode="no_bounty")

        reported_at = datetime.fromisoformat(result["stolen_reported_at"])
        deadline = datetime.fromisoformat(result["retract_grace_expires_at"])
        assert deadline - reported_at == svc.STOLEN_RETRACT_GRACE

    def test_report_stolen_with_no_current_pilot_does_not_query_or_flip_anyone(self):
        owner = _owner()
        ship = _ship(owner=owner, pilot_id=None)
        db = _FakeSession(thief=None)

        result = report_stolen(db, ship=ship, owner=owner, recovery_mode="no_bounty")

        assert result["ship_id"] == str(ship.id)  # ran to completion, no unexpected query

    def test_retract_clears_is_wanted_once_no_longer_stolen(self):
        owner = _owner()
        thief_id = uuid.uuid4()
        thief = Player(id=thief_id, team_id=None, is_wanted=True, personal_reputation=0)
        ship = self._make_stolen_for_retract(owner=owner, thief_id=thief_id)
        db = _FakeSession(bounty_claim=None, thief=thief, still_piloting_stolen=False)

        retract_stolen_report(db, ship=ship, owner=owner)

        assert thief.is_wanted is False
        assert thief.wanted_declared_at is None

    def test_retract_keeps_is_wanted_if_low_reputation_still_applies(self):
        """OR-combination: retracting the stolen report clears the STOLEN
        trigger, but the thief stays Wanted if personal_reputation < -500
        independently qualifies (ranking.md's trigger union)."""
        owner = _owner()
        thief_id = uuid.uuid4()
        thief = Player(id=thief_id, team_id=None, is_wanted=True, personal_reputation=-600)
        ship = self._make_stolen_for_retract(owner=owner, thief_id=thief_id)
        db = _FakeSession(bounty_claim=None, thief=thief, still_piloting_stolen=False)

        retract_stolen_report(db, ship=ship, owner=owner)

        assert thief.is_wanted is True  # still Wanted via the reputation trigger

    @staticmethod
    def _make_stolen_for_retract(*, owner, thief_id):
        return _ship(
            owner=owner,
            pilot_id=thief_id,
            stolen_status=True,
            stolen_reported_at=datetime.now(timezone.utc),
            stolen_recovery_mode="no_bounty",
            stolen_thief_id=thief_id,
            stolen_bounty_ref=None,
        )
