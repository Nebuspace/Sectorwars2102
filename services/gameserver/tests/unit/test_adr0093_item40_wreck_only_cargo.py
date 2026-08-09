"""ADR-0093 item 40 — non-voluntary destroy_ship is wreck-only (no 10% pod rescue)."""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from src.models.ship import ShipType
from src.services import ship_service as ship_service_mod
from src.services.ship_service import ShipService


@pytest.fixture(autouse=True)
def _noop_flag_modified(monkeypatch):
    monkeypatch.setattr(ship_service_mod, "flag_modified", lambda *a, **k: None)


@pytest.mark.unit
def test_transfer_emergency_cargo_method_retired() -> None:
    assert not hasattr(ShipService, "_transfer_emergency_cargo")


@pytest.mark.unit
def test_non_voluntary_destroy_leaves_hull_cargo_intact() -> None:
    """Combat destroy must not move hold contents onto the escape pod."""
    player_id = uuid.uuid4()
    ship_id = uuid.uuid4()
    pod_id = uuid.uuid4()

    player = SimpleNamespace(id=player_id, current_ship_id=ship_id, credits=0)
    ship = SimpleNamespace(
        id=ship_id,
        name="Freighter",
        owner=player,
        owner_id=player_id,
        type=ShipType.LIGHT_FREIGHTER,
        sector_id=1,
        hangar=None,
        cargo={
            "capacity": 100,
            "used": 40,
            "contents": {"ore": 40},
        },
        is_destroyed=False,
        is_active=True,
        destruction_cause=None,
        insurance=None,
        purchase_value=1000,
        current_pilot_id=player_id,
    )
    pod = SimpleNamespace(
        id=pod_id,
        name="Emergency Escape Pod",
        type=ShipType.ESCAPE_POD,
        cargo={"capacity": 10, "used": 0, "contents": {}},
        sector_id=1,
        is_active=True,
        current_pilot_id=None,
    )

    svc = ShipService(MagicMock())
    svc._ensure_escape_pod = MagicMock(return_value=pod)  # type: ignore[method-assign]

    result = svc.destroy_ship(ship, destroyer=None, cause="combat")

    assert result is pod
    assert ship.cargo["contents"] == {"ore": 40}
    assert ship.cargo["used"] == 40
    assert pod.cargo["contents"] == {}
    assert ship.is_destroyed is True
    assert player.current_ship_id == pod_id


@pytest.mark.unit
def test_voluntary_destroy_still_transfers_full_cargo() -> None:
    """warp_gate_anchor / genesis keep _transfer_all_cargo."""
    player_id = uuid.uuid4()
    ship_id = uuid.uuid4()
    pod_id = uuid.uuid4()

    player = SimpleNamespace(id=player_id, current_ship_id=ship_id, credits=0)
    ship = SimpleNamespace(
        id=ship_id,
        name="Warp Jumper",
        owner=player,
        owner_id=player_id,
        type=ShipType.WARP_JUMPER,
        sector_id=1,
        hangar=None,
        cargo={
            "capacity": 100,
            "used": 40,
            "contents": {"ore": 40},
        },
        is_destroyed=False,
        is_active=True,
        destruction_cause=None,
        insurance=None,
        purchase_value=1000,
        current_pilot_id=player_id,
    )
    pod = SimpleNamespace(
        id=pod_id,
        name="Emergency Escape Pod",
        type=ShipType.ESCAPE_POD,
        cargo={"capacity": 10, "used": 0, "contents": {}},
        sector_id=1,
        is_active=True,
        current_pilot_id=None,
    )

    svc = ShipService(MagicMock())
    svc._ensure_escape_pod = MagicMock(return_value=pod)  # type: ignore[method-assign]

    svc.destroy_ship(ship, destroyer=None, cause="warp_gate_anchor")

    assert ship.cargo["contents"] == {}
    assert pod.cargo["contents"] == {"ore": 40}
