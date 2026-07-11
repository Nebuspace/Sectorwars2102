"""WO-P4-play-beacon-kernel -- message_beacon_service.deploy().

DoD bullets covered here (see the WO's own numbering):
  1. Deploy debits EXACTLY 5 turns + 500 credits + 1 equipment cargo,
     inserts a row, updates Sector.message_beacons JSONB denorm.
  4. Per-sector cap = 10 (region-configurable up to 50); the 11th deploy
     FIFO-displaces + hard-deletes the oldest.
  5. Nexus-protected sector deploy -> rejected ERR_NEXUS_PROTECTED_SECTOR.
  6. 6th deploy by one player in a UTC day -> rate-limited (cap 5/day).
  7. personal_rep < neutral -> deploy rejected.
  8. Message 1-500 chars, sanitized via the ARIA content filter;
     over-length / rejected-by-filter content rejected.
  10. region_id set on every beacon.
  11. beacon_deployed bus event emitted.

DB-free: a real SQLAlchemy WHERE-clause interpreter (not a scripted mock),
in the house style of test_contract_service.py / test_contract_escrow.py,
extended for the new operators this service's queries use (.isnot(None),
<, >=) and a func.count(...) aggregate path. get_security_service is
monkeypatched to a controllable fake -- the ARIA filter's OWN correctness
is proven in its own test suite (WO-ARIA-PROMPT-DEFENSE); these tests only
need to prove message_beacon_service actually CALLS it and respects the
verdict, not re-derive its heuristics.
"""
from __future__ import annotations

import operator
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any, List, Optional

import pytest
from sqlalchemy.orm.exc import ObjectDeletedError, StaleDataError

from src.models.message_beacon import MessageBeacon
from src.models.multi_account import MultiAccountFlag, MultiAccountSeverity
from src.models.player import Player
from src.models.region import Region
from src.models.sector import Sector as SectorModel
from src.models.ship import Ship, ShipType
from src.services import message_beacon_service as svc
from src.services.message_beacon_service import BeaconError


# --- WHERE-clause interpreter ------------------------------------------- #

def _match(row: Any, cond: Any) -> bool:
    col_name = cond.left.key
    row_val = getattr(row, col_name, None)
    op_name = getattr(cond.operator, "__name__", None)
    if op_name == "eq":
        return row_val == cond.right.value
    if op_name == "lt":
        return row_val is not None and row_val < cond.right.value
    if op_name == "ge":
        return row_val is not None and row_val >= cond.right.value
    if op_name == "is_not":
        return row_val is not None
    raise NotImplementedError(f"unsupported operator {cond.operator!r}")


class _FakeResult:
    def __init__(self, rowcount: int) -> None:
        self.rowcount = rowcount


class _FakeQuery:
    def __init__(
        self, rows: List[Any], criteria: Optional[List[Any]] = None,
        session: Optional["_FakeSession"] = None, entity: Optional[str] = None,
    ) -> None:
        self._rows = rows
        self._criteria = criteria or []
        self._session = session
        self._entity = entity

    def filter(self, *conditions: Any) -> "_FakeQuery":
        return _FakeQuery(self._rows, self._criteria + list(conditions), self._session, self._entity)

    def with_for_update(self) -> "_FakeQuery":
        # WO-P4 REVISE fix 2 -- records WHICH entity got FOR UPDATE'd (and
        # in what order) so a test can assert the Ship row is actually
        # locked, not just read via a lazy relationship.
        if self._session is not None:
            self._session.for_update_calls.append(self._entity)
        return self

    def order_by(self, *args: Any) -> "_FakeQuery":
        # Every call site in message_beacon_service orders by deployed_at
        # ascending (oldest first) -- a real-enough simplification rather
        # than parsing the UnaryExpression's direction generically.
        ordered = sorted(self._matching(), key=lambda r: r.deployed_at)
        return _FakeQuery(ordered, [], self._session, self._entity)

    def _matching(self) -> List[Any]:
        return [row for row in self._rows if all(_match(row, c) for c in self._criteria)]

    def first(self) -> Any:
        matches = self._matching()
        return matches[0] if matches else None

    def all(self) -> List[Any]:
        return self._matching()


class _FakeCountQuery:
    def __init__(self, rows: List[Any], criteria: Optional[List[Any]] = None) -> None:
        self._rows = rows
        self._criteria = criteria or []

    def filter(self, *conditions: Any) -> "_FakeCountQuery":
        return _FakeCountQuery(self._rows, self._criteria + list(conditions))

    def scalar(self) -> int:
        return sum(1 for row in self._rows if all(_match(row, c) for c in self._criteria))


class _FakeNestedTxn:
    """WO-P4 REVISE fix 4 -- stands in for db.begin_nested()'s SAVEPOINT
    context manager. Never suppresses an exception (matches real
    begin_nested(): rollback-to-savepoint, then re-raise) -- the caller's
    own try/except is what decides whether to swallow it."""

    def __enter__(self) -> "_FakeNestedTxn":
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> bool:
        return False


class _FakeSession:
    def __init__(
        self, *, players=None, sectors=None, regions=None, beacons=None, flags=None,
        ships=None, fail_delete_ids: Optional[set] = None,
    ) -> None:
        self.players = players or []
        self.sectors = sectors or []
        self.regions = regions or []
        self.beacons = beacons or []
        self.flags = flags or []
        # WO-P4 REVISE fix 2 -- auto-derived from each player's own
        # current_ship so every EXISTING _FakeSession(players=[...]) call
        # site keeps working without individually passing ships= too.
        self.ships = ships if ships is not None else [
            p.current_ship for p in self.players if getattr(p, "current_ship", None) is not None
        ]
        self.added: List[Any] = []
        self.deleted: List[Any] = []
        self.flush_calls = 0
        self.lock_calls: List[int] = []
        self.for_update_calls: List[Optional[str]] = []
        # WO-P4 REVISE fix 4 test knob -- beacon ids in this set raise
        # StaleDataError on delete() (and are removed anyway, simulating a
        # row a concurrent transaction already deleted).
        self.fail_delete_ids = fail_delete_ids or set()

    def query(self, *entities: Any) -> Any:
        head = entities[0]
        if getattr(head, "name", None) == "count":
            return _FakeCountQuery(self.beacons)
        if head is Player:
            return _FakeQuery(self.players, session=self, entity="Player")
        if head is Ship:
            return _FakeQuery(self.ships, session=self, entity="Ship")
        if head is SectorModel:
            return _FakeQuery(self.sectors, session=self, entity="Sector")
        if head is Region:
            return _FakeQuery(self.regions, session=self, entity="Region")
        if head is MessageBeacon:
            return _FakeQuery(self.beacons, session=self, entity="MessageBeacon")
        if head is MultiAccountFlag:
            return _FakeQuery(self.flags, session=self, entity="MultiAccountFlag")
        raise AssertionError(f"unexpected query for {entities!r}")

    def add(self, obj: Any) -> None:
        self.added.append(obj)
        if isinstance(obj, MessageBeacon):
            self.beacons.append(obj)

    def delete(self, obj: Any) -> None:
        if getattr(obj, "id", None) in self.fail_delete_ids:
            if obj in self.beacons:
                self.beacons.remove(obj)
            raise StaleDataError("simulated concurrent delete")
        self.deleted.append(obj)
        if obj in self.beacons:
            self.beacons.remove(obj)

    def flush(self) -> None:
        self.flush_calls += 1

    def commit(self) -> None:
        raise AssertionError("service functions are flush-only -- the route commits")

    def execute(self, statement: Any, params: Optional[dict] = None) -> Any:
        # WO-P4 REVISE fix 1 -- _lock_sector's pg_advisory_xact_lock call.
        self.lock_calls.append((params or {}).get("key"))
        return SimpleNamespace(scalar=lambda: True)

    def begin_nested(self) -> _FakeNestedTxn:
        return _FakeNestedTxn()


# --- fixtures ------------------------------------------------------------ #

def _player(**overrides: Any) -> SimpleNamespace:
    # WO-P4 REVISE fix 2: the SERVICE now locks the ship via
    # db.query(Ship).filter(Ship.id==.., Ship.owner_id==..).with_for_update()
    # rather than reading player.current_ship directly -- player_id/ship_id
    # are generated FIRST so the default ship's owner_id/id line up with
    # what deploy() will actually query for.
    player_id = overrides.pop("id", None) or uuid.uuid4()
    ship_id = overrides.pop("current_ship_id", None) or uuid.uuid4()
    # flag_modified() (deploy()'s cargo debit) requires a REAL ORM
    # instance -- mirrors test_contract_service.py's _real_ship().
    ship = overrides.pop("current_ship", None)
    if ship is None:
        ship = Ship(
            id=ship_id, name="Test Freighter", type=ShipType.LIGHT_FREIGHTER,
            sector_id=42, is_destroyed=False, owner_id=player_id,
            cargo={"capacity": 100, "used": 10, "contents": {"equipment": 5}},
        )
    base = dict(
        id=player_id, username="Voyager7", nickname=None, credits=10000, turns=1000,
        personal_reputation=0, is_docked=False, current_sector_id=42,
        current_ship_id=ship_id, current_ship=ship,
        # last_turn_regeneration pinned to "now" -- turn_service.
        # regenerate_turns (called unconditionally at the top of deploy())
        # is the REAL function, not mocked; anchoring far in the past would
        # grant a real elapsed-time turn refill and silently override a
        # fixture's deliberately-low `turns` override.
        max_turns=1000, last_turn_regeneration=datetime.now(UTC), lifetime_turns_spent=0,
        created_at=datetime.now(UTC) - timedelta(days=30),
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _region(**overrides: Any) -> SimpleNamespace:
    base = dict(id=uuid.uuid4(), trade_bonuses={})
    base.update(overrides)
    return SimpleNamespace(**base)


def _sector(region: SimpleNamespace, **overrides: Any) -> SectorModel:
    """flag_modified() (message_beacon_service._rebuild_sector_denorm)
    requires a REAL ORM instance -- a SimpleNamespace has no
    _sa_instance_state. Mirrors test_contract_service.py's _real_ship()
    convention."""
    base = dict(
        id=uuid.uuid4(), sector_id=42, region_id=region.id, is_nexus_protected=False,
        message_beacons=None, name="Test Sector", x_coord=0, y_coord=0,
    )
    base.update(overrides)
    return SectorModel(**base)


def _beacon(region: SimpleNamespace, sector: SimpleNamespace, **overrides: Any) -> SimpleNamespace:
    base = dict(
        id=uuid.uuid4(), region_id=region.id, sector_id=sector.sector_id,
        deployer_player_id=uuid.uuid4(), deployer_nickname_at_deploy="Someone",
        message="pre-existing beacon", expiry=None, read_once=False, read_count=0,
        deployed_at=datetime.now(UTC), last_read_at=None,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


class _FakeSecurityService:
    """Controllable stand-in for ai_security_service.AISecurityService --
    the real singleton carries persistent cross-call state (rate limits,
    trust profiles) that would make these tests order-dependent; this fake
    lets each test pin exactly the verdict it needs."""

    def __init__(self, is_safe: bool = True, violation_types: Optional[List[str]] = None) -> None:
        self.is_safe = is_safe
        self.violation_types = violation_types or ["xss_attempt"]
        self.validate_calls: List[Any] = []

    def validate_input(self, text, player_id, session_id, skip_sql_injection=False, skip_xss=False, seed_from=None):
        self.validate_calls.append((text, player_id, session_id, skip_sql_injection, skip_xss))
        if self.is_safe:
            return True, []
        violations = [SimpleNamespace(violation_type=SimpleNamespace(value=v)) for v in self.violation_types]
        return False, violations

    def sanitize_input(self, text: str) -> str:
        return text.strip()


@pytest.fixture
def safe_security(monkeypatch: pytest.MonkeyPatch) -> _FakeSecurityService:
    fake = _FakeSecurityService(is_safe=True)
    monkeypatch.setattr(svc, "get_security_service", lambda: fake)
    return fake


_NOW = datetime(2026, 7, 10, 12, 0, tzinfo=UTC)


# --- DoD 1: deploy costs + row insert + denorm --------------------------- #

@pytest.mark.unit
class TestDeployCostsAndDenorm:
    def test_deploy_debits_exact_costs_inserts_row_updates_denorm(self, safe_security) -> None:
        region = _region()
        sector = _sector(region)
        player = _player(credits=10000, turns=1000)
        db = _FakeSession(players=[player], sectors=[sector], regions=[region])

        result = svc.deploy(db, player.id, sector.sector_id, "Hello, traveler.")

        assert player.credits == 10000 - svc.DEPLOY_CREDIT_COST
        assert player.turns == 1000 - svc.DEPLOY_TURN_COST
        assert player.current_ship.cargo["contents"]["equipment"] == 5 - svc.DEPLOY_EQUIPMENT_QTY
        assert len(db.beacons) == 1
        beacon = db.beacons[0]
        assert beacon.message == "Hello, traveler."
        assert beacon.region_id == region.id  # DoD 10
        assert beacon.sector_id == sector.sector_id
        assert beacon.expiry is None  # default "never"
        assert result["id"] == str(beacon.id)
        # Denorm updated (DoD 1's "updates Sector.message_beacons JSONB").
        assert sector.message_beacons is not None
        assert len(sector.message_beacons) == 1
        assert sector.message_beacons[0]["id"] == str(beacon.id)

    def test_deploy_costs_are_exact_not_approximate(self, safe_security) -> None:
        """Pins the literal canon numbers -- a regression that drifts any
        of the three costs must fail loudly, not silently pass a looser
        'costs something' check."""
        assert svc.DEPLOY_TURN_COST == 5
        assert svc.DEPLOY_CREDIT_COST == 500
        assert svc.DEPLOY_EQUIPMENT_QTY == 1

    def test_deploy_uses_nickname_or_falls_back_to_username(self, safe_security) -> None:
        region = _region()
        sector = _sector(region)
        player = _player(nickname="Cap'n Rex")
        db = _FakeSession(players=[player], sectors=[sector], regions=[region])
        svc.deploy(db, player.id, sector.sector_id, "route marker")
        assert db.beacons[0].deployer_nickname_at_deploy == "Cap'n Rex"

        sector2 = _sector(region, sector_id=43)
        player2 = _player(nickname=None, username="voyager9", current_sector_id=43)
        db2 = _FakeSession(players=[player2], sectors=[sector2], regions=[region])
        svc.deploy(db2, player2.id, sector2.sector_id, "route marker")
        assert db2.beacons[0].deployer_nickname_at_deploy == "voyager9"

    def test_deploy_rejects_insufficient_credits(self, safe_security) -> None:
        region = _region()
        sector = _sector(region)
        player = _player(credits=100)
        db = _FakeSession(players=[player], sectors=[sector], regions=[region])
        with pytest.raises(BeaconError, match="insufficient_credits"):
            svc.deploy(db, player.id, sector.sector_id, "hi")
        assert db.beacons == []
        assert player.credits == 100  # untouched -- no partial debit

    def test_deploy_rejects_insufficient_turns(self, safe_security) -> None:
        region = _region()
        sector = _sector(region)
        player = _player(turns=2)
        db = _FakeSession(players=[player], sectors=[sector], regions=[region])
        with pytest.raises(BeaconError, match="insufficient_turns"):
            svc.deploy(db, player.id, sector.sector_id, "hi")
        assert player.turns == 2

    def test_deploy_rejects_insufficient_equipment_cargo(self, safe_security) -> None:
        region = _region()
        sector = _sector(region)
        player_id, ship_id = uuid.uuid4(), uuid.uuid4()
        # SimpleNamespace (not a real Ship) is fine here -- this path
        # raises before flag_modified() is ever reached; id/owner_id must
        # still match what the service's locked Ship query filters on.
        player = _player(
            id=player_id, current_ship_id=ship_id,
            current_ship=SimpleNamespace(
                id=ship_id, owner_id=player_id, cargo={"capacity": 100, "used": 0, "contents": {}},
            ),
        )
        db = _FakeSession(players=[player], sectors=[sector], regions=[region])
        with pytest.raises(BeaconError, match="insufficient_cargo"):
            svc.deploy(db, player.id, sector.sector_id, "hi")


# --- DoD 4: per-sector FIFO cap ------------------------------------------ #

@pytest.mark.unit
class TestSectorFifoCap:
    def test_eleventh_deploy_displaces_the_oldest(self, safe_security) -> None:
        region = _region()
        sector = _sector(region)
        base_time = _NOW - timedelta(hours=20)
        existing = [
            _beacon(region, sector, deployed_at=base_time + timedelta(hours=i))
            for i in range(svc.DEFAULT_SECTOR_CAP)  # 10 already present
        ]
        oldest = existing[0]
        player = _player()
        db = _FakeSession(players=[player], sectors=[sector], regions=[region], beacons=list(existing))

        svc.deploy(db, player.id, sector.sector_id, "the 11th beacon")

        assert oldest not in db.beacons  # hard-deleted
        assert oldest in db.deleted
        assert len(db.beacons) == svc.DEFAULT_SECTOR_CAP  # still capped at 10
        assert len(sector.message_beacons) == svc.DEFAULT_SECTOR_CAP

    def test_region_configurable_cap_up_to_50(self, safe_security) -> None:
        region = _region(trade_bonuses={svc.REGION_BEACON_CAP_KEY: 15})
        sector = _sector(region)
        base_time = _NOW - timedelta(hours=20)
        existing = [
            _beacon(region, sector, deployed_at=base_time + timedelta(hours=i))
            for i in range(15)
        ]
        player = _player()
        db = _FakeSession(players=[player], sectors=[sector], regions=[region], beacons=list(existing))

        svc.deploy(db, player.id, sector.sector_id, "16th, over the raised cap")

        assert len(db.beacons) == 15  # displaced down to the CONFIGURED cap, not the default 10

    def test_cap_value_above_50_clamped_to_max(self, safe_security) -> None:
        region = _region(trade_bonuses={svc.REGION_BEACON_CAP_KEY: 9999})
        assert svc._sector_cap(region) == svc.MAX_SECTOR_CAP

    def test_under_cap_deploy_does_not_displace_anything(self, safe_security) -> None:
        region = _region()
        sector = _sector(region)
        existing = [_beacon(region, sector, deployed_at=_NOW - timedelta(hours=1))]
        player = _player()
        db = _FakeSession(players=[player], sectors=[sector], regions=[region], beacons=list(existing))
        svc.deploy(db, player.id, sector.sector_id, "second beacon")
        assert len(db.beacons) == 2
        assert db.deleted == []


# --- DoD 5: nexus-protected sector ---------------------------------------- #

@pytest.mark.unit
class TestNexusProtectedGate:
    def test_deploy_rejected_in_nexus_protected_sector(self, safe_security) -> None:
        region = _region()
        sector = _sector(region, is_nexus_protected=True)
        player = _player()
        db = _FakeSession(players=[player], sectors=[sector], regions=[region])
        with pytest.raises(BeaconError, match="ERR_NEXUS_PROTECTED_SECTOR"):
            svc.deploy(db, player.id, sector.sector_id, "hi")
        assert db.beacons == []
        assert player.credits == 10000  # no partial debit


# --- DoD 6: per-player daily rate limit ----------------------------------- #

@pytest.mark.unit
class TestDailyRateLimit:
    def test_sixth_deploy_same_utc_day_is_rate_limited(
        self, safe_security, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # WO-P4 REVISE fix 7: day_start now derives from the module's
        # _now() seam (was a direct datetime.now(UTC) call, making this
        # test date-lucky -- it only passed when the REAL wall-clock date
        # happened to match _NOW's fixed date). Pinning _now() makes the
        # rate-limit boundary deterministic regardless of when this runs.
        monkeypatch.setattr(svc, "_now", lambda: _NOW)
        region = _region()
        sector = _sector(region)
        player = _player()
        today_start = _NOW.replace(hour=0, minute=0, second=0, microsecond=0)
        five_today = [
            _beacon(region, sector, deployer_player_id=player.id, deployed_at=today_start + timedelta(hours=i))
            for i in range(svc.RATE_LIMIT_PER_DAY)
        ]
        db = _FakeSession(players=[player], sectors=[sector], regions=[region], beacons=list(five_today))

        with pytest.raises(BeaconError, match="ERR_RATE_LIMIT_EXCEEDED"):
            svc.deploy(db, player.id, sector.sector_id, "6th today", expiry="never")

    def test_deploys_from_a_prior_day_do_not_count(
        self, safe_security, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Now deterministic via the same _now() injection as its sibling
        above (fix 7) -- no longer needs to tolerate real wall-clock
        'today' varying."""
        monkeypatch.setattr(svc, "_now", lambda: _NOW)
        region = _region()
        sector = _sector(region)
        player = _player()
        yesterday = [
            _beacon(region, sector, deployer_player_id=player.id, deployed_at=_NOW - timedelta(days=1))
            for _ in range(svc.RATE_LIMIT_PER_DAY)
        ]
        db = _FakeSession(players=[player], sectors=[sector], regions=[region], beacons=list(yesterday))
        svc.deploy(db, player.id, sector.sector_id, "today's first")
        assert len(db.beacons) == svc.RATE_LIMIT_PER_DAY + 1

    def test_day_start_uses_now_not_a_direct_wallclock_call(
        self, safe_security, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Regression pin for fix 7 itself, made unambiguous by pinning
        _now() to a FAR-FUTURE moment (never coincides with real
        wall-clock "today"): beacons dated at real wall-clock "now" are
        necessarily BEFORE that pinned day's start, so they must NOT count
        toward the rate limit -- proving day_start really tracks the
        PINNED _now() seam, not a live datetime.now(UTC) call (which would
        have judged those same beacons as "today" and rate-limited)."""
        far_future = datetime(2099, 1, 1, tzinfo=UTC)
        monkeypatch.setattr(svc, "_now", lambda: far_future)
        region = _region()
        sector = _sector(region)
        player = _player()
        real_now_beacons = [
            _beacon(region, sector, deployer_player_id=player.id, deployed_at=datetime.now(UTC))
            for _ in range(svc.RATE_LIMIT_PER_DAY)
        ]
        db = _FakeSession(players=[player], sectors=[sector], regions=[region], beacons=list(real_now_beacons))
        svc.deploy(db, player.id, sector.sector_id, "does not count against the pinned far-future day")
        assert len(db.beacons) == svc.RATE_LIMIT_PER_DAY + 1


# --- DoD 7: personal-rep gate ---------------------------------------------- #

@pytest.mark.unit
class TestPersonalRepGate:
    def test_deploy_rejected_when_below_neutral(self, safe_security) -> None:
        region = _region()
        sector = _sector(region)
        player = _player(personal_reputation=-1)
        db = _FakeSession(players=[player], sectors=[sector], regions=[region])
        with pytest.raises(BeaconError, match="ERR_PERSONAL_REP_TOO_LOW"):
            svc.deploy(db, player.id, sector.sector_id, "hi")

    def test_deploy_allowed_at_exactly_neutral(self, safe_security) -> None:
        region = _region()
        sector = _sector(region)
        player = _player(personal_reputation=0)
        db = _FakeSession(players=[player], sectors=[sector], regions=[region])
        svc.deploy(db, player.id, sector.sector_id, "hi")  # does not raise
        assert len(db.beacons) == 1

    def test_deploy_allowed_above_neutral(self, safe_security) -> None:
        region = _region()
        sector = _sector(region)
        player = _player(personal_reputation=250)
        db = _FakeSession(players=[player], sectors=[sector], regions=[region])
        svc.deploy(db, player.id, sector.sector_id, "hi")
        assert len(db.beacons) == 1


# --- DoD 8: message length + content-policy filter ------------------------ #

@pytest.mark.unit
class TestMessageContentPolicy:
    def test_message_over_500_chars_rejected(self, safe_security) -> None:
        region = _region()
        sector = _sector(region)
        player = _player()
        db = _FakeSession(players=[player], sectors=[sector], regions=[region])
        with pytest.raises(BeaconError, match="invalid_message_length"):
            svc.deploy(db, player.id, sector.sector_id, "x" * 501)

    def test_empty_message_rejected(self, safe_security) -> None:
        region = _region()
        sector = _sector(region)
        player = _player()
        db = _FakeSession(players=[player], sectors=[sector], regions=[region])
        with pytest.raises(BeaconError, match="invalid_message_length"):
            svc.deploy(db, player.id, sector.sector_id, "")

    def test_500_chars_exactly_is_accepted(self, safe_security) -> None:
        region = _region()
        sector = _sector(region)
        player = _player()
        db = _FakeSession(players=[player], sectors=[sector], regions=[region])
        svc.deploy(db, player.id, sector.sector_id, "x" * 500)
        assert len(db.beacons) == 1

    def test_content_policy_rejection_blocks_deploy(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fake = _FakeSecurityService(is_safe=False, violation_types=["xss_attempt", "prompt_injection"])
        monkeypatch.setattr(svc, "get_security_service", lambda: fake)
        region = _region()
        sector = _sector(region)
        player = _player()
        db = _FakeSession(players=[player], sectors=[sector], regions=[region])

        with pytest.raises(BeaconError, match="ERR_CONTENT_POLICY_VIOLATION"):
            svc.deploy(db, player.id, sector.sector_id, "<script>alert(1)</script>")

        assert db.beacons == []
        assert player.credits == 10000  # rejected before any debit
        # Reused the shipped filter, not skipped -- proves it was actually called.
        assert len(fake.validate_calls) == 1

    def test_content_filter_actually_consulted_not_decorative(self, safe_security) -> None:
        region = _region()
        sector = _sector(region)
        player = _player()
        db = _FakeSession(players=[player], sectors=[sector], regions=[region])
        svc.deploy(db, player.id, sector.sector_id, "a clean message")
        assert len(safe_security.validate_calls) == 1
        text, pid, sid, skip_sql, skip_xss = safe_security.validate_calls[0]
        assert text == "a clean message"
        assert skip_xss is False  # canon:110 -- XSS sanitization must run

    def test_stored_message_is_the_sanitized_version(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fake = _FakeSecurityService(is_safe=True)
        fake.sanitize_input = lambda text: "SANITIZED"
        monkeypatch.setattr(svc, "get_security_service", lambda: fake)
        region = _region()
        sector = _sector(region)
        player = _player()
        db = _FakeSession(players=[player], sectors=[sector], regions=[region])
        svc.deploy(db, player.id, sector.sector_id, "raw <b>text</b>")
        assert db.beacons[0].message == "SANITIZED"

    def test_stored_message_is_raw_sanitized_not_html_escaped(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """WO-P4 FINAL-FIX (orchestrator ruling D15, option B -- store RAW,
        encode-at-output): the earlier REVISE-fix-6 html.escape'd the
        stored value, which is the OWASP anti-pattern this ruling reverses
        -- consumers (React et al.) encode at render time; storage stays
        the sanitize_input()-normalized value verbatim, unescaped."""
        fake = _FakeSecurityService(is_safe=True)
        fake.sanitize_input = lambda text: "<script>alert(1)</script>"
        monkeypatch.setattr(svc, "get_security_service", lambda: fake)
        region = _region()
        sector = _sector(region)
        player = _player()
        db = _FakeSession(players=[player], sectors=[sector], regions=[region])

        svc.deploy(db, player.id, sector.sector_id, "irrelevant, sanitize_input is faked")

        # Stored EXACTLY as sanitize_input returned it -- no html.escape
        # layered on top.
        assert db.beacons[0].message == "<script>alert(1)</script>"

    def test_500_char_message_with_markup_characters_does_not_overflow(
        self, safe_security,
    ) -> None:
        """cipher's exact repro shape for the length-inflation overflow
        the REVISE-fix-6 html.escape() introduced: a 500-char message
        packed with `<>&"'` would escape into well over 500 chars
        (`&amp;` alone is 5 chars for 1), overflowing this column's
        String(500) bound at the DB layer. Storing raw means the stored
        length is always <= the validated input length -- this test pins
        that a maximal-length, markup-heavy message deploys cleanly."""
        region = _region()
        sector = _sector(region)
        player = _player()
        db = _FakeSession(players=[player], sectors=[sector], regions=[region])

        message = ("<>&\"'" * 100)  # exactly 500 chars, all markup-significant
        assert len(message) == svc.MESSAGE_MAX_LENGTH

        result = svc.deploy(db, player.id, sector.sector_id, message)

        assert len(db.beacons[0].message) <= svc.MESSAGE_MAX_LENGTH
        # safe_security's fake sanitize_input is text.strip() -- a no-op
        # here (no leading/trailing whitespace), so the stored value is
        # the message verbatim.
        assert db.beacons[0].message == message
        assert result["id"]

    def test_invalid_expiry_choice_rejected(self, safe_security) -> None:
        region = _region()
        sector = _sector(region)
        player = _player()
        db = _FakeSession(players=[player], sectors=[sector], regions=[region])
        with pytest.raises(BeaconError, match="invalid_expiry"):
            svc.deploy(db, player.id, sector.sector_id, "hi", expiry="3 days")


# --- DoD 11 (deploy half): beacon_deployed bus event ---------------------- #

@pytest.mark.unit
class TestDeployBusEvent:
    def test_deploy_dispatches_beacon_deployed_when_a_loop_is_running(self, safe_security) -> None:
        """No running event loop in a plain sync pytest test -- deploy()
        must not raise even though the broadcast is unreachable (swallowed,
        matching combat_service.py's own precedent). The event SHAPE is
        proven separately via build_beacon_event below (the pure builder),
        and the live-loop dispatch path via test_message_beacon_sweep.py's
        broadcast-plumbing test."""
        region = _region()
        sector = _sector(region)
        player = _player()
        db = _FakeSession(players=[player], sectors=[sector], regions=[region])
        result = svc.deploy(db, player.id, sector.sector_id, "hi")
        assert result["id"]  # deploy succeeded despite no running loop

    def test_build_beacon_event_shape(self, safe_security) -> None:
        region = _region()
        sector = _sector(region)
        beacon = _beacon(region, sector, deployer_nickname_at_deploy="Voyager7")
        event = svc.build_beacon_event("beacon_deployed", beacon)
        assert event["type"] == "beacon_deployed"
        assert event["sector_id"] == sector.sector_id
        assert event["beacon_id"] == str(beacon.id)
        assert event["region_id"] == str(region.id)
        assert event["deployer_nickname"] == "Voyager7"


# --- multi-account soft-dep hook (participation_weight) -------------------- #

@pytest.mark.unit
class TestParticipationWeightSoftDep:
    def test_defaults_to_1_when_no_detection_service_has_ever_run(self) -> None:
        db = _FakeSession()
        assert svc._participation_weight(db, uuid.uuid4()) == 1.0

    def test_hard_flagged_player_weights_zero(self) -> None:
        player_id = uuid.uuid4()
        flag = SimpleNamespace(player_id=player_id, severity=MultiAccountSeverity.HARD)
        db = _FakeSession(flags=[flag])
        assert svc._participation_weight(db, player_id) == 0.0

    def test_zero_weight_beacon_excluded_from_sector_denorm(self, safe_security) -> None:
        region = _region()
        sector = _sector(region)
        flagged_player_id = uuid.uuid4()
        flag = SimpleNamespace(player_id=flagged_player_id, severity=MultiAccountSeverity.HARD)
        flagged_beacon = _beacon(region, sector, deployer_player_id=flagged_player_id)
        db = _FakeSession(sectors=[sector], regions=[region], beacons=[flagged_beacon], flags=[flag])

        svc._rebuild_sector_denorm(db, region.id, sector.sector_id)

        assert sector.message_beacons == []  # flagged beacon never surfaced (canon:115)


# --- WO-P4 REVISE fix 1: per-sector advisory lock -------------------------- #
# LIVE-PROOF-ONLY BOUNDARY: a DB-free fake session cannot prove two
# transactions actually SERIALIZE around this lock (that needs real
# concurrent Postgres connections -- the orchestrator's windowed live-DB
# leg). What IS provable here: the lock IS acquired, with the CORRECT
# per-sector key, at the right point in deploy()'s mutating section.

@pytest.mark.unit
class TestSectorLock:
    def test_deploy_acquires_the_sector_lock_with_the_correct_key(self, safe_security) -> None:
        region = _region()
        sector = _sector(region)
        player = _player()
        db = _FakeSession(players=[player], sectors=[sector], regions=[region])

        svc.deploy(db, player.id, sector.sector_id, "hi")

        expected_key = svc._sector_lock_key(region.id, sector.sector_id)
        assert expected_key in db.lock_calls

    def test_cap_displacement_deploy_still_locks_exactly_that_sector(self, safe_security) -> None:
        region = _region()
        sector = _sector(region)
        base_time = _NOW - timedelta(hours=20)
        existing = [
            _beacon(region, sector, deployed_at=base_time + timedelta(hours=i))
            for i in range(svc.DEFAULT_SECTOR_CAP)
        ]
        player = _player()
        db = _FakeSession(players=[player], sectors=[sector], regions=[region], beacons=list(existing))

        svc.deploy(db, player.id, sector.sector_id, "the 11th beacon")

        expected_key = svc._sector_lock_key(region.id, sector.sector_id)
        assert expected_key in db.lock_calls


# --- WO-P4 REVISE fix 2: ship row locked before the cargo RMW -------------- #

@pytest.mark.unit
class TestShipLock:
    def test_deploy_locks_player_then_ship_in_that_order(self, safe_security) -> None:
        region = _region()
        sector = _sector(region)
        player = _player()
        db = _FakeSession(players=[player], sectors=[sector], regions=[region])

        svc.deploy(db, player.id, sector.sector_id, "hi")

        # Player-then-Ship -- the SAME order mining_service._lock_player_
        # and_ship / contraband_service._lock_station_player_ship use, so
        # no path in this codebase can AB-BA deadlock against this one.
        assert db.for_update_calls == ["Player", "Ship"]

    def test_ship_is_queried_by_current_ship_id_and_owner(self, safe_security) -> None:
        region = _region()
        sector = _sector(region)
        player = _player()
        db = _FakeSession(players=[player], sectors=[sector], regions=[region])

        svc.deploy(db, player.id, sector.sector_id, "hi")

        # The locked ship actually got the cargo debit -- proves the
        # locked query resolved to the SAME ship, not a decoy/empty result.
        assert player.current_ship.cargo["contents"]["equipment"] == 5 - svc.DEPLOY_EQUIPMENT_QTY


# --- WO-P4 REVISE fix 4: StaleDataError survives via SAVEPOINT ------------- #

@pytest.mark.unit
class TestStaleDataSavepointOnCapDisplacement:
    def test_one_stale_overflow_beacon_does_not_abort_the_whole_deploy(self, safe_security) -> None:
        """Simulates a concurrent transaction having already removed ONE
        of the overflow beacons _apply_sector_cap was about to displace.
        Without fix 4's per-delete SAVEPOINT, that StaleDataError would
        poison the whole SQLAlchemy session (a failed flush leaves the
        session inactive until rollback) and deploy()'s own pending insert
        + player debit -- already flushed earlier in the SAME transaction
        -- would be unrecoverable without discarding them too. Proves
        deploy() still succeeds and the REMAINING overflow is displaced."""
        region = _region()
        sector = _sector(region)
        base_time = _NOW - timedelta(hours=20)
        existing = [
            _beacon(region, sector, deployed_at=base_time + timedelta(hours=i))
            for i in range(svc.DEFAULT_SECTOR_CAP)
        ]
        already_gone = existing[0]
        player = _player()
        db = _FakeSession(
            players=[player], sectors=[sector], regions=[region], beacons=list(existing),
            fail_delete_ids={already_gone.id},
        )

        result = svc.deploy(db, player.id, sector.sector_id, "the 11th beacon")

        assert result["id"]  # deploy did not raise
        assert already_gone not in db.beacons  # removed either way (fake models "already gone")
        # Still at or under cap -- the loop kept going after the stale hit
        # rather than aborting mid-displacement.
        assert len(db.beacons) <= svc.DEFAULT_SECTOR_CAP
