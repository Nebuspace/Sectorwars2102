"""Pins Planet.production_efficiency wiring into PlanetaryService._calculate_production_rates
(WO-PLN-PRODEFF-1, planetary_service.py:2380 signature, multiplier block ~:2441-2459).

CANON (SYSTEMS/planetary-production-tick.md:106-113):
    effective_rate = base_rate
                   * (1 + 0.10 * relevant_building_level)
                   * specialization_multiplier
                   * (1 + 0.05 * citadel_level)
                   * production_efficiency

"production_efficiency is the catch-all admin-tunable multiplier (0.0-2.0)." The doc's
base_rate formula (lines 56-60) defines fuel_rate / organics_rate / equipment_rate only —
colonist growth (line 78-88, 151-162) is governed solely by habitability_ratio and
research yield (line 76) solely by "the research multiplier, citadel bonus, and siege
penalty" — neither mentions production_efficiency. So this multiplier is scoped to the
three commodity rates and must NOT move colonists or research.

DB-free house pattern (matches test_colony_cap_taper.py): PlanetaryService(db=None) is
safe because owner_id=None skips the only self.db read (the Overclock lookup, gated
behind `if planet.owner_id is not None`).
"""
from types import SimpleNamespace
from uuid import uuid4

import pytest

from src.services.planetary_service import PlanetaryService

# Non-trivial allocations/buildings so every scaled rate is comfortably nonzero,
# and habitability held at 100 (>= LOW_HABITABILITY_THRESHOLD) so the low-hab
# penalty never perturbs the numbers.
BASE_COLONISTS = 1000
BASE_HABITABILITY = 100


def make_planet(*, production_efficiency=1.0, has_efficiency_attr=True,
                 factory_level=2, farm_level=2, mine_level=2,
                 fuel_allocation=100, organics_allocation=100, equipment_allocation=100,
                 research_level=2, colonists=BASE_COLONISTS, population=0, max_population=0):
    kwargs = dict(
        id=uuid4(),
        factory_level=factory_level, farm_level=farm_level, mine_level=mine_level,
        fuel_allocation=fuel_allocation, organics_allocation=organics_allocation,
        equipment_allocation=equipment_allocation,
        type=None,
        research_level=research_level,
        specialization=None,
        citadel_level=0,
        owner_id=None,  # skips the Overclock self.db.query lookup entirely
        active_events={},  # no gourmet-food stockpile
        under_siege=False,
        habitability_score=BASE_HABITABILITY,
        colonists=colonists,
        population=population,
        max_population=max_population,
        max_colonists=1000,
    )
    if has_efficiency_attr:
        kwargs["production_efficiency"] = production_efficiency
    return SimpleNamespace(**kwargs)


def _rates(planet):
    svc = PlanetaryService(db=None)
    return svc._calculate_production_rates(planet)


# --------------------------------------------------------------------------- #
# (1) 2.0 doubles every scaled commodity rate vs a 1.0 control
# --------------------------------------------------------------------------- #

def test_efficiency_2x_exactly_doubles_commodity_rates():
    control = _rates(make_planet(production_efficiency=1.0))
    doubled = _rates(make_planet(production_efficiency=2.0))

    for key in ("fuel", "organics", "equipment"):
        assert control[key] > 0, f"control {key} rate must be nonzero to prove doubling"
        assert doubled[key] == pytest.approx(2 * control[key]), key


def test_efficiency_2x_does_not_move_colonists_or_research():
    control = _rates(make_planet(production_efficiency=1.0, colonists=10_000))
    doubled = _rates(make_planet(production_efficiency=2.0, colonists=10_000))

    assert doubled["colonists"] == pytest.approx(control["colonists"])
    assert doubled["research"] == pytest.approx(control["research"])
    assert control["colonists"] > 0
    assert control["research"] > 0


# --------------------------------------------------------------------------- #
# (2) 0.0 zeroes the scaled commodity rates, leaves colonists/research alone
# --------------------------------------------------------------------------- #

def test_efficiency_zero_zeroes_commodity_rates_only():
    rates = _rates(make_planet(production_efficiency=0.0, colonists=10_000))

    assert rates["fuel"] == 0.0
    assert rates["organics"] == 0.0
    assert rates["equipment"] == 0.0
    assert rates["colonists"] > 0.0
    assert rates["research"] > 0.0


# --------------------------------------------------------------------------- #
# (3) NULL / missing attribute behaves as 1.0 -- byte-identical to a default run
# --------------------------------------------------------------------------- #

def test_efficiency_none_is_byte_identical_to_explicit_one():
    control = _rates(make_planet(production_efficiency=1.0))
    from_none = _rates(make_planet(production_efficiency=None))
    assert from_none == control


def test_efficiency_missing_attribute_is_byte_identical_to_explicit_one():
    """A pre-existing DB-free fixture (predating this column's wiring) that never
    sets production_efficiency at all -- getattr(..., None) must not raise, and
    must resolve to the same neutral 1.0 as an explicit default."""
    control = _rates(make_planet(production_efficiency=1.0))
    missing_attr = _rates(make_planet(has_efficiency_attr=False))
    assert missing_attr == control


# --------------------------------------------------------------------------- #
# (4) Out-of-range values clamp to the documented 0.0-2.0 admin band
# --------------------------------------------------------------------------- #

def test_efficiency_negative_clamps_to_zero():
    clamped = _rates(make_planet(production_efficiency=-1.0))
    floor = _rates(make_planet(production_efficiency=0.0))
    assert clamped == floor


def test_efficiency_above_band_clamps_to_two():
    clamped = _rates(make_planet(production_efficiency=3.0))
    ceiling = _rates(make_planet(production_efficiency=2.0))
    assert clamped == ceiling


# --------------------------------------------------------------------------- #
# (5) Regression: the existing multiplier stack at efficiency=1.0 is unchanged.
# Pins the full formula -- allocation * base_rate * (1 + 0.10*building level),
# with type_eff neutral (type=None), specialization neutral (None), and
# citadel_level=0 -- so this test breaks if any earlier multiplier in the
# stack shifts, not just this WO's new one.
# --------------------------------------------------------------------------- #

def test_full_multiplier_stack_at_efficiency_one_matches_hand_calc():
    planet = make_planet(
        production_efficiency=1.0,
        fuel_allocation=100, mine_level=2,
        organics_allocation=50, farm_level=3,
        equipment_allocation=25, factory_level=1,
    )
    rates = _rates(planet)

    expected_fuel = 100 * 10 * (1 + 0.10 * 2)       # 1200.0
    expected_organics = 50 * 10 * (1 + 0.10 * 3)    # 650.0
    expected_equipment = 25 * 10 * (1 + 0.10 * 1)   # 275.0

    assert rates["fuel"] == pytest.approx(expected_fuel)
    assert rates["organics"] == pytest.approx(expected_organics)
    assert rates["equipment"] == pytest.approx(expected_equipment)
