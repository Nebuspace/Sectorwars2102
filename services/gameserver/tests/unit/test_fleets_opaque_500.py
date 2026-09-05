"""LEG-3812 — fleets route handlers must not echo Exception text on 500s.

Mirrors LEG-3794 planets / LEG-3690 admin_fleets opaque densify.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException

from src.api.routes import fleets as fleets_mod
from src.api.routes.fleets import (
    BattleInitiateRequest,
    CreateFleetRequest,
    create_fleet,
    initiate_battle,
    simulate_battle_round,
)
from src.models.fleet import Fleet, FleetBattle, FleetMember


def _flatten(conditions):
    out = []
    for c in conditions:
        if type(c).__name__ == "BooleanClauseList":
            out.extend(_flatten(c.get_children()))
        else:
            out.append(c)
    return out


def _condition_matches(row, condition):
    attr_name = condition.left.name
    right = condition.right
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
            r
            for r in self._pool
            if all(_condition_matches(r, c) for c in self._conditions)
        ]
        return matches[0] if matches else None


class _FakeSession:
    def __init__(self, pools: Dict[type, List[Any]]):
        self._pools = pools

    def query(self, model):
        return _FakeQuery(self._pools.get(model, []))


def _player(**overrides):
    defaults = dict(id=uuid4(), team_id=uuid4())
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _fleet(**overrides) -> Fleet:
    defaults = dict(
        id=uuid4(),
        team_id=uuid4(),
        name="Alpha Fleet",
        formation="standard",
        status="ready",
        supply_level=100,
        coordination_bonus=0.0,
        commander_id=None,
    )
    defaults.update(overrides)
    return Fleet(**defaults)


def _battle(*, attacker_fleet=None, defender_fleet=None, **overrides) -> FleetBattle:
    defaults = dict(
        id=uuid4(),
        attacker_fleet_id=attacker_fleet.id if attacker_fleet else None,
        defender_fleet_id=defender_fleet.id if defender_fleet else None,
        phase="combat",
        attacker_ships_initial=1,
        defender_ships_initial=1,
    )
    defaults.update(overrides)
    battle = FleetBattle(**defaults)
    battle.attacker_fleet = attacker_fleet
    battle.defender_fleet = defender_fleet
    battle.battle_log = []
    battle.ended_at = None
    battle.winner = None
    return battle


@pytest.mark.asyncio
async def test_create_fleet_boom_is_opaque_500():
    secret = "secret-create-fleet-should-not-leak"
    player = _player()
    request = CreateFleetRequest(name="Strike Group", formation="standard")
    mock_service = MagicMock()
    mock_service.create_fleet.side_effect = RuntimeError(secret)

    with patch.object(fleets_mod, "FleetService", return_value=mock_service):
        with pytest.raises(HTTPException) as excinfo:
            await create_fleet(request=request, player=player, db=MagicMock())

    exc = excinfo.value
    assert exc.status_code == 500
    assert exc.detail == {
            "error_code": "ERR_FLEETS_CREATE_FAILED",
            "detail": "Failed to create fleet",
        }
    assert secret not in str(exc.detail)


@pytest.mark.asyncio
async def test_initiate_battle_boom_is_opaque_500():
    secret = "secret-initiate-battle-should-not-leak"
    player = _player()
    fleet = _fleet(commander_id=player.id)
    request = BattleInitiateRequest(defender_fleet_id=uuid4())
    mock_service = MagicMock()
    mock_service.initiate_battle.side_effect = RuntimeError(secret)
    db = _FakeSession({Fleet: [fleet]})

    with patch.object(fleets_mod, "FleetService", return_value=mock_service):
        with pytest.raises(HTTPException) as excinfo:
            await initiate_battle(
                fleet_id=fleet.id,
                request=request,
                player=player,
                db=db,
            )

    exc = excinfo.value
    assert exc.status_code == 500
    assert exc.detail == {
            "error_code": "ERR_FLEETS_INITIATE_BATTLE_FAILED",
            "detail": "Failed to initiate battle",
        }
    assert secret not in str(exc.detail)


@pytest.mark.asyncio
async def test_simulate_battle_round_boom_is_opaque_500():
    secret = "secret-simulate-round-should-not-leak"
    player = _player()
    attacker_fleet = _fleet()
    defender_fleet = _fleet()
    battle = _battle(attacker_fleet=attacker_fleet, defender_fleet=defender_fleet)
    membership = FleetMember(
        id=uuid4(),
        fleet_id=attacker_fleet.id,
        ship_id=uuid4(),
        player_id=player.id,
        role="attacker",
    )
    db = _FakeSession({
        FleetBattle: [battle],
        FleetMember: [membership],
    })
    mock_service = MagicMock()
    mock_service.simulate_battle_round.side_effect = RuntimeError(secret)

    with patch.object(fleets_mod, "FleetService", return_value=mock_service):
        with pytest.raises(HTTPException) as excinfo:
            await simulate_battle_round(
                battle_id=battle.id,
                player=player,
                db=db,
            )

    exc = excinfo.value
    assert exc.status_code == 500
    assert exc.detail == {
            "error_code": "ERR_FLEETS_SIMULATE_ROUND_FAILED",
            "detail": "Failed to simulate battle round",
        }
    assert secret not in str(exc.detail)


def test_fleets_http500_is_opaque():
    """LEG-3812 — static pin: fleet route 500 details stay opaque."""
    src = Path(fleets_mod.__file__).read_text(encoding="utf-8")
    assert "ERR_FLEETS_CREATE_FAILED" in src
    assert "ERR_FLEETS_INITIATE_BATTLE_FAILED" in src
    assert "ERR_FLEETS_SIMULATE_ROUND_FAILED" in src
    assert "route_internal_error" in src
    assert "Failed to create fleet: {str(e)}" not in src
    assert "Failed to initiate battle: {str(e)}" not in src
    assert "Failed to simulate battle round: {str(e)}" not in src
