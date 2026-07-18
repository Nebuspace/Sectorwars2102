"""Unit tests for WO-PROG-TURN-COSTS (drone-squadron deploy turn charge).

Mock-DB (Tier A -- no real DB), pattern: tests/unit/test_drone_cap_enforcement.py's
_DeploySession, extended with a `run_sync` bridge (AsyncSession.run_sync(fn, *arg)
-> fn(sync_db, *arg)) since deploy_drone's turn-spend rail bridges
turn_service.regenerate_turns (sync-oriented -- its medal `turn_regen` bonus lookup
uses the sync ORM `.query()` API) into the fully-async DroneService.

Scope: drone-squadron deploy only (turns.md:87, 3 turns -- this WO takes it live
from the previously-shipped 0). "Scan adjacent sectors" (turns.md:83) is NOT
charged by this WO: canon itself marks it "currently free / passive map fill",
and a repo-wide grep for any explicit adjacent-scan action/endpoint came back
clean -- inventing one would be scope creep past the WO's read-first instruction.
That lane is a finding + a proposed DECISIONS contract instead (see the WO
report), not test-covered here.
"""
import types
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

import src.services.drone_service as drone_service_module
from src.models.drone import DroneStatus
from src.services.drone_service import DRONE_DEPLOY_TURN_COST, DroneService
from src.services.turn_service import spend_turns as real_spend_turns


def _result(*, scalar_one_or_none=None, scalar=None):
    r = MagicMock()
    r.scalar_one_or_none.return_value = scalar_one_or_none
    r.scalar.return_value = scalar
    return r


def _player(turns=100):
    # last_turn_regeneration anchored at "now" keeps regenerate_turns's
    # elapsed_seconds ~0 for the life of a test, so it takes the "not yet a
    # full turn" early return before ever touching the medal-bonus lookup --
    # these tests exercise the spend rail, not the regen-credit math (that is
    # turn_service's own test surface).
    now = datetime.now(timezone.utc)
    return types.SimpleNamespace(
        id=uuid.uuid4(),
        current_ship_id=uuid.uuid4(),
        turns=turns,
        max_turns=1000,
        last_turn_regeneration=now,
        created_at=now,
        aria_bonus_multiplier=1.0,
        military_rank="Recruit",
        lifetime_turns_spent=0,
    )


def _ship(upgrades=None):
    return types.SimpleNamespace(id=uuid.uuid4(), type="light_freighter", upgrades=upgrades or {})


def _drone(player_id, status=DroneStatus.IDLE.value):
    return types.SimpleNamespace(
        id=uuid.uuid4(), player_id=player_id, status=status, sector_id=None, deployed_at=None,
    )


class _DeploySession:
    """Drives DroneService.deploy_drone's call order under generous cap headroom
    (max_drones=99, deployed_count=0 by default) so every test here isolates the
    turn-cost rail, not the (separately pinned, test_drone_cap_enforcement.py)
    cap-enforcement logic.

    Order: get(Drone) -> execute(lock, full Player) -> run_sync(regenerate_turns)
    -> get(Player) -> get(Ship) -> execute(max_drones) -> execute(deployed_count)
    -> [execute(prior deployment) -> add/commit/refresh on success].
    """

    def __init__(self, *, drone, player, ship, max_drones=99, deployed_count=0,
                 prior_deployment=None, commit_side_effect=None):
        self.get = AsyncMock(side_effect=[drone, player, ship])
        self.execute = AsyncMock(side_effect=[
            _result(scalar_one_or_none=player),
            _result(scalar_one_or_none=max_drones),
            _result(scalar=deployed_count),
            _result(scalar_one_or_none=prior_deployment),
        ])
        self.run_sync = AsyncMock(side_effect=lambda fn, *a, **kw: fn(None, *a, **kw))
        self.add = MagicMock()
        self.commit = AsyncMock(side_effect=commit_side_effect)
        self.refresh = AsyncMock()
        self.rollback = AsyncMock()


# --- exact debit through the rail -------------------------------------------

@pytest.mark.asyncio
async def test_deploy_drone_debits_exactly_3_through_the_rail(monkeypatch):
    """The 3-turn charge must be visibly the turn_service rail, not an ad-hoc
    decrement: spy on drone_service's bound `spend_turns` reference (wraps the
    real function, so the genuine numeric side effect still happens) and
    assert it was called with exactly (player, DRONE_DEPLOY_TURN_COST)."""
    player = _player(turns=50)
    drone = _drone(player.id)
    ship = _ship()
    db = _DeploySession(drone=drone, player=player, ship=ship)
    spy = MagicMock(wraps=real_spend_turns)
    monkeypatch.setattr(drone_service_module, "spend_turns", spy)

    service = DroneService(db)
    deployment = await service.deploy_drone(drone_id=drone.id, sector_id=uuid.uuid4())

    spy.assert_called_once_with(player, DRONE_DEPLOY_TURN_COST)
    assert DRONE_DEPLOY_TURN_COST == 3
    assert player.turns == 47
    assert player.lifetime_turns_spent == 3
    assert deployment.drone_id == drone.id
    assert drone.status == DroneStatus.DEPLOYED.value


@pytest.mark.asyncio
async def test_deploy_drone_regens_via_the_shared_hook_not_a_reimplementation():
    """The affordability check must be preceded by the SAME regenerate_turns
    hook every other spend site calls (turn-regeneration.md invariant 6),
    bridged through run_sync -- proven by identity, not just a lack of crash."""
    player = _player(turns=50)
    drone = _drone(player.id)
    ship = _ship()
    db = _DeploySession(drone=drone, player=player, ship=ship)

    service = DroneService(db)
    await service.deploy_drone(drone_id=drone.id, sector_id=uuid.uuid4())

    db.run_sync.assert_awaited_once()
    called_fn = db.run_sync.await_args.args[0]
    assert called_fn is drone_service_module.regenerate_turns


# --- insufficient turns: clean rejection, zero side effects -----------------

@pytest.mark.asyncio
async def test_deploy_drone_insufficient_turns_rejected_cleanly():
    player = _player(turns=2)  # < DRONE_DEPLOY_TURN_COST (3)
    drone = _drone(player.id)
    ship = _ship()
    db = _DeploySession(drone=drone, player=player, ship=ship)

    service = DroneService(db)
    with pytest.raises(ValueError, match="Not enough turns"):
        await service.deploy_drone(drone_id=drone.id, sector_id=uuid.uuid4())

    # Clean rejection: no debit, no mutation, no commit/rollback attempted.
    assert player.turns == 2
    assert player.lifetime_turns_spent == 0
    assert drone.status == DroneStatus.IDLE.value
    db.add.assert_not_called()
    db.commit.assert_not_awaited()
    db.rollback.assert_not_awaited()


# --- abort-after-debit: refund -----------------------------------------------

@pytest.mark.asyncio
async def test_deploy_drone_abort_after_debit_refunds_turns():
    """A failure between spend_turns() and the final commit (here: the commit
    itself -- e.g. a dropped connection / IntegrityError) must not leave the
    player permanently charged for a deployment that never persisted."""
    player = _player(turns=50)
    drone = _drone(player.id)
    ship = _ship()
    db = _DeploySession(
        drone=drone, player=player, ship=ship,
        commit_side_effect=RuntimeError("connection dropped"),
    )

    service = DroneService(db)
    with pytest.raises(RuntimeError, match="connection dropped"):
        await service.deploy_drone(drone_id=drone.id, sector_id=uuid.uuid4())

    # Refunded in-memory BEFORE rollback (rollback expires `player` under a
    # real AsyncSession; a post-expiry attribute touch on an AsyncSession-
    # backed object needs an await, which refund_turns's plain attribute
    # mutation can't provide -- so the ordering itself is load-bearing).
    assert player.turns == 50
    assert player.lifetime_turns_spent == 0
    db.rollback.assert_awaited_once()


def test_deploy_drone_refund_precedes_rollback_in_source():
    """Static pin for the ordering the runtime test above exercises: refund
    happens textually before rollback inside deploy_drone's except block."""
    import inspect

    src = inspect.getsource(DroneService.deploy_drone)
    except_block = src[src.index("except Exception"):]
    assert except_block.index("refund_turns(") < except_block.index("self.session.rollback()")


# --- simulated concurrency safety --------------------------------------------

@pytest.mark.asyncio
async def test_deploy_drone_lock_query_requests_for_update():
    """DB-free proof the player row is actually locked: inspect the real
    SQLAlchemy Core Select passed to db.execute() for the FIRST call (the
    player lock) and assert it carries a FOR UPDATE clause -- the mechanism
    two concurrent deploys rely on to serialize instead of racing a stale
    in-memory turns balance."""
    player = _player(turns=50)
    drone = _drone(player.id)
    ship = _ship()
    captured = []
    canned = [
        _result(scalar_one_or_none=player),
        _result(scalar_one_or_none=99),
        _result(scalar=0),
        _result(scalar_one_or_none=None),
    ]

    async def _spy_execute(stmt, *a, **kw):
        captured.append(stmt)
        return canned.pop(0)

    db = _DeploySession(drone=drone, player=player, ship=ship)
    db.execute = AsyncMock(side_effect=_spy_execute)

    service = DroneService(db)
    await service.deploy_drone(drone_id=drone.id, sector_id=uuid.uuid4())

    lock_stmt = captured[0]
    assert lock_stmt._for_update_arg is not None


@pytest.mark.asyncio
async def test_deploy_drone_sequential_reuse_sees_the_prior_debit():
    """Simulated concurrency: two deploys sharing the same underlying `player`
    (as two serialized holders of the same FOR UPDATE lock would) -- the
    second call must see the FIRST call's debit, not a stale pre-debit
    balance, and correctly reject once turns run out."""
    player = _player(turns=5)  # exactly one deploy's worth (3), not two (6)
    drone_a = _drone(player.id)
    drone_b = _drone(player.id)

    db_a = _DeploySession(drone=drone_a, player=player, ship=_ship())
    await DroneService(db_a).deploy_drone(drone_id=drone_a.id, sector_id=uuid.uuid4())
    assert player.turns == 2

    db_b = _DeploySession(drone=drone_b, player=player, ship=_ship())
    with pytest.raises(ValueError, match="Not enough turns"):
        await DroneService(db_b).deploy_drone(drone_id=drone_b.id, sector_id=uuid.uuid4())
    assert player.turns == 2  # second attempt left untouched
