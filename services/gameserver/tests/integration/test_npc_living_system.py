"""Integration tests — Living NPC System (db fixture).

Covers the plan's integration surface: move_npc two-sector presence
consistency, KIA → death log + respawn split + zero-gap promotion,
pirate resurrection after cooldown, NPC trade stops (stock + price +
demand-split effects), PendingEngagement dispatch/arrival on
lifetime_turns_spent, and trader-roster seeding idempotency.

Lock-order/concurrency behavior is NOT provable in this single-session
fixture (every test runs in one rolled-back transaction) — that is
verified live on dev per the plan.
"""

import random
import uuid
from datetime import datetime, timedelta, UTC

import pytest
from sqlalchemy.orm import Session

from src.models.cluster import Cluster, ClusterType
from src.models.galaxy import Galaxy
from src.models.market_transaction import MarketPrice, MarketTransaction
from src.models.npc_character import (
    NPCArchetype,
    NPCCharacter,
    NPCDeathLog,
    NPCLifecycleStage,
    NPCRoster,
    NPCStatus,
)
from src.models.pending_engagement import EngagementStatus, PendingEngagement
from src.models.player import Player
from src.models.region import Region, RegionType
from src.models.sector import Sector
from src.models.ship import Ship, ShipSpecification, ShipStatus, ShipType
from src.models.station import Station, StationClass, StationType
from src.models.user import User
from src.models.warp_tunnel import WarpTunnel, WarpTunnelType
from src.services import npc_movement_service, npc_trading_service
from src.services.npc_engagement_service import (
    route_engagement,
    sweep_pending_engagements,
)
from src.services.npc_scheduler_service import _resurrect_respawned
from src.services.npc_spawn_service import (
    _presence_entry,
    handle_npc_ship_destroyed,
    seed_trader_rosters,
)


# ---------------------------------------------------------------------------
# Fixture world: terran-space region, two warp-connected sectors,
# complementary stations with market rows.
# ---------------------------------------------------------------------------

def _commodity(quantity, capacity, base_price, buys, sells):
    return {
        "quantity": quantity, "capacity": capacity,
        "base_price": base_price, "current_price": base_price,
        "production_rate": 0, "price_variance": 20,
        "buys": buys, "sells": sells,
    }


class World:
    """Bag of fixture rows shared by the tests."""


@pytest.fixture
def world(db: Session) -> World:
    w = World()
    base = random.randint(800_000, 980_000)

    w.region = Region(
        name=f"npc-test-{base}",
        display_name="NPC Test Space",
        region_type=RegionType.TERRAN_SPACE.value,
        total_sectors=300,
    )
    db.add(w.region)
    db.flush()

    w.cluster = Cluster(
        name="NPC Test Cluster",
        region_id=w.region.id,
        type=ClusterType.STANDARD,
    )
    db.add(w.cluster)
    db.flush()

    def make_sector(offset: int) -> Sector:
        sector = Sector(
            sector_id=base + offset,
            name=f"NPC Test Sector {offset}",
            region_id=w.region.id,
            cluster_id=w.cluster.id,
            x_coord=offset,
            y_coord=0,
            z_coord=0,
        )
        db.add(sector)
        return sector

    w.sector_a = make_sector(1)
    w.sector_b = make_sector(2)
    w.sector_isolated = make_sector(9)  # no warp connection on purpose
    db.flush()

    w.tunnel = WarpTunnel(
        name="Test Conduit A-B",
        origin_sector_id=w.sector_a.id,
        destination_sector_id=w.sector_b.id,
        type=WarpTunnelType.NATURAL,
        is_bidirectional=True,
        turn_cost=1,
    )
    db.add(w.tunnel)

    # Station A supplies ore (surplus seller); station B wants ore and
    # supplies fuel (the complementary pair for route generation).
    w.station_a = Station(
        name="Ore Works",
        sector_id=w.sector_a.sector_id,
        sector_uuid=w.sector_a.id,
        station_class=StationClass.CLASS_1,
        type=StationType.MINING,
        commodities={
            "ore": _commodity(4000, 5000, 15, buys=False, sells=True),
            "fuel": _commodity(100, 4000, 12, buys=True, sells=False),
        },
    )
    w.station_b = Station(
        name="Fuel Depot",
        sector_id=w.sector_b.sector_id,
        sector_uuid=w.sector_b.id,
        station_class=StationClass.CLASS_4,
        type=StationType.TRADING,
        commodities={
            "ore": _commodity(200, 5000, 18, buys=True, sells=False),
            "fuel": _commodity(3500, 4000, 12, buys=False, sells=True),
        },
    )
    db.add_all([w.station_a, w.station_b])
    db.flush()

    for station in (w.station_a, w.station_b):
        for name, cfg in station.commodities.items():
            db.add(MarketPrice(
                station_id=station.id,
                commodity=name,
                buy_price=cfg["base_price"],
                sell_price=cfg["base_price"] + 5,
                quantity=cfg["quantity"],
            ))
    db.flush()
    return w


def _make_ship(db: Session, name: str, sector_id: int,
               ship_type: ShipType = ShipType.LIGHT_FREIGHTER,
               cargo_capacity: int = 100) -> Ship:
    ship = Ship(
        name=name,
        type=ship_type,
        owner_id=None,
        is_npc=True,
        sector_id=sector_id,
        base_speed=1.0,
        current_speed=1.0,
        turn_cost=1,
        warp_capable=False,
        is_active=True,
        status=ShipStatus.IN_SPACE,
        maintenance={"condition": 100.0},
        cargo={"capacity": cargo_capacity, "used": 0, "contents": {}},
        combat={"hull": 100, "max_hull": 100, "shields": 50, "max_shields": 50},
        attack_turn_cost=1,
        genesis_devices=0,
        max_genesis_devices=0,
        mines=0,
        max_mines=0,
        is_destroyed=False,
        is_flagship=False,
        purchase_value=0,
        current_value=0,
        upgrades={},
        equipment_slots={},
        insurance=None,
    )
    db.add(ship)
    db.flush()
    return ship


def _make_npc(db: Session, world: World, *, name: str,
              archetype: NPCArchetype, faction_code: str,
              sector: Sector, ship: Ship, title: str = None,
              duty_role: str = None, roster_ref: str = None,
              add_presence: bool = True) -> NPCCharacter:
    npc = NPCCharacter(
        name=name,
        title=title,
        faction_code=faction_code,
        archetype=archetype,
        status=NPCStatus.ON_DUTY,
        current_sector_id=sector.sector_id,
        ship_id=ship.id,
        home_region_id=world.region.id,
        lifecycle_stage=NPCLifecycleStage.ACTIVE,
        daily_schedule={},
        duty_role=duty_role,
        bang_roster_ref=roster_ref,
        role_history=[],
        backstory={},
        credits=0,
    )
    db.add(npc)
    db.flush()
    if add_presence:
        sector.players_present = list(sector.players_present or []) + [
            _presence_entry(npc, ship)
        ]
    db.flush()
    return npc


def _presence_ids(sector: Sector) -> set:
    return {p.get("player_id") for p in (sector.players_present or [])}


# ---------------------------------------------------------------------------
# move_npc — two-sector presence consistency
# ---------------------------------------------------------------------------

class TestMoveNpc:
    def test_single_hop_updates_both_sectors(self, db, world):
        ship = _make_ship(db, "Test Marauder", world.sector_a.sector_id)
        npc = _make_npc(
            db, world, name="Redbeard", archetype=NPCArchetype.HOSTILE_RAIDER,
            faction_code="pirates", sector=world.sector_a, ship=ship,
        )

        events = npc_movement_service.move_npc(
            db, npc, world.sector_b.sector_id, enforce_pacing=False
        )

        assert [e["type"] for e in events] == [
            "npc_left_sector", "npc_entered_sector",
        ]
        assert npc.current_sector_id == world.sector_b.sector_id
        assert ship.sector_id == world.sector_b.sector_id
        assert str(npc.id) not in _presence_ids(world.sector_a)
        assert str(npc.id) in _presence_ids(world.sector_b)

    def test_no_connection_is_a_noop(self, db, world):
        ship = _make_ship(db, "Test Marauder", world.sector_a.sector_id)
        npc = _make_npc(
            db, world, name="Redbeard", archetype=NPCArchetype.HOSTILE_RAIDER,
            faction_code="pirates", sector=world.sector_a, ship=ship,
        )

        events = npc_movement_service.move_npc(
            db, npc, world.sector_isolated.sector_id, enforce_pacing=False
        )

        assert events == []
        assert npc.current_sector_id == world.sector_a.sector_id
        assert str(npc.id) in _presence_ids(world.sector_a)

    def test_kia_npc_never_moves(self, db, world):
        ship = _make_ship(db, "Test Marauder", world.sector_a.sector_id)
        npc = _make_npc(
            db, world, name="Redbeard", archetype=NPCArchetype.HOSTILE_RAIDER,
            faction_code="pirates", sector=world.sector_a, ship=ship,
        )
        npc.status = NPCStatus.KIA
        db.flush()

        events = npc_movement_service.move_npc(
            db, npc, world.sector_b.sector_id, enforce_pacing=False
        )
        assert events == []


# ---------------------------------------------------------------------------
# KIA processing — death log, respawn split, zero-gap promotion
# ---------------------------------------------------------------------------

class TestKiaProcessing:
    def test_pirate_goes_respawning_with_cooldown(self, db, world):
        ship = _make_ship(db, "Test Marauder", world.sector_a.sector_id)
        npc = _make_npc(
            db, world, name="Redbeard", archetype=NPCArchetype.HOSTILE_RAIDER,
            faction_code="pirates", sector=world.sector_a, ship=ship,
        )

        result = handle_npc_ship_destroyed(db, ship.id, destruction_cause="combat")

        assert result is not None and result.id == npc.id
        assert npc.status == NPCStatus.RESPAWNING
        assert npc.respawn_eligible_at is not None
        assert npc.lifecycle_stage == NPCLifecycleStage.ACTIVE  # career persists
        assert npc.current_sector_id is None
        assert str(npc.id) not in _presence_ids(world.sector_a)

        log = db.query(NPCDeathLog).filter(NPCDeathLog.npc_id == npc.id).one()
        assert log.sector_id == world.sector_a.sector_id
        assert log.destruction_cause == "combat"

    def test_law_enforcement_is_permanently_kia(self, db, world):
        ship = _make_ship(db, "Marshal Interdictor", world.sector_a.sector_id)
        npc = _make_npc(
            db, world, name="Vance", title="Marshal",
            archetype=NPCArchetype.LAW_ENFORCEMENT,
            faction_code="terran_federation",
            sector=world.sector_a, ship=ship,
        )

        handle_npc_ship_destroyed(db, ship.id)

        assert npc.status == NPCStatus.KIA
        assert npc.lifecycle_stage == NPCLifecycleStage.KIA
        assert npc.respawn_eligible_at is None
        # Row persists per canon — never deleted.
        assert db.query(NPCCharacter).filter(NPCCharacter.id == npc.id).count() == 1

    def test_zero_gap_promotion_of_backup(self, db, world):
        roster_ref = f"test:{uuid.uuid4()}"
        primary_ship = _make_ship(db, "Primary Interdictor", world.sector_a.sector_id)
        backup_ship = _make_ship(db, "Backup Interdictor", world.sector_a.sector_id)
        primary = _make_npc(
            db, world, name="Vance", title="Marshal",
            archetype=NPCArchetype.LAW_ENFORCEMENT,
            faction_code="terran_federation", sector=world.sector_a,
            ship=primary_ship, duty_role="primary_marshal",
            roster_ref=roster_ref,
        )
        backup = _make_npc(
            db, world, name="Reyes", title="Marshal",
            archetype=NPCArchetype.LAW_ENFORCEMENT,
            faction_code="terran_federation", sector=world.sector_a,
            ship=backup_ship, duty_role="backup_marshal",
            roster_ref=roster_ref,
        )

        handle_npc_ship_destroyed(db, primary_ship.id)

        assert primary.status == NPCStatus.KIA
        assert backup.duty_role == "primary_marshal"
        assert backup.promotion_pending_at is None
        assert any(
            entry.get("to") == "primary_marshal"
            for entry in (backup.role_history or [])
        )

    def test_double_destruction_is_idempotent(self, db, world):
        ship = _make_ship(db, "Test Marauder", world.sector_a.sector_id)
        npc = _make_npc(
            db, world, name="Redbeard", archetype=NPCArchetype.HOSTILE_RAIDER,
            faction_code="pirates", sector=world.sector_a, ship=ship,
        )
        handle_npc_ship_destroyed(db, ship.id)
        handle_npc_ship_destroyed(db, ship.id)

        assert npc.status == NPCStatus.RESPAWNING
        assert db.query(NPCDeathLog).filter(NPCDeathLog.npc_id == npc.id).count() == 1


# ---------------------------------------------------------------------------
# Pirate respawn cycle (Loop B resurrect)
# ---------------------------------------------------------------------------

class TestRespawnCycle:
    def test_cooled_down_pirate_returns_to_duty(self, db, world):
        spec = (
            db.query(ShipSpecification)
            .filter(ShipSpecification.type == ShipType.LIGHT_FREIGHTER)
            .first()
        )
        if spec is None:
            pytest.skip("LIGHT_FREIGHTER ShipSpecification not seeded in this DB")

        roster_ref = f"test:{uuid.uuid4()}"
        db.add(NPCRoster(
            region_id=world.region.id,
            faction_code="pirates",
            role="pirate_captain",
            default_archetype=NPCArchetype.HOSTILE_RAIDER,
            schedule_template={},
            target_count=1,
            name_pool={"names": ["Redbeard"]},
            host_sector_id=world.sector_a.sector_id,
            bang_roster_ref=roster_ref,
        ))
        old_ship = _make_ship(db, "Old Marauder", world.sector_a.sector_id)
        npc = _make_npc(
            db, world, name="Redbeard", title="Captain",
            archetype=NPCArchetype.HOSTILE_RAIDER, faction_code="pirates",
            sector=world.sector_a, ship=old_ship, roster_ref=roster_ref,
        )
        npc.status = NPCStatus.RESPAWNING
        npc.current_sector_id = None
        npc.ship_id = None
        npc.respawn_eligible_at = datetime.now(UTC) - timedelta(minutes=1)
        db.flush()

        events = _resurrect_respawned(db, datetime.now(UTC))

        assert npc.status == NPCStatus.ON_DUTY
        assert npc.current_sector_id == world.sector_a.sector_id
        assert npc.respawn_eligible_at is None
        assert npc.ship_id is not None and npc.ship_id != old_ship.id
        assert str(npc.id) in _presence_ids(world.sector_a)
        assert any(e["type"] == "npc_respawned" for e in events)


# ---------------------------------------------------------------------------
# Trader NPCs — route generation + trade stop market effects
# ---------------------------------------------------------------------------

class TestTraderNpc:
    def test_generate_route_pairs_complementary_stations(self, db, world):
        route = npc_trading_service.generate_trade_route(
            db, world.region.id, world.sector_a.sector_id
        )

        assert route is not None
        assert 2 <= len(route) <= 4
        assert route[0]["station_id"] == str(world.station_a.id)
        assert route[0]["buy_here"] == ["ore"]
        assert route[-1]["buy_here"] == []

    def test_trade_stop_sells_cargo_and_buys_outbound(self, db, world):
        ship = _make_ship(
            db, "Trader Hauler", world.sector_b.sector_id,
            ship_type=ShipType.CARGO_HAULER, cargo_capacity=100,
        )
        ship.cargo = {"capacity": 100, "used": 40, "contents": {"ore": 40}}
        npc = _make_npc(
            db, world, name="Okonkwo", title="Trader",
            archetype=NPCArchetype.TRADER, faction_code="merchants",
            sector=world.sector_b, ship=ship,
        )
        npc.credits = 1000
        db.flush()

        ore_price_b = (
            db.query(MarketPrice)
            .filter(MarketPrice.station_id == world.station_b.id,
                    MarketPrice.commodity == "ore")
            .one()
        )
        fuel_price_b = (
            db.query(MarketPrice)
            .filter(MarketPrice.station_id == world.station_b.id,
                    MarketPrice.commodity == "fuel")
            .one()
        )
        ore_stock_before = world.station_b.commodities["ore"]["quantity"]
        fuel_stock_before = world.station_b.commodities["fuel"]["quantity"]
        expected_sale = max(1, int(ore_price_b.buy_price)) * 40

        stop = {
            "station_id": str(world.station_b.id),
            "sector_id": world.sector_b.sector_id,
            "buy_here": ["fuel"],
        }
        npc_trading_service.run_trade_stop(db, npc, stop)

        contents = ship.cargo["contents"]
        # Sold all the ore, bought fuel for the next leg.
        assert "ore" not in contents
        assert contents.get("fuel", 0) > 0
        assert world.station_b.commodities["ore"]["quantity"] == ore_stock_before + 40
        assert world.station_b.commodities["fuel"]["quantity"] == (
            fuel_stock_before - contents["fuel"]
        )

        # Wallet: gained the ore sale, spent on fuel.
        fuel_cost = max(1, int(fuel_price_b.sell_price)) * contents["fuel"]
        assert npc.credits == 1000 + expected_sale - fuel_cost

        # Demand split: only npc_restock_demand moves, never the player key.
        ore_cfg = world.station_b.commodities["ore"]
        fuel_cfg = world.station_b.commodities["fuel"]
        assert ore_cfg["npc_restock_demand"] < 1.0   # supply arrived
        assert fuel_cfg["npc_restock_demand"] > 1.0  # stock drawn down
        assert "player_demand_score" not in ore_cfg
        assert "player_demand_score" not in fuel_cfg

        # Attribution: MarketTransaction rows carry npc_id, no player_id.
        rows = (
            db.query(MarketTransaction)
            .filter(MarketTransaction.npc_id == npc.id)
            .all()
        )
        assert len(rows) == 2  # one SELL (ore), one BUY (fuel)
        assert all(r.player_id is None for r in rows)

    def test_trade_stop_requires_colocation(self, db, world):
        ship = _make_ship(
            db, "Trader Hauler", world.sector_a.sector_id,
            ship_type=ShipType.CARGO_HAULER,
        )
        npc = _make_npc(
            db, world, name="Okonkwo", title="Trader",
            archetype=NPCArchetype.TRADER, faction_code="merchants",
            sector=world.sector_a, ship=ship,
        )
        stop = {
            "station_id": str(world.station_b.id),  # NPC is in sector A
            "sector_id": world.sector_b.sector_id,
            "buy_here": ["fuel"],
        }
        assert npc_trading_service.run_trade_stop(db, npc, stop) == []

    def test_seed_trader_rosters_idempotent(self, db, world):
        galaxy = Galaxy(name=f"Test Galaxy {uuid.uuid4()}")
        db.add(galaxy)
        db.flush()

        first = seed_trader_rosters(db, galaxy)
        second = seed_trader_rosters(db, galaxy)

        ref = f"{galaxy.id}:trader:{world.region.id}"
        rosters = (
            db.query(NPCRoster)
            .filter(NPCRoster.bang_roster_ref == ref)
            .all()
        )
        assert len(rosters) == 1
        assert rosters[0].role == "merchant_captain"
        assert first["trader_rosters_created"] >= 1
        assert second["trader_rosters_created"] == 0


# ---------------------------------------------------------------------------
# Police engagement — dispatch + arrival on lifetime_turns_spent
# ---------------------------------------------------------------------------

def _make_offender(db: Session, world: World, *, rep: int,
                   turns_spent: int) -> Player:
    user = User(
        username=f"offender_{uuid.uuid4().hex[:8]}",
        email=f"offender_{uuid.uuid4().hex[:8]}@test.local",
        is_admin=False,
    )
    db.add(user)
    db.flush()
    player = Player(
        user_id=user.id,
        username=user.username,
        credits=1000,
        current_sector_id=world.sector_a.sector_id,
        personal_reputation=rep,
        lifetime_turns_spent=turns_spent,
    )
    db.add(player)
    db.flush()
    return player


class TestPoliceEngagement:
    def _make_marshal(self, db, world, name="Vance"):
        ship = _make_ship(db, f"Marshal {name}'s Interdictor",
                          world.sector_a.sector_id)
        return _make_npc(
            db, world, name=name, title="Marshal",
            archetype=NPCArchetype.LAW_ENFORCEMENT,
            faction_code="terran_federation",
            sector=world.sector_a, ship=ship,
        )

    def test_dispatch_and_arrival_at_turn_threshold(self, db, world):
        marshal = self._make_marshal(db, world)
        player = _make_offender(db, world, rep=-100, turns_spent=10)

        engagement = route_engagement(
            db, player, "attack_innocent", world.sector_a
        )

        assert engagement is not None
        assert engagement.status == EngagementStatus.PENDING
        assert engagement.jurisdiction == "federation"
        assert engagement.npc_squad_ids == [str(marshal.id)]
        assert engagement.arrival_turn_threshold == 12
        assert marshal.status == NPCStatus.ENGAGED_PENDING_ARRIVAL

        # One turn spent — not there yet.
        player.lifetime_turns_spent = 11
        db.flush()
        sweep_pending_engagements(db)
        assert engagement.status == EngagementStatus.PENDING

        # Second turn spent: the squad arrives at the offender's sector.
        player.lifetime_turns_spent = 12
        player.current_sector_id = world.sector_b.sector_id
        db.flush()
        sweep_pending_engagements(db)

        assert engagement.status == EngagementStatus.ARRIVED
        assert engagement.arrival_sector_id == world.sector_b.sector_id
        assert marshal.status == NPCStatus.ENGAGED
        assert marshal.current_sector_id == world.sector_b.sector_id
        assert str(marshal.id) in _presence_ids(world.sector_b)

    def test_same_offense_type_cooldown(self, db, world):
        self._make_marshal(db, world)
        player = _make_offender(db, world, rep=-100, turns_spent=10)

        first = route_engagement(db, player, "attack_innocent", world.sector_a)
        second = route_engagement(db, player, "attack_innocent", world.sector_a)

        assert first is not None
        assert second is None  # within the 5-turn per-offense-type window

    def test_arrived_squad_releases_when_offender_leaves(self, db, world):
        marshal = self._make_marshal(db, world)
        player = _make_offender(db, world, rep=-100, turns_spent=10)
        engagement = route_engagement(
            db, player, "attack_innocent", world.sector_a
        )
        player.lifetime_turns_spent = 12
        db.flush()
        sweep_pending_engagements(db)
        assert engagement.status == EngagementStatus.ARRIVED

        # Offender moves on; the encounter resolves and the officer
        # returns to duty.
        player.current_sector_id = world.sector_b.sector_id
        db.flush()
        sweep_pending_engagements(db)

        assert engagement.status == EngagementStatus.RESOLVED
        assert marshal.status == NPCStatus.ON_DUTY
