"""LEG-132: available-moves exposes Sector row UUID alongside numeric sector_id.

Fleet ``POST /fleets/{id}/move`` needs the destination Sector UUID. The route
already loads each neighbour Sector for region/coords/formations — surface
``Sector.id`` as ``MoveOption.id`` (mirrors ``SectorResponse.id``) without
renaming numeric ``sector_id``. Discovery filtering stays in MovementService
(unchanged here).
"""
from __future__ import annotations

from typing import Any, Dict, List
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from src.api.routes.player import MoveOption, get_available_moves
from src.models.player import Player
from src.models.sector import Sector


def _flatten(conditions):
    out = []
    for c in conditions:
        clauses = getattr(c, "get_children", None)
        if clauses and type(c).__name__ == "BooleanClauseList":
            out.extend(_flatten(c.get_children()))
        else:
            out.append(c)
    return out


def _condition_matches(row, condition):
    left = condition.left
    right = condition.right
    attr_name = left.name
    expected = right.value if hasattr(right, "value") else right
    return getattr(row, attr_name, None) == expected


class _FakeQuery:
    def __init__(self, pool: List[Any]):
        self._pool = pool
        self._conditions: List[Any] = []

    def filter(self, *conditions):
        self._conditions = self._conditions + _flatten(conditions)
        return self

    def first(self):
        matches = [
            r for r in self._pool if all(_condition_matches(r, c) for c in self._conditions)
        ]
        return matches[0] if matches else None


class _FakeSession:
    def __init__(self, pools: Dict[type, List[Any]]):
        self._pools = pools

    def query(self, model):
        return _FakeQuery(self._pools.get(model, []))


def test_move_option_schema_carries_id_and_sector_id():
    row_id = uuid4()
    opt = MoveOption(
        id=str(row_id),
        sector_id=4144,
        name="Neighbor",
        type="STANDARD",
        turn_cost=1,
        can_afford=True,
    )
    dumped = opt.model_dump()
    assert dumped["id"] == str(row_id)
    assert dumped["sector_id"] == 4144
    # Compatibility: numeric field alone still constructs
    bare = MoveOption(
        sector_id=99,
        name="Legacy",
        type="STANDARD",
        turn_cost=1,
        can_afford=False,
    )
    assert bare.id is None
    assert bare.sector_id == 99


@pytest.mark.asyncio
async def test_available_moves_warps_and_tunnels_expose_sector_row_uuid():
    warp_uuid = uuid4()
    tunnel_uuid = uuid4()
    warp_sector = Sector(
        id=warp_uuid,
        sector_id=1001,
        sector_number=1,
        name="Warp Dest",
        type="STANDARD",
        region_id=None,
        x_coord=1,
        y_coord=2,
        z_coord=3,
        hazard_level=0,
        radiation_level=0.0,
    )
    tunnel_sector = Sector(
        id=tunnel_uuid,
        sector_id=2002,
        sector_number=2,
        name="Tunnel Dest",
        type="NEBULA",
        region_id=None,
        x_coord=4,
        y_coord=5,
        z_coord=6,
        hazard_level=0,
        radiation_level=0.0,
    )
    player = Player(
        id=uuid4(),
        user_id=uuid4(),
        turns=50,
        max_turns=1000,
        credits=0,
        current_sector_id=42,
        current_region_id=None,
        current_ship_id=uuid4(),
        is_suspect=False,
    )
    db = _FakeSession({Sector: [warp_sector, tunnel_sector]})

    moves = {
        "warps": [
            {
                "sector_id": 1001,
                "name": "Warp Dest",
                "type": "STANDARD",
                "turn_cost": 1,
                "can_afford": True,
            }
        ],
        "tunnels": [
            {
                "sector_id": 2002,
                "name": "Tunnel Dest",
                "type": "NEBULA",
                "turn_cost": 2,
                "can_afford": True,
                "tunnel_type": "natural",
                "stability": 0.9,
                "one_way": False,
            }
        ],
    }

    fake_svc = MagicMock()
    fake_svc.get_available_moves.return_value = moves

    with patch("src.api.routes.player.MovementService", return_value=fake_svc), patch(
        "src.services.special_formation_service.find_formations_for_sector",
        return_value=[],
    ):
        resp = await get_available_moves(player=player, db=db)

    assert len(resp.warps) == 1
    assert len(resp.tunnels) == 1
    assert resp.warps[0].id == str(warp_uuid)
    assert resp.warps[0].sector_id == 1001
    assert resp.tunnels[0].id == str(tunnel_uuid)
    assert resp.tunnels[0].sector_id == 2002
    # Listing still comes from MovementService; we did not invent extra hops
    fake_svc.get_available_moves.assert_called_once_with(player.id)
    assert {w.sector_id for w in resp.warps} == {1001}
    assert {t.sector_id for t in resp.tunnels} == {2002}
