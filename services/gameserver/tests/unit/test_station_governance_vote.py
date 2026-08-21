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
    # Disbandment mirrors sale (table silent — LEG-2008)
    assert VOTE_SPECS["disbandment"] == VOTE_SPECS["sale"]
    assert VOTE_SPECS["withdrawal"]["threshold"] == 0.50
    assert VOTE_SPECS["withdrawal"]["veto"] is False


def test_normalize_aliases():
    assert normalize_vote_type("tariff_change") == "tariff"
    assert normalize_vote_type("major_upgrade") == "upgrade"
    assert normalize_vote_type("withdrawal-schedule") == "withdrawal"
    assert normalize_vote_type("disband") == "disbandment"
    assert normalize_vote_type("dissolve") == "disbandment"


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


# --- LEG-2007 / LEG-2008 Soft-ORDER: execute on passed sale / disbandment ---


def test_execute_sale_lists_when_no_buyer(monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import MagicMock
    from uuid import uuid4

    from src.services import station_governance_service as gov

    station_id = uuid4()
    owner_id = uuid4()
    station = SimpleNamespace(
        id=station_id,
        owner_id=owner_id,
        ownership={
            "player_id": str(owner_id),
            "acquisition_cost": 1_000_000,
            "co_ownership_mode": "syndicate",
            "co_ownership_shares": [
                {"player_id": str(owner_id), "pct": 60, "primary": True},
                {"player_id": str(uuid4()), "pct": 40},
            ],
        },
    )
    row = SimpleNamespace(
        vote_type="sale",
        proposed_value={"value": None},
        outcome={"status": "passed", "passed": True},
    )
    listing = SimpleNamespace(id=uuid4())
    db = MagicMock()
    calls = {"list": 0, "transfer": 0, "clear": 0}

    monkeypatch.setattr(gov, "_cancel_open_campaigns", lambda *a, **k: None)
    monkeypatch.setattr(
        gov,
        "_clear_ownership_for_resale",
        lambda *a, **k: calls.__setitem__("clear", calls["clear"] + 1),
    )
    monkeypatch.setattr(gov, "is_listable", lambda s: True)
    monkeypatch.setattr(
        gov,
        "list_station",
        lambda db, station, price=None, now=None: (
            calls.__setitem__("list", calls["list"] + 1) or listing
        ),
    )
    monkeypatch.setattr(
        gov,
        "_transfer_station",
        lambda *a, **k: calls.__setitem__("transfer", calls["transfer"] + 1),
    )
    monkeypatch.setattr(gov, "flag_modified", lambda *a, **k: None)
    monkeypatch.setattr(gov, "_acquisition_cost", lambda s: 1_000_000)

    from datetime import datetime, timezone

    gov._execute_passed_vote(db, station, row, datetime.now(timezone.utc))
    assert calls["list"] == 1
    assert calls["clear"] == 1
    assert calls["transfer"] == 0
    assert row.outcome["execution"]["action"] == "list"
    assert row.outcome["execution"]["listing_id"] == str(listing.id)

    # idempotent
    gov._execute_passed_vote(db, station, row, datetime.now(timezone.utc))
    assert calls["list"] == 1


def test_execute_sale_transfers_when_buyer_present(monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import MagicMock
    from uuid import uuid4

    from src.services import station_governance_service as gov

    buyer_id = uuid4()
    station = SimpleNamespace(
        id=uuid4(),
        owner_id=uuid4(),
        ownership={"acquisition_cost": 800_000},
    )
    buyer = SimpleNamespace(id=buyer_id)
    row = SimpleNamespace(
        vote_type="sale",
        proposed_value={"buyer_id": str(buyer_id), "price": 750_000},
        outcome={"status": "passed", "passed": True},
    )
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = buyer
    transferred = {}

    monkeypatch.setattr(gov, "_cancel_open_campaigns", lambda *a, **k: None)
    monkeypatch.setattr(
        gov, "_lock_players_ascending", lambda db, ids: {buyer_id: buyer}
    )
    monkeypatch.setattr(
        gov,
        "_transfer_station",
        lambda db, station, new_owner, price, now, method: transferred.update(
            {"buyer": new_owner.id, "price": price, "method": method}
        ),
    )
    monkeypatch.setattr(gov, "flag_modified", lambda *a, **k: None)
    monkeypatch.setattr(gov, "_acquisition_cost", lambda s: 800_000)
    monkeypatch.setattr(
        gov, "list_station", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no list"))
    )

    from datetime import datetime, timezone

    gov._execute_passed_vote(db, station, row, datetime.now(timezone.utc))
    assert transferred["buyer"] == buyer_id
    assert transferred["price"] == 750_000
    assert transferred["method"] == "governance_sale"
    assert row.outcome["execution"]["action"] == "transfer"


def test_execute_disbandment_lists_at_depreciated(monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import MagicMock
    from uuid import uuid4

    from src.services import station_governance_service as gov

    station = SimpleNamespace(
        id=uuid4(),
        owner_id=uuid4(),
        ownership={"acquisition_cost": 1_000_000},
    )
    row = SimpleNamespace(
        vote_type="disbandment",
        proposed_value={},
        outcome={"status": "passed", "passed": True},
    )
    listing = SimpleNamespace(id=uuid4())
    listed_price = {}

    monkeypatch.setattr(gov, "_cancel_open_campaigns", lambda *a, **k: None)
    monkeypatch.setattr(gov, "_clear_ownership_for_resale", lambda *a, **k: None)
    monkeypatch.setattr(gov, "is_listable", lambda s: True)
    monkeypatch.setattr(gov, "_acquisition_cost", lambda s: 1_000_000)
    monkeypatch.setattr(gov, "depreciated_value", lambda acq: int(acq * 0.5))
    monkeypatch.setattr(
        gov,
        "list_station",
        lambda db, station, price=None, now=None: (
            listed_price.update({"price": price}) or listing
        ),
    )
    monkeypatch.setattr(gov, "flag_modified", lambda *a, **k: None)
    monkeypatch.setattr(
        gov,
        "_transfer_station",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("no transfer")),
    )

    from datetime import datetime, timezone

    gov._execute_passed_vote(db := MagicMock(), station, row, datetime.now(timezone.utc))
    assert listed_price["price"] == 500_000
    assert row.outcome["execution"]["action"] == "depreciated_auto_sell"
    assert row.outcome["execution"]["depreciated_value"] == 500_000


def test_execute_skips_non_passed_sale(monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import MagicMock
    from uuid import uuid4
    from datetime import datetime, timezone

    from src.services import station_governance_service as gov

    row = SimpleNamespace(
        vote_type="sale",
        proposed_value={},
        outcome={"status": "open", "passed": False},
    )
    called = {"n": 0}
    monkeypatch.setattr(
        gov, "list_station", lambda *a, **k: called.__setitem__("n", called["n"] + 1)
    )
    gov._execute_passed_vote(
        MagicMock(),
        SimpleNamespace(id=uuid4(), ownership={}),
        row,
        datetime.now(timezone.utc),
    )
    assert called["n"] == 0
    assert "execution" not in (row.outcome or {})


# --- LEG-2013 Soft-ORDER: tariff vote executes set_tax ---


def test_execute_tariff_sets_tax_rate(monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import MagicMock
    from uuid import uuid4
    from datetime import datetime, timezone

    from src.services import station_governance_service as gov

    station_id = uuid4()
    station = SimpleNamespace(id=station_id, tax_rate=0.10, ownership={})
    row = SimpleNamespace(
        vote_type="tariff",
        proposed_value={"tax_rate": 0.15},
        outcome={"status": "passed", "passed": True},
    )
    db = MagicMock()
    monkeypatch.setattr(gov, "_lock_station", lambda db, sid: station)
    monkeypatch.setattr(gov, "flag_modified", lambda *a, **k: None)
    monkeypatch.setattr(gov, "_acquisition_cost", lambda s: 0)

    gov._execute_passed_vote(db, station, row, datetime.now(timezone.utc))
    assert station.tax_rate == 0.15
    assert row.outcome["execution"]["action"] == "set_tax"
    assert row.outcome["execution"]["prior_tax_rate"] == 0.10
    assert row.outcome["execution"]["tax_rate"] == 0.15

    # idempotent
    gov._execute_passed_vote(db, station, row, datetime.now(timezone.utc))
    assert station.tax_rate == 0.15


def test_execute_tariff_rejects_out_of_bounds(monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import MagicMock
    from uuid import uuid4
    from datetime import datetime, timezone
    import pytest

    from src.services import station_governance_service as gov
    from src.services.port_ownership_service import PortOwnershipError

    station = SimpleNamespace(id=uuid4(), tax_rate=0.10, ownership={})
    row = SimpleNamespace(
        vote_type="tariff",
        proposed_value={"rate": 0.50},
        outcome={"status": "passed", "passed": True},
    )
    monkeypatch.setattr(gov, "_lock_station", lambda db, sid: station)
    monkeypatch.setattr(gov, "_acquisition_cost", lambda s: 0)

    with pytest.raises(PortOwnershipError):
        gov._execute_passed_vote(
            MagicMock(), station, row, datetime.now(timezone.utc)
        )
    assert station.tax_rate == 0.10
    assert "execution" not in (row.outcome or {})



# --- LEG-2014 Soft-ORDER: withdrawal vote persists schedule ---


def test_execute_withdrawal_sets_schedule(monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import MagicMock
    from uuid import uuid4
    from datetime import datetime, timezone

    from src.services import station_governance_service as gov
    from src.services import port_ownership_service as pos

    station_id = uuid4()
    station = SimpleNamespace(
        id=station_id,
        ownership={},
        treasury_balance=0,
    )
    row = SimpleNamespace(
        vote_type="withdrawal",
        proposed_value={"schedule": "weekly"},
        outcome={"status": "passed", "passed": True},
    )
    db = MagicMock()
    monkeypatch.setattr(gov, "_lock_station", lambda db, sid: station)
    monkeypatch.setattr(gov, "_acquisition_cost", lambda s: 0)
    monkeypatch.setattr(gov, "flag_modified", lambda *a, **k: None)
    monkeypatch.setattr(pos, "flag_modified", lambda *a, **k: None)

    gov._execute_passed_vote(db, station, row, datetime.now(timezone.utc))
    assert station.ownership.get("withdrawal_schedule") == "weekly"
    assert row.outcome["execution"]["action"] == "set_withdrawal_schedule"
    assert row.outcome["execution"]["schedule"] == "weekly"

    # idempotent
    gov._execute_passed_vote(db, station, row, datetime.now(timezone.utc))
    assert station.ownership.get("withdrawal_schedule") == "weekly"


def test_execute_withdrawal_rejects_bad_schedule(monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import MagicMock
    from uuid import uuid4
    from datetime import datetime, timezone
    import pytest

    from src.services import station_governance_service as gov
    from src.services.port_ownership_service import PortOwnershipError

    station = SimpleNamespace(id=uuid4(), ownership={})
    row = SimpleNamespace(
        vote_type="withdrawal",
        proposed_value={"schedule": "hourly"},
        outcome={"status": "passed", "passed": True},
    )
    monkeypatch.setattr(gov, "_lock_station", lambda db, sid: station)
    monkeypatch.setattr(gov, "_acquisition_cost", lambda s: 0)

    with pytest.raises(PortOwnershipError):
        gov._execute_passed_vote(
            MagicMock(), station, row, datetime.now(timezone.utc)
        )
    assert "execution" not in (row.outcome or {})


# --- LEG-2015 Soft-ORDER: 90-day inactive stake forfeit ---


def test_inactive_stake_forfeit_rebalances_active(monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import MagicMock
    from uuid import uuid4
    from datetime import datetime, timedelta, timezone

    from src.services import port_ownership_service as pos

    primary = uuid4()
    inactive = uuid4()
    active = uuid4()
    now = datetime.now(timezone.utc)
    station = SimpleNamespace(
        id=uuid4(),
        owner_id=primary,
        ownership={
            "co_ownership_mode": "syndicate",
            "co_ownership_shares": [
                {"player_id": str(primary), "pct": 40, "primary": True},
                {"player_id": str(inactive), "pct": 30},
                {"player_id": str(active), "pct": 30},
            ],
            "co_ownership_invites": [],
        },
    )

    class _Q:
        def __init__(self, rows):
            self._rows = rows

        def filter(self, *a, **k):
            return self

        def all(self):
            return self._rows

    players = [
        SimpleNamespace(id=primary, last_game_login=now),
        SimpleNamespace(
            id=inactive, last_game_login=now - timedelta(days=100)
        ),
        SimpleNamespace(id=active, last_game_login=now),
    ]
    db = MagicMock()
    db.query.return_value = _Q(players)

    monkeypatch.setattr(
        pos.game_time,
        "scaled_elapsed",
        lambda start, end: end - start,
    )
    monkeypatch.setattr(pos, "flag_modified", lambda *a, **k: None)

    result = pos.apply_inactive_stake_forfeits(db, station, now)
    assert len(result["forfeited"]) == 1
    assert result["forfeited"][0]["player_id"] == str(inactive)
    assert result["mode"] == "syndicate"
    share_map = {s["player_id"]: s["pct"] for s in result["shares"]}
    assert str(inactive) not in share_map
    assert share_map[str(primary)] + share_map[str(active)] == 100


# --- LEG-2012 Soft-ORDER: buyout → solo at fair value ---


def test_buyout_syndicate_to_solo_pays_others(monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import MagicMock
    from uuid import uuid4
    from datetime import datetime, timezone

    from src.services import port_ownership_service as pos

    buyer_id = uuid4()
    other_id = uuid4()
    station_id = uuid4()
    now = datetime.now(timezone.utc)
    station = SimpleNamespace(
        id=station_id,
        owner_id=other_id,
        ownership={
            "co_ownership_mode": "syndicate",
            "acquisition_cost": 1_000_000,
            "co_ownership_shares": [
                {"player_id": str(other_id), "pct": 60, "primary": True},
                {"player_id": str(buyer_id), "pct": 40},
            ],
            "co_ownership_invites": [],
        },
        treasury_balance=0,
    )
    buyer = SimpleNamespace(id=buyer_id, credits=800_000)
    other = SimpleNamespace(id=other_id, credits=0)

    db = MagicMock()
    monkeypatch.setattr(pos, "_lock_station", lambda db, sid: station)
    monkeypatch.setattr(pos, "apply_inactive_stake_forfeits", lambda *a, **k: {"forfeited": []})
    monkeypatch.setattr(pos, "forced_sale_price", lambda *a, **k: 500_000)
    monkeypatch.setattr(
        pos,
        "_lock_players_ascending",
        lambda db, ids: {buyer_id: buyer, other_id: other},
    )
    monkeypatch.setattr(pos, "flag_modified", lambda *a, **k: None)
    # association table helpers
    monkeypatch.setattr(pos, "player_stations", MagicMock())
    db.execute = MagicMock()

    result = pos.buyout_syndicate_to_solo(db, station, buyer, now)
    assert result["mode"] == "solo"
    assert result["fair_value"] == 500_000
    # other owns 60% → due 300_000
    assert result["paid_total"] == 300_000
    assert buyer.credits == 800_000 - 300_000
    assert other.credits == 300_000
    assert station.owner_id == buyer_id
    assert station.ownership.get("co_ownership_mode") == "solo"
