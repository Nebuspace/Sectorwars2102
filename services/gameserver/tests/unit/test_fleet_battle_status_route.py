"""GET /fleets/battles/{battle_id} — single-battle detail status.

FleetService.get_battle_status was fully built (phase/casualties/damage/
ships-remaining snapshot) but had zero callers: GET /battles only lists,
POST /battles/{id}/simulate-round only advances a round, and neither
returns a single-battle detail view. This wires the missing GET route,
reusing the exact "player has ships in either fleet" ownership check
simulate_battle_round already applies.

DB-free, direct-handler-call house pattern — mirrors
test_fleets_route_dep_swap_mack.py's _FakeSyncSession /
test_fleet_casualty_succession.py's condition-matching _FakeQuery, combined
here since the route's own ownership check and the service's internal
queries share one session object.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Dict, List
from uuid import uuid4

import pytest
from fastapi import HTTPException

from src.api.routes import fleets as route
from src.models.fleet import Fleet, FleetBattle, FleetBattleCasualty, FleetMember


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
        matches = [r for r in self._pool if all(_condition_matches(r, c) for c in self._conditions)]
        return matches[0] if matches else None

    def all(self):
        return [r for r in self._pool if all(_condition_matches(r, c) for c in self._conditions)]


class _FakeSession:
    def __init__(self, pools: Dict[type, List[Any]]):
        self._pools = pools

    def query(self, model):
        return _FakeQuery(self._pools.get(model, []))


def make_player(**overrides):
    defaults = dict(id=uuid4(), team_id=uuid4())
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def make_fleet(**overrides) -> Fleet:
    defaults = dict(id=uuid4(), team_id=uuid4(), name="Fleet", formation="standard",
                     status="in_battle", supply_level=100, coordination_bonus=0.0)
    defaults.update(overrides)
    return Fleet(**defaults)


def make_battle(*, attacker_fleet=None, defender_fleet=None, **overrides) -> FleetBattle:
    defaults = dict(
        id=uuid4(),
        attacker_fleet_id=attacker_fleet.id if attacker_fleet else None,
        defender_fleet_id=defender_fleet.id if defender_fleet else None,
        phase="preparation",
        attacker_ships_initial=0, defender_ships_initial=0,
        attacker_ships_destroyed=0, defender_ships_destroyed=0,
        attacker_ships_retreated=0, defender_ships_retreated=0,
        total_damage_dealt=0, attacker_damage_dealt=0, defender_damage_dealt=0,
        credits_looted=0,
    )
    defaults.update(overrides)
    battle = FleetBattle(**defaults)
    battle.attacker_fleet = attacker_fleet
    battle.defender_fleet = defender_fleet
    return battle


class TestBattleNotFound:
    @pytest.mark.asyncio
    async def test_unknown_battle_id_returns_404(self):
        player = make_player()
        db = _FakeSession({})
        with pytest.raises(HTTPException) as exc_info:
            await route.get_battle_status(battle_id=uuid4(), player=player, db=db)
        assert exc_info.value.status_code == 404


class TestBattleOwnershipEnforced:
    @pytest.mark.asyncio
    async def test_player_with_no_ships_in_either_fleet_gets_403(self):
        attacker_fleet = make_fleet()
        defender_fleet = make_fleet()
        battle = make_battle(attacker_fleet=attacker_fleet, defender_fleet=defender_fleet)
        player = make_player()
        db = _FakeSession({FleetBattle: [battle], FleetMember: []})

        with pytest.raises(HTTPException) as exc_info:
            await route.get_battle_status(battle_id=battle.id, player=player, db=db)

        assert exc_info.value.status_code == 403


class TestBattleStatusSuccess:
    @pytest.mark.asyncio
    async def test_attacker_member_gets_full_status_snapshot(self):
        attacker_fleet = make_fleet(total_ships=0)
        defender_fleet = make_fleet(total_ships=0)
        battle = make_battle(attacker_fleet=attacker_fleet, defender_fleet=defender_fleet,
                              phase="combat", winner=None)
        player = make_player()
        membership = FleetMember(id=uuid4(), fleet_id=attacker_fleet.id, ship_id=uuid4(),
                                  player_id=player.id, role="attacker")
        db = _FakeSession({
            FleetBattle: [battle],
            FleetMember: [membership],
            FleetBattleCasualty: [],
        })

        result = await route.get_battle_status(battle_id=battle.id, player=player, db=db)

        assert result["battle_id"] == str(battle.id)
        assert result["phase"] == "combat"
        assert result["is_active"] is True
        assert result["attacker"]["ships_remaining"] == 0
        assert result["defender"]["ships_remaining"] == 0

    @pytest.mark.asyncio
    async def test_defender_member_is_also_authorized(self):
        attacker_fleet = make_fleet(total_ships=0)
        defender_fleet = make_fleet(total_ships=0)
        battle = make_battle(attacker_fleet=attacker_fleet, defender_fleet=defender_fleet)
        player = make_player()
        membership = FleetMember(id=uuid4(), fleet_id=defender_fleet.id, ship_id=uuid4(),
                                  player_id=player.id, role="attacker")
        db = _FakeSession({
            FleetBattle: [battle],
            FleetMember: [membership],
            FleetBattleCasualty: [],
        })

        result = await route.get_battle_status(battle_id=battle.id, player=player, db=db)

        assert result["battle_id"] == str(battle.id)
