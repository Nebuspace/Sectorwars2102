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
from pydantic import ValidationError
from sqlalchemy.sql.elements import Null, True_
from sqlalchemy.sql.operators import in_op, is_

from src.api.routes.contracts import PostContractRequest
from src.models.contract import (
    Contract,
    ContractDisputeResolution,
    ContractEscrowState,
    ContractInsuranceCoverageTier,
    ContractIssuerType,
    ContractStatus,
    ContractType,
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
    if cond.operator is operator.ne:
        # WO-CONTRACT-57 addendum: _bulk_expire_remaining_posted_contracts
        # now excludes the per-candidate loop's own eligible set via a
        # `!=` predicate (issuer_type != PLAYER / escrow_state != HELD).
        return row_val != cond.right.value
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


class _SnapshotRestoringNestedTransaction:
    """See `_SnapshotRestoringFakeSession`'s own docstring for why this
    exists (a purpose-built exception to this file's shared `_FakeNested
    Transaction` no-op convention). Snapshots every watched object's
    attribute dict on `__enter__`; restores it verbatim if the block
    raises. A shallow, single-savepoint simulation -- good enough for
    this test's one-nested-block-at-a-time shape, never claims to model
    real Postgres savepoint stacking or cross-session isolation (that
    needs live Postgres, mack's lane)."""

    def __init__(self, watched: List[Any]) -> None:
        self._watched = watched
        self._snapshots: List[dict] = []

    def __enter__(self) -> "_SnapshotRestoringNestedTransaction":
        self._snapshots = [dict(vars(obj)) for obj in self._watched]
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> bool:
        if exc_type is not None:
            for obj, snapshot in zip(self._watched, self._snapshots, strict=True):
                obj.__dict__.clear()
                obj.__dict__.update(snapshot)
        return False


class _SnapshotRestoringFakeSession(_FakeSession):
    """WO-CONTRACT-2b-HOLD-ESCROW gate revise (cipher MEDIUM, stranded-
    escrow atomicity): the shared `_FakeSession`/`_FakeNestedTransaction`
    used everywhere else in this file is a documented pure no-op
    passthrough that does NOT simulate real SAVEPOINT rollback of Python
    attribute mutations (matching this codebase's established DB-free-
    proof-limits convention -- see `TestPerRowSavepointIsolation` in
    test_mack_attack_accepted_sweep.py for the sibling precedent, and its
    own docstring's explicit "not reverted by a savepoint failure"
    caveat for the OLDER guard-before-savepoint shape). This subclass
    DOES simulate it, specifically to prove the ONE new atomicity claim
    the WO-2b gate revise's fix makes: a failure anywhere inside `sweep_
    expired_dispute_window`'s nested block now reverts BOTH the guard's
    `escrow_state` flip AND the refund together, leaving the row exactly
    as it was before the savepoint began -- not stranded."""

    def begin_nested(self) -> _SnapshotRestoringNestedTransaction:
        return _SnapshotRestoringNestedTransaction(list(self.contracts) + list(self.players))


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

    # --- WO-CONTRACT-4-BULK: contract_type ---

    def test_defaults_to_cargo_delivery_when_omitted(self) -> None:
        """Backward compat -- every existing caller that never passes
        `contract_type` at all must keep getting cargo_delivery, byte-
        identical to before this WO."""
        issuer = _player(credits=5000)
        destination = _station()
        db = _FakeSession(players=[issuer], stations=[destination], resources=[_resource()])

        contract_service.post_player_contract(db, issuer.id, **_post_kwargs(destination))

        assert db.added[0].contract_type == ContractType.CARGO_DELIVERY

    def test_bulk_procurement_type_is_honored(self) -> None:
        issuer = _player(credits=5000)
        destination = _station()
        db = _FakeSession(players=[issuer], stations=[destination], resources=[_resource()])

        result = contract_service.post_player_contract(
            db, issuer.id, **_post_kwargs(destination, contract_type=ContractType.BULK_PROCUREMENT),
        )

        c = db.added[0]
        assert c.contract_type == ContractType.BULK_PROCUREMENT
        # Escrow math is ALREADY type-agnostic -- byte-identical to the
        # cargo_delivery case for the same payment/reserve.
        assert result["escrow_amount"] == 1000.0
        assert issuer.credits == 4000
        # The static `penalty` column still seeds to `payment` at post
        # time for EVERY type (unchanged) -- it's only the DEGENERATE
        # resting value for bulk; the real walk-away penalty is computed
        # dynamically at expiry/abandon time from the Locker's fill.
        assert c.penalty == Decimal("1000.00")


@pytest.mark.unit
class TestPostContractRequestContractTypeRestriction:
    """WO-CONTRACT-4-BULK: `PostContractRequest.contract_type` (the route-
    layer Pydantic model, contracts.py) is restricted to {cargo_delivery,
    bulk_procurement} ONLY -- the other 5 ContractType members carry NPC-
    generator-only pricing/reputation logic post_player_contract never
    computes, so a player-post of one of those must be rejected at the
    request-validation boundary, before it ever reaches the service
    layer. Pure Pydantic validation -- no DB/session needed."""

    def _kwargs(self, **overrides: Any) -> dict:
        base = dict(
            destination_station_id=str(uuid.uuid4()), commodity_type="ore",
            quantity=50, payment=Decimal("1000"), deadline=_FAR_DEADLINE,
        )
        base.update(overrides)
        return base

    def test_omitted_defaults_to_cargo_delivery(self) -> None:
        req = PostContractRequest(**self._kwargs())
        assert req.contract_type == ContractType.CARGO_DELIVERY

    def test_explicit_cargo_delivery_accepted(self) -> None:
        req = PostContractRequest(**self._kwargs(contract_type="cargo_delivery"))
        assert req.contract_type == ContractType.CARGO_DELIVERY

    def test_bulk_procurement_accepted(self) -> None:
        req = PostContractRequest(**self._kwargs(contract_type="bulk_procurement"))
        assert req.contract_type == ContractType.BULK_PROCUREMENT

    @pytest.mark.parametrize(
        "rejected_type",
        ["express_delivery", "hazardous_transport", "refugee_transport", "acquisition_bounty", "escort"],
    )
    def test_every_other_contract_type_is_rejected(self, rejected_type: str) -> None:
        with pytest.raises(ValidationError):
            PostContractRequest(**self._kwargs(contract_type=rejected_type))


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

        result = contract_service.complete(db, contract.id, acceptor.id, now=_NOW)

        # WO-C3-a: this contract never funded an insurance_pool_reserve
        # (default 0, see _post_kwargs) -- the issuer's pool refund is a
        # no-op, byte-identical to pre-WO-C3-a behavior. issuer.credits
        # stays exactly where it was; the acceptor receives the full
        # payment, minus the (already sunk, unrefunded) acceptance fee.
        assert result["issuer_pool_refund"] == 0
        assert issuer.credits == 4000
        assert acceptor.credits == 5000 - fee + 1000
        # Total in the system: issuer+acceptor lost exactly `fee` (sunk to
        # nowhere) -- the payment itself is fully conserved (left issuer,
        # arrived at acceptor), only the acceptance fee is a genuine sink.
        assert (issuer.credits + acceptor.credits) == starting_total - fee
        assert contract.escrow_state == ContractEscrowState.RELEASED

    def test_complete_returns_unused_insurance_pool_reserve_to_issuer(self) -> None:
        """WO-C3-a: the sibling of test_post_to_complete_conserves_the_sum
        above, with a funded insurance_pool_reserve -- proves the gap this
        WO closes. Before this WO, a clean completion never returned the
        issuer's pool at all (the escrow table's own Complete row only
        released the acceptor's payment); expiry/abandon already refunded
        it in full (abandon():568, sweep_expired_dispute_window()), so
        only THIS path stranded it."""
        issuer = _player(credits=5000)
        acceptor = _player(credits=5000)
        destination = _station()
        db = _FakeSession(players=[issuer, acceptor], stations=[destination], resources=[_resource()])

        contract_service.post_player_contract(
            db, issuer.id, **_post_kwargs(destination, insurance_pool_reserve=Decimal("200")),
        )
        contract = db.added[0]
        # escrow_amount = payment(1000) + pool(200) = 1200, debited in full
        # at post time -- see test_debits_combined_escrow_with_insurance_
        # pool_reserve above for the isolated post-time proof.
        assert issuer.credits == 3800
        assert contract.escrow_amount == Decimal("1200.00")
        assert contract.insurance_pool_reserve == Decimal("200.00")

        contract_service.accept(db, contract.id, acceptor.id, now=_NOW)
        fee = 20  # 2% of payment (1000), NOT of the combined escrow
        assert acceptor.credits == 5000 - fee

        acceptor.is_docked = True
        acceptor.current_port_id = destination.id
        acceptor.current_ship = Ship(
            id=uuid.uuid4(), name="Freighter", type=ShipType.LIGHT_FREIGHTER, sector_id=1,
            is_destroyed=False, cargo={"capacity": 500, "used": 50, "contents": {"ore": 50}},
        )

        result = contract_service.complete(db, contract.id, acceptor.id, now=_NOW)

        # (a) issuer credited EXACTLY the remaining insurance_pool_reserve
        # (never drawn by a claim here -- see complete()'s own WO-C3-a
        # comment for why a claim draw and a clean completion are
        # structurally mutually exclusive in this codebase).
        assert result["issuer_pool_refund"] == 200
        assert issuer.credits == 3800 + 200
        # (b) acceptor credited payment (no early-arrival bonus --
        # cargo_delivery never gets one).
        assert result["payout"] == 1000
        assert acceptor.credits == 5000 - fee + 1000
        # (c) escrow -> RELEASED.
        assert contract.escrow_state == ContractEscrowState.RELEASED
        # (d) insurance_pool_reserve zeroed after crediting -- mirrors
        # sweep_expired_dispute_window's own escrow_amount zero-out idiom.
        assert contract.insurance_pool_reserve == Decimal("0")
        # (e) escrow_amount LEFT NON-ZERO (Max ruling, abandon()-parity --
        # abandon()/the dispute-window sweep don't zero it after their own
        # full-escrow refunds either; a uniform-zero-terminal-escrow
        # invariant is a separate, un-invented follow-up).
        assert contract.escrow_amount == Decimal("1200.00")
        # (f) EXACT conservation: acceptor's payout + issuer's pool refund
        # == the original combined escrow -- nothing minted, nothing
        # stranded. (The acceptance fee is a SEPARATE, pre-existing sink,
        # already proven independently by test_post_to_complete_conserves_
        # the_sum above -- not part of this identity.)
        assert result["payout"] + result["issuer_pool_refund"] == 1200

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

    def test_accepted_expiry_charges_acceptor_and_holds_escrow(self) -> None:
        """WO-DRIFT-econ-accepted-deadline-expiry -- the ACCEPTED-deadline
        twin of test_post_to_expire_refunds_issuer_and_conserves_the_sum
        above. WO-CONTRACT-2b-HOLD-ESCROW (Max R, option C): the issuer-
        refund half no longer happens HERE -- escrow stays HELD through
        the 48h dispute window (see `sweep_expired_dispute_window`'s own
        docstring for the eventual undisputed refund, or `file_dispute`/
        `resolve_dispute` for a disputed one); superseding the prior
        immediate-refund-at-expiry design this test used to pin."""
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
        # Issuer's escrow is HELD, not refunded, at expiry -- see module
        # docstring for the design change.
        assert issuer.credits == 4000
        assert contract.escrow_state == ContractEscrowState.HELD
        assert contract.escrow_amount == Decimal("1000.00")  # untouched -- no tier, no pool draw

        past_window = past_deadline + timedelta(hours=48, seconds=1)
        window_result = contract_service.sweep_expired_dispute_window(db, now=past_window)

        assert window_result == {"refunded": 1}
        assert issuer.credits == 4000 + 1000  # held escrow reaches the issuer once the window closes
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
            # WO-CONTRACT-4-BULK: the sweep now reads `candidate.contract_
            # type` unconditionally.
            contract_type=ContractType.CARGO_DELIVERY,
            commodity_type="ore", quantity=50, status=ContractStatus.ACCEPTED,
            payment=Decimal("1000.00"), penalty=Decimal("1000.00"),
            acceptance_fee_pct=Decimal("2.0"), escrow_amount=Decimal("1000.00"),
            escrow_state=ContractEscrowState.REFUNDING,  # already handled (raced cancel, say)
            deadline=_NOW - timedelta(hours=1), posted_at=_NOW - timedelta(hours=5),
            posting_stations=[destination.id], accepted_at=_NOW - timedelta(hours=4), completed_at=None,
            # WO-CONTRACT-1b-CLAIM-SAFETY: sweep_expired_accepted_contracts
            # now unconditionally reads these on every candidate.
            insurance_coverage_tier=None, insurance_premium_paid=Decimal("0"),
            insurance_claim_filed=False, insurance_pool_reserve=Decimal("0"),
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
        # no cancel-shaped escape hatch remains. WO-CONTRACT-2b-HOLD-
        # ESCROW: the issuer's escrow is HELD (not refunded) at expiry --
        # unrelated to the mack HIGH #1 fix this test pins, just a
        # different design layer on top of it.
        result = contract_service.sweep_expired_accepted_contracts(db, now=past_deadline)
        assert result == {"expired": 1}
        assert contract.status == ContractStatus.EXPIRED
        assert acceptor.credits == acceptor_after_accept - 1000  # penalty enforced
        assert issuer.credits == issuer_after_post  # escrow HELD, not refunded, at expiry

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

    def test_post_to_expire_to_dispute_destination_unreachable_conserves_the_sum(self) -> None:
        """WO-CONTRACT-2-DISPUTE-T1: post -> accept -> deadline-lapse
        (sweep_expired_accepted_contracts) -> file_dispute (Tier-1
        destination_unreachable), through the REALISTIC post_player_
        contract/accept/sweep/dispute path (not hand-built SimpleNamespaces)
        -- proves conservation end-to-end at the level a real caller would
        hit it. WO-CONTRACT-2b-HOLD-ESCROW: the issuer's escrow now stays
        HELD across the expiry sweep and is returned at DISPUTE-filing
        time instead (via `_settle_dispute_escrow`, since destination_
        unreachable draws nothing issuer-funded -- the FULL held escrow
        returns to the issuer as the settlement's remainder). The sweep's
        own credit-penalty (charged to the acceptor at expiry) is NOT
        reversed by the dispute resolution (see file_dispute's own
        [NO-CANON] note) -- asserted explicitly here as a real, deliberate
        outcome, not an oversight."""
        issuer = _player(credits=5000)
        acceptor = _player(credits=5000)
        destination = _station(status=StationStatus.OPERATIONAL)  # OPERATIONAL at post time
        db = _FakeSession(players=[issuer, acceptor], stations=[destination], resources=[_resource()])
        deadline = _NOW + timedelta(hours=2)

        contract_service.post_player_contract(
            db, issuer.id, **_post_kwargs(destination, deadline=deadline, payment=Decimal("1000")),
        )
        contract = db.added[0]
        # "the destination station went offline mid-delivery" (contracts.md
        # :390's own worked example) -- goes offline AFTER posting, which
        # post_player_contract itself would otherwise reject up front.
        destination.status = StationStatus.ABANDONED
        issuer_after_post = issuer.credits  # 5000 - 1000 escrow
        assert issuer_after_post == 4000

        contract_service.accept(db, contract.id, acceptor.id, now=_NOW)
        acceptor_after_accept = acceptor.credits  # 5000 - 20 (2% fee)
        assert acceptor_after_accept == 4980

        past_deadline = deadline + timedelta(seconds=1)
        swept = contract_service.sweep_expired_accepted_contracts(db, now=past_deadline)
        assert swept == {"expired": 1}
        assert contract.status == ContractStatus.EXPIRED
        acceptor_after_expiry = acceptor.credits  # -1000 (flat penalty)
        assert acceptor_after_expiry == acceptor_after_accept - 1000
        # WO-CONTRACT-2b-HOLD-ESCROW: NOT refunded at expiry -- held.
        assert issuer.credits == issuer_after_post
        assert contract.escrow_state == ContractEscrowState.HELD
        assert contract.escrow_amount == Decimal("1000.00")

        result = contract_service.file_dispute(
            db, contract.id, acceptor.id, "the destination station was offline", now=past_deadline,
        )

        assert result["tier1_resolution"] == "destination_unreachable"
        assert result["payout"] == 20  # 2% acceptance fee refunded in full
        assert contract.status == ContractStatus.CANCELLED
        # A RESOLVED Tier-1 case never touches escalated_to_admin at all --
        # falsy either way (None here: this fixture constructs a raw
        # Contract() directly, never through a real flush/insert that
        # would apply the column's DB-side `default=False`; production
        # rows are False from insert).
        assert not contract.escalated_to_admin
        assert result["escalated_to_admin"] is False  # file_dispute's own return always coerces via bool()

        # Conservation: the dispute refunds the 20cr acceptance fee
        # (acceptor self-refund) AND finally releases the FULL held
        # escrow (1000, untouched) to the issuer as the settlement's
        # remainder -- destination_unreachable draws nothing issuer-
        # funded, so the entire held ledger returns. The acceptor's
        # earlier flat penalty (sweep) is untouched -- a real, [NO-CANON]-
        # flagged gap (canon's own settlement bullet for this case never
        # mentions reversing the sweep's penalty), not a bug in this test.
        assert issuer.credits == issuer_after_post + 1000
        assert acceptor.credits == acceptor_after_expiry + 20
        assert contract.escrow_amount == Decimal("0")
        assert contract.escrow_state == ContractEscrowState.REFUNDING

    def test_post_to_expire_to_dispute_escalated_to_resolve_full_payout_conserves_the_sum(self) -> None:
        """WO-CONTRACT-2b-HOLD-ESCROW: the Tier-2 twin of the Tier-1
        destination_unreachable test above -- post -> accept -> deadline-
        lapse (escrow HELD) -> file_dispute (no Tier-1 case matches,
        escalates) -> resolve_dispute (FULL_PAYOUT), through the REALISTIC
        pipeline end-to-end, not hand-built fixtures. Proves the full
        chain integrates: escrow survives untouched through the entire
        48h-eligible window AND the Tier-1 miss, then `_settle_dispute_
        escrow` draws it down correctly at Tier-2 resolution."""
        issuer = _player(credits=5000)
        acceptor = _player(credits=5000)
        destination = _station(status=StationStatus.OPERATIONAL)  # no Tier-1 case matches
        db = _FakeSession(players=[issuer, acceptor], stations=[destination], resources=[_resource()])
        deadline = _NOW + timedelta(hours=2)

        contract_service.post_player_contract(
            db, issuer.id, **_post_kwargs(destination, deadline=deadline, payment=Decimal("1000")),
        )
        contract = db.added[0]
        issuer_after_post = issuer.credits  # 5000 - 1000 escrow
        contract_service.accept(db, contract.id, acceptor.id, now=_NOW)
        acceptor_after_accept = acceptor.credits  # 5000 - 20

        past_deadline = deadline + timedelta(seconds=1)
        contract_service.sweep_expired_accepted_contracts(db, now=past_deadline)
        acceptor_after_expiry = acceptor.credits  # -1000 flat penalty
        assert acceptor_after_expiry == acceptor_after_accept - 1000
        assert issuer.credits == issuer_after_post  # HELD, not refunded
        assert contract.escrow_amount == Decimal("1000.00")

        dispute_result = contract_service.file_dispute(
            db, contract.id, acceptor.id, "believed delivered on time", now=past_deadline,
        )
        assert dispute_result["tier1_resolution"] is None  # no Tier-1 case matches -> Tier-2 queue
        assert contract.status == ContractStatus.DISPUTED
        assert contract.escrow_state == ContractEscrowState.DISPUTED
        assert contract.escrow_amount == Decimal("1000.00")  # untouched by filing alone
        assert issuer.credits == issuer_after_post  # still untouched
        assert acceptor.credits == acceptor_after_expiry  # still untouched

        resolve_result = contract_service.resolve_dispute(
            db, contract.id, uuid.uuid4(), ContractDisputeResolution.FULL_PAYOUT,
            notes="proven delivered", now=past_deadline,
        )

        assert resolve_result["amount_to_acceptor"] == 1000
        assert contract.status == ContractStatus.COMPLETED
        # The FULL held escrow (1000) is drawn to the acceptor -- remainder
        # 0 -- the issuer's wallet stays exactly where it was since the
        # post-time debit (never separately touched again).
        assert issuer.credits == issuer_after_post
        assert acceptor.credits == acceptor_after_expiry + 1000
        assert contract.escrow_amount == Decimal("0")
        assert contract.escrow_state == ContractEscrowState.RELEASED


_PAST_DEADLINE = _FAR_DEADLINE + timedelta(seconds=1)


@pytest.mark.unit
class TestClaimOffsetInsuranceSafety:
    """WO-CONTRACT-1b-CLAIM-SAFETY: the rebuilt insurance CLAIM as a
    penalty-OFFSET (never a positive payout), settled through the real
    post_player_contract -> accept -> insure -> sweep_expired_accepted_
    contracts pipeline (real ORM Contract rows, real `insurance_pool_
    reserve` column -- not hand-built SimpleNamespaces) so the offset
    engine, the guarded sweep, AND the issuer-refund netting (finding #4
    of this WO's verify-first report -- refunding the issuer's FULL
    escrow_amount while the pool absorbed part of the acceptor's penalty
    is itself a mint) are all exercised together, the way a real caller
    hits them.

    Every scenario uses `payment=1000` (so `penalty == 1000`, the
    unmodified default) and HAZARD tier (15% deductible, 10% premium)
    unless noted -- `insurer_nominal = 850`, `acceptor_floor = 150`.
    `post_player_contract`/`accept`/`insure` all run at `_NOW`; every
    sweep call runs at `_PAST_DEADLINE` (one second past `_post_kwargs`'
    own default `_FAR_DEADLINE`) -- the sweep only expires a STRICTLY
    past-deadline ACCEPTED contract."""

    def _posted_accepted_insured(
        self, db: "_FakeSession", issuer: SimpleNamespace, acceptor: SimpleNamespace,
        destination: SimpleNamespace, *, pool: Decimal,
        tier: ContractInsuranceCoverageTier = ContractInsuranceCoverageTier.HAZARD,
        payment: Decimal = Decimal("1000"),
    ) -> Contract:
        contract_service.post_player_contract(
            db, issuer.id, **_post_kwargs(destination, payment=payment, insurance_pool_reserve=pool),
        )
        contract = db.added[-1]  # NOT [0] -- this helper is called twice in the two-contract test
        contract_service.accept(db, contract.id, acceptor.id, now=_NOW)
        contract_service.insure(db, contract.id, acceptor.id, tier, now=_NOW)
        return contract

    def test_no_tier_full_penalty_charged_pool_never_touched(self) -> None:
        """A funded pool with NO tier purchased is inert -- `_compute_
        claim_offset` short-circuits on `insurance_coverage_tier is None`
        before ever reading the pool. Full penalty; the FULL escrow (incl.
        the untouched reserve) is HELD at expiry (WO-CONTRACT-2b-HOLD-
        ESCROW, no longer refunded immediately) and only reaches the
        issuer once the 48h dispute window closes undisputed."""
        issuer = _player(credits=5000)
        acceptor = _player(credits=5000)
        destination = _station()
        db = _FakeSession(players=[issuer, acceptor], stations=[destination], resources=[_resource()])

        contract_service.post_player_contract(
            db, issuer.id, **_post_kwargs(destination, payment=Decimal("1000"), insurance_pool_reserve=Decimal("500")),
        )
        contract = db.added[0]
        contract_service.accept(db, contract.id, acceptor.id, now=_NOW)
        acceptor_after_accept = acceptor.credits  # 5000 - 20
        assert issuer.credits == 5000 - 1500  # escrow = payment(1000) + pool(500)

        result = contract_service.sweep_expired_accepted_contracts(db, now=_PAST_DEADLINE)

        assert result == {"expired": 1}
        assert acceptor.credits == acceptor_after_accept - 1000  # full penalty, uninsured
        assert issuer.credits == 5000 - 1500  # NOT refunded yet -- escrow stays HELD
        assert contract.escrow_state == ContractEscrowState.HELD
        assert contract.escrow_amount == Decimal("1500.00")  # untouched, pool never read
        assert contract.insurance_pool_reserve == Decimal("500")  # untouched

        past_window = _PAST_DEADLINE + timedelta(hours=48, seconds=1)
        window_result = contract_service.sweep_expired_dispute_window(db, now=past_window)

        assert window_result == {"refunded": 1}
        assert issuer.credits == 5000 - 1500 + 1500  # full escrow back, reserve untouched
        assert contract.escrow_amount == Decimal("0")
        assert contract.escrow_state == ContractEscrowState.REFUNDING

    def test_fully_funded_pool_offsets_to_exactly_the_deductible_floor(self) -> None:
        issuer = _player(credits=5000)
        acceptor = _player(credits=5000)
        destination = _station()
        db = _FakeSession(players=[issuer, acceptor], stations=[destination], resources=[_resource()])

        contract = self._posted_accepted_insured(db, issuer, acceptor, destination, pool=Decimal("850"))
        acceptor_after_insure = acceptor.credits  # 5000 - 20(fee) - 100(HAZARD premium)
        assert acceptor_after_insure == 4880
        issuer_after_post = 5000 - Decimal("1850")  # payment(1000) + pool(850)

        result = contract_service.sweep_expired_accepted_contracts(db, now=_PAST_DEADLINE)

        assert result == {"expired": 1}
        assert acceptor.credits == acceptor_after_insure - 150  # exactly the deductible floor
        assert issuer.credits == issuer_after_post  # NOT refunded yet -- escrow stays HELD
        assert contract.escrow_state == ContractEscrowState.HELD
        # WO-CONTRACT-2b-HOLD-ESCROW (R3): escrow(1850) - pool_draw(850) =
        # 1000 exactly, whole-credit, consumed at expiry so a later
        # disposition can never re-mint the drawn pool.
        assert contract.escrow_amount == Decimal("1000.00")
        assert contract.insurance_pool_reserve == Decimal("0")  # fully drained, floored at 0

        past_window = _PAST_DEADLINE + timedelta(hours=48, seconds=1)
        window_result = contract_service.sweep_expired_dispute_window(db, now=past_window)

        assert window_result == {"refunded": 1}
        assert issuer.credits == issuer_after_post + 1000  # exactly the held remainder
        assert contract.escrow_amount == Decimal("0")

    def test_partially_funded_pool_degrades_smoothly_never_negative(self) -> None:
        issuer = _player(credits=5000)
        acceptor = _player(credits=5000)
        destination = _station()
        db = _FakeSession(players=[issuer, acceptor], stations=[destination], resources=[_resource()])

        contract = self._posted_accepted_insured(db, issuer, acceptor, destination, pool=Decimal("300"))
        acceptor_after_insure = acceptor.credits
        issuer_after_post = 5000 - Decimal("1300")  # payment(1000) + pool(300)

        result = contract_service.sweep_expired_accepted_contracts(db, now=_PAST_DEADLINE)

        assert result == {"expired": 1}
        # Pool (300) < insurer_nominal (850) -- acceptor pays MORE than the
        # deductible floor (150), but strictly less than the full penalty.
        assert acceptor.credits == acceptor_after_insure - 700
        assert issuer.credits == issuer_after_post  # NOT refunded yet
        assert contract.escrow_amount == Decimal("1000.00")  # 1300 - pool_draw(300)
        assert contract.insurance_pool_reserve == Decimal("0")  # drained exactly to 0, not negative

        past_window = _PAST_DEADLINE + timedelta(hours=48, seconds=1)
        contract_service.sweep_expired_dispute_window(db, now=past_window)

        assert issuer.credits == issuer_after_post + 1000  # held remainder, once the window closes

    def test_zero_pool_insured_contract_still_pays_full_penalty(self) -> None:
        """Locks in the verify-first finding: coverage is structurally
        inert without a funded pool -- an insured acceptor with a $0 pool
        pays the SAME as an uninsured one. Not a bug; the ruled model's
        own "pool floor at 0, never negative" degrades all the way to
        zero coverage when the pool starts at zero."""
        issuer = _player(credits=5000)
        acceptor = _player(credits=5000)
        destination = _station()
        db = _FakeSession(players=[issuer, acceptor], stations=[destination], resources=[_resource()])

        contract = self._posted_accepted_insured(db, issuer, acceptor, destination, pool=Decimal("0"))
        acceptor_after_insure = acceptor.credits

        contract_service.sweep_expired_accepted_contracts(db, now=_PAST_DEADLINE)

        assert acceptor.credits == acceptor_after_insure - 1000  # full penalty
        assert contract.insurance_pool_reserve == Decimal("0")

    def test_overfunded_pool_offset_bounded_by_deductible_not_pool_balance(self) -> None:
        """Offset is bounded by BOTH the penalty (via the deductible
        floor) AND the pool -- a pool far larger than what this ONE
        claim could ever need is only drawn down by the nominal insurer
        share (850), never more; the issuer keeps the untouched surplus."""
        issuer = _player(credits=10000)
        acceptor = _player(credits=5000)
        destination = _station()
        db = _FakeSession(players=[issuer, acceptor], stations=[destination], resources=[_resource()])

        contract = self._posted_accepted_insured(db, issuer, acceptor, destination, pool=Decimal("5000"))
        acceptor_after_insure = acceptor.credits
        issuer_after_post = 10000 - Decimal("6000")  # payment(1000) + pool(5000)

        result = contract_service.sweep_expired_accepted_contracts(db, now=_PAST_DEADLINE)

        assert result == {"expired": 1}
        assert acceptor.credits == acceptor_after_insure - 150  # deductible floor, same as fully-funded
        assert issuer.credits == issuer_after_post  # NOT refunded yet -- escrow stays HELD
        # escrow(6000) - pool_draw(850) = 5150 held -- the 4150cr surplus
        # the pool never needed sits in escrow too, not drawn down early.
        assert contract.escrow_amount == Decimal("5150.00")
        assert contract.insurance_pool_reserve == Decimal("4150")  # 5000 - 850, NOT drained to 0

        past_window = _PAST_DEADLINE + timedelta(hours=48, seconds=1)
        window_result = contract_service.sweep_expired_dispute_window(db, now=past_window)

        assert window_result == {"refunded": 1}
        # The full held remainder (5150, incl. the untouched 4150 pool
        # surplus) reaches the issuer once the window closes -- nothing
        # is stranded, nothing double-counted.
        assert issuer.credits == issuer_after_post + Decimal("5150")
        assert contract.escrow_amount == Decimal("0")

    def test_half_credit_deductible_boundary_derivation_never_mints(self) -> None:
        """WO-CONTRACT-2b-HOLD-ESCROW gate revise (mack): the sharpest
        possible rounding case -- penalty=10, BASIC 5% deductible lands
        EXACTLY on `floor = 0.50`. Independently rounding BOTH the floor
        AND the nominal insurer share would mint a credit here (floor =
        round(0.50) = 1, nominal = round(9.50) = 10, sum = 11 != 10) --
        `_compute_claim_offset`'s whole-credit-early derivation (R3)
        structurally cannot hit this: the nominal share is DERIVED by
        exact integer subtraction from the already-whole penalty and
        floor, never independently rounded on its own. Exercised directly
        via `apply_claim_offset` (contract_service.py's own re-export) --
        the pure-function half of this claim; see the sibling full-
        lifecycle test below for the same boundary proven end-to-end."""
        contract = SimpleNamespace(
            insurance_coverage_tier=ContractInsuranceCoverageTier.BASIC,
            insurance_pool_reserve=Decimal("100"),  # generously funded -- bounded by the deductible, not the pool
        )

        offset = contract_service.apply_claim_offset(contract, Decimal("10"))

        assert offset["acceptor_debit"] + offset["pool_draw"] == Decimal("10")  # exact, by construction
        assert offset["acceptor_debit"] == Decimal("1")  # the .50 floor rounds UP (ROUND_HALF_UP)
        assert offset["pool_draw"] == Decimal("9")
        assert contract.insurance_pool_reserve == Decimal("91")  # 100 - 9, drained by exactly the draw

    def test_half_credit_deductible_boundary_full_lifecycle_no_mint(self) -> None:
        """The SAME .50-boundary deductible case, at a payment scale
        (110, BASIC 5% -> floor = 5.50 exactly) chosen to avoid OTHER,
        unrelated sub-credit rounding noise from `accept()`'s/`insure()`'s
        own fee math at a payment as small as 10 -- proven end-to-end
        through the real post -> accept -> insure -> expire pipeline, not
        just the pure function in isolation."""
        issuer = _player(credits=5000)
        acceptor = _player(credits=5000)
        destination = _station()
        db = _FakeSession(players=[issuer, acceptor], stations=[destination], resources=[_resource()])

        contract = self._posted_accepted_insured(
            db, issuer, acceptor, destination, pool=Decimal("200"),
            tier=ContractInsuranceCoverageTier.BASIC, payment=Decimal("110"),
        )
        acceptor_after_insure = acceptor.credits
        issuer_after_post = 5000 - Decimal("310")  # payment(110) + pool(200)
        assert issuer.credits == issuer_after_post

        result = contract_service.sweep_expired_accepted_contracts(db, now=_PAST_DEADLINE)

        assert result == {"expired": 1}
        # floor = round(110*0.05) = round(5.50) = 6 (ROUND_HALF_UP);
        # nominal = 110 - 6 = 104 (derived, never independently rounded);
        # pool_draw = min(104, 200) = 104; acceptor_debit = 110 - 104 = 6.
        # 6 + 104 == 110 exactly -- no mint at the sharpest boundary.
        assert acceptor.credits == acceptor_after_insure - 6
        assert issuer.credits == issuer_after_post  # still HELD, not refunded yet
        assert contract.escrow_amount == Decimal("206.00")  # escrow(310) - pool_draw(104)
        assert contract.insurance_pool_reserve == Decimal("96")  # 200 - 104

        past_window = _PAST_DEADLINE + timedelta(hours=48, seconds=1)
        contract_service.sweep_expired_dispute_window(db, now=past_window)
        assert issuer.credits == issuer_after_post + Decimal("206")
        assert contract.escrow_amount == Decimal("0")

    @pytest.mark.parametrize(
        "tier,pool",
        [
            (ContractInsuranceCoverageTier.BASIC, Decimal("950")),      # 5% deductible -> nominal 950
            (ContractInsuranceCoverageTier.STANDARD, Decimal("900")),   # 10% deductible -> nominal 900
            (ContractInsuranceCoverageTier.HAZARD, Decimal("850")),     # 15% deductible -> nominal 850
        ],
    )
    def test_acceptor_credit_delta_never_positive_across_every_tier(
        self, tier: ContractInsuranceCoverageTier, pool: Decimal,
    ) -> None:
        """WO's own mandatory Accept criterion: assert the acceptor's
        credit-delta is <= 0 at every step of the claim/offset path --
        the offset only ever REDUCES a debit, never adds a credit, for
        every tier, not just HAZARD."""
        issuer = _player(credits=5000)
        acceptor = _player(credits=5000)
        destination = _station()
        db = _FakeSession(players=[issuer, acceptor], stations=[destination], resources=[_resource()])

        contract_service.post_player_contract(
            db, issuer.id, **_post_kwargs(destination, payment=Decimal("1000"), insurance_pool_reserve=pool),
        )
        contract = db.added[0]
        contract_service.accept(db, contract.id, acceptor.id, now=_NOW)
        before_insure = acceptor.credits
        contract_service.insure(db, contract.id, acceptor.id, tier, now=_NOW)
        assert acceptor.credits <= before_insure  # premium is a debit too
        before_expiry = acceptor.credits

        contract_service.sweep_expired_accepted_contracts(db, now=_PAST_DEADLINE)

        assert acceptor.credits <= before_expiry  # offset never credits the acceptor

    def test_one_self_inflicted_loss_offsets_each_contract_independently_not_n_times(self) -> None:
        """The cipher-exploit regression: the SAME acceptor (and, here,
        the SAME issuer) holds TWO independently-insured contracts that
        both expire in ONE sweep pass -- a single incident must offset
        EACH contract's own failure against ITS OWN pool, summing to
        150+150=300, never a shared/duplicated/multiplied total."""
        issuer = _player(credits=10000)
        acceptor = _player(credits=5000)
        destination = _station()
        db = _FakeSession(players=[issuer, acceptor], stations=[destination], resources=[_resource()])

        contract_a = self._posted_accepted_insured(db, issuer, acceptor, destination, pool=Decimal("850"))
        contract_b = self._posted_accepted_insured(db, issuer, acceptor, destination, pool=Decimal("850"))
        acceptor_after_insure = acceptor.credits  # 2x accept fee + 2x HAZARD premium already sunk

        result = contract_service.sweep_expired_accepted_contracts(db, now=_PAST_DEADLINE)

        assert result == {"expired": 2}
        # 150 owed per contract, NOT 150 total (shared) and NOT 300x2=600.
        assert acceptor.credits == acceptor_after_insure - 150 - 150
        assert contract_a.insurance_pool_reserve == Decimal("0")
        assert contract_b.insurance_pool_reserve == Decimal("0")

    @pytest.mark.parametrize("pool", [Decimal("0"), Decimal("300"), Decimal("850"), Decimal("5000")])
    def test_full_lifecycle_conservation_holds_regardless_of_pool_depth(self, pool: Decimal) -> None:
        """The invariant underneath every scenario above, made explicit,
        now in TWO phases (WO-CONTRACT-2b-HOLD-ESCROW: escrow is HELD at
        expiry, not refunded immediately):

        Phase 1 (right after expiry, escrow HELD): `issuer + acceptor +
        contract.escrow_amount` -- INCLUDING the still-held ledger --
        equals `starting - accept_fee - premium - penalty` exactly. This
        is the invariant that matters DURING the 48h window: the money
        hasn't vanished, it's sitting in `escrow_amount` accounted for.

        Phase 2 (after the deferred-refund sweep, escrow fully disposed):
        `issuer + acceptor` ALONE now equals the SAME total -- the held
        ledger has zeroed out into the issuer's wallet. Both phases hold
        REGARDLESS of pool depth -- the pool only decides who between
        issuer and acceptor absorbs the penalty portion, never whether
        it's conserved. This is what makes finding #4's issuer-refund
        netting (into `escrow_amount`, not a direct credit) correct
        rather than an independently-plausible-looking guess."""
        issuer = _player(credits=10000)  # enough to cover the largest pool param (5000)
        acceptor = _player(credits=5000)
        destination = _station()
        db = _FakeSession(players=[issuer, acceptor], stations=[destination], resources=[_resource()])
        starting_total = issuer.credits + acceptor.credits

        contract = self._posted_accepted_insured(db, issuer, acceptor, destination, pool=pool)
        contract_service.sweep_expired_accepted_contracts(db, now=_PAST_DEADLINE)

        accept_fee = 20  # 2% of 1000
        premium = 100  # HAZARD: 10% of 1000
        penalty = 1000
        expected_remaining = starting_total - accept_fee - premium - penalty

        # Phase 1: the held escrow is part of the conserved total (whole-
        # credit by construction, R3 -- `int()` here is a lossless read,
        # not a rounding operation).
        assert (issuer.credits + acceptor.credits + int(contract.escrow_amount)) == expected_remaining

        past_window = _PAST_DEADLINE + timedelta(hours=48, seconds=1)
        contract_service.sweep_expired_dispute_window(db, now=past_window)

        # Phase 2: fully disposed -- the same total, now with nothing held.
        assert (issuer.credits + acceptor.credits) == expected_remaining
        assert contract.escrow_amount == Decimal("0")


@pytest.mark.unit
class TestDisputeWindowRace:
    """WO-CONTRACT-2b-HOLD-ESCROW money invariant (d): the 48h boundary
    race between `file_dispute` (unlocked, an ordinary API request
    transaction) and `sweep_expired_dispute_window` (CEXP-advisory-locked)
    is closed by the ROW-LEVEL atomic guard (`_guarded_file_dispute`'s own
    extra `escrow_state == 'held'` predicate, beyond `status`) -- NOT by
    the advisory lock. Neither test here holds or needs any lock at all,
    which is the point: proving the guard alone is what does the work.

    Both orderings are simulated by calling the two REAL functions with
    DIFFERENT `now` values against the SAME db/contract (not actual
    concurrent threads) -- which call's own guarded UPDATE lands FIRST is
    what decides the winner, exactly mirroring what Postgres row-level
    locking decides for two genuinely concurrent transactions."""

    def _held_contract_past_window(
        self, db: "_FakeSession", issuer: SimpleNamespace, acceptor: SimpleNamespace, destination: SimpleNamespace,
    ) -> tuple:
        deadline = _NOW + timedelta(hours=2)
        contract_service.post_player_contract(
            db, issuer.id, **_post_kwargs(destination, deadline=deadline, payment=Decimal("1000")),
        )
        contract = db.added[-1]
        contract_service.accept(db, contract.id, acceptor.id, now=_NOW)
        past_deadline = deadline + timedelta(seconds=1)
        contract_service.sweep_expired_accepted_contracts(db, now=past_deadline)
        assert contract.status == ContractStatus.EXPIRED
        assert contract.escrow_state == ContractEscrowState.HELD
        return contract, deadline

    def test_deferred_refund_wins_dispute_filed_after_rejected_cleanly(self) -> None:
        """Sweep's guarded UPDATE commits first (escrow_state: held ->
        refunding; status stays EXPIRED -- the sweep never touches it).
        The LATER dispute-filing attempt's own UPFRONT Python checks
        (status still EXPIRED, and its own `now` sits well within the 48h
        window) BOTH pass in isolation -- it is ONLY `_guarded_file_
        dispute`'s extra `escrow_state == 'held'` predicate that catches
        the race, with zero mutation from the rejected filing."""
        issuer = _player(credits=5000)
        acceptor = _player(credits=5000)
        destination = _station()
        db = _FakeSession(players=[issuer, acceptor], stations=[destination], resources=[_resource()])
        contract, deadline = self._held_contract_past_window(db, issuer, acceptor, destination)

        just_past_window = deadline + timedelta(hours=48, seconds=2)
        window_result = contract_service.sweep_expired_dispute_window(db, now=just_past_window)
        assert window_result == {"refunded": 1}
        assert contract.escrow_state == ContractEscrowState.REFUNDING
        assert contract.status == ContractStatus.EXPIRED  # sweep never touches status
        issuer_after_refund = issuer.credits

        just_inside_window = deadline + timedelta(hours=47, minutes=59)
        with pytest.raises(contract_service.ContractConflictError, match="dispute window has already closed"):
            contract_service.file_dispute(db, contract.id, acceptor.id, "too late", now=just_inside_window)

        # Zero mutation from the rejected filing -- no double-disposition.
        assert contract.status == ContractStatus.EXPIRED
        assert contract.escrow_state == ContractEscrowState.REFUNDING
        assert issuer.credits == issuer_after_refund
        assert contract.dispute_filed_at is None

    def test_dispute_filed_wins_deferred_sweep_skips_cleanly(self) -> None:
        """Dispute's guarded UPDATE commits first (status/escrow_state:
        expired/held -> disputed/disputed). The deferred sweep's OWN
        candidate SELECT (`status == EXPIRED`) no longer matches this row
        at all by the time it runs -- it finds zero candidates and
        returns cleanly, never re-touching the contract or double-
        refunding the issuer."""
        issuer = _player(credits=5000)
        acceptor = _player(credits=5000)
        destination = _station()  # OPERATIONAL by default -- no Tier-1 case matches
        db = _FakeSession(players=[issuer, acceptor], stations=[destination], resources=[_resource()])
        contract, deadline = self._held_contract_past_window(db, issuer, acceptor, destination)

        just_inside_window = deadline + timedelta(hours=47, minutes=59)
        result = contract_service.file_dispute(
            db, contract.id, acceptor.id, "filed just in time", now=just_inside_window,
        )
        assert result["tier1_resolution"] is None  # unresolved -- lands in the Tier-2 queue
        assert contract.status == ContractStatus.DISPUTED
        assert contract.escrow_state == ContractEscrowState.DISPUTED
        issuer_after_filing = issuer.credits

        just_past_window = deadline + timedelta(hours=48, seconds=2)
        window_result = contract_service.sweep_expired_dispute_window(db, now=just_past_window)

        assert window_result == {"refunded": 0}  # candidate query never matched -- status isn't EXPIRED
        assert contract.status == ContractStatus.DISPUTED  # untouched
        assert contract.escrow_state == ContractEscrowState.DISPUTED  # untouched
        assert issuer.credits == issuer_after_filing  # no double-refund


@pytest.mark.unit
class TestDisputeWindowRefundAtomicity:
    """WO-CONTRACT-2b-HOLD-ESCROW gate revise (cipher MEDIUM): a transient
    failure during the refund must NOT strand the escrow. Before the fix,
    the guard's `escrow_state -> REFUNDING` UPDATE committed on the OUTER
    transaction, separate from the `db.begin_nested()` savepoint wrapping
    the refund -- a failure inside that savepoint rolled back the refund
    and the `escrow_amount` zeroing but NOT the guard flip, leaving the
    row `escrow_state=REFUNDING` with a non-zero `escrow_amount` and no
    issuer credit: permanently stranded, since neither this sweep nor
    `_guarded_file_dispute` will ever touch a row that isn't `(expired,
    held)` again. Fixed by folding the guard into the SAME savepoint --
    proven here with `_SnapshotRestoringFakeSession` (see its own
    docstring for why the file's shared, pure-no-op `_FakeSession` can't
    prove this specific claim)."""

    def test_transient_refund_failure_leaves_row_expired_held_not_stranded(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        issuer = _player(credits=5000)
        acceptor = _player(credits=5000)
        destination = _station()
        db = _SnapshotRestoringFakeSession(
            players=[issuer, acceptor], stations=[destination], resources=[_resource()],
        )
        deadline = _NOW + timedelta(hours=2)
        contract_service.post_player_contract(
            db, issuer.id, **_post_kwargs(destination, deadline=deadline, payment=Decimal("1000")),
        )
        contract = db.added[0]
        contract_service.accept(db, contract.id, acceptor.id, now=_NOW)
        past_deadline = deadline + timedelta(seconds=1)
        contract_service.sweep_expired_accepted_contracts(db, now=past_deadline)
        assert contract.status == ContractStatus.EXPIRED
        assert contract.escrow_state == ContractEscrowState.HELD
        assert contract.escrow_amount == Decimal("1000.00")
        issuer_before = issuer.credits

        # Simulate a transient failure loading the issuer during the
        # refund (e.g. the row vanishing mid-transaction) -- defensive
        # hardening, not reachable via any hard-delete path today, same
        # precedent as TestPerRowSavepointIsolation's own missing-player
        # scenario (test_mack_attack_accepted_sweep.py).
        def _raise(*args: Any, **kwargs: Any) -> None:
            raise RuntimeError("issuer row vanished")

        monkeypatch.setattr(contract_service, "_load_player", _raise)

        past_window = past_deadline + timedelta(hours=48, seconds=2)
        result = contract_service.sweep_expired_dispute_window(db, now=past_window)

        # Must complete without raising -- caught, logged, sweep returns
        # cleanly (matches the established "poisoned row" convention).
        assert result == {"refunded": 0}
        # NOT stranded: the guard's escrow_state flip reverted TOGETHER
        # with the (never-applied) refund -- the row is exactly where it
        # was before this sweep tick, retryable on the next one.
        assert contract.status == ContractStatus.EXPIRED
        assert contract.escrow_state == ContractEscrowState.HELD
        assert contract.escrow_amount == Decimal("1000.00")
        assert issuer.credits == issuer_before

        # A subsequent, un-poisoned tick refunds it correctly -- proves
        # the retry path, not just the revert.
        monkeypatch.undo()
        result2 = contract_service.sweep_expired_dispute_window(db, now=past_window)
        assert result2 == {"refunded": 1}
        assert contract.escrow_state == ContractEscrowState.REFUNDING
        assert contract.escrow_amount == Decimal("0")
        assert issuer.credits == issuer_before + 1000


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
            # WO-CONTRACT-4-BULK: abandon() now reads `contract.contract_
            # type` unconditionally.
            contract_type=ContractType.CARGO_DELIVERY,
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
        # WO-C3-a: the issuer-pool-refund branch is gated on issuer_type ==
        # PLAYER -- an NPC-issued row (no real issuer Player row exists at
        # all here, see this test's own db.players) never enters it. No
        # crash, and the new field reports the byte-unchanged no-op.
        assert result["issuer_pool_refund"] == 0

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
