"""LEG-301: syndicate governance vote thresholds / quorum / veto / tiebreak.

Canon magnitudes from FEATURES/economy/port-ownership.md:138-152 only.
DB-free: resolve_governance_ballots is a pure function.
"""
from src.services.station_governance_service import (
    VOTE_SPECS,
    normalize_vote_type,
    resolve_governance_ballots,
)


A = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
B = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
C = "cccccccc-cccc-cccc-cccc-cccccccccccc"


def _snap(*pairs, inactive=None):
    inactive = inactive or {}
    out = []
    for pid, pct in pairs:
        out.append({"player_id": pid, "pct": pct, "inactive": bool(inactive.get(pid))})
    return out


def test_canon_thresholds():
    assert VOTE_SPECS["tariff"]["threshold"] == 0.50
    assert VOTE_SPECS["tariff"]["veto"] is False
    assert VOTE_SPECS["tariff"]["window_hours"] == 72.0
    assert VOTE_SPECS["upgrade"]["threshold"] == 0.50
    assert VOTE_SPECS["upgrade"]["veto"] is True
    assert VOTE_SPECS["upgrade"]["capex_min"] == 500_000
    assert VOTE_SPECS["sale"]["threshold"] == 0.66
    assert VOTE_SPECS["sale"]["window_hours"] == 96.0
    assert VOTE_SPECS["withdrawal"]["threshold"] == 0.50
    assert VOTE_SPECS["withdrawal"]["veto"] is False


def test_normalize_aliases():
    assert normalize_vote_type("tariff_change") == "tariff"
    assert normalize_vote_type("major_upgrade") == "upgrade"
    assert normalize_vote_type("withdrawal-schedule") == "withdrawal"


def test_tariff_passes_at_50_with_quorum():
    snap = _snap((A, 60), (B, 40))
    ballots = [
        {"player_id": A, "position": "for"},
        {"player_id": B, "position": "against"},
    ]
    out = resolve_governance_ballots(
        vote_type="tariff", snapshot=snap, ballots=ballots, rng_seed=1, window_closed=False
    )
    assert out["quorum_ok"] is True
    assert out["threshold_ok"] is True
    assert out["status"] == "passed"


def test_sale_requires_66():
    snap = _snap((A, 60), (B, 40))
    ballots = [
        {"player_id": A, "position": "for"},
        {"player_id": B, "position": "against"},
    ]
    out = resolve_governance_ballots(
        vote_type="sale", snapshot=snap, ballots=ballots, rng_seed=1, window_closed=False
    )
    assert out["threshold_ok"] is False
    assert out["status"] == "open"


def test_quorum_fails_when_under_50_represented():
    snap = _snap((A, 40), (B, 30), (C, 30))
    ballots = [{"player_id": A, "position": "for"}]
    out = resolve_governance_ballots(
        vote_type="tariff", snapshot=snap, ballots=ballots, rng_seed=1, window_closed=False
    )
    assert out["quorum_ok"] is False
    assert out["status"] == "open"
    # registered absent counts toward quorum
    ballots2 = [
        {"player_id": A, "position": "for"},
        {"player_id": B, "position": "absent"},
    ]
    out2 = resolve_governance_ballots(
        vote_type="tariff", snapshot=snap, ballots=ballots2, rng_seed=1, window_closed=False
    )
    assert out2["quorum_ok"] is True


def test_inactive_halves_voting_power():
    snap = _snap((A, 60), (B, 40), inactive={A: True})
    ballots = [
        {"player_id": A, "position": "for"},
        {"player_id": B, "position": "against"},
    ]
    out = resolve_governance_ballots(
        vote_type="tariff", snapshot=snap, ballots=ballots, rng_seed=1, window_closed=False
    )
    # A counts 30 yes; threshold is 50 of total stake → fail
    assert out["yes_weight"] == 30.0
    assert out["threshold_ok"] is False


def test_veto_by_holder_over_25_on_sale():
    snap = _snap((A, 70), (B, 30))
    ballots = [
        {"player_id": A, "position": "for"},
        {"player_id": B, "position": "veto"},
    ]
    out = resolve_governance_ballots(
        vote_type="sale", snapshot=snap, ballots=ballots, rng_seed=1, window_closed=False
    )
    assert out["status"] == "vetoed"
    assert out["passed"] is False


def test_veto_override_75_of_voting_stake():
    snap = _snap((A, 80), (B, 20))
    # B is 20% — cannot veto (need >25). Use C at 30.
    snap = _snap((A, 70), (B, 30))
    ballots = [
        {"player_id": A, "position": "against_veto"},
        {"player_id": B, "position": "veto"},
    ]
    out = resolve_governance_ballots(
        vote_type="upgrade", snapshot=snap, ballots=ballots, rng_seed=1, window_closed=False
    )
    # voting stake = 70+30=100; against_veto 70% < 75% → still vetoed
    assert out["status"] == "vetoed"
    snap = _snap((A, 80), (B, 20))
    # B cannot veto (20% not >25)
    ballots = [
        {"player_id": A, "position": "for"},
        {"player_id": B, "position": "veto"},
    ]
    out = resolve_governance_ballots(
        vote_type="upgrade", snapshot=snap, ballots=ballots, rng_seed=1, window_closed=False
    )
    assert out["status"] == "passed"


def test_active_26pct_veto_not_overridden_at_integer_100():
    snap = _snap((A, 74), (B, 26))
    ballots = [
        {"player_id": A, "position": "against_veto"},
        {"player_id": B, "position": "veto"},
    ]
    out = resolve_governance_ballots(
        vote_type="sale", snapshot=snap, ballots=ballots, rng_seed=1, window_closed=False
    )
    assert out["status"] == "vetoed"
    assert out["veto_overridden"] is False


def test_veto_override_when_veto_holder_inactive_halved():
    snap = _snap((A, 70), (B, 30), inactive={B: True})
    ballots = [
        {"player_id": A, "position": "against_veto"},
        {"player_id": B, "position": "veto"},
    ]
    out = resolve_governance_ballots(
        vote_type="sale", snapshot=snap, ballots=ballots, rng_seed=1, window_closed=False
    )
    # B still has veto right (raw 30>25). voting = 70+15=85; against_veto 70/85>=0.75
    assert out["veto_overridden"] is True
    assert out["status"] != "vetoed"


def test_tiebreak_highest_stakeholder():
    snap = _snap((A, 40), (B, 35), (C, 25))
    ballots = [
        {"player_id": A, "position": "for"},
        {"player_id": B, "position": "against"},
        {"player_id": C, "position": "against"},
    ]
    # A 40 < 50 threshold; window closed → tiebreak uses A's for → passed
    out = resolve_governance_ballots(
        vote_type="tariff", snapshot=snap, ballots=ballots, rng_seed=7, window_closed=True
    )
    assert out["status"] == "tiebreak"
    assert out["tiebreak_player_id"] == A
    assert out["passed"] is True


def test_tiebreak_top_tie_uses_seed():
    snap = _snap((A, 40), (B, 40), (C, 20))
    ballots = [
        {"player_id": A, "position": "for"},
        {"player_id": B, "position": "against"},
        {"player_id": C, "position": "against"},
    ]
    out1 = resolve_governance_ballots(
        vote_type="tariff", snapshot=snap, ballots=ballots, rng_seed=0, window_closed=True
    )
    out2 = resolve_governance_ballots(
        vote_type="tariff", snapshot=snap, ballots=ballots, rng_seed=1, window_closed=True
    )
    assert out1["status"] == "tiebreak"
    assert out2["status"] == "tiebreak"
    assert out1["tiebreak_player_id"] in {A, B}
    assert out2["tiebreak_player_id"] in {A, B}


def test_route_registered():
    from src.api.routes.station_governance import router

    paths = [getattr(r, "path", None) for r in router.routes]
    assert "/stations/{station_id}/governance/vote" in paths
