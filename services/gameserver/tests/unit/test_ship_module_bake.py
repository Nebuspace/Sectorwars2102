"""SHIP-MODS §7.1 bake-correctness test (WO-SM-3).

The load-bearing contract: install_module / remove_module drive
_apply_module_effects, which is a _baked-delta REPLACE over the columns that
ALSO carry the legacy upgrade contribution. So installing a module ON TOP of a
legacy upgrade must ADD exactly the module's contribution (no double-count,
legacy bonus untouched), and removing it must restore the column EXACTLY to
spec_base + legacy_upgrade_contribution.

DB-free: these use SimpleNamespace ship/player/spec and a tiny fake query layer,
and monkeypatch flag_modified (SimpleNamespace is not a mapped instance) + the
shipyard gate (the gate is exercised separately; this test is about the bake
math). The real flag_modified / shipyard gate / DB are exercised in the live
dev proof.
"""
import types
import uuid

import pytest

import src.services.ship_upgrade_service as SUS
from src.services.ship_upgrade_service import ShipUpgradeService
from src.models.ship import ShipType


# --- spec_base + legacy SHIELD upgrade fixture numbers ---
SPEC_BASE_MAX_SHIELDS = 1000          # the hull spec's base max_shields
SHIELD_UPGRADE_CONTRIBUTION = 400     # two legacy SHIELD levels @ +200 each, already baked in
SHIELD_BASE_AT_START = SPEC_BASE_MAX_SHIELDS + SHIELD_UPGRADE_CONTRIBUTION  # 1400


def _spec():
    """A LIGHT_FREIGHTER-ish spec with a single (unlocked) module slot."""
    return types.SimpleNamespace(
        type=ShipType.LIGHT_FREIGHTER,
        scanner_range=5,
        module_slots={
            "v": 1, "cols": 3, "rows": 1,
            "slots": [
                # slot 0: unlocked, NOT supercharged.
                {"i": 0, "x": 0, "y": 0, "super": False, "class": None, "requires": None},
                # slot 1: unlocked, supercharged (used to prove super math separately).
                {"i": 1, "x": 1, "y": 0, "super": True, "class": None, "requires": None},
            ],
        },
    )


def _ship():
    """A ship whose combat.max_shields ALREADY carries spec_base + a legacy
    SHIELD-upgrade contribution baked in (the legacy upgrades wrote it
    incrementally; modules has no _baked yet)."""
    return types.SimpleNamespace(
        id=uuid.uuid4(),
        type=ShipType.LIGHT_FREIGHTER,
        name="Test Hull",
        owner_id=None,            # set to player.id in the harness
        is_destroyed=False,
        base_speed=5.0,
        current_speed=5.0,
        max_genesis_devices=0,
        combat={"max_shields": SHIELD_BASE_AT_START, "shields": SHIELD_BASE_AT_START},
        cargo={},
        maintenance={},
        modules=None,             # never installed a module → None → seeded on first install
        upgrades={"shield": 2},   # the legacy SHIELD levels that produced the +400
        equipment_slots={},
    )


class _FakeQuery:
    """A minimal stand-in for SQLAlchemy's Query: returns the single object
    registered for the model being queried, ignoring filters."""
    def __init__(self, obj):
        self._obj = obj

    def filter(self, *a, **k):
        return self

    def with_for_update(self):
        return self

    def first(self):
        return self._obj


class _FakeDB:
    def __init__(self, mapping):
        # mapping: {model_class: instance_to_return}
        self._mapping = mapping
        self.flushed = False

    def query(self, model):
        return _FakeQuery(self._mapping.get(model))

    def flush(self):
        self.flushed = True


@pytest.fixture
def service(monkeypatch):
    """A ShipUpgradeService wired over fake ship/player/spec, with flag_modified
    and the shipyard gate neutralised (this test asserts the bake math)."""
    from src.models.player import Player
    from src.models.ship import Ship, ShipSpecification

    monkeypatch.setattr(SUS, "flag_modified", lambda *a, **k: None)

    ship = _ship()
    spec = _spec()
    player = types.SimpleNamespace(
        id=uuid.uuid4(), credits=10_000_000, is_docked=True, current_port_id=uuid.uuid4(),
    )
    ship.owner_id = player.id

    db = _FakeDB({Player: player, Ship: ship, ShipSpecification: spec})
    svc = ShipUpgradeService(db)

    # Neutralise the shipyard gate — it is proven separately; here we test the bake.
    monkeypatch.setattr(
        ShipUpgradeService, "_resolve_docked_shipyard_station",
        lambda self, p: (types.SimpleNamespace(is_spacedock=True), None),
    )

    return types.SimpleNamespace(svc=svc, ship=ship, spec=spec, player=player)


def _module_contribution(module_class, tier):
    """The tier-scaled shield_bonus the catalog defines for (class, tier)."""
    entry = ShipUpgradeService.MODULE_DEFINITIONS[(module_class, tier)]
    return entry["effects"]["shield_bonus"]


def test_install_then_remove_shield_module_is_exactly_reversible(service):
    svc, ship, player = service.svc, service.ship, service.player

    # Sanity: the column starts at spec_base + legacy upgrade (no module yet).
    assert ship.combat["max_shields"] == SHIELD_BASE_AT_START

    module_bonus = _module_contribution("shield", 1)
    assert module_bonus > 0  # the Mk I shield module must add something

    # --- INSTALL the shield module into the (non-super) slot 0 ---
    res = svc.install_module(ship.id, player.id, slot_index=0, module_class="shield", tier=1)
    assert res["success"], res

    # max_shields == spec_base + upgrade_contribution + module_contribution.
    assert ship.combat["max_shields"] == SHIELD_BASE_AT_START + module_bonus
    assert ship.combat["max_shields"] == (
        SPEC_BASE_MAX_SHIELDS + SHIELD_UPGRADE_CONTRIBUTION + module_bonus
    )

    # The IN-PLACE mutation preserved/created the modules dict with installed + _baked.
    assert isinstance(ship.modules, dict)
    assert "0" in ship.modules["installed"]
    assert ship.modules["installed"]["0"]["class"] == "shield"
    assert "_baked" in ship.modules            # the bake snapshot exists
    assert ship.modules["_baked"]["shield_bonus"] == module_bonus

    # --- REMOVE the module → re-bake must restore EXACTLY to spec_base + upgrade ---
    res2 = svc.remove_module(ship.id, player.id, slot_index=0)
    assert res2["success"], res2

    assert ship.combat["max_shields"] == SHIELD_BASE_AT_START
    assert ship.combat["max_shields"] == SPEC_BASE_MAX_SHIELDS + SHIELD_UPGRADE_CONTRIBUTION
    # The slot is gone; the _baked snapshot drained the module's shield_bonus to 0.
    assert "0" not in ship.modules["installed"]
    assert ship.modules.get("_baked", {}).get("shield_bonus", 0) == 0

    # Salvage refund credited (int truncation of cost × SALVAGE_FRACTION).
    cost = ShipUpgradeService.MODULE_DEFINITIONS[("shield", 1)]["cost"]
    assert res2["refund"] == int(cost * ShipUpgradeService.SALVAGE_FRACTION)


def test_supercharged_slot_multiplies_the_module_contribution(service):
    """Installing into the supercharged slot 1 must add module_bonus × SUPERCHARGE_MULT
    on top of the legacy upgrade, and removal must still restore exactly."""
    svc, ship, player = service.svc, service.ship, service.player
    module_bonus = _module_contribution("shield", 1)
    expected_super = module_bonus * ShipUpgradeService.SUPERCHARGE_MULT

    res = svc.install_module(ship.id, player.id, slot_index=1, module_class="shield", tier=1)
    assert res["success"], res
    assert ship.combat["max_shields"] == pytest.approx(SHIELD_BASE_AT_START + expected_super)
    assert ship.modules["installed"]["1"]["super_at_install"] is True

    svc.remove_module(ship.id, player.id, slot_index=1)
    assert ship.combat["max_shields"] == pytest.approx(SHIELD_BASE_AT_START)


def test_install_rejects_occupied_slot(service):
    svc, ship, player = service.svc, service.ship, service.player
    assert svc.install_module(ship.id, player.id, 0, "shield", 1)["success"]
    dup = svc.install_module(ship.id, player.id, 0, "hull", 1)
    assert not dup["success"]
    assert "occupied" in dup["message"].lower()


def test_install_rejects_missing_slot(service):
    svc, ship, player = service.svc, service.ship, service.player
    res = svc.install_module(ship.id, player.id, 99, "shield", 1)
    assert not res["success"]
    assert "no module slot" in res["message"].lower()


def test_install_rejects_incompatible_hull(service):
    """genesis modules gate on the genesis hulls; LIGHT_FREIGHTER is not one."""
    svc, ship, player = service.svc, service.ship, service.player
    res = svc.install_module(ship.id, player.id, 0, "genesis", 1)
    assert not res["success"]
    assert "not compatible" in res["message"].lower()


def test_equipment_family_module_install_blocked(service):
    """An equipment-family module (lander/harvester/mining/tractor) is BLOCKED
    from install while its consumer wiring is deferred — it would be runtime-inert
    if fitted, so install rejects (no pay-for-nothing) and flags consumer_inert.
    The block fires right after the catalog lookup, before any charge."""
    svc, ship, player = service.svc, service.ship, service.player
    res = svc.install_module(ship.id, player.id, 0, "lander", 1)
    assert res["success"] is False, res
    assert res.get("consumer_inert") is True
    assert "not yet installable" in res["message"].lower() or "coming soon" in res["message"].lower()
