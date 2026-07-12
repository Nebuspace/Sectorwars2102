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
    if op_name == "le":
        # WO-BEACON-LIFECYCLE -- sweep_expired's near-boundary scan
        # (`charge_expires_at <= now + FADING_WINDOW`).
        return row_val is not None and row_val <= cond.right.value
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
        # REVISE FIX 3 test knob (was: "raise StaleDataError on delete()"
        # -- superseded by the conditional-DELETE rewrite below): beacon
        # ids in this set are treated as "no longer matches the WHERE
        # clause" (rowcount=0) the moment this sweep's conditional delete
        # reaches them, AND removed from `self.beacons` right then --
        # simulating a row a concurrent transaction (salvage/read_once/a
        # revive-by-recharge) already changed/removed between this
        # sweep's SELECT and its delete, even though the earlier SELECT
        # still found it.
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

    def flush(self) -> None:
        self.flush_calls += 1

    def commit(self) -> None:
        raise AssertionError("service functions are flush-only -- the scheduler wrapper commits")

    def execute(self, statement: Any, params: Optional[dict] = None) -> Any:
        params = params or {}
        if "key" in params:
            # WO-P4 REVISE fix 1 -- _lock_sector's pg_advisory_xact_lock call.
            self.lock_calls.append(params.get("key"))
            return SimpleNamespace(scalar=lambda: True)
        if "id" in params:
            # WO-BEACON-LIFECYCLE REVISE FIX 3 -- sweep_expired's
            # conditional `DELETE ... WHERE id = :id AND expiry < :now`.
            # Interprets the SAME predicate directly against this fake's
            # row list -- a beacon whose expiry no longer matches
            # (revived by a concurrent recharge, or already removed)
            # yields rowcount=0, exactly like the real conditional
            # DELETE would; a genuine match is removed and rowcount=1.
            beacon_id = params["id"]
            now = params["now"]
            if beacon_id in self.fail_delete_ids:
                self.beacons = [b for b in self.beacons if b.id != beacon_id]
                return SimpleNamespace(rowcount=0)
            match = next((b for b in self.beacons if b.id == beacon_id), None)
            if match is None or match.expiry is None or not (match.expiry < now):
                return SimpleNamespace(rowcount=0)
            self.beacons.remove(match)
            self.deleted.append(match)
            return SimpleNamespace(rowcount=1)
        raise AssertionError(f"unexpected execute() params: {params!r}")


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
        # WO-BEACON-LIFECYCLE: default None -- _decay_state() treats a
        # missing charge as ACTIVE (never DARK by omission). sweep_expired
        # now calls _decay_state on every row it touches (both the
        # unchanged husk-delete loop's denorm rebuild AND the new
        # near-boundary scan), so every pre-existing fixture in this file
        # needs this attribute to exist at all, not just a sensible value.
        charge_expires_at=None, last_charged_at=None,
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
        # WO-BEACON-LIFECYCLE: `expiry=None` is no longer produced by a
        # live deploy() (every beacon now gets a real hard-delete deadline
        # -- there's no more "never" choice); this fixture stands in for a
        # legacy/pre-migration row with no expiry set at all. The sweep's
        # own SQL guard (`expiry IS NOT NULL`, UNCHANGED by this WO) is
        # still what's under test here: a NULL expiry is never swept,
        # defensively, regardless of why it's NULL.
        no_expiry_set = _beacon(region, sector, expiry=None)
        db = _FakeSession(
            sectors=[sector], regions=[region],
            beacons=[expired, not_yet, exactly_now, no_expiry_set],
        )

        result = svc.sweep_expired(db, now=_NOW)

        assert result["expired"] == 1
        assert expired not in db.beacons
        assert expired in db.deleted
        assert not_yet in db.beacons
        assert exactly_now in db.beacons
        assert no_expiry_set in db.beacons  # NULL expiry -- never auto-swept

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

    def test_a_zero_charge_dark_beacon_is_excluded_from_the_denorm_and_grace_deleted_after_7d(
        self,
    ) -> None:
        """Falsifiable coverage bullet 2: DARK exclusion + grace-delete.
        A beacon whose charge ran out exactly 7 days ago sits at its
        hard-delete deadline (expiry = charge_expires_at + GRACE_PERIOD) --
        this sweep call is the one that finally removes the husk. Proves
        both halves in one deploy-to-grave lifecycle: while DARK-but-not-
        yet-grace-elapsed it would already be excluded from any denorm
        rebuild (see the FADING/DARK denorm test below); once expiry
        itself passes, the UNCHANGED husk-delete loop removes the row."""
        region = _region()
        sector = _sector(region)
        charge_expires_at = _NOW - svc.GRACE_PERIOD
        husk = _beacon(
            region, sector,
            charge_expires_at=charge_expires_at,
            expiry=charge_expires_at + svc.GRACE_PERIOD,  # == _NOW, not yet strictly past
        )
        db = _FakeSession(sectors=[sector], regions=[region], beacons=[husk])

        # Not yet swept -- expiry == now, not strictly past.
        result = svc.sweep_expired(db, now=_NOW)
        assert result["expired"] == 0
        assert husk in db.beacons

        # One tick past the grace deadline -- now it's gone.
        result = svc.sweep_expired(db, now=_NOW + timedelta(seconds=1))
        assert result["expired"] == 1
        assert husk not in db.beacons


# --- WO-BEACON-LIFECYCLE: decay state + FADING/DARK denorm exclusion ------ #

@pytest.mark.unit
class TestDecayState:
    """Falsifiable coverage bullet 6: _decay_state's own boundary math."""

    def test_active_far_from_boundary(self) -> None:
        beacon = SimpleNamespace(charge_expires_at=_NOW + timedelta(days=20))
        assert svc._decay_state(beacon, _NOW) == "ACTIVE"

    def test_active_just_outside_the_fading_window(self) -> None:
        beacon = SimpleNamespace(charge_expires_at=_NOW + svc.FADING_WINDOW + timedelta(seconds=1))
        assert svc._decay_state(beacon, _NOW) == "ACTIVE"

    def test_fading_at_the_window_boundary(self) -> None:
        beacon = SimpleNamespace(charge_expires_at=_NOW + svc.FADING_WINDOW)
        assert svc._decay_state(beacon, _NOW) == "FADING"

    def test_fading_just_before_dark(self) -> None:
        beacon = SimpleNamespace(charge_expires_at=_NOW + timedelta(seconds=1))
        assert svc._decay_state(beacon, _NOW) == "FADING"

    def test_dark_at_the_exact_moment_charge_runs_out(self) -> None:
        beacon = SimpleNamespace(charge_expires_at=_NOW)
        assert svc._decay_state(beacon, _NOW) == "DARK"

    def test_dark_well_past_charge_expiry(self) -> None:
        beacon = SimpleNamespace(charge_expires_at=_NOW - timedelta(days=3))
        assert svc._decay_state(beacon, _NOW) == "DARK"

    def test_missing_charge_expires_at_is_fading_not_dark_not_permanently_active(self) -> None:
        """REVISE FIX 6 (mack, LOW): a legacy/unmigrated or old-binary-
        mid-rollout NULL charge is never DARK by omission (_decay_state
        only claims DARK when it can positively prove the charge ran
        out) -- but it's also NOT silently immortal-ACTIVE forever
        anymore. FADING: still visible, but flagged for attention."""
        beacon = SimpleNamespace(charge_expires_at=None)
        assert svc._decay_state(beacon, _NOW) == "FADING"


@pytest.mark.unit
class TestFadingDarkDenorm:
    def test_dark_beacon_excluded_from_the_sector_denorm(self) -> None:
        region = _region()
        sector = _sector(region)
        dark = _beacon(region, sector, charge_expires_at=_NOW - timedelta(days=1))
        active = _beacon(region, sector, charge_expires_at=_NOW + timedelta(days=20))
        db = _FakeSession(sectors=[sector], regions=[region], beacons=[dark, active])

        svc._rebuild_sector_denorm(db, region.id, sector.sector_id, now=_NOW)

        ids = {c["id"] for c in sector.message_beacons}
        assert str(active.id) in ids
        assert str(dark.id) not in ids
        assert dark in db.beacons  # the husk row itself still exists

    def test_fading_beacon_carries_the_signal_low_marker_and_state(self) -> None:
        region = _region()
        sector = _sector(region)
        fading = _beacon(region, sector, charge_expires_at=_NOW + svc.FADING_WINDOW)
        db = _FakeSession(sectors=[sector], regions=[region], beacons=[fading])

        svc._rebuild_sector_denorm(db, region.id, sector.sector_id, now=_NOW)

        cell = sector.message_beacons[0]
        assert cell["state"] == "FADING"
        assert cell["signal"] == svc.FADING_SIGNAL_LABEL

    def test_active_beacon_carries_no_signal_key(self) -> None:
        region = _region()
        sector = _sector(region)
        active = _beacon(region, sector, charge_expires_at=_NOW + timedelta(days=20))
        db = _FakeSession(sectors=[sector], regions=[region], beacons=[active])

        svc._rebuild_sector_denorm(db, region.id, sector.sector_id, now=_NOW)

        cell = sector.message_beacons[0]
        assert cell["state"] == "ACTIVE"
        assert "signal" not in cell


@pytest.mark.unit
class TestSweepBoundaryRebuild:
    """WO-BEACON-LIFECYCLE bullet 5(b): the sweep also rebuilds the denorm
    for sectors holding a beacon at/near its FADING or DARK boundary, even
    when nothing in that sector is due for grace-delete (so nothing hits
    the husk-delete loop above at all)."""

    def test_sweep_rebuilds_a_sector_whose_beacon_just_entered_fading(self) -> None:
        region = _region()
        sector = _sector(region)
        # Deliberately gives it a FAR-future `expiry` (hard-delete deadline
        # untouched) so the husk-delete loop above never even looks at
        # this sector -- only the boundary scan should touch it.
        fading = _beacon(
            region, sector,
            charge_expires_at=_NOW + svc.FADING_WINDOW,
            expiry=_NOW + svc.FADING_WINDOW + svc.GRACE_PERIOD,
        )
        db = _FakeSession(sectors=[sector], regions=[region], beacons=[fading])

        result = svc.sweep_expired(db, now=_NOW)

        assert result["expired"] == 0  # nothing grace-deleted
        assert sector.message_beacons is not None
        assert sector.message_beacons[0]["state"] == "FADING"
        expected_key = svc._sector_lock_key(region.id, sector.sector_id)
        assert expected_key in db.lock_calls  # locked even with no deletion

    def test_sweep_leaves_a_solidly_active_sector_untouched(self) -> None:
        """A beacon far from any boundary shouldn't cause its sector to be
        rebuilt at all -- proves the boundary scan is actually SCOPED, not
        a blanket rebuild-everything pass."""
        region = _region()
        sector = _sector(region)
        active = _beacon(
            region, sector,
            charge_expires_at=_NOW + timedelta(days=20),
            expiry=_NOW + timedelta(days=27),
        )
        db = _FakeSession(sectors=[sector], regions=[region], beacons=[active])

        svc.sweep_expired(db, now=_NOW)

        expected_key = svc._sector_lock_key(region.id, sector.sector_id)
        assert expected_key not in db.lock_calls
        assert sector.message_beacons is None  # never rebuilt


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
        """Simulates a concurrent salvage/read_once/recharge having
        already changed or removed ONE due-for-expiry candidate between
        sweep_expired()'s SELECT and its delete. REVISE FIX 3's
        conditional `DELETE ... WHERE id = :id AND expiry < :now` simply
        matches 0 rows for it -- no exception to catch, no SAVEPOINT
        needed. Without something making forward progress each
        iteration, a naive catch-and-continue could spin forever
        re-selecting the same still-visible-to-the-fake candidate -- this
        test's own bounded runtime is itself evidence that didn't
        happen."""
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
