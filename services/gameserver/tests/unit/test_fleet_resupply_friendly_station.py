"""WO-FLEET-RESUPPLY-FRIENDLY-STATION: FleetService.resupply_fleet now
enforces the same "friendly port" reputation gate as ship insurance
(check_friendly_port, shared via src.services.port_friendliness_service)
before restoring supply.

DB-free, direct-service-call house pattern (mirrors
tests/unit/test_insurance_canon_gate.py's _FakeQuery/_FakeSession
convention, keyed by model class -- a query for an unspecified model
raises AssertionError, proving a faction-less station never touches
Faction/Reputation at all).

Covers:
  (a) friendly (NEUTRAL+) station -- regression, resupply still succeeds
      exactly as before.
  (b) faction-less/unaffiliated station -- same, always-friendly by
      design (insurance-factionless-port-gate, reused unmodified).
  (c) hostile (below-NEUTRAL) station -- NEW: rejected before any credits
      move or supply_level mutates.
  (d) "not docked" rejection is unchanged (fires before the friendliness
      check is ever reached, so it never touches Faction/Reputation).
"""
from __future__ import annotations

import uuid
from typing import Any, Dict

import pytest

from src.models.faction import Faction, FactionType
from src.models.fleet import Fleet, FleetMember, FleetStatus
from src.models.player import Player
from src.models.reputation import Reputation, ReputationLevel
from src.models.station import Station, StationClass, StationType
from src.services.fleet_service import FleetService

# ---------------------------------------------------------------------------
# Fixture builders -- real (unpersisted) ORM instances, never flushed.
# ---------------------------------------------------------------------------


def _fleet(*, id=None, status=FleetStatus.READY.value, sector_id=None,
           supply_level=50) -> Fleet:
    return Fleet(
        id=id or uuid.uuid4(),
        name="Test Fleet",
        status=status,
        sector_id=sector_id,
        supply_level=supply_level,
    )


def _member(*, fleet_id, player_id) -> FleetMember:
    return FleetMember(id=uuid.uuid4(), fleet_id=fleet_id, player_id=player_id)


def _player(*, id=None, credits=100_000, is_docked=True, current_port_id=None,
            current_sector_id=1) -> Player:
    return Player(
        id=id or uuid.uuid4(),
        credits=credits,
        is_docked=is_docked,
        current_port_id=current_port_id,
        current_sector_id=current_sector_id,
    )


def _station(*, id=None, sector_uuid=None, faction_affiliation=None,
             station_class=StationClass.CLASS_2) -> Station:
    return Station(
        id=id or uuid.uuid4(),
        name="Test Station",
        sector_id=1,
        sector_uuid=sector_uuid,
        type=StationType.TRADING,
        station_class=station_class,
        faction_affiliation=faction_affiliation,
    )


def _faction(*, id=None, name="Terran Federation") -> Faction:
    return Faction(id=id or uuid.uuid4(), name=name, faction_type=FactionType.FEDERATION)


def _reputation(*, player_id, faction_id, level: ReputationLevel) -> Reputation:
    return Reputation(player_id=player_id, faction_id=faction_id, current_level=level)


class _FakeQuery:
    def __init__(self, *, first: Any = None) -> None:
        self._first = first

    def filter(self, *a: Any, **k: Any) -> "_FakeQuery":
        return self

    def populate_existing(self, *a: Any, **k: Any) -> "_FakeQuery":
        return self

    def with_for_update(self, *a: Any, **k: Any) -> "_FakeQuery":
        return self

    def first(self) -> Any:
        return self._first


class _FakeSession:
    """Keyed by model class. A query for an unspecified model raises
    AssertionError -- proves e.g. a faction-less station never queries
    Faction/Reputation, and the "not docked" rejection never queries
    Station/Faction/Reputation at all."""

    def __init__(self, specs: Dict[type, _FakeQuery]) -> None:
        self._specs = specs
        self.commit_calls = 0
        self.refresh_calls = 0

    def query(self, target: Any) -> _FakeQuery:
        key = target if isinstance(target, type) else target.class_
        assert key in self._specs, f"unexpected query for {target!r}"
        return self._specs[key]

    def commit(self) -> None:
        self.commit_calls += 1

    def refresh(self, obj: Any) -> None:
        self.refresh_calls += 1

    def rollback(self) -> None:
        pass


def _make_session(*, fleet, member, player, station, faction=None, reputation=None):
    specs: Dict[type, _FakeQuery] = {
        Fleet: _FakeQuery(first=fleet),
        FleetMember: _FakeQuery(first=member),
        Player: _FakeQuery(first=player),
    }
    if station is not None:
        specs[Station] = _FakeQuery(first=station)
    if faction is not None or Station in specs:
        # check_friendly_port always queries Faction once the "not docked"
        # gate is passed (a faction-less station still queries Faction,
        # gets None back, and short-circuits to friendly).
        specs[Faction] = _FakeQuery(first=faction)
    if reputation is not None or faction is not None:
        specs[Reputation] = _FakeQuery(first=reputation)
    return _FakeSession(specs)


# ---------------------------------------------------------------------------
# (a) friendly (NEUTRAL+) station -- regression
# ---------------------------------------------------------------------------


class TestResupplyFriendlyStation:
    def test_resupply_succeeds_at_neutral_reputation_station(self):
        sector_id = uuid.uuid4()
        player_id = uuid.uuid4()
        fleet = _fleet(sector_id=sector_id, supply_level=40)
        station = _station(sector_uuid=sector_id, faction_affiliation="Terran Federation")
        faction = _faction(name="Terran Federation")
        player = _player(id=player_id, current_port_id=station.id)
        member = _member(fleet_id=fleet.id, player_id=player_id)
        rep = _reputation(player_id=player_id, faction_id=faction.id,
                           level=ReputationLevel.NEUTRAL)

        db = _make_session(fleet=fleet, member=member, player=player,
                            station=station, faction=faction, reputation=rep)
        result = FleetService(db).resupply_fleet(fleet.id, player_id)

        assert result["supply_restored"] > 0
        assert fleet.supply_level > 40
        assert db.commit_calls == 1

    def test_resupply_succeeds_above_neutral_reputation_station(self):
        """Regression: TRUSTED (well above NEUTRAL) still passes."""
        sector_id = uuid.uuid4()
        player_id = uuid.uuid4()
        fleet = _fleet(sector_id=sector_id, supply_level=40)
        station = _station(sector_uuid=sector_id, faction_affiliation="Terran Federation")
        faction = _faction(name="Terran Federation")
        player = _player(id=player_id, current_port_id=station.id)
        member = _member(fleet_id=fleet.id, player_id=player_id)
        rep = _reputation(player_id=player_id, faction_id=faction.id,
                           level=ReputationLevel.TRUSTED)

        db = _make_session(fleet=fleet, member=member, player=player,
                            station=station, faction=faction, reputation=rep)
        result = FleetService(db).resupply_fleet(fleet.id, player_id)

        assert result["supply_restored"] > 0
        assert db.commit_calls == 1


# ---------------------------------------------------------------------------
# (b) faction-less/unaffiliated station -- always friendly
# ---------------------------------------------------------------------------


class TestResupplyFactionlessStation:
    def test_resupply_succeeds_at_unaffiliated_station(self):
        sector_id = uuid.uuid4()
        player_id = uuid.uuid4()
        fleet = _fleet(sector_id=sector_id, supply_level=40)
        station = _station(sector_uuid=sector_id, faction_affiliation=None)
        player = _player(id=player_id, current_port_id=station.id)
        member = _member(fleet_id=fleet.id, player_id=player_id)

        # No faction_affiliation -> _station_controlling_faction short-
        # circuits on `if not faction_name: return None` and never queries
        # Faction/Reputation at all.
        db = _make_session(fleet=fleet, member=member, player=player, station=station)
        db._specs.pop(Faction, None)
        db._specs.pop(Reputation, None)

        result = FleetService(db).resupply_fleet(fleet.id, player_id)

        assert result["supply_restored"] > 0
        assert db.commit_calls == 1

    def test_resupply_succeeds_when_faction_name_does_not_resolve(self):
        """A faction_affiliation naming a faction with no seeded Faction row
        also resolves to "no controlling faction" -> always friendly."""
        sector_id = uuid.uuid4()
        player_id = uuid.uuid4()
        fleet = _fleet(sector_id=sector_id, supply_level=40)
        station = _station(sector_uuid=sector_id, faction_affiliation="Ghost Faction")
        player = _player(id=player_id, current_port_id=station.id)
        member = _member(fleet_id=fleet.id, player_id=player_id)

        db = _make_session(fleet=fleet, member=member, player=player,
                            station=station, faction=None)
        db._specs.pop(Reputation, None)

        result = FleetService(db).resupply_fleet(fleet.id, player_id)

        assert result["supply_restored"] > 0
        assert db.commit_calls == 1


# ---------------------------------------------------------------------------
# (c) hostile (below-NEUTRAL) station -- NEW rejection
# ---------------------------------------------------------------------------


class TestResupplyHostileStationRejected:
    @pytest.mark.parametrize("level", [
        ReputationLevel.PUBLIC_ENEMY,
        ReputationLevel.CRIMINAL,
        ReputationLevel.SUSPICIOUS,
    ])
    def test_resupply_rejected_below_neutral(self, level):
        sector_id = uuid.uuid4()
        player_id = uuid.uuid4()
        fleet = _fleet(sector_id=sector_id, supply_level=40)
        station = _station(sector_uuid=sector_id, faction_affiliation="Terran Federation")
        faction = _faction(name="Terran Federation")
        player = _player(id=player_id, current_port_id=station.id, credits=100_000)
        member = _member(fleet_id=fleet.id, player_id=player_id)
        rep = _reputation(player_id=player_id, faction_id=faction.id, level=level)

        db = _make_session(fleet=fleet, member=member, player=player,
                            station=station, faction=faction, reputation=rep)

        with pytest.raises(ValueError, match="hostile station"):
            FleetService(db).resupply_fleet(fleet.id, player_id)

        # Rejected before ANY state mutates: no charge, no supply change, no commit.
        assert fleet.supply_level == 40
        assert player.credits == 100_000
        assert db.commit_calls == 0


# ---------------------------------------------------------------------------
# (d) "not docked" rejection is unchanged, and never reaches the
#     friendliness check (no Station/Faction/Reputation query at all).
# ---------------------------------------------------------------------------


class TestResupplyNotDockedUnchanged:
    def test_resupply_rejected_when_not_docked(self):
        player_id = uuid.uuid4()
        fleet = _fleet(supply_level=40)
        member = _member(fleet_id=fleet.id, player_id=player_id)
        player = _player(id=player_id, is_docked=False, current_port_id=None)

        db = _FakeSession({
            Fleet: _FakeQuery(first=fleet),
            FleetMember: _FakeQuery(first=member),
            Player: _FakeQuery(first=player),
            # deliberately no Station/Faction/Reputation entries: any query
            # against them raises AssertionError, proving the friendliness
            # gate is never reached for a not-docked player.
        })

        with pytest.raises(ValueError, match="must be docked"):
            FleetService(db).resupply_fleet(fleet.id, player_id)

        assert fleet.supply_level == 40
        assert db.commit_calls == 0
