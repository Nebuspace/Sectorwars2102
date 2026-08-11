"""NPC trader levy parity — platform fee + region tax (Max canon 2026-08-11).

NPCs never get a free ride: same Class-0 STANDARD_TRANSACTION_FEE and
region tax a player pays on buy/sell. Pins run_trade_stop sell/buy math
without re-simulating the whole FakeSession surface.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from src.models.market_transaction import MarketPrice
from src.models.ship import Ship
from src.models.station import Station
from src.services import docking_service, npc_trading_service
from src.services.trading_service import STANDARD_TRANSACTION_FEE


def _fake_query(rows):
    q = MagicMock()
    q.filter.return_value = q
    q.populate_existing.return_value = q
    q.with_for_update.return_value = q
    q.first.return_value = rows[0] if rows else None
    q.all.return_value = list(rows)
    return q


def test_npc_sell_withholds_platform_fee_like_player():
    """unowned Class-0 port: 0 station tax, 2% platform fee, 0 region tax."""
    station_id = uuid.uuid4()
    ship_id = uuid.uuid4()
    station = SimpleNamespace(
        id=station_id,
        name="Port",
        sector_id=1,
        owner_id=None,
        tax_rate=None,
        tradedock_tier=None,
        region_id=None,
        commodities={"ore": {"buys": True, "sells": False, "quantity": 0, "capacity": 1000}},
        ownership={},
        treasury_balance=0,
        type=None,
    )
    mp = SimpleNamespace(
        station_id=station_id,
        commodity="ore",
        buy_price=100,
        sell_price=120,
        quantity=0,
        last_transaction_at=None,
    )
    ship = SimpleNamespace(
        id=ship_id,
        is_destroyed=False,
        cargo={"capacity": 100, "used": 50, "contents": {"ore": 50}},
    )
    npc = SimpleNamespace(
        id=uuid.uuid4(),
        display_name="Hauler",
        current_sector_id=1,
        ship_id=ship_id,
        credits=0,
        notoriety=0,
        daily_schedule={},
        last_seen_at=None,
    )

    db = MagicMock()

    def query(model):
        if model is Station:
            return _fake_query([station])
        if model is Ship:
            return _fake_query([ship])
        if model is MarketPrice:
            return _fake_query([mp])
        return _fake_query([])

    db.query.side_effect = query
    db.add = MagicMock()
    db.flush = MagicMock()

    with patch.object(docking_service, "acquire_for_npc", return_value=True), patch(
        "src.services.npc_trading_service.flag_modified",
    ), patch(
        "src.services.npc_trading_service.compute_region_tariff_multiplier",
        return_value=(1.0, 0.0),
    ), patch(
        "src.services.npc_trading_service.compute_region_tax_rate",
        return_value=0.0,
    ), patch(
        "src.services.npc_trading_service.TradingService",
    ) as TS:
        TS.return_value.update_market_prices = MagicMock()
        npc_trading_service.run_trade_stop(
            db, npc, {"station_id": str(station_id), "sector_id": 1, "buy_here": []},
        )

    assert STANDARD_TRANSACTION_FEE == 0.02
    # gross 50*100=5000; fee int(5000*0.02)=100; net=4900
    assert npc.credits == 4900
    assert ship.cargo["contents"] == {}


def test_npc_buy_charges_platform_fee_like_player():
    station_id = uuid.uuid4()
    ship_id = uuid.uuid4()
    station = SimpleNamespace(
        id=station_id,
        name="Port",
        sector_id=1,
        owner_id=None,
        tax_rate=None,
        tradedock_tier=None,
        region_id=None,
        commodities={
            "ore": {"buys": False, "sells": True, "quantity": 100, "capacity": 1000},
        },
        ownership={},
        treasury_balance=0,
        type=None,
    )
    mp = SimpleNamespace(
        station_id=station_id,
        commodity="ore",
        buy_price=80,
        sell_price=100,
        quantity=100,
        last_transaction_at=None,
    )
    ship = SimpleNamespace(
        id=ship_id,
        is_destroyed=False,
        cargo={"capacity": 100, "used": 0, "contents": {}},
    )
    npc = SimpleNamespace(
        id=uuid.uuid4(),
        display_name="Hauler",
        current_sector_id=1,
        ship_id=ship_id,
        credits=10_200,  # exactly 100 units @ 100 + 2% fee
        notoriety=0,
        daily_schedule={},
        last_seen_at=None,
    )

    db = MagicMock()

    def query(model):
        if model is Station:
            return _fake_query([station])
        if model is Ship:
            return _fake_query([ship])
        if model is MarketPrice:
            return _fake_query([mp])
        return _fake_query([])

    db.query.side_effect = query
    db.add = MagicMock()
    db.flush = MagicMock()

    with patch.object(docking_service, "acquire_for_npc", return_value=True), patch(
        "src.services.npc_trading_service.flag_modified",
    ), patch(
        "src.services.npc_trading_service.compute_region_tariff_multiplier",
        return_value=(1.0, 0.0),
    ), patch(
        "src.services.npc_trading_service.compute_region_tax_rate",
        return_value=0.0,
    ), patch(
        "src.services.npc_trading_service.TradingService",
    ) as TS:
        TS.return_value.update_market_prices = MagicMock()
        npc_trading_service.run_trade_stop(
            db,
            npc,
            {
                "station_id": str(station_id),
                "sector_id": 1,
                "buy_here": ["ore"],
            },
        )

    # 100*100=10000 goods + 200 fee = 10200
    assert npc.credits == 0
    assert ship.cargo["contents"]["ore"] == 100
