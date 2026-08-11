"""Unit tests for regional docking-fee subsidy + arbitrage stipend levers."""
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

from src.services import regional_activity_levers_service as levers


def test_docking_fee_rebate_half_and_cap():
    assert levers.docking_fee_rebate(100) == 50
    assert levers.docking_fee_rebate(1_000) == levers.DOCKING_SUBSIDY_CAP
    assert levers.docking_fee_rebate(0) == 0


def test_apply_lever_flags_toggles_trade_bonuses():
    region = SimpleNamespace(trade_bonuses={})
    applied = levers.apply_lever_flags(region, {
        levers.LEVER_DOCKING_FEE_SUBSIDY: True,
        levers.LEVER_ARBITRAGE_STIPEND: True,
    })
    assert region.trade_bonuses[levers.LEVER_DOCKING_FEE_SUBSIDY] is True
    assert region.trade_bonuses[levers.LEVER_ARBITRAGE_STIPEND] is True
    assert levers.LEVER_DOCKING_FEE_SUBSIDY in applied


def test_docking_subsidy_pays_resident_from_treasury():
    rid = uuid4()
    pid = uuid4()
    region = SimpleNamespace(
        id=rid,
        treasury_balance=10_000,
        trade_bonuses={levers.LEVER_DOCKING_FEE_SUBSIDY: True},
    )
    player = SimpleNamespace(id=pid, home_region_id=rid, credits=1_000)
    db = MagicMock()
    net, rebate = levers.apply_docking_fee_subsidy(db, region, player, 200)
    assert rebate == 100
    assert net == 100
    assert region.treasury_balance == 9_900
    assert db.add.called


def test_docking_subsidy_skips_non_resident():
    region = SimpleNamespace(
        id=uuid4(),
        treasury_balance=10_000,
        trade_bonuses={levers.LEVER_DOCKING_FEE_SUBSIDY: True},
    )
    player = SimpleNamespace(id=uuid4(), home_region_id=uuid4(), credits=1_000)
    db = MagicMock()
    net, rebate = levers.apply_docking_fee_subsidy(db, region, player, 200)
    assert rebate == 0
    assert net == 200
    assert region.treasury_balance == 10_000


def test_docking_subsidy_auto_suspends_on_empty_treasury():
    rid = uuid4()
    region = SimpleNamespace(
        id=rid,
        treasury_balance=10,
        trade_bonuses={levers.LEVER_DOCKING_FEE_SUBSIDY: True},
    )
    player = SimpleNamespace(id=uuid4(), home_region_id=rid, credits=1_000)
    db = MagicMock()
    net, rebate = levers.apply_docking_fee_subsidy(db, region, player, 200)
    assert rebate == 0
    assert net == 200
    assert region.treasury_balance == 10


def test_arbitrage_stipend_round_trip_and_daily_cap():
    rid = uuid4()
    foreign = uuid4()
    region = SimpleNamespace(
        id=rid,
        treasury_balance=50_000,
        trade_bonuses={levers.LEVER_ARBITRAGE_STIPEND: True},
    )
    player = SimpleNamespace(
        id=uuid4(),
        home_region_id=rid,
        credits=0,
        settings={},
    )
    db = MagicMock()
    levers.note_foreign_region_visit(player, foreign)
    r1 = levers.try_pay_arbitrage_stipend(db, region, player)
    assert r1["paid"] == 500
    assert player.credits == 500
    assert region.treasury_balance == 49_500

    levers.note_foreign_region_visit(player, foreign)
    r2 = levers.try_pay_arbitrage_stipend(db, region, player)
    assert r2["paid"] == 500
    assert player.credits == 1_000

    levers.note_foreign_region_visit(player, foreign)
    r3 = levers.try_pay_arbitrage_stipend(db, region, player)
    assert r3["paid"] == 0
    assert r3["skipped"] == "daily_cap"
    assert player.credits == 1_000
