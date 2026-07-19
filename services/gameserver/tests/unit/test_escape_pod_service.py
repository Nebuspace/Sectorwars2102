"""WO-GWQ-STRANDING-2 lane A -- escape_pod_service.eject_to_escape_pod: the
FREE (zero fuel, zero turns, zero reputation) stranding-recovery mechanism,
at the cost of abandoning the current ship (undestroyed, left behind at its
CURRENT sector, marked is_abandoned/abandoned_at as a recoverable derelict).

DB-free: hand-built fakes, mirrors test_stranding_recovery.py's established
_FakeQuery/_FakeSession + fixture conventions (self-contained per file, not
shared via import -- matches that file's own stated precedent).

ShipService._ensure_escape_pod is monkeypatched to a canned pod rather than
exercised for real: it is PRE-EXISTING, already-shipped machinery with its
own test coverage (test_fleet_casualty_succession.py, test_combat_loot_
history_nh3b.py) -- this file's job is the NEW orchestration
(escape_pod_service.py itself), not re-proving pod creation/reuse.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

import pytest

from src.models.sector import Sector, sector_warps
from src.models.ship import ShipStatus, ShipType
from src.services import escape_pod_service
from src.services.escape_pod_service import EscapePodError

# --- shared fakes (mirrors test_stranding_recovery.py) --- #

class _FakeQuery:
    def __init__(self, first: Any = None, all: Optional[List[Any]] = None) -> None:
        self._first = first
        self._all = all if all is not None else []

    def filter(self, *a: Any, **k: Any) -> "_FakeQuery":
        return self

    def populate_existing(self) -> "_FakeQuery":
        return self

    def with_for_update(self, *a: Any, **k: Any) -> "_FakeQuery":
        return self

    def first(self) -> Any:
        return self._first

    def all(self) -> List[Any]:
        return self._all


class _FakeSession:
    def __init__(
        self, *, player: Any = None,
        sectors: Optional[List[Any]] = None, warps: Optional[List[Any]] = None,
    ) -> None:
        self._player = player
        self._sectors = sectors or []
        self._warps = warps or []
        self.flush_calls = 0

    def query(self, *entities: Any) -> _FakeQuery:
        assert entities, "query() called with no entities"
        head = entities[0]
        if head is __import__("src.models.player", fromlist=["Player"]).Player:
            return _FakeQuery(first=self._player)
        if head is Sector.id:
            return _FakeQuery(all=self._sectors)
        if head is sector_warps.c.source_sector_id:
            return _FakeQuery(all=self._warps)
        raise AssertionError(f"unexpected query for {entities!r}")

    def flush(self) -> None:
        self.flush_calls += 1

    def commit(self) -> None:
        raise AssertionError("service functions are flush-only -- the route commits")


def _fake_player(**overrides: Any) -> SimpleNamespace:
    base = dict(
        id=uuid.uuid4(),
        current_sector_id=1,
        current_region_id=uuid.uuid4(),
        current_ship=None,
        current_ship_id=None,
        is_docked=False,
        is_landed=False,
        current_port_id=None,
        current_planet_id=None,
        credits=10000,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _fake_ship(**overrides: Any) -> SimpleNamespace:
    base = dict(
        id=uuid.uuid4(),
        name="Test Freighter",
        type=ShipType.LIGHT_FREIGHTER,
        status=ShipStatus.IN_SPACE,
        is_destroyed=False,
        sector_id=1,
        is_abandoned=False,
        abandoned_at=None,
        cargo={"capacity": 100, "used": 40, "contents": {"organics": 40}},
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _sector(sector_id: int, *, name: Optional[str] = None) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(), sector_id=sector_id, region_id=uuid.uuid4(),
        name=name or f"Sector {sector_id}",
    )


def _edge(a: SimpleNamespace, b: SimpleNamespace, *, bidirectional: bool = True) -> SimpleNamespace:
    return SimpleNamespace(
        source_sector_id=a.id, destination_sector_id=b.id, is_bidirectional=bidirectional,
    )


def _install_stubs(monkeypatch: pytest.MonkeyPatch, *, pod: Any) -> Dict[str, Any]:
    """Stubs the three cross-service collaborators eject_to_escape_pod
    calls: docking_service.release, movement_service.MovementService, and
    ShipService._ensure_escape_pod (returns the canned `pod`). Records calls
    so tests can assert the arrival path actually ran."""
    calls: Dict[str, Any] = {"release": [], "presence": [], "ensure_pod": []}

    def _fake_release(db: Any, station: Any, player: Any) -> bool:
        calls["release"].append((station, player))
        return False

    class _FakeMovementService:
        def __init__(self, db: Any) -> None:
            self.db = db

        def _update_player_presence(self, player: Any, old_sector_id: int, new_sector_id: int) -> None:
            calls["presence"].append((old_sector_id, new_sector_id))

    def _fake_ensure_pod(self, player: Any, sector_id: int) -> Any:
        calls["ensure_pod"].append((player.id, sector_id))
        return pod

    monkeypatch.setattr("src.services.docking_service.release", _fake_release)
    monkeypatch.setattr("src.services.movement_service.MovementService", _FakeMovementService)
    monkeypatch.setattr(
        "src.services.ship_service.ShipService._ensure_escape_pod", _fake_ensure_pod
    )
    return calls


# --- core behavior ------------------------------------------------------- #

@pytest.mark.unit
class TestEjectToEscapePod:
    def test_free_ejection_abandons_ship_in_place_and_teleports_pod(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        origin = _sector(1)  # sink -- inbound-only edge below
        target = _sector(2)  # non-sink, hop 1
        ship = _fake_ship(sector_id=origin.sector_id)
        pod = _fake_ship(id=uuid.uuid4(), type=ShipType.ESCAPE_POD, sector_id=origin.sector_id)
        player = _fake_player(current_sector_id=origin.sector_id, current_ship=ship, turns=500, credits=777)
        db = _FakeSession(
            player=player, sectors=[origin, target],
            warps=[_edge(target, origin, bidirectional=False)],
        )
        calls = _install_stubs(monkeypatch, pod=pod)
        pinned_now = datetime(2026, 1, 1, tzinfo=timezone.utc)

        result = escape_pod_service.eject_to_escape_pod(db, player.id, now=pinned_now)

        assert result["outcome"] == "escape_pod_ejection"
        assert result["destination_sector_id"] == target.sector_id
        assert result["hops"] == 1
        assert result["fuel_spent"] == 0
        assert result["turns_spent"] == 0
        assert result["reputation_delta"] == 0

        # zero cost -- nothing about turns/credits changed
        assert player.turns == 500
        assert player.credits == 777

        # the ORIGINAL ship is abandoned in place, undestroyed, cargo intact
        assert ship.is_abandoned is True
        assert ship.abandoned_at == pinned_now
        assert ship.is_destroyed is False
        assert ship.sector_id == origin.sector_id  # stayed at the SINK, not the destination
        assert ship.cargo["contents"] == {"organics": 40}  # untouched

        # the player now pilots the pod, at the destination
        assert player.current_ship_id == pod.id
        assert player.current_sector_id == target.sector_id
        assert pod.sector_id == target.sector_id
        assert calls["ensure_pod"] == [(player.id, origin.sector_id)]
        assert calls["presence"] == [(origin.sector_id, target.sector_id)]
        assert db.flush_calls == 1  # flush-only -- route commits

    def test_already_in_a_pod_is_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        ship = _fake_ship(type=ShipType.ESCAPE_POD)
        player = _fake_player(current_ship=ship)
        db = _FakeSession(player=player)
        with pytest.raises(EscapePodError, match="already piloting"):
            escape_pod_service.eject_to_escape_pod(db, player.id)

    def test_docked_player_is_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        ship = _fake_ship()
        player = _fake_player(current_ship=ship, is_docked=True)
        db = _FakeSession(player=player)
        with pytest.raises(EscapePodError, match="docked"):
            escape_pod_service.eject_to_escape_pod(db, player.id)

    def test_landed_player_is_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        ship = _fake_ship()
        player = _fake_player(current_ship=ship, is_landed=True)
        db = _FakeSession(player=player)
        with pytest.raises(EscapePodError, match="planet surface"):
            escape_pod_service.eject_to_escape_pod(db, player.id)

    def test_harmonizing_ship_is_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        ship = _fake_ship(status=ShipStatus.HARMONIZING)
        player = _fake_player(current_ship=ship)
        db = _FakeSession(player=player)
        with pytest.raises(EscapePodError, match="harmonizing"):
            escape_pod_service.eject_to_escape_pod(db, player.id)

    def test_no_active_ship_is_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        player = _fake_player(current_ship=None)
        db = _FakeSession(player=player)
        with pytest.raises(EscapePodError, match="No active ship"):
            escape_pod_service.eject_to_escape_pod(db, player.id)

    def test_zero_hop_from_a_non_sink_sector_still_abandons_the_ship(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Not gated on actually being stranded -- matches distress beacon /
        Slipdrive's own philosophy. Firing from a well-connected sector is a
        harmless (if wasteful) self-teleport that still abandons the ship."""
        origin = _sector(1)
        other = _sector(2)
        ship = _fake_ship(sector_id=origin.sector_id)
        pod = _fake_ship(id=uuid.uuid4(), type=ShipType.ESCAPE_POD, sector_id=origin.sector_id)
        player = _fake_player(current_sector_id=origin.sector_id, current_ship=ship)
        db = _FakeSession(
            player=player, sectors=[origin, other],
            warps=[_edge(origin, other, bidirectional=False)],  # origin has its own outbound -> not a sink
        )
        calls = _install_stubs(monkeypatch, pod=pod)

        result = escape_pod_service.eject_to_escape_pod(db, player.id, now=datetime(2026, 1, 1, tzinfo=timezone.utc))

        assert result["hops"] == 0
        assert result["destination_sector_id"] == origin.sector_id
        assert ship.is_abandoned is True  # abandoned regardless
        assert player.current_sector_id == origin.sector_id  # unchanged (was already there)
        # No actual sector transition -> no presence-sync / docking-release call fired
        assert calls["presence"] == []
        assert calls["release"] == []

    def test_no_recovery_target_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Every reachable sector is ALSO a sink (a fully-disconnected
        island) -- no non-sink target exists anywhere."""
        origin = _sector(1)
        other = _sector(2)
        ship = _fake_ship(sector_id=origin.sector_id)
        player = _fake_player(current_sector_id=origin.sector_id, current_ship=ship)
        db = _FakeSession(player=player, sectors=[origin, other], warps=[])  # no edges anywhere
        with pytest.raises(EscapePodError, match="no_recovery_target"):
            escape_pod_service.eject_to_escape_pod(db, player.id)
