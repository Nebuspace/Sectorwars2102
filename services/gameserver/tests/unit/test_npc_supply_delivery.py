"""LEG-394 — visible NPC supply-delivery restock.

Pins low-stock detection + hauler spawn schedule/cargo without a live DB.
Sell-into-stock math is already covered by test_npc_trader_fee_parity
(run_trade_stop); this file pins the dispatch gap that fee_parity does not.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from src.models.npc_character import NPCArchetype, NPCCharacter, NPCStatus
from src.models.sector import Sector
from src.models.ship import ShipSpecification, ShipType
from src.models.station import Station
from src.services import npc_trading_service


def test_iter_low_stock_deficits_uses_ratio_and_demand():
    station = SimpleNamespace(
        commodities={
            "ore": {
                "buys": True,
                "sells": False,
                "quantity": 100,
                "capacity": 1000,
                "npc_restock_demand": 1.0,
            },
            "fuel": {
                "buys": True,
                "sells": False,
                "quantity": 900,
                "capacity": 1000,
                "npc_restock_demand": 1.5,
            },
            "food": {
                "buys": False,
                "sells": True,
                "quantity": 10,
                "capacity": 1000,
                "npc_restock_demand": 2.0,
            },
        }
    )
    deficits = {d["commodity"]: d for d in npc_trading_service.iter_low_stock_deficits(station)}
    assert "ore" in deficits  # ratio 0.1 <= DEFICIT_RATIO
    assert deficits["ore"]["fill"] > 0
    assert "fuel" in deficits  # demand 1.5 >= threshold even if ratio high
    assert "food" not in deficits  # station does not buy food


def test_build_supply_delivery_schedule_is_sell_only():
    sched = npc_trading_service.build_supply_delivery_schedule(
        station_id="abc",
        sector_id=42,
        commodity="ore",
        quantity=25,
    )
    assert sched["mission"] == npc_trading_service.SUPPLY_DELIVERY_MISSION
    assert sched["supply_delivery"]["commodity"] == "ore"
    assert sched["trade_route"][0]["buy_here"] == []
    block = sched["route_cycle"]["days"]["0"][0]
    assert block["activity"] == "work_station"
    assert block["location_ref"]["buy_here"] == []


def test_dispatch_visible_supply_delivery_spawns_cargo_hauler():
    """Empty-sector spawn carries deficit goods + sell-only schedule."""
    station_id = uuid.uuid4()
    region_id = uuid.uuid4()
    station = SimpleNamespace(
        id=station_id,
        name="Fuel Depot",
        sector_id=200,
        commodities={"ore": {"buys": True, "quantity": 50, "capacity": 1000}},
    )
    station_sector = SimpleNamespace(
        sector_id=200, region_id=region_id, players_present=[],
    )
    empty_sector = SimpleNamespace(
        sector_id=999, region_id=region_id, players_present=[],
    )
    spec = SimpleNamespace(type=ShipType.CARGO_HAULER, max_cargo=80)

    db = MagicMock()
    added = []

    def query(model):
        q = MagicMock()
        q.filter.return_value = q
        q.first.return_value = None
        q.all.return_value = []
        if model is Sector:
            # first call: station sector lookup; later: empty sectors /
            # presence helpers may re-query — return both.
            q.first.return_value = station_sector
            q.all.return_value = [station_sector, empty_sector]
            return q
        if model is Station:
            q.all.return_value = [SimpleNamespace(sector_id=200)]
            return q
        if model is ShipSpecification:
            q.first.return_value = spec
            return q
        if model is NPCCharacter:
            q.all.return_value = []
            return q
        return q

    db.query.side_effect = query

    def _add(obj):
        added.append(obj)
        if getattr(obj, "id", None) is None:
            obj.id = uuid.uuid4()

    db.add.side_effect = _add
    db.flush = MagicMock()

    fake_ship = SimpleNamespace(
        id=uuid.uuid4(),
        cargo={"capacity": 80, "used": 0, "contents": {}},
        sector_id=999,
    )

    with patch(
        "src.services.npc_spawn_service._build_npc_ship",
        return_value=fake_ship,
    ), patch(
        "src.services.npc_movement_service.add_npc_presence",
    ) as add_presence, patch(
        "src.services.npc_trading_service.flag_modified",
    ):
        result = npc_trading_service.dispatch_visible_supply_delivery(
            db, station, "ore", 40, spawn_sector=empty_sector,
        )

    assert result is not None
    assert result["spawn_sector_id"] == 999
    assert result["commodity"] == "ore"
    assert result["quantity"] > 0
    assert result["quantity"] <= 40
    assert fake_ship.cargo["contents"]["ore"] == result["quantity"]
    assert fake_ship.cargo["used"] == result["quantity"]

    npcs = [o for o in added if isinstance(o, NPCCharacter)]
    assert len(npcs) == 1
    npc = npcs[0]
    assert npc.archetype == NPCArchetype.TRADER
    assert npc.status == NPCStatus.ON_DUTY
    assert npc.current_sector_id == 999
    assert npc.daily_schedule["mission"] == "supply_delivery"
    assert npc.daily_schedule["trade_route"][0]["buy_here"] == []
    add_presence.assert_called_once()


def test_scan_skips_in_flight_supply_targets():
    station_id = uuid.uuid4()
    station = SimpleNamespace(
        id=station_id,
        name="Depot",
        sector_id=10,
        commodities={
            "ore": {
                "buys": True,
                "quantity": 10,
                "capacity": 1000,
                "npc_restock_demand": 1.8,
            }
        },
    )
    live_npc = SimpleNamespace(
        archetype=NPCArchetype.TRADER,
        status=NPCStatus.ON_DUTY,
        daily_schedule={
            "mission": "supply_delivery",
            "supply_delivery": {
                "station_id": str(station_id),
                "commodity": "ore",
            },
        },
    )
    db = MagicMock()

    def query(model):
        q = MagicMock()
        q.filter.return_value = q
        q.first.return_value = None
        q.all.return_value = []
        if model is NPCCharacter:
            q.all.return_value = [live_npc]
            return q
        if model is Station:
            q.all.return_value = [station]
            return q
        return q

    db.query.side_effect = query

    with patch.object(
        npc_trading_service,
        "dispatch_visible_supply_delivery",
        return_value={"ok": True},
    ) as dispatch:
        out = npc_trading_service.scan_and_dispatch_supply_deliveries(db, limit=5)

    assert out == []
    dispatch.assert_not_called()
