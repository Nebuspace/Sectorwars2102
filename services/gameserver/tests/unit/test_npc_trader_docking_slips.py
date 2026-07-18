"""Coverage for WO-P9-realtime-npc-trader-slips.

TRADER-archetype NPCs occupy real docking slips through the SAME
DockingSlipOccupancy table the player dock path uses (npc-traders.md §
Market participation), rather than a parallel occupancy store, with an
anti-camp tenure ceiling so a slip is never held forever.

Two proof strategies, matched to what each needs:

1. `TestExactlyOneOwnerConstraint` uses a REAL SQLite in-memory engine
   scoped to ONLY `DockingSlipOccupancy.__table__` (its columns are all
   portable UUID/String/Integer/DateTime types -- no Postgres-only JSONB,
   unlike Station/NPCCharacter, so this is the rare case where a genuine
   `__table__.create()` against SQLite is viable per this suite's own
   established convention, e.g. test_chatlog_constraint.py's docstring on
   when that's NOT viable). This is the only way to prove the CHECK
   constraint and the two UNIQUE constraints are actually enforced at the
   DB level, not just "the Python code happens to behave."

2. Everything else uses a hand-rolled FakeSession (this suite's
   established convention for DB-free service-logic tests) faithful to
   the EXACT query shapes docking_service.py / npc_trading_service.py
   issue: `.filter(Model.attr == value)` and `.filter(Model.attr.isnot
   (None))`, interpreted generically via SQLAlchemy's own expression
   `.left.key` / `.right.value` / `.operator` rather than hand-parsing
   each call site, so a query SHAPE change breaks loudly (an
   unrecognized operator raises) instead of silently under-matching.

Fully DB-free (aside from the isolated SQLite proof above) and
network-free throughout.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, UTC
from types import SimpleNamespace
from typing import Any, List
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.sql import operators as sa_operators

from src.models.docking import DockingQueueEntry, DockingSlipOccupancy
from src.models.npc_character import NPCCharacter, NPCLifecycleStage
from src.models.player import Player
from src.models.station import Station, StationClass
from src.services import docking_service, npc_trading_service


# --------------------------------------------------------------------------- #
# (1) Real SQLite: the CHECK + UNIQUE constraints are genuinely enforced.
# --------------------------------------------------------------------------- #

@pytest.fixture()
def sqlite_occupancy_session():
    engine = create_engine("sqlite:///:memory:")
    DockingSlipOccupancy.__table__.create(bind=engine)
    session = Session(bind=engine)
    yield session
    session.close()


class TestExactlyOneOwnerConstraint:
    def test_player_only_row_is_valid(self, sqlite_occupancy_session):
        db = sqlite_occupancy_session
        db.add(DockingSlipOccupancy(
            station_id=uuid.uuid4(), player_id=uuid.uuid4(), slip_class="transient",
        ))
        db.commit()  # does not raise

    def test_npc_only_row_is_valid(self, sqlite_occupancy_session):
        db = sqlite_occupancy_session
        db.add(DockingSlipOccupancy(
            station_id=uuid.uuid4(), npc_id=uuid.uuid4(), slip_class="transient",
        ))
        db.commit()  # does not raise

    def test_both_owners_set_violates_check(self, sqlite_occupancy_session):
        db = sqlite_occupancy_session
        db.add(DockingSlipOccupancy(
            station_id=uuid.uuid4(), player_id=uuid.uuid4(), npc_id=uuid.uuid4(),
            slip_class="transient",
        ))
        with pytest.raises(IntegrityError):
            db.commit()

    def test_neither_owner_set_violates_check(self, sqlite_occupancy_session):
        db = sqlite_occupancy_session
        db.add(DockingSlipOccupancy(station_id=uuid.uuid4(), slip_class="transient"))
        with pytest.raises(IntegrityError):
            db.commit()

    def test_npc_id_unique_across_galaxy(self, sqlite_occupancy_session):
        db = sqlite_occupancy_session
        npc_id = uuid.uuid4()
        db.add(DockingSlipOccupancy(
            station_id=uuid.uuid4(), npc_id=npc_id, slip_class="transient",
        ))
        db.commit()
        db.add(DockingSlipOccupancy(
            station_id=uuid.uuid4(), npc_id=npc_id, slip_class="transient",
        ))
        with pytest.raises(IntegrityError):
            db.commit()


# --------------------------------------------------------------------------- #
# FakeSession: faithful interpreter for the exact query shapes used here.
# --------------------------------------------------------------------------- #

class _FakeQuery:
    def __init__(self, rows: List[Any], filters=None):
        self._rows = rows
        self._filters = filters or []

    def filter(self, *criteria):
        return _FakeQuery(self._rows, self._filters + list(criteria))

    def with_for_update(self):
        return self

    def populate_existing(self):
        return self

    def order_by(self, *args, **kwargs):
        return self

    def _row_matches(self, row) -> bool:
        for crit in self._filters:
            key = crit.left.key
            if crit.operator is sa_operators.is_not:
                if getattr(row, key, None) is None:
                    return False
                continue
            if crit.operator is sa_operators.eq:
                if getattr(row, key, None) != crit.right.value:
                    return False
                continue
            raise AssertionError(f"FakeQuery: unrecognized operator {crit.operator} on {key}")
        return True

    def first(self):
        for row in self._rows:
            if self._row_matches(row):
                return row
        return None

    def all(self):
        return [row for row in self._rows if self._row_matches(row)]


class FakeDB:
    """Minimal in-memory session covering exactly the models/queries
    docking_service.acquire / acquire_for_npc / release_stale_trader_slips
    issue."""

    def __init__(self):
        self.stations: List[Any] = []
        self.occupancies: List[DockingSlipOccupancy] = []
        self.npcs: List[Any] = []
        self.players: List[Any] = []
        self.queue_entries: List[Any] = []

    def query(self, model):
        if model is Station:
            return _FakeQuery(self.stations)
        if model is DockingSlipOccupancy:
            return _FakeQuery(self.occupancies)
        if model is NPCCharacter:
            return _FakeQuery(self.npcs)
        if model is Player:
            return _FakeQuery(self.players)
        if model is DockingQueueEntry:
            return _FakeQuery(self.queue_entries)
        raise AssertionError(f"FakeDB: unexpected model queried: {model}")

    def add(self, obj):
        if isinstance(obj, DockingSlipOccupancy):
            if obj.docked_at is None:
                # Simulates the server_default=func.now() a real DB applies
                # on INSERT -- bare Python instantiation never fires it, and
                # docking_service.acquire_for_npc (like the player-path
                # acquire()) deliberately relies on that server default
                # rather than setting it explicitly.
                obj.docked_at = datetime.now(UTC)
            self.occupancies.append(obj)
        else:
            raise AssertionError(f"FakeDB: unexpected add(): {obj!r}")

    def delete(self, obj):
        if obj in self.occupancies:
            self.occupancies.remove(obj)

    def flush(self):
        pass


def make_station(station_id=None, capacity_class=StationClass.CLASS_3, **overrides):
    station = SimpleNamespace(
        id=station_id or uuid.uuid4(),
        station_class=capacity_class,
        is_spacedock=False,
        tradedock_tier=None,
        reputation_threshold=0,  # gate skipped -- no Faction/Reputation queries needed
        name="Test Station",
    )
    for k, v in overrides.items():
        setattr(station, k, v)
    return station


def make_npc(npc_id=None, daily_schedule=None, lifecycle_stage=NPCLifecycleStage.ACTIVE):
    return SimpleNamespace(
        id=npc_id or uuid.uuid4(),
        daily_schedule=daily_schedule or {},
        lifecycle_stage=lifecycle_stage,
        display_name="Test Trader",
    )


def _full_day_block(activity: str, location_type: str, station_id) -> dict:
    """A route_cycle schedule with ONE day (cycle_days=1, so day_number %
    1 == 0 regardless of the actual real day) and ONE block spanning the
    whole day (0-1440), so resolve_schedule_block's result is deterministic
    and independent of wall-clock time -- no time mocking needed."""
    return {
        "route_cycle": {
            "cycle_days": 1,
            "days": {
                "0": [{
                    "start_minute": 0, "end_minute": 1440,
                    "activity": activity, "location_type": location_type,
                    "location_ref": {"station_id": str(station_id)},
                }],
            },
        },
    }


# --------------------------------------------------------------------------- #
# (2) docking_service.acquire_for_npc
# --------------------------------------------------------------------------- #

class TestAcquireForNpc:
    def test_occupies_a_free_slip(self):
        db = FakeDB()
        station = make_station()
        db.stations.append(station)
        npc = make_npc()

        granted = docking_service.acquire_for_npc(db, station, npc, ship_id=uuid.uuid4())

        assert granted is True
        assert len(db.occupancies) == 1
        assert db.occupancies[0].npc_id == npc.id
        assert db.occupancies[0].station_id == station.id
        assert db.occupancies[0].slip_class == DockingSlipOccupancy.SLIP_CLASS_TRANSIENT

    def test_idempotent_on_repeat_calls_at_the_same_station(self):
        db = FakeDB()
        station = make_station()
        db.stations.append(station)
        npc = make_npc()

        docking_service.acquire_for_npc(db, station, npc)
        docking_service.acquire_for_npc(db, station, npc)
        docking_service.acquire_for_npc(db, station, npc)

        assert len(db.occupancies) == 1

    def test_full_station_soft_fails_without_gating(self):
        """A full transient pool does not raise or block -- it just means
        this trader doesn't hold a slip this stop (see acquire_for_npc's
        own docstring: not a hard economic gate)."""
        db = FakeDB()
        station = make_station(capacity_class=StationClass.CLASS_1)  # capacity 8... use tiny synthetic instead
        station.station_class = StationClass.CLASS_1
        db.stations.append(station)
        capacity = docking_service.slip_capacity_for(station)
        # Pre-fill the station to capacity with OTHER npc-held rows.
        for _ in range(capacity):
            db.occupancies.append(DockingSlipOccupancy(
                station_id=station.id, npc_id=uuid.uuid4(), slip_class="transient",
            ))

        newcomer = make_npc()
        granted = docking_service.acquire_for_npc(db, station, newcomer)

        assert granted is False
        assert len(db.occupancies) == capacity  # nothing added

    def test_relocating_npc_releases_its_old_slip_first(self):
        db = FakeDB()
        station_a = make_station()
        station_b = make_station()
        db.stations += [station_a, station_b]
        npc = make_npc()

        docking_service.acquire_for_npc(db, station_a, npc)
        assert len(db.occupancies) == 1
        assert db.occupancies[0].station_id == station_a.id

        docking_service.acquire_for_npc(db, station_b, npc)

        assert len(db.occupancies) == 1  # never two at once
        assert db.occupancies[0].station_id == station_b.id


# --------------------------------------------------------------------------- #
# (3) The literal WO proof: full-of-traders parity with full-of-players,
#     exercised through the REAL player-path acquire().
# --------------------------------------------------------------------------- #

class TestPlayerNpcOccupancyParity:
    def test_trader_occupied_slip_is_visible_to_player_dock_api_count(self):
        """'trader at a 2-slip station occupies one (player dock API sees
        1 free)' -- exercised via the exact count docking_service.acquire
        and routes/trading.py's GET .../slips both use."""
        db = FakeDB()
        station = make_station()
        db.stations.append(station)
        npc = make_npc()

        docking_service.acquire_for_npc(db, station, npc)

        occupancies = docking_service._transient_occupancies(db, station.id)
        assert len(occupancies) == 1
        # Mirrors GET /stations/{id}/slips's own "free = capacity - occupied".
        capacity = docking_service.slip_capacity_for(station)
        free = capacity - len(occupancies)
        assert free == capacity - 1

    def test_full_of_traders_station_rejects_player_dock_like_full_of_players(self):
        """The load-bearing parity assert: a station whose transient pool
        is entirely NPC-occupied returns the SAME 'full' shape
        docking_service.acquire gives a player when it's entirely
        player-occupied -- same status, same capacity/occupied numbers,
        same route-level 409 the caller builds from them."""
        db = FakeDB()
        station = make_station(capacity_class=StationClass.CLASS_1)  # capacity 8
        db.stations.append(station)
        capacity = docking_service.slip_capacity_for(station)

        for _ in range(capacity):
            npc = make_npc()
            db.npcs.append(npc)
            granted = docking_service.acquire_for_npc(db, station, npc)
            assert granted is True

        walk_up_player = SimpleNamespace(id=uuid.uuid4())
        result = docking_service.acquire(db, station, walk_up_player)

        assert result["status"] == "full"
        assert result["occupied"] == capacity
        assert result["capacity"] == capacity
        assert result["queue_length"] == 0

    def test_mixed_player_and_npc_occupancy_counts_together(self):
        db = FakeDB()
        station = make_station(capacity_class=StationClass.CLASS_1)  # capacity 8
        db.stations.append(station)

        npc = make_npc()
        db.occupancies.append(DockingSlipOccupancy(
            station_id=station.id, npc_id=npc.id, slip_class="transient",
        ))
        player = SimpleNamespace(id=uuid.uuid4())
        db.occupancies.append(DockingSlipOccupancy(
            station_id=station.id, player_id=player.id, slip_class="transient",
        ))

        occupancies = docking_service._transient_occupancies(db, station.id)
        assert len(occupancies) == 2


# --------------------------------------------------------------------------- #
# (4) npc_trading_service.release_stale_trader_slips
# --------------------------------------------------------------------------- #

class TestReleaseStaleTraderSlips:
    def test_releases_on_tenure_ceiling_even_though_schedule_still_matches(self):
        """The stall safety net: tenure exceeded, but the NPC's schedule
        STILL resolves to the same work_station block (a frozen/stalled
        schedule would look exactly like this) -- must still release."""
        db = FakeDB()
        station_id = uuid.uuid4()
        npc = make_npc(daily_schedule=_full_day_block("work_station", "station", station_id))
        db.npcs.append(npc)
        stale_docked_at = datetime.now(UTC) - timedelta(
            hours=npc_trading_service.TRADER_SLIP_TENURE_CEILING_HOURS + 1
        )
        db.occupancies.append(DockingSlipOccupancy(
            station_id=station_id, npc_id=npc.id, slip_class="transient",
            docked_at=stale_docked_at,
        ))

        released = npc_trading_service.release_stale_trader_slips(db)

        assert released == 1
        assert db.occupancies == []

    def test_releases_when_schedule_moves_to_socialize_even_within_tenure(self):
        """The normal, prompt release: well within tenure, but the
        schedule has moved on to `socialize` at the same station (no
        driver re-visits this NPC there) -- releases immediately rather
        than waiting out the full tenure ceiling."""
        db = FakeDB()
        station_id = uuid.uuid4()
        npc = make_npc(daily_schedule=_full_day_block("socialize", "station", station_id))
        db.npcs.append(npc)
        db.occupancies.append(DockingSlipOccupancy(
            station_id=station_id, npc_id=npc.id, slip_class="transient",
            docked_at=datetime.now(UTC) - timedelta(minutes=5),
        ))

        released = npc_trading_service.release_stale_trader_slips(db)

        assert released == 1
        assert db.occupancies == []

    def test_does_not_release_when_within_tenure_and_still_working_here(self):
        db = FakeDB()
        station_id = uuid.uuid4()
        npc = make_npc(daily_schedule=_full_day_block("work_station", "station", station_id))
        db.npcs.append(npc)
        db.occupancies.append(DockingSlipOccupancy(
            station_id=station_id, npc_id=npc.id, slip_class="transient",
            docked_at=datetime.now(UTC) - timedelta(minutes=5),
        ))

        released = npc_trading_service.release_stale_trader_slips(db)

        assert released == 0
        assert len(db.occupancies) == 1

    def test_does_not_release_a_different_stations_work_station_block(self):
        """Still WORK_STATION/station, but at a DIFFERENT station than the
        one this occupancy row is for -- must release (the NPC moved to a
        new stop without the old slip ever being explicitly freed)."""
        db = FakeDB()
        old_station_id = uuid.uuid4()
        new_station_id = uuid.uuid4()
        npc = make_npc(daily_schedule=_full_day_block("work_station", "station", new_station_id))
        db.npcs.append(npc)
        db.occupancies.append(DockingSlipOccupancy(
            station_id=old_station_id, npc_id=npc.id, slip_class="transient",
            docked_at=datetime.now(UTC) - timedelta(minutes=5),
        ))

        released = npc_trading_service.release_stale_trader_slips(db)

        assert released == 1

    def test_releases_when_owning_npc_is_missing(self):
        db = FakeDB()
        db.occupancies.append(DockingSlipOccupancy(
            station_id=uuid.uuid4(), npc_id=uuid.uuid4(), slip_class="transient",
            docked_at=datetime.now(UTC) - timedelta(minutes=5),
        ))
        # no matching db.npcs row -- simulates a deleted/never-loaded NPC

        released = npc_trading_service.release_stale_trader_slips(db)

        assert released == 1

    def test_releases_when_owning_npc_is_kia(self):
        db = FakeDB()
        station_id = uuid.uuid4()
        npc = make_npc(
            daily_schedule=_full_day_block("work_station", "station", station_id),
            lifecycle_stage=NPCLifecycleStage.KIA,
        )
        db.npcs.append(npc)
        db.occupancies.append(DockingSlipOccupancy(
            station_id=station_id, npc_id=npc.id, slip_class="transient",
            docked_at=datetime.now(UTC) - timedelta(minutes=5),
        ))

        released = npc_trading_service.release_stale_trader_slips(db)

        assert released == 1

    def test_ignores_player_owned_rows_entirely(self):
        db = FakeDB()
        db.occupancies.append(DockingSlipOccupancy(
            station_id=uuid.uuid4(), player_id=uuid.uuid4(), slip_class="transient",
            docked_at=datetime.now(UTC) - timedelta(hours=100),  # would be "stale" if it were a trader row
        ))

        released = npc_trading_service.release_stale_trader_slips(db)

        assert released == 0
        assert len(db.occupancies) == 1

    def test_no_occupancies_is_a_cheap_no_op(self):
        db = FakeDB()
        assert npc_trading_service.release_stale_trader_slips(db) == 0


# --------------------------------------------------------------------------- #
# (5) run_trade_stop wires the acquire call at the right point.
# --------------------------------------------------------------------------- #

class TestRunTradeStopAcquiresSlip:
    def test_acquire_for_npc_called_with_station_npc_and_ship(self):
        """Spy-based: proves the wiring (station/npc/ship_id threaded
        through correctly) without re-simulating run_trade_stop's whole
        economic engine, which has no existing FakeSession scaffolding to
        build on and is out of this WO's scope."""
        db = FakeDB()
        station_id = uuid.uuid4()
        station = make_station(station_id=station_id)
        station.sector_id = 42
        station.commodities = {}  # nothing to buy/sell -- traded stays False
        station.owner_id = None  # unowned -- _station_tax_rate short-circuits to 0.0
        db.stations.append(station)

        ship_id = uuid.uuid4()
        ship = SimpleNamespace(id=ship_id, is_destroyed=False, cargo={"capacity": 0, "used": 0, "contents": {}})
        npc = make_npc()
        npc.current_sector_id = 42
        npc.ship_id = ship_id
        npc.credits = 0
        npc.daily_schedule = {}

        # run_trade_stop's own DB queries: Station (already in db.stations),
        # Ship (needs its own row list -- reuse FakeDB.players list is wrong;
        # extend inline since Ship isn't in FakeDB's known models).
        from src.models.ship import Ship as ShipModel
        original_query = db.query

        def query_with_ship(model):
            if model is ShipModel:
                return _FakeQuery([ship])
            return original_query(model)
        db.query = query_with_ship

        with patch.object(docking_service, "acquire_for_npc", return_value=True) as mock_acquire:
            npc_trading_service.run_trade_stop(
                db, npc, {"station_id": str(station_id), "sector_id": 42, "buy_here": []},
            )

        mock_acquire.assert_called_once()
        call_args = mock_acquire.call_args
        assert call_args.args[0] is db
        assert call_args.args[1] is station
        assert call_args.args[2] is npc
        assert call_args.kwargs.get("ship_id") == ship_id
