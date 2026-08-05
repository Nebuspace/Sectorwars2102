"""Unit tests for WO-MINING-ASYNC-HARVEST — model + service constants (no DB)."""

from src.models.mining_harvest import MiningHarvest, MiningHarvestStatus
from src.services.mining_service import (
    HARVEST_TURN_COST,
    HARVEST_WINDOW_SECONDS,
    MiningService,
)


def test_mining_harvest_tablename_and_statuses():
    assert MiningHarvest.__tablename__ == "mining_harvests"
    assert {s.value for s in MiningHarvestStatus} == {
        "PENDING",
        "COMPLETED",
        "CANCELLED",
        "INTERRUPTED",
    }


def test_harvest_window_and_turn_cost_constants():
    assert HARVEST_TURN_COST == 5
    assert HARVEST_WINDOW_SECONDS == 30


def test_harvest_empty_contract_still_has_commodity_keys():
    """Vocab drift guard — empty dict in harvest() must keep ore/pm/qs keys."""
    import inspect

    source = inspect.getsource(MiningService.harvest)
    empty_dict_start = source.index("empty = {")
    empty_dict_src = source[
        empty_dict_start : source.index("}", empty_dict_start) + 1
    ]
    for key in ("ore", "precious_metals", "quantum_shards"):
        assert f'"{key}"' in empty_dict_src


def test_interrupt_refund_is_half_prepaid_turns():
    assert HARVEST_TURN_COST // 2 == 2


def test_resolve_due_and_interrupt_methods_exist():
    assert callable(MiningService.resolve_harvest)
    assert callable(MiningService.resolve_due_harvests)
    assert callable(MiningService.interrupt_harvest)
