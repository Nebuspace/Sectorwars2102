"""LEG-378: residual economy balancing levers (bounty / insurance / station commodity)."""
from __future__ import annotations

import src.services.economy_balancing_levers as levers


def setup_function() -> None:
    """Reset process-local lever state between tests."""
    levers.BOUNTY_PAYOUT_RATIO = 1.0
    levers.INSURANCE_PREMIUM_PCT.update(
        {"BASIC": 0.10, "STANDARD": 0.17, "PREMIUM": 0.22}
    )
    levers.INSURANCE_NET_PAYOUT_PCT.update(
        {"BASIC": 0.45, "STANDARD": 0.65, "PREMIUM": 0.75}
    )


def test_snapshot_includes_residual_keys() -> None:
    snap = levers.snapshot()
    assert snap["bounty_payout_ratio"] == 1.0
    assert snap["insurance_premium_pct"]["BASIC"] == 0.10
    assert snap["insurance_net_payout_pct"]["STANDARD"] == 0.65


def test_set_bounty_payout_ratio_and_apply() -> None:
    applied = levers.set_bounty_payout_ratio(0.5)
    assert applied == {"old": 1.0, "new": 0.5}
    assert levers.apply_bounty_payout_ratio(1000) == 500
    assert levers.apply_bounty_payout_ratio(0) == 0


def test_set_insurance_net_payout_pct() -> None:
    applied = levers.set_insurance_net_payout_pct({"BASIC": 0.40})
    assert applied["BASIC"]["old"] == 0.45
    assert applied["BASIC"]["new"] == 0.40
    assert levers.INSURANCE_NET_PAYOUT_PCT["BASIC"] == 0.40


def test_bounty_ratio_rejects_out_of_range() -> None:
    try:
        levers.set_bounty_payout_ratio(6.0)
        assert False, "expected ValueError"
    except ValueError as exc:
        assert "bounty_payout_ratio" in str(exc)
