"""LEG-3194 — admin GET /colonization/production surfaces real commodity
stockpiles and per-planet tick warnings instead of synthetic charts."""
import asyncio
from types import SimpleNamespace
from uuid import uuid4

from src.api.routes.admin_colonization import (
    _alerts_from_planets,
    _stockpile_totals,
    get_colony_production,
)
from src.services.planetary_service import STARVATION_WARNING_KEY


def make_planet(
    *,
    name="Alpha",
    fuel_ore=0,
    organics=0,
    equipment=0,
    citadel_level=1,
    active_events=None,
):
    return SimpleNamespace(
        id=uuid4(),
        name=name,
        owner_id=uuid4(),
        colonized_at="2026-08-01T00:00:00Z",
        fuel_ore=fuel_ore,
        organics=organics,
        equipment=equipment,
        citadel_level=citadel_level,
        active_events=active_events or {},
    )


class _FakeQuery:
    def __init__(self, planets):
        self._planets = planets

    def filter(self, *args, **kwargs):
        return self

    def all(self):
        return self._planets


class _FakeSession:
    def __init__(self, planets):
        self._planets = planets

    def query(self, model):
        return _FakeQuery(self._planets)


def test_stockpile_totals_sums_real_columns():
    planets = [
        make_planet(fuel_ore=100, organics=50, equipment=25),
        make_planet(fuel_ore=200, organics=75, equipment=10),
    ]
    totals = _stockpile_totals(planets)
    assert totals == {"fuel_ore": 300, "organics": 125, "equipment": 35}


def test_alerts_from_overflow_and_starvation_warnings():
    planets = [
        make_planet(
            name="Overflow Prime",
            active_events={
                "overflow_warning": {
                    "resources": {"fuel_ore": 42},
                    "cap": 1000,
                    "at": "2026-08-31T10:00:00Z",
                }
            },
        ),
        make_planet(
            name="Hungry Rock",
            active_events={
                STARVATION_WARNING_KEY: {
                    "food_deficit": 31,
                    "colonists_lost": 62,
                    "at": "2026-08-31T09:00:00Z",
                }
            },
        ),
        make_planet(name="Healthy"),
    ]
    alerts = _alerts_from_planets(planets)
    assert len(alerts) == 2
    types = {a.type for a in alerts}
    assert types == {"overflow", "starvation"}
    colonies = {a.colony for a in alerts}
    assert colonies == {"Overflow Prime", "Hungry Rock"}


def test_get_colony_production_returns_real_stockpiles_and_empty_alerts():
    planets = [
        make_planet(name="Alpha", fuel_ore=500, organics=300, equipment=100),
        make_planet(name="Beta", fuel_ore=200, organics=50, equipment=25),
    ]
    db = _FakeSession(planets)
    result = asyncio.run(
        get_colony_production(
            timeRange="day",
            resource="all",
            current_admin=SimpleNamespace(),
            db=db,
        )
    )
    assert len(result["history"]) == 1
    assert result["history"][0]["fuel_ore"] == 700
    assert result["history"][0]["organics"] == 350
    assert result["history"][0]["equipment"] == 125
    assert result["alerts"] == []
    assert result["stats"]["totalProduction"]["fuel_ore"] == 700


def test_get_colony_production_surfaces_tick_warnings_in_alerts():
    planets = [
        make_planet(
            name="Overflow Prime",
            fuel_ore=1000,
            active_events={
                "overflow_warning": {
                    "resources": {"organics": 15},
                    "cap": 500,
                    "at": "2026-08-31T10:00:00Z",
                }
            },
        ),
    ]
    db = _FakeSession(planets)
    result = asyncio.run(
        get_colony_production(
            timeRange="day",
            resource="all",
            current_admin=SimpleNamespace(),
            db=db,
        )
    )
    assert len(result["alerts"]) == 1
    assert result["alerts"][0]["type"] == "overflow"
    assert result["alerts"][0]["colony"] == "Overflow Prime"
    assert result["stats"]["bottlenecks"][0]["issue"] == "Storage overflow — production wasted"
