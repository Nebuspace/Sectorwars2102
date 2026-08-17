"""LEG-164: combat folds WO-G6 ``citadel_passive_defense_rating`` into
``_calculate_planetary_defense_reduction`` (same helper as defensePower).

Mid-upgrade: current-level capacity + 50% of the next level's passive-defense
delta. Idle: current-level only (no spurious mid-upgrade bonus).
"""
from __future__ import annotations

import types

import pytest

from src.services.citadel_service import (
    citadel_passive_defense,
    citadel_passive_defense_rating,
)
from src.services.combat_service import CombatService


def _cs() -> CombatService:
    return CombatService(db=None)


def _planet(*, citadel_level: int, citadel_upgrading: bool, defense_fighters: int = 0):
    return types.SimpleNamespace(
        defense_level=0,
        defense_shields=0,
        shields=0,
        defense_turrets=0,
        defense_fighters=defense_fighters,
        specialization=None,
        active_events={},
        citadel_level=citadel_level,
        citadel_upgrading=citadel_upgrading,
    )


class TestCitadelMidUpgradeCombatDefense:
    def test_idle_rating_is_current_level_only(self):
        planet = _planet(citadel_level=1, citadel_upgrading=False)
        assert citadel_passive_defense_rating(planet) == citadel_passive_defense(1)
        assert citadel_passive_defense_rating(planet) == 10

    def test_upgrading_adds_half_next_level_delta(self):
        # L1=10, L2=25 → delta 15 → mid = 10 + int(0.5*15) = 17
        planet = _planet(citadel_level=1, citadel_upgrading=True)
        assert citadel_passive_defense_rating(planet) == 17

    def test_mid_upgrade_combat_stronger_than_idle_same_level(self):
        cs = _cs()
        idle = cs._calculate_planetary_defense_reduction(
            _planet(citadel_level=1, citadel_upgrading=False)
        )
        upgrading = cs._calculate_planetary_defense_reduction(
            _planet(citadel_level=1, citadel_upgrading=True)
        )
        # Fighter term: 1 kill per 5 garrison — idle 10→2, mid 17→3
        assert idle["anti_drone_kills_per_round"] == 2
        assert upgrading["anti_drone_kills_per_round"] == 3
        assert upgrading["anti_drone_kills_per_round"] > idle["anti_drone_kills_per_round"]
        assert upgrading["damage_reduction"] >= idle["damage_reduction"]

    def test_idle_matches_current_level_capacity_kills(self):
        """Not upgrading: combat uses current-level rating only (unchanged vs base)."""
        cs = _cs()
        idle_l2 = cs._calculate_planetary_defense_reduction(
            _planet(citadel_level=2, citadel_upgrading=False)
        )
        # L2 capacity 25 → 25//5 = 5
        assert idle_l2["anti_drone_kills_per_round"] == 5

    def test_purchased_fighters_stack_with_citadel_rating(self):
        cs = _cs()
        # idle L1 rating 10 + 5 purchased = 15 → 15//5 = 3
        result = cs._calculate_planetary_defense_reduction(
            _planet(citadel_level=1, citadel_upgrading=False, defense_fighters=5)
        )
        assert result["anti_drone_kills_per_round"] == 3
