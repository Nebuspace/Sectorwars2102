"""Unit tests: _sync_quantum_harvester_slot from equipment OR lattice (WO residual 2)."""

from types import SimpleNamespace

from src.services.ship_upgrade_service import ShipUpgradeService


def test_sync_true_from_equipment_only():
    ship = SimpleNamespace(
        equipment_slots={"quantum_harvester": {"installed": True}},
        modules={},
        quantum_harvester_slot=False,
    )
    assert ShipUpgradeService._sync_quantum_harvester_slot(ship) is True
    assert ship.quantum_harvester_slot is True


def test_sync_false_while_equipment_pending():
    from datetime import datetime, timedelta, timezone

    future = (datetime.now(timezone.utc) + timedelta(hours=12)).isoformat()
    ship = SimpleNamespace(
        equipment_slots={"quantum_harvester": {"ready_at": future, "pending": True}},
        modules={},
        quantum_harvester_slot=True,
    )
    assert ShipUpgradeService._sync_quantum_harvester_slot(ship) is False
    assert ship.quantum_harvester_slot is False


def test_sync_true_from_lattice_harvester_only():
    ship = SimpleNamespace(
        equipment_slots={},
        modules={"installed": {"0": {"class": "harvester", "tier": 1}}},
        quantum_harvester_slot=False,
    )
    assert ShipUpgradeService._sync_quantum_harvester_slot(ship) is True
    assert ship.quantum_harvester_slot is True


def test_sync_true_when_both_present():
    ship = SimpleNamespace(
        equipment_slots={"quantum_harvester": {}},
        modules={"installed": {"1": {"class": "harvester", "tier": 1}}},
        quantum_harvester_slot=False,
    )
    assert ShipUpgradeService._sync_quantum_harvester_slot(ship) is True
    assert ship.quantum_harvester_slot is True


def test_sync_false_when_empty():
    ship = SimpleNamespace(
        equipment_slots={},
        modules={"installed": {}},
        quantum_harvester_slot=True,
    )
    assert ShipUpgradeService._sync_quantum_harvester_slot(ship) is False
    assert ship.quantum_harvester_slot is False


def test_sync_ignores_non_harvester_modules():
    ship = SimpleNamespace(
        equipment_slots={},
        modules={"installed": {"0": {"class": "engine", "tier": 1}}},
        quantum_harvester_slot=True,
    )
    assert ShipUpgradeService._sync_quantum_harvester_slot(ship) is False
    assert ship.quantum_harvester_slot is False


def test_sync_keeps_true_when_equipment_survives_lattice_removal():
    """After lattice harvester gone, equipment alone must keep the flag."""
    ship = SimpleNamespace(
        equipment_slots={"quantum_harvester": {}},
        modules={"installed": {}},
        quantum_harvester_slot=True,
    )
    assert ShipUpgradeService._sync_quantum_harvester_slot(ship) is True
    assert ship.quantum_harvester_slot is True
