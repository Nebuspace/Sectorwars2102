"""LEG-427 — asteroid depletion band + replenish ETA readout (mining.md:199-207, :253)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from src.models.sector import SectorType
from src.services.mining_service import (
    DEPLETION_LAST_HARVEST_AT_KEY,
    DEPLETION_POOL_PER_TIER,
    DEPLETION_REPLENISH_HEAVY_HOURS,
    DEPLETION_REPLENISH_LIGHT_HOURS,
    _depletion_yield_modifier,
    build_asteroid_depletion_readout,
    depletion_band_for_consumed_fraction,
)


def test_band_labels_match_yield_modifier_cut_points():
    assert depletion_band_for_consumed_fraction(0.0) == "fresh"
    assert depletion_band_for_consumed_fraction(0.01) == "light"
    assert depletion_band_for_consumed_fraction(0.049) == "light"
    assert depletion_band_for_consumed_fraction(0.05) == "moderate"
    assert depletion_band_for_consumed_fraction(0.50) == "moderate"
    assert depletion_band_for_consumed_fraction(0.51) == "heavy"
    assert depletion_band_for_consumed_fraction(0.90) == "heavy"
    assert depletion_band_for_consumed_fraction(0.91) == "exhausted"

    # Fresh/Light share 1.0×; Moderate 0.75×; Heavy 0.5×; Exhausted 0.0.
    assert _depletion_yield_modifier(0.0) == 1.0
    assert _depletion_yield_modifier(0.04) == 1.0
    assert _depletion_yield_modifier(0.05) == 0.75
    assert _depletion_yield_modifier(0.50) == 0.75
    assert _depletion_yield_modifier(0.51) == 0.5
    assert _depletion_yield_modifier(0.90) == 0.5
    assert _depletion_yield_modifier(0.91) == 0.0


def test_readout_none_for_non_asteroid_field():
    sector = SimpleNamespace(
        type=SectorType.STANDARD,
        resources={},
        resource_regeneration=0.5,
    )
    assert build_asteroid_depletion_readout(sector) is None


def test_readout_fresh_full_pool_no_countdown():
    tier = 3
    pool = tier * DEPLETION_POOL_PER_TIER
    sector = SimpleNamespace(
        type=SectorType.ASTEROID_FIELD,
        resource_regeneration=0.5,
        resources={
            "asteroid_richness": {"richness_tier": tier, "richness": "moderate"},
            "depletion_pool": pool,
        },
    )
    out = build_asteroid_depletion_readout(sector)
    assert out is not None
    assert out["band"] == "fresh"
    assert out["yield_modifier"] == 1.0
    assert out["depletion_pool"] == pool
    assert out["pool_size"] == pool
    assert out["replenish_hours"] is None
    assert out["replenish_eta"] is None
    assert out["last_harvest_at"] is None


def test_readout_heavy_seven_day_replenish_eta():
    tier = 5
    pool = tier * DEPLETION_POOL_PER_TIER
    # >50% consumed → heavy → 7d hours
    depletion_pool = int(pool * 0.40)
    last = datetime(2026, 8, 10, 12, 0, tzinfo=timezone.utc)
    sector = SimpleNamespace(
        type=SectorType.ASTEROID_FIELD,
        resource_regeneration=0.95,
        resources={
            "asteroid_richness": {"richness_tier": tier, "richness": "legendary"},
            "depletion_pool": depletion_pool,
            DEPLETION_LAST_HARVEST_AT_KEY: last.isoformat(),
        },
    )
    out = build_asteroid_depletion_readout(sector)
    assert out is not None
    assert out["band"] == "heavy"
    assert out["yield_modifier"] == 0.5
    assert out["replenish_hours"] == DEPLETION_REPLENISH_HEAVY_HOURS
    assert out["replenish_hours"] == 24 * 7
    expected_eta = (last + timedelta(hours=DEPLETION_REPLENISH_HEAVY_HOURS)).isoformat()
    assert out["replenish_eta"] == expected_eta


def test_readout_moderate_light_hours_without_last_harvest():
    tier = 2
    pool = tier * DEPLETION_POOL_PER_TIER
    # 20% consumed → moderate; hours known, eta None without last_harvest
    depletion_pool = int(pool * 0.80)
    sector = SimpleNamespace(
        type=SectorType.ASTEROID_FIELD,
        resource_regeneration=0.4,
        resources={
            "asteroid_richness": {"richness_tier": tier},
            "depletion_pool": depletion_pool,
        },
    )
    out = build_asteroid_depletion_readout(sector)
    assert out is not None
    assert out["band"] == "moderate"
    assert out["replenish_hours"] == DEPLETION_REPLENISH_LIGHT_HOURS
    assert out["replenish_eta"] is None
