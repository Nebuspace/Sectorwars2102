"""WO-P2-economy-docking-priority-bump -- construction_service.purchase_
priority_bump + the priority_bumps_count sort-key wiring in _sorted_queue.

Canon: FEATURES/economy/docking-slips.md:118-127 -- "Bump 1 position" 5% /
"Bump 5 positions" 25% / "Bump 10 positions" 60% / "Bump to front" 100% of
total project cost.

DB-free: mirrors test_contract_escrow.py's real SQLAlchemy WHERE-clause
interpreter (`_match`), extended with `.notin_()` (the station-scoped
non-terminal reservation query) and a `.order_by()` passthrough, and
dispatches on Station/Player/ConstructionReservation identity.

Money-path tests give the station a full standard-slip pool (12 filler
`hold_active` reservations) so the reservation under test stays `queued`
through `advance()`'s lazy-engine promotion pass -- a station with slack
capacity would auto-promote a lone queued reservation before the bump fee
even applies, which is CORRECT behavior (no reason to charge a bump fee to
skip a queue that isn't backed up) but would make the money-path assertions
below flaky on which branch fires. The reorder tests below call
`_sorted_queue` directly instead -- it does not touch slip pools at all.
"""
from __future__ import annotations

import operator
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any, List, Optional

import pytest
from sqlalchemy.sql.operators import in_op, not_in_op

from src.models.construction import ConstructionReservation
from src.models.player import Player
from src.models.station import Station
from src.services import construction_service as cs
from src.services.construction_service import ConstructionError


def _match(row: Any, cond: Any) -> bool:
    col_name = cond.left.key
    row_val = getattr(row, col_name, None)
    if cond.operator is operator.eq:
        return row_val == cond.right.value
    if cond.operator is in_op:
        return row_val in cond.right.value
    if cond.operator is not_in_op:
        return row_val not in cond.right.value
    raise NotImplementedError(f"unsupported operator {cond.operator!r}")


class _FakeQuery:
    def __init__(self, rows: List[Any], criteria: Optional[List[Any]] = None) -> None:
        self._rows = rows
        self._criteria = criteria or []

    def filter(self, *conditions: Any) -> "_FakeQuery":
        return _FakeQuery(self._rows, self._criteria + list(conditions))

    def with_for_update(self) -> "_FakeQuery":
        # Real locking is proven live on Postgres, not faked here -- see
        # test_contract_escrow.py's sibling copy of this method.
        return self

    def populate_existing(self) -> "_FakeQuery":
        return self

    def order_by(self, *args: Any) -> "_FakeQuery":
        # _advance_station's candidate-list order doesn't drive correctness
        # (_sorted_queue re-sorts for promotion) -- passthrough only.
        return self

    def first(self) -> Any:
        for row in self._rows:
            if all(_match(row, c) for c in self._criteria):
                return row
        return None

    def all(self) -> List[Any]:
        return [row for row in self._rows if all(_match(row, c) for c in self._criteria)]


class _FakeSession:
    def __init__(
        self, *, players: Optional[List[Any]] = None, stations: Optional[List[Any]] = None,
        reservations: Optional[List[Any]] = None,
    ) -> None:
        self.players = players or []
        self.stations = stations or []
        self.reservations = reservations or []
        self.flush_calls = 0
        self.query_log: List[type] = []  # entity classes, in call order -- lock-order proof

    def query(self, *entities: Any) -> _FakeQuery:
        head = entities[0]
        self.query_log.append(head)
        if head is Player:
            return _FakeQuery(self.players)
        if head is Station:
            return _FakeQuery(self.stations)
        if head is ConstructionReservation:
            return _FakeQuery(self.reservations)
        raise AssertionError(f"unexpected query for {entities!r}")

    def flush(self) -> None:
        self.flush_calls += 1

    def commit(self) -> None:
        raise AssertionError("service functions are flush-only -- the route commits")


# --- fixtures -------------------------------------------------------------- #

_NOW = datetime(2102, 6, 1, 12, 0, 0, tzinfo=UTC)
SCOUT_COST = cs.SHIP_BUILD_SPECS["SCOUT_SHIP"]["total_cost"]  # 40,000


def _player(**overrides: Any) -> SimpleNamespace:
    base = dict(id=uuid.uuid4(), credits=100_000)
    base.update(overrides)
    return SimpleNamespace(**base)


def _station(**overrides: Any) -> SimpleNamespace:
    base = dict(
        id=uuid.uuid4(), tradedock_tier="B", treasury_balance=0, faction_affiliation=None,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _reservation(station: SimpleNamespace, player: SimpleNamespace, **overrides: Any) -> SimpleNamespace:
    base = dict(
        id=uuid.uuid4(), station_id=station.id, player_id=player.id,
        ship_type="SCOUT_SHIP", ship_name=None, state="queued", total_cost=SCOUT_COST,
        deposit_paid=cs.milestone_amounts(SCOUT_COST)["deposit"],
        credits_paid=cs.milestone_amounts(SCOUT_COST)["deposit"],
        priority_bumps_count=0, uses_specialized_slip=False,
        hold_expires_at=None, phase_deadline=None, claim_expires_at=None,
        rent_paid_until=None, rent_owed_since=None, queue_bonus_credit=0,
        milestones={"deposit": True}, resources_required={}, resources_delivered={},
        created_at=_NOW - timedelta(hours=1), updated_at=_NOW - timedelta(hours=1),
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _fill_standard_slots(station: SimpleNamespace, player: SimpleNamespace, count: int = 12) -> List[SimpleNamespace]:
    """12 `hold_active` filler reservations occupying every Tier-B standard
    slip, so a queued reservation under test is NOT auto-promoted by
    advance()'s lazy-engine pass before the bump fee applies."""
    return [
        _reservation(
            station, player, state="hold_active",
            hold_expires_at=_NOW + timedelta(hours=24),
        )
        for _ in range(count)
    ]


# --- money-path tests -------------------------------------------------------- #

class TestPurchasePriorityBumpFees:
    @pytest.mark.parametrize("tier,expected_fee,expected_weight", [
        ("bump_1", 2_000, 1),      # 5% of 40,000
        ("bump_5", 10_000, 5),     # 25%
        ("bump_10", 24_000, 10),   # 60%
        ("bump_front", 40_000, cs.PRIORITY_BUMP_FRONT_WEIGHT),  # 100%
    ])
    def test_debits_correct_percentage_and_weight(self, tier, expected_fee, expected_weight):
        player = _player(credits=100_000)
        station = _station()
        reservation = _reservation(station, player)
        fillers = _fill_standard_slots(station, player)
        db = _FakeSession(players=[player], stations=[station], reservations=[reservation, *fillers])

        result = cs.purchase_priority_bump(db, reservation, player, tier, now=_NOW)

        assert result["fee_paid"] == expected_fee
        assert result["priority_bumps_count"] == expected_weight
        assert reservation.priority_bumps_count == expected_weight
        assert reservation.state == "queued"  # never promoted here -- slots are full

    def test_conservation_debit_equals_credit(self):
        """The fee that leaves the player's credits is EXACTLY the fee that
        lands in the station treasury -- no leak, no double-charge."""
        player = _player(credits=100_000)
        station = _station(treasury_balance=5_000)
        reservation = _reservation(station, player)
        fillers = _fill_standard_slots(station, player)
        db = _FakeSession(players=[player], stations=[station], reservations=[reservation, *fillers])

        cs.purchase_priority_bump(db, reservation, player, "bump_5", now=_NOW)

        assert player.credits == 100_000 - 10_000
        assert station.treasury_balance == 5_000 + 10_000
        # Full conservation across the two ledgers touched.
        assert (100_000 - player.credits) == (station.treasury_balance - 5_000)

    def test_stacked_bumps_accumulate_weight_and_fee(self):
        player = _player(credits=100_000)
        station = _station()
        reservation = _reservation(station, player)
        fillers = _fill_standard_slots(station, player)
        db = _FakeSession(players=[player], stations=[station], reservations=[reservation, *fillers])

        cs.purchase_priority_bump(db, reservation, player, "bump_1", now=_NOW)
        cs.purchase_priority_bump(db, reservation, player, "bump_5", now=_NOW)

        assert reservation.priority_bumps_count == 1 + 5
        assert player.credits == 100_000 - 2_000 - 10_000

    def test_unknown_tier_rejected(self):
        player = _player()
        station = _station()
        reservation = _reservation(station, player)
        db = _FakeSession(players=[player], stations=[station], reservations=[reservation])

        with pytest.raises(ConstructionError) as exc:
            cs.purchase_priority_bump(db, reservation, player, "bump_1000", now=_NOW)
        assert exc.value.status_code == 400
        assert reservation.priority_bumps_count == 0

    def test_insufficient_credits_rejected_and_untouched(self):
        player = _player(credits=1_000)  # short of bump_1's 2,000
        station = _station()
        reservation = _reservation(station, player)
        fillers = _fill_standard_slots(station, player)
        db = _FakeSession(players=[player], stations=[station], reservations=[reservation, *fillers])

        with pytest.raises(ConstructionError) as exc:
            cs.purchase_priority_bump(db, reservation, player, "bump_1", now=_NOW)
        assert exc.value.status_code == 400
        assert player.credits == 1_000  # unchanged
        assert reservation.priority_bumps_count == 0
        assert station.treasury_balance == 0

    def test_rejected_once_slip_is_held(self):
        """A promoted reservation is no longer competing for queue position
        -- the bump fee no longer applies once state has left 'queued'."""
        player = _player()
        station = _station()
        reservation = _reservation(
            station, player, state="hold_active", hold_expires_at=_NOW + timedelta(hours=24),
        )
        db = _FakeSession(players=[player], stations=[station], reservations=[reservation])

        with pytest.raises(ConstructionError) as exc:
            cs.purchase_priority_bump(db, reservation, player, "bump_1", now=_NOW)
        assert exc.value.status_code == 400
        assert reservation.priority_bumps_count == 0

    def test_rejected_when_terminal(self):
        player = _player()
        station = _station()
        reservation = _reservation(station, player, state="cancelled")
        db = _FakeSession(players=[player], stations=[station], reservations=[reservation])

        with pytest.raises(ConstructionError):
            cs.purchase_priority_bump(db, reservation, player, "bump_1", now=_NOW)

    def test_flush_only_never_commits(self):
        """Money-path convention: the service flushes, the route commits."""
        player = _player()
        station = _station()
        reservation = _reservation(station, player)
        fillers = _fill_standard_slots(station, player)
        db = _FakeSession(players=[player], stations=[station], reservations=[reservation, *fillers])

        cs.purchase_priority_bump(db, reservation, player, "bump_1", now=_NOW)

        assert db.flush_calls >= 1
        # _FakeSession.commit() raises -- purchase_priority_bump never calls it.


class TestLockOrdering:
    def test_station_locked_before_player(self):
        """Resource-before-player: the file's own documented lock-ordering
        contract (module docstring) -- Station must be queried/locked before
        Player, so concurrent bumps on the same station's queue serialize on
        the station row, matching every sibling money-path function here
        (pay_milestone, pay_rent, ...)."""
        player = _player()
        station = _station()
        reservation = _reservation(station, player)
        fillers = _fill_standard_slots(station, player)
        db = _FakeSession(players=[player], stations=[station], reservations=[reservation, *fillers])

        cs.purchase_priority_bump(db, reservation, player, "bump_1", now=_NOW)

        first_station_idx = db.query_log.index(Station)
        first_player_idx = db.query_log.index(Player)
        assert first_station_idx < first_player_idx


# --- reorder tests (via _sorted_queue directly -- no slip pools involved) --- #

class TestQueueReorder:
    def test_bumped_reservation_sorts_ahead_of_unbumped_peer(self):
        player_a, player_b = _player(), _player()
        station = _station()
        older_unbumped = _reservation(
            station, player_a, priority_bumps_count=0, created_at=_NOW - timedelta(hours=5),
        )
        newer_bumped = _reservation(
            station, player_b, priority_bumps_count=1, created_at=_NOW - timedelta(hours=1),
        )
        db = _FakeSession(stations=[station])

        order = cs._sorted_queue(db, station, [older_unbumped, newer_bumped])

        # Bumped wins even though it's the newer (normally-later-FIFO) row.
        assert order == [newer_bumped, older_unbumped]

    def test_higher_tier_sorts_ahead_of_lower_tier(self):
        player_a, player_b, player_c = _player(), _player(), _player()
        station = _station()
        res_10 = _reservation(station, player_a, priority_bumps_count=10)
        res_5 = _reservation(station, player_b, priority_bumps_count=5)
        res_front = _reservation(station, player_c, priority_bumps_count=cs.PRIORITY_BUMP_FRONT_WEIGHT)
        db = _FakeSession(stations=[station])

        order = cs._sorted_queue(db, station, [res_10, res_5, res_front])

        assert order == [res_front, res_10, res_5]

    def test_front_tier_dominates_any_realistic_stack_of_lower_tiers(self):
        """Even a peer who repeatedly bought the most expensive POSITIONAL
        tier (bump_10, x50) never outranks a single 'bump to front' buy --
        'front' is categorically the maximal tier, not a relative position."""
        player_a, player_b = _player(), _player()
        station = _station()
        heavily_stacked = _reservation(station, player_a, priority_bumps_count=10 * 50)
        single_front = _reservation(station, player_b, priority_bumps_count=cs.PRIORITY_BUMP_FRONT_WEIGHT)
        db = _FakeSession(stations=[station])

        order = cs._sorted_queue(db, station, [heavily_stacked, single_front])

        assert order == [single_front, heavily_stacked]

    def test_unbumped_peers_keep_existing_fifo_tiebreak(self):
        """Zero bumps on both sides -- ordering falls through to the
        pre-existing (rep desc, deposit desc, created_at asc) tiebreak,
        byte-unchanged."""
        player_a, player_b = _player(), _player()
        station = _station()
        earlier = _reservation(station, player_a, created_at=_NOW - timedelta(hours=3))
        later = _reservation(station, player_b, created_at=_NOW - timedelta(hours=1))
        db = _FakeSession(stations=[station])

        order = cs._sorted_queue(db, station, [later, earlier])

        assert order == [earlier, later]

    def test_status_payload_reports_new_queue_position_after_bump(self):
        player_a, player_b = _player(), _player()
        station = _station()
        front_of_line = _reservation(
            station, player_a, priority_bumps_count=0, created_at=_NOW - timedelta(hours=5),
        )
        just_bumped = _reservation(
            station, player_b, priority_bumps_count=5, created_at=_NOW - timedelta(hours=1),
        )
        db = _FakeSession(
            stations=[station], reservations=[front_of_line, just_bumped],
        )

        payload = cs.status_payload(db, just_bumped, now=_NOW)

        assert payload["priority_bumps_count"] == 5
        assert payload["queue_position"] == 1
        assert payload["queue_length"] == 2
