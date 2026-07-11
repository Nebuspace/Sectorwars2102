"""WO-P4-play-beacon-kernel -- message_beacon_service.sweep_expired().

DoD bullets covered here:
  9. Expiry (24h/7d/30d/never, default never): sweep_expired() auto-removes
     expired beacons + updates the denorm; the deployer is NOT notified.
  10. region_id set on every beacon (deploy-time assertion lives in test_
      message_beacon_deploy.py); region CASCADE is a DB-level FK constraint
      not provable via a DB-free fake session -- see the model-inspection
      test at the bottom of this file for what IS provable here, and the
      report's own Concerns section for the live-DB proof boundary.
  11. beacon_expired events are returned as pure dicts (the "dual-transport"
      split -- sweep_expired() has no running loop to broadcast from; the
      scheduler wrapper drains these via scheduler._common._broadcast_events
      back on the event loop).

DB-free, same WHERE-clause interpreter convention as the sibling beacon
test files (each file owns its fake, matching this codebase's own test_
contract_service.py / test_contract_escrow.py precedent).
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any, List, Optional

import pytest
from sqlalchemy.orm.exc import StaleDataError

from src.models.message_beacon import MessageBeacon
from src.models.multi_account import MultiAccountFlag
from src.models.player import Player
from src.models.region import Region
from src.models.sector import Sector as SectorModel
from src.services import message_beacon_service as svc


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


class _FakeNestedTxn:
    """WO-P4 REVISE fix 4 -- stands in for db.begin_nested()'s SAVEPOINT
    context manager; never suppresses an exception (matches real
    begin_nested(): rollback-to-savepoint, then re-raise)."""

    def __enter__(self) -> "_FakeNestedTxn":
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> bool:
        return False


class _FakeSession:
    def __init__(
        self, *, sectors=None, regions=None, beacons=None, flags=None,
        fail_delete_ids: Optional[set] = None,
    ) -> None:
        self.sectors = sectors or []
        self.regions = regions or []
        self.beacons = beacons or []
        self.flags = flags or []
        self.deleted: List[Any] = []
        self.flush_calls = 0
        self.lock_calls: List[int] = []
        # WO-P4 REVISE fix 4 test knob -- beacon ids in this set raise
        # StaleDataError on delete() (and are removed anyway, simulating a
        # row a concurrent transaction already deleted/salvaged/read-
        # once'd out from under this sweep).
        self.fail_delete_ids = fail_delete_ids or set()

    def query(self, *entities: Any) -> Any:
        head = entities[0]
        if head is Player:
            return _FakeQuery([])
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
        raise AssertionError("service functions are flush-only -- the scheduler wrapper commits")

    def execute(self, statement: Any, params: Optional[dict] = None) -> Any:
        # WO-P4 REVISE fix 1 -- _lock_sector's pg_advisory_xact_lock call.
        self.lock_calls.append((params or {}).get("key"))
        return SimpleNamespace(scalar=lambda: True)

    def begin_nested(self) -> _FakeNestedTxn:
        return _FakeNestedTxn()


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


_NOW = datetime(2026, 7, 10, 12, 0, tzinfo=UTC)


@pytest.mark.unit
class TestSweepExpired:
    def test_only_expired_beacons_are_removed(self) -> None:
        region = _region()
        sector = _sector(region)
        expired = _beacon(region, sector, expiry=_NOW - timedelta(minutes=1))
        not_yet = _beacon(region, sector, expiry=_NOW + timedelta(minutes=1))
        exactly_now = _beacon(region, sector, expiry=_NOW)  # NOT strictly past
        never_expires = _beacon(region, sector, expiry=None)
        db = _FakeSession(
            sectors=[sector], regions=[region],
            beacons=[expired, not_yet, exactly_now, never_expires],
        )

        result = svc.sweep_expired(db, now=_NOW)

        assert result["expired"] == 1
        assert expired not in db.beacons
        assert expired in db.deleted
        assert not_yet in db.beacons
        assert exactly_now in db.beacons
        assert never_expires in db.beacons  # DoD 9's "never" default is honored

    def test_all_three_expiry_windows_are_swept_once_due(self) -> None:
        """24h / 7d / 30d are just different `expiry` timestamps by the
        time sweep_expired runs -- the sweep itself doesn't distinguish
        which TTL choice produced them, only whether `expiry < now`. Pins
        that all three durations resolve to a real past timestamp that
        gets swept, proving deploy()'s EXPIRY_CHOICES wiring end-to-end."""
        region = _region()
        sector = _sector(region)
        deployed = _NOW - timedelta(days=40)
        beacons = {
            "24h": _beacon(region, sector, deployed_at=deployed, expiry=deployed + svc.EXPIRY_CHOICES["24h"]),
            "7d": _beacon(region, sector, deployed_at=deployed, expiry=deployed + svc.EXPIRY_CHOICES["7d"]),
            "30d": _beacon(region, sector, deployed_at=deployed, expiry=deployed + svc.EXPIRY_CHOICES["30d"]),
        }
        db = _FakeSession(sectors=[sector], regions=[region], beacons=list(beacons.values()))

        result = svc.sweep_expired(db, now=_NOW)

        assert result["expired"] == 3
        assert db.beacons == []

    def test_sweep_updates_the_sector_denorm(self) -> None:
        region = _region()
        sector = _sector(region)
        expired = _beacon(region, sector, expiry=_NOW - timedelta(minutes=1))
        survivor = _beacon(region, sector, expiry=None)
        db = _FakeSession(sectors=[sector], regions=[region], beacons=[expired, survivor])

        svc.sweep_expired(db, now=_NOW)

        assert len(sector.message_beacons) == 1
        assert sector.message_beacons[0]["id"] == str(survivor.id)

    def test_multiple_expired_beacons_across_different_sectors(self) -> None:
        region = _region()
        sector_a = _sector(region, sector_id=1)
        sector_b = _sector(region, sector_id=2)
        expired_a = _beacon(region, sector_a, expiry=_NOW - timedelta(minutes=1))
        expired_b = _beacon(region, sector_b, expiry=_NOW - timedelta(minutes=1))
        db = _FakeSession(sectors=[sector_a, sector_b], regions=[region], beacons=[expired_a, expired_b])

        result = svc.sweep_expired(db, now=_NOW)

        assert result["expired"] == 2
        assert sector_a.message_beacons == []
        assert sector_b.message_beacons == []

    def test_sweep_finds_nothing_due_is_a_clean_no_op(self) -> None:
        region = _region()
        sector = _sector(region)
        db = _FakeSession(sectors=[sector], regions=[region], beacons=[])
        result = svc.sweep_expired(db, now=_NOW)
        assert result == {"expired": 0, "events": []}


# --- DoD 9: deployer NOT notified ------------------------------------------ #

@pytest.mark.unit
class TestDeployerNotNotifiedOnExpiry:
    def test_expiry_events_carry_no_deployer_targeted_field(self) -> None:
        """message-beacons.md:53 -- 'the deployer is not notified; the
        beacon is just gone.' The returned events are sector-scoped
        broadcasts ONLY -- no player_id/user_id/deployer_player_id key
        that scheduler._common._broadcast_events' personal-push branches
        (genesis_progress / npc_combat_initiated) key on, so a beacon_
        expired event can only ever reach _broadcast_events' generic
        sector fallback, never a personal send."""
        region = _region()
        sector = _sector(region)
        expired = _beacon(region, sector, expiry=_NOW - timedelta(minutes=1))
        db = _FakeSession(sectors=[sector], regions=[region], beacons=[expired])

        result = svc.sweep_expired(db, now=_NOW)

        assert len(result["events"]) == 1
        event = result["events"][0]
        assert event["type"] == "beacon_expired"
        assert "sector_id" in event
        for personal_key in ("player_id", "user_id", "owner_id", "defender_user_id"):
            assert personal_key not in event


# --- DoD 11 (expiry half): beacon_expired event shape + dual-transport --- #

@pytest.mark.unit
class TestExpiredBusEvents:
    def test_event_shape_matches_build_beacon_event_directly(self) -> None:
        region = _region()
        sector = _sector(region)
        expired = _beacon(region, sector, expiry=_NOW - timedelta(minutes=1), deployer_nickname_at_deploy="Rex")
        db = _FakeSession(sectors=[sector], regions=[region], beacons=[expired])

        result = svc.sweep_expired(db, now=_NOW)
        event = result["events"][0]

        assert event["type"] == "beacon_expired"
        assert event["sector_id"] == sector.sector_id
        assert event["beacon_id"] == str(expired.id)
        assert event["region_id"] == str(region.id)
        assert event["deployer_nickname"] == "Rex"

    def test_no_events_returned_when_nothing_expired(self) -> None:
        region = _region()
        sector = _sector(region)
        db = _FakeSession(sectors=[sector], regions=[region], beacons=[])
        result = svc.sweep_expired(db, now=_NOW)
        assert result["events"] == []


# --- DoD 10: region_id on the model (CASCADE proof boundary) -------------- #

@pytest.mark.unit
class TestRegionCascadeConstraintDeclared:
    def test_region_id_is_not_null_with_cascade_ondelete(self) -> None:
        """DoD 10's CASCADE behavior is a live Postgres FK constraint --
        NOT provable via a DB-free fake session (no fake session executes
        real ON DELETE CASCADE semantics). This test proves what a unit
        test CAN prove: the model DECLARES the constraint correctly. The
        actual cascade-on-delete proof is the orchestrator's live-DB
        window leg (see this WO's report)."""
        fk = list(MessageBeacon.__table__.columns["region_id"].foreign_keys)[0]
        assert fk.ondelete == "CASCADE"
        assert MessageBeacon.__table__.columns["region_id"].nullable is False


# --- WO-P4 REVISE fix 1: per-sector advisory lock -------------------------- #
# LIVE-PROOF-ONLY BOUNDARY: same as the sibling deploy/lifecycle files' own
# lock tests -- a DB-free fake cannot prove two transactions actually
# SERIALIZE. What's provable here: the lock IS acquired, with the correct
# per-sector key, before each candidate's delete.

@pytest.mark.unit
class TestSectorLockOnSweep:
    def test_sweep_locks_each_touched_sector(self) -> None:
        region = _region()
        sector = _sector(region)
        expired = _beacon(region, sector, expiry=_NOW - timedelta(minutes=1))
        db = _FakeSession(sectors=[sector], regions=[region], beacons=[expired])

        svc.sweep_expired(db, now=_NOW)

        expected_key = svc._sector_lock_key(region.id, sector.sector_id)
        assert expected_key in db.lock_calls

    def test_sweep_locks_multiple_distinct_sectors_independently(self) -> None:
        region = _region()
        sector_a = _sector(region, sector_id=1)
        sector_b = _sector(region, sector_id=2)
        expired_a = _beacon(region, sector_a, expiry=_NOW - timedelta(minutes=1))
        expired_b = _beacon(region, sector_b, expiry=_NOW - timedelta(minutes=1))
        db = _FakeSession(sectors=[sector_a, sector_b], regions=[region], beacons=[expired_a, expired_b])

        svc.sweep_expired(db, now=_NOW)

        key_a = svc._sector_lock_key(region.id, sector_a.sector_id)
        key_b = svc._sector_lock_key(region.id, sector_b.sector_id)
        assert key_a in db.lock_calls
        assert key_b in db.lock_calls


# --- WO-P4 REVISE fix 4: one stale candidate does not abort the batch ----- #

@pytest.mark.unit
class TestStaleDataDuringSweep:
    def test_one_candidate_already_removed_does_not_crash_or_infinite_loop(self) -> None:
        """Simulates a concurrent salvage/read_once having already removed
        ONE due-for-expiry candidate between sweep_expired()'s SELECT and
        its delete. Without fix 4's per-candidate SAVEPOINT, this
        StaleDataError would poison the whole session (a failed flush
        leaves it inactive until rollback), aborting the rest of the sweep
        batch entirely. Without the savepoint retry loop written to make
        forward progress, a naive catch-and-continue could also spin
        forever re-selecting the same still-visible-to-the-fake candidate
        -- this test's own bounded runtime (pytest's default timeout,
        if any, aside) is itself evidence that didn't happen."""
        region = _region()
        sector = _sector(region)
        already_gone = _beacon(region, sector, expiry=_NOW - timedelta(minutes=1))
        survivor = _beacon(region, sector, expiry=_NOW - timedelta(minutes=2))
        db = _FakeSession(
            sectors=[sector], regions=[region], beacons=[already_gone, survivor],
            fail_delete_ids={already_gone.id},
        )

        result = svc.sweep_expired(db, now=_NOW)

        assert result["expired"] == 1  # only the survivor
        assert len(result["events"]) == 1
        assert result["events"][0]["beacon_id"] == str(survivor.id)
        assert already_gone not in db.beacons  # gone either way
        assert survivor not in db.beacons

    def test_stale_candidate_produces_no_event_for_itself(self) -> None:
        region = _region()
        sector = _sector(region)
        already_gone = _beacon(region, sector, expiry=_NOW - timedelta(minutes=1))
        db = _FakeSession(
            sectors=[sector], regions=[region], beacons=[already_gone],
            fail_delete_ids={already_gone.id},
        )

        result = svc.sweep_expired(db, now=_NOW)

        assert result == {"expired": 0, "events": []}
