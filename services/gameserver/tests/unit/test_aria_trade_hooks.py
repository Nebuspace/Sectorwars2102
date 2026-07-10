"""DB-free pins for the ARIA trade + market-observation hooks in
routes/trading.py:

- WO-ARIA-OBS-LOG: ``_record_aria_trade_hooks``, wired into the buy and sell
  completion paths.
- WO-ARIA-MARKET-OBS: ``_record_aria_market_observation``, wired into
  ``get_market_info`` and ``dock_at_station``.

Scope: this lane owns trading.py's CALL SITES only -- not the real
``aria_personal_intelligence_service.py`` internals (lane B's file). Every
ARIA surface (``record_trade_memory_sync``, ``record_trade_observation``,
``record_market_observation_sync``) is therefore SPIED via a fake service
object substituted for ``get_aria_intelligence_service()``, rather than
exercised for real -- this isolates the pins to "did trading.py call the
right surface with the right data, and does a raise from either surface
still leave the route intact", which is exactly what this lane is
responsible for proving.

Harness: reuses test_trading_core_pins.py's proven DB-free
_FakeSession/_FakeQuery/_neutral_player/_neutral_station/_ship/_market_price
convention for calling the REAL route coroutines directly (no
test-file-to-test-file import -- each trading.py test file keeps its own
self-contained harness, matching that file's own precedent of not sharing
fixtures via a conftest).

``dock_at_station`` note: unlike buy/sell/get_market_info, this route also
touches docking_service.acquire/_realize_fee/ship_size_for and
turn_service.regenerate_turns -- faking all of that DB surface just to
prove a 3-line hook addition is disproportionate and risks a fake harness
that doesn't reflect the real service. Its coverage here is therefore: (1)
a full DB-free unit test of the shared ``_record_aria_market_observation``
helper (identical code path both routes call, including the exception-
isolation contract), plus (2) structural/source pins proving the hook is
wired at the right point in ``dock_at_station`` (before its single commit,
never preceded by ``_ensure_market_prices``). ``get_market_info`` gets full
route-level coverage since its DB surface is tractable.
"""
from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, patch

import pytest

from src.api.routes.trading import (
    TradeRequest,
    _record_aria_market_observation,
    buy_resource,
    get_market_info,
    sell_resource,
)
from src.models.market_transaction import MarketPrice, MarketTransaction
from src.models.player import Player
from src.models.ship import Ship, ShipType
from src.models.station import Station, StationClass, StationStatus, StationType

# ---------------------------------------------------------------------------
# Fake DB session (mirrors test_trading_core_pins.py's _FakeQuery/_FakeSession)
# ---------------------------------------------------------------------------


class _FakeQuery:
    def __init__(self, *, first: Any = None, seq=None, all_results=None) -> None:
        self._first = first
        self._seq = list(seq) if seq is not None else None
        self._all = list(all_results) if all_results is not None else []

    def filter(self, *a: Any, **k: Any) -> "_FakeQuery":
        return self

    def populate_existing(self) -> "_FakeQuery":
        return self

    def with_for_update(self, *a: Any, **k: Any) -> "_FakeQuery":
        return self

    def first(self) -> Any:
        if self._seq is not None:
            return self._seq.pop(0) if self._seq else None
        return self._first

    def all(self) -> list:
        return self._all


class _FakeSession:
    """Same shape as test_trading_core_pins.py's _FakeSession, plus one
    addition: .add() backfills a client-side UUID `id` default the way a
    real flush would (Column(default=uuid.uuid4) is only applied by
    SQLAlchemy's real flush machinery -- a fake session never triggers it,
    so without this the MarketTransaction this lane threads into the
    observation's `trade_id` would stay None here even though it's real
    at runtime)."""

    def __init__(self, specs: Dict[type, _FakeQuery]) -> None:
        self._specs = specs
        self.added: List[Any] = []
        self.commit_calls = 0
        self.flush_calls = 0

    def query(self, target: Any) -> _FakeQuery:
        key = target if isinstance(target, type) else target.class_
        assert key in self._specs, f"unexpected query for {target!r}"
        return self._specs[key]

    def add(self, obj: Any) -> None:
        if getattr(obj, "id", None) is None:
            obj.id = uuid.uuid4()
        self.added.append(obj)

    def commit(self) -> None:
        self.commit_calls += 1

    def flush(self) -> None:
        self.flush_calls += 1

    def rollback(self) -> None:
        pass


def _neutral_player(*, credits: int, turns: int = 50) -> Player:
    return Player(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        credits=credits,
        turns=turns,
        current_sector_id=7,
        current_ship_id=uuid.uuid4(),
        is_docked=True,
        military_rank="Recruit",
        reputation_tier="Neutral",
        personal_reputation=0,
        settings={},
        team_id=None,
        aria_total_interactions=0,
        aria_consciousness_level=1,
        aria_bonus_multiplier=1.0,
    )


def _neutral_station() -> Station:
    return Station(
        id=uuid.uuid4(),
        name="Neutral Station",
        sector_id=7,
        station_class=StationClass.CLASS_1,
        type=StationType.TRADING,
        status=StationStatus.OPERATIONAL,
        commodities={},
        faction_affiliation=None,
        region_id=None,
        owner_id=None,
        tax_rate=None,
    )


def _ship(*, capacity=100, used=0, contents=None) -> Ship:
    return Ship(
        id=uuid.uuid4(),
        name="Test Hauler",
        type=ShipType.CARGO_HAULER,
        base_speed=1.0,
        current_speed=1.0,
        turn_cost=1,
        sector_id=7,
        maintenance={"condition": 80.0},
        cargo={"capacity": capacity, "used": used, "contents": dict(contents or {})},
        combat={},
    )


def _market_price(station_id, *, buy_price, sell_price, quantity, commodity="ore") -> MarketPrice:
    return MarketPrice(
        id=uuid.uuid4(), station_id=station_id, commodity=commodity,
        buy_price=buy_price, sell_price=sell_price, quantity=quantity,
        # price_trend has a Column(default=0.0) that only applies on a REAL
        # flush (never triggered by this fake session) -- get_market_info's
        # response building does float(price.price_trend), which raises on
        # a bare None. Set explicitly so a fake row behaves like a real one.
        price_trend=0.0,
    )


def _session_for(player: Player, station: Station, ship: Ship, market_price: MarketPrice,
                  *, player_seq_len: int) -> _FakeSession:
    return _FakeSession({
        Station: _FakeQuery(first=station),
        Player: _FakeQuery(seq=[player, None] * player_seq_len),
        Ship: _FakeQuery(first=ship),
        MarketPrice: _FakeQuery(first=market_price),
    })


def _market_info_session_for(station: Station, market_price_rows: list) -> _FakeSession:
    """get_market_info's DB surface: Station (._get_station_or_404) +
    MarketPrice.all() (the resources loop). station.commodities={}
    (_neutral_station) short-circuits _ensure_market_prices before it ever
    touches MarketPrice, so no MarketPrice.first() spec is needed here."""
    return _FakeSession({
        Station: _FakeQuery(first=station),
        MarketPrice: _FakeQuery(all_results=market_price_rows),
    })


def _added_transaction(db: _FakeSession) -> MarketTransaction:
    txs = [obj for obj in db.added if isinstance(obj, MarketTransaction)]
    assert len(txs) == 1, f"expected exactly one MarketTransaction added, got {len(txs)}"
    return txs[0]


# ---------------------------------------------------------------------------
# Fake ARIA service -- spies on record_trade_memory_sync / record_trade_observation
# ---------------------------------------------------------------------------


class _FakeARIAService:
    """Stands in for ARIAPersonalIntelligenceService. All three surfaces are
    sync: record_trade_memory_sync (aria_personal_intelligence_service.py:
    444, the sync twin of record_trade_memory), record_trade_observation
    (:2061), and record_market_observation_sync (proposed WO-ARIA-MARKET-OBS
    contract -- adjust here the moment lane B's final signature lands). Each
    call is recorded verbatim for inspection; any of the three can be
    scripted to raise, to pin the non-blocking contract."""

    def __init__(self) -> None:
        self.memory_calls: List[Dict[str, Any]] = []
        self.observation_calls: List[Dict[str, Any]] = []
        self.market_observation_calls: List[Dict[str, Any]] = []
        self.memory_raises: Optional[Exception] = None
        self.observation_raises: Optional[Exception] = None
        self.market_observation_raises: Optional[Exception] = None

    def record_trade_memory_sync(self, player_id, trade_data, db):
        self.memory_calls.append({"player_id": player_id, "trade_data": trade_data, "db": db})
        if self.memory_raises is not None:
            raise self.memory_raises

    def record_trade_observation(self, player_id, trade_result, db):
        self.observation_calls.append({"player_id": player_id, "trade_result": trade_result, "db": db})
        if self.observation_raises is not None:
            raise self.observation_raises

    def record_market_observation_sync(self, player_id, station_id, market_prices, db):
        self.market_observation_calls.append({
            "player_id": player_id, "station_id": station_id,
            "market_prices": market_prices, "db": db,
        })
        if self.market_observation_raises is not None:
            raise self.market_observation_raises


@pytest.fixture
def fake_aria():
    fake = _FakeARIAService()
    with patch(
        "src.services.aria_personal_intelligence_service.get_aria_intelligence_service",
        return_value=fake,
    ):
        yield fake


@pytest.fixture(autouse=True)
def _quiet_websocket_pushes():
    """Same suppression as test_trading_core_pins.py -- the route's
    post-commit real-time pushes are unrelated to this lane."""
    with patch("src.api.routes.trading._publish_trade_tick", new=AsyncMock(return_value=None)), \
         patch("src.api.routes.trading._emit_transaction_completed", new=AsyncMock(return_value=None)):
        yield


# ---------------------------------------------------------------------------
# Acceptance: one memory call + one observation call per completed trade
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestAriaTradeHooksBuy:
    async def test_buy_produces_exactly_one_memory_and_one_observation_call(self, fake_aria):
        player = _neutral_player(credits=10_000)
        station = _neutral_station()
        ship = _ship(capacity=100)
        mp = _market_price(station.id, buy_price=20, sell_price=30, quantity=500)
        db = _session_for(player, station, ship, mp, player_seq_len=1)

        await buy_resource(
            trade_request=TradeRequest(station_id=str(station.id), resource_type="ore", quantity=10),
            db=db, current_user=None, current_player=player,
        )

        assert len(fake_aria.memory_calls) == 1
        assert len(fake_aria.observation_calls) == 1

        mem = fake_aria.memory_calls[0]
        assert mem["player_id"] == str(player.id)
        assert mem["trade_data"]["action"] == "buy"
        assert mem["trade_data"]["commodity"] == "ore"
        assert mem["trade_data"]["quantity"] == 10
        assert mem["trade_data"]["total_value"] == 300  # 10 * 30 (buy charges sell_price)
        assert mem["db"] is db

        obs = fake_aria.observation_calls[0]
        assert obs["player_id"] == str(player.id)
        tr = obs["trade_result"]
        assert tr["action"] == "buy"
        assert tr["commodity"] == "ore"
        assert tr["quantity"] == 10
        assert tr["unit_price"] == 30
        assert tr["total_credits"] == 300
        assert tr["source_station_id"] == station.id
        assert tr["source_sector_id"] == 7
        assert tr["trade_id"] == _added_transaction(db).id
        assert obs["db"] is db

    async def test_buy_never_repopulates_pending_aria_memories(self, fake_aria):
        player = _neutral_player(credits=10_000)
        station = _neutral_station()
        ship = _ship(capacity=100)
        mp = _market_price(station.id, buy_price=20, sell_price=30, quantity=500)
        db = _session_for(player, station, ship, mp, player_seq_len=1)

        await buy_resource(
            trade_request=TradeRequest(station_id=str(station.id), resource_type="ore", quantity=10),
            db=db, current_user=None, current_player=player,
        )

        assert "pending_aria_memories" not in player.settings


@pytest.mark.asyncio
class TestAriaTradeHooksSell:
    async def test_sell_produces_exactly_one_memory_and_one_observation_call(self, fake_aria):
        player = _neutral_player(credits=1_000)
        station = _neutral_station()
        ship = _ship(capacity=100, used=10, contents={"ore": 10})
        mp = _market_price(station.id, buy_price=20, sell_price=30, quantity=500)
        db = _session_for(player, station, ship, mp, player_seq_len=1)

        await sell_resource(
            trade_request=TradeRequest(station_id=str(station.id), resource_type="ore", quantity=10),
            db=db, current_user=None, current_player=player,
        )

        assert len(fake_aria.memory_calls) == 1
        assert len(fake_aria.observation_calls) == 1

        mem = fake_aria.memory_calls[0]
        assert mem["trade_data"]["action"] == "sell"
        assert mem["trade_data"]["commodity"] == "ore"
        assert mem["trade_data"]["quantity"] == 10
        assert mem["trade_data"]["total_value"] == 200  # 10 * 20 (sell pays buy_price)

        obs = fake_aria.observation_calls[0]
        tr = obs["trade_result"]
        assert tr["action"] == "sell"
        assert tr["unit_price"] == 20
        assert tr["total_credits"] == 200
        assert tr["source_station_id"] == station.id
        assert tr["trade_id"] == _added_transaction(db).id

    async def test_sell_never_repopulates_pending_aria_memories(self, fake_aria):
        player = _neutral_player(credits=1_000)
        station = _neutral_station()
        ship = _ship(capacity=100, used=10, contents={"ore": 10})
        mp = _market_price(station.id, buy_price=20, sell_price=30, quantity=500)
        db = _session_for(player, station, ship, mp, player_seq_len=1)

        await sell_resource(
            trade_request=TradeRequest(station_id=str(station.id), resource_type="ore", quantity=10),
            db=db, current_user=None, current_player=player,
        )

        assert "pending_aria_memories" not in player.settings


# ---------------------------------------------------------------------------
# Acceptance: an ARIA-write raise never fails or rolls back the trade
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestAriaWriteNeverBlocksTrade:
    async def test_memory_raise_does_not_block_buy_or_the_observation_write(self, fake_aria):
        fake_aria.memory_raises = RuntimeError("simulated ARIA memory outage")
        player = _neutral_player(credits=10_000)
        station = _neutral_station()
        ship = _ship(capacity=100)
        mp = _market_price(station.id, buy_price=20, sell_price=30, quantity=500)
        db = _session_for(player, station, ship, mp, player_seq_len=1)

        result = await buy_resource(
            trade_request=TradeRequest(station_id=str(station.id), resource_type="ore", quantity=10),
            db=db, current_user=None, current_player=player,
        )

        # Trade completed normally despite the memory write raising.
        assert result["transaction"]["total_cost"] == 300
        assert player.credits == 10_000 - 300
        assert db.commit_calls == 1
        # The observation write is independent -- it still ran.
        assert len(fake_aria.observation_calls) == 1

    async def test_observation_raise_does_not_block_sell_or_the_memory_write(self, fake_aria):
        fake_aria.observation_raises = RuntimeError("simulated ARIA observation outage")
        player = _neutral_player(credits=1_000)
        station = _neutral_station()
        ship = _ship(capacity=100, used=10, contents={"ore": 10})
        mp = _market_price(station.id, buy_price=20, sell_price=30, quantity=500)
        db = _session_for(player, station, ship, mp, player_seq_len=1)

        result = await sell_resource(
            trade_request=TradeRequest(station_id=str(station.id), resource_type="ore", quantity=10),
            db=db, current_user=None, current_player=player,
        )

        # Trade completed normally despite the observation write raising.
        assert result["transaction"]["total_earnings"] == 200
        assert player.credits == 1_000 + 200
        assert db.commit_calls == 1
        # The memory write is independent -- it still ran.
        assert len(fake_aria.memory_calls) == 1

    async def test_both_writes_raising_still_leaves_the_trade_intact(self, fake_aria):
        fake_aria.memory_raises = RuntimeError("memory outage")
        fake_aria.observation_raises = RuntimeError("observation outage")
        player = _neutral_player(credits=10_000)
        station = _neutral_station()
        ship = _ship(capacity=100)
        mp = _market_price(station.id, buy_price=20, sell_price=30, quantity=500)
        db = _session_for(player, station, ship, mp, player_seq_len=1)

        result = await buy_resource(
            trade_request=TradeRequest(station_id=str(station.id), resource_type="ore", quantity=10),
            db=db, current_user=None, current_player=player,
        )

        assert result["transaction"]["total_cost"] == 300
        assert db.commit_calls == 1


# ---------------------------------------------------------------------------
# WO-ARIA-MARKET-OBS: get_market_info (full route-level coverage)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestAriaMarketObservationGetMarketInfo:
    async def test_get_market_info_records_one_batched_observation_call(self, fake_aria):
        station = _neutral_station()
        rows = [
            _market_price(station.id, commodity="ore", buy_price=20, sell_price=30, quantity=500),
            _market_price(station.id, commodity="equipment", buy_price=80, sell_price=100, quantity=50),
        ]
        player = _neutral_player(credits=10_000)
        db = _market_info_session_for(station, rows)

        await get_market_info(
            station_id=str(station.id), db=db, current_user=None, current_player=player,
        )

        assert len(fake_aria.market_observation_calls) == 1
        call = fake_aria.market_observation_calls[0]
        assert call["player_id"] == str(player.id)
        assert call["station_id"] == str(station.id)
        assert call["db"] is db

        # All commodities batched into the ONE call -- not one call each.
        payload = call["market_prices"]
        assert len(payload) == 2
        by_commodity = {p["commodity"]: p for p in payload}
        assert by_commodity["ore"]["buy_price"] == 20
        assert by_commodity["ore"]["sell_price"] == 30
        assert by_commodity["ore"]["quantity"] == 500
        assert by_commodity["equipment"]["buy_price"] == 80
        assert by_commodity["equipment"]["sell_price"] == 100

    async def test_get_market_info_commits_exactly_once_for_the_observation_write(self, fake_aria):
        """get_market_info is otherwise a pure GET with no commit of its
        own (_neutral_station's commodities={} short-circuits
        _ensure_market_prices before any commit inside it) -- the ONLY
        commit in this route comes from the ARIA hook's explicit commit."""
        station = _neutral_station()
        rows = [_market_price(station.id, commodity="ore", buy_price=20, sell_price=30, quantity=500)]
        player = _neutral_player(credits=10_000)
        db = _market_info_session_for(station, rows)

        await get_market_info(
            station_id=str(station.id), db=db, current_user=None, current_player=player,
        )

        assert db.commit_calls == 1

    async def test_get_market_info_returns_normally_when_observation_write_raises(self, fake_aria):
        fake_aria.market_observation_raises = RuntimeError("simulated ARIA market outage")
        station = _neutral_station()
        rows = [_market_price(station.id, commodity="ore", buy_price=20, sell_price=30, quantity=500)]
        player = _neutral_player(credits=10_000)
        db = _market_info_session_for(station, rows)

        result = await get_market_info(
            station_id=str(station.id), db=db, current_user=None, current_player=player,
        )

        # Route completed normally (200-equivalent: a real MarketInfoResponse,
        # not an exception) despite the observation write raising.
        assert result.resources["ore"]["buy_price"] == 20
        assert db.commit_calls == 1

    async def test_get_market_info_skips_the_call_when_station_has_no_priced_commodities(self, fake_aria):
        """Empty market_price_rows -> _record_aria_market_observation's own
        early-return -- no call at all, not a call with an empty payload."""
        station = _neutral_station()
        player = _neutral_player(credits=10_000)
        db = _market_info_session_for(station, [])

        await get_market_info(
            station_id=str(station.id), db=db, current_user=None, current_player=player,
        )

        assert fake_aria.market_observation_calls == []
        # No pending write -> the route's explicit commit is still a no-op
        # call (harmless), so this isn't asserted either way here.


# ---------------------------------------------------------------------------
# WO-ARIA-MARKET-OBS: shared _record_aria_market_observation helper
# (this IS dock_at_station's observation code path too -- see module
# docstring for why dock_at_station itself isn't route-level tested here)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestAriaMarketObservationHelper:
    async def test_helper_batches_all_rows_into_one_call(self, fake_aria):
        station = _neutral_station()
        player = _neutral_player(credits=10_000)
        rows = [
            _market_price(station.id, commodity="ore", buy_price=20, sell_price=30, quantity=500),
            _market_price(station.id, commodity="fuel", buy_price=5, sell_price=8, quantity=200),
        ]
        db = _FakeSession({})

        await _record_aria_market_observation(db, player, station, rows)

        assert len(fake_aria.market_observation_calls) == 1
        payload = fake_aria.market_observation_calls[0]["market_prices"]
        assert {p["commodity"] for p in payload} == {"ore", "fuel"}

    async def test_helper_never_raises_when_the_service_call_raises(self, fake_aria):
        fake_aria.market_observation_raises = RuntimeError("simulated outage")
        station = _neutral_station()
        player = _neutral_player(credits=10_000)
        rows = [_market_price(station.id, commodity="ore", buy_price=20, sell_price=30, quantity=500)]
        db = _FakeSession({})

        # Must not raise -- this is the exact guarantee dock_at_station's
        # own try/except relies on to keep returning 200 with an injected
        # observation-path exception.
        await _record_aria_market_observation(db, player, station, rows)

        assert len(fake_aria.market_observation_calls) == 1

    async def test_helper_makes_zero_calls_for_an_empty_row_list(self, fake_aria):
        station = _neutral_station()
        player = _neutral_player(credits=10_000)
        db = _FakeSession({})

        await _record_aria_market_observation(db, player, station, [])

        assert fake_aria.market_observation_calls == []


# ---------------------------------------------------------------------------
# WO-ARIA-MARKET-OBS: structural pins for dock_at_station's wiring
# ---------------------------------------------------------------------------


class TestAriaMarketObservationDockStructural:
    """dock_at_station's own DB surface (docking_service.acquire/
    _realize_fee/ship_size_for, turn_service.regenerate_turns) is out of
    this lane's scope to fake -- see module docstring. These pins instead
    prove, from source, that the hook is wired where it needs to be:
    inside the try block, before the route's single commit (so the write
    folds into the docking transaction), and never behind a call to
    _ensure_market_prices (whose own docstring warns it may commit, which
    would split "one session, single commit" mid-flight -- see the inline
    comment at the call site)."""

    def test_hook_call_precedes_the_single_commit(self):
        import inspect

        from src.api.routes import trading as trading_routes

        source = inspect.getsource(trading_routes.dock_at_station)
        hook_index = source.index("_record_aria_market_observation(")
        # dock_at_station has an EARLIER db.commit() too (the "all slips
        # occupied" queue-enqueue early-return branch, before the granted-
        # slip check) -- search from hook_index so this finds the commit
        # AFTER the hook (the real single-commit at the end of the try
        # block), not that unrelated earlier one.
        commit_index = source.index("db.commit()", hook_index)
        assert hook_index < commit_index, (
            "the market-observation hook must run before dock_at_station's "
            "single commit so the write folds into the same transaction"
        )

    def test_dock_at_station_never_calls_ensure_market_prices(self):
        import inspect

        from src.api.routes import trading as trading_routes

        source = inspect.getsource(trading_routes.dock_at_station)
        assert "_ensure_market_prices(" not in source

    def test_hook_reads_market_price_rows_queried_in_the_same_function(self):
        import inspect

        from src.api.routes import trading as trading_routes

        source = inspect.getsource(trading_routes.dock_at_station)
        assert "MarketPrice" in source
        assert "_record_aria_market_observation(db, current_player, station, station_prices)" in source


# ---------------------------------------------------------------------------
# Structural pin: the settings-JSONB stash is fully retired repo-wide
# ---------------------------------------------------------------------------


class TestPendingAriaMemoriesFullyRetired:
    """WO-ARIA-OBS-LOG deletes the pending_aria_memories settings stash (a
    cap-10 list with zero readers) at both trading.py hooks. Comments
    documenting its retirement mentioning the bare word are expected and
    fine (this suite's own docstring does); what must be zero is any LIVE
    dict-key reference -- a subscript or .get() access on that key -- left
    anywhere in src/. Patterns are deliberately narrow (the exact old-code
    access shapes) rather than a bare substring search, so this pin can't
    self-defeat against a comment merely naming the retired key."""

    LIVE_PATTERNS = (
        '"pending_aria_memories"]',
        '.get("pending_aria_memories"',
        "'pending_aria_memories']",
        ".get('pending_aria_memories'",
    )

    def test_zero_live_dict_key_references_in_src(self):
        src_root = Path(__file__).resolve().parents[2] / "src"
        assert src_root.is_dir(), f"expected src/ at {src_root}"

        hits = []
        for path in sorted(src_root.rglob("*.py")):
            text = path.read_text(encoding="utf-8", errors="ignore")
            for lineno, line in enumerate(text.splitlines(), start=1):
                if line.strip().startswith("#"):
                    continue
                for pattern in self.LIVE_PATTERNS:
                    if pattern in line:
                        hits.append(f"{path.relative_to(src_root)}:{lineno}: {line.strip()}")

        assert hits == [], f"live pending_aria_memories dict-key references remain: {hits}"
