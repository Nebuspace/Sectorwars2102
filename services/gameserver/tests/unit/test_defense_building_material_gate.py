"""SEC-DEFBUILD-MATERIALS: proves the live-exploit gap in
``CitadelService.build_defense_building`` (internal; HTTP construct retired to
POST /planets/{id}/grid/place per ADR-0094) is closed.

Two independent gaps were live in ``construct_defense_building`` /
``build_defense_building`` before this fix:

  1. Research gate — turned out to ALREADY be correctly enforced (CRT WO-K0-3,
     shipped 2026-06-20, `git log -S"CRT WO-K0-3" citadel_service.py`) despite a
     same-morning docstring flag (commit 27a39b6a) claiming it was skipped.
     ``test_research_gate_still_blocks_ungated_player`` pins that this is (still)
     true so a future regression is caught.
  2. Material cost — the REAL live gap. defense.md canon prices every defense
     building except orbital_platform with a per-planet MATERIAL cost (e.g.
     rail_gun "150,000 cr + 20,000 ore + 10,000 equipment"), and
     DEFENSE_BUILDINGS even carried some of that as metadata (e.g.
     planet_minefield's ``effects.equipment_cost``), but
     ``build_defense_building`` only ever deducted credits — players could
     construct rail guns / defense grids / scanner arrays / turret networks /
     minefields for free materials. Fixed by adding a ``materials`` (+
     tier-aware ``tier_materials``) key to each spec and charging/validating it
     from the already-locked ``Planet`` row, mirroring
     ``planet_grid.py::_charge_materials``.

FakeSession/_planet/_player mirror test_planet_minefield_build_endpoint.py's
established hand-rolled-fake pattern (query() routed by SQLAlchemy model class).
"""
import uuid
from types import SimpleNamespace

import pytest

from src.models.planet import Planet
from src.models.player import Player
from src.services.citadel_service import DEFENSE_BUILDINGS, CitadelService


class _FakeQuery:
    def __init__(self, obj):
        self._obj = obj

    def filter(self, *a, **k):
        return self

    def populate_existing(self, *a, **k):
        return self

    def with_for_update(self, *a, **k):
        return self

    def first(self):
        return self._obj


class _FakeSession:
    def __init__(self, planet, player):
        self._planet = planet
        self._player = player

    def query(self, model):
        if model is Planet:
            return _FakeQuery(self._planet)
        if model is Player:
            return _FakeQuery(self._player)
        raise AssertionError(f"unexpected query model: {model}")

    def flush(self):
        pass


def _planet(*, citadel_level, owner_id, fuel_ore=0, equipment=0, research_ledger=None):
    return SimpleNamespace(
        id=uuid.uuid4(),
        owner_id=owner_id,
        citadel_level=citadel_level,
        active_events={},
        fuel_ore=fuel_ore,
        equipment=equipment,
    )


def _player(*, owner_id, credits=10_000_000, unlocked=None):
    return SimpleNamespace(
        id=owner_id,
        credits=credits,
        # research_service.ledger_of reads player.research_ledger.
        research_ledger={"rp": 0, "unlocked": list(unlocked or [])},
    )


def _build(planet, player, building_type):
    db = _FakeSession(planet, player)
    svc = CitadelService(db)
    return svc.build_defense_building(planet.id, player.id, building_type)


# --------------------------------------------------------------------------- #
# 1. Research gate — pin the pre-existing (correct) rejection behaviour.
# --------------------------------------------------------------------------- #

def test_research_gate_still_blocks_ungated_player():
    owner = uuid.uuid4()
    planet = _planet(citadel_level=4, owner_id=owner, fuel_ore=1_000_000, equipment=1_000_000)
    player = _player(owner_id=owner, unlocked=[])  # no research unlocked

    result = _build(planet, player, "rail_gun")

    assert result["success"] is False
    assert "research" in result["message"].lower()
    # Rejected before any charge — proves the gate short-circuits ahead of cost.
    assert player.credits == 10_000_000
    assert planet.fuel_ore == 1_000_000
    assert planet.equipment == 1_000_000


def test_research_gate_passes_once_unlocked():
    owner = uuid.uuid4()
    planet = _planet(citadel_level=4, owner_id=owner, fuel_ore=1_000_000, equipment=1_000_000)
    player = _player(owner_id=owner, unlocked=["t.defense.railgun.1"])

    result = _build(planet, player, "rail_gun")

    assert result["success"] is True


# --------------------------------------------------------------------------- #
# 2. Material cost — the exploit this WO closes.
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize(
    "building_type,unlocked,materials",
    [
        ("rail_gun", ["t.defense.railgun.1"], {"fuel_ore": 20000, "equipment": 10000}),
        ("planetary_defense_grid", ["t.defense.grid.1"], {"equipment": 15000}),
        ("scanner_array", [], {"equipment": 10000}),
        ("turret_network", [], {"equipment": 8000}),
        ("planet_minefield", [], {"equipment": 10000}),
    ],
)
def test_material_cost_now_charged_on_success(building_type, unlocked, materials):
    """The core exploit-closed proof: constructing each material-gated defense
    building actually DEBITS the planet's stockpile columns by the canon amount."""
    owner = uuid.uuid4()
    spec = DEFENSE_BUILDINGS[building_type]
    planet = _planet(
        citadel_level=spec["min_citadel_level"],
        owner_id=owner,
        fuel_ore=1_000_000,
        equipment=1_000_000,
    )
    player = _player(owner_id=owner, unlocked=unlocked)

    fuel_ore_before, equipment_before = planet.fuel_ore, planet.equipment

    result = _build(planet, player, building_type)

    assert result["success"] is True, result
    assert result["materials_deducted"] == materials
    assert planet.fuel_ore == fuel_ore_before - materials.get("fuel_ore", 0)
    assert planet.equipment == equipment_before - materials.get("equipment", 0)


@pytest.mark.parametrize(
    "building_type,unlocked,fuel_ore,equipment,missing_material",
    [
        # fuel_ore short (equipment plentiful) — dict-iteration order matters
        # here (fuel_ore is checked first), so isolate each shortage.
        ("rail_gun", ["t.defense.railgun.1"], 0, 1_000_000, "fuel_ore"),
        ("rail_gun", ["t.defense.railgun.1"], 1_000_000, 0, "equipment"),
        ("planetary_defense_grid", ["t.defense.grid.1"], 0, 0, "equipment"),
        ("scanner_array", [], 0, 0, "equipment"),
        ("turret_network", [], 0, 0, "equipment"),
        ("planet_minefield", [], 0, 0, "equipment"),
    ],
)
def test_insufficient_material_rejects_and_charges_nothing(
    building_type, unlocked, fuel_ore, equipment, missing_material
):
    """Pre-fix, a planet with ZERO stockpile could still build for free. Post-fix
    it must be rejected — and rejected WITHOUT deducting credits either (no
    partial charge)."""
    owner = uuid.uuid4()
    spec = DEFENSE_BUILDINGS[building_type]
    planet = _planet(
        citadel_level=spec["min_citadel_level"],
        owner_id=owner,
        fuel_ore=fuel_ore,
        equipment=equipment,
    )
    player = _player(owner_id=owner, unlocked=unlocked, credits=10_000_000)

    result = _build(planet, player, building_type)

    assert result["success"] is False
    assert missing_material in result["message"]
    assert player.credits == 10_000_000  # no partial charge
    assert planet.fuel_ore == fuel_ore
    assert planet.equipment == equipment


def test_orbital_platform_stays_credits_only():
    """orbital_platform is canon-corrected (defense.md 2026-08-04) to credits-only
    — the material gate must NOT invent a cost for it."""
    owner = uuid.uuid4()
    planet = _planet(citadel_level=4, owner_id=owner, fuel_ore=0, equipment=0)
    player = _player(owner_id=owner, credits=10_000_000)

    result = _build(planet, player, "orbital_platform")

    assert result["success"] is True
    assert result["materials_deducted"] == {}
    assert player.credits == 10_000_000 - 500000


def test_planetary_defense_grid_l2_upgrade_stays_credits_only():
    """L2 (2nd unit, 300k cr) carries no equipment cost per canon — must NOT
    fall back to L1's 15,000-equipment amount."""
    owner = uuid.uuid4()
    planet = _planet(
        citadel_level=4, owner_id=owner, fuel_ore=0, equipment=0,
    )
    planet.active_events = {"defense_buildings": {"planetary_defense_grid": 1}}
    player = _player(owner_id=owner, unlocked=["t.defense.grid.1"], credits=10_000_000)

    result = _build(planet, player, "planetary_defense_grid")

    assert result["success"] is True
    assert result["materials_deducted"] == {}
    assert player.credits == 10_000_000 - 300000
