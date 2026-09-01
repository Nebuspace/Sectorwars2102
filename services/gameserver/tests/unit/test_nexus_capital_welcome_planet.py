"""LEG-297 — Gateway Plaza Capital welcome planet + Pioneer Office hub flag.

DB-free: row builder + source-order pins (mirrors test_nexus_tradedock_seed
TestGatewayPlazaCapitalStationWiring). Pioneer Office has no dedicated table;
pioneer.py treats is_population_hub (or population >= 1e6) as the venue.
"""
from __future__ import annotations

import inspect
from types import SimpleNamespace

from src.models.planet import PlanetStatus, PlanetType
from src.services.nexus_generation_service import NexusGenerationService


class TestCapitalWelcomePlanetRow:
    def test_terra_type_population_hub_and_canon_max_pop(self):
        row = NexusGenerationService._build_capital_welcome_planet_row(
            sector_id=2551, region_id="nexus-region"
        )
        assert row["name"] == "Terra Nova Prime"
        assert row["sector_id"] == 2551
        assert row["type"] == PlanetType.TERRAN
        assert row["status"] == PlanetStatus.HABITABLE
        assert row["is_population_hub"] is True
        assert row["habitability_score"] == 100
        assert row["max_population"] == 100 * 1000

    def test_dead_sector_1_branch_reuses_welcome_row_builder(self):
        source = inspect.getsource(NexusGenerationService._generate_planet_for_sector)
        assert "self._build_capital_welcome_planet_row(" in source

    def test_pioneer_office_venue_is_hub_flag_not_new_table(self):
        row = NexusGenerationService._build_capital_welcome_planet_row(
            sector_id=2551, region_id="r"
        )
        planet = SimpleNamespace(
            is_population_hub=row["is_population_hub"],
            population=0,
        )
        is_hub = bool(planet.is_population_hub) or (planet.population or 0) >= 1_000_000
        assert is_hub is True


class TestGatewayPlazaCapitalPlanetWiring:
    def test_generate_central_nexus_seeds_planet_after_station_before_market(self):
        source = inspect.getsource(NexusGenerationService.generate_central_nexus)
        station_idx = source.index("self._seed_nexus_capital_station(")
        planet_idx = source.index("self._seed_nexus_capital_planet(")
        market_idx = source.index("self._create_market_prices_for_nexus_stations(")
        assert station_idx < planet_idx < market_idx

    def test_seed_inserts_welcome_row(self):
        source = inspect.getsource(NexusGenerationService._seed_nexus_capital_planet)
        assert "self._build_capital_welcome_planet_row(" in source
        assert "insert(Planet)" in source
        assert "is_population_hub" in source
