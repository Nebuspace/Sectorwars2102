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
    ran-and-found-nothing-due tick)."""
    from src.core.database import SessionLocal
    from src.services.contract_service import sweep_expired_contracts

    db = SessionLocal()
    try:
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
        result = sweep_expired_contracts(db)
        db.commit()
        return result.get("expired", 0)
    except Exception:
        logger.exception("Contract expire sweep failed")
        db.rollback()
        return 0
    finally:
        db.close()


