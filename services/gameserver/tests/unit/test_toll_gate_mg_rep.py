"""LEG-3376 — MG emergent rep for public toll-gate traversal (cargo / 5k).

Canon: factions-and-teams.md MG table — "Use a player toll gate (someone
else's public gate, with cargo) | +1 / 5,000 cr cargo value".
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from src.models.player import Player
from src.services import warp_gate_service
from tests.unit.test_warp_gate_toll import (
    _FakeQuery,
    _FakeSession,
    _fake_player,
    _fake_tunnel,
)


def _fake_ship_with_ore(units: int) -> SimpleNamespace:
    return SimpleNamespace(
        cargo={
            "capacity": 10_000,
            "used": units,
            "contents": {"ore": units},
        }
    )


@pytest.mark.unit
class TestTollGateMgRep:
    def test_public_gate_paid_toll_awards_mg_rep_from_cargo(self) -> None:
        owner = _fake_player(credits=1_000)
        traverser = _fake_player(credits=5_000)
        tunnel = _fake_tunnel(
            created_by_player_id=owner.id,
            access_requirements={"toll_amount": 300},
            is_public=True,
        )
        ship = _fake_ship_with_ore(400)  # 400 × 15 cr = 6,000 cargo value
        db = _FakeSession({Player: _FakeQuery(first=owner)})

        with patch(
            "src.services.emergent_reputation_service.apply_trade_volume_rep"
        ) as mock_rep:
            result = warp_gate_service.collect_toll(
                db, traverser, tunnel, actor_ship=ship
            )

        assert result["charged"] == 300
        mock_rep.assert_called_once()
        args, kwargs = mock_rep.call_args
        assert args[1] is traverser
        assert args[2] == "TRADE_VOLUME_MG"
        assert args[3] == 6_000
        assert kwargs == {} or "context" in (mock_rep.call_args.kwargs or {})

    def test_owner_traversal_skips_mg_rep(self) -> None:
        owner = _fake_player(credits=1_000)
        tunnel = _fake_tunnel(
            created_by_player_id=owner.id,
            access_requirements={"toll_amount": 300},
            is_public=True,
        )
        ship = _fake_ship_with_ore(400)
        db = _FakeSession({Player: _FakeQuery(first=owner)})

        with patch(
            "src.services.emergent_reputation_service.apply_trade_volume_rep"
        ) as mock_rep:
            warp_gate_service.collect_toll(db, owner, tunnel, actor_ship=ship)

        mock_rep.assert_not_called()

    def test_private_gate_skips_mg_rep(self) -> None:
        owner = _fake_player(credits=1_000)
        traverser = _fake_player(credits=5_000)
        tunnel = _fake_tunnel(
            created_by_player_id=owner.id,
            access_requirements={"toll_amount": 300},
            is_public=False,
        )
        ship = _fake_ship_with_ore(400)
        db = _FakeSession({Player: _FakeQuery(first=owner)})

        with patch(
            "src.services.emergent_reputation_service.apply_trade_volume_rep"
        ) as mock_rep:
            warp_gate_service.collect_toll(
                db, traverser, tunnel, actor_ship=ship
            )

        mock_rep.assert_not_called()

    def test_whitelist_exempt_skips_mg_rep(self) -> None:
        owner = _fake_player(credits=1_000)
        traverser = _fake_player(credits=5_000)
        tunnel = _fake_tunnel(
            created_by_player_id=owner.id,
            access_requirements={
                "toll_amount": 300,
                "whitelist": [str(traverser.id)],
            },
            is_public=True,
        )
        ship = _fake_ship_with_ore(400)
        db = _FakeSession({Player: _FakeQuery(first=owner)})

        with patch(
            "src.services.emergent_reputation_service.apply_trade_volume_rep"
        ) as mock_rep:
            result = warp_gate_service.collect_toll(
                db, traverser, tunnel, actor_ship=ship
            )

        assert result["charged"] == 0
        assert result["exempt_reason"] == "whitelist"
        mock_rep.assert_not_called()

    def test_zero_cargo_skips_mg_rep(self) -> None:
        owner = _fake_player(credits=1_000)
        traverser = _fake_player(credits=5_000)
        tunnel = _fake_tunnel(
            created_by_player_id=owner.id,
            access_requirements={"toll_amount": 300},
            is_public=True,
        )
        empty_ship = SimpleNamespace(
            cargo={"capacity": 1_000, "used": 0, "contents": {}}
        )
        db = _FakeSession({Player: _FakeQuery(first=owner)})

        with patch(
            "src.services.emergent_reputation_service.apply_trade_volume_rep"
        ) as mock_rep:
            warp_gate_service.collect_toll(
                db, traverser, tunnel, actor_ship=empty_ship
            )

        mock_rep.assert_not_called()
