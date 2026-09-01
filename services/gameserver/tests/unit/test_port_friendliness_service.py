"""Unit tests — port_friendliness_service.py (the shared "friendly port"
reputation gate, DECISION insurance-factionless-port-gate 2026-08-07).

`test_fleet_resupply_friendly_station.py` exercises `check_friendly_port`
indirectly through `fleet_service`'s resupply orchestration, but no test
file targets this module's own resolution logic directly. Adds direct,
DB-free unit tests for `_player_reputation_level_for_faction`,
`_station_controlling_faction`, and `check_friendly_port` itself.

Team standing (LEG-1908 / LEG-1907 shared helper): level comes from
``resolve_effective_faction_standing_value`` mapped through FactionService
thresholds — teamed players honor team AVERAGE; factionless always-pass
unchanged.
"""

from uuid import uuid4

import pytest

from src.models.faction import Faction
from src.models.player import Player
from src.models.reputation import Reputation, ReputationLevel
from src.models.station import Station
from src.services.port_friendliness_service import (
    _player_reputation_level_for_faction,
    _station_controlling_faction,
    check_friendly_port,
)

# Representative numeric values inside each ReputationLevel band
# (FactionService._calculate_reputation_level thresholds).
_LEVEL_VALUE = {
    ReputationLevel.PUBLIC_ENEMY: -750,
    ReputationLevel.CRIMINAL: -650,
    ReputationLevel.OUTLAW: -550,
    ReputationLevel.PIRATE: -450,
    ReputationLevel.SMUGGLER: -350,
    ReputationLevel.UNTRUSTWORTHY: -250,
    ReputationLevel.SUSPICIOUS: -150,
    ReputationLevel.QUESTIONABLE: -75,
    ReputationLevel.NEUTRAL: 0,
    ReputationLevel.RECOGNIZED: 50,
    ReputationLevel.ACKNOWLEDGED: 100,
    ReputationLevel.TRUSTED: 200,
    ReputationLevel.RESPECTED: 300,
    ReputationLevel.VALUED: 400,
    ReputationLevel.HONORED: 500,
    ReputationLevel.REVERED: 600,
    ReputationLevel.EXALTED: 700,
}


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


def _reputation(current_level=ReputationLevel.NEUTRAL, current_value=None):
    r = Reputation()
    r.player_id = uuid4()
    r.faction_id = uuid4()
    r.current_level = current_level
    r.current_value = (
        _LEVEL_VALUE[current_level] if current_value is None else current_value
    )
    return r


def _solo_db(*, faction=None, rep=None):
    """Player → None team; Reputation row for personal resolve path."""
    queues = {Player: [None], Reputation: [rep]}
    if faction is not None:
        queues[Faction] = [faction]
    return _FakeDb(results=queues)


class TestPlayerReputationLevelForFaction:
    def test_none_faction_is_neutral(self):
        db = _FakeDb()
        level = _player_reputation_level_for_faction(db, uuid4(), None)
        assert level == ReputationLevel.NEUTRAL

    def test_missing_reputation_row_is_neutral(self):
        faction = _faction()
        db = _solo_db(rep=None)
        level = _player_reputation_level_for_faction(db, uuid4(), faction)
        assert level == ReputationLevel.NEUTRAL

    def test_zero_value_maps_to_neutral(self):
        faction = _faction()
        rep = _reputation(current_level=ReputationLevel.NEUTRAL, current_value=0)
        db = _solo_db(rep=rep)
        level = _player_reputation_level_for_faction(db, uuid4(), faction)
        assert level == ReputationLevel.NEUTRAL

    def test_exalted_value_maps_to_exalted(self):
        faction = _faction()
        rep = _reputation(current_level=ReputationLevel.EXALTED)
        db = _solo_db(rep=rep)
        level = _player_reputation_level_for_faction(db, uuid4(), faction)
        assert level == ReputationLevel.EXALTED

    def test_teamed_player_uses_team_aggregate_level(self, monkeypatch):
        faction = _faction()
        player_id = uuid4()

        def _resolve(db, pid, fid, *, team_id=None):
            assert pid == player_id
            assert fid == faction.id
            return 700, "team"

        monkeypatch.setattr(
            "src.services.faction_service.resolve_effective_faction_standing_value",
            _resolve,
        )
        level = _player_reputation_level_for_faction(_FakeDb(), player_id, faction)
        assert level == ReputationLevel.EXALTED

    def test_teamed_player_below_neutral_maps_questionable(self, monkeypatch):
        faction = _faction()
        monkeypatch.setattr(
            "src.services.faction_service.resolve_effective_faction_standing_value",
            lambda *_a, **_k: (-75, "team"),
        )
        level = _player_reputation_level_for_faction(_FakeDb(), uuid4(), faction)
        assert level == ReputationLevel.QUESTIONABLE


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
        db = _FakeDb(results={Faction: [faction], Player: [None], Reputation: [rep]})
        ok, reason = check_friendly_port(db, uuid4(), station)
        assert ok is True
        assert reason is None

    def test_above_neutral_reputation_passes(self):
        faction = _faction()
        station = _station(faction_affiliation=faction.name)
        rep = _reputation(current_level=ReputationLevel.EXALTED)
        db = _FakeDb(results={Faction: [faction], Player: [None], Reputation: [rep]})
        ok, reason = check_friendly_port(db, uuid4(), station)
        assert ok is True
        assert reason is None

    def test_no_reputation_row_defaults_to_neutral_and_passes(self):
        faction = _faction()
        station = _station(faction_affiliation=faction.name)
        db = _FakeDb(results={Faction: [faction], Player: [None], Reputation: [None]})
        ok, reason = check_friendly_port(db, uuid4(), station)
        assert ok is True
        assert reason is None

    def test_just_below_neutral_is_rejected(self):
        faction = _faction(name="Terran Federation")
        station = _station(faction_affiliation=faction.name)
        rep = _reputation(current_level=ReputationLevel.QUESTIONABLE)
        db = _FakeDb(results={Faction: [faction], Player: [None], Reputation: [rep]})
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
        db = _FakeDb(results={Faction: [faction], Player: [None], Reputation: [rep]})
        ok, reason = check_friendly_port(db, uuid4(), station)
        assert ok is False
        assert "PUBLIC_ENEMY" in reason

    def test_teamed_player_passes_via_team_aggregate(self, monkeypatch):
        faction = _faction(name="Terran Federation")
        station = _station(faction_affiliation=faction.name)
        monkeypatch.setattr(
            "src.services.faction_service.resolve_effective_faction_standing_value",
            lambda *_a, **_k: (0, "team"),
        )
        db = _FakeDb(results={Faction: [faction]})
        ok, reason = check_friendly_port(db, uuid4(), station)
        assert ok is True
        assert reason is None

    def test_teamed_player_denied_when_team_below_neutral(self, monkeypatch):
        faction = _faction(name="Terran Federation")
        station = _station(faction_affiliation=faction.name)
        monkeypatch.setattr(
            "src.services.faction_service.resolve_effective_faction_standing_value",
            lambda *_a, **_k: (-75, "team"),
        )
        db = _FakeDb(results={Faction: [faction]})
        ok, reason = check_friendly_port(db, uuid4(), station)
        assert ok is False
        assert "QUESTIONABLE" in reason

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
        db = _FakeDb(results={Faction: [faction], Player: [None], Reputation: [rep]})
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
        db = _FakeDb(results={Faction: [faction], Player: [None], Reputation: [rep]})
        ok, reason = check_friendly_port(db, uuid4(), station)
        assert ok is True
        assert reason is None
