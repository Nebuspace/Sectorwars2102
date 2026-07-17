"""NPC contract generation + expiry sweeps
(WO-QUALITY-techdebt-scheduler-split, WO-ECON-CONTRACT-1-KERNEL).

Own SessionLocal, commit after, close on exit; the pure, testable cores
(contract_generator.generate_npc_contracts / contract_service.sweep_
expired_contracts) live in their own service modules — these wrappers are
session-management glue only.

Moved verbatim from the old ``npc_scheduler_service.py`` — including the
Part-C decoupled generation-loop shape (3 short separate transactions) and
its F1 cancel_event orphan-guard, kept 100% intact.
"""

import logging
import threading
from datetime import datetime, UTC

from sqlalchemy import text

from src.services.scheduler._common import (
    _CONTRACT_GENERATION_STATE_KEY,
    _CONTRACT_EXPIRE_STATE_KEY,
    CONTRACT_GENERATION_SWEEP_SECONDS,
    CONTRACT_EXPIRE_SWEEP_SECONDS,
    _CONTRACT_GENERATION_LOCK_KEY,
    _CONTRACT_EXPIRE_LOCK_KEY,
    _sweep_is_due,
    _sweep_due_and_advance,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# NPC contract generation + expiry sweeps (WO-ECON-CONTRACT-1-KERNEL) — own
# SessionLocal, commit after, close on exit; mirrors _run_price_alert_sweep_
# sync's discipline exactly. The pure, testable cores
# (contract_generator.generate_npc_contracts / contract_service.sweep_
# expired_contracts) live in their own service modules — these wrappers are
# session-management glue only, not independently unit-tested. Cadence is
# now durable/wall-clock via _sweep_due_and_advance (WO-SCHED-CADENCE-DRIFT)
# — the caller (npc_scheduler_loop) invokes these EVERY iteration; the
# not-due-yet case returns cheaply without touching contract_generator /
# contract_service at all.
# ---------------------------------------------------------------------------

def _run_contract_generation_sync(cancel_event: "threading.Event | None" = None) -> int:
    """WO-SCHED-LOOP-WEDGE: 3 short, SEPARATE transactions instead of one
    session spanning the whole pass — read (gather) → pure-Python compute
    with NO open transaction at all → write (advisory-lock + durable
    due-check + INSERTs + anchor stamp, atomic in one commit).

    The orchestrator's live capture on heimdall (2026-07-10) found the
    OLD single-session shape idle-in-transaction for 28+ minutes: the read
    phase's last DB op committed nothing, then pure-Python reachability
    compute ran the whole time with the transaction still open, pinning
    the WAL/vacuum horizon for no reason (the compute touches no rows).
    This split makes "no open transaction spans the compute" a structural
    property (compute_contract_generation_batch takes no db/Session
    parameter at all), not just a discipline to remember.

    A cheap read-only peek (_sweep_is_due) skips gather+compute entirely
    when obviously not due yet. The WRITE phase's _sweep_due_and_advance
    call is the sole AUTHORITATIVE, stamping due-check — lock-gated
    (_CONTRACT_GENERATION_LOCK_KEY) so two gameserver instances can't
    double-generate, mirroring _run_suspect_clear_sweep_sync's own
    lock-then-due-check discipline. A computed batch discarded because
    another instance won the write race (or already generated since the
    peek) is wasted CPU, never a correctness issue — nothing was written.

    `cancel_event` (WO-SCHED-GEN-ORPHAN-CANCEL): this runs in a worker
    thread via `asyncio.to_thread` — cancelling the awaiting asyncio task
    (as npc_scheduler_loop's shutdown does to _contract_generation_loop)
    returns control to the event loop in ~1ms but does NOT stop this OS
    thread; a running `concurrent.futures.Future` can't be interrupted, so
    without this check the thread runs on unattended, straight into the
    write phase's advisory-lock acquisition + open write transaction, even
    after the scheduler has shut down. `cancel_event` is set from the
    async side the instant that cancellation is observed
    (_contract_generation_loop's own CancelledError handler); checked here
    at every phase boundary (after peek, after gather, after compute) so
    an orphaned thread returns 0 before ever reaching the write phase —
    never holding the lock or an open txn past shutdown. `None` (the
    default) means "never cancelled" — every other caller and existing
    test is unaffected."""
    from src.core.database import SessionLocal
    from src.services.contract_generator import (
        compute_contract_generation_batch,
        gather_contract_generation_inputs,
        write_contract_generation_batch,
    )

    now = datetime.now(UTC)

    peek_db = SessionLocal()
    try:
        if not _sweep_is_due(peek_db, _CONTRACT_GENERATION_STATE_KEY, CONTRACT_GENERATION_SWEEP_SECONDS, now):
            return 0
    except Exception:
        logger.exception("NPC contract generation peek phase failed")
        return 0
    finally:
        peek_db.close()

    if cancel_event is not None and cancel_event.is_set():
        return 0

    read_db = SessionLocal()
    try:
        inputs = gather_contract_generation_inputs(read_db)
    except Exception:
        logger.exception("NPC contract generation gather phase failed")
        return 0
    finally:
        read_db.close()

    if cancel_event is not None and cancel_event.is_set():
        return 0

    try:
        batch = compute_contract_generation_batch(inputs)
    except Exception:
        logger.exception("NPC contract generation compute phase failed")
        return 0

    if cancel_event is not None and cancel_event.is_set():
        return 0

    write_db = SessionLocal()
    try:
        got_lock = write_db.execute(
            text("SELECT pg_try_advisory_xact_lock(:key)"),
            {"key": _CONTRACT_GENERATION_LOCK_KEY},
        ).scalar()
        if not got_lock:
            logger.info("NPC scheduler: contract generation write phase — lock busy, skipped")
            return 0
        if not _sweep_due_and_advance(
            write_db, _CONTRACT_GENERATION_STATE_KEY, CONTRACT_GENERATION_SWEEP_SECONDS, now,
        ):
            return 0
        generated = write_contract_generation_batch(write_db, batch, now=now)
        write_db.commit()
        return generated
    except Exception:
        logger.exception("NPC contract generation write phase failed")
        write_db.rollback()
        return 0
    finally:
        write_db.close()


def _run_contract_expire_sweep_sync() -> int:
    """Expire due NPC contracts (and refund escrow — WO-DRIFT-econ-expired-
    escrow-refund builds on top of this lock). WO-DRIFT-econ-contract-sweep-
    advisory-lock (expire half): previously took no lock at all, unlike the
    generation sweep's own CGEN-gated write phase (a921392) — two gameserver
    instances could double-expire (and double-refund) the same contracts.
    Mirrors _run_suspect_clear_sweep_sync's lock-then-due-check ordering: the
    advisory lock is acquired FIRST, and only a successful acquirer proceeds
    to the durable due-check. On lock contention this logs and returns 0
    rather than skipping silently (WO-SWEEP-SILENT-SWEEPS discipline — a
    lock-skip must be distinguishable, in the log, from a legitimate
    ran-and-found-nothing-due tick).

    WO-DRIFT-econ-accepted-deadline-expiry: also runs sweep_expired_
    accepted_contracts (the ACCEPTED-past-deadline half — previously never
    swept at all, so an accepted contract's failure penalty was never
    applied) under this SAME CEXP lock + due-check + single commit, rather
    than a second lock/tick of its own — both are "contract expiry", just
    two different source statuses.

    WO-STORE-EXPIRY-CLAIMABLE + D19 (deposit-wins is a REQUIRED semantic,
    orchestrator-ruled, not an accepted side-effect): also runs storage_
    service.sweep_expired_lockers under this SAME CEXP lock + due-check +
    single commit, AFTER the accepted-contracts sweep -- a THIRD facet of
    "contract expiry", not a new lock/tick of its own.

    WO-CONTRACT-2b-HOLD-ESCROW: also runs contract_service.sweep_expired_
    dispute_window under this SAME CEXP lock + due-check + single commit,
    right after the accepted-contracts sweep -- a FOURTH facet of
    "contract expiry" (an EXPIRED contract's held escrow, once its 48h
    dispute window has closed undisputed, finally returns to the issuer).
    No new lock, no new cadence constant: 5-minute granularity on a 48-
    hour window is trivially fine, and folding it in here (rather than a
    separate scheduler loop) keeps the entire held-escrow design inside
    this lane -- zero `core_loop.py` touch. Order relative to the locker
    sweep doesn't matter for correctness (a contract can't be both
    "just expired this tick" and ">48h past its own deadline" in the same
    tick -- if it were that overdue it would already have expired on an
    earlier 5-minute tick), placed here to keep the two payment/escrow
    sweeps adjacent.

    THE DEADLOCK + THE DEPOSIT-WINS REQUIREMENT, SOLVED BY ONE MECHANISM:
    an earlier version of this fix split contract-expiry and locker-
    conversion into two SEPARATE transactions (deadlock-free by
    construction, since the second never held a Contract lock) -- but
    that gives an ORDINARY first-committer-wins race for a completing
    deposit landing at the deadline, not the DETERMINISTIC deposit-wins
    D19 requires. The single-transaction design below fixes BOTH at
    once: sweep_expired_accepted_contracts (contract_service.py) now
    takes an `expiry_gate` callback (storage_service.gate_contract_
    expiry_on_locker) that, for a storage-linked contract, probes its
    Locker's row lock SKIP LOCKED *before* expiring the Contract. A
    contended Locker (a live completing deposit_cargo call already holds
    it) means the gate DEFERS that one contract's expiry this tick --
    the in-flight deposit finishes uncontested and completes the
    contract (deposit-wins), and the sweep picks the contract up on a
    LATER tick if it's still overdue then. An uncontended Locker means
    the gate ACQUIRES it (Locker-then-Contract order, matching deposit_
    cargo's own order exactly) before the contract's guarded UPDATE runs
    -- consistent ordering across the WHOLE codebase kills the AB-BA
    cycle structurally (grepped: storage_service.py and this file are
    the ONLY two places anywhere in src/ that ever touch both a Contract
    lock and a Locker lock in the same transaction; storage_service.py
    already always locks Locker first). See contract_service.sweep_
    expired_accepted_contracts's own docstring for the gate contract and
    the infinite-loop fix its `.all()`-based rewrite required, and
    storage_service.gate_contract_expiry_on_locker's own docstring for
    the two-step existence-then-skip_locked-probe.

    The return value stays contracts-expired-count-only (posted +
    accepted, unchanged shape for any existing caller); lockers-
    converted is reported separately via its own log line, not folded
    into the returned int.

    WO-CONTRACT-57 (axis-2): the 3 contract-expiry sweeps below used to be
    3 separate calls, each running its own candidates through its own
    isolated per-candidate loop -- inside this SAME shared transaction,
    that left the tick's overall Player-lock acquisition sequence in
    plain candidate-query order, unrelated to player_id, a Player-vs-
    Player AB-BA risk against any concurrent API call (contract_service.
    run_contract_expiry_sweeps' own docstring has the full theorem). The
    single call below replaces all 3 -- it internally gathers all 3
    sweeps' candidates, applies `expiry_gate` first (unchanged), and
    visits every candidate in one globally player_id-ascending merged
    order, closing that cycle. Returns the SAME 3 result dicts the 3
    separate calls used to, unpacked exactly as before.

    ADDENDUM (hub-required safety net): the merged dispatch's per-
    candidate Player locks are still BLOCKING (`_load_player(...,
    for_update=True)`, unchanged) -- the ascending order makes a
    deadlock impossible, but a hung/long-lived CONCURRENT API
    transaction merely holding a contended Player row could otherwise
    block this whole tick indefinitely (the same failure mode `move_npc`'s
    own comment describes for Loop A). `SET LOCAL lock_timeout` below
    bounds every blocking lock the WHOLE transaction takes -- txn-scoped,
    so it covers every per-candidate lock `run_contract_expiry_sweeps`
    acquires, not just one statement -- the `SET LOCAL lock_timeout`
    MECHANISM ITSELF, and the 3s VALUE, match the identical convention
    already established at every other blocking-lock call site in this
    codebase (planets.py / movement_service.py / npc_movement_service.py
    / planetary_service.py / intrasystem_movement_service.py all use 3s
    or 5s; 3s -- the more common of the two -- is used here). The PER-
    CANDIDATE `.orig.pgcode == '55P03'` DISCRIMINATION on the resulting
    `OperationalError` (below), by contrast, is NOVEL to this codebase --
    no other blocking-lock call site inspects the failure's SQLSTATE at
    all (they all just fail the whole request/statement); this is the
    first site needing to tell "transient contention, safe to defer"
    apart from "genuine deadlock or other failure, must surface" inside
    a single already-open, multi-candidate transaction. Caught PER-
    CANDIDATE inside `run_contract_expiry_sweeps`' own per-candidate
    functions -- see `contract_service._is_lock_timeout`'s own docstring
    for the precise discrimination (only a genuine 55P03 defers; a real
    deadlock or any other OperationalError is NOT silently deferred).
    The defensive try/except mirrors every sibling call site -- `SET
    LOCAL` failing (e.g. a DB-free fake session in a unit test, which
    never exercises this real-SessionLocal path at all, or an
    unexpected driver quirk) must never crash the sweep."""
    from src.core.database import SessionLocal
    from src.services import contract_service
    from src.services.storage_service import gate_contract_expiry_on_locker, sweep_expired_lockers

    db = SessionLocal()
    try:
        try:
            db.execute(text("SET LOCAL lock_timeout = '3s'"))
        except Exception:
            logger.debug(
                "_run_contract_expire_sweep_sync: could not set lock_timeout", exc_info=True,
            )

        got_lock = db.execute(
            text("SELECT pg_try_advisory_xact_lock(:key)"),
            {"key": _CONTRACT_EXPIRE_LOCK_KEY},
        ).scalar()
        if not got_lock:
            logger.info("NPC scheduler: contract expire sweep — lock busy, skipped")
            return 0
        if not _sweep_due_and_advance(
            db, _CONTRACT_EXPIRE_STATE_KEY, CONTRACT_EXPIRE_SWEEP_SECONDS, datetime.now(UTC),
        ):
            return 0
        posted_result, accepted_result, dispute_window_result = contract_service.run_contract_expiry_sweeps(
            db, expiry_gate=gate_contract_expiry_on_locker,
        )
        if dispute_window_result.get("refunded", 0):
            logger.info(
                "NPC scheduler: %d contract(s) refunded past their undisputed "
                "dispute window", dispute_window_result["refunded"],
            )
        locker_result = sweep_expired_lockers(db)
        if locker_result.get("converted", 0):
            logger.info(
                "NPC scheduler: %d locker(s) converted to CLAIMABLE storage", locker_result["converted"],
            )
        db.commit()
        return posted_result.get("expired", 0) + accepted_result.get("expired", 0)
    except Exception:
        logger.exception("Contract expire sweep failed")
        db.rollback()
        return 0
    finally:
        db.close()


