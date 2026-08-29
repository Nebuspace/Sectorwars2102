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
    # Disbandment mirrors sale (vote table silent; Soft-ORDER invent=0).
    assert VOTE_SPECS["disbandment"]["threshold"] == 0.66
    assert VOTE_SPECS["disbandment"]["veto"] is True
    assert VOTE_SPECS["disbandment"]["window_hours"] == 96.0
    assert VOTE_SPECS["withdrawal"]["threshold"] == 0.50
    assert VOTE_SPECS["withdrawal"]["veto"] is False


def test_normalize_aliases():
    assert normalize_vote_type("tariff_change") == "tariff"
    assert normalize_vote_type("major_upgrade") == "upgrade"
    assert normalize_vote_type("withdrawal-schedule") == "withdrawal"
    assert normalize_vote_type("disband") == "disbandment"


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


# --- LEG-2014 Soft-ORDER: withdrawal vote persists schedule + sweep ---


def test_apply_passed_withdrawal_sets_schedule(monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import MagicMock
    from uuid import uuid4
    from datetime import datetime, timezone

    from src.services import station_governance_service as gov
    from src.services import port_ownership_service as pos

    station = SimpleNamespace(id=uuid4(), ownership={}, treasury_balance=0)
    row = SimpleNamespace(
        id=uuid4(),
        vote_type="withdrawal",
        proposed_value={"schedule": "weekly"},
        outcome={"status": "passed", "passed": True},
    )
    monkeypatch.setattr(gov, "_lock_station", lambda db, sid: station)
    monkeypatch.setattr(gov, "flag_modified", lambda *a, **k: None)
    monkeypatch.setattr(pos, "flag_modified", lambda *a, **k: None)

    now = datetime.now(timezone.utc)
    gov._apply_passed_vote(MagicMock(), station, row, row.outcome, now=now)
    assert station.ownership.get("withdrawal_schedule") == "weekly"
    assert row.outcome["execution"]["action"] == "set_withdrawal_schedule"
    assert row.outcome["execution"]["schedule"] == "weekly"

    # idempotent — second apply does not clear / rewrite
    station.ownership["withdrawal_schedule"] = "weekly"
    gov._apply_passed_vote(MagicMock(), station, row, row.outcome, now=now)
    assert station.ownership.get("withdrawal_schedule") == "weekly"


def test_apply_passed_withdrawal_skips_bad_schedule(monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import MagicMock
    from uuid import uuid4

    from src.services import station_governance_service as gov

    station = SimpleNamespace(id=uuid4(), ownership={})
    row = SimpleNamespace(
        id=uuid4(),
        vote_type="withdrawal",
        proposed_value={"schedule": "hourly"},
        outcome={"status": "passed", "passed": True},
    )
    monkeypatch.setattr(gov, "_lock_station", lambda db, sid: station)

    gov._apply_passed_vote(MagicMock(), station, row, row.outcome)
    assert station.ownership.get("withdrawal_schedule") is None
    assert "execution" not in (row.outcome or {})


def test_maybe_run_scheduled_withdrawal_honors_cadence_and_cushion(monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import MagicMock
    from uuid import uuid4
    from datetime import datetime, timedelta, timezone

    from src.services import port_ownership_service as pos

    owner_id = uuid4()
    station_id = uuid4()
    now = datetime.now(timezone.utc)
    set_at = (now - timedelta(days=8)).isoformat()
    station = SimpleNamespace(
        id=station_id,
        owner_id=owner_id,
        treasury_balance=1000,
        ownership={
            "withdrawal_schedule": "weekly",
            "withdrawal_schedule_set_at": set_at,
            "co_ownership_mode": "solo",
        },
    )
    owner = SimpleNamespace(id=owner_id, credits=0)

    monkeypatch.setattr(pos, "_lock_station", lambda db, sid: station)
    monkeypatch.setattr(
        pos, "_lock_players_ascending", lambda db, pids: {owner_id: owner}
    )
    monkeypatch.setattr(pos, "flag_modified", lambda *a, **k: None)
    # Force cadence elapsed regardless of GAME_TIME_SCALE.
    monkeypatch.setattr(
        pos.game_time, "scaled_elapsed", lambda last, n: timedelta(days=8)
    )

    result = pos.maybe_run_scheduled_withdrawal(MagicMock(), station, now)
    assert result is not None
    assert result["status"] == "swept"
    assert result["amount"] == 900  # 90% cushion cap
    assert station.treasury_balance == 100
    assert owner.credits == 900
    assert station.ownership.get("withdrawal_schedule_last_at")


def test_maybe_run_scheduled_withdrawal_noop_before_cadence(monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import MagicMock
    from uuid import uuid4
    from datetime import datetime, timedelta, timezone

    from src.services import port_ownership_service as pos

    now = datetime.now(timezone.utc)
    station = SimpleNamespace(
        id=uuid4(),
        owner_id=uuid4(),
        treasury_balance=1000,
        ownership={
            "withdrawal_schedule": "weekly",
            "withdrawal_schedule_set_at": now.isoformat(),
        },
    )
    monkeypatch.setattr(
        pos.game_time, "scaled_elapsed", lambda last, n: timedelta(days=1)
    )
    result = pos.maybe_run_scheduled_withdrawal(MagicMock(), station, now)
    assert result is None
    assert station.treasury_balance == 1000
