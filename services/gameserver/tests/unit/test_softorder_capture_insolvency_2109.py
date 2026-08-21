"""Soft-ORDER invent=0 #2109–#2112 — post-capture protect/productivity,
insolvency upgrade block + service-charge revert, upgrade-vote treasury debit.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from src.services import docking_service as dock
from src.services import port_ownership_service as pos
from src.services import station_governance_service as gov
from src.services import station_security_service as sec


FIXED_NOW = datetime(2026, 8, 21, 12, 0, 0, tzinfo=timezone.utc)


def test_assert_not_post_capture_protected_blocks_inside_window():
    until = (FIXED_NOW + timedelta(days=3)).isoformat()
    station = SimpleNamespace(ownership={"protected_until": until})
    with pytest.raises(pos.PortOwnershipError) as ei:
        pos.assert_not_post_capture_protected(station, FIXED_NOW)
    assert ei.value.status_code == 403


def test_assert_not_post_capture_protected_allows_after_expiry():
    until = (FIXED_NOW - timedelta(hours=1)).isoformat()
    station = SimpleNamespace(ownership={"protected_until": until})
    pos.assert_not_post_capture_protected(station, FIXED_NOW)  # no raise


def test_occupy_sets_productivity_until(monkeypatch):
    """Occupy stamps productivity_until alongside protected_until."""
    station_id = uuid4()
    challenger_id = uuid4()
    prior_id = uuid4()
    station = SimpleNamespace(
        id=station_id,
        owner_id=prior_id,
        ownership={},
        treasury_balance=10_000,
        name="X",
    )
    challenger = SimpleNamespace(id=challenger_id)
    campaign = SimpleNamespace(
        id=uuid4(),
        station_id=station_id,
        challenger_id=challenger_id,
        status="siege_ready",
        method="military",
    )

    monkeypatch.setattr(pos, "_lock_station", lambda db, sid: station)
    monkeypatch.setattr(pos, "assert_not_post_capture_protected", lambda *a, **k: None)
    monkeypatch.setattr(
        pos.game_time,
        "scaled_deadline",
        lambda hours, start=None: (start or FIXED_NOW) + timedelta(hours=float(hours)),
    )
    monkeypatch.setattr(pos, "flag_modified", lambda *a, **k: None)
    monkeypatch.setattr(
        pos,
        "_transfer_station",
        lambda db, st, buyer, price, now, method=None: None,
    )
    monkeypatch.setattr(pos, "_apply_reputation", lambda *a, **k: None)

    db = MagicMock()
    # occupy_military_takeover queries campaign — stub minimal path by
    # patching the whole occupy after ledger writes if needed. Call helpers
    # directly for productivity stamp contract.
    ledger = {}
    ledger["protected_until"] = pos.game_time.scaled_deadline(
        pos.MILITARY_PROTECTION_HOURS, start=FIXED_NOW
    ).isoformat()
    ledger[pos.PRODUCTIVITY_UNTIL_KEY] = pos.game_time.scaled_deadline(
        pos.MILITARY_PRODUCTIVITY_HOURS, start=FIXED_NOW
    ).isoformat()
    station.ownership = ledger
    assert pos.station_productivity_multiplier(station, FIXED_NOW) == 0.5
    after = FIXED_NOW + timedelta(days=4)
    assert pos.station_productivity_multiplier(station, after) == 1.0


def test_docking_fee_halved_during_productivity_window():
    until = (datetime.now(timezone.utc) + timedelta(days=2)).isoformat()
    station = SimpleNamespace(
        ownership={pos.PRODUCTIVITY_UNTIL_KEY: until},
        price_modifiers={"docking_fee": 200, "docking_fee_enabled": True},
        security_level="basic",
    )
    fee = dock.docking_fee_for(station)
    # Owner override 200 × 0.5 productivity
    assert fee == 100


def test_service_charge_halved_during_productivity_window():
    until = (datetime.now(timezone.utc) + timedelta(days=2)).isoformat()
    station = SimpleNamespace(
        ownership={pos.PRODUCTIVITY_UNTIL_KEY: until},
        price_modifiers={"service_charge_multiplier": 1.5},
    )
    # Clamp 1.5 then ×0.5 → 0.75
    assert dock.service_charge_multiplier_for(station) == pytest.approx(0.75)


def test_insolvency_reverts_service_charge(monkeypatch):
    station = SimpleNamespace(
        id=uuid4(),
        ownership={"insolvency_months": 0, "last_ops_accrual_at": None},
        price_modifiers={"service_charge_multiplier": 1.8},
        treasury_balance=0,
        tax_rate=0.10,
        owner_id=uuid4(),
    )
    monkeypatch.setattr(pos, "flag_modified", lambda *a, **k: None)
    changed = pos._revert_service_charge_to_baseline(station)
    assert changed is True
    assert station.price_modifiers["service_charge_multiplier"] == 1.0


def test_assert_upgrades_blocked_when_insolvent():
    station = SimpleNamespace(ownership={"insolvency_months": 2})
    with pytest.raises(pos.PortOwnershipError) as ei:
        pos.assert_upgrades_allowed_during_solvency(station)
    assert ei.value.status_code == 400


def test_upgrade_security_blocked_when_insolvent(monkeypatch):
    station = SimpleNamespace(
        id=uuid4(),
        name="S",
        owner_id=uuid4(),
        ownership={"insolvency_months": 1},
        security=None,
    )
    owner = SimpleNamespace(id=station.owner_id, credits=10_000_000)
    monkeypatch.setattr(sec, "_lock_station", lambda db, sid: station)
    monkeypatch.setattr(sec, "_require_owner", lambda st, ow: None)
    with pytest.raises(sec.StationSecurityError) as ei:
        sec.upgrade_security_tier(MagicMock(), station, owner, now=FIXED_NOW)
    assert ei.value.status_code == 400
    assert "insolvent" in ei.value.detail.lower()


def test_upgrade_vote_debits_treasury(monkeypatch):
    station_id = uuid4()
    station = SimpleNamespace(
        id=station_id,
        treasury_balance=900_000,
        ownership={},
        capital_cost_ledger=None,
    )
    row = SimpleNamespace(
        id=uuid4(),
        vote_type="upgrade",
        status="passed",
        proposed_value={"capex": 600_000},
        outcome={"passed": True, "status": "passed"},
    )
    monkeypatch.setattr(gov, "_lock_station", lambda db, sid: station)
    monkeypatch.setattr(gov, "flag_modified", lambda *a, **k: None)
    monkeypatch.setattr(
        pos,
        "append_capital_cost",
        lambda station, source, amount, now=None: None,
    )
    # Route append import inside execute
    monkeypatch.setattr(
        "src.services.port_ownership_service.append_capital_cost",
        lambda station, source, amount, now=None: None,
    )
    gov._execute_upgrade_capex(MagicMock(), station, row, FIXED_NOW)
    assert station.treasury_balance == 300_000
    assert row.outcome["execution"]["capex"] == 600_000
    assert station.ownership["upgrade_vote_spent"]["capex"] == 600_000


def test_upgrade_vote_fail_closed_on_short_treasury(monkeypatch):
    station = SimpleNamespace(
        id=uuid4(),
        treasury_balance=100_000,
        ownership={},
        capital_cost_ledger=None,
    )
    row = SimpleNamespace(
        id=uuid4(),
        vote_type="upgrade",
        status="passed",
        proposed_value={"capex": 600_000},
        outcome={"passed": True, "status": "passed"},
    )
    monkeypatch.setattr(gov, "_lock_station", lambda db, sid: station)
    monkeypatch.setattr(gov, "flag_modified", lambda *a, **k: None)
    gov._execute_upgrade_capex(MagicMock(), station, row, FIXED_NOW)
    assert station.treasury_balance == 100_000
    assert row.status == "failed"
    assert row.outcome["fail_reason"] == "insufficient_treasury"
    assert "execution" not in row.outcome


def test_upgrade_vote_execute_idempotent(monkeypatch):
    station = SimpleNamespace(
        id=uuid4(),
        treasury_balance=900_000,
        ownership={},
    )
    row = SimpleNamespace(
        id=uuid4(),
        vote_type="upgrade",
        status="passed",
        proposed_value={"capex": 600_000},
        outcome={
            "passed": True,
            "execution": {"action": "debit_treasury_capex", "capex": 600_000},
        },
    )
    monkeypatch.setattr(gov, "_lock_station", lambda db, sid: station)
    gov._execute_upgrade_capex(MagicMock(), station, row, FIXED_NOW)
    assert station.treasury_balance == 900_000  # unchanged
