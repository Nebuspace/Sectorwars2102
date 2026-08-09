"""Deepen coverage for RankingService pure helpers (ladder / bonuses / points).

Companion to test_ranking_service_nh5.py (canon gates). This file pins the
ladder math ADR-0004 turn cap, combat/trade point bands, and legacy aliases.
"""
from __future__ import annotations

import types

import pytest

from src.services.ranking_service import (
    LEGACY_RANK_MAP,
    RANK_DEFINITIONS,
    RankingService,
)


@pytest.mark.parametrize(
    "points,expected_name",
    [
        (0, "Recruit"),
        (49, "Recruit"),
        (50, "Spacer"),
        (299, "Corporal"),
        (300, "Sergeant"),
        (9999, "Captain"),
        (10000, "Senior Captain"),
        (60000, "Fleet Admiral"),
        (999_999, "Fleet Admiral"),
    ],
)
def test_get_rank_for_points_thresholds(points, expected_name):
    assert RankingService.get_rank_for_points(points)["name"] == expected_name


def test_get_next_rank_ladder_and_ceiling():
    assert RankingService.get_next_rank("Recruit")["name"] == "Spacer"
    assert RankingService.get_next_rank("Private")["name"] == "Spacer"  # legacy
    assert RankingService.get_next_rank("Fleet Admiral") is None
    assert RankingService.get_next_rank("NotARank") is None


@pytest.mark.parametrize(
    "name,level",
    [
        ("Recruit", 0),
        ("Private", 0),  # legacy → Recruit
        ("Captain", 11),
        ("General", 16),  # legacy → Admiral
        ("UnknownRank", 0),
    ],
)
def test_get_rank_level(name, level):
    assert RankingService.get_rank_level(name) == level


def test_get_rank_bonuses_known_and_unknown():
    spacer = RankingService.get_rank_bonuses("Spacer")
    assert spacer == {
        "trading_discount_percent": 2,
        "max_turns_bonus": 5,
        "combat_damage_bonus_percent": 1,
    }
    # legacy Private → Recruit (all zeros)
    assert RankingService.get_rank_bonuses("Private") == {
        "trading_discount_percent": 0,
        "max_turns_bonus": 0,
        "combat_damage_bonus_percent": 0,
    }
    assert RankingService.get_rank_bonuses("Bogus")["max_turns_bonus"] == 0


def test_calculate_max_turns_base_plus_rank_only():
    player = types.SimpleNamespace(military_rank="Spacer")
    assert RankingService.calculate_max_turns(player) == 1000 + 5
    assert RankingService.calculate_max_turns(player, base_turns=500) == 505


@pytest.mark.parametrize(
    "winner,loser,expected",
    [
        ("Recruit", "Recruit", 15),  # same rank → base 10 + 5
        ("Recruit", "Spacer", 20),  # +1 level → +10
        ("Recruit", "Fleet Admiral", 50),  # capped at 50
        ("Fleet Admiral", "Recruit", 10),  # lower opponent → floor 10
    ],
)
def test_calculate_combat_points_bands(winner, loser, expected):
    assert RankingService.calculate_combat_points(winner, loser) == expected


@pytest.mark.parametrize(
    "value,expected",
    [
        (0, 0),
        (999, 0),
        (1000, 5),
        (10000, 10),
        (50000, 15),
        (100000, 20),
    ],
)
def test_calculate_trading_points_milestones(value, expected):
    assert RankingService.calculate_trading_points(value) == expected


def test_fixed_exploration_and_colony_points():
    assert RankingService.calculate_exploration_points() == 3
    assert RankingService.calculate_colony_points() == 25


def test_legacy_map_targets_exist_in_definitions():
    names = {r["name"] for r in RANK_DEFINITIONS}
    for legacy, mapped in LEGACY_RANK_MAP.items():
        assert mapped in names, f"{legacy} → {mapped} missing from RANK_DEFINITIONS"
