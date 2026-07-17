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
from sqlalchemy.sql.elements import Null, True_
from sqlalchemy.sql.operators import in_op, is_

from src.models.contract import (
    Contract,
    ContractEscrowState,
    ContractInsuranceCoverageTier,
    ContractIssuerType,
    ContractStatus,
)
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
        # WO-CONTRACT-1-INSURANCE: generalized beyond the original hardcoded
        # `bool(row_val) is True` (which silently mis-evaluated the NEW
        # `Contract.insurance_coverage_tier.is_(None)` clause -- `cond.right`
        # for an IS clause is a SQL singleton (Null()/True_()/False_()), not
        # a BindParameter with `.value`, so an isinstance check against the
        # actual right-hand singleton is the only correct read). `is_(True)`
        # (Resource.is_active) keeps working identically to before.
        if isinstance(cond.right, Null):
            return row_val is None
        if isinstance(cond.right, True_):
            return row_val is True
        raise NotImplementedError(f"unsupported IS operand {cond.right!r}")
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

    def with_for_update(self) -> "_FakeQuery":
        # WO-ECON-CONTRACT-MONEY-HARDEN: no-op passthrough -- see
        # test_contract_service.py's sibling copy of this method for the
        # full rationale (real locking is proven live on Postgres, not
        # faked here).
        return self

    def populate_existing(self) -> "_FakeQuery":
        # WO-MONEY-REREAD-CLASS: no-op passthrough, matching the real
        # chainable Query API this fake models -- contract_service._load_player
        # now chains .populate_existing() ahead of .with_for_update() on every
        # for_update=True re-read (identity-map freshness, see
        # money-reread-class-fake-query-passthrough in mack's project memory).
        # This fake has no identity map to refresh; the passthrough just keeps
        # the query chain from AttributeError-ing.
        return self

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

    def begin_nested(self) -> "_FakeNestedTransaction":
        return _FakeNestedTransaction()

    def commit(self) -> None:
        raise AssertionError("service functions are flush-only -- the route commits")


class _FakeNestedTransaction:
    """WO-ECON-CONTRACT-MONEY-HARDEN: no-op savepoint passthrough -- see
    test_contract_service.py's sibling copy of this class for the full
    rationale."""

    def __enter__(self) -> "_FakeNestedTransaction":
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> bool:
        return False


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

    def test_post_to_expire_refunds_issuer_and_conserves_the_sum(self) -> None:
        """WO-DRIFT-econ-expired-escrow-refund -- contracts.md:71: an
        unaccepted PLAYER-issued posting whose deadline passes must return
        the issuer's escrow in full, mirroring abandon()'s refund idiom."""
        issuer = _player(credits=5000)
        destination = _station()
        db = _FakeSession(players=[issuer], stations=[destination], resources=[_resource()])
        deadline = _NOW + timedelta(hours=2)

        contract_service.post_player_contract(
            db, issuer.id, **_post_kwargs(destination, deadline=deadline, payment=Decimal("1500")),
        )
        contract = db.added[0]
        assert issuer.credits == 3500  # 5000 - 1500 escrow debited at post time

        past_deadline = deadline + timedelta(seconds=1)
        result = contract_service.sweep_expired_contracts(db, now=past_deadline)

        assert result == {"expired": 1}
        assert contract.status == ContractStatus.EXPIRED
        assert contract.escrow_state == ContractEscrowState.REFUNDING
        # Exact escrow (N) refunded, not a percentage -- the issuer did
        # nothing wrong here (no acceptor, no kill-fee). Full conservation:
        # the issuer ends exactly where they started.
        assert issuer.credits == 5000

    def test_accepted_expiry_charges_acceptor_and_refunds_issuer_in_full(self) -> None:
        """WO-DRIFT-econ-accepted-deadline-expiry -- the ACCEPTED-deadline
        twin of test_post_to_expire_refunds_issuer_and_conserves_the_sum
        above. [NO-CANON] the issuer-refund half is the flagged conservative
        default (see sweep_expired_accepted_contracts's own docstring) --
        reuses abandon()'s exact refund idiom rather than inventing a new
        forfeit-to-sink behavior."""
        issuer = _player(credits=5000)
        acceptor = _player(credits=5000)
        destination = _station()
        db = _FakeSession(players=[issuer, acceptor], stations=[destination], resources=[_resource()])
        deadline = _NOW + timedelta(hours=2)

        contract_service.post_player_contract(
            db, issuer.id, **_post_kwargs(destination, deadline=deadline, payment=Decimal("1000")),
        )
        contract = db.added[0]
        contract_service.accept(db, contract.id, acceptor.id, now=_NOW)
        acceptor_after_accept = acceptor.credits  # 5000 - 20 (2% acceptance fee)
        assert issuer.credits == 4000  # escrow (1000) left at post time

        past_deadline = deadline + timedelta(seconds=1)
        result = contract_service.sweep_expired_accepted_contracts(db, now=past_deadline)

        assert result == {"expired": 1}
        assert contract.status == ContractStatus.EXPIRED
        # Acceptor pays the FLAT penalty (1.0x payment, same math as
        # abandon()) -- the acceptance fee sunk at accept time is NOT
        # refunded (contracts.md's Penalties section: "acceptance fee is
        # not refunded").
        assert acceptor.credits == acceptor_after_accept - 1000
        # Issuer's FULL escrow refunds separately -- the flagged NO-CANON
        # default.
        assert issuer.credits == 4000 + 1000
        assert contract.escrow_state == ContractEscrowState.REFUNDING

    def test_accepted_expiry_does_not_double_refund_an_already_refunding_row(self) -> None:
        """Mirrors test_sweep_does_not_double_refund_an_already_refunding_
        row -- the accepted-expiry sweep's escrow branch is gated on
        `escrow_state == HELD`, same idempotency guard."""
        issuer = _player(credits=5000)
        acceptor = _player(credits=5000)
        destination = _station()
        contract = SimpleNamespace(
            id=uuid.uuid4(), issuer_type=ContractIssuerType.PLAYER, issuer_id=issuer.id,
            acceptor_player_id=acceptor.id, destination_station_id=destination.id,
            commodity_type="ore", quantity=50, status=ContractStatus.ACCEPTED,
            payment=Decimal("1000.00"), penalty=Decimal("1000.00"),
            acceptance_fee_pct=Decimal("2.0"), escrow_amount=Decimal("1000.00"),
            escrow_state=ContractEscrowState.REFUNDING,  # already handled (raced cancel, say)
            deadline=_NOW - timedelta(hours=1), posted_at=_NOW - timedelta(hours=5),
            posting_stations=[destination.id], accepted_at=_NOW - timedelta(hours=4), completed_at=None,
        )
        db = _FakeSession(players=[issuer, acceptor], contracts=[contract])

        result = contract_service.sweep_expired_accepted_contracts(db, now=_NOW)

        assert result == {"expired": 1}  # still expires -- the penalty leg is unconditional
        assert contract.status == ContractStatus.EXPIRED
        assert acceptor.credits == 5000 - 1000  # penalty still charged
        assert issuer.credits == 5000  # untouched -- NOT refunded a second time
        assert contract.escrow_state == ContractEscrowState.REFUNDING  # unchanged

    def test_issuer_cancel_past_deadline_blocked_409_sweep_still_enforces_penalty(self) -> None:
        """Mack HIGH #1 regression (WO-DRIFT-econ-accepted-deadline-expiry
        revise): full post -> accept -> deadline-lapse -> issuer-cancel-
        attempt -> sweep flow, through the realistic post_player_contract/
        accept path (not a hand-built SimpleNamespace) -- proves the fix at
        the level a real caller would hit it. Before the fix, this exact
        cancel call succeeded and silently waived the acceptor's deadline
        penalty (see test_mack_attack_accepted_sweep.py's own before/after
        docstring for the original finding)."""
        issuer = _player(credits=5000)
        acceptor = _player(credits=5000)
        destination = _station()
        db = _FakeSession(players=[issuer, acceptor], stations=[destination], resources=[_resource()])
        deadline = _NOW + timedelta(hours=2)

        contract_service.post_player_contract(
            db, issuer.id, **_post_kwargs(destination, deadline=deadline, payment=Decimal("1000")),
        )
        contract = db.added[0]
        contract_service.accept(db, contract.id, acceptor.id, now=_NOW)
        acceptor_after_accept = acceptor.credits  # 5000 - 20 (2% acceptance fee)
        issuer_after_post = issuer.credits  # 5000 - 1000 escrow

        past_deadline = deadline + timedelta(seconds=1)
        with pytest.raises(contract_service.ContractConflictError, match="past_deadline"):
            contract_service.cancel_player_contract(db, contract.id, issuer.id, now=past_deadline)

        # Blocked BEFORE any mutation -- contract still accepted, both
        # balances exactly where they were, nothing waived or diverged.
        assert contract.status == ContractStatus.ACCEPTED
        assert acceptor.credits == acceptor_after_accept
        assert issuer.credits == issuer_after_post

        # The sweep still enforces the acceptor's WO-guaranteed penalty --
        # no cancel-shaped escape hatch remains.
        result = contract_service.sweep_expired_accepted_contracts(db, now=past_deadline)
        assert result == {"expired": 1}
        assert contract.status == ContractStatus.EXPIRED
        assert acceptor.credits == acceptor_after_accept - 1000  # penalty enforced
        assert issuer.credits == issuer_after_post + 1000  # full escrow refund

    def test_accepted_cancel_with_insurance_refunds_acceptor_pro_rata(self) -> None:
        """WO-CONTRACT-1-INSURANCE (ADR-0062 E-I2): issuer-cancels an
        ACCEPTED, INSURED contract -- the EXISTING kill-fee math (unchanged)
        AND the acceptor's pro-rata insurance refund both settle in the
        SAME call. Worked example: STANDARD premium (5% of 1000 = 50),
        accepted_at=T, deadline=T+10h, issuer cancels at T+4h ->
        remaining_fraction=0.6, refund = 50 * 0.6 * 0.90 = 27.00 exactly."""
        issuer = _player(credits=5000)
        acceptor = _player(credits=5000)
        destination = _station()
        db = _FakeSession(players=[issuer, acceptor], stations=[destination], resources=[_resource()])
        accepted_at = _NOW
        deadline = accepted_at + timedelta(hours=10)

        contract_service.post_player_contract(
            db, issuer.id, **_post_kwargs(destination, payment=Decimal("1000"), deadline=deadline),
        )
        contract = db.added[0]
        contract_service.accept(db, contract.id, acceptor.id, now=accepted_at)
        acceptor_after_accept = acceptor.credits  # 5000 - 20

        contract_service.insure(
            db, contract.id, acceptor.id, ContractInsuranceCoverageTier.STANDARD, now=accepted_at,
        )
        acceptor_after_insure = acceptor.credits  # -50 (5% of 1000)
        assert acceptor_after_insure == acceptor_after_accept - 50

        cancelled_at = accepted_at + timedelta(hours=4)
        result = contract_service.cancel_player_contract(db, contract.id, issuer.id, now=cancelled_at)

        # Existing kill-fee math, unchanged: escrow(1000) - accept_fee(20) - cancel_fee(100) = 880.
        assert result["refund"] == 880.0
        assert result["insurance_refund"] == 27
        assert issuer.credits == 5000 - 1000 + 880
        assert acceptor.credits == acceptor_after_insure + 27  # pro-rata insurance refund only
        assert contract.status == ContractStatus.CANCELLED


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

    def test_sweep_does_not_double_refund_an_already_refunding_row(self) -> None:
        """The sweep's per-row candidate query gates on `escrow_state ==
        HELD` -- a row already REFUNDING (an earlier tick, or a raced
        cancel/abandon) must be skipped by the refund branch entirely and
        fall through to the plain bulk status-flip, exactly like an NPC
        row does."""
        issuer = _player(credits=5000)
        destination = _station()
        contract = SimpleNamespace(
            id=uuid.uuid4(), issuer_type=ContractIssuerType.PLAYER, issuer_id=issuer.id,
            acceptor_player_id=None, destination_station_id=destination.id,
            commodity_type="ore", quantity=50, status=ContractStatus.POSTED,
            payment=Decimal("1000.00"), penalty=Decimal("1000.00"),
            acceptance_fee_pct=Decimal("2.0"), escrow_amount=Decimal("1000.00"),
            escrow_state=ContractEscrowState.REFUNDING,  # already handled
            deadline=_NOW - timedelta(hours=1), posted_at=_NOW - timedelta(hours=5),
            posting_stations=[destination.id], accepted_at=None, completed_at=None,
        )
        db = _FakeSession(players=[issuer], contracts=[contract])

        result = contract_service.sweep_expired_contracts(db, now=_NOW)

        assert result == {"expired": 1}  # still gets its status flipped by the bulk pass
        assert contract.status == ContractStatus.EXPIRED
        assert issuer.credits == 5000  # untouched -- NOT refunded a second time
        assert contract.escrow_state == ContractEscrowState.REFUNDING  # unchanged


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
            # WO-CONTRACT-1-INSURANCE
            insurance_coverage_tier=None, insurance_premium_paid=Decimal("0"),
            insurance_claim_filed=False,
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

    def test_npc_expiry_sweep_only_status_flips_no_refund_branch(self) -> None:
        """WO-DRIFT-econ-expired-escrow-refund: the sweep's new per-row
        refund pass is gated on `issuer_type == PLAYER` -- an NPC-issued
        posting must be swept by the plain bulk UPDATE exactly as before,
        with escrow_amount/escrow_state byte-unchanged. Zero players
        seeded on purpose: if the refund branch wrongly matched this row,
        `_load_player(db, destination.id)` would raise (no such player),
        making a regression here fail loudly rather than silently."""
        destination = _station()
        contract = self._npc_contract(destination)
        contract.status = ContractStatus.POSTED
        contract.deadline = _NOW - timedelta(hours=1)
        db = _FakeSession(contracts=[contract])  # NO players seeded at all

        result = contract_service.sweep_expired_contracts(db, now=_NOW)

        assert result == {"expired": 1}
        assert contract.status == ContractStatus.EXPIRED  # the only field touched
        assert contract.escrow_amount == Decimal("0")  # untouched
        assert contract.escrow_state == ContractEscrowState.HELD  # untouched -- refund branch never fires

    def test_npc_accepted_expiry_charges_acceptor_only_no_issuer_refund_branch(self) -> None:
        """WO-DRIFT-econ-accepted-deadline-expiry sibling to the posted-
        expiry test above: an NPC-issued ACCEPTED contract past deadline
        pays the acceptor's penalty (the one leg every row gets, NPC or
        PLAYER) but must NOT touch the issuer-refund branch. NO station-as-
        player row seeded for `destination` on purpose -- if the refund
        branch wrongly matched this row, `_load_player(db, destination.id)`
        would raise (no such player), making a regression fail loudly."""
        destination = _station()
        acceptor = _player(credits=1000)
        contract = self._npc_contract(destination)
        contract.acceptor_player_id = acceptor.id
        contract.deadline = _NOW - timedelta(hours=1)
        db = _FakeSession(players=[acceptor], contracts=[contract])  # NO station-as-player row

        result = contract_service.sweep_expired_accepted_contracts(db, now=_NOW)

        assert result == {"expired": 1}
        assert contract.status == ContractStatus.EXPIRED
        assert acceptor.credits == 0  # 1000 - penalty(1000), exactly as WO-1's flat-penalty math
        assert contract.escrow_amount == Decimal("0")  # untouched
        assert contract.escrow_state == ContractEscrowState.HELD  # untouched -- refund branch never fires
