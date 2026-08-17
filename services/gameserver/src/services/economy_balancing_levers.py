"""In-process economy balancing levers (lifecycle.md § Balancing levers).

Mutable via admin ``/admin/economy/levers*`` — same process-local contract as
upgrade-definition edits: changes apply immediately and revert on gameserver
restart unless persisted elsewhere.

Station commodity ``base_price`` / ``production_rate`` live on
``Station.commodities`` JSONB and are persisted; they are not stored here.
"""

from __future__ import annotations

from typing import Dict, Mapping, Optional

# Global faucet throttle applied when collecting player/system bounty pots.
BOUNTY_PAYOUT_RATIO: float = 1.0

# Ship insurance premium as fraction of purchase_value (ADR-0081).
INSURANCE_PREMIUM_PCT: Dict[str, float] = {
    "BASIC": 0.10,
    "STANDARD": 0.17,
    "PREMIUM": 0.22,
}

# Net payout on destruction as fraction of purchase_value (ADR-0061).
INSURANCE_NET_PAYOUT_PCT: Dict[str, float] = {
    "BASIC": 0.45,
    "STANDARD": 0.65,
    "PREMIUM": 0.75,
}

_VALID_INSURANCE_TIERS = frozenset({"BASIC", "STANDARD", "PREMIUM"})


def snapshot() -> dict:
    """Read-only snapshot for the Economy Levers admin GET."""
    return {
        "bounty_payout_ratio": float(BOUNTY_PAYOUT_RATIO),
        "insurance_premium_pct": dict(INSURANCE_PREMIUM_PCT),
        "insurance_net_payout_pct": dict(INSURANCE_NET_PAYOUT_PCT),
    }


def set_bounty_payout_ratio(ratio: float) -> dict:
    """Set global bounty faucet throttle. Returns {old, new}."""
    global BOUNTY_PAYOUT_RATIO
    if ratio < 0.0 or ratio > 5.0:
        raise ValueError("bounty_payout_ratio must be between 0.0 and 5.0")
    old = float(BOUNTY_PAYOUT_RATIO)
    BOUNTY_PAYOUT_RATIO = float(ratio)
    return {"old": old, "new": float(BOUNTY_PAYOUT_RATIO)}


def set_insurance_premium_pct(updates: Mapping[str, float]) -> dict:
    """Merge tier → premium fraction updates. Returns applied {tier: {old, new}}."""
    return _merge_insurance_map(INSURANCE_PREMIUM_PCT, updates, "insurance_premium_pct")


def set_insurance_net_payout_pct(updates: Mapping[str, float]) -> dict:
    """Merge tier → net payout fraction updates. Returns applied {tier: {old, new}}."""
    return _merge_insurance_map(
        INSURANCE_NET_PAYOUT_PCT, updates, "insurance_net_payout_pct"
    )


def _merge_insurance_map(
    target: Dict[str, float],
    updates: Mapping[str, float],
    label: str,
) -> dict:
    if not updates:
        raise ValueError(f"No {label} fields to update")
    applied: dict = {}
    for tier, value in updates.items():
        key = str(tier).upper()
        if key not in _VALID_INSURANCE_TIERS:
            raise ValueError(f"Unknown insurance tier for {label}: {tier}")
        v = float(value)
        if v < 0.0 or v > 1.0:
            raise ValueError(f"{label}[{key}] must be between 0.0 and 1.0")
        old = float(target[key])
        target[key] = v
        applied[key] = {"old": old, "new": v}
    return applied


def apply_bounty_payout_ratio(raw_amount: int) -> int:
    """Scale a raw bounty credit amount by the live faucet throttle."""
    if raw_amount <= 0:
        return 0
    ratio = float(BOUNTY_PAYOUT_RATIO)
    if ratio == 1.0:
        return int(raw_amount)
    return max(0, int(raw_amount * ratio))
