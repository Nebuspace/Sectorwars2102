"""WO-QUANTUM-HARVESTER-COST-REFUND-ALIGN — catalog cost + uninstall salvage.

Pins:
  1. install_equipment("quantum_harvester") charges the canon 50,000 cr.
  2. uninstall_equipment refunds int(cost × SALVAGE_FRACTION) (= 25%).
  3. Residual (3): Class-7+ venue + 24h pending before quantum_harvester_slot.

DB-free harness mirrors test_ship_module_bake.py (FakeDB + flag_modified stub).
"""
import types
import uuid
from datetime import datetime, timedelta, timezone

import pytest

import src.services.ship_upgrade_service as SUS
from src.models.ship import ShipType
from src.models.station import StationClass
from src.services.ship_upgrade_service import ShipUpgradeService


HARVESTER_COST = 50_000


def _scout_ship():
    return types.SimpleNamespace(
        id=uuid.uuid4(),
        type=ShipType.SCOUT_SHIP,
        name="Test Scout",
        owner_id=None,
        is_destroyed=False,
        equipment_slots={},
        quantum_harvester_slot=False,
        modules={},
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
def harness(monkeypatch):
    from src.models.player import Player
    from src.models.ship import Ship
    from src.models.station import Station

    monkeypatch.setattr(SUS, "flag_modified", lambda *a, **k: None)

    ship = _scout_ship()
    port_id = uuid.uuid4()
    player = types.SimpleNamespace(
        id=uuid.uuid4(),
        credits=100_000,
        is_docked=True,
        current_port_id=port_id,
    )
    ship.owner_id = player.id
    station = types.SimpleNamespace(
        id=port_id,
        name="Tech Port 7",
        station_class=StationClass.CLASS_7,
    )

    db = _FakeDB({Player: player, Ship: ship, Station: station})
    svc = ShipUpgradeService(db)
    return types.SimpleNamespace(svc=svc, ship=ship, player=player, db=db, station=station)


def test_quantum_harvester_catalog_cost_is_50k():
    """Docs-win: EQUIPMENT_DEFINITIONS + MODULE_DEFINITIONS Mk I lock to canon 50k."""
    assert ShipUpgradeService.EQUIPMENT_DEFINITIONS["quantum_harvester"]["cost"] == HARVESTER_COST
    assert ShipUpgradeService.MODULE_DEFINITIONS[("harvester", 1)]["cost"] == HARVESTER_COST


def test_install_charges_50k(harness):
    svc, ship, player = harness.svc, harness.ship, harness.player
    before = player.credits

    res = svc.install_equipment(ship.id, player.id, "quantum_harvester")
    assert res["success"], res
    assert res["cost_paid"] == HARVESTER_COST
    assert player.credits == before - HARVESTER_COST
    # Residual (3): paid + seated, but slot inactive until ready_at
    assert ship.quantum_harvester_slot is False
    assert res.get("pending") is True
    assert "quantum_harvester" in ship.equipment_slots
    assert harness.db.flushed is True


def test_uninstall_refunds_25_percent_of_install_cost(harness):
    svc, ship, player = harness.svc, harness.ship, harness.player

    assert svc.install_equipment(ship.id, player.id, "quantum_harvester")["success"]
    credits_after_install = player.credits
    expected_refund = int(HARVESTER_COST * ShipUpgradeService.SALVAGE_FRACTION)
    assert expected_refund == 12_500  # 25% of 50k

    res = svc.uninstall_equipment(ship.id, player.id, "quantum_harvester")
    assert res["success"], res
    assert res["refund"] == expected_refund
    assert player.credits == credits_after_install + expected_refund
    assert res["remaining_credits"] == player.credits
    assert ship.quantum_harvester_slot is False
    assert "quantum_harvester" not in ship.equipment_slots


def test_install_rejects_below_class_7(harness):
    harness.station.station_class = StationClass.CLASS_3
    res = harness.svc.install_equipment(harness.ship.id, harness.player.id, "quantum_harvester")
    assert res["success"] is False
    assert "Class-7" in res["message"]
    assert harness.player.credits == 100_000


def test_slot_activates_after_ready_at(harness):
    svc, ship, player = harness.svc, harness.ship, harness.player
    assert svc.install_equipment(ship.id, player.id, "quantum_harvester")["success"]
    assert ship.quantum_harvester_slot is False

    past = datetime.now(timezone.utc) - timedelta(hours=1)
    ship.equipment_slots["quantum_harvester"]["ready_at"] = past.isoformat()
    assert ShipUpgradeService._sync_quantum_harvester_slot(ship) is True
    assert ship.quantum_harvester_slot is True
