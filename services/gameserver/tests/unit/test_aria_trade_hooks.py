"""DB-free pins for the ARIA trade hooks (WO-ARIA-OBS-LOG Lane C):
routes/trading.py's ``_record_aria_trade_hooks`` helper, wired into both the
buy and sell completion paths.

Scope: this lane owns trading.py's CALL SITE only -- not the real
``aria_personal_intelligence_service.py`` internals (lane A/B's file). Both
ARIA surfaces (``record_trade_memory``, ``record_trade_observation``) are
therefore SPIED via a fake service object substituted for
``get_aria_intelligence_service()``, rather than exercised for real -- this
isolates the pins to "did trading.py call the right surface with the right
data, and does a raise from either surface still leave the trade intact",
which is exactly what this lane is responsible for proving.

Harness: reuses test_trading_core_pins.py's proven DB-free
_FakeSession/_FakeQuery/_neutral_player/_neutral_station/_ship/_market_price
convention for calling the REAL buy_resource/sell_resource route coroutines
directly (no test-file-to-test-file import -- each trading.py test file
keeps its own self-contained harness, matching that file's own precedent of
not sharing fixtures via a conftest).
"""
from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, patch

import pytest

from src.api.routes.trading import TradeRequest, buy_resource, sell_resource
from src.models.market_transaction import MarketPrice, MarketTransaction
from src.models.player import Player
from src.models.ship import Ship, ShipType
from src.models.station import Station, StationClass, StationStatus, StationType

# ---------------------------------------------------------------------------
# Fake DB session (mirrors test_trading_core_pins.py's _FakeQuery/_FakeSession)
# ---------------------------------------------------------------------------


class _FakeQuery:
    def __init__(self, *, first: Any = None, seq=None) -> None:
        self._first = first
        self._seq = list(seq) if seq is not None else None

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


def _market_price(station_id, *, buy_price, sell_price, quantity) -> MarketPrice:
    return MarketPrice(
        id=uuid.uuid4(), station_id=station_id, commodity="ore",
        buy_price=buy_price, sell_price=sell_price, quantity=quantity,
    )


def _session_for(player: Player, station: Station, ship: Ship, market_price: MarketPrice,
                  *, player_seq_len: int) -> _FakeSession:
    return _FakeSession({
        Station: _FakeQuery(first=station),
        Player: _FakeQuery(seq=[player, None] * player_seq_len),
        Ship: _FakeQuery(first=ship),
        MarketPrice: _FakeQuery(first=market_price),
    })


def _added_transaction(db: _FakeSession) -> MarketTransaction:
    txs = [obj for obj in db.added if isinstance(obj, MarketTransaction)]
    assert len(txs) == 1, f"expected exactly one MarketTransaction added, got {len(txs)}"
    return txs[0]


# ---------------------------------------------------------------------------
# Fake ARIA service -- spies on record_trade_memory_sync / record_trade_observation
# ---------------------------------------------------------------------------


class _FakeARIAService:
    """Stands in for ARIAPersonalIntelligenceService. Both surfaces are sync,
    matching the real record_trade_memory_sync (aria_personal_intelligence_
    service.py:444, the sync twin of record_trade_memory) and
    record_trade_observation (:2061). Each call is recorded verbatim for
    inspection; either can be scripted to raise, to pin the non-blocking
    contract."""

    def __init__(self) -> None:
        self.memory_calls: List[Dict[str, Any]] = []
        self.observation_calls: List[Dict[str, Any]] = []
        self.memory_raises: Optional[Exception] = None
        self.observation_raises: Optional[Exception] = None

    def record_trade_memory_sync(self, player_id, trade_data, db):
        self.memory_calls.append({"player_id": player_id, "trade_data": trade_data, "db": db})
        if self.memory_raises is not None:
            raise self.memory_raises

    def record_trade_observation(self, player_id, trade_result, db):
        self.observation_calls.append({"player_id": player_id, "trade_result": trade_result, "db": db})
        if self.observation_raises is not None:
            raise self.observation_raises


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
