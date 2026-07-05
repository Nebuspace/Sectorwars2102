"""WO-GWQ-LUMEN-FAUCET — Lumen Crystal supply chain: player ledger + nebula
harvest drops + Class-5+ Shard-to-Crystal refining.

Canon: sw2102-docs quantum-resources.md:223-237 (Emerald 1% / Crimson 0.2%
harvest drop; Class-5+ refine 100 Shards -> 1 Lumen, 10,000 cr, 12h) +
ADR-0037.

Two collaborators are exercised with hand-built fakes (no DB, no app):

  - quantum_service.harvest_nebula: seeded-RNG proof that only Emerald/
    Crimson roll for a Lumen drop, and that the roll is a real coin-flip
    against the canon rate (not a bypassed/always-on hack). A "never drops"
    color test supplies the fake RNG only ONE `.random()` return value —
    if the drop-roll code path ever called `.random()` a second time for a
    0%-rate color, the fake would raise StopIteration and fail loudly,
    proving the short-circuit rather than just observing its output.

  - refining_service.start_lumen_refine / collect_lumen_refine: venue gate,
    shard/credit gates, exact debit, single in-flight job slot, and the 12h
    timer (collect rejects before the deadline, credits exactly 1 Lumen
    Crystal after it).

Plus a static check that the Player.lumen_crystals column is additive
(NOT NULL, server_default '0') so existing rows backfill to 0 on migration.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

import pytest

from src.models.cluster import Cluster
from src.models.player import Player
from src.models.sector import Sector, SectorType
from src.models.ship import Ship
from src.models.station import Station, StationClass
from src.services import quantum_service, refining_service
from src.services.refining_service import RefiningError

# --- shared fakes ---------------------------------------------------------


class _FakeQuery:
    """Stands in for a SQLAlchemy Query — filter()/populate_existing()/
    with_for_update() are no-ops that return self; first() returns the
    pre-wired result regardless of the filter predicate (the test already
    controls exactly what's in the fake session)."""

    def __init__(self, result: Any) -> None:
        self._result = result

    def filter(self, *args: Any, **kwargs: Any) -> "_FakeQuery":
        return self

    def populate_existing(self) -> "_FakeQuery":
        return self

    def with_for_update(self) -> "_FakeQuery":
        return self

    def first(self) -> Any:
        return self._result


class _FakeSession:
    """Maps a model class to the single fake row db.query(Model) should
    return. flush()/commit()/rollback() are no-ops — both services under
    test are FLUSH-ONLY (the route owns the commit)."""

    def __init__(self, rows: Dict[type, Any]) -> None:
        self._rows = rows
        self.flushed = False

    def query(self, model: type) -> _FakeQuery:
        assert model in self._rows, f"unexpected query for {model!r}"
        return _FakeQuery(self._rows[model])

    def flush(self) -> None:
        self.flushed = True


class _SeqRNG:
    """A fake `_RNG` exposing exactly the two methods harvest_nebula calls:
    randint() (shard yield, fixed) and random() (crit roll, then lumen
    roll) drawn from a supplied sequence. Supplying too few values makes a
    surprise extra call raise StopIteration instead of silently succeeding —
    used deliberately by the "never drops" tests below."""

    def __init__(self, randint_value: int, random_values: List[float]) -> None:
        self._randint_value = randint_value
        self._random_iter = iter(random_values)

    def randint(self, lo: int, hi: int) -> int:
        return self._randint_value

    def random(self) -> float:
        return next(self._random_iter)


def _fake_player(**overrides: Any) -> SimpleNamespace:
    base = dict(
        id=uuid.uuid4(),
        turns=100,
        lifetime_turns_spent=0,
        quantum_shards=0,
        lumen_crystals=0,
        current_sector_id=1,
        is_docked=False,
        current_port_id=None,
        credits=0,
        lumen_refine_ready_at=None,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _fake_ship(**overrides: Any) -> SimpleNamespace:
    base = dict(
        id=uuid.uuid4(),
        is_destroyed=False,
        quantum_harvester_slot=True,
        quantum_harvest_cooldown_until=None,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _fake_station(**overrides: Any) -> SimpleNamespace:
    base = dict(
        id=uuid.uuid4(),
        name="Test Station",
        is_spacedock=False,
        station_class=StationClass.CLASS_5,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


@pytest.fixture(autouse=True)
def _no_op_harvest_collaborators(monkeypatch: pytest.MonkeyPatch) -> None:
    """Neuters the two collaborators harvest_nebula calls that would
    otherwise hit a real DB (turn regen's medal-bonus lookup, emergent-rep
    dispatch) — neither is what this WO's tests are proving."""
    monkeypatch.setattr(quantum_service, "regenerate_turns", lambda db, player: {})
    monkeypatch.setattr(
        quantum_service, "apply_emergent_action", lambda db, player, action, payload: None
    )


def _harvest(
    monkeypatch: pytest.MonkeyPatch,
    nebula_type: str,
    randint_value: int,
    random_values: List[float],
    player: Optional[SimpleNamespace] = None,
) -> Dict[str, Any]:
    player = player or _fake_player()
    ship = _fake_ship()
    player.current_ship = ship
    sector = SimpleNamespace(
        sector_id=player.current_sector_id, type=SectorType.NEBULA, cluster_id=1
    )
    cluster = SimpleNamespace(id=1, nebula_type=nebula_type)
    db = _FakeSession({Player: player, Ship: ship, Sector: sector, Cluster: cluster})
    monkeypatch.setattr(quantum_service, "_RNG", _SeqRNG(randint_value, random_values))
    return quantum_service.harvest_nebula(db, player.id)


# --- harvest-drop: Emerald/Crimson roll, all other colors never ----------


@pytest.mark.unit
class TestLumenHarvestDrop:
    def test_emerald_forced_roll_drops_lumen(self, monkeypatch: pytest.MonkeyPatch) -> None:
        player = _fake_player()
        # random_values: [0] crit roll (0.5 >= HARVEST_CRIT_RATE, no crit),
        # [1] lumen roll (0.005 < 0.01 emerald rate -> hit).
        result = _harvest(
            monkeypatch, "emerald", randint_value=1, random_values=[0.5, 0.005], player=player
        )

        assert result["lumen_dropped"] is True
        assert player.lumen_crystals == 1
        assert result["lumen_crystals"] == 1

    def test_emerald_miss_does_not_drop_lumen(self, monkeypatch: pytest.MonkeyPatch) -> None:
        player = _fake_player()
        result = _harvest(
            monkeypatch, "emerald", randint_value=1, random_values=[0.5, 0.5], player=player
        )

        assert result["lumen_dropped"] is False
        assert player.lumen_crystals == 0
        assert result["lumen_crystals"] == 0

    def test_crimson_forced_roll_drops_lumen_at_0_2_percent(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        player = _fake_player()
        # Crimson rate is 0.002 — 0.005 would MISS at emerald's 0.01 band
        # test above but must MISS here too (0.005 >= 0.002); use 0.001 to
        # force the tighter band's hit.
        result = _harvest(
            monkeypatch, "crimson", randint_value=2, random_values=[0.5, 0.001], player=player
        )

        assert result["lumen_dropped"] is True
        assert player.lumen_crystals == 1

    def test_crimson_just_above_rate_misses(self, monkeypatch: pytest.MonkeyPatch) -> None:
        player = _fake_player()
        result = _harvest(
            monkeypatch, "crimson", randint_value=2, random_values=[0.5, 0.002], player=player
        )

        assert result["lumen_dropped"] is False
        assert player.lumen_crystals == 0

    @pytest.mark.parametrize("nebula_type", ["azure", "violet", "amber", "obsidian"])
    def test_other_colors_never_drop_lumen(
        self, monkeypatch: pytest.MonkeyPatch, nebula_type: str
    ) -> None:
        player = _fake_player()
        # Only ONE random() value supplied (the crit roll). If the
        # implementation ever called _RNG.random() a second time to roll a
        # Lumen drop for a 0%-rate color, next() would raise StopIteration
        # and this test would fail loudly rather than just asserting False.
        result = _harvest(
            monkeypatch, nebula_type, randint_value=1, random_values=[0.5], player=player
        )

        assert result["lumen_dropped"] is False
        assert player.lumen_crystals == 0

    def test_lumen_dropped_and_balance_included_in_payload_keys(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        result = _harvest(monkeypatch, "emerald", randint_value=1, random_values=[0.99, 0.99])
        assert "lumen_dropped" in result
        assert "lumen_crystals" in result


# --- Player.lumen_crystals column: additive, defaults 0 -------------------


@pytest.mark.unit
class TestLumenColumnDefaults:
    def test_lumen_crystals_column_is_additive_not_null_default_zero(self) -> None:
        col = Player.__table__.c.lumen_crystals
        assert col.nullable is False
        assert col.server_default is not None
        assert col.server_default.arg.text == "0"

    def test_lumen_refine_ready_at_column_is_nullable(self) -> None:
        col = Player.__table__.c.lumen_refine_ready_at
        assert col.nullable is True


# --- Class-5+ refine: venue gate, shard/credit gates, exact debit --------


@pytest.mark.unit
class TestStartLumenRefine:
    def _db(self, player: SimpleNamespace, station: Optional[SimpleNamespace]) -> _FakeSession:
        return _FakeSession({Player: player, Station: station})

    def test_rejects_class_below_5(self) -> None:
        player = _fake_player(
            is_docked=True, current_port_id=uuid.uuid4(), quantum_shards=1000, credits=1_000_000
        )
        station = _fake_station(station_class=StationClass.CLASS_4, is_spacedock=False)
        db = self._db(player, station)

        with pytest.raises(RefiningError, match="Class-5"):
            refining_service.start_lumen_refine(db, player.id)

    def test_spacedock_qualifies_even_below_class_5(self) -> None:
        player = _fake_player(
            is_docked=True, current_port_id=uuid.uuid4(), quantum_shards=1000, credits=1_000_000
        )
        station = _fake_station(station_class=StationClass.CLASS_1, is_spacedock=True)
        db = self._db(player, station)

        result = refining_service.start_lumen_refine(db, player.id)
        assert result["shards_spent"] == 100
        assert player.lumen_refine_ready_at is not None

    def test_rejects_insufficient_shards(self) -> None:
        player = _fake_player(
            is_docked=True, current_port_id=uuid.uuid4(), quantum_shards=50, credits=1_000_000
        )
        station = _fake_station()
        db = self._db(player, station)

        with pytest.raises(RefiningError, match="100 Quantum Shards"):
            refining_service.start_lumen_refine(db, player.id)
        assert player.quantum_shards == 50  # untouched — rejected before debit

    def test_rejects_insufficient_credits(self) -> None:
        player = _fake_player(
            is_docked=True, current_port_id=uuid.uuid4(), quantum_shards=1000, credits=500
        )
        station = _fake_station()
        db = self._db(player, station)

        with pytest.raises(RefiningError, match="10,000 credits"):
            refining_service.start_lumen_refine(db, player.id)
        assert player.credits == 500  # untouched — rejected before debit

    def test_debits_exactly_100_shards_10k_credits_and_arms_12h_deadline(self) -> None:
        player = _fake_player(
            is_docked=True, current_port_id=uuid.uuid4(), quantum_shards=150, credits=20_000
        )
        station = _fake_station()
        db = self._db(player, station)

        before = datetime.now(timezone.utc)
        result = refining_service.start_lumen_refine(db, player.id)

        assert player.quantum_shards == 50
        assert player.credits == 10_000
        assert result["shards_spent"] == 100
        assert result["credits_spent"] == 10_000
        assert player.lumen_refine_ready_at is not None
        # 12h canonical, scaled through game_time — always >= "now" at start
        # regardless of the active GAME_TIME_SCALE, and matches the returned
        # ISO string.
        assert player.lumen_refine_ready_at > before
        assert result["lumen_refine_ready_at"] == player.lumen_refine_ready_at.isoformat()

    def test_rejects_second_job_while_one_in_flight(self) -> None:
        player = _fake_player(
            is_docked=True,
            current_port_id=uuid.uuid4(),
            quantum_shards=1000,
            credits=1_000_000,
            lumen_refine_ready_at=datetime.now(timezone.utc) + timedelta(hours=1),
        )
        station = _fake_station()
        db = self._db(player, station)

        with pytest.raises(RefiningError, match="already in progress"):
            refining_service.start_lumen_refine(db, player.id)
        assert player.quantum_shards == 1000  # untouched


@pytest.mark.unit
class TestCollectLumenRefine:
    def _db(self, player: SimpleNamespace) -> _FakeSession:
        return _FakeSession({Player: player})

    def test_rejects_when_no_job_in_flight(self) -> None:
        player = _fake_player(lumen_refine_ready_at=None)
        db = self._db(player)

        with pytest.raises(RefiningError, match="No Lumen Crystal refine job"):
            refining_service.collect_lumen_refine(db, player.id)

    def test_rejects_before_deadline(self) -> None:
        player = _fake_player(
            lumen_refine_ready_at=datetime.now(timezone.utc) + timedelta(hours=1)
        )
        db = self._db(player)

        with pytest.raises(RefiningError, match="not ready yet"):
            refining_service.collect_lumen_refine(db, player.id)
        assert player.lumen_crystals == 0

    def test_credits_exactly_one_lumen_after_deadline_and_clears_slot(self) -> None:
        player = _fake_player(
            lumen_crystals=5,
            lumen_refine_ready_at=datetime.now(timezone.utc) - timedelta(minutes=1),
        )
        db = self._db(player)

        result = refining_service.collect_lumen_refine(db, player.id)

        assert player.lumen_crystals == 6
        assert player.lumen_refine_ready_at is None
        assert result["lumen_crystals"] == 6
        assert result["lumen_yield"] == 1


@pytest.mark.unit
class TestLumenRefineStatus:
    def test_no_job_reports_not_pending(self) -> None:
        player = _fake_player(lumen_refine_ready_at=None)
        status = refining_service.get_lumen_refine_status(player)
        assert status == {"pending": False, "ready_at": None, "collectible": False}

    def test_pending_job_before_deadline_is_not_collectible(self) -> None:
        ready_at = datetime.now(timezone.utc) + timedelta(hours=1)
        player = _fake_player(lumen_refine_ready_at=ready_at)
        status = refining_service.get_lumen_refine_status(player)
        assert status["pending"] is True
        assert status["collectible"] is False
        assert status["ready_at"] == ready_at.isoformat()

    def test_pending_job_past_deadline_is_collectible(self) -> None:
        ready_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        player = _fake_player(lumen_refine_ready_at=ready_at)
        status = refining_service.get_lumen_refine_status(player)
        assert status["collectible"] is True
