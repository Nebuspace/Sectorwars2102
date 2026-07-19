"""WO-BEACON-LIFECYCLE -- message_beacon_service.recharge() + the charge-
column migration's backfill.

DB-free: same real SQLAlchemy WHERE-clause interpreter convention as the
sibling beacon test files (test_message_beacon_deploy.py / test_message_
beacon_sweep.py), extended for the operators recharge()'s own queries use.
Each file owns its own fake (house style, test_contract_service.py /
test_contract_escrow.py precedent) rather than sharing one across files.
"""
from __future__ import annotations

import ast
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any, List, Optional

import pytest

from src.models.message_beacon import MessageBeacon
from src.models.multi_account import MultiAccountFlag
from src.models.player import Player
from src.models.region import Region
from src.models.sector import Sector as SectorModel
from src.services import message_beacon_service as svc
from src.services.message_beacon_service import BeaconError, BeaconNotFoundError

# --- WHERE-clause interpreter --------------------------------------------- #

def _match(row: Any, cond: Any) -> bool:
    col_name = cond.left.key
    row_val = getattr(row, col_name, None)
    op_name = getattr(cond.operator, "__name__", None)
    if op_name == "eq":
        return row_val == cond.right.value
    if op_name == "lt":
        return row_val is not None and row_val < cond.right.value
    if op_name == "le":
        return row_val is not None and row_val <= cond.right.value
    if op_name == "is_not":
        return row_val is not None
    raise NotImplementedError(f"unsupported operator {cond.operator!r}")


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

    def populate_existing(self) -> "_FakeQuery":
        return self

    def with_for_update(self) -> "_FakeQuery":
        if self._session is not None:
            self._session.for_update_calls.append(self._entity)
        return self

    def order_by(self, *args: Any) -> "_FakeQuery":
        # Every call site this fake serves orders by deployed_at ascending
        # (oldest first) -- matches the sibling beacon test files' own
        # simplification.
        ordered = sorted(self._matching(), key=lambda r: r.deployed_at)
        return _FakeQuery(ordered, [], self._session, self._entity)

    def _matching(self) -> List[Any]:
        return [row for row in self._rows if all(_match(row, c) for c in self._criteria)]

    def first(self) -> Any:
        matches = self._matching()
        return matches[0] if matches else None

    def all(self) -> List[Any]:
        return self._matching()


class _FakeSession:
    def __init__(
        self, *, players=None, sectors=None, regions=None, beacons=None, flags=None,
    ) -> None:
        self.players = players or []
        self.sectors = sectors or []
        self.regions = regions or []
        self.beacons = beacons or []
        self.flags = flags or []
        self.deleted: List[Any] = []
        self.flush_calls = 0
        self.lock_calls: List[int] = []
        self.for_update_calls: List[Optional[str]] = []

    def query(self, *entities: Any) -> Any:
        head = entities[0]
        if head is Player:
            return _FakeQuery(self.players, session=self, entity="Player")
        if head is SectorModel:
            return _FakeQuery(self.sectors, session=self, entity="Sector")
        if head is Region:
            return _FakeQuery(self.regions, session=self, entity="Region")
        if head is MessageBeacon:
            return _FakeQuery(self.beacons, session=self, entity="MessageBeacon")
        if head is MultiAccountFlag:
            return _FakeQuery(self.flags, session=self, entity="MultiAccountFlag")
        raise AssertionError(f"unexpected query for {entities!r}")

    def delete(self, obj: Any) -> None:
        # salvage()'s ORM-style delete (now against an explicitly locked
        # row -- see salvage()'s own REVISE docstring -- so no
        # StaleDataError simulation is needed here the way the sweep
        # fake's conditional-DELETE branch below needs one).
        self.deleted.append(obj)
        if obj in self.beacons:
            self.beacons.remove(obj)

    def flush(self) -> None:
        self.flush_calls += 1

    def commit(self) -> None:
        raise AssertionError("service functions are flush-only -- the route commits")

    def execute(self, statement: Any, params: Optional[dict] = None) -> Any:
        params = params or {}
        if "key" in params:
            self.lock_calls.append(params.get("key"))
            return SimpleNamespace(scalar=lambda: True)
        if "id" in params:
            # WO-BEACON-LIFECYCLE REVISE FIX 3 -- sweep_expired's
            # conditional `DELETE ... WHERE id = :id AND expiry < :now`,
            # needed here so a "recharge survives a concurrent sweep"
            # test can call svc.sweep_expired() against this SAME fake.
            beacon_id = params["id"]
            now = params["now"]
            match = next((b for b in self.beacons if b.id == beacon_id), None)
            if match is None or match.expiry is None or not (match.expiry < now):
                return SimpleNamespace(rowcount=0)
            self.beacons.remove(match)
            self.deleted.append(match)
            return SimpleNamespace(rowcount=1)
        raise AssertionError(f"unexpected execute() params: {params!r}")


# --- fixtures --------------------------------------------------------------- #

def _player(**overrides: Any) -> SimpleNamespace:
    base = dict(
        id=uuid.uuid4(), username="Voyager7", nickname=None, credits=10000,
        current_sector_id=42,
        # Only needed by cross-function tests that also exercise
        # salvage() (turn_service.regenerate_turns is the REAL function,
        # not mocked) -- recharge() itself never touches turns.
        turns=1000, max_turns=1000, last_turn_regeneration=datetime.now(UTC),
        lifetime_turns_spent=0,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _region(**overrides: Any) -> SimpleNamespace:
    base = dict(id=uuid.uuid4(), trade_bonuses={})
    base.update(overrides)
    return SimpleNamespace(**base)


def _sector(region: SimpleNamespace, **overrides: Any) -> SectorModel:
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
        charge_expires_at=None, last_charged_at=None,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


_NOW = datetime(2026, 7, 12, 12, 0, tzinfo=UTC)


# --- auth predicate: owner OR present -------------------------------------- #

@pytest.mark.unit
class TestRechargeAuth:
    def test_owner_can_recharge_remotely_not_present(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(svc, "_now", lambda: _NOW)
        region = _region()
        sector = _sector(region, sector_id=42)
        owner_id = uuid.uuid4()
        beacon = _beacon(
            region, sector, deployer_player_id=owner_id,
            charge_expires_at=_NOW + timedelta(days=10),
        )
        owner = _player(id=owner_id, current_sector_id=999)  # NOT in the beacon's sector
        db = _FakeSession(players=[owner], sectors=[sector], regions=[region], beacons=[beacon])

        result = svc.recharge(db, beacon.id, owner_id)

        assert result["id"] == str(beacon.id)
        assert owner.credits == 10000 - svc.RECHARGE_CREDIT_COST

    def test_present_non_owner_can_recharge(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(svc, "_now", lambda: _NOW)
        region = _region()
        sector = _sector(region, sector_id=42)
        beacon = _beacon(region, sector, charge_expires_at=_NOW + timedelta(days=10))
        bystander = _player(current_sector_id=42)  # present, not the owner
        db = _FakeSession(players=[bystander], sectors=[sector], regions=[region], beacons=[beacon])

        result = svc.recharge(db, beacon.id, bystander.id)

        assert result["id"] == str(beacon.id)
        assert bystander.credits == 10000 - svc.RECHARGE_CREDIT_COST

    def test_neither_owner_nor_present_is_rejected_as_not_found(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Anti-oracle, matching read()/salvage()'s own convention: a
        caller who's neither the owner nor present gets the SAME 404 a
        truly nonexistent beacon id would -- can't distinguish the two."""
        monkeypatch.setattr(svc, "_now", lambda: _NOW)
        region = _region()
        sector = _sector(region, sector_id=42)
        beacon = _beacon(region, sector, charge_expires_at=_NOW + timedelta(days=10))
        stranger = _player(current_sector_id=999)  # not present, not the owner
        db = _FakeSession(players=[stranger], sectors=[sector], regions=[region], beacons=[beacon])

        with pytest.raises(BeaconNotFoundError):
            svc.recharge(db, beacon.id, stranger.id)
        assert stranger.credits == 10000  # no debit

    def test_nonexistent_beacon_id_is_not_found(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(svc, "_now", lambda: _NOW)
        player = _player()
        db = _FakeSession(players=[player])
        with pytest.raises(BeaconNotFoundError):
            svc.recharge(db, uuid.uuid4(), player.id)


# --- lock order: Beacon-then-Player ----------------------------------------- #

@pytest.mark.unit
class TestRechargeLockOrder:
    def test_locks_beacon_then_player(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(svc, "_now", lambda: _NOW)
        region = _region()
        sector = _sector(region, sector_id=42)
        beacon = _beacon(region, sector, charge_expires_at=_NOW + timedelta(days=10))
        player = _player(current_sector_id=42)
        db = _FakeSession(players=[player], sectors=[sector], regions=[region], beacons=[beacon])

        svc.recharge(db, beacon.id, player.id)

        assert db.for_update_calls == ["MessageBeacon", "Player"]

    def test_recharge_locks_the_sector_before_rebuilding_the_denorm(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(svc, "_now", lambda: _NOW)
        region = _region()
        sector = _sector(region, sector_id=42)
        beacon = _beacon(region, sector, charge_expires_at=_NOW + timedelta(days=10))
        player = _player(current_sector_id=42)
        db = _FakeSession(players=[player], sectors=[sector], regions=[region], beacons=[beacon])

        svc.recharge(db, beacon.id, player.id)

        expected_key = svc._sector_lock_key(region.id, sector.sector_id)
        assert expected_key in db.lock_calls


# --- atomic debit ------------------------------------------------------------ #

@pytest.mark.unit
class TestRechargeDebit:
    def test_debits_exactly_200_credits(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(svc, "_now", lambda: _NOW)
        region = _region()
        sector = _sector(region, sector_id=42)
        beacon = _beacon(region, sector, charge_expires_at=_NOW + timedelta(days=10))
        player = _player(current_sector_id=42, credits=1000)
        db = _FakeSession(players=[player], sectors=[sector], regions=[region], beacons=[beacon])

        result = svc.recharge(db, beacon.id, player.id)

        assert svc.RECHARGE_CREDIT_COST == 200
        assert player.credits == 800
        assert result["credits"] == 800

    def test_insufficient_credits_rejected_no_partial_debit(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(svc, "_now", lambda: _NOW)
        region = _region()
        sector = _sector(region, sector_id=42)
        beacon = _beacon(region, sector, charge_expires_at=_NOW + timedelta(days=10))
        player = _player(current_sector_id=42, credits=199)
        db = _FakeSession(players=[player], sectors=[sector], regions=[region], beacons=[beacon])

        with pytest.raises(BeaconError, match="insufficient_credits"):
            svc.recharge(db, beacon.id, player.id)
        assert player.credits == 199
        assert beacon.charge_expires_at == _NOW + timedelta(days=10)  # untouched

    def test_exactly_200_credits_is_sufficient(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(svc, "_now", lambda: _NOW)
        region = _region()
        sector = _sector(region, sector_id=42)
        beacon = _beacon(region, sector, charge_expires_at=_NOW + timedelta(days=10))
        player = _player(current_sector_id=42, credits=200)
        db = _FakeSession(players=[player], sectors=[sector], regions=[region], beacons=[beacon])

        svc.recharge(db, beacon.id, player.id)
        assert player.credits == 0

    def test_never_double_debits_a_single_recharge_call(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(svc, "_now", lambda: _NOW)
        region = _region()
        sector = _sector(region, sector_id=42)
        beacon = _beacon(region, sector, charge_expires_at=_NOW + timedelta(days=10))
        player = _player(current_sector_id=42, credits=10000)
        db = _FakeSession(players=[player], sectors=[sector], regions=[region], beacons=[beacon])

        svc.recharge(db, beacon.id, player.id)
        assert player.credits == 10000 - svc.RECHARGE_CREDIT_COST  # exactly one debit


# --- charge math: max(charge_expires_at, now) + 30d, capped ---------------- #

@pytest.mark.unit
class TestRechargeChargeMath:
    def test_active_beacon_stacks_a_cell_on_top_of_its_remaining_runway(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """REVISE FIX 4 (cipher+mack, corrects the original min()-based
        contract bug): charge_expires_at = max(current, now) + 30d. A
        still-alive beacon (current > now) STACKS the new cell on top of
        its remaining runway rather than discarding it."""
        monkeypatch.setattr(svc, "_now", lambda: _NOW)
        region = _region()
        sector = _sector(region, sector_id=42)
        beacon = _beacon(region, sector, charge_expires_at=_NOW + timedelta(days=25))
        player = _player(current_sector_id=42)
        db = _FakeSession(players=[player], sectors=[sector], regions=[region], beacons=[beacon])

        svc.recharge(db, beacon.id, player.id)

        assert beacon.charge_expires_at == _NOW + timedelta(days=25) + svc.CHARGE_CELL_DURATION
        assert beacon.expiry == beacon.charge_expires_at + svc.GRACE_PERIOD
        assert beacon.last_charged_at == _NOW

    def test_dark_husk_revives_with_a_fresh_cell_from_now(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """REVISE FIX 4: a DARK-but-not-yet-grace-deleted beacon has no
        remaining runway to stack onto -- max(past charge_expires_at,
        now) picks "now", so it revives with a FULL FRESH cell, same as
        a beacon with no prior charge at all."""
        monkeypatch.setattr(svc, "_now", lambda: _NOW)
        region = _region()
        sector = _sector(region, sector_id=42)
        old_charge_expires_at = _NOW - timedelta(days=3)  # DARK for 3 days
        beacon = _beacon(
            region, sector, charge_expires_at=old_charge_expires_at,
            expiry=old_charge_expires_at + svc.GRACE_PERIOD,  # still inside grace
        )
        player = _player(current_sector_id=42)
        db = _FakeSession(players=[player], sectors=[sector], regions=[region], beacons=[beacon])

        svc.recharge(db, beacon.id, player.id)

        assert beacon.charge_expires_at == _NOW + svc.CHARGE_CELL_DURATION
        assert svc._decay_state(beacon, _NOW) == "ACTIVE"  # revived, no longer DARK

    def test_missing_charge_expires_at_treated_as_no_runway(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(svc, "_now", lambda: _NOW)
        region = _region()
        sector = _sector(region, sector_id=42)
        beacon = _beacon(region, sector, charge_expires_at=None)
        player = _player(current_sector_id=42)
        db = _FakeSession(players=[player], sectors=[sector], regions=[region], beacons=[beacon])

        svc.recharge(db, beacon.id, player.id)

        assert beacon.charge_expires_at == _NOW + svc.CHARGE_CELL_DURATION

    def test_the_90d_cap_now_binds_stacks_to_ceiling_then_rejects(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """REVISE FIX 4 (cipher+mack): the cap is now genuinely
        load-bearing. Starting fresh, three back-to-back recharges on a
        beacon that stays active stack to exactly the 90d/3-cell ceiling
        (30d -> 60d -> 90d); a FOURTH is REJECTED with no partial debit
        and no charge mutation."""
        monkeypatch.setattr(svc, "_now", lambda: _NOW)
        region = _region()
        sector = _sector(region, sector_id=42)
        beacon = _beacon(region, sector, charge_expires_at=None)  # fresh, no runway yet
        player = _player(current_sector_id=42, credits=10_000)
        db = _FakeSession(players=[player], sectors=[sector], regions=[region], beacons=[beacon])

        svc.recharge(db, beacon.id, player.id)  # -> 30d
        assert beacon.charge_expires_at == _NOW + svc.CHARGE_CELL_DURATION
        svc.recharge(db, beacon.id, player.id)  # -> 60d
        assert beacon.charge_expires_at == _NOW + (svc.CHARGE_CELL_DURATION * 2)
        svc.recharge(db, beacon.id, player.id)  # -> 90d, exactly at the ceiling
        assert beacon.charge_expires_at == _NOW + (svc.CHARGE_CELL_DURATION * svc.MAX_CHARGE_CELLS)

        credits_before_rejected_attempt = player.credits
        with pytest.raises(BeaconError, match="ERR_RECHARGE_CAP_REACHED"):
            svc.recharge(db, beacon.id, player.id)  # -> would be 120d, REJECTED

        assert player.credits == credits_before_rejected_attempt  # no partial debit
        assert beacon.charge_expires_at == _NOW + (svc.CHARGE_CELL_DURATION * svc.MAX_CHARGE_CELLS)

    def test_no_turn_cost(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Recharge costs credits only, no turns -- the response payload
        carries no "turns" key (unlike deploy/salvage)."""
        monkeypatch.setattr(svc, "_now", lambda: _NOW)
        region = _region()
        sector = _sector(region, sector_id=42)
        beacon = _beacon(region, sector, charge_expires_at=_NOW + timedelta(days=10))
        player = _player(current_sector_id=42)
        db = _FakeSession(players=[player], sectors=[sector], regions=[region], beacons=[beacon])

        result = svc.recharge(db, beacon.id, player.id)
        assert "turns" not in result


# --- denorm reflects the post-recharge state immediately ------------------- #

@pytest.mark.unit
class TestRechargeDenormRefresh:
    def test_recharging_a_fading_beacon_flips_the_denorm_back_to_active(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(svc, "_now", lambda: _NOW)
        region = _region()
        sector = _sector(region, sector_id=42)
        beacon = _beacon(region, sector, charge_expires_at=_NOW + svc.FADING_WINDOW)
        player = _player(current_sector_id=42)
        db = _FakeSession(players=[player], sectors=[sector], regions=[region], beacons=[beacon])

        result = svc.recharge(db, beacon.id, player.id)

        assert result["state"] == "ACTIVE"
        cell = sector.message_beacons[0]
        assert cell["state"] == "ACTIVE"
        assert "signal" not in cell


# --- REVISE: canonical lock order (Sector -> Beacon -> Player) ------------- #
# LIVE-PROOF-ONLY BOUNDARY (matches every other lock test in this file's
# sibling files): a DB-free, single-threaded fake cannot make two
# transactions actually CONTEND for a lock, so it can't directly witness a
# real Postgres deadlock or its absence. What IS provable here: (1) both
# recharge() and salvage() now acquire their locks in the SAME relative
# order (Beacon before Player, both after the Sector advisory lock), which
# is the actual precondition for deadlock-freedom between them; (2) a
# same-player recharge-then-salvage sequence on the SAME beacon completes
# cleanly with no exception escaping either call. The genuine 2-connection
# deadlock proof is the orchestrator's own live-DB window leg.

@pytest.mark.unit
class TestCanonicalLockOrderAcrossMethods:
    def test_recharge_locks_sector_then_beacon_then_player(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(svc, "_now", lambda: _NOW)
        region = _region()
        sector = _sector(region, sector_id=42)
        beacon = _beacon(region, sector, charge_expires_at=_NOW + timedelta(days=10))
        player = _player(current_sector_id=42)
        db = _FakeSession(players=[player], sectors=[sector], regions=[region], beacons=[beacon])

        svc.recharge(db, beacon.id, player.id)

        expected_key = svc._sector_lock_key(region.id, sector.sector_id)
        assert db.lock_calls[0] == expected_key  # sector lock acquired FIRST
        assert db.for_update_calls == ["MessageBeacon", "Player"]  # then Beacon, then Player

    def test_salvage_locks_sector_then_beacon_then_player(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """REVISE FIX 1 (mack CRITICAL): salvage() previously locked
        Player FIRST and only implicitly locked the Beacon at db.delete()'s
        flush -- the reverse of recharge()'s order. Now both agree."""
        monkeypatch.setattr(svc, "_now", lambda: _NOW)
        region = _region()
        sector = _sector(region, sector_id=42)
        beacon = _beacon(region, sector, charge_expires_at=_NOW + timedelta(days=10))
        player = _player(current_sector_id=42)
        db = _FakeSession(players=[player], sectors=[sector], regions=[region], beacons=[beacon])

        svc.salvage(db, beacon.id, player.id)

        expected_key = svc._sector_lock_key(region.id, sector.sector_id)
        assert db.lock_calls[0] == expected_key  # sector lock acquired FIRST
        assert db.for_update_calls == ["MessageBeacon", "Player"]  # SAME order as recharge()

    def test_recharge_then_salvage_same_player_same_beacon_completes_cleanly(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A same-player double-click/retry sequence (recharge, then
        salvage the SAME beacon they just topped up) must resolve to a
        clean success or a clean BeaconError/BeaconNotFoundError -- never
        an uncaught exception that would surface as a raw HTTP 500 at the
        route layer (the failure mode mack's CRITICAL flagged)."""
        monkeypatch.setattr(svc, "_now", lambda: _NOW)
        region = _region()
        sector = _sector(region, sector_id=42)
        beacon = _beacon(region, sector, charge_expires_at=_NOW + timedelta(days=10))
        player = _player(current_sector_id=42, credits=10000)
        db = _FakeSession(players=[player], sectors=[sector], regions=[region], beacons=[beacon])

        recharge_result = svc.recharge(db, beacon.id, player.id)
        assert recharge_result["id"] == str(beacon.id)

        salvage_result = svc.salvage(db, beacon.id, player.id)
        assert salvage_result["id"] == str(beacon.id)
        assert beacon not in db.beacons  # actually removed
        # 10000 - 200 (recharge) + 250 (salvage refund) = 10050
        assert player.credits == 10000 - svc.RECHARGE_CREDIT_COST + svc.SALVAGE_CREDIT_REFUND


# --- REVISE FIX 3: a paid recharge survives a racing sweep ----------------- #

@pytest.mark.unit
class TestRechargeSurvivesConcurrentSweep:
    def test_a_recharged_beacon_is_not_swept_even_if_it_was_a_stale_candidate(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Simulates the exact race REVISE FIX 3 closes: a beacon is
        already past its (old) hard-delete deadline -- a sweep tick would
        have deleted it -- but a player pays to recharge it FIRST. The
        sweep, run afterward against the SAME session/row list, must find
        NOTHING to delete: the recharge's extended `expiry` no longer
        matches the conditional DELETE's `WHERE ... AND expiry < :now`.
        The 200cr the player paid is not wasted."""
        monkeypatch.setattr(svc, "_now", lambda: _NOW)
        region = _region()
        sector = _sector(region, sector_id=42)
        # DARK for 8 days -- one day PAST its own grace deadline
        # (charge_expires_at + GRACE_PERIOD), i.e. exactly the husk a
        # sweep tick would hard-delete right now, before any recharge.
        old_charge_expires_at = _NOW - timedelta(days=8)
        beacon = _beacon(
            region, sector, charge_expires_at=old_charge_expires_at,
            expiry=old_charge_expires_at + svc.GRACE_PERIOD,
        )
        # Sanity: this beacon WOULD be swept right now, before any recharge.
        assert beacon.expiry < _NOW

        player = _player(current_sector_id=42, credits=10000)
        db = _FakeSession(players=[player], sectors=[sector], regions=[region], beacons=[beacon])

        recharge_result = svc.recharge(db, beacon.id, player.id)
        assert player.credits == 10000 - svc.RECHARGE_CREDIT_COST  # paid, not wasted

        sweep_result = svc.sweep_expired(db, now=_NOW)

        assert sweep_result["expired"] == 0  # nothing swept
        assert beacon in db.beacons  # the paid-for revival survives
        assert recharge_result["state"] in ("ACTIVE", "FADING")


# --- charge-column migration ------------------------------------------------ #

_MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "alembic" / "versions" / "09d0c6e55927_add_beacon_charge_columns.py"
)


@pytest.mark.unit
class TestChargeColumnMigration:
    """Falsifiable coverage bullet 5: every existing beacon gets exactly
    one fresh 30d cell (expiry = +37d), anchored to the migration's OWN
    apply time -- NOT `deployed_at` (which would instantly orphan/drop any
    beacon already older than ~37 days the moment this migration lands).
    A live-DB apply/backfill proof is the orchestrator's own window leg;
    what's provable here (DB-free) is that the migration's SOURCE does
    what it claims -- an AST-pin, not a string/grep match, so a comment
    quoting these same literals elsewhere in the file can't false-pass
    or false-fail it."""

    @staticmethod
    def _upgrade_source() -> str:
        """Source-with-comments, used only for the add_column assertions
        below -- comments are fine there since neither assertion checks
        for a SPECIFIC string's absence."""
        assert _MIGRATION_PATH.exists(), f"migration file missing: {_MIGRATION_PATH}"
        module_source = _MIGRATION_PATH.read_text()
        module = ast.parse(module_source)
        upgrade_fn = next(
            n for n in module.body if isinstance(n, ast.FunctionDef) and n.name == "upgrade"
        )
        return ast.get_source_segment(module_source, upgrade_fn) or ""

    @staticmethod
    def _backfill_sql() -> str:
        """The LITERAL string argument passed to op.execute() inside
        upgrade() -- AST-extracted (not a source-line slice) specifically
        so this can't be false-failed by a surrounding CODE COMMENT that
        happens to mention "deployed_at" in prose (this file's own
        anchoring rationale does, deliberately, right above the call)."""
        module_source = _MIGRATION_PATH.read_text()
        module = ast.parse(module_source)
        upgrade_fn = next(
            n for n in module.body if isinstance(n, ast.FunctionDef) and n.name == "upgrade"
        )
        for node in ast.walk(upgrade_fn):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "execute"
                and node.args
                and isinstance(node.args[0], ast.Constant)
            ):
                return node.args[0].value
        raise AssertionError("no op.execute(<literal SQL>) call found in upgrade()")

    def test_adds_two_nullable_columns(self) -> None:
        source = self._upgrade_source()
        assert "charge_expires_at" in source
        assert "last_charged_at" in source
        assert source.count("nullable=True") >= 2

    def test_backfill_is_anchored_to_now_not_deployed_at(self) -> None:
        sql = self._backfill_sql()
        assert "deployed_at" not in sql  # never reads the row's own age
        assert "now()" in sql

    def test_backfill_grants_exactly_one_30d_cell_with_37d_hard_delete_deadline(self) -> None:
        sql = self._backfill_sql()
        assert "interval '30 days'" in sql
        assert "interval '37 days'" in sql
        assert "charge_expires_at" in sql
        assert "expiry" in sql

    def test_down_revision_matches_the_confirmed_live_head(self) -> None:
        """Pins this migration onto the single confirmed alembic head
        (`alembic heads` -- verified live, not grepped/guessed) so this
        migration can't silently create a second branch."""
        module_source = _MIGRATION_PATH.read_text()
        tree = ast.parse(module_source)
        assigns = {
            n.targets[0].id: n.value.value
            for n in tree.body
            if isinstance(n, ast.Assign) and isinstance(n.targets[0], ast.Name)
            and isinstance(n.value, ast.Constant)
        }
        assert assigns.get("down_revision") == "b9a7404a2c20"
        assert assigns.get("revision") == "09d0c6e55927"
