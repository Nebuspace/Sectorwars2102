"""DB-free pins for WO-CMB-SALVAGE-LOOP-1 Lane 2: the turn-cost gate added to
``salvage_service.salvage_wreck`` (1 turn / 100 units, rounded up,
ships.md:275-277).

Determinism ("frozen datetimes -- no sleeps", per the WO):
  - ``_frozen_now`` (autouse) monkeypatches ``salvage_service.datetime`` to a
    subclass whose ``.now()`` always returns ``FROZEN_NOW`` -- the grace-
    window math and the Suspect timestamp this module itself computes are
    therefore exact, not "close enough to real time".
  - ``turn_service.regenerate_turns`` lives in a SEPARATE module with its
    own real-clock ``datetime.now()`` call that this file does not own/
    patch. Rather than reach into another lane's module, every player
    fixture anchors ``last_turn_regeneration`` far in the FUTURE
    (``_FAR_FUTURE_ANCHOR``) -- ``regenerate_turns``'s own documented
    "clock-skew guard" (anchor ahead of now -> negative elapsed -> 0 turns
    added, no mutation) makes lazy regen a guaranteed no-op regardless of
    the real wall clock at test-run time. ``player.turns`` in each fixture
    is therefore the exact, sole source of truth for the turn math below.

Harness: same DB-free _FakeQuery/_FakeSession convention as
test_aria_trade_hooks.py / test_trading_core_pins.py (each trading-adjacent
test file keeps its own self-contained harness). The medal-turn-regen-bonus
read inside ``regenerate_turns`` queries a model this harness doesn't map,
which is BY DESIGN -- ``_medal_turn_regen_bonus`` already wraps that read in
its own try/except and degrades to a 0.0 bonus on any failure (proven
pattern, see test_trading_core_pins.py's docstring for the same trick).
"""
from __future__ import annotations

import math
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict

import pytest

from src.models.cargo_wreck import CargoWreck, WreckCause
from src.models.player import Player
from src.models.sector import Sector
from src.models.ship import Ship, ShipType
from src.services import salvage_service

FROZEN_NOW = datetime(2026, 7, 10, 12, 0, 0, tzinfo=timezone.utc)
_FAR_FUTURE_ANCHOR = datetime(2099, 1, 1, tzinfo=timezone.utc)


class _FrozenDateTime(datetime):
    """Subclasses the real ``datetime`` (not a Mock) so every OTHER method
    (``.replace``, arithmetic, comparisons) keeps working exactly as normal
    -- only ``.now()`` is overridden."""

    @classmethod
    def now(cls, tz=None):
        return FROZEN_NOW


@pytest.fixture(autouse=True)
def _frozen_now(monkeypatch):
    monkeypatch.setattr(salvage_service, "datetime", _FrozenDateTime)


# ---------------------------------------------------------------------------
# Fake DB session
# ---------------------------------------------------------------------------


class _FakeQuery:
    def __init__(self, *, first: Any = None) -> None:
        self._first = first

    def filter(self, *a: Any, **k: Any) -> "_FakeQuery":
        return self

    def with_for_update(self, *a: Any, **k: Any) -> "_FakeQuery":
        return self

    def first(self) -> Any:
        return self._first


class _FakeSession:
    def __init__(self, specs: Dict[type, _FakeQuery]) -> None:
        self._specs = specs
        self.deleted: list = []
        self.commit_calls = 0
        self.commit_raises: Exception | None = None

    def query(self, target: Any) -> _FakeQuery:
        assert target in self._specs, f"unexpected query for {target!r}"
        return self._specs[target]

    def delete(self, obj: Any) -> None:
        self.deleted.append(obj)

    def commit(self) -> None:
        self.commit_calls += 1
        if self.commit_raises is not None:
            raise self.commit_raises

    def rollback(self) -> None:
        pass


def _player(*, turns: int, max_turns: int = 1000, team_id=None, is_suspect: bool = False) -> Player:
    return Player(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        turns=turns,
        max_turns=max_turns,
        lifetime_turns_spent=0,
        credits=0,
        current_sector_id=5,
        current_region_id=None,
        current_ship_id=uuid.uuid4(),
        team_id=team_id,
        is_suspect=is_suspect,
        suspect_declared_at=None,
        aria_bonus_multiplier=1.0,
        # See module docstring: guarantees regenerate_turns is a no-op.
        last_turn_regeneration=_FAR_FUTURE_ANCHOR,
    )


def _ship(*, capacity: int, used: int = 0, contents=None) -> Ship:
    return Ship(
        id=uuid.uuid4(),
        name="Salvage Hauler",
        type=ShipType.CARGO_HAULER,
        base_speed=1.0,
        current_speed=1.0,
        turn_cost=1,
        sector_id=5,
        maintenance={"condition": 80.0},
        cargo={"capacity": capacity, "used": used, "contents": dict(contents or {})},
        combat={},
    )


def _sector(*, sector_num: int = 5) -> Sector:
    return Sector(id=uuid.uuid4(), sector_id=sector_num, name="Test Sector", region_id=None)


def _wreck(*, sector_uuid, cargo: Dict[str, int], created_at=FROZEN_NOW,
           original_owner_id=None, original_team_id=None, killing_blow_pilot_id=None,
           cause: WreckCause = WreckCause.COMBAT,
           destroyed_ship_type: ShipType = ShipType.CARGO_HAULER) -> CargoWreck:
    return CargoWreck(
        id=uuid.uuid4(),
        sector_id=sector_uuid,
        original_owner_id=original_owner_id,
        original_team_id=original_team_id,
        killing_blow_pilot_id=killing_blow_pilot_id,
        destroyed_ship_id=None,
        destroyed_ship_type=destroyed_ship_type,
        cargo=dict(cargo),
        created_at=created_at,
        cause=cause,
    )


def _session_for(sector: Sector, wreck: CargoWreck | None) -> _FakeSession:
    return _FakeSession({
        Sector: _FakeQuery(first=sector),
        CargoWreck: _FakeQuery(first=wreck),
    })


def _rig(*, turns: int, capacity: int, wreck_cargo: Dict[str, int], used: int = 0,
          non_exempt: bool = True):
    """Common setup: a stranger player (no owner/team/killer exemption, so
    grace exemption never contaminates the pure turn-cost pins), a ship with
    the requested free hold, and a wreck outside the grace window (so
    suspect-flag logic never fires either) -- isolates these tests to turn
    math alone."""
    player = _player(turns=turns)
    ship = _ship(capacity=capacity, used=used)
    player.current_ship = ship
    sector = _sector()
    created_at = FROZEN_NOW - timedelta(hours=2) if non_exempt else FROZEN_NOW
    wreck = _wreck(sector_uuid=sector.id, cargo=wreck_cargo, created_at=created_at)
    db = _session_for(sector, wreck)
    return db, player, ship, sector, wreck


# ---------------------------------------------------------------------------
# Turn math: ceil(units_transferred / 100), pinned at the WO's boundaries
# ---------------------------------------------------------------------------


class TestTurnCostBoundaryMath:
    @pytest.mark.parametrize("units,expected_turns", [(100, 1), (101, 2), (250, 3), (1000, 10)])
    def test_ceil_boundary(self, units, expected_turns):
        db, player, ship, sector, wreck = _rig(
            turns=100, capacity=5000, wreck_cargo={"ore": units + 500},
        )
        result = salvage_service.salvage_wreck(db, player, str(wreck.id), quantity=units)

        assert sum(result["salvaged"].values()) == units
        assert result["turns_spent"] == expected_turns
        assert player.turns == 100 - expected_turns
        assert db.commit_calls == 1


class TestTurnCostPropertyInvariant:
    @pytest.mark.parametrize("n", [1, 50, 99, 100, 101, 150, 999, 1000])
    def test_cost_always_equals_ceil_n_over_100(self, n):
        db, player, ship, sector, wreck = _rig(
            turns=100, capacity=5000, wreck_cargo={"ore": n + 500},
        )
        result = salvage_service.salvage_wreck(db, player, str(wreck.id), quantity=n)

        assert sum(result["salvaged"].values()) == n
        # Equality is the exact contract here; it trivially satisfies the
        # WO's >= ceil(n/100) invariant.
        assert result["turns_spent"] == math.ceil(n / 100)


# ---------------------------------------------------------------------------
# Cap composition: cargo hold, turns, and an explicit quantity all compose
# ---------------------------------------------------------------------------


class TestCapComposition:
    def test_turn_short_caps_transfer_and_retains_wreck_remainder(self):
        """3 turns available -> 300-unit cap, well under the 1000-unit wreck
        and the 1000-unit free hold -- turns are the binding constraint."""
        db, player, ship, sector, wreck = _rig(
            turns=3, capacity=1000, wreck_cargo={"ore": 1000},
        )
        result = salvage_service.salvage_wreck(db, player, str(wreck.id))

        assert sum(result["salvaged"].values()) == 300
        assert result["turns_spent"] == 3
        assert player.turns == 0
        assert result["wreck_cleared"] is False
        assert wreck.cargo == {"ore": 700}  # remainder RETAINED, not deleted
        assert db.deleted == []

    def test_cargo_space_tighter_than_turns_caps_at_free_hold(self):
        """Free hold 50 + 10 turns available (1000-unit cap) -> cargo space
        is the binding constraint: 50 units, 1 turn (not 10)."""
        db, player, ship, sector, wreck = _rig(
            turns=10, capacity=50, wreck_cargo={"ore": 500},
        )
        result = salvage_service.salvage_wreck(db, player, str(wreck.id))

        assert sum(result["salvaged"].values()) == 50
        assert result["turns_spent"] == 1
        assert player.turns == 9

    def test_explicit_quantity_tighter_than_both_caps(self):
        db, player, ship, sector, wreck = _rig(
            turns=10, capacity=1000, wreck_cargo={"ore": 1000},
        )
        result = salvage_service.salvage_wreck(db, player, str(wreck.id), quantity=75)

        assert sum(result["salvaged"].values()) == 75
        assert result["turns_spent"] == 1  # ceil(75/100)


# ---------------------------------------------------------------------------
# Rejections: 0 turns, non-positive quantity -- 4xx, zero mutation
# ---------------------------------------------------------------------------


class TestRejectionsLeaveEverythingUnchanged:
    def test_zero_turns_rejects_with_no_mutation(self):
        db, player, ship, sector, wreck = _rig(
            turns=0, capacity=1000, wreck_cargo={"ore": 500},
        )
        original_wreck_cargo = dict(wreck.cargo)

        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            salvage_service.salvage_wreck(db, player, str(wreck.id))

        assert exc_info.value.status_code == 400
        assert player.turns == 0
        assert wreck.cargo == original_wreck_cargo
        assert ship.cargo["used"] == 0
        assert db.commit_calls == 0

    @pytest.mark.parametrize("bad_quantity", [0, -1, -100])
    def test_non_positive_quantity_rejects_with_no_mutation(self, bad_quantity):
        db, player, ship, sector, wreck = _rig(
            turns=10, capacity=1000, wreck_cargo={"ore": 500},
        )
        original_turns = player.turns
        original_wreck_cargo = dict(wreck.cargo)

        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            salvage_service.salvage_wreck(db, player, str(wreck.id), quantity=bad_quantity)

        assert exc_info.value.status_code == 400
        assert player.turns == original_turns
        assert wreck.cargo == original_wreck_cargo
        assert db.commit_calls == 0


# ---------------------------------------------------------------------------
# Atomicity: turns + cargo are one all-or-nothing unit
# ---------------------------------------------------------------------------


class TestAtomicity:
    def test_commit_failure_propagates_rather_than_being_swallowed(self):
        """salvage_wreck stages the turn spend (in-memory spend_turns) and
        the cargo/wreck mutations, then commits ONCE. A real Postgres
        session that fails to commit rolls all of it back together; this
        pins that the failure actually reaches the caller instead of being
        silently caught somewhere -- the guarantee this lane's atomicity
        promise depends on."""
        db, player, ship, sector, wreck = _rig(
            turns=10, capacity=1000, wreck_cargo={"ore": 500},
        )
        db.commit_raises = RuntimeError("simulated commit failure")

        with pytest.raises(RuntimeError):
            salvage_service.salvage_wreck(db, player, str(wreck.id), quantity=50)

    def test_exactly_one_commit_per_execution_path(self):
        """Structural pin: salvage_wreck has exactly two db.commit() call
        sites in its source -- the empty-manifest free-clear early return,
        and the main path's single final commit. Never two commits on the
        SAME execution path (which would break the all-or-nothing
        guarantee the test above relies on)."""
        import inspect
        source = inspect.getsource(salvage_service.salvage_wreck)
        assert source.count("db.commit()") == 2


# ---------------------------------------------------------------------------
# Regression: the pre-existing Suspect-flag behavior (:121-132 lineage) is
# untouched by the turn-cost addition
# ---------------------------------------------------------------------------


class TestSuspectFlagRegression:
    def test_early_non_exempt_salvage_still_flags_suspect(self):
        player = _player(turns=10)
        ship = _ship(capacity=1000)
        player.current_ship = ship
        sector = _sector()
        # Inside the 1h grace window, no owner/team/killer relation.
        wreck = _wreck(sector_uuid=sector.id, cargo={"ore": 100}, created_at=FROZEN_NOW)
        db = _session_for(sector, wreck)

        result = salvage_service.salvage_wreck(db, player, str(wreck.id))

        assert result["suspect_flagged"] is True
        assert player.is_suspect is True
        assert player.suspect_declared_at == FROZEN_NOW
        # And the turn cost still applies on top of the flag.
        assert result["turns_spent"] == 1

    def test_owner_salvaging_own_wreck_pays_turns_but_never_flags(self):
        """Canon edge case (ships.md): the owner gets no time-cost
        preferential treatment, only the Suspect exemption."""
        player = _player(turns=10)
        ship = _ship(capacity=1000)
        player.current_ship = ship
        sector = _sector()
        wreck = _wreck(
            sector_uuid=sector.id, cargo={"ore": 150}, created_at=FROZEN_NOW,
            original_owner_id=player.id,
        )
        db = _session_for(sector, wreck)

        result = salvage_service.salvage_wreck(db, player, str(wreck.id))

        assert result["suspect_flagged"] is False
        assert player.is_suspect is False
        assert result["turns_spent"] == 2  # ceil(150/100) -- no exemption from cost
        assert player.turns == 8

    def test_grace_expired_salvage_never_flags(self):
        player = _player(turns=10)
        ship = _ship(capacity=1000)
        player.current_ship = ship
        sector = _sector()
        wreck = _wreck(
            sector_uuid=sector.id, cargo={"ore": 100},
            created_at=FROZEN_NOW - timedelta(hours=2),
        )
        db = _session_for(sector, wreck)

        result = salvage_service.salvage_wreck(db, player, str(wreck.id))

        assert result["suspect_flagged"] is False
        assert player.is_suspect is False


# ---------------------------------------------------------------------------
# NO-CANON: empty-manifest free-clear
# ---------------------------------------------------------------------------


class TestEmptyManifestFreeClear:
    def test_all_zero_cargo_wreck_is_cleared_at_zero_turn_cost(self):
        player = _player(turns=10)
        ship = _ship(capacity=1000)
        player.current_ship = ship
        sector = _sector()
        wreck = _wreck(sector_uuid=sector.id, cargo={"ore": 0, "fuel": 0}, created_at=FROZEN_NOW)
        db = _session_for(sector, wreck)

        result = salvage_service.salvage_wreck(db, player, str(wreck.id))

        assert result == {
            "salvaged": {}, "suspect_flagged": False,
            "wreck_cleared": True, "turns_spent": 0,
        }
        assert player.turns == 10  # untouched -- zero-cost clear
        assert wreck in db.deleted
        assert db.commit_calls == 1


# ---------------------------------------------------------------------------
# Sanity: a wreck that no longer exists (already fully salvaged / deleted by
# a concurrent caller) still 404s cleanly with the new turn-cost code in place
# ---------------------------------------------------------------------------


class TestPreExistingBehaviorStillIntact:
    def test_missing_wreck_404s_before_any_turn_or_cargo_work(self):
        player = _player(turns=10)
        ship = _ship(capacity=1000)
        player.current_ship = ship
        sector = _sector()
        db = _session_for(sector, wreck=None)

        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            salvage_service.salvage_wreck(db, player, str(uuid.uuid4()))

        assert exc_info.value.status_code == 404
        assert player.turns == 10
        assert db.commit_calls == 0
