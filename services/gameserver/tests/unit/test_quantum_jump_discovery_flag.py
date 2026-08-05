"""WO-FIX-QUANTUM-JUMP-DISCOVERY-FLAG.

sectors.md FEATURES/galaxy/sectors.md Phase 3 step 5 ("On any successful
arrival (including misfire), mark the destination as discovered: set
sector.is_discovered = true and sector.discovered_by_id = player.id") was
spec but unbuilt -- quantum_service.jump() wired discovered_by_id (via
discovery_service.mark_sector_discovered, per the 2026-08-04 void_walker
ruling) but never touched is_discovered. Sector.is_discovered is NOT a
harmless always-true field: bang_import_service seeds it from the galaxy
payload's `explored` flag (default False), so most generated sectors start
undiscovered.

DB-free: real Ship/Player ORM instances (never added to a session, never
flushed) + a two-sector-point galaxy (origin + one candidate on-bearing
sector) so jump() takes the real resolve-to-a-candidate branch rather than
cost_surfacing's degenerate misfire-collapse-in-place branch, which never
reaches the discovery block at all. Side-effecting arrival hooks unrelated to
discovery (docking release, MovementService presence bookkeeping, contraband
transit scan, exploration medals) are monkeypatched to no-ops -- this test is
about the is_discovered/discovered_by_id flip only.
"""
from __future__ import annotations

import uuid
from typing import Any, List, Optional

import pytest

from src.models.player import Player
from src.models.sector import Sector, SectorType
from src.models.ship import Ship, ShipStatus, ShipType
from src.services.quantum_service import jump


class _FakeQuery:
    def __init__(self, first: Any = None, all_rows: Optional[List[Any]] = None, scalar: Any = None):
        self._first = first
        self._all = all_rows if all_rows is not None else []
        self._scalar = scalar

    def filter(self, *a, **kw) -> "_FakeQuery":
        return self

    def populate_existing(self) -> "_FakeQuery":
        return self

    def with_for_update(self) -> "_FakeQuery":
        return self

    def first(self) -> Any:
        return self._first

    def all(self) -> List[Any]:
        return self._all

    def scalar(self) -> Any:
        return self._scalar


class _FakeSession:
    def __init__(self, *, player, sector_points, destination_sector):
        self.player = player
        self.sector_points = sector_points
        self.destination_sector = destination_sector
        self.commits = 0

    def query(self, *entities: Any) -> _FakeQuery:
        head = entities[0]
        if head is Player:
            return _FakeQuery(first=self.player)
        if head is Ship:
            return _FakeQuery(all_rows=[])  # no tow haulers
        if getattr(head, "name", None) == "max":
            return _FakeQuery(scalar=None)  # no fleet jump cooldown
        if head is Sector:
            # discovery_service's post-resolve re-fetch of the destination
            return _FakeQuery(first=self.destination_sector)
        if getattr(head, "class_", None) is Sector:
            return _FakeQuery(all_rows=self.sector_points)
        raise AssertionError(f"unexpected query for {entities!r}")

    def refresh(self, obj: Any) -> None:
        pass

    def flush(self) -> None:
        pass

    def commit(self) -> None:
        self.commits += 1


def _make_env(*, is_discovered: bool, discovered_by_id: Any = None):
    ship = Ship(
        id=uuid.uuid4(),
        type=ShipType.WARP_JUMPER,
        is_destroyed=False,
        status=ShipStatus.IN_SPACE,
        sector_id=1,
        quantum_charges=1,
        quantum_jump_cooldown_until=None,
        quantum_scan_cooldown_until=None,
        tow_state=None,
        upgrades={},
        combat={"hull": 100, "max_hull": 100},
    )
    player = Player(
        id=uuid.uuid4(),
        turns=100,
        lifetime_turns_spent=0,
        is_docked=False,
        is_landed=False,
        current_sector_id=1,
        current_region_id=None,
        current_port_id=None,
        current_planet_id=None,
        settings={},
    )
    player.current_ship = ship

    origin = Sector(
        id=uuid.uuid4(), sector_id=1, name="Origin",
        x_coord=0.0, y_coord=0.0, z_coord=0.0,
        type=SectorType.STANDARD, region_id=None,
    )
    destination = Sector(
        id=uuid.uuid4(), sector_id=2, name="Destination",
        x_coord=10.0, y_coord=0.0, z_coord=0.0,
        type=SectorType.STANDARD, region_id=None,
    )
    destination.is_discovered = is_discovered
    destination.discovered_by_id = discovered_by_id

    db = _FakeSession(player=player, sector_points=[origin, destination], destination_sector=destination)
    return db, player, ship, destination


def _stub_side_effects(monkeypatch):
    """Neutralize arrival hooks unrelated to sector discovery."""
    import src.services.docking_service as docking_service
    import src.services.movement_service as movement_service
    import src.services.contraband_service as contraband_service
    import src.services.medal_service as medal_service

    monkeypatch.setattr(docking_service, "release", lambda db, station, player: False)
    monkeypatch.setattr(
        movement_service.MovementService, "_update_player_presence",
        lambda self, player, old_sector_id, new_sector_id: None,
    )
    monkeypatch.setattr(
        contraband_service, "scan_in_transit_best_effort",
        lambda *a, **kw: None,
    )
    monkeypatch.setattr(
        medal_service, "check_and_award_exploration_medals",
        lambda *a, **kw: None,
    )


@pytest.mark.unit
class TestQuantumJumpDiscoveryFlag:
    def test_arrival_on_undiscovered_sector_flips_both_fields(self, monkeypatch):
        _stub_side_effects(monkeypatch)
        db, player, _ship, destination = _make_env(is_discovered=False, discovered_by_id=None)

        jump(db, player.id, yaw_deg=0.0, pitch_deg=0.0, range_band="near")

        assert destination.is_discovered is True
        assert destination.discovered_by_id == player.id

    def test_arrival_on_already_discovered_sector_is_idempotent(self, monkeypatch):
        """First-wins: discovered_by_id must not be overwritten by a later
        arriving player; is_discovered stays True (unconditional flip is a
        no-op here, not a regression)."""
        _stub_side_effects(monkeypatch)
        earlier_discoverer = uuid.uuid4()
        db, player, _ship, destination = _make_env(
            is_discovered=True, discovered_by_id=earlier_discoverer,
        )

        jump(db, player.id, yaw_deg=0.0, pitch_deg=0.0, range_band="near")

        assert destination.is_discovered is True
        assert destination.discovered_by_id == earlier_discoverer
