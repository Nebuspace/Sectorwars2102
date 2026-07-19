"""mack -- behavioral gate on WO-CONTRACT-3b-BULK (REVISE): real 2-session
SQLAlchemy+SQLite repro + fix-proof for the self-loop lost-update in
`contract_bulk.deliver()`'s intermediate-partial edge.

THE BUG (mack CRITICAL, money-path gate): `deliver()`'s "another partial,
still not complete" transition is IN_PROGRESS -> IN_PROGRESS -- a SELF-
LOOP. `_guarded_transition`'s status-only `WHERE status = :from_status`
does NOT change status on a self-loop, so it silently stops being a lock
for that one edge: two concurrent `deliver()` calls both reading the SAME
stale `partial_fulfilled_amount` snapshot would BOTH pass that WHERE
clause, and the second writer's UPDATE overwrites the first's contribution
-- a genuine lost update, reachable via two overlapping `/deliver` requests
from the same player (retry after timeout / double-click), no exploit
needed.

THE FIX: `contract_bulk._guarded_deliver` folds `Contract.partial_
fulfilled_amount == expected_partial_fulfilled_amount` (the exact value
read at `deliver()`'s call start) into the SAME WHERE clause as the status
check, uniformly for every deliver() transition -- a genuine compare-and-
swap. See that function's own docstring for the full reasoning.

Real-SQLAlchemy convention (mirrors test_money_nolock_rmw_mack.py's own
precedent exactly): SQLite (StaticPool + check_same_thread=False, two
independent Session() objects sharing one in-memory DB) so a second
session's commit is a genuine concurrent write -- NOT the hand-rolled
_FakeSession pattern (test_contract_service.py's own `_FakeSession`/
`_FakeQuery`), which has no identity map and executes everything against
a single shared Python list, so it structurally cannot model "session A
committed, session B's own snapshot is now stale" the way two independent
SQLAlchemy Sessions against a real (if in-memory) engine can.

Tests `_guarded_deliver` DIRECTLY (a Contract-only mirror table, no
Player/Ship/cargo scaffolding) rather than the full `deliver()` call chain
-- the lost-update bug and its fix are BOTH entirely about the Contract
row's own compare-and-swap; the payout/cargo/quota math around it already
has its own dedicated, thorough FakeSession coverage in test_contract_
service.py's TestDeliver (unaffected by this fix -- see that file's own
docstrings). Matches this codebase's own established "minimal generalizing
reproduction over the full call chain" precedent (test_money_nolock_rmw_
mack.py's own docstring, itself citing test_storage_deposit_prelock_
identity_map.py).

Live-Postgres two-CONNECTION verification (as opposed to this same-
process two-SESSION SQLite proof) is owed at the deploy window -- SQLite's
`with_for_update()` is a documented no-op irrelevant here anyway, since
this fix is about the WHERE-clause CAS predicate, not row-lock
acquisition (same framing as every other genuine-contention property in
this codebase's own `_mack` gate files)."""
from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import declarative_base, sessionmaker
from sqlalchemy.pool import StaticPool

from src.models.contract import ContractIssuerType, ContractStatus, ContractType
from src.services import contract_bulk, contract_escrow_core
from src.services.contract_escrow_core import ContractConflictError


def _schema():
    Base = declarative_base()

    class MirrorContract(Base):
        __tablename__ = "mirror_contracts_bulk_cas"
        id = sa.Column(sa.String, primary_key=True)
        status = sa.Column(sa.Enum(ContractStatus))
        partial_fulfilled_amount = sa.Column(sa.Integer, nullable=True)
        partial_fulfilled_payout = sa.Column(sa.Numeric(19, 2))
        completed_at = sa.Column(sa.DateTime, nullable=True)

    return Base, MirrorContract


def _session_factory(base) -> sessionmaker:
    engine = sa.create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False)


def _seed(SessionFactory, MirrorContract, contract_id: str) -> None:
    """Contract at 4/10 delivered, 400/1000cr paid so far -- mack's own
    repro numbers (contract 4/10, A commits to 7/10, B's stale 4/10
    snapshot would overwrite to 6/10, losing A's +3/+300)."""
    seed = SessionFactory()
    seed.add(MirrorContract(
        id=contract_id, status=ContractStatus.IN_PROGRESS,
        partial_fulfilled_amount=4, partial_fulfilled_payout=Decimal("400.00"),
    ))
    seed.commit()
    seed.close()


class TestGuardedDeliverConcurrentPartialLostUpdate:
    def test_two_concurrent_intermediate_partials_one_wins_one_conflicts_no_lost_update(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """THE FIX, proven: two independent sessions both read the SAME
        stale 4/10 snapshot before either commits. Caller A's compare-and-
        swap (expected=4) commits first, landing at 7/10 -- 700cr. Caller
        B's own call, STILL carrying its stale expected=4, must now fail
        (the real row has already moved to 7) rather than silently
        overwrite A's contribution. Final state reflects ONLY A's
        delivery -- exactly the invariant a lost update would violate."""
        Base, MirrorContract = _schema()
        SessionFactory = _session_factory(Base)
        contract_id = str(uuid.uuid4())
        _seed(SessionFactory, MirrorContract, contract_id)

        monkeypatch.setattr(contract_bulk, "Contract", MirrorContract)

        session_a = SessionFactory()
        session_b = SessionFactory()

        # Both sessions read the SAME stale snapshot (already_delivered=4)
        # before either commits -- the exact race window `deliver()` opens
        # between its own unlocked _load_contract read and its eventual
        # _guarded_deliver call.
        row_a = session_a.query(MirrorContract).filter(MirrorContract.id == contract_id).first()
        row_b = session_b.query(MirrorContract).filter(MirrorContract.id == contract_id).first()
        assert row_a.partial_fulfilled_amount == 4
        assert row_b.partial_fulfilled_amount == 4

        # Caller A commits first -- a genuine partial delivery, +3 units / +300cr.
        contract_bulk._guarded_deliver(
            session_a, row_a, ContractStatus.IN_PROGRESS, ContractStatus.IN_PROGRESS,
            4, partial_fulfilled_amount=7, partial_fulfilled_payout=Decimal("700.00"),
        )
        session_a.commit()

        # Caller B's own attempt (+2 units / +200cr) still carries its
        # stale expected=4 -- the row has already moved to 7 underneath it.
        with pytest.raises(ContractConflictError, match="concurrent_delivery"):
            contract_bulk._guarded_deliver(
                session_b, row_b, ContractStatus.IN_PROGRESS, ContractStatus.IN_PROGRESS,
                4, partial_fulfilled_amount=6, partial_fulfilled_payout=Decimal("600.00"),
            )
        session_b.rollback()

        verify = SessionFactory()
        final = verify.query(MirrorContract).filter(MirrorContract.id == contract_id).first()
        assert final.partial_fulfilled_amount == 7, (
            f"expected ONLY caller A's delivery to have landed (7) -- got "
            f"{final.partial_fulfilled_amount}. A value of 6 here would mean "
            f"caller B's stale overwrite won, silently losing A's +3 units "
            f"-- the exact lost update this WO's compare-and-swap fix closes."
        )
        assert final.partial_fulfilled_payout == Decimal("700.00")
        verify.close()
        session_a.close()
        session_b.close()

    def test_first_partial_from_accepted_also_gets_the_cas_uniformly(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The FIRST partial (ACCEPTED -> IN_PROGRESS, a real status
        CHANGE, not a self-loop) is already serialized by the status
        check alone -- but _guarded_deliver applies the SAME CAS predicate
        uniformly rather than special-casing the self-loop, per its own
        docstring ("one code path, no self-loop-only special case").
        Confirms that uniform application doesn't itself break the
        already-safe ACCEPTED-origin edge: a fresh contract (partial_
        fulfilled_amount=None, matching the column's real nullable-no-
        default shape) accepts its first delivery correctly."""
        Base, MirrorContract = _schema()
        SessionFactory = _session_factory(Base)
        contract_id = str(uuid.uuid4())

        seed = SessionFactory()
        seed.add(MirrorContract(
            id=contract_id, status=ContractStatus.ACCEPTED,
            partial_fulfilled_amount=None, partial_fulfilled_payout=Decimal("0"),
        ))
        seed.commit()
        seed.close()

        monkeypatch.setattr(contract_bulk, "Contract", MirrorContract)

        session = SessionFactory()
        row = session.query(MirrorContract).filter(MirrorContract.id == contract_id).first()
        assert row.partial_fulfilled_amount is None

        contract_bulk._guarded_deliver(
            session, row, ContractStatus.ACCEPTED, ContractStatus.IN_PROGRESS,
            None, partial_fulfilled_amount=3, partial_fulfilled_payout=Decimal("300.00"),
        )
        session.commit()

        verify = SessionFactory()
        final = verify.query(MirrorContract).filter(MirrorContract.id == contract_id).first()
        assert final.status == ContractStatus.IN_PROGRESS
        assert final.partial_fulfilled_amount == 3
        verify.close()
        session.close()

    def test_inverted_plain_status_only_where_loses_the_update(self) -> None:
        """Non-vacuous companion: the IDENTICAL race, but using the PRE-
        FIX WHERE shape (status-only -- i.e. exactly what `_guarded_
        transition` itself would do for a self-loop, with no partial_
        fulfilled_amount predicate at all). Proves the harness can
        actually detect the regression this WO's fix prevents, and that
        the loss is real -- not a fixture artifact of this test file."""
        Base, MirrorContract = _schema()
        SessionFactory = _session_factory(Base)
        contract_id = str(uuid.uuid4())
        _seed(SessionFactory, MirrorContract, contract_id)

        session_a = SessionFactory()
        session_b = SessionFactory()
        row_a = session_a.query(MirrorContract).filter(MirrorContract.id == contract_id).first()
        row_b = session_b.query(MirrorContract).filter(MirrorContract.id == contract_id).first()

        def _plain_status_only_update(session, row, values):
            stmt = (
                sa.update(MirrorContract)
                .where(MirrorContract.id == row.id, MirrorContract.status == ContractStatus.IN_PROGRESS)
                .values(**values)
            )
            result = session.execute(stmt)
            assert result.rowcount == 1  # BOTH calls "succeed" here -- that IS the bug

        _plain_status_only_update(
            session_a, row_a, {"partial_fulfilled_amount": 7, "partial_fulfilled_payout": Decimal("700.00")},
        )
        session_a.commit()
        _plain_status_only_update(
            session_b, row_b, {"partial_fulfilled_amount": 6, "partial_fulfilled_payout": Decimal("600.00")},
        )
        session_b.commit()  # B's stale overwrite silently WINS -- A's +3/+300 is lost

        verify = SessionFactory()
        final = verify.query(MirrorContract).filter(MirrorContract.id == contract_id).first()
        assert final.partial_fulfilled_amount == 6, (
            "harness sanity check failed -- the pre-fix status-only WHERE "
            "must genuinely lose caller A's update here (a 7 would mean "
            "this harness can't actually reproduce the race at all, and "
            "the fix-proof test above would be vacuous)"
        )
        verify.close()
        session_a.close()
        session_b.close()


# =============================================================================
# cipher addendum (WO-3b money-path re-gate): the SAME lost-update is also a
# real MINT -- payment=1000, racing partials corrupt the counter DOWNWARD
# (a winner's own contribution silently overwritten), so the eventual
# completing "exact remainder" delivery pays out MORE than the true
# remaining balance (cipher's own numbers: counter corrupted to 250 while
# 350 was really paid, completing delivery pays 750 -> 1100 total, 100cr
# minted). The fix must ALSO leave a REJECTED racer's cargo/credits
# completely untouched -- no partial mutation lingering from a call that
# ultimately failed (a clean no-op + retryable error, never a "half-
# applied" side effect requiring an explicit undo). Runs the REAL
# contract_bulk.deliver() end to end (not just _guarded_deliver in
# isolation) against a fuller mirror schema (Contract + Player + Ship),
# proving deliver()'s own internal ordering -- the guarded UPDATE strictly
# BEFORE the cargo/credit mutation lines -- holds under a genuine two-
# session race, not just by static code reading.
# =============================================================================

def _full_schema():
    Base = declarative_base()

    class MirrorShip(Base):
        __tablename__ = "mirror_ships_bulk_e2e"
        id = sa.Column(sa.String, primary_key=True)
        cargo = sa.Column(sa.JSON)

    class MirrorPlayer(Base):
        __tablename__ = "mirror_players_bulk_e2e"
        id = sa.Column(sa.String, primary_key=True)
        credits = sa.Column(sa.Integer)
        is_docked = sa.Column(sa.Boolean)
        current_port_id = sa.Column(sa.String, nullable=True)
        current_ship_id = sa.Column(sa.String, sa.ForeignKey("mirror_ships_bulk_e2e.id"), nullable=True)
        current_ship = sa.orm.relationship("MirrorShip", foreign_keys=[current_ship_id])

    class MirrorContract(Base):
        __tablename__ = "mirror_contracts_bulk_e2e"
        id = sa.Column(sa.String, primary_key=True)
        contract_type = sa.Column(sa.Enum(ContractType))
        issuer_type = sa.Column(sa.Enum(ContractIssuerType))
        status = sa.Column(sa.Enum(ContractStatus))
        acceptor_player_id = sa.Column(sa.String, nullable=True)
        destination_station_id = sa.Column(sa.String, nullable=True)
        commodity_type = sa.Column(sa.String, nullable=True)
        quantity = sa.Column(sa.Integer, nullable=True)
        payment = sa.Column(sa.Numeric(19, 2))
        partial_fulfilled_amount = sa.Column(sa.Integer, nullable=True)
        partial_fulfilled_payout = sa.Column(sa.Numeric(19, 2))
        escrow_state = sa.Column(sa.String, nullable=True)
        completed_at = sa.Column(sa.DateTime, nullable=True)

    return Base, MirrorContract, MirrorPlayer, MirrorShip


class TestDeliverEndToEndRejectedRacerLosesNothing:
    """cipher addendum: the CAS-rejected racer must have ZERO side effects
    -- no cargo decrement, no credit payout, no counter change -- and the
    WINNER must never be shorted or over-paid. Runs the REAL contract_
    bulk.deliver() end to end."""

    def test_two_racing_deliveries_winner_correct_loser_untouched(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        Base, MirrorContract, MirrorPlayer, MirrorShip = _full_schema()
        SessionFactory = _session_factory(Base)
        contract_id = str(uuid.uuid4())
        player_id = str(uuid.uuid4())
        ship_id = str(uuid.uuid4())
        station_id = str(uuid.uuid4())

        seed = SessionFactory()
        seed.add(MirrorShip(id=ship_id, cargo={"capacity": 5000, "used": 500, "contents": {"ore": 500}}))
        seed.add(MirrorPlayer(
            id=player_id, credits=1000, is_docked=True, current_port_id=station_id, current_ship_id=ship_id,
        ))
        seed.add(MirrorContract(
            id=contract_id, contract_type=ContractType.BULK_PROCUREMENT,
            issuer_type=ContractIssuerType.NPC, status=ContractStatus.IN_PROGRESS,
            acceptor_player_id=player_id, destination_station_id=station_id,
            commodity_type="ore", quantity=100, payment=Decimal("1000.00"),
            partial_fulfilled_amount=40, partial_fulfilled_payout=Decimal("400.00"),
        ))
        seed.commit()
        seed.close()

        monkeypatch.setattr(contract_bulk, "Contract", MirrorContract)
        monkeypatch.setattr(contract_escrow_core, "Contract", MirrorContract)
        monkeypatch.setattr(contract_escrow_core, "Player", MirrorPlayer)

        session_a = SessionFactory()
        session_b = SessionFactory()

        # Pre-warm BOTH sessions' identity maps with the SAME stale
        # Contract snapshot (40/100) BEFORE either commits -- a bare
        # `.first()` on an already-identity-mapped object does NOT
        # refresh it from a later SELECT (the same staleness precondition
        # test_money_nolock_rmw_mack.py's own tests rely on; `_load_
        # contract`, contract_escrow_core.py, is exactly this kind of
        # bare, non-`populate_existing()` read). This is what lets B's
        # LATER deliver() call (issued after A has already committed)
        # still operate against the ORIGINAL 40/100 snapshot -- the
        # genuine race window, not a same-thread sequencing artifact.
        preload_a = session_a.query(MirrorContract).filter(MirrorContract.id == contract_id).first()
        preload_b = session_b.query(MirrorContract).filter(MirrorContract.id == contract_id).first()
        assert preload_a.partial_fulfilled_amount == 40
        assert preload_b.partial_fulfilled_amount == 40

        # Caller A: delivers 20 units, commits cleanly. Plain STRING ids --
        # MirrorContract/MirrorPlayer.id are sa.String columns (seeded as
        # str(uuid4()) above); deliver()'s own type hint says uuid.UUID,
        # but Python doesn't enforce that at runtime, and passing a real
        # UUID object here would silently fail to match the String-typed
        # column in `_load_contract`'s `Contract.id == contract_id` filter.
        result_a = contract_bulk.deliver(session_a, contract_id, player_id, 20, now=None)
        session_a.commit()

        # Caller B: still holds the STALE 40/100 snapshot -- its own
        # attempt (15 units) must be rejected outright.
        with pytest.raises(ContractConflictError, match="concurrent_delivery"):
            contract_bulk.deliver(session_b, contract_id, player_id, 15, now=None)
        session_b.rollback()

        # WINNER (A): correct pro-rata payout, cargo genuinely decremented.
        assert result_a["payout_this_delivery"] == 200  # 1000 x 20/100
        assert result_a["partial_fulfilled_amount"] == 60

        # Re-read the TRUE state from a fresh session -- must reflect
        # ONLY A's delivery, never any trace of B's rejected attempt.
        verify = SessionFactory()
        final_contract = verify.query(MirrorContract).filter(MirrorContract.id == contract_id).first()
        assert final_contract.partial_fulfilled_amount == 60, (
            f"expected ONLY A's delivery (60) -- got {final_contract.partial_fulfilled_amount}. "
            f"55 (40+15) would mean B's rejected attempt still mutated the counter."
        )
        assert final_contract.partial_fulfilled_payout == Decimal("600.00"), (
            "expected 400 (seed) + 200 (A only) -- a value including B's own "
            "150 would mean the rejected racer still minted a payout"
        )
        final_player = verify.query(MirrorPlayer).filter(MirrorPlayer.id == player_id).first()
        assert final_player.credits == 1000 + 200, (
            f"expected 1000 + 200 (A's payout only) -- got {final_player.credits}. "
            f"A value including B's own payout would mean a rejected racer still "
            f"got paid -- exactly the mint cipher's own repro demonstrated."
        )
        final_ship = verify.query(MirrorShip).filter(MirrorShip.id == ship_id).first()
        assert final_ship.cargo["contents"]["ore"] == 480, (
            f"expected 500 - 20 (A's delivery only) = 480 -- got "
            f"{final_ship.cargo['contents']['ore']}. 465 (500-20-15) would mean "
            f"the rejected racer's cargo decrement still landed -- deliver()'s "
            f"own ordering (guarded UPDATE strictly BEFORE the cargo mutation "
            f"lines) is what prevents this: the ContractConflictError raised "
            f"from _guarded_deliver propagates out of B's deliver() call BEFORE "
            f"it ever reaches the cargo-decrement code, so B's session never "
            f"even attempted to write cargo -- nothing to roll back, a true no-op."
        )
        verify.close()
        session_a.close()
        session_b.close()
