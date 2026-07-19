"""Pins colonist food consumption + starvation wired into
PlanetaryService.apply_resource_production (WO-P5, planetary_service.py:~880).

CANON (SYSTEMS/planetary-production-tick.md:123-134, "Colonist consumption"):
    food_consumed = colonists * 0.5 * (elapsed_minutes / 1440)
    If organics_on_hand < food_consumed:
        food_deficit = food_consumed - organics_on_hand
        organics_on_hand = 0
        colonists -= ceil(food_deficit * 2)

Clock domain: apply_resource_production's inner anchor (planet.last_production)
is WALL-CLOCK (structures.py's clock-domain table), so every test below drives
elapsed via a backdated `last_production` and reads the SAME capped_elapsed
derivation the production code already uses -- no GAME_TIME_SCALE involved.

DB-free house pattern (matches test_production_efficiency.py /
test_colony_cap_taper.py): PlanetaryService(db=None) is safe because
owner_id=None skips the only self.db read (the Overclock lookup, gated behind
`if planet.owner_id is not None`); surplus_rate is also gated on owner_id, so
it stays 0 here and never perturbs the "did anything happen" gate.
"""
from datetime import datetime, timedelta, UTC
from types import SimpleNamespace
from uuid import uuid4

from src.services.planetary_service import PlanetaryService

BASE_HABITABILITY = 100  # >= LOW_HABITABILITY_THRESHOLD, keeps the low-hab penalty out


def make_planet(
    *,
    colonists,
    organics,
    elapsed_hours,
    fuel_allocation=0,
    organics_allocation=0,
    equipment_allocation=0,
    factory_level=0,
    farm_level=0,
    mine_level=0,
    research_level=0,
    population=None,
    active_events=None,
):
    now = datetime.now(UTC)
    return SimpleNamespace(
        id=uuid4(),
        factory_level=factory_level, farm_level=farm_level, mine_level=mine_level,
        fuel_allocation=fuel_allocation, organics_allocation=organics_allocation,
        equipment_allocation=equipment_allocation,
        type=None,
        research_level=research_level,
        specialization=None,
        citadel_level=0,  # uncapped storage -- keeps overflow out of this test's numbers
        owner_id=None,  # DB-free: skips Overclock lookup AND zeroes surplus_rate
        active_events=active_events if active_events is not None else {},
        under_siege=False,
        habitability_score=BASE_HABITABILITY,
        colonists=colonists,
        population=population if population is not None else colonists,
        max_population=0,
        max_colonists=1000,
        production_efficiency=1.0,
        organics=organics,
        fuel_ore=0,
        equipment=0,
        last_production=now - timedelta(hours=elapsed_hours),
    )


def _run(planet):
    svc = PlanetaryService(db=None)
    changed = svc.apply_resource_production(planet, _via_settle=True)
    return changed


# --------------------------------------------------------------------------- #
# (1) A backdated colony whose pre-tick organics can't cover consumption
#     starves: loses ceil(deficit * 2) colonists and records starvation_warning.
# --------------------------------------------------------------------------- #

def test_starvation_kills_ceil_deficit_times_two_and_records_warning():
    # colonists=1000, elapsed=2h -> food_consumed = 1000*0.5*(120/1440) = 41.666..
    # -> whole-unit consumption = 41. organics on hand = 10 -> deficit = 31.
    # starvation_deaths = ceil(31*2) = 62.
    planet = make_planet(colonists=1000, organics=10, elapsed_hours=2)

    changed = _run(planet)

    assert changed is True
    assert planet.colonists == 1000 - 62, "expected ceil(deficit*2) = 62 colonist deaths"
    assert planet.population == 1000 - 62, "population must mirror the colonist loss"
    assert planet.organics == 0, "CANON invariant 8: starvation floors organics at 0, never negative"

    warning = planet.active_events.get("starvation_warning")
    assert warning is not None, "a food deficit must record planet.starvation_warning"
    assert warning["food_deficit"] == 31
    assert warning["colonists_lost"] == 62
    assert "at" in warning


def test_starvation_deaths_never_exceed_colonist_count():
    # A near-total wipeout: massive population, zero organics, tiny window is
    # not enough here -- use a big elapsed window so the deficit would exceed
    # the whole colony, and confirm the floor-at-0 (never negative) holds.
    planet = make_planet(colonists=5, organics=0, elapsed_hours=24)

    _run(planet)

    assert planet.colonists >= 0
    assert planet.population >= 0


# --------------------------------------------------------------------------- #
# (2) A well-fed colony (organics on hand covers consumption) loses zero
#     colonists, and the exact organics delta pins the canon formula.
# --------------------------------------------------------------------------- #

def test_well_fed_colony_loses_zero_colonists_and_pins_formula():
    # colonists=1000, elapsed=2h -> food_consumed_whole = 41 (same calc as
    # test 1). organics on hand = 1000, comfortably covers it.
    planet = make_planet(colonists=1000, organics=1000, elapsed_hours=2)

    changed = _run(planet)

    assert changed is True
    assert planet.colonists == 1000, "a well-fed colony must lose zero colonists"
    assert planet.population == 1000
    assert planet.organics == 1000 - 41, "organics must net down by exactly the whole-unit food_consumed"
    assert planet.active_events.get("starvation_warning") is None


def test_food_consumed_matches_canon_formula_for_a_distinct_known_pair():
    # A different (colonists, elapsed) pair than the other tests, to prove the
    # formula isn't a coincidental match: colonists=200, elapsed=6h.
    # food_consumed = 200 * 0.5 * (360/1440) = 100 * 0.25 = 25.0 (exact, no
    # fractional remainder to complicate the assertion).
    planet = make_planet(colonists=200, organics=500, elapsed_hours=6)
    expected_food_consumed = int(200 * 0.5 * ((6 * 60) / 1440))
    assert expected_food_consumed == 25  # sanity on the hand-calc itself

    _run(planet)

    assert planet.organics == 500 - 25
    assert planet.colonists == 200, "no deficit at this pair -- zero deaths"


# --------------------------------------------------------------------------- #
# (3) An idle-allocation colony (zero production rates) still eats -- proves
#     the early "nothing producing" short-circuit no longer swallows a live
#     colony's food consumption.
# --------------------------------------------------------------------------- #

def test_idle_allocation_colony_still_consumes_food():
    planet = make_planet(
        colonists=100, organics=1000, elapsed_hours=2,
        fuel_allocation=0, organics_allocation=0, equipment_allocation=0,
    )
    # food_consumed = 100*0.5*(120/1440) = 4.1666.. -> whole unit = 4
    changed = _run(planet)

    assert changed is True
    assert planet.organics == 1000 - 4


# --------------------------------------------------------------------------- #
# (4) A stale starvation_warning from a prior tick is cleared once the colony
#     is healthy again -- mirrors overflow_warning's stamp/clear idiom.
# --------------------------------------------------------------------------- #

def test_stale_starvation_warning_cleared_on_a_healthy_tick():
    # Must cross a whole-unit food_consumed threshold this tick (>0), or the
    # "nothing happened yet" short-circuit returns before active_events is
    # ever touched -- same early-return path overflow_warning relies on.
    # colonists=1000, elapsed=2h -> food_consumed_whole = 41 (see test 1/2).
    planet = make_planet(
        colonists=1000, organics=1000, elapsed_hours=2,
        active_events={"starvation_warning": {"food_deficit": 99, "colonists_lost": 1, "at": "stale"}},
    )

    _run(planet)

    assert planet.colonists == 1000, "well-fed this tick -- no new starvation"
    assert planet.active_events.get("starvation_warning") is None
