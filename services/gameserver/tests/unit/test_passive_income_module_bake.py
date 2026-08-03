"""Unit tests: get_passive_income reads equipment_slots + modules._baked (WO residual 2)."""

from types import SimpleNamespace

from src.services.ship_upgrade_service import (
    ShipUpgradeService,
    _EQUIPMENT_FAMILY_DEFERRED,
)


def test_passive_income_from_equipment_only():
    ship = SimpleNamespace(
        equipment_slots={"quantum_harvester": {"installed": True}},
        modules={},
    )
    assert ShipUpgradeService.get_passive_income(ship) == 100


def test_passive_income_from_baked_harvester_module_only():
    ship = SimpleNamespace(
        equipment_slots={},
        modules={"_baked": {"passive_income": 100}},
    )
    assert ShipUpgradeService.get_passive_income(ship) == 100


def test_passive_income_sums_equipment_and_baked_module():
    ship = SimpleNamespace(
        equipment_slots={"quantum_harvester": {}},
        modules={"_baked": {"passive_income": 150}},
    )
    assert ShipUpgradeService.get_passive_income(ship) == 250


def test_passive_income_zero_when_empty():
    ship = SimpleNamespace(equipment_slots={}, modules=None)
    assert ShipUpgradeService.get_passive_income(ship) == 0


def test_harvester_no_longer_in_equipment_family_deferred():
    assert "harvester" not in _EQUIPMENT_FAMILY_DEFERRED
    assert "lander" in _EQUIPMENT_FAMILY_DEFERRED
