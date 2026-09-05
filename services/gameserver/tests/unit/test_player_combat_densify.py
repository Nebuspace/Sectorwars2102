"""LEG-3931 — player_combat unexpected failures return structured 500s."""

from __future__ import annotations

from pathlib import Path

from src.api.routes import player_combat as pc_mod


def test_player_combat_http500_catches_are_structured():
    src = Path(pc_mod.__file__).read_text(encoding="utf-8")
    for code in (
        "ERR_PLAYER_COMBAT_ENGAGE_FAILED",
        "ERR_PLAYER_COMBAT_SECTOR_NOT_FOUND",
    ):
        assert code in src
    assert "route_internal_error" in src
    assert 'HTTPException(status_code=500, detail="Failed to engage in combat")' not in src
    assert 'HTTPException(status_code=500, detail="Current sector not found")' not in src
