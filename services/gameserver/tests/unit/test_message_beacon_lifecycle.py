"""WO-P4-play-beacon-kernel -- message_beacon_service.read() / salvage().

DoD bullets covered here:
  2. Read costs 0 turns; if read_once=true, deletes the row on read.
  3. Salvage costs 1 turn, refunds 250 credits, removes the row; equipment
     NOT refunded.
  11. beacon_salvaged bus event emitted (build_beacon_event shape --
      dispatch-plumbing is the same _dispatch_event_frame proven safe-when-
      loopless in test_message_beacon_deploy.py).

DB-free, same WHERE-clause interpreter convention as test_message_beacon_
deploy.py (kept self-contained per this codebase's own test_contract_
service.py / test_contract_escrow.py precedent -- each file owns its fake).
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any, List, Optional

import pytest
from sqlalchemy.orm.exc import ObjectDeletedError, StaleDataError

from src.models.message_beacon import MessageBeacon
from src.models.multi_account import MultiAccountFlag
from src.models.player import Player
from src.models.region import Region
from src.models.sector import Sector as SectorModel
from src.services import message_beacon_service as svc
from src.services.message_beacon_service import BeaconError, BeaconNotFoundError


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


class _FakeQuery:
    def __init__(self, rows: List[Any], criteria: Optional[List[Any]] = None) -> None:
        self._rows = rows
        self._criteria = criteria or []

    def filter(self, *conditions: Any) -> "_FakeQuery":
        return _FakeQuery(self._rows, self._criteria + list(conditions))

    def populate_existing(self) -> "_FakeQuery":
        # WO-MONEY-REREAD-SERVICES: no-op passthrough, matches real
        # SQLAlchemy Query's chainable-and-returns-self shape.
        return self

    def with_for_update(self) -> "_FakeQuery":
        return self

    def order_by(self, *args: Any) -> "_FakeQuery":
        ordered = sorted(self._matching(), key=lambda r: r.deployed_at)
        return _FakeQuery(ordered, [])

    def _matching(self) -> List[Any]:
        return [row for row in self._rows if all(_match(row, c) for c in self._criteria)]

    def first(self) -> Any:
        matches = self._matching()
        return matches[0] if matches else None

    def all(self) -> List[Any]:
        return self._matching()

    def update(self, values: dict, synchronize_session: Any = None) -> int:
        """WO-P4 REVISE fix 5 -- interprets the atomic
        `MessageBeacon.read_count: MessageBeacon.read_count + 1`-shaped SET
        clause (a BinaryExpression whose .right.value is the literal
        operand) against every currently-matching row, mutating it
        directly. _load_beacon's earlier `.first()` already returned this
        SAME row object (not a copy), so the caller's in-hand `beacon`
        reference reflects the change too -- exactly what a real
        db.refresh() achieves after a real synchronize_session=False bulk
        UPDATE."""
        matches = self._matching()
        for row in matches:
            for col, val in values.items():
                col_name = col.key
                if hasattr(val, "right") and hasattr(val, "operator"):
                    op_name = getattr(val.operator, "__name__", None)
                    current = getattr(row, col_name, None) or 0
                    rhs = val.right.value if hasattr(val.right, "value") else val.right
                    if op_name in ("add", "iadd"):
                        setattr(row, col_name, current + rhs)
                    else:
                        raise NotImplementedError(f"unsupported update operator {val.operator!r}")
                else:
                    setattr(row, col_name, val)
        return len(matches)


class _FakeSession:
    def __init__(
        self, *, players=None, sectors=None, regions=None, beacons=None, flags=None,
        fail_delete_ids: Optional[set] = None, fail_refresh_ids: Optional[set] = None,
    ) -> None:
        self.players = players or []
        self.sectors = sectors or []
        self.regions = regions or []
        self.beacons = beacons or []
        self.flags = flags or []
        self.deleted: List[Any] = []
        self.flush_calls = 0
        self.lock_calls: List[int] = []
        self.refresh_calls: List[Any] = []
        # WO-P4 REVISE fix 4 test knob -- beacon ids in this set raise
        # StaleDataError on delete() (and are removed anyway, simulating a
        # row a concurrent transaction already deleted).
        self.fail_delete_ids = fail_delete_ids or set()
        # WO-P4 FINAL-FIX change 3 -- mirrors fail_delete_ids, but for
        # db.refresh(): a PLAIN read takes no row lock, so the row can be
        # removed by a concurrent op AFTER the atomic .update() but before
        # this refresh() call. Real SQLAlchemy raises ObjectDeletedError
        # refreshing a row that's gone; without this knob a plain-read
        # StaleData test would pass vacuously (refresh() never raising).
        self.fail_refresh_ids = fail_refresh_ids or set()

    def query(self, *entities: Any) -> Any:
        head = entities[0]
        if head is Player:
            return _FakeQuery(self.players)
        if head is SectorModel:
            return _FakeQuery(self.sectors)
        if head is Region:
            return _FakeQuery(self.regions)
        if head is MessageBeacon:
            return _FakeQuery(self.beacons)
        if head is MultiAccountFlag:
            return _FakeQuery(self.flags)
        raise AssertionError(f"unexpected query for {entities!r}")

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

    def refresh(self, obj: Any) -> None:
        if getattr(obj, "id", None) in self.fail_refresh_ids:
            raise ObjectDeletedError.__new__(ObjectDeletedError)  # simulated: row gone under us
        # The fake .update() above already mutated this SAME in-memory
        # object directly -- nothing further to reload in the fake. Call
        # is recorded so a test can prove db.refresh() actually happened
        # (fix 5's own contract, distinct from just "the number is right").
        self.refresh_calls.append(obj)


def _player(**overrides: Any) -> SimpleNamespace:
    base = dict(
        id=uuid.uuid4(), username="Voyager7", nickname=None, credits=5000, turns=1000,
        # WO-P4 REVISE fix 3: default matches _sector()'s own default
        # sector_id=42, so every EXISTING salvager/reader fixture in this
        # file satisfies the new location gate without individual edits.
        current_sector_id=42,
        max_turns=1000, last_turn_regeneration=datetime.now(UTC), lifetime_turns_spent=0,
        created_at=datetime.now(UTC) - timedelta(days=30),
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _region(**overrides: Any) -> SimpleNamespace:
    base = dict(id=uuid.uuid4())
    base.update(overrides)
    return SimpleNamespace(**base)


def _sector(region: SimpleNamespace, **overrides: Any) -> SectorModel:
    base = dict(
        id=uuid.uuid4(), sector_id=42, region_id=region.id, is_nexus_protected=False,
        message_beacons=None, name="Test Sector", x_coord=0, y_coord=0,
    )
    base.update(overrides)
    return SectorModel(**base)


def _beacon(region: SimpleNamespace, sector: SectorModel, **overrides: Any) -> SimpleNamespace:
    base = dict(
        id=uuid.uuid4(), region_id=region.id, sector_id=sector.sector_id,
        deployer_player_id=uuid.uuid4(), deployer_nickname_at_deploy="Someone",
        message="A message in a bottle.", expiry=None, read_once=False, read_count=0,
        deployed_at=datetime.now(UTC) - timedelta(hours=1), last_read_at=None,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


# --- DoD 2: read -------------------------------------------------------- #

@pytest.mark.unit
class TestRead:
    def test_read_costs_zero_turns_returns_message_and_author(self) -> None:
        region = _region()
        sector = _sector(region)
        beacon = _beacon(region, sector, message="Watch out for pirates here.", deployer_nickname_at_deploy="Rex")
        reader = _player(current_sector_id=sector.sector_id)
        db = _FakeSession(players=[reader], sectors=[sector], regions=[region], beacons=[beacon])

        result = svc.read(db, beacon.id, reader.id)

        assert result["message"] == "Watch out for pirates here."
        assert result["deployer_nickname"] == "Rex"
        # DoD 2's "costs 0 turns": WO-P4 REVISE fix 3 now requires reading
        # the Player row for the location gate, so "0 turns" is proven via
        # the unchanged turns balance, not "no Player row queried at all"
        # (that invariant no longer holds once the gate needs the reader's
        # current_sector_id).
        assert reader.turns == 1000

    def test_read_increments_read_count_and_last_read_at(self) -> None:
        region = _region()
        sector = _sector(region)
        beacon = _beacon(region, sector, read_count=3)
        reader = _player()
        db = _FakeSession(players=[reader], sectors=[sector], regions=[region], beacons=[beacon])

        result = svc.read(db, beacon.id, reader.id)

        assert beacon.read_count == 4
        assert beacon.last_read_at is not None
        assert result["read_count"] == 4
        assert beacon in db.beacons  # a normal read never removes the row
        # WO-P4 REVISE fix 5: the atomic-update code path was actually
        # exercised (not a leftover Python +1), and its contract of
        # re-reading via db.refresh() was honored.
        assert beacon in db.refresh_calls

    def test_read_once_deletes_row_on_read(self) -> None:
        region = _region()
        sector = _sector(region)
        beacon = _beacon(region, sector, read_once=True)
        reader = _player()
        db = _FakeSession(players=[reader], sectors=[sector], regions=[region], beacons=[beacon])

        result = svc.read(db, beacon.id, reader.id)

        assert result["message"] == beacon.message  # the reader still gets the content
        assert beacon in db.deleted
        assert beacon not in db.beacons
        assert sector.message_beacons == []  # denorm reconciled

    def test_read_once_second_read_404s(self) -> None:
        region = _region()
        sector = _sector(region)
        beacon = _beacon(region, sector, read_once=True)
        reader = _player()
        db = _FakeSession(players=[reader], sectors=[sector], regions=[region], beacons=[beacon])
        svc.read(db, beacon.id, reader.id)
        with pytest.raises(BeaconNotFoundError):
            svc.read(db, beacon.id, reader.id)

    def test_non_read_once_beacon_survives_many_reads(self) -> None:
        region = _region()
        sector = _sector(region)
        beacon = _beacon(region, sector, read_once=False)
        reader = _player()
        db = _FakeSession(players=[reader], sectors=[sector], regions=[region], beacons=[beacon])
        for _ in range(5):
            svc.read(db, beacon.id, reader.id)
        assert beacon.read_count == 5
        assert beacon in db.beacons

    def test_read_nonexistent_beacon_404s(self) -> None:
        reader = _player()
        db = _FakeSession(players=[reader])
        with pytest.raises(BeaconNotFoundError):
            svc.read(db, uuid.uuid4(), reader.id)


# --- WO-P4 REVISE fix 3: reader must be in the beacon's sector ------------- #

@pytest.mark.unit
class TestReadLocationGate:
    def test_read_from_wrong_sector_404s(self) -> None:
        region = _region()
        sector = _sector(region, sector_id=42)
        beacon = _beacon(region, sector)
        elsewhere_reader = _player(current_sector_id=99)
        db = _FakeSession(players=[elsewhere_reader], sectors=[sector], regions=[region], beacons=[beacon])
        with pytest.raises(BeaconNotFoundError):
            svc.read(db, beacon.id, elsewhere_reader.id)
        assert beacon in db.beacons  # untouched -- rejected before any mutation

    def test_wrong_sector_and_nonexistent_beacon_raise_the_identical_message(self) -> None:
        """Anti-oracle: a caller must not be able to distinguish "wrong
        sector" from "no such beacon" from the exception text alone."""
        region = _region()
        sector = _sector(region, sector_id=42)
        beacon = _beacon(region, sector)
        elsewhere_reader = _player(current_sector_id=99)
        db = _FakeSession(players=[elsewhere_reader], sectors=[sector], regions=[region], beacons=[beacon])

        with pytest.raises(BeaconNotFoundError) as wrong_sector_exc:
            svc.read(db, beacon.id, elsewhere_reader.id)
        with pytest.raises(BeaconNotFoundError) as missing_exc:
            svc.read(db, uuid.uuid4(), elsewhere_reader.id)

        assert str(wrong_sector_exc.value) == f"Beacon {beacon.id} not found"
        assert "not found" in str(missing_exc.value)

    def test_read_from_the_correct_sector_succeeds(self) -> None:
        region = _region()
        sector = _sector(region, sector_id=42)
        beacon = _beacon(region, sector)
        reader = _player(current_sector_id=42)
        db = _FakeSession(players=[reader], sectors=[sector], regions=[region], beacons=[beacon])
        result = svc.read(db, beacon.id, reader.id)
        assert result["id"] == str(beacon.id)


# --- DoD 3: salvage ------------------------------------------------------- #

@pytest.mark.unit
class TestSalvage:
    def test_salvage_costs_one_turn_refunds_250_removes_row(self) -> None:
        region = _region()
        sector = _sector(region)
        beacon = _beacon(region, sector)
        salvager = _player(credits=1000, turns=100)
        db = _FakeSession(players=[salvager], sectors=[sector], regions=[region], beacons=[beacon])

        result = svc.salvage(db, beacon.id, salvager.id)

        assert salvager.turns == 100 - svc.SALVAGE_TURN_COST
        assert salvager.credits == 1000 + svc.SALVAGE_CREDIT_REFUND
        assert result["salvage_refund"] == svc.SALVAGE_CREDIT_REFUND
        assert beacon not in db.beacons
        assert beacon in db.deleted

    def test_salvage_costs_are_exact_canon_numbers(self) -> None:
        assert svc.SALVAGE_TURN_COST == 1
        assert svc.SALVAGE_CREDIT_REFUND == 250

    def test_salvage_does_not_refund_equipment_cargo(self) -> None:
        """message-beacons.md:42 -- 'no equipment cargo (destroyed with the
        beacon's casing)'. salvage() never touches a Ship at all -- proven
        by NOT constructing a current_ship on the fixture player; if the
        service tried to credit cargo it would AttributeError."""
        region = _region()
        sector = _sector(region)
        beacon = _beacon(region, sector)
        salvager = _player()
        assert not hasattr(salvager, "current_ship")
        db = _FakeSession(players=[salvager], sectors=[sector], regions=[region], beacons=[beacon])
        svc.salvage(db, beacon.id, salvager.id)  # does not raise -- no cargo touch attempted

    def test_any_player_can_salvage_not_only_the_deployer(self) -> None:
        region = _region()
        sector = _sector(region)
        deployer_id = uuid.uuid4()
        beacon = _beacon(region, sector, deployer_player_id=deployer_id)
        stranger = _player()
        db = _FakeSession(players=[stranger], sectors=[sector], regions=[region], beacons=[beacon])
        result = svc.salvage(db, beacon.id, stranger.id)
        assert result["salvage_refund"] == svc.SALVAGE_CREDIT_REFUND

    def test_salvage_rejects_insufficient_turns(self) -> None:
        region = _region()
        sector = _sector(region)
        beacon = _beacon(region, sector)
        salvager = _player(turns=0)
        db = _FakeSession(players=[salvager], sectors=[sector], regions=[region], beacons=[beacon])
        with pytest.raises(BeaconError, match="insufficient_turns"):
            svc.salvage(db, beacon.id, salvager.id)
        assert beacon in db.beacons  # untouched -- no partial removal
        assert salvager.credits == 5000

    def test_salvage_nonexistent_beacon_404s(self) -> None:
        salvager = _player()
        db = _FakeSession(players=[salvager])
        with pytest.raises(BeaconNotFoundError):
            svc.salvage(db, uuid.uuid4(), salvager.id)

    def test_salvage_from_wrong_sector_404s(self) -> None:
        """WO-P4 REVISE fix 3 (cipher, HIGH): id-only lookup let a leaked/
        guessed uuid trigger a remote 250cr/1-turn salvage-farm. A
        location mismatch must reject BEFORE any turn/credit mutation."""
        region = _region()
        sector = _sector(region, sector_id=42)
        beacon = _beacon(region, sector)
        elsewhere_salvager = _player(current_sector_id=99, credits=5000, turns=100)
        db = _FakeSession(players=[elsewhere_salvager], sectors=[sector], regions=[region], beacons=[beacon])

        with pytest.raises(BeaconNotFoundError):
            svc.salvage(db, beacon.id, elsewhere_salvager.id)

        assert beacon in db.beacons  # untouched
        assert elsewhere_salvager.credits == 5000  # no partial refund
        assert elsewhere_salvager.turns == 100  # no partial debit

    def test_salvage_updates_sector_denorm(self) -> None:
        region = _region()
        sector = _sector(region)
        beacon = _beacon(region, sector)
        other = _beacon(region, sector)
        salvager = _player()
        db = _FakeSession(
            players=[salvager], sectors=[sector], regions=[region], beacons=[beacon, other],
        )
        svc.salvage(db, beacon.id, salvager.id)
        assert len(sector.message_beacons) == 1
        assert sector.message_beacons[0]["id"] == str(other.id)


# --- DoD 11 (salvage half): beacon_salvaged event -------------------------- #

@pytest.mark.unit
class TestSalvageBusEvent:
    def test_salvage_builds_the_event_before_deleting_the_row(self) -> None:
        """Regression pin for the exact bug shape this service's own
        docstring warns about: building the WS frame from an ORM instance
        AFTER db.delete() reads expired/stale attributes. salvage() must
        succeed (not raise) even though the beacon row is gone by the time
        the broadcast would fire."""
        region = _region()
        sector = _sector(region)
        beacon = _beacon(region, sector, deployer_nickname_at_deploy="Rex")
        salvager = _player()
        db = _FakeSession(players=[salvager], sectors=[sector], regions=[region], beacons=[beacon])
        result = svc.salvage(db, beacon.id, salvager.id)  # no running loop either -- must not raise
        assert result["id"] == str(beacon.id)


# --- WO-P4 REVISE fix 1: per-sector advisory lock -------------------------- #
# LIVE-PROOF-ONLY BOUNDARY: same as test_message_beacon_deploy.py's own
# TestSectorLock -- a DB-free fake cannot prove two transactions actually
# SERIALIZE (that needs real concurrent Postgres connections, the
# orchestrator's live-DB leg). What's provable here: the lock IS acquired,
# with the correct per-sector key, before the delete + denorm rebuild.

@pytest.mark.unit
class TestSectorLockOnLifecycleOps:
    def test_read_once_delete_acquires_the_sector_lock(self) -> None:
        region = _region()
        sector = _sector(region)
        beacon = _beacon(region, sector, read_once=True)
        reader = _player()
        db = _FakeSession(players=[reader], sectors=[sector], regions=[region], beacons=[beacon])

        svc.read(db, beacon.id, reader.id)

        expected_key = svc._sector_lock_key(region.id, sector.sector_id)
        assert expected_key in db.lock_calls

    def test_plain_read_does_not_touch_the_sector_lock(self) -> None:
        """Non-read_once reads mutate neither the cap nor the denorm (only
        read_count/last_read_at on the beacon row itself) -- no sector-
        scoped race to serialize, so no lock should be acquired."""
        region = _region()
        sector = _sector(region)
        beacon = _beacon(region, sector, read_once=False)
        reader = _player()
        db = _FakeSession(players=[reader], sectors=[sector], regions=[region], beacons=[beacon])

        svc.read(db, beacon.id, reader.id)

        assert db.lock_calls == []

    def test_salvage_acquires_the_sector_lock(self) -> None:
        region = _region()
        sector = _sector(region)
        beacon = _beacon(region, sector)
        salvager = _player()
        db = _FakeSession(players=[salvager], sectors=[sector], regions=[region], beacons=[beacon])

        svc.salvage(db, beacon.id, salvager.id)

        expected_key = svc._sector_lock_key(region.id, sector.sector_id)
        assert expected_key in db.lock_calls


# --- WO-P4 REVISE fix 4: StaleDataError -> clean 404, not a raw 500 ------- #

@pytest.mark.unit
class TestStaleDataOnLifecycleDeletes:
    def test_read_once_hit_by_concurrent_removal_404s_not_500s(self) -> None:
        """Simulates another transaction (a concurrent salvage, or a sweep
        tick) having already removed this exact row between our SELECT and
        this delete. Without fix 4, SQLAlchemy's StaleDataError would
        propagate uncaught past read()'s own except BeaconError clause in
        the route -- a raw 500 instead of a clean 404."""
        region = _region()
        sector = _sector(region)
        beacon = _beacon(region, sector, read_once=True)
        reader = _player()
        db = _FakeSession(
            players=[reader], sectors=[sector], regions=[region], beacons=[beacon],
            fail_delete_ids={beacon.id},
        )
        with pytest.raises(BeaconNotFoundError):
            svc.read(db, beacon.id, reader.id)

    def test_salvage_hit_by_concurrent_removal_404s_not_500s(self) -> None:
        region = _region()
        sector = _sector(region)
        beacon = _beacon(region, sector)
        salvager = _player(credits=5000, turns=100)
        db = _FakeSession(
            players=[salvager], sectors=[sector], regions=[region], beacons=[beacon],
            fail_delete_ids={beacon.id},
        )
        with pytest.raises(BeaconNotFoundError):
            svc.salvage(db, beacon.id, salvager.id)
        # The route rolls back the WHOLE transaction on any BeaconError
        # (including this one) -- so whether the fake left the in-memory
        # turn/credit mutation applied is moot in production, but this
        # proves salvage() itself didn't return a success payload.


# --- WO-P4 FINAL-FIX change 2: plain-read StaleData/ObjectDeletedError --- #

@pytest.mark.unit
class TestStaleDataOnPlainRead:
    def test_concurrent_removal_during_a_plain_read_404s_not_500s(self) -> None:
        """A PLAIN (non-read_once) read takes NO row lock -- unlike
        read_once, salvage, and sweep, which are all protected by fix 1's
        sector lock around their delete. A concurrent salvage/read_once/
        sweep can remove this exact row in the window between
        _load_beacon's SELECT and the atomic UPDATE + db.refresh() below.
        Without change 2, db.refresh() would raise ObjectDeletedError
        uncaught past read()'s own callers -- a raw 500 instead of a
        clean 404. fail_refresh_ids (not fail_delete_ids -- a plain read
        never calls delete()) is what makes this test non-vacuous."""
        region = _region()
        sector = _sector(region)
        beacon = _beacon(region, sector, read_once=False)
        reader = _player()
        db = _FakeSession(
            players=[reader], sectors=[sector], regions=[region], beacons=[beacon],
            fail_refresh_ids={beacon.id},
        )
        with pytest.raises(BeaconNotFoundError):
            svc.read(db, beacon.id, reader.id)

    def test_fail_refresh_knob_does_not_affect_read_once_or_unrelated_beacons(self) -> None:
        """Sanity check on the test harness itself: fail_refresh_ids only
        affects db.refresh() calls (the plain-read path), and only for the
        targeted beacon id -- a different beacon's plain read is
        unaffected."""
        region = _region()
        sector = _sector(region)
        targeted = _beacon(region, sector, read_once=False)
        other = _beacon(region, sector, read_once=False)
        reader = _player()
        db = _FakeSession(
            players=[reader], sectors=[sector], regions=[region], beacons=[targeted, other],
            fail_refresh_ids={targeted.id},
        )
        result = svc.read(db, other.id, reader.id)  # untouched beacon -- succeeds normally
        assert result["id"] == str(other.id)


# --- WO-P4 REVISE fix 5: atomic SQL read_count increment ------------------ #

@pytest.mark.unit
class TestReadCountAtomicUpdate:
    def test_read_uses_the_update_path_and_refreshes_not_a_python_rmw(self) -> None:
        region = _region()
        sector = _sector(region)
        beacon = _beacon(region, sector, read_count=0)
        reader = _player()
        db = _FakeSession(players=[reader], sectors=[sector], regions=[region], beacons=[beacon])

        result = svc.read(db, beacon.id, reader.id)

        assert result["read_count"] == 1
        assert beacon.read_count == 1
        # The atomic UPDATE path was exercised (not `beacon.read_count =
        # beacon.read_count + 1` in Python) -- proven via db.refresh()
        # having been called on this exact row, fix 5's own contract.
        assert beacon in db.refresh_calls
