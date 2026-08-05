"""Unit tests for WO-FIX-MINING-LASER-L3-FREEZE-PRECIOUS-METALS (no DB).

ADR-0062 E-F3 vs mining.md Laser L3 contradiction, ruled 2026-08-04
(DECISIONS.md adr-0062-ef3-vs-mining-laser-l3, Option 2 -- match canon):
precious_metals scaling freezes at the Laser L2 value; Laser L3 grants no
further precious-metals bump. Ore-efficiency and the quantum_shards L3 gate
are untouched by this ruling.
"""

import inspect

from src.services.mining_service import (
    PRECIOUS_METALS_BASE_RATE,
    PRECIOUS_METALS_CAP,
    PRECIOUS_METALS_PER_LEVEL,
    QUANTUM_SHARDS_MIN_LASER_LEVEL,
    MiningService,
)


def _pm_rate(laser_col: int) -> float:
    """Mirrors harvest()'s pm_rate formula post-freeze (pm_laser_level clamp)."""
    pm_laser_level = min(laser_col, 2)
    return min(
        PRECIOUS_METALS_CAP,
        PRECIOUS_METALS_BASE_RATE + PRECIOUS_METALS_PER_LEVEL * pm_laser_level,
    )


def test_precious_metals_rate_frozen_at_l2_value():
    l0 = _pm_rate(0)
    l1 = _pm_rate(1)
    l2 = _pm_rate(2)
    l3 = _pm_rate(3)
    assert l0 == PRECIOUS_METALS_BASE_RATE == 0.05
    assert l1 == 0.07
    assert l2 == 0.09
    # The whole point of the ruling: L3 must equal L2, not scale further.
    assert l3 == l2 == 0.09


def test_precious_metals_constants_unchanged_by_the_freeze():
    """The freeze is a use-site clamp, not a constant retune -- BASE/PER_LEVEL/
    CAP stay as documented; only the effective laser index feeding the formula
    is clamped at the use site."""
    assert PRECIOUS_METALS_BASE_RATE == 0.05
    assert PRECIOUS_METALS_PER_LEVEL == 0.02
    assert PRECIOUS_METALS_CAP == 0.11


def test_quantum_shards_l3_gate_untouched_by_the_freeze():
    """The ruling explicitly does not touch QUANTUM_SHARDS_MIN_LASER_LEVEL."""
    assert QUANTUM_SHARDS_MIN_LASER_LEVEL == 2


def test_resolve_harvest_source_clamps_pm_laser_level_not_ore_efficiency():
    """Vocab/regression guard -- the freeze must be implemented as a
    pm_laser_level clamp feeding pm_rate, and must not touch the
    efficiency= _laser_efficiency_multiplier(laser_col) call (ore-efficiency
    is explicitly out of scope for this ruling)."""
    source = inspect.getsource(MiningService.resolve_harvest)
    assert "pm_laser_level = min(laser_col, 2)" in source
    assert "PRECIOUS_METALS_PER_LEVEL * pm_laser_level" in source
    assert "self._laser_efficiency_multiplier(laser_col)" in source
