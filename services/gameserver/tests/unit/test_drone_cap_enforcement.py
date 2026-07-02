"""Unit tests for WO-DRONE-INTEGRITY (B4 out-of-lane triage).

Mock-DB (Tier A — no real DB), mirroring tests/unit/test_region_invite_service.py:
a mock AsyncSession drives DroneService's ordered execute()/get() calls via
side_effect lists.

Findings this file pins:

1. Cap enforcement (create + deploy) was ALREADY shipped at HEAD
   (DroneService.create_drone / deploy_drone each lock the player row then
   reject an over-cap create/deploy) — these tests are a regression pin, not
   a new fix.
2. Player.attack_drones / defense_drones (the bulk combat-consumable counters,
   armory.py) and the Drone/DroneDeployment rows (individually-tracked
   deployable units, drone_service.py) are INTENTIONALLY separate systems
   per canon (sw2102-docs/FEATURES/gameplay/drones.md:9 and combat.md#drones)
   — there is no sync mechanism because there is nothing to sync: the two
   counters never read or write each other. This is documented here as the
   "derive vs write-through" answer: NEITHER — decoupled by design.
3. The dead System-B drone-combat path (CombatService._resolve_drone_combat,
   which referenced DroneDeployment.drone_count — a column that has never
   existed on the model) has been removed. It had zero callers anywhere in
   the codebase; the live sector-drone-combat path is
   CombatService._resolve_sector_drone_combat, which operates on the real
   Drone rows.
"""
import types
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.models.drone import DroneStatus, DroneType
from src.services.combat_service import CombatService
from src.services.drone_service import DroneService


def _result(*, scalar_one_or_none=None, scalar=None):
    r = MagicMock()
    r.scalar_one_or_none.return_value = scalar_one_or_none
    r.scalar.return_value = scalar
    return r


def _player(current_ship_id=None):
    return types.SimpleNamespace(id=uuid.uuid4(), current_ship_id=current_ship_id)


def _ship(upgrades=None):
    return types.SimpleNamespace(id=uuid.uuid4(), type="light_freighter", upgrades=upgrades or {})


class _CreateSession:
    """Drives DroneService.create_drone's fixed call order:
    execute (lock) -> get (Player) -> get (Ship) -> execute (max_drones)
    -> execute (live count) -> [add/commit/refresh on success].
    """

    def __init__(self, *, player, ship, max_drones, live_count):
        self.execute = AsyncMock(side_effect=[
            _result(scalar_one_or_none=player.id),          # lock check
            _result(scalar_one_or_none=max_drones),          # ShipSpecification.max_drones
            _result(scalar=live_count),                      # _count_live_drones
        ])
        self.get = AsyncMock(side_effect=[player, ship])
        self.add = MagicMock()
        self.commit = AsyncMock()
        self.refresh = AsyncMock()


@pytest.mark.asyncio
async def test_create_drone_rejects_at_cap():
    """Creation cap: current == max_drones -> the (current+1)th create is rejected."""
    player = _player(current_ship_id=uuid.uuid4())
    ship = _ship()
    db = _CreateSession(player=player, ship=ship, max_drones=2, live_count=2)
    service = DroneService(db)

    with pytest.raises(ValueError, match="Drone capacity reached"):
        await service.create_drone(player_id=player.id, drone_type=DroneType.ATTACK.value)

    db.add.assert_not_called()
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_create_drone_allows_under_cap():
    player = _player(current_ship_id=uuid.uuid4())
    ship = _ship()
    db = _CreateSession(player=player, ship=ship, max_drones=2, live_count=1)
    service = DroneService(db)

    drone = await service.create_drone(player_id=player.id, drone_type=DroneType.DEFENSE.value)

    assert drone.player_id == player.id
    db.add.assert_called_once()
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_create_drone_no_ship_caps_at_zero():
    """No active ship -> cap 0 -> any create is rejected (matches the armory
    'You need an active ship to carry armory items' posture)."""
    player = _player(current_ship_id=None)
    db = _CreateSession(player=player, ship=None, max_drones=0, live_count=0)
    # _get_max_drones returns 0 before touching Ship/ShipSpecification (no
    # active ship), so only one get() (Player) fires — but _count_live_drones
    # still runs (cap is checked as current+1 > max_drones regardless of the
    # cap value), so both execute() calls (lock, live count) still fire.
    db.get = AsyncMock(side_effect=[player])
    db.execute = AsyncMock(side_effect=[
        _result(scalar_one_or_none=player.id),  # lock check
        _result(scalar=0),                       # _count_live_drones
    ])
    service = DroneService(db)

    with pytest.raises(ValueError, match="Drone capacity reached"):
        await service.create_drone(player_id=player.id, drone_type=DroneType.SCOUT.value)


class _DeploySession:
    """Drives DroneService.deploy_drone's fixed call order:
    get (Drone) -> execute (lock) -> get (Player) -> get (Ship)
    -> execute (max_drones) -> execute (deployed count)
    -> [execute (prior deployment) -> add/commit/refresh on success].
    """

    def __init__(self, *, drone, player, ship, max_drones, deployed_count, prior_deployment=None):
        self.get = AsyncMock(side_effect=[drone, player, ship])
        self.execute = AsyncMock(side_effect=[
            _result(scalar_one_or_none=player.id),               # lock check
            _result(scalar_one_or_none=max_drones),               # ShipSpecification.max_drones
            _result(scalar=deployed_count),                       # _count_deployed_drones
            _result(scalar_one_or_none=prior_deployment),         # prior active deployment
        ])
        self.add = MagicMock()
        self.commit = AsyncMock()
        self.refresh = AsyncMock()


def _drone(player_id, status=DroneStatus.IDLE.value):
    return types.SimpleNamespace(
        id=uuid.uuid4(), player_id=player_id, status=status, sector_id=None, deployed_at=None,
    )


@pytest.mark.asyncio
async def test_deploy_drone_rejects_at_cap():
    """Deployment cap: fielded == max_drones -> deploying one more is rejected,
    even though the drone itself is IDLE (not yet occupying a field slot)."""
    player = _player(current_ship_id=uuid.uuid4())
    ship = _ship()
    drone = _drone(player.id, status=DroneStatus.IDLE.value)
    db = _DeploySession(drone=drone, player=player, ship=ship, max_drones=1, deployed_count=1)
    service = DroneService(db)

    with pytest.raises(ValueError, match="Drone deployment limit reached"):
        await service.deploy_drone(drone_id=drone.id, sector_id=uuid.uuid4())

    db.add.assert_not_called()
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_deploy_drone_allows_under_cap():
    player = _player(current_ship_id=uuid.uuid4())
    ship = _ship()
    drone = _drone(player.id, status=DroneStatus.IDLE.value)
    db = _DeploySession(drone=drone, player=player, ship=ship, max_drones=2, deployed_count=1)
    service = DroneService(db)

    deployment = await service.deploy_drone(drone_id=drone.id, sector_id=uuid.uuid4())

    assert deployment.drone_id == drone.id
    assert drone.status == DroneStatus.DEPLOYED.value
    db.add.assert_called_once()
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_deploy_drone_redeploy_excludes_self_from_cap():
    """Re-deploying an already-fielded drone is a no-op against the cap: the
    drone being deployed is excluded from its own field tally, so fielded ==
    max_drones with the redeployed drone as the sole occupant still succeeds."""
    player = _player(current_ship_id=uuid.uuid4())
    ship = _ship()
    drone = _drone(player.id, status=DroneStatus.DEPLOYED.value)
    # deployed_count as returned by _count_deployed_drones already excludes
    # this drone (exclude_drone_id=drone_id) — 0 other fielded drones, cap 1.
    db = _DeploySession(drone=drone, player=player, ship=ship, max_drones=1, deployed_count=0)
    service = DroneService(db)

    deployment = await service.deploy_drone(drone_id=drone.id, sector_id=uuid.uuid4())

    assert deployment.drone_id == drone.id
    db.add.assert_called_once()


class TestArmoryDroneCounterCapIsIndependentOfDroneTable:
    """Player.attack_drones / defense_drones (armory.py) and Drone rows
    (drone_service.py) are two separate counters by design (drones.md:9,
    combat.md#drones) — pin that DroneService never touches the Player
    counters, so there is nothing for a "sync" mechanism to reconcile."""

    def test_create_drone_does_not_touch_player_drone_counters(self):
        import inspect

        src = inspect.getsource(DroneService.create_drone)
        assert "attack_drones" not in src
        assert "defense_drones" not in src

    def test_deploy_drone_does_not_touch_player_drone_counters(self):
        import inspect

        src = inspect.getsource(DroneService.deploy_drone)
        assert "attack_drones" not in src
        assert "defense_drones" not in src


def test_dead_system_b_drone_combat_path_removed():
    """CombatService._resolve_drone_combat (legacy, referenced the
    nonexistent DroneDeployment.drone_count column, zero live callers) has
    been removed. The live sector-drone-combat resolver is
    _resolve_sector_drone_combat, called from attack_sector_drones."""
    assert not hasattr(CombatService, "_resolve_drone_combat")
    assert hasattr(CombatService, "_resolve_sector_drone_combat")
