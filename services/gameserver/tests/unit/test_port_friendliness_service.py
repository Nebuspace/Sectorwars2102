"""Unit tests — port_friendliness_service.py (the shared "friendly port"
reputation gate, DECISION insurance-factionless-port-gate 2026-08-07).

`test_fleet_resupply_friendly_station.py` exercises `check_friendly_port`
indirectly through `fleet_service`'s resupply orchestration, but no test
file targets this module's own resolution logic directly. Adds direct,
DB-free unit tests for `_player_reputation_level_for_faction`,
`_station_controlling_faction`, and `check_friendly_port` itself.

Sections:
  TestPlayerReputationLevelForFaction — None-faction -> NEUTRAL, missing
    Reputation row -> NEUTRAL, a null current_level -> NEUTRAL, and the
    real stored level passed through unchanged.
  TestStationControllingFaction — no faction_affiliation -> None, a name
    that doesn't resolve to a seeded Faction -> None, and a resolving name
    -> the matching Faction row.
  TestCheckFriendlyPort — the full gate: unaffiliated/unresolvable stations
    always pass, every reputation rank at-or-above NEUTRAL passes, every
    rank below NEUTRAL rejects with the ERR_UNFRIENDLY_PORT reason, and the
    exact boundary at NEUTRAL itself.
"""

from uuid import uuid4

import pytest

from src.models.faction import Faction
from src.models.reputation import Reputation, ReputationLevel
from src.models.station import Station
from src.services.port_friendliness_service import (
    _player_reputation_level_for_faction,
    _station_controlling_faction,
    check_friendly_port,
)


class _FakeQuery:
    def __init__(self, value):
        self._value = value

    def filter(self, *_args, **_kwargs):
        return self

    def first(self):
        return self._value


class _FakeDb:
    def __init__(self, results=None):
        self._queues = {k: list(v) for k, v in (results or {}).items()}

    def query(self, model):
        queue = self._queues.get(model, [])
        value = queue.pop(0) if queue else None
        return _FakeQuery(value)


def _faction(id=None, name="Terran Federation"):
    f = Faction()
    f.id = id or uuid4()
    f.name = name
    return f


def _station(faction_affiliation=None):
    s = Station()
    s.id = uuid4()
    s.faction_affiliation = faction_affiliation
    return s


def _reputation(current_level=ReputationLevel.NEUTRAL):
    r = Reputation()
    r.player_id = uuid4()
    r.faction_id = uuid4()
    r.current_level = current_level
    return r


class TestPlayerReputationLevelForFaction:
    def test_none_faction_is_neutral(self):
        db = _FakeDb()
        level = _player_reputation_level_for_faction(db, uuid4(), None)
        assert level == ReputationLevel.NEUTRAL

    def test_missing_reputation_row_is_neutral(self):
        faction = _faction()
        db = _FakeDb(results={Reputation: [None]})
        level = _player_reputation_level_for_faction(db, uuid4(), faction)
        assert level == ReputationLevel.NEUTRAL

    def test_null_current_level_is_neutral(self):
        faction = _faction()
        rep = _reputation()
        rep.current_level = None
        db = _FakeDb(results={Reputation: [rep]})
        level = _player_reputation_level_for_faction(db, uuid4(), faction)
        assert level == ReputationLevel.NEUTRAL

    def test_real_stored_level_passes_through(self):
        faction = _faction()
        rep = _reputation(current_level=ReputationLevel.EXALTED)
        db = _FakeDb(results={Reputation: [rep]})
        level = _player_reputation_level_for_faction(db, uuid4(), faction)
        assert level == ReputationLevel.EXALTED


class TestStationControllingFaction:
    def test_no_affiliation_is_none(self):
        station = _station(faction_affiliation=None)
        db = _FakeDb()
        assert _station_controlling_faction(db, station) is None

    def test_empty_string_affiliation_is_none(self):
        station = _station(faction_affiliation="")
        db = _FakeDb()
        assert _station_controlling_faction(db, station) is None

    def test_unresolvable_name_is_none(self):
        station = _station(faction_affiliation="Ghost Syndicate")
        db = _FakeDb(results={Faction: [None]})
        assert _station_controlling_faction(db, station) is None

    def test_resolving_name_returns_the_faction_row(self):
        faction = _faction(name="Terran Federation")
        station = _station(faction_affiliation="Terran Federation")
        db = _FakeDb(results={Faction: [faction]})
        resolved = _station_controlling_faction(db, station)
        assert resolved is faction


class TestCheckFriendlyPort:
    def test_unaffiliated_station_always_passes(self):
        station = _station(faction_affiliation=None)
        db = _FakeDb()
        ok, reason = check_friendly_port(db, uuid4(), station)
        assert ok is True
        assert reason is None

    def test_unresolvable_faction_name_always_passes(self):
        station = _station(faction_affiliation="Ghost Syndicate")
        db = _FakeDb(results={Faction: [None]})
        ok, reason = check_friendly_port(db, uuid4(), station)
        assert ok is True
        assert reason is None

    def test_neutral_reputation_passes(self):
        faction = _faction()
        station = _station(faction_affiliation=faction.name)
        rep = _reputation(current_level=ReputationLevel.NEUTRAL)
        db = _FakeDb(results={Faction: [faction], Reputation: [rep]})
        ok, reason = check_friendly_port(db, uuid4(), station)
        assert ok is True
        assert reason is None

    def test_above_neutral_reputation_passes(self):
        faction = _faction()
        station = _station(faction_affiliation=faction.name)
        rep = _reputation(current_level=ReputationLevel.EXALTED)
        db = _FakeDb(results={Faction: [faction], Reputation: [rep]})
        ok, reason = check_friendly_port(db, uuid4(), station)
        assert ok is True
        assert reason is None

    def test_no_reputation_row_defaults_to_neutral_and_passes(self):
        faction = _faction()
        station = _station(faction_affiliation=faction.name)
        db = _FakeDb(results={Faction: [faction], Reputation: [None]})
        ok, reason = check_friendly_port(db, uuid4(), station)
        assert ok is True
        assert reason is None

    def test_just_below_neutral_is_rejected(self):
        faction = _faction(name="Terran Federation")
        station = _station(faction_affiliation=faction.name)
        rep = _reputation(current_level=ReputationLevel.QUESTIONABLE)
        db = _FakeDb(results={Faction: [faction], Reputation: [rep]})
        ok, reason = check_friendly_port(db, uuid4(), station)
        assert ok is False
        assert reason == (
            "ERR_UNFRIENDLY_PORT: insurance requires at least NEUTRAL standing "
            "with Terran Federation (you are QUESTIONABLE)"
        )

    def test_lowest_reputation_is_rejected(self):
        faction = _faction()
        station = _station(faction_affiliation=faction.name)
        rep = _reputation(current_level=ReputationLevel.PUBLIC_ENEMY)
        db = _FakeDb(results={Faction: [faction], Reputation: [rep]})
        ok, reason = check_friendly_port(db, uuid4(), station)
        assert ok is False
        assert "PUBLIC_ENEMY" in reason

    @pytest.mark.parametrize(
        "level",
        [
            ReputationLevel.PUBLIC_ENEMY,
            ReputationLevel.CRIMINAL,
            ReputationLevel.OUTLAW,
            ReputationLevel.PIRATE,
            ReputationLevel.SMUGGLER,
            ReputationLevel.UNTRUSTWORTHY,
            ReputationLevel.SUSPICIOUS,
            ReputationLevel.QUESTIONABLE,
        ],
    )
    def test_every_below_neutral_rank_is_rejected(self, level):
        faction = _faction()
        station = _station(faction_affiliation=faction.name)
        rep = _reputation(current_level=level)
        db = _FakeDb(results={Faction: [faction], Reputation: [rep]})
        ok, _reason = check_friendly_port(db, uuid4(), station)
        assert ok is False

    @pytest.mark.parametrize(
        "level",
        [
            ReputationLevel.NEUTRAL,
            ReputationLevel.RECOGNIZED,
            ReputationLevel.ACKNOWLEDGED,
            ReputationLevel.TRUSTED,
            ReputationLevel.RESPECTED,
            ReputationLevel.VALUED,
            ReputationLevel.HONORED,
            ReputationLevel.REVERED,
            ReputationLevel.EXALTED,
        ],
    )
    def test_every_neutral_or_above_rank_passes(self, level):
        faction = _faction()
        station = _station(faction_affiliation=faction.name)
        rep = _reputation(current_level=level)
        db = _FakeDb(results={Faction: [faction], Reputation: [rep]})
        ok, reason = check_friendly_port(db, uuid4(), station)
        assert ok is True
        assert reason is None
