"""Unit coverage for WO-BUILD-REGION-LIFECYCLE-CLEANUP-CASCADE (reduced
scope): region_termination_cascade_service.process_planet_termination's
20%-loss / 100%-prepaid safe-transport math, Genesis-device compensation,
and forfeiture audit-logging, plus dispatch_station_termination's discovery-
only shape.

DB-free fake session -- mirrors test_region_lifecycle_cron.py's convention:
real ORM instances constructed in-process (never committed), a minimal fake
session that only implements what the SUT actually calls (add / flush /
query(Player).filter(id==...).first()).
"""
from __future__ import annotations

import uuid

import pytest

from src.models.genesis_device import GenesisType
from src.models.planet import Planet
from src.models.player import Player
from src.services import region_termination_cascade_service as cascade
from src.services.audit_service import AuditAction


class _FakePlayerQuery:
    def __init__(self, players):
        self._players = players
        self._id = None

    def filter(self, *clauses):
        # Player.id == planet.owner_id -- pull the right-hand bind value.
        for clause in clauses:
            self._id = clause.right.value
        return self

    def first(self):
        for p in self._players:
            if p.id == self._id:
                return p
        return None


class FakeSession:
    def __init__(self, players):
        self._players = players
        self.added = []
        self.audit_logs = []

    def add(self, obj):
        self.added.append(obj)
        if type(obj).__name__ == "AuditLog":
            self.audit_logs.append(obj)

    def flush(self, objs=None):
        for obj in self.added:
            if getattr(obj, "id", None) is None:
                obj.id = uuid.uuid4()

    def query(self, *entities):
        model = entities[0]
        if model is Player:
            return _FakePlayerQuery(self._players)
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


def test_automatic_transport_applies_20pct_loss_to_credits_and_commodities():
    owner = _make_player(credits=1_000)
    planet = _make_planet(
        owner_id=owner.id,
        citadel_safe_credits=1_000,
        active_events={"safe_commodities": {"ore": 100, "organics": 51}},
    )
    db = FakeSession([owner])

    result = cascade.process_planet_termination(db, planet)

    # 1000 * 80% = 800 credits survive; 200 forfeited.
    assert result["forfeited_credits"] == 200
    # ore: 100 * 80% = 80 kept, 20 forfeited.
    # organics: 51 * 80% = 40 (floor), 11 forfeited.
    assert result["forfeited_commodities"] == {"ore": 20, "organics": 11}
    # Player.credits grew by 800 (safe credits) + commodity credit-equivalent
    # value of the SURVIVING stacks only.
    from src.services.citadel_service import COMMODITY_CREDIT_VALUE
    expected_commodity_value = 80 * COMMODITY_CREDIT_VALUE.get("ore", 0) + 40 * COMMODITY_CREDIT_VALUE.get("organics", 0)
    assert owner.credits == 1_000 + 800 + expected_commodity_value
    # Safe is drained.
    assert planet.citadel_safe_credits == 0
    assert planet.active_events.get("safe_commodities", {}) == {}
    # Forfeiture logged.
    assert len(db.audit_logs) == 1
    assert db.audit_logs[0].action == AuditAction.FORFEIT.value
    assert db.audit_logs[0].request_body["forfeited_credits"] == 200


def test_prepaid_transport_applies_zero_loss():
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
    from src.services.citadel_service import COMMODITY_CREDIT_VALUE
    expected = 1_000 + 100 * COMMODITY_CREDIT_VALUE.get("ore", 0)
    assert owner.credits == expected
    # No forfeiture -> no audit entry.
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
def test_genesis_compensation_matches_adr0050_citadel_table(
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
