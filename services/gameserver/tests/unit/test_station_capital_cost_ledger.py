"""WO-BUILD-STATION-ACQUISITION-COST-CAPITAL-LEDGER.

ADR-0050 "Station relocation paths" -- 30% of (acquisition cost + sum of
upgrade capital costs). acquisition_cost was already tracked in
station.ownership['acquisition_cost'] (port_ownership_service._acquisition_
cost); this WO adds the missing half -- a per-upgrade capital-cost ledger --
and a relocation_fee() formula helper that reads both. The region-
termination-cascade dispatch that would actually charge/debit this fee stays
a separately-deferred discovery-only stub, out of this WO's scope.

DB-free, mirrors test_station_security_ladder.py's fixture pattern: a real
(transient) Station() ORM instance with committed_state reset, and a
_FakeSession for the upgrade_security_tier ledger-wiring test.
"""

import uuid
from datetime import datetime, UTC
from types import SimpleNamespace

from sqlalchemy import inspect as sa_inspect

from src.core import game_time
from src.models.player import Player
from src.models.station import Station
from src.services import port_ownership_service as po
from src.services import station_security_service as sts

FIXED_NOW = datetime(2026, 8, 4, 12, 0, 0, tzinfo=UTC)


def _fresh_committed_station(*, owner_id=None, security=None, ownership=None,
                              capital_cost_ledger=None, price_modifiers=None):
    station = Station()
    station.id = uuid.uuid4()
    station.name = "Test Station"
    station.owner_id = owner_id
    station.security = security
    station.ownership = ownership
    station.capital_cost_ledger = capital_cost_ledger
    station.price_modifiers = price_modifiers if price_modifiers is not None else {}
    station.tax_rate = 0.10
    insp = sa_inspect(station)
    insp.committed_state.clear()
    insp._commit_all(insp.dict)
    return station


def _fake_player(**overrides):
    base = dict(id=uuid.uuid4(), credits=1_000_000)
    base.update(overrides)
    return SimpleNamespace(**base)


class _FakeQuery:
    def __init__(self, result):
        self._result = result

    def filter(self, *a, **k):
        return self

    def populate_existing(self):
        return self

    def with_for_update(self, *a, **k):
        return self

    def first(self):
        return self._result


class _FakeSession:
    def __init__(self, *, station=None, player=None):
        self._station = station
        self._player = player
        self.flush_calls = 0

    def query(self, model):
        if model is Station:
            return _FakeQuery(self._station)
        if model is Player:
            return _FakeQuery(self._player)
        raise AssertionError(f"unexpected query for {model!r}")

    def flush(self):
        self.flush_calls += 1

    def commit(self):
        raise AssertionError("service functions are flush-only -- the route commits")

    def rollback(self):
        pass


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------

class TestAppendCapitalCost:
    def test_appends_entry_with_source_amount_and_timestamp(self):
        station = _fresh_committed_station(capital_cost_ledger=[])
        po.append_capital_cost(station, source="security_upgrade:basic", amount=50_000, now=FIXED_NOW)
        assert station.capital_cost_ledger == [
            {"source": "security_upgrade:basic", "amount": 50_000, "at": FIXED_NOW.isoformat()}
        ]

    def test_appends_to_existing_ledger_not_overwrite(self):
        station = _fresh_committed_station(
            capital_cost_ledger=[{"source": "seed", "amount": 1_000, "at": "2026-01-01T00:00:00+00:00"}]
        )
        po.append_capital_cost(station, source="security_upgrade:standard", amount=200_000, now=FIXED_NOW)
        assert len(station.capital_cost_ledger) == 2
        assert station.capital_cost_ledger[0]["source"] == "seed"
        assert station.capital_cost_ledger[1]["amount"] == 200_000

    def test_null_ledger_column_treated_as_empty(self):
        station = _fresh_committed_station(capital_cost_ledger=None)
        po.append_capital_cost(station, source="security_upgrade:basic", amount=50_000, now=FIXED_NOW)
        assert station.capital_cost_ledger == [
            {"source": "security_upgrade:basic", "amount": 50_000, "at": FIXED_NOW.isoformat()}
        ]

    def test_non_positive_amount_is_a_noop(self):
        station = _fresh_committed_station(capital_cost_ledger=[])
        po.append_capital_cost(station, source="x", amount=0, now=FIXED_NOW)
        po.append_capital_cost(station, source="x", amount=-5, now=FIXED_NOW)
        assert station.capital_cost_ledger == []


class TestTotalCapitalCost:
    def test_sums_all_entries(self):
        station = _fresh_committed_station(capital_cost_ledger=[
            {"source": "a", "amount": 50_000, "at": "x"},
            {"source": "b", "amount": 200_000, "at": "y"},
        ])
        assert po.total_capital_cost(station) == 250_000

    def test_null_ledger_sums_to_zero(self):
        station = _fresh_committed_station(capital_cost_ledger=None)
        assert po.total_capital_cost(station) == 0

    def test_empty_ledger_sums_to_zero(self):
        station = _fresh_committed_station(capital_cost_ledger=[])
        assert po.total_capital_cost(station) == 0


class TestRelocationFee:
    def test_thirty_percent_of_acquisition_plus_capital(self):
        station = _fresh_committed_station(
            ownership={"acquisition_cost": 500_000},
            capital_cost_ledger=[
                {"source": "security_upgrade:basic", "amount": 50_000, "at": "x"},
                {"source": "security_upgrade:standard", "amount": 200_000, "at": "y"},
            ],
        )
        # 30% of (500,000 + 250,000) = 225,000
        assert po.relocation_fee(station) == 225_000

    def test_zero_capital_cost_still_thirty_percent_of_acquisition(self):
        station = _fresh_committed_station(
            ownership={"acquisition_cost": 500_000}, capital_cost_ledger=[]
        )
        assert po.relocation_fee(station) == 150_000

    def test_legacy_station_falls_back_to_acquisition_requirements(self):
        station = _fresh_committed_station(ownership=None, capital_cost_ledger=[])
        station.acquisition_requirements = {"base_price": 500_000}
        assert po.relocation_fee(station) == 150_000


# ---------------------------------------------------------------------------
# Wiring: upgrade_security_tier ledgers its own cost
# ---------------------------------------------------------------------------

class TestSecurityUpgradeLedgersCapitalCost:
    def test_upgrade_appends_ledger_entry_for_exact_cost(self, monkeypatch):
        monkeypatch.setattr(game_time, "GAME_TIME_SCALE", 1.0)
        owner = _fake_player(credits=1_000_000)
        station = _fresh_committed_station(owner_id=owner.id, security=None, capital_cost_ledger=[])
        session = _FakeSession(station=station, player=owner)

        sts.upgrade_security_tier(session, station, owner, now=FIXED_NOW)

        assert len(station.capital_cost_ledger) == 1
        entry = station.capital_cost_ledger[0]
        assert entry["source"] == "security_upgrade:basic"
        assert entry["amount"] == 50_000
        assert entry["at"] == FIXED_NOW.isoformat()
        assert session.flush_calls == 1  # still a single flush -- no extra flush added

    def test_two_sequential_upgrades_both_ledgered(self, monkeypatch):
        monkeypatch.setattr(game_time, "GAME_TIME_SCALE", 1.0)
        owner = _fake_player(credits=2_000_000)
        station = _fresh_committed_station(owner_id=owner.id, security=None, capital_cost_ledger=[])
        session = _FakeSession(station=station, player=owner)

        sts.upgrade_security_tier(session, station, owner, now=FIXED_NOW)
        # Settle the pending upgrade so the next upgrade call is valid.
        station.security["tier"] = "basic"
        station.security["upgrade_to"] = None
        station.security["upgrade_completes_at"] = None
        sts.upgrade_security_tier(session, station, owner, now=FIXED_NOW)

        assert po.total_capital_cost(station) == 50_000 + 200_000
