"""Regression pin for the colony overcrowding cap-taper
(WO-SECA-PIN-TESTS Lane C, WO-CT2).

Pins the taper block inside PlanetaryService._calculate_production_rates
(planetary_service.py:2380 signature, taper block :2490-2502) against the
CONCRETE canon rule (colonization.md:193-195): population growth halts at the
demographic ceiling (max_population) and tapers linearly across the top 10%
band. The 0.9 band boundary and the linear shape are hardcoded here
deliberately (per the WO — they are concrete canon, not a NO-CANON constant),
so any drift in the code's band/shape breaks this pin.

DB-free: PlanetaryService(db=None) is safe as long as planet.owner_id is None
— the only self.db read in _calculate_production_rates is the Overclock
lookup, gated behind `if planet.owner_id is not None`.
"""
from types import SimpleNamespace
from uuid import uuid4

import pytest

from src.services.planetary_service import PlanetaryService

# A fixed pre-taper baseline: colonist_rate = colonists * 0.01 * (habitability / 100).
# 10,000 colonists * 0.01 * 1.0 (habitability 100) = 100.0/day, well clear of the
# LOW_HABITABILITY_THRESHOLD (30) so no other multiplier perturbs it.
BASE_COLONISTS = 10_000
BASE_HABITABILITY = 100
BASE_RATE = BASE_COLONISTS * 0.01 * (BASE_HABITABILITY / 100.0)  # 100.0


def make_planet(*, population, max_population, max_colonists=1000, colonists=BASE_COLONISTS,
                 habitability_score=BASE_HABITABILITY):
    return SimpleNamespace(
        id=uuid4(),
        # Production-rate inputs held neutral so only the taper moves colonist_rate.
        factory_level=0, farm_level=0, mine_level=0,
        fuel_allocation=0, organics_allocation=0, equipment_allocation=0,
        type=None,
        research_level=0,
        specialization=None,
        citadel_level=0,
        owner_id=None,  # skips the Overclock self.db.query lookup entirely
        active_events={},  # no gourmet-food stockpile
        under_siege=False,
        habitability_score=habitability_score,
        colonists=colonists,
        population=population,
        max_population=max_population,
        max_colonists=max_colonists,
    )


def _colonist_rate(planet):
    svc = PlanetaryService(db=None)
    return svc._calculate_production_rates(planet)["colonists"]


# --------------------------------------------------------------------------- #
# (1) population >= max_population -> colonist_rate == 0.0
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("population", [1000, 1500], ids=["at-cap", "over-cap"])
def test_colonist_rate_zero_at_or_above_max_population(population):
    planet = make_planet(population=population, max_population=1000)
    assert _colonist_rate(planet) == 0.0


# --------------------------------------------------------------------------- #
# (2) 95% of max_population -> exactly 50% of the untapered rate
# --------------------------------------------------------------------------- #

def test_colonist_rate_at_95_percent_is_half_the_untapered_rate():
    max_population = 1000
    untapered = _colonist_rate(make_planet(population=900, max_population=max_population))
    tapered = _colonist_rate(make_planet(population=950, max_population=max_population))
    assert untapered == pytest.approx(BASE_RATE)
    assert tapered == pytest.approx(0.5 * untapered, abs=0.01)


# --------------------------------------------------------------------------- #
# (3) population <= 0.9 * max_population -> rate unchanged by the taper
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("population", [500, 899, 900], ids=["well-clear", "just-under-band", "exact-boundary"])
def test_colonist_rate_unchanged_at_or_below_90_percent(population):
    planet = make_planet(population=population, max_population=1000)
    assert _colonist_rate(planet) == pytest.approx(BASE_RATE)


# --------------------------------------------------------------------------- #
# (4) taper never yields a negative rate (multiplier clamped 0..1)
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("fraction", [0.90, 0.925, 0.95, 0.975, 0.999, 1.0, 1.5])
def test_colonist_rate_never_negative_across_the_taper_band(fraction):
    max_population = 1000
    planet = make_planet(population=int(fraction * max_population), max_population=max_population)
    rate = _colonist_rate(planet)
    assert rate >= 0.0
    assert rate <= BASE_RATE


# --------------------------------------------------------------------------- #
# (5) max_population == 0 / None -> taper skipped entirely
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("max_population", [0, None])
def test_taper_skipped_when_max_population_is_unset(max_population):
    # An overcrowded-looking population is irrelevant once the demographic
    # ceiling itself is unset (:2494 guard) — the untapered rate is returned.
    planet = make_planet(population=5000, max_population=max_population)
    assert _colonist_rate(planet) == pytest.approx(BASE_RATE)


# --------------------------------------------------------------------------- #
# Keyed on max_population, never max_colonists (the workforce cap, ADR-0035)
# --------------------------------------------------------------------------- #

def test_taper_keys_on_max_population_not_max_colonists():
    """Varying max_colonists alone — with population/max_population held at
    the 95%-tapered point — must not move colonist_rate at all."""
    low = _colonist_rate(make_planet(population=950, max_population=1000, max_colonists=1000))
    high = _colonist_rate(make_planet(population=950, max_population=1000, max_colonists=200_000))
    assert low == high
