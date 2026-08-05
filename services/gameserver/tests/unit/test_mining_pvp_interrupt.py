"""WO-MINING-PVP-INTERRUPT — wiring + refund contract (no DB)."""

import inspect

from src.services.combat_service import CombatService
from src.services.mining_service import HARVEST_TURN_COST, MiningService


def test_interrupt_refund_is_half_of_prepaid_turns():
    assert HARVEST_TURN_COST // 2 == 2


def test_interrupt_pending_for_ship_helper_exists():
    assert callable(MiningService.interrupt_pending_for_ship)
    assert callable(MiningService.interrupt_harvest)


def test_attack_player_wires_mining_interrupt():
    source = inspect.getsource(CombatService.attack_player)
    assert "interrupt_pending_for_ship" in source
    assert "ShipStatus.MINING" in source
    assert "pvp_attack" in source


def test_npc_attack_player_wires_mining_interrupt():
    source = inspect.getsource(CombatService.npc_attack_player)
    assert "interrupt_pending_for_ship" in source
    assert "ShipStatus.MINING" in source
    assert "npc_pvp_attack" in source
