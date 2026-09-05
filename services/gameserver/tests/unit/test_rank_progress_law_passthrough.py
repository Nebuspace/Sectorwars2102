"""LEG-4129 — GET /ranking/progress passes through is_wanted/is_suspect."""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from src.api.routes import ranking as ranking_mod
from src.api.routes.ranking import RankProgressResponse, get_rank_progress


def _rank_info(*, is_suspect: bool, is_wanted: bool) -> dict:
    return {
        "player_id": str(uuid.uuid4()),
        "username": "lawful-or-not",
        "current_rank": "Sergeant",
        "rank_level": 4,
        "rank_tier": "NCO",
        "rank_points": 300,
        "points_to_next_rank": 200,
        "next_rank": "Staff Sergeant",
        "next_rank_points_required": 500,
        "progress_percent": 60.0,
        "bonuses": {
            "trading_discount_percent": 0,
            "max_turns_bonus": 0,
            "combat_damage_bonus_percent": 0,
        },
        "is_max_rank": False,
        "effective_max_turns": 1000,
        "aria_multiplier": 1.0,
        "is_suspect": is_suspect,
        "is_wanted": is_wanted,
    }


def _db_with_zero_stats() -> MagicMock:
    """CombatLog / MarketTransaction count+sum chains all return 0."""
    db = MagicMock()
    chain = MagicMock()
    chain.filter.return_value = chain
    chain.scalar.return_value = 0
    db.query.return_value = chain
    return db


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "is_suspect,is_wanted",
    [
        (True, False),
        (False, True),
        (True, True),
        (False, False),
    ],
)
async def test_get_rank_progress_passthrough_matches_rank_info(is_suspect, is_wanted):
    rank_info = _rank_info(is_suspect=is_suspect, is_wanted=is_wanted)
    mock_service = MagicMock()
    mock_service.get_rank_info.return_value = rank_info
    player = SimpleNamespace(
        id=uuid.UUID(rank_info["player_id"]),
        credits=0,
        turns=10,
        aria_total_interactions=0,
    )

    with patch.object(ranking_mod, "RankingService", return_value=mock_service):
        result = await get_rank_progress(player=player, db=_db_with_zero_stats())

    assert isinstance(result, RankProgressResponse)
    assert result.is_suspect is is_suspect
    assert result.is_wanted is is_wanted
    # Matches get_rank_info keys the progress route previously dropped.
    assert result.is_suspect is bool(rank_info["is_suspect"])
    assert result.is_wanted is bool(rank_info["is_wanted"])


def test_rank_progress_response_defaults_law_flags_false():
    payload = RankProgressResponse(
        player_id=str(uuid.uuid4()),
        username="recruit",
        current_rank="Recruit",
        rank_level=0,
        rank_tier="Enlisted",
        rank_points=0,
        points_to_next_rank=50,
        progress_percent=0.0,
        bonuses=ranking_mod.RankBonuses(
            trading_discount_percent=0,
            max_turns_bonus=0,
            combat_damage_bonus_percent=0,
        ),
        is_max_rank=False,
        effective_max_turns=1000,
        aria_multiplier=1.0,
        stats={},
        requirements=[],
    )
    assert payload.is_suspect is False
    assert payload.is_wanted is False
