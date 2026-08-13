"""ADR-0094 point-2: /grid/place is the canonical defense-construct surface.

combat_service._read_defense_buildings still reads active_events["defense_buildings"].
planet_grid._bump_ct1_defense keeps that CT1 store in sync with catalog kinds that
carry effect.kind == ct1_defense.
"""
from types import SimpleNamespace

from src.api.routes.planet_grid import _bump_ct1_defense, _ct1_kind
from src.services.building_catalog import BUILDING_CATALOG, assert_catalog_valid


def test_catalog_includes_planet_minefield_and_stays_valid():
    assert "PLANET_MINEFIELD" in BUILDING_CATALOG
    spec = BUILDING_CATALOG["PLANET_MINEFIELD"]
    assert spec["effect"]["ct1_kind"] == "planet_minefield"
    assert spec["cost"][1]["equipment"] == 10000
    assert_catalog_valid()


def test_ct1_kind_maps_defense_rows_and_ignores_economy():
    assert _ct1_kind("TURRET_NETWORK") == "turret_network"
    assert _ct1_kind("ORBITAL_PLATFORM") == "orbital_platform"
    assert _ct1_kind("SCANNER_ARRAY") == "scanner_array"
    assert _ct1_kind("RAIL_GUN") == "rail_gun"
    assert _ct1_kind("DEFENSE_GRID") == "planetary_defense_grid"
    assert _ct1_kind("PLANET_MINEFIELD") == "planet_minefield"
    assert _ct1_kind("MINE") is None
    assert _ct1_kind("not-a-kind") is None


def test_bump_ct1_defense_increments_and_decrements():
    planet = SimpleNamespace(active_events={})
    assert _bump_ct1_defense(planet, "TURRET_NETWORK", +1) is True
    assert planet.active_events["defense_buildings"]["turret_network"] == 1

    assert _bump_ct1_defense(planet, "TURRET_NETWORK", +1) is True
    assert planet.active_events["defense_buildings"]["turret_network"] == 2

    assert _bump_ct1_defense(planet, "PLANET_MINEFIELD", +1) is True
    assert planet.active_events["defense_buildings"]["planet_minefield"] == 1

    assert _bump_ct1_defense(planet, "TURRET_NETWORK", -1) is True
    assert planet.active_events["defense_buildings"]["turret_network"] == 1

    assert _bump_ct1_defense(planet, "TURRET_NETWORK", -1) is True
    assert "turret_network" not in planet.active_events["defense_buildings"]
    assert planet.active_events["defense_buildings"]["planet_minefield"] == 1


def test_bump_ct1_defense_noops_for_economy_and_zero_delta():
    planet = SimpleNamespace(active_events={"defense_buildings": {"scanner_array": 1}})
    assert _bump_ct1_defense(planet, "MINE", +1) is False
    assert _bump_ct1_defense(planet, "TURRET_NETWORK", 0) is False
    assert planet.active_events["defense_buildings"] == {"scanner_array": 1}


def test_defense_catalog_costs_include_sec_defbuild_materials():
    assert BUILDING_CATALOG["TURRET_NETWORK"]["cost"][1]["equipment"] == 8000
    assert BUILDING_CATALOG["SCANNER_ARRAY"]["cost"][1]["equipment"] == 10000
    assert BUILDING_CATALOG["RAIL_GUN"]["cost"][1]["fuel_ore"] == 20000
    assert BUILDING_CATALOG["RAIL_GUN"]["cost"][1]["equipment"] == 10000
    assert BUILDING_CATALOG["DEFENSE_GRID"]["cost"][1]["equipment"] == 15000
    assert "equipment" not in BUILDING_CATALOG["ORBITAL_PLATFORM"]["cost"][1]
