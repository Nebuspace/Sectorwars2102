"""Point-of-use wiring for tech-tree readers (WO-BUILD-WIRE-TECH-TREE-POINT-OF-USE-READERS).

Five consumers must actually call has_tool / gate_value / tech_modifier — not just
the catalog + reader tests. DB-free where possible (SimpleNamespace + MagicMock).
"""
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from src.api.routes import planet_grid as grid_routes
from src.services import research_service, structures
from src.services.movement_service import MovementService
from src.services.planetary_service import PlanetaryService
from src.services.tech_tree import FREE_ROOT_ID


def _ledger(*unlocked):
    return {
        "rp": 0,
        "insight": 0,
        "doctrine": 0,
        "unlocked": [FREE_ROOT_ID, *unlocked],
    }


def _player(*unlocked):
    return SimpleNamespace(id=uuid4(), research_ledger=_ledger(*unlocked))


# ---------------------------------------------------------------------------
# 1. production_rate → planetary production tick
# ---------------------------------------------------------------------------

def _commodity_planet(*, owner_id=None):
    return SimpleNamespace(
        id=uuid4(),
        factory_level=2,
        farm_level=2,
        mine_level=2,
        fuel_allocation=100,
        organics_allocation=100,
        equipment_allocation=100,
        type=None,
        research_level=2,
        specialization=None,
        citadel_level=0,
        owner_id=owner_id,
        active_events={},
        under_siege=False,
        habitability_score=100,
        colonists=1000,
        population=0,
        max_population=0,
        max_colonists=1000,
        production_efficiency=1.0,
    )


def test_production_rate_modifier_lifts_commodity_rates():
    owner = _player("t.production.yield.1")
    control_owner = _player()
    planet = _commodity_planet(owner_id=owner.id)

    def _svc(player):
        db = MagicMock()
        q = MagicMock()
        q.filter.return_value.first.return_value = player
        db.query.return_value = q
        return PlanetaryService(db)

    boosted = _svc(owner)._calculate_production_rates(planet)
    baseline = _svc(control_owner)._calculate_production_rates(planet)

    for key in ("fuel", "organics", "equipment"):
        assert baseline[key] > 0
        assert boosted[key] == pytest.approx(baseline[key] * 1.05)
    assert boosted["colonists"] == pytest.approx(baseline["colonists"])


def test_production_rate_absent_is_byte_identical_to_unowned():
    owner = _player()
    owned = _commodity_planet(owner_id=owner.id)
    unowned = _commodity_planet(owner_id=None)

    db = MagicMock()
    q = MagicMock()
    q.filter.return_value.first.return_value = owner
    db.query.return_value = q

    with_owner = PlanetaryService(db)._calculate_production_rates(owned)
    without = PlanetaryService(db=None)._calculate_production_rates(unowned)
    for key in ("fuel", "organics", "equipment", "colonists"):
        assert with_owner[key] == pytest.approx(without[key])


# ---------------------------------------------------------------------------
# 2. turn_cost → warp cost calc
# ---------------------------------------------------------------------------

def _good_ship():
    return SimpleNamespace(
        type="SCOUT_SHIP",
        current_speed=1.0,
        base_speed=1.0,
        maintenance={"condition": 80.0, "last_maintenance": None},
    )


def test_turn_cost_modifier_cheapens_warp_by_five_percent():
    svc = MovementService(MagicMock())
    ship = _good_ship()
    efficient = _player("t.ships.efficiency.1")
    baseline = svc._warp_cost_from_turn_cost(20, ship)
    cheap = svc._warp_cost_from_turn_cost(20, ship, player=efficient)
    assert baseline == 20
    assert cheap == 19  # round(20 * 0.95)


def test_turn_cost_modifier_never_drops_below_one():
    svc = MovementService(MagicMock())
    ship = _good_ship()
    efficient = _player("t.ships.efficiency.1")
    assert svc._warp_cost_from_turn_cost(1, ship, player=efficient) == 1


# ---------------------------------------------------------------------------
# 3. terraform_intensity gate → settle-tick ceiling
# ---------------------------------------------------------------------------

def test_terraform_intensity_default_is_standard():
    assert structures.terraform_intensity_for_player(None) == "standard"
    assert structures.terraform_intensity_for_player(_player()) == "standard"


def test_terraform_intensity_unlock_raises_to_aggressive():
    assert structures.terraform_intensity_for_player(
        _player("t.terraforming.intensity.1")
    ) == "aggressive"


# ---------------------------------------------------------------------------
# 4. plot_clear / hazard_clear primitives
# ---------------------------------------------------------------------------

def _tiny_grid(*, cleared=False, hazard=None, surveyed=False):
    return {
        "grid": {"cols": 1, "rows": 1},
        "plots": [{
            "x": 0, "y": 0,
            "cleared": cleared,
            "hazard": hazard,
            "surveyed": surveyed,
            "terrain": "FLAT",
            "axes": {"thermal": 40, "hydro": 40},
            "building_id": None,
        }],
        "buildings": [],
    }


def test_clear_plot_requires_no_hazard_and_sets_cleared():
    st = _tiny_grid(cleared=False)
    ok, reason = structures.clear_plot(st, 0, 0)
    assert ok, reason
    assert st["plots"][0]["cleared"] is True


def test_clear_plot_refuses_hazard_cell():
    st = _tiny_grid(cleared=False, hazard="radiation")
    ok, reason = structures.clear_plot(st, 0, 0)
    assert not ok
    assert "hazard" in reason


def test_clear_hazard_removes_marker_and_clears():
    st = _tiny_grid(cleared=False, hazard="radiation")
    ok, reason = structures.clear_hazard(st, 0, 0)
    assert ok, reason
    assert st["plots"][0]["hazard"] is None
    assert st["plots"][0]["cleared"] is True


def test_clear_hazard_noops_when_clean():
    st = _tiny_grid(cleared=True, hazard=None)
    ok, _ = structures.clear_hazard(st, 0, 0)
    assert not ok


def test_has_tool_keys_match_catalog():
    surveyor = _player("t.exploration.survey.1")
    clearer = _player("t.terraforming.plot_clear.1")
    remed = _player("t.terraforming.hazard_clear.1")
    assert research_service.has_tool(surveyor, "grid_survey")
    assert research_service.has_tool(clearer, "plot_clear")
    assert research_service.has_tool(remed, "hazard_clear")
    assert not research_service.has_tool(_player(), "grid_survey")
    assert not research_service.has_tool(_player(), "plot_clear")
    assert not research_service.has_tool(_player(), "hazard_clear")


# ---------------------------------------------------------------------------
# 5. grid_survey fog/reveal
# ---------------------------------------------------------------------------

def test_survey_plot_marks_surveyed():
    st = _tiny_grid(surveyed=False)
    ok, reason = structures.survey_plot(st, 0, 0)
    assert ok, reason
    assert st["plots"][0]["surveyed"] is True


def test_grid_fog_redacts_unsurveyed_without_tool():
    planet = SimpleNamespace(
        structures=_tiny_grid(surveyed=False, hazard="radiation"),
        size=5,
    )
    fogged = grid_routes._apply_survey_fog(planet.structures["plots"], _player())
    assert fogged[0].get("fog") is True
    assert "hazard" not in fogged[0]
    assert "terrain" not in fogged[0]
    assert "axes" not in fogged[0]
    assert fogged[0]["x"] == 0


def test_grid_fog_lifts_with_survey_tool():
    plots = _tiny_grid(surveyed=False, hazard="radiation")["plots"]
    revealed = grid_routes._apply_survey_fog(plots, _player("t.exploration.survey.1"))
    assert revealed[0].get("hazard") == "radiation"
    assert "fog" not in revealed[0]


def test_grid_fog_lifts_for_already_surveyed_plot():
    plots = _tiny_grid(surveyed=True, hazard="radiation")["plots"]
    revealed = grid_routes._apply_survey_fog(plots, _player())
    assert revealed[0].get("hazard") == "radiation"
    assert "fog" not in revealed[0]
