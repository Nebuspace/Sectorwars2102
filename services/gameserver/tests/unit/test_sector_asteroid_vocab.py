"""Unit tests for WO-ARCH-RES-2I-B (ghost-vocabulary purge — sector asteroid yield).

Pure-catalog test, no DB — asserts the ``asteroid_yield`` Column default on
Sector.resources uses exactly the frozen mining harvest contract's keys
(mining_service.harvest's empty-result dict), not the ghost slug
'radioactives' which no market/registry/harvest path ever produces.
"""

from src.models.sector import Sector
from src.services.mining_service import MiningService


def test_asteroid_yield_default_matches_harvest_contract_keys():
    default = Sector.__table__.c.resources.default.arg
    yield_keys = set(default["asteroid_yield"].keys())
    assert yield_keys == {"ore", "precious_metals", "quantum_shards"}


def test_asteroid_yield_default_has_no_ghost_radioactives_key():
    default = Sector.__table__.c.resources.default.arg
    assert "radioactives" not in default["asteroid_yield"]


def test_asteroid_yield_keys_cross_asserted_against_harvest_empty_contract():
    """Drift-proof: if the harvest contract's key set ever changes, this test
    fails rather than the two silently diverging again."""
    import inspect

    source = inspect.getsource(MiningService.harvest)
    empty_dict_start = source.index('empty = {')
    empty_dict_src = source[empty_dict_start:source.index('}', empty_dict_start) + 1]

    default = Sector.__table__.c.resources.default.arg
    yield_keys = set(default["asteroid_yield"].keys())

    for key in yield_keys:
        assert f'"{key}"' in empty_dict_src, (
            f"asteroid_yield key {key!r} not found in mining_service's harvest "
            "empty-result contract — vocab drift"
        )
