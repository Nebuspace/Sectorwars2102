"""WO-CRT-4 §2.7 — the FLYWHEEL GOVERNOR proof (the headline correctness risk).
DB-free: governed_rp / _gov_apply / faucet_copay are pure. Encodes per-empire-SUM
same-day idempotence, day-roll reset, reproduce-exactly (soft_cap=inf), and the copay.
(Reviewer MED: the headline idempotence shipped without a unit test — this is it.)
"""
import math
from datetime import datetime, timezone

import src.services.research_service as RS

UTC = timezone.utc


def test_governed_rp_reproduce_exactly_inf():
    # soft_cap=inf == today byte-for-byte for every raw.
    for raw in [0.0, 100.0, 1500.0, 2000.0, 100000.0]:
        assert RS.governed_rp(raw, math.inf) == raw


def test_governed_rp_tapers_above_cap_monotonic():
    cap = float(RS.GOV_BASE_SOFT_CAP)
    assert RS.governed_rp(cap - 1, cap) == cap - 1        # under cap: full value
    assert RS.governed_rp(cap, cap) == cap
    g2 = RS.governed_rp(cap * 2, cap)
    g4 = RS.governed_rp(cap * 4, cap)
    assert cap < g2 < cap * 2                              # tapered above cap
    assert g2 < g4 < cap * 4                               # still monotonic (next lab yields more, but less)


def test_gov_apply_governs_the_EMPIRE_SUM_idempotent_same_day():
    # 4 sub-cap planet drains summing OVER the cap are governed as ONE empire sum
    # (kills the lab-spread dodge), and the credit is incremental + idempotent.
    led, cap, now = {}, float(RS.GOV_BASE_SOFT_CAP), datetime(2026, 6, 1, tzinfo=UTC)
    drains = [500, 500, 500, 500]              # sum 2000 > cap 1500
    credited = sum(RS._gov_apply(led, d, cap, now=now) for d in drains)
    assert credited == int(math.floor(RS.governed_rp(float(sum(drains)), cap)))
    # re-settle the same canonical day with nothing new → ZERO new RP (no double-count).
    assert RS._gov_apply(led, 0, cap, now=now) == 0


def test_gov_apply_day_roll_resets_the_running_sum():
    led, cap = {}, float(RS.GOV_BASE_SOFT_CAP)
    d1 = datetime(2026, 6, 1, tzinfo=UTC)
    d2 = datetime(2026, 6, 3, tzinfo=UTC)     # >=2 canonical-day buckets apart at any GAME_TIME_SCALE
    RS._gov_apply(led, 2000, cap, now=d1)
    assert RS._canonical_day_bucket(d1) != RS._canonical_day_bucket(d2), "test needs distinct buckets"
    RS._gov_apply(led, 100, cap, now=d2)
    assert led["gov_raw_today"] == 100         # reset to the new day's sum, NOT 2100


def test_gov_apply_reproduce_exactly_inf_byte_identical():
    led, now = {}, datetime(2026, 6, 1, tzinfo=UTC)
    credited = sum(RS._gov_apply(led, d, math.inf, now=now) for d in [700, 800, 900])
    assert credited == 2400                    # soft_cap=inf → governed total == raw sum


def test_faucet_copay_off_and_scaling_nonneg():
    assert RS.faucet_copay(0) == 0             # COPAY off-equivalent at zero banked
    expect = int(math.floor(RS.FAUCET_CREDIT_COPAY * 1000 * RS.RP_TO_CREDIT_RATE))
    assert RS.faucet_copay(1000) == expect
    assert RS.faucet_copay(1000) >= 0
