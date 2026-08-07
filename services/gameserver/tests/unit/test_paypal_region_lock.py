"""ADR-0050 SK18 -- DB-level advisory lock on the PayPal region
provisioning/takeover critical section (paypal_service.py's
``_activate_regional_ownership``, the ONLY place a Region gets created or
has ownership transferred as a result of a PayPal webhook -- verified by
grep: ``_activate_regional_ownership`` has exactly one caller,
``_handle_subscription_activated``, both webhook-triggered; there is no
separate admin-force region-takeover path in this service).

Two layers of proof, mirroring the house convention already established by
test_message_beacon_sector_race.py / test_scheduler_lock_keys.py:

1. Call-shape proof (AsyncMock): the lock statement is issued, keyed on
   ``region_lock_key(region_name)``, BEFORE the existing-region SELECT --
   same ordering as every other region_lock_key call site (dedupe first at
   the webhook-idempotency layer in ``handle_subscription_webhook``, THEN
   serialize the mutation).
2. Real-concurrency proof: two ``asyncio`` tasks race
   ``_activate_regional_ownership`` for the SAME region_name against a
   shared in-process fake store, with an explicit ``await`` yield between
   the SELECT and the create/update decision (the exact round-trip window
   the advisory lock exists to close). Each race runs BOTH variants:
   * WITH the lock (module code unmodified) -- proves exactly one racer
     takes the create branch and the other correctly falls through to the
     ownership-transfer branch.
   * WITHOUT the lock (the pg_advisory_xact_lock statement short-circuited
     to a no-op, reproducing the pre-fix code path) -- proves the race is
     REAL: both racers independently take the create branch (duplicate
     provisioning), i.e. falsifiability -- this test would have failed
     before SK18 existed.

No live Postgres is reachable from this Mac (CLAUDE.md Execution
Environment) so ``pg_advisory_xact_lock`` itself can't be exercised; the
concurrency test instead swaps in a real ``asyncio.Lock``-backed registry
providing the identical blocking-mutual-exclusion contract, in-process --
same technique the sync-thread beacon-race test uses with
``threading.Lock``.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock

import pytest

from src.services import paypal_service as svc
from src.services.scheduler._common import region_lock_key


# --------------------------------------------------------------------------- #
# 1. Call-shape proof
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_advisory_lock_acquired_before_the_region_select_on_create():
    """New-region path: the lock statement is the FIRST session.execute
    call, keyed on region_lock_key(region_name) (no Region.id exists yet
    for a not-yet-created region)."""
    session = AsyncMock()
    select_result = AsyncMock()
    select_result.scalar_one_or_none = lambda: None  # no existing region
    session.execute.return_value = select_result

    svc_instance = SimpleNamespace()  # method body never touches self
    await svc.PayPalService._activate_regional_ownership(
        svc_instance, session, "user-1", "frontier_alpha", "sub-123",
    )

    assert session.execute.await_count == 2, "expected exactly [lock, select]"
    lock_call, select_call = session.execute.await_args_list
    lock_stmt, lock_params = lock_call.args
    assert "pg_advisory_xact_lock" in str(lock_stmt)
    assert lock_params == {"key": region_lock_key("frontier_alpha")}

    # Region actually created and added to the session.
    session.add.assert_called_once()
    created = session.add.call_args.args[0]
    assert created.name == "frontier_alpha"
    assert created.owner_id == "user-1"


@pytest.mark.asyncio
async def test_advisory_lock_acquired_before_the_region_select_on_takeover():
    """Existing-region path (takeover / re-subscription): same lock key
    derived from region_name -- the identifier stable across BOTH branches,
    so a create and a concurrent takeover for the same name always
    serialize on the identical key."""
    existing_region = SimpleNamespace(
        owner_id="old-owner", paypal_subscription_id=None, status="suspended", name="frontier_alpha",
    )
    session = AsyncMock()
    select_result = AsyncMock()
    select_result.scalar_one_or_none = lambda: existing_region
    session.execute.return_value = select_result

    svc_instance = SimpleNamespace()
    await svc.PayPalService._activate_regional_ownership(
        svc_instance, session, "new-owner", "frontier_alpha", "sub-456",
    )

    lock_call, _select_call = session.execute.await_args_list
    _lock_stmt, lock_params = lock_call.args
    assert lock_params == {"key": region_lock_key("frontier_alpha")}

    # No new region created -- existing one transferred in place.
    session.add.assert_not_called()
    assert existing_region.owner_id == "new-owner"
    assert existing_region.paypal_subscription_id == "sub-456"
    assert existing_region.status == "active"


# --------------------------------------------------------------------------- #
# 2. Real-concurrency proof
# --------------------------------------------------------------------------- #

class _AsyncAdvisoryLockRegistry:
    """Real asyncio.Lock per key -- the same blocking-mutual-exclusion
    contract pg_advisory_xact_lock provides for the real thing, in-process."""

    def __init__(self) -> None:
        self._locks: Dict[Any, asyncio.Lock] = {}

    def _get(self, key: Any) -> asyncio.Lock:
        lock = self._locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[key] = lock
        return lock

    async def acquire(self, key: Any) -> None:
        await self._get(key).acquire()

    def release(self, key: Any) -> None:
        self._get(key).release()


class _AsyncBarrier:
    """Two-waiter async barrier -- forces genuine interleaving at the
    read-decide point. asyncio is single-threaded/cooperative so no lock is
    needed around the counter itself; a short timeout on the wait means a
    racer that's genuinely blocked elsewhere (held out by the real
    advisory-lock registry) doesn't hang the test -- it just proceeds alone,
    which is the correct, expected outcome when the lock is serializing."""

    def __init__(self, n: int) -> None:
        self.n = n
        self.count = 0
        self.event = asyncio.Event()

    async def wait(self) -> None:
        self.count += 1
        if self.count >= self.n:
            self.event.set()
        try:
            await asyncio.wait_for(self.event.wait(), timeout=0.2)
        except asyncio.TimeoutError:
            pass


class _RacySession:
    """One per concurrent webhook-handler call. ``store`` is the shared
    'committed' table (a plain dict keyed by region_name) -- a race manifests
    as a duplicate ``create`` event landing in ``creates``, exactly what
    ``session.add()`` is only ever called for in the create branch of
    ``_activate_regional_ownership``.
    """

    def __init__(
        self, store: Dict[str, Any], *, use_lock: bool,
        registry: _AsyncAdvisoryLockRegistry, creates: List[Any],
        barrier: "_AsyncBarrier",
    ) -> None:
        self.store = store
        self.use_lock = use_lock
        self.registry = registry
        self.creates = creates
        self.barrier = barrier
        self._held_key: Optional[int] = None
        self._pending: Optional[Any] = None

    async def execute(self, statement: Any, params: Optional[dict] = None) -> Any:
        sql = str(statement)
        if "pg_advisory_xact_lock" in sql:
            key = params["key"]
            if self.use_lock:
                await self.registry.acquire(key)
                self._held_key = key
            else:
                # Reproduces the pre-fix code path: statement issued but no
                # actual serialization -- matches how the sector-race test's
                # `_fake_lock_sector` short-circuits to a no-op.
                await asyncio.sleep(0)
            return SimpleNamespace()

        # select(Region).where(Region.name == region_name) -- force BOTH
        # racers to reach this read-decide point before either proceeds to
        # the create/update decision (the barrier is the async analog of
        # the sync sector-race test's threading.Barrier). When the lock is
        # held, only one racer can even get this far at a time, so the
        # barrier legitimately times out for it -- proof the lock serialized
        # them, not a test bug.
        name = statement.whereclause.right.value
        region = self.store.get(name)
        await self.barrier.wait()
        return SimpleNamespace(scalar_one_or_none=lambda: region)

    def add(self, obj: Any) -> None:
        self.creates.append(obj)
        self._pending = obj

    async def commit_to_store(self) -> None:
        """Mirrors the caller (handle_subscription_webhook) committing the
        outer transaction right after _activate_regional_ownership returns
        -- this is also where the real pg_advisory_xact_lock would release."""
        if self._pending is not None:
            self.store[self._pending.name] = self._pending
        if self.use_lock and self._held_key is not None:
            self.registry.release(self._held_key)


async def _run_race(*, use_lock: bool) -> List[Any]:
    store: Dict[str, Any] = {}
    registry = _AsyncAdvisoryLockRegistry()
    creates: List[Any] = []
    barrier = _AsyncBarrier(2)

    async def _worker(user_id: str, subscription_id: str) -> None:
        session = _RacySession(
            store, use_lock=use_lock, registry=registry, creates=creates, barrier=barrier,
        )
        # Uses the real ORM-mapped Region class -- select(Region) needs the
        # actual mapped class to build the Select, and plain instantiation
        # (session.add() is never actually flushed here) doesn't touch a
        # live DB either.
        svc_instance = SimpleNamespace()
        await svc.PayPalService._activate_regional_ownership(
            svc_instance, session, user_id, "contested_region", subscription_id,
        )
        await session.commit_to_store()

    await asyncio.wait_for(
        asyncio.gather(
            _worker("user-a", "sub-a"),
            _worker("user-b", "sub-b"),
        ),
        timeout=5,
    )
    return creates


@pytest.mark.asyncio
async def test_concurrent_provisioning_serializes_with_the_advisory_lock():
    creates = await _run_race(use_lock=True)
    assert len(creates) == 1, (
        f"expected exactly ONE create (the second racer must see the "
        f"first's row and take the ownership-transfer branch instead), "
        f"got {len(creates)} creates"
    )


@pytest.mark.asyncio
async def test_concurrent_provisioning_duplicates_without_the_advisory_lock():
    """Falsifiability check: reproduces the pre-fix code path (the lock
    statement is a no-op) and proves the described race is REAL -- both
    concurrent activations see no existing region and BOTH take the create
    branch."""
    creates = await _run_race(use_lock=False)
    assert len(creates) == 2, (
        "expected the race to duplicate-provision (this is the "
        "FALSIFIABILITY check proving the test isn't vacuous) but got "
        f"{len(creates)} creates -- the race didn't manifest as expected"
    )
