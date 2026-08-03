"""Unit tests for ADR-0089 progressive anti-RMT (pinned Ratified parameters)."""

from datetime import datetime, timedelta, timezone

from src.services.player_trade_antirmt import (
    CLUSTER_OUTFLOW_CAP_7D,
    COUNTERPARTY_CAP_30D,
    RECEIVE_CAP_ESTABLISHED_7D,
    RECEIVE_CAP_NEW_LOW_REP_7D,
    SEND_CAP_7D,
    WindowTotals,
    check_caps,
    is_new_or_low_rep,
    new_low_rep_receive_surcharge,
    progressive_surcharge_on_increase,
)


def test_surcharge_zero_inside_first_band():
    assert progressive_surcharge_on_increase(0, 40_000) == 0


def test_surcharge_marginal_cross_into_10pct():
    # prev 40k, add 20k → 10k still at 0%, 10k at 10% = 1000
    assert progressive_surcharge_on_increase(40_000, 20_000) == 1000


def test_surcharge_high_band():
    # prev 900k, add 200k → 100k at 30% + 100k at 60% = 30k+60k = 90k
    assert progressive_surcharge_on_increase(900_000, 200_000) == 90_000


def test_surcharge_no_charge_on_net_receive():
    assert progressive_surcharge_on_increase(100_000, -50_000) == 0


def test_new_low_rep_predicate_age_and_score():
    now = datetime(2026, 8, 3, tzinfo=timezone.utc)
    assert is_new_or_low_rep(
        created_at=now - timedelta(days=3),
        personal_reputation=0,
        now=now,
    )
    assert is_new_or_low_rep(
        created_at=now - timedelta(days=30),
        personal_reputation=-10,
        now=now,
    )
    assert not is_new_or_low_rep(
        created_at=now - timedelta(days=30),
        personal_reputation=0,
        now=now,
    )


def test_new_low_rep_receive_surcharge():
    assert new_low_rep_receive_surcharge(5_000, is_new_low_rep=True) == 0
    # 15k receive → 5k excess * 25% = 1250
    assert new_low_rep_receive_surcharge(15_000, is_new_low_rep=True) == 1250
    assert new_low_rep_receive_surcharge(15_000, is_new_low_rep=False) == 0


def test_caps_match_adr():
    assert SEND_CAP_7D == 2_000_000
    assert RECEIVE_CAP_ESTABLISHED_7D == 1_000_000
    assert RECEIVE_CAP_NEW_LOW_REP_7D == 50_000
    assert COUNTERPARTY_CAP_30D == 250_000
    assert CLUSTER_OUTFLOW_CAP_7D == 500_000


def test_check_caps_send_block():
    prior = WindowTotals(sent=1_900_000, received=0)
    assert (
        check_caps(
            prior_7d=prior,
            send_this=200_000,
            receive_this=0,
            is_new_low_rep=False,
            prior_cp_flow_30d=0,
            cp_flow_this=200_000,
        )
        == "send_cap_exceeded"
    )


def test_check_caps_new_receive_block():
    prior = WindowTotals(sent=0, received=40_000)
    assert (
        check_caps(
            prior_7d=prior,
            send_this=0,
            receive_this=20_000,
            is_new_low_rep=True,
            prior_cp_flow_30d=0,
            cp_flow_this=20_000,
        )
        == "receive_cap_exceeded"
    )
