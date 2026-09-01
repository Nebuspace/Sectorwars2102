"""WO-BUILD-SECTOR-IS-OUTLAW-ZONE-IS-NPC-BARRACKS-SECTOR — gate reject."""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from src.services import warp_gate_service


def _player() -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        current_sector_id=1,
        turns=100,
        credits=1_000_000,
        quantum_crystals=5,
    )


def test_deploy_beacon_rejects_outlaw_zone_source(monkeypatch):
    player = _player()
    source = SimpleNamespace(
        sector_id=1,
        is_nexus_protected=False,
        is_outlaw_zone=True,
        special_features=[],
    )
    dest = SimpleNamespace(
        sector_id=2,
        is_nexus_protected=False,
        is_outlaw_zone=False,
        special_features=[],
    )
    monkeypatch.setattr(warp_gate_service, "_lock_player", lambda db, pid: player)
    monkeypatch.setattr(warp_gate_service, "_require_warp_jumper", lambda *a, **k: None)
    monkeypatch.setattr(
        warp_gate_service,
        "_sector_by_number",
        lambda db, n: source if n == 1 else dest,
    )
    with pytest.raises(warp_gate_service.WarpGateError) as exc:
        warp_gate_service.deploy_beacon(MagicMock(), player, 2)
    assert exc.value.status_code == 403
    assert "ERR_OUTLAW_ZONE" in exc.value.detail


def test_deploy_beacon_rejects_outlaw_zone_destination(monkeypatch):
    player = _player()
    source = SimpleNamespace(
        sector_id=1,
        is_nexus_protected=False,
        is_outlaw_zone=False,
        special_features=[],
    )
    dest = SimpleNamespace(
        sector_id=2,
        is_nexus_protected=False,
        is_outlaw_zone=True,
        special_features=[],
    )
    monkeypatch.setattr(warp_gate_service, "_lock_player", lambda db, pid: player)
    monkeypatch.setattr(warp_gate_service, "_require_warp_jumper", lambda *a, **k: None)
    monkeypatch.setattr(
        warp_gate_service,
        "_sector_by_number",
        lambda db, n: source if n == 1 else dest,
    )
    with pytest.raises(warp_gate_service.WarpGateError) as exc:
        warp_gate_service.deploy_beacon(MagicMock(), player, 2)
    assert exc.value.status_code == 403
    assert "ERR_OUTLAW_ZONE" in exc.value.detail
