"""Regression pin for the siege stockpile skim (WO-SECA-PIN-TESTS Lane B).

Pins PlanetaryService._skim_siege_stockpiles (planetary_service.py:1659-1763),
invoked exactly once per APPLIED siege turn from _apply_siege_turn (:1649).
DB-free: PlanetaryService(MagicMock()) stands in for the Session; the two
locked-query chains (besieger Player, then their Ship — the lock order the
code documents at :1699-1701) are stubbed via db.query.side_effect, one mock
per expected call in that exact order.
"""
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from src.services import planetary_service as ps
from src.services.planetary_service import PlanetaryService, SIEGE_STOCKPILE_SKIM_FRACTION


@pytest.fixture(autouse=True)
def _noop_flag_modified(monkeypatch):
    """The SimpleNamespace ship stand-ins below aren't SQLAlchemy-mapped, so
    the real flag_modified would raise (established pattern,
    test_bounty_service_nh2.py) — the JSONB-dirty-flag is irrelevant to the
    skim logic under test (the code also reassigns ship.cargo directly)."""
    monkeypatch.setattr(ps, "flag_modified", lambda *a, **k: None)


def make_planet(*, attacker_id=None, fuel_ore=0, organics=0, equipment=0):
    return SimpleNamespace(
        id=uuid4(),
        siege_attacker_id=attacker_id,
        fuel_ore=fuel_ore,
        organics=organics,
        equipment=equipment,
    )


def make_besieger(*, current_ship_id=None):
    return SimpleNamespace(id=uuid4(), current_ship_id=current_ship_id)


def make_ship(*, capacity=10_000, contents=None):
    contents = dict(contents or {})
    return SimpleNamespace(
        id=uuid4(),
        cargo={"capacity": capacity, "used": sum(contents.values()), "contents": contents},
    )


def _locked_query(result):
    """A MagicMock .query(...) chain: filter().with_for_update().first() -> result."""
    q = MagicMock()
    q.filter.return_value.with_for_update.return_value.first.return_value = result
    return q


def _service_with_queries(*results):
    """PlanetaryService(MagicMock()) whose db.query(...) returns `results` in
    call order — Player lock first, then Ship lock (:1699-1701)."""
    svc = PlanetaryService(db=MagicMock())
    svc.db.query.side_effect = [_locked_query(r) for r in results]
    return svc


# --------------------------------------------------------------------------- #
# (1) No besieger -> no-op, zero locks
# --------------------------------------------------------------------------- #

def test_no_siege_attacker_returns_empty_and_queries_nothing():
    planet = make_planet(attacker_id=None, fuel_ore=1000, organics=1000, equipment=1000)
    svc = _service_with_queries()  # no query() call is expected at all
    result = svc._skim_siege_stockpiles(planet)
    assert result == {}
    assert svc.db.query.call_count == 0


# --------------------------------------------------------------------------- #
# (2) Nothing plunderable -> no-op, no locks acquired
# --------------------------------------------------------------------------- #

def test_all_stockpiles_zero_returns_empty_before_any_lock():
    planet = make_planet(attacker_id=uuid4(), fuel_ore=0, organics=0, equipment=0)
    svc = _service_with_queries()
    result = svc._skim_siege_stockpiles(planet)
    assert result == {}
    assert svc.db.query.call_count == 0


def test_stockpiles_too_small_to_skim_floor_to_zero_before_any_lock():
    """int(stock * SIEGE_STOCKPILE_SKIM_FRACTION) flooring to 0 (e.g. stock=1
    at the shipped 5% fraction) is treated the same as empty — no locks."""
    planet = make_planet(attacker_id=uuid4(), fuel_ore=1, organics=1, equipment=1)
    assert int(1 * SIEGE_STOCKPILE_SKIM_FRACTION) == 0  # guards the premise
    svc = _service_with_queries()
    result = svc._skim_siege_stockpiles(planet)
    assert result == {}
    assert svc.db.query.call_count == 0


# --------------------------------------------------------------------------- #
# (3) Skim per column == int(stock * SIEGE_STOCKPILE_SKIM_FRACTION)
# (4) Zero-sum: planet decrement == ship credit; used recomputed from contents
# --------------------------------------------------------------------------- #

def test_skim_matches_fraction_and_is_zero_sum_with_ample_capacity():
    attacker_id = uuid4()
    planet = make_planet(attacker_id=attacker_id, fuel_ore=1000, organics=2000, equipment=400)
    besieger = make_besieger(current_ship_id=uuid4())
    ship = make_ship(capacity=1_000_000)  # ample: no clamp exercised here

    svc = _service_with_queries(besieger, ship)
    moved = svc._skim_siege_stockpiles(planet)

    expected_ore = int(1000 * SIEGE_STOCKPILE_SKIM_FRACTION)
    expected_organics = int(2000 * SIEGE_STOCKPILE_SKIM_FRACTION)
    expected_equipment = int(400 * SIEGE_STOCKPILE_SKIM_FRACTION)
    assert moved == {
        "ore": expected_ore,
        "organics": expected_organics,
        "equipment": expected_equipment,
    }

    # Zero-sum: the planet loses exactly what the besieger's hold gains.
    assert planet.fuel_ore == 1000 - expected_ore
    assert planet.organics == 2000 - expected_organics
    assert planet.equipment == 400 - expected_equipment

    contents = ship.cargo["contents"]
    assert contents == {
        "ore": expected_ore,
        "organics": expected_organics,
        "equipment": expected_equipment,
    }
    assert ship.cargo["used"] == sum(contents.values())


# --------------------------------------------------------------------------- #
# (5) Capacity clamp: moved amounts never exceed remaining capacity
# --------------------------------------------------------------------------- #

def test_skim_clamped_to_remaining_cargo_capacity():
    attacker_id = uuid4()
    planet = make_planet(attacker_id=attacker_id, fuel_ore=1000, organics=0, equipment=0)
    besieger = make_besieger(current_ship_id=uuid4())
    # Capacity nearly full: only 10 units of room left.
    ship = make_ship(capacity=100, contents={"fuel": 90})

    svc = _service_with_queries(besieger, ship)
    moved = svc._skim_siege_stockpiles(planet)

    wanted = int(1000 * SIEGE_STOCKPILE_SKIM_FRACTION)  # 50 — exceeds the 10 remaining
    assert wanted > 10
    assert moved == {"ore": 10}
    # The planet is decremented by only the amount actually moved, not the
    # full (unclamped) skim.
    assert planet.fuel_ore == 1000 - 10

    contents = ship.cargo["contents"]
    assert contents["ore"] == 10
    assert ship.cargo["used"] == 100  # pre-existing 90 + the 10 moved
    assert ship.cargo["used"] == sum(contents.values())


# --------------------------------------------------------------------------- #
# (6) Besieger gone / no ship -> no-op, planet untouched
# --------------------------------------------------------------------------- #

def test_besieger_not_found_returns_empty_and_planet_untouched():
    planet = make_planet(attacker_id=uuid4(), fuel_ore=1000, organics=1000, equipment=1000)
    svc = _service_with_queries(None)  # the locked Player query finds nobody
    result = svc._skim_siege_stockpiles(planet)
    assert result == {}
    assert (planet.fuel_ore, planet.organics, planet.equipment) == (1000, 1000, 1000)
    assert svc.db.query.call_count == 1  # never reaches the Ship lock


def test_besieger_with_no_current_ship_returns_empty_and_planet_untouched():
    planet = make_planet(attacker_id=uuid4(), fuel_ore=1000, organics=1000, equipment=1000)
    besieger = make_besieger(current_ship_id=None)
    svc = _service_with_queries(besieger)
    result = svc._skim_siege_stockpiles(planet)
    assert result == {}
    assert (planet.fuel_ore, planet.organics, planet.equipment) == (1000, 1000, 1000)
    assert svc.db.query.call_count == 1  # never reaches the Ship lock


def test_besieger_ship_missing_returns_empty_and_planet_untouched():
    planet = make_planet(attacker_id=uuid4(), fuel_ore=1000, organics=1000, equipment=1000)
    besieger = make_besieger(current_ship_id=uuid4())
    svc = _service_with_queries(besieger, None)  # the locked Ship query finds nothing
    result = svc._skim_siege_stockpiles(planet)
    assert result == {}
    assert (planet.fuel_ore, planet.organics, planet.equipment) == (1000, 1000, 1000)
    assert svc.db.query.call_count == 2
