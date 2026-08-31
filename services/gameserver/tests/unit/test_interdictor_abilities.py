"""LEG-295 — Interdictor hull special abilities (police-forces.md:154-196)."""
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from src.models.ship import ShipType
from src.models.warp_gate import WarpGateBeaconStatus
from src.services import interdictor_abilities_service as svc


def _ship(ship_type=ShipType.NPC_MARSHAL_INTERDICTOR, **kwargs):
    defaults = {
        "id": kwargs.pop("id", "00000000-0000-4000-8000-000000000001"),
        "type": ship_type,
        "equipment_slots": {},
        "is_npc": ship_type in (
            ShipType.NPC_MARSHAL_INTERDICTOR,
            ShipType.NPC_SENTINEL_INTERDICTOR,
        ),
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


class TestShipHasAbility:
    def test_marshal_interdictor_field(self):
        assert svc.ship_has_ability(_ship(), "interdictor_field")

    def test_marshal_contraband_scanner(self):
        assert svc.ship_has_ability(_ship(), "contraband_scanner")

    def test_marshal_lacks_beacon_disruptor(self):
        assert not svc.ship_has_ability(_ship(), "beacon_disruptor")

    def test_sentinel_beacon_disruptor(self):
        ship = _ship(ShipType.NPC_SENTINEL_INTERDICTOR)
        assert svc.ship_has_ability(ship, "beacon_disruptor")
        assert svc.ship_has_ability(ship, "concord_authorization")


class TestInterdictorField:
    def test_apply_and_query_rounds(self):
        interdictor = _ship()
        target = _ship(ShipType.LIGHT_FREIGHTER, is_npc=False)
        assert svc.apply_interdictor_field(interdictor, target)
        assert svc.interdictor_field_rounds_remaining(target) == 3
        assert svc.is_interdictor_field_active(target)

    def test_warp_cost_clamped_while_active(self):
        target = _ship(ShipType.LIGHT_FREIGHTER, is_npc=False)
        svc.apply_interdictor_field(_ship(), target)
        assert svc.warp_turn_cost_with_interdictor_field(5, target) == svc.INFINITY_TURN_COST

    def test_decrement_clears_field(self):
        target = _ship(ShipType.LIGHT_FREIGHTER, is_npc=False)
        svc.apply_interdictor_field(_ship(), target, rounds=1)
        assert svc.decrement_interdictor_field_round(target) == 0
        assert not svc.is_interdictor_field_active(target)

    def test_maybe_apply_skips_npc_defender(self):
        interdictor = _ship()
        npc_target = _ship(ShipType.NPC_MARSHAL_INTERDICTOR)
        assert not svc.maybe_apply_interdictor_on_npc_attack(interdictor, npc_target)


class TestContrabandScannerPatrolGate:
    def test_federation_marshal_patrol_enables_scan(self):
        sector = SimpleNamespace(
            defenses={
                "police_patrol_ships": [
                    {"squad_kind": "federation_marshal", "wanted_threshold": -500},
                ]
            }
        )
        db = MagicMock()
        assert svc.sector_has_contraband_scanner_patrol(db, sector)

    def test_absent_patrol_disables_scan(self):
        sector = SimpleNamespace(defenses={})
        db = MagicMock()
        assert not svc.sector_has_contraband_scanner_patrol(db, sector)


class TestBeaconDisruptor:
    def test_cancels_deployed_beacons_in_range(self):
        beacon = SimpleNamespace(
            id="b1",
            source_sector_id=100,
            destination_sector_id=200,
            status=WarpGateBeaconStatus.DEPLOYED,
        )
        db = MagicMock()
        center = SimpleNamespace(id=1, sector_id=100)
        db.query.return_value.filter.return_value.first.return_value = center
        db.execute.return_value.fetchall.return_value = []
        db.query.return_value.filter.return_value.all.return_value = [beacon]

        disruptor = _ship(ShipType.NPC_SENTINEL_INTERDICTOR)
        cancelled = svc.disrupt_phase1_beacons_near_sector(
            db, 100, disruptor_ship=disruptor,
        )
        assert len(cancelled) == 1
        assert beacon.status == WarpGateBeaconStatus.CANCELLED

    def test_no_op_without_ability(self):
        db = MagicMock()
        marshal = _ship(ShipType.NPC_MARSHAL_INTERDICTOR)
        assert svc.disrupt_phase1_beacons_near_sector(db, 100, disruptor_ship=marshal) == []
