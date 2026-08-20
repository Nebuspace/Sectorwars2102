"""LEG-320 — module upgrade dry-run preview (before/after effect rows).

DB-free: SimpleNamespace ship/player/spec + fake query layer (same harness shape
as test_ship_module_bake). Asserts:
  - current vs projected rows cover Engine/Cargo/Shield/Hull/Sensor/Drone/Genesis
  - magnitudes come from MODULE_DEFINITIONS (no invented numbers)
  - preview does not mutate ship.modules / does not flush
  - route is wired to preview_module_install
"""
from __future__ import annotations

import types
import uuid
from pathlib import Path

import pytest

import src.services.ship_upgrade_service as SUS
from src.models.ship import ShipType
from src.services.ship_upgrade_service import ShipUpgradeService


REQUIRED_PREVIEW_KEYS = (
    "speed_bonus",
    "cargo_bonus_percent",
    "shield_bonus",
    "hull_bonus",
    "evasion_bonus_percent",
    "drone_capacity_bonus",
    "genesis_capacity_bonus",
)


def _spec():
    return types.SimpleNamespace(
        type=ShipType.LIGHT_FREIGHTER,
        scanner_range=5,
        module_slots={
            "v": 1,
            "cols": 3,
            "rows": 1,
            "slots": [
                {"i": 0, "x": 0, "y": 0, "super": False, "class": None, "requires": None},
                {"i": 1, "x": 1, "y": 0, "super": True, "class": None, "requires": None},
            ],
        },
    )


def _ship():
    return types.SimpleNamespace(
        id=uuid.uuid4(),
        type=ShipType.LIGHT_FREIGHTER,
        name="Preview Hull",
        owner_id=None,
        is_destroyed=False,
        base_speed=5.0,
        current_speed=5.0,
        max_genesis_devices=0,
        combat={"max_shields": 1000, "shields": 1000, "max_hull": 2000, "hull": 2000},
        cargo={},
        maintenance={},
        modules={"v": 1, "installed": {}},
        upgrades={},
        equipment_slots={},
    )


class _FakeQuery:
    def __init__(self, obj):
        self._obj = obj

    def filter(self, *a, **k):
        return self

    def populate_existing(self):
        return self

    def with_for_update(self):
        return self

    def first(self):
        return self._obj


class _FakeDB:
    def __init__(self, mapping):
        self._mapping = mapping
        self.flushed = False

    def query(self, model):
        return _FakeQuery(self._mapping.get(model))

    def flush(self):
        self.flushed = True


@pytest.fixture
def preview_harness(monkeypatch):
    from src.models.player import Player
    from src.models.ship import Ship, ShipSpecification

    monkeypatch.setattr(SUS, "flag_modified", lambda *a, **k: None)

    ship = _ship()
    spec = _spec()
    player = types.SimpleNamespace(
        id=uuid.uuid4(),
        credits=10_000_000,
        is_docked=True,
        current_port_id=uuid.uuid4(),
    )
    ship.owner_id = player.id
    db = _FakeDB({Player: player, Ship: ship, ShipSpecification: spec})
    svc = ShipUpgradeService(db)
    return types.SimpleNamespace(svc=svc, ship=ship, spec=spec, player=player, db=db)


@pytest.mark.unit
def test_preview_empty_slot_engine_shows_catalog_delta(preview_harness):
    h = preview_harness
    entry = ShipUpgradeService.MODULE_DEFINITIONS[("engine", 1)]
    expected = float(entry["effects"]["speed_bonus"])

    before_modules = dict(h.ship.modules)
    res = h.svc.preview_module_install(
        h.ship.id, h.player.id, slot_index=0, module_class="engine", tier=1
    )

    assert res["success"] is True
    for key in REQUIRED_PREVIEW_KEYS:
        assert key in res["current"]
        assert key in res["projected"]
        assert key in res["delta"]
    assert res["current"]["speed_bonus"] == 0.0
    assert res["projected"]["speed_bonus"] == expected
    assert res["delta"]["speed_bonus"] == expected
    assert res["candidate"]["effects"]["speed_bonus"] == entry["effects"]["speed_bonus"]
    # No write / no flush
    assert h.ship.modules == before_modules
    assert h.db.flushed is False


@pytest.mark.unit
def test_preview_covers_family_catalog_rows(preview_harness):
    """Spot-check Cargo/Shield/Hull/Sensor/Drone/Genesis projected deltas."""
    h = preview_harness
    cases = (
        ("cargo", "cargo_bonus_percent"),
        ("shield", "shield_bonus"),
        ("hull", "hull_bonus"),
        ("sensor", "evasion_bonus_percent"),
        ("drone", "drone_capacity_bonus"),
        ("genesis", "genesis_capacity_bonus"),
    )
    for module_class, effect_key in cases:
        entry = ShipUpgradeService.MODULE_DEFINITIONS[(module_class, 1)]
        # genesis is hull-gated on LIGHT_FREIGHTER — still previewable for stats;
        # totals still use MODULE_DEFINITIONS magnitudes.
        expected = float(entry["effects"][effect_key])
        res = h.svc.preview_module_install(
            h.ship.id, h.player.id, 0, module_class, 1
        )
        assert res["success"] is True, res
        assert res["projected"][effect_key] == expected
        assert res["delta"][effect_key] == expected


@pytest.mark.unit
def test_preview_swap_replaces_slot_contribution(preview_harness):
    h = preview_harness
    shield_t1 = float(
        ShipUpgradeService.MODULE_DEFINITIONS[("shield", 1)]["effects"]["shield_bonus"]
    )
    hull_t1 = float(
        ShipUpgradeService.MODULE_DEFINITIONS[("hull", 1)]["effects"]["hull_bonus"]
    )
    h.ship.modules = {
        "v": 1,
        "installed": {"0": {"class": "shield", "tier": 1}},
        "_baked": {"shield_bonus": shield_t1},
    }

    res = h.svc.preview_module_install(
        h.ship.id, h.player.id, slot_index=0, module_class="hull", tier=1
    )
    assert res["success"] is True
    assert res["replacing"] == {"class": "shield", "tier": 1}
    assert res["current"]["shield_bonus"] == shield_t1
    assert res["projected"]["shield_bonus"] == 0.0
    assert res["projected"]["hull_bonus"] == hull_t1
    # modules JSONB unchanged
    assert h.ship.modules["installed"]["0"]["class"] == "shield"


@pytest.mark.unit
def test_preview_unknown_module_fails(preview_harness):
    h = preview_harness
    res = h.svc.preview_module_install(
        h.ship.id, h.player.id, 0, "not_a_real_module", 1
    )
    assert res["success"] is False
    assert "Unknown module" in res["message"]


@pytest.mark.unit
def test_route_wires_preview_endpoint() -> None:
    route_src = (
        Path(__file__).resolve().parents[2]
        / "src"
        / "api"
        / "routes"
        / "ship_upgrades.py"
    ).read_text(encoding="utf-8")
    assert '@router.post("/{ship_id}/modules/preview")' in route_src
    assert "preview_module_install" in route_src
