"""WO-WIRE-PRIVATE-WARP-GATE-BUILD — BUILD_PRIVATE_WARP_GATE registration +
access_mode validation on anchor_focus.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from src.models.faction import FactionType
from src.services.emergent_reputation_service import EMERGENT_ACTIONS
from src.services import warp_gate_service


def test_build_private_warp_gate_action_registered():
    action = EMERGENT_ACTIONS["BUILD_PRIVATE_WARP_GATE"]
    deltas = {(d.faction, d.delta) for d in action.deltas}
    assert (FactionType.FEDERATION, -5) in deltas
    assert (FactionType.INDEPENDENTS, 5) in deltas
    assert (FactionType.OUTLAWS, 5) in deltas
    assert (FactionType.SYNDICATE, 10) in deltas


def test_anchor_focus_rejects_unknown_access_mode():
    db = MagicMock()
    player = SimpleNamespace(id="player-1")
    with pytest.raises(warp_gate_service.WarpGateError) as exc:
        warp_gate_service.anchor_focus(
            db, player, "00000000-0000-0000-0000-000000000001",
            access_mode="NOT_A_MODE",
        )
    assert exc.value.status_code == 400
    assert "Unknown access mode" in exc.value.detail
