"""Law-standing name_color override (LEG-4135 invent=0).

Canon: sw2102-docs/FEATURES/gameplay/ranking.md — Suspect / Wanted name
color overrides personal-reputation tier color on every surface that
already renders ``Player.name_color``. Hex values are pinned to RankDisplay
law chrome (``.rank-username.wanted`` / ``.suspect``), not invented.
"""
from __future__ import annotations

from src.models.player import Player

# RankDisplay `.rank-username.wanted` / `.suspect` (player-client ranking.css).
WANTED_NAME_COLOR = "#FF4444"
SUSPECT_NAME_COLOR = "#FFAA44"


def apply_law_name_color(player: Player) -> str:
    """Set ``player.name_color`` from Wanted → Suspect → reputation tier.

    Wanted overrides Suspect and tier; Suspect overrides tier; otherwise
    restore via ``PersonalReputationService._get_tier_for_score``. Returns
    the color written. Pure in-memory mutation — caller owns flush/commit.
    """
    if player.is_wanted:
        player.name_color = WANTED_NAME_COLOR
        return player.name_color
    if player.is_suspect:
        player.name_color = SUSPECT_NAME_COLOR
        return player.name_color

    from src.services.personal_reputation_service import PersonalReputationService

    score = player.personal_reputation if player.personal_reputation is not None else 0
    _tier, color = PersonalReputationService._get_tier_for_score(score)
    player.name_color = color
    return player.name_color


__all__ = [
    "WANTED_NAME_COLOR",
    "SUSPECT_NAME_COLOR",
    "apply_law_name_color",
]
