"""Unit tests for depletion-pool real-time full-restore
(WO-BUILD-MINING-DEPLETION-POOL-REPLENISH-TIMER)."""

from datetime import datetime, timedelta, timezone

from src.services.mining_service import (
    DEPLETION_LAST_HARVEST_AT_KEY,
    DEPLETION_POOL_PER_TIER,
    DEPLETION_REPLENISH_HEAVY_HOURS,
    DEPLETION_REPLENISH_LIGHT_HOURS,
    _depletion_replenish_hours,
    _maybe_replenish_depletion_pool,
)


def test_replenish_hours_light_vs_heavy():
    pool = 5 * DEPLETION_POOL_PER_TIER  # 500
    # Moderate: 40% consumed → 24h
    assert _depletion_replenish_hours(pool, int(pool * 0.60)) == DEPLETION_REPLENISH_LIGHT_HOURS
    # Exactly 50% consumed stays Moderate (matches yield modifier ≤0.50) → 24h
    assert _depletion_replenish_hours(pool, int(pool * 0.50)) == DEPLETION_REPLENISH_LIGHT_HOURS
    # Heavy: >50% consumed → 7d
    assert _depletion_replenish_hours(pool, int(pool * 0.49)) == DEPLETION_REPLENISH_HEAVY_HOURS


def test_maybe_replenish_noop_when_full():
    tier = 3
    pool = tier * DEPLETION_POOL_PER_TIER
    resources = {"depletion_pool": pool}
    assert _maybe_replenish_depletion_pool(resources, tier=tier) is False


def test_maybe_replenish_light_after_24h():
    tier = 3
    pool = tier * DEPLETION_POOL_PER_TIER
    now = datetime(2026, 8, 5, 12, 0, tzinfo=timezone.utc)
    resources = {
        "depletion_pool": pool - 10,  # light consumption
        DEPLETION_LAST_HARVEST_AT_KEY: (now - timedelta(hours=24)).isoformat(),
    }
    assert _maybe_replenish_depletion_pool(resources, tier=tier, now=now) is True
    assert resources["depletion_pool"] == pool
    assert DEPLETION_LAST_HARVEST_AT_KEY not in resources


def test_maybe_replenish_light_not_before_24h():
    tier = 3
    pool = tier * DEPLETION_POOL_PER_TIER
    now = datetime(2026, 8, 5, 12, 0, tzinfo=timezone.utc)
    resources = {
        "depletion_pool": pool - 10,
        DEPLETION_LAST_HARVEST_AT_KEY: (now - timedelta(hours=23)).isoformat(),
    }
    assert _maybe_replenish_depletion_pool(resources, tier=tier, now=now) is False
    assert resources["depletion_pool"] == pool - 10


def test_maybe_replenish_heavy_needs_7_days():
    tier = 3
    pool = tier * DEPLETION_POOL_PER_TIER
    now = datetime(2026, 8, 5, 12, 0, tzinfo=timezone.utc)
    resources = {
        "depletion_pool": int(pool * 0.40),  # 60% consumed → Heavy (>50%)
        DEPLETION_LAST_HARVEST_AT_KEY: (now - timedelta(days=6)).isoformat(),
    }
    assert _maybe_replenish_depletion_pool(resources, tier=tier, now=now) is False

    resources[DEPLETION_LAST_HARVEST_AT_KEY] = (now - timedelta(days=7)).isoformat()
    assert _maybe_replenish_depletion_pool(resources, tier=tier, now=now) is True
    assert resources["depletion_pool"] == pool
