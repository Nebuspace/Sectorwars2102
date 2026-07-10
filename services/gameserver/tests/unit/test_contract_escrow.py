"""WO-ECON-CONTRACT-2-PLAYER-ESCROW lane 3 -- post_player_contract /
cancel_player_contract + the escrow-touching edits to complete()/abandon().
ESCROW CONSERVATION is the star: every test that moves credits asserts the
FULL sum-invariance, not just the mutated side.

DB-free: the same real SQLAlchemy WHERE-clause interpreter as test_
contract_service.py, extended to dispatch on entity identity across
Resource/Station/Contract/Player (mirrors test_contract_generator.py's own
multi-entity dispatch convention) and to handle `.is_(True)` (a distinct
`is_` operator, not `eq` -- `Column.is_(True)`'s right-hand side is a SQL
literal, not a BindParameter with `.value`).
"""
from __future__ import annotations

import operator
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from typing import Any, List, Optional

import pytest
from sqlalchemy.sql.operators import in_op, is_

from src.models.contract import Contract, ContractEscrowState, ContractIssuerType, ContractStatus
from src.models.player import Player
from src.models.resource import Resource
from src.models.ship import Ship, ShipType
from src.models.station import Station, StationStatus
from src.services import contract_service


def _match(row: Any, cond: Any) -> bool:
    col_name = cond.left.key
    row_val = getattr(row, col_name, None)
    if cond.operator is operator.eq:
        return row_val == cond.right.value
    if cond.operator is in_op:
        return row_val in cond.right.value
    if cond.operator is operator.lt:
        return row_val < cond.right.value
    if cond.operator is is_:
        return bool(row_val) is True
    raise NotImplementedError(f"unsupported operator {cond.operator!r}")


class _FakeResult:
    def __init__(self, rowcount: int) -> None:
        self.rowcount = rowcount


class _FakeQuery:
    def __init__(self, rows: List[Any], criteria: Optional[List[Any]] = None) -> None:
        self._rows = rows
        self._criteria = criteria or []

    def filter(self, *conditions: Any) -> "_FakeQuery":
        return _FakeQuery(self._rows, self._criteria + list(conditions))

    def first(self) -> Any:
        for row in self._rows:
            if all(_match(row, c) for c in self._criteria):
                return row
        return None

    def all(self) -> List[Any]:
        return [row for row in self._rows if all(_match(row, c) for c in self._criteria)]

    def count(self) -> int:
        return len(self.all())


class _FakeSession:
    def __init__(
        self, *, players: Optional[List[Any]] = None, stations: Optional[List[Any]] = None,
        contracts: Optional[List[Any]] = None, resources: Optional[List[Any]] = None,
    ) -> None:
        self.players = players or []
        self.stations = stations or []
        self.contracts = contracts or []
        self.resources = resources or []
        self.added: List[Any] = []
        self.flush_calls = 0

    def query(self, *entities: Any) -> _FakeQuery:
        head = entities[0]
        if head is Player:
            return _FakeQuery(self.players)
        if head is Contract:
            return _FakeQuery(self.contracts)
        if head is Resource:
            return _FakeQuery(self.resources)
        if head is Station or head is Station.id:
            return _FakeQuery(self.stations)
        raise AssertionError(f"unexpected query for {entities!r}")

    def add(self, obj: Any) -> None:
        self.added.append(obj)
        self.contracts.append(obj)

    def execute(self, stmt: Any) -> _FakeResult:
        values = {col.name: bind.value for col, bind in stmt._values.items()}
        matched = 0
        for row in self.contracts:
            if all(_match(row, c) for c in stmt._where_criteria):
                for k, v in values.items():
                    setattr(row, k, v)
                matched += 1
        return _FakeResult(matched)

    def flush(self) -> None:
        self.flush_calls += 1

    def commit(self) -> None:
        raise AssertionError("service functions are flush-only -- the route commits")


# --- fixtures ------------------------------------------------------------ #

def _player(**overrides: Any) -> SimpleNamespace:
    base = dict(id=uuid.uuid4(), credits=10000, is_docked=False, current_port_id=None, current_ship=None)
    base.update(overrides)
    return SimpleNamespace(**base)


def _station(**overrides: Any) -> SimpleNamespace:
    base = dict(id=uuid.uuid4(), region_id=uuid.uuid4(), status=StationStatus.OPERATIONAL)
    base.update(overrides)
    return SimpleNamespace(**base)


def _resource(**overrides: Any) -> SimpleNamespace:
    base = dict(name="ore", is_active=True)
    base.update(overrides)
    return SimpleNamespace(**base)


_NOW = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
_FAR_DEADLINE = _NOW + timedelta(hours=4)


def _post_kwargs(destination: SimpleNamespace, **overrides: Any) -> dict:
    base = dict(
        destination_station_id=destination.id, commodity_type="ore", quantity=50,
        payment=Decimal("1000"), deadline=_FAR_DEADLINE, now=_NOW,
    )
    base.update(overrides)
    return base


@pytest.mark.unit
class TestPostPlayerContract:
    def test_debits_escrow_exact_amount_no_insurance(self) -> None:
        issuer = _player(credits=5000)
        destination = _station()
        db = _FakeSession(players=[issuer], stations=[destination], resources=[_resource()])

        result = contract_service.post_player_contract(db, issuer.id, **_post_kwargs(destination))

        assert result["escrow_amount"] == 1000.0
        assert issuer.credits == 4000  # 5000 - 1000, exact debit -- nothing left "sitting"
        c = db.added[0]
        assert c.issuer_type == ContractIssuerType.PLAYER
        assert c.issuer_id == issuer.id
        assert c.status == ContractStatus.POSTED
        assert c.escrow_amount == Decimal("1000.00")
        assert c.escrow_state == ContractEscrowState.HELD
        assert c.penalty == Decimal("1000.00")  # default 1.0x payment
        assert db.flush_calls == 1

    def test_debits_combined_escrow_with_insurance_pool_reserve(self) -> None:
        issuer = _player(credits=5000)
        destination = _station()
        db = _FakeSession(players=[issuer], stations=[destination], resources=[_resource()])

        result = contract_service.post_player_contract(
            db, issuer.id, **_post_kwargs(destination, insurance_pool_reserve=Decimal("200")),
        )

        assert result["escrow_amount"] == 1200.0
        assert issuer.credits == 3800

    def test_insufficient_credits_400_zero_rows(self) -> None:
        issuer = _player(credits=500)
        destination = _station()
        db = _FakeSession(players=[issuer], stations=[destination], resources=[_resource()])

        with pytest.raises(contract_service.ContractError, match="insufficient_credits"):
            contract_service.post_player_contract(db, issuer.id, **_post_kwargs(destination))

        assert issuer.credits == 500  # untouched
        assert db.added == []

    def test_unknown_commodity_rejected(self) -> None:
        issuer = _player()
        destination = _station()
        db = _FakeSession(players=[issuer], stations=[destination], resources=[_resource(name="ore")])
        with pytest.raises(contract_service.ContractError, match="unknown_commodity"):
            contract_service.post_player_contract(
                db, issuer.id, **_post_kwargs(destination, commodity_type="water_crystals"),
            )
        assert db.added == []

    def test_inactive_commodity_rejected(self) -> None:
        issuer = _player()
        destination = _station()
        db = _FakeSession(players=[issuer], stations=[destination], resources=[_resource(is_active=False)])
        with pytest.raises(contract_service.ContractError, match="unknown_commodity"):
            contract_service.post_player_contract(db, issuer.id, **_post_kwargs(destination))

    def test_destination_not_found_rejected(self) -> None:
        issuer = _player()
        db = _FakeSession(players=[issuer], stations=[], resources=[_resource()])
        with pytest.raises(contract_service.ContractError, match="not found"):
            contract_service.post_player_contract(
                db, issuer.id, **_post_kwargs(_station()),  # never added to db.stations
            )

    def test_destination_abandoned_rejected(self) -> None:
        issuer = _player()
        destination = _station(status=StationStatus.ABANDONED)
        db = _FakeSession(players=[issuer], stations=[destination], resources=[_resource()])
        with pytest.raises(contract_service.ContractError, match="offline"):
            contract_service.post_player_contract(db, issuer.id, **_post_kwargs(destination))

    def test_quantity_and_payment_must_be_positive(self) -> None:
        issuer = _player()
        destination = _station()
        db = _FakeSession(players=[issuer], stations=[destination], resources=[_resource()])
        with pytest.raises(contract_service.ContractError, match="quantity"):
            contract_service.post_player_contract(db, issuer.id, **_post_kwargs(destination, quantity=0))
        with pytest.raises(contract_service.ContractError, match="payment"):
            contract_service.post_player_contract(db, issuer.id, **_post_kwargs(destination, payment=Decimal("0")))

    def test_blocklist_seam_is_actually_consulted(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Proves _is_player_blocklisted is a real gate, not decoration --
        monkeypatched True blocks an otherwise-fully-valid post."""
        issuer = _player()
        destination = _station()
        db = _FakeSession(players=[issuer], stations=[destination], resources=[_resource()])
        monkeypatch.setattr(contract_service, "_is_player_blocklisted", lambda db, pid: True)
        with pytest.raises(contract_service.ContractError, match="blocklisted"):
            contract_service.post_player_contract(db, issuer.id, **_post_kwargs(destination))

    # --- deadline floor boundary ---

    def test_deadline_exactly_at_floor_is_accepted(self) -> None:
        issuer = _player()
        destination = _station()
        db = _FakeSession(players=[issuer], stations=[destination], resources=[_resource()])
        deadline = _NOW + timedelta(
            hours=contract_service.PLAYER_POST_MIN_DEADLINE_HOURS,
        )
        result = contract_service.post_player_contract(
            db, issuer.id, **_post_kwargs(destination, deadline=deadline),
        )
        assert result["status"] == "posted"

    def test_deadline_one_second_under_the_floor_is_rejected(self) -> None:
        issuer = _player()
        destination = _station()
        db = _FakeSession(players=[issuer], stations=[destination], resources=[_resource()])
        deadline = (
            _NOW + timedelta(hours=contract_service.PLAYER_POST_MIN_DEADLINE_HOURS)
            - timedelta(seconds=1)
        )
        with pytest.raises(contract_service.ContractError, match="deadline"):
            contract_service.post_player_contract(
                db, issuer.id, **_post_kwargs(destination, deadline=deadline),
            )

    # --- per-region posting cap boundary ---

    def _seed_active_postings(self, issuer_id: uuid.UUID, destination: SimpleNamespace, count: int) -> List[Any]:
        return [
            SimpleNamespace(
                id=uuid.uuid4(), issuer_type=ContractIssuerType.PLAYER, issuer_id=issuer_id,
                status=ContractStatus.POSTED, destination_station_id=destination.id,
            )
            for _ in range(count)
        ]

    def test_ninth_posting_in_region_is_allowed_tenth_is_not(self) -> None:
        issuer = _player(credits=100000)
        destination = _station()
        db = _FakeSession(
            players=[issuer], stations=[destination], resources=[_resource()],
            contracts=self._seed_active_postings(issuer.id, destination, 9),
        )
        # The 10th active posting (index 9, i.e. count already at 9) is allowed --
        result = contract_service.post_player_contract(db, issuer.id, **_post_kwargs(destination))
        assert result["status"] == "posted"

        # Now at 10 active -- the 11th attempt is rejected.
        with pytest.raises(contract_service.ContractError, match="posting_cap_reached"):
            contract_service.post_player_contract(db, issuer.id, **_post_kwargs(destination))

    def test_cap_only_counts_the_same_region(self) -> None:
        issuer = _player(credits=100000)
        capped_region_destination = _station()
        other_region_destination = _station()  # different region_id (fresh uuid4)
        db = _FakeSession(
            players=[issuer],
            stations=[capped_region_destination, other_region_destination],
            resources=[_resource()],
            contracts=self._seed_active_postings(issuer.id, capped_region_destination, 10),
        )
        with pytest.raises(contract_service.ContractError, match="posting_cap_reached"):
            contract_service.post_player_contract(db, issuer.id, **_post_kwargs(capped_region_destination))

        # A different region's board is untouched by the other region's cap.
        result = contract_service.post_player_contract(db, issuer.id, **_post_kwargs(other_region_destination))
        assert result["status"] == "posted"


@pytest.mark.unit
class TestEscrowConservationEndToEnd:
    def _posted_contract(self, issuer: SimpleNamespace, destination: SimpleNamespace, db: _FakeSession) -> Contract:
        contract_service.post_player_contract(db, issuer.id, **_post_kwargs(destination))
        return db.added[0]

    def test_post_to_complete_conserves_the_sum(self) -> None:
        issuer = _player(credits=5000)
        acceptor = _player(credits=5000)
        destination = _station()
        db = _FakeSession(players=[issuer, acceptor], stations=[destination], resources=[_resource()])
        starting_total = issuer.credits + acceptor.credits

        contract = self._posted_contract(issuer, destination, db)
        assert issuer.credits == 4000  # escrow (1000) left immediately at post

        contract_service.accept(db, contract.id, acceptor.id, now=_NOW)
        fee = 20  # 2% of 1000
        assert acceptor.credits == 5000 - fee

        acceptor.is_docked = True
        acceptor.current_port_id = destination.id
        # flag_modified needs a real ORM Ship for complete()'s cargo mutation.
        acceptor.current_ship = Ship(
            id=uuid.uuid4(), name="Freighter", type=ShipType.LIGHT_FREIGHTER, sector_id=1,
            is_destroyed=False, cargo={"capacity": 500, "used": 50, "contents": {"ore": 50}},
        )

        contract_service.complete(db, contract.id, acceptor.id, now=_NOW)

        # Issuer never gets anything back on a clean completion -- the escrow
        # was the payment. Acceptor receives the full payment, minus the
        # (already sunk, unrefunded) acceptance fee.
        assert issuer.credits == 4000
        assert acceptor.credits == 5000 - fee + 1000
        # Total in the system: issuer+acceptor lost exactly `fee` (sunk to
        # nowhere) -- the payment itself is fully conserved (left issuer,
        # arrived at acceptor), only the acceptance fee is a genuine sink.
        assert (issuer.credits + acceptor.credits) == starting_total - fee
        assert contract.escrow_state == ContractEscrowState.RELEASED

    def test_posted_cancel_refunds_99_percent_1_percent_sinks(self) -> None:
        issuer = _player(credits=5000)
        destination = _station()
        db = _FakeSession(players=[issuer], stations=[destination], resources=[_resource()])
        starting = issuer.credits

        contract = self._posted_contract(issuer, destination, db)
        assert issuer.credits == starting - 1000

        result = contract_service.cancel_player_contract(db, contract.id, issuer.id, now=_NOW)

        assert result["refund"] == 990.0  # 99% of 1000
        assert issuer.credits == starting - 1000 + 990  # net -10, the 1% sink
        assert contract.status == ContractStatus.CANCELLED
        assert contract.escrow_state == ContractEscrowState.REFUNDING

    def test_accepted_cancel_kill_fee_matrix_acceptor_gets_nothing(self) -> None:
        issuer = _player(credits=5000)
        acceptor = _player(credits=5000)
        destination = _station()
        db = _FakeSession(players=[issuer, acceptor], stations=[destination], resources=[_resource()])

        contract = self._posted_contract(issuer, destination, db)
        contract_service.accept(db, contract.id, acceptor.id, now=_NOW)
        acceptor_balance_after_accept = acceptor.credits  # 5000 - 20 (2% fee)
        assert acceptor_balance_after_accept == 4980

        result = contract_service.cancel_player_contract(db, contract.id, issuer.id, now=_NOW)

        # escrow=1000, accept_fee_equivalent=20 (2%), cancel_fee=100 (10% of
        # payment) -> issuer refund = 1000 - 20 - 100 = 880.
        assert result["refund"] == 880.0
        assert issuer.credits == 5000 - 1000 + 880
        # The escrow table's Acceptor column is a flat "0" for this row --
        # the acceptor's balance is UNCHANGED by the issuer's cancellation.
        assert acceptor.credits == acceptor_balance_after_accept
        assert contract.status == ContractStatus.CANCELLED

    def test_abandon_on_player_contract_refunds_issuer_in_full(self) -> None:
        issuer = _player(credits=5000)
        acceptor = _player(credits=5000)
        destination = _station()
        db = _FakeSession(players=[issuer, acceptor], stations=[destination], resources=[_resource()])

        contract = self._posted_contract(issuer, destination, db)
        contract_service.accept(db, contract.id, acceptor.id, now=_NOW)
        acceptor_after_accept = acceptor.credits  # 5000 - 20

        result = contract_service.abandon(db, contract.id, acceptor.id, now=_NOW)

        # Acceptor pays the FLAT penalty (1.0x payment, unchanged mechanic
        # from WO-ECON-CONTRACT-1-KERNEL) -- issuer's FULL escrow refunds
        # separately (the new addition this WO).
        assert result["penalty_charged"] == 1000
        assert acceptor.credits == acceptor_after_accept - 1000
        assert issuer.credits == 5000 - 1000 + 1000  # escrow fully returned
        assert contract.escrow_state == ContractEscrowState.REFUNDING


@pytest.mark.unit
class TestDoubleReleaseImpossibility:
    def test_complete_cannot_release_escrow_twice(self) -> None:
        issuer = _player(credits=5000)
        acceptor = _player(credits=5000)
        destination = _station()
        db = _FakeSession(players=[issuer, acceptor], stations=[destination], resources=[_resource()])

        contract_service.post_player_contract(db, issuer.id, **_post_kwargs(destination))
        contract = db.added[0]
        contract_service.accept(db, contract.id, acceptor.id, now=_NOW)

        acceptor.is_docked = True
        acceptor.current_port_id = destination.id
        acceptor.current_ship = Ship(
            id=uuid.uuid4(), name="Freighter", type=ShipType.LIGHT_FREIGHTER, sector_id=1,
            is_destroyed=False, cargo={"capacity": 500, "used": 50, "contents": {"ore": 50}},
        )

        contract_service.complete(db, contract.id, acceptor.id, now=_NOW)
        credits_after_first_release = acceptor.credits
        assert contract.escrow_state == ContractEscrowState.RELEASED

        with pytest.raises(contract_service.ContractConflictError):
            contract_service.complete(db, contract.id, acceptor.id, now=_NOW)

        assert acceptor.credits == credits_after_first_release  # no second payout
        assert contract.escrow_state == ContractEscrowState.RELEASED  # unchanged

    def test_cancel_after_terminal_status_rejected_no_double_refund(self) -> None:
        issuer = _player(credits=5000)
        destination = _station()
        db = _FakeSession(players=[issuer], stations=[destination], resources=[_resource()])

        contract_service.post_player_contract(db, issuer.id, **_post_kwargs(destination))
        contract = db.added[0]
        contract_service.cancel_player_contract(db, contract.id, issuer.id, now=_NOW)
        credits_after_first_cancel = issuer.credits

        with pytest.raises(contract_service.ContractConflictError):
            contract_service.cancel_player_contract(db, contract.id, issuer.id, now=_NOW)

        assert issuer.credits == credits_after_first_cancel  # no second refund


@pytest.mark.unit
class TestNpcPathByteUnchangedRegression:
    """Pin: WO-ECON-CONTRACT-2-PLAYER-ESCROW touches complete()/abandon(),
    both gated on `issuer_type == PLAYER`. An NPC-issued contract must run
    through exactly the pre-existing WO-ECON-CONTRACT-1-KERNEL math -- no
    new branch fires, no crash from `_load_player` on a station-id
    issuer_id."""

    def _npc_contract(self, destination: SimpleNamespace) -> SimpleNamespace:
        return SimpleNamespace(
            id=uuid.uuid4(), issuer_type=ContractIssuerType.NPC, issuer_id=destination.id,
            acceptor_player_id=None, destination_station_id=destination.id,
            commodity_type="ore", quantity=50, status=ContractStatus.ACCEPTED,
            payment=Decimal("1000.00"), penalty=Decimal("1000.00"),
            acceptance_fee_pct=Decimal("2.0"), escrow_amount=Decimal("0"),
            escrow_state=ContractEscrowState.HELD, deadline=_FAR_DEADLINE,
            posted_at=_NOW, accepted_at=_NOW, completed_at=None,
        )

    def test_npc_complete_mints_unchanged_escrow_state_untouched(self) -> None:
        destination = _station()
        acceptor = _player(
            credits=1000, is_docked=True, current_port_id=destination.id,
            current_ship=Ship(
                id=uuid.uuid4(), name="Freighter", type=ShipType.LIGHT_FREIGHTER, sector_id=1,
                is_destroyed=False, cargo={"capacity": 500, "used": 50, "contents": {"ore": 50}},
            ),
        )
        contract = self._npc_contract(destination)
        contract.acceptor_player_id = acceptor.id
        db = _FakeSession(players=[acceptor], contracts=[contract])

        result = contract_service.complete(db, contract.id, acceptor.id, now=_NOW)

        assert result["payout"] == 1000
        assert acceptor.credits == 2000  # minted, exactly as WO-1
        assert contract.escrow_state == ContractEscrowState.HELD  # untouched -- NOT flipped to released

    def test_npc_abandon_no_escrow_branch_no_crash(self) -> None:
        destination = _station()
        acceptor = _player(credits=1000)
        contract = self._npc_contract(destination)
        contract.acceptor_player_id = acceptor.id
        db = _FakeSession(players=[acceptor], contracts=[contract])  # NO station-as-player row exists

        result = contract_service.abandon(db, contract.id, acceptor.id, now=_NOW)

        assert result["penalty_charged"] == 1000
        assert acceptor.credits == 0  # exactly as WO-1's flat-penalty math
        assert contract.escrow_state == ContractEscrowState.HELD  # untouched -- NPC branch never fires
