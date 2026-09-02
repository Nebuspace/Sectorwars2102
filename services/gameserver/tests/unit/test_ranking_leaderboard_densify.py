"""LEG-4046 — ranking leaderboard route returns structured 500s."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

from src.api.routes import ranking as ranking_mod
from src.api.routes.ranking import get_rankings_leaderboard


@pytest.mark.asyncio
async def test_get_rankings_leaderboard_boom_returns_structured_500():
    secret = "secret-leaderboard-service-should-not-leak"
    mock_service = MagicMock()
    mock_service.get_leaderboard.side_effect = RuntimeError(secret)

    with patch.object(ranking_mod, "RankingService", return_value=mock_service):
        with pytest.raises(HTTPException) as excinfo:
            await get_rankings_leaderboard(
                limit=20,
                admin=SimpleNamespace(),
                db=MagicMock(),
            )

    exc = excinfo.value
    assert exc.status_code == 500
    assert exc.detail == {
        "error_code": "ERR_RANKING_LEADERBOARD_FETCH_FAILED",
        "detail": "Failed to fetch rankings leaderboard",
    }
    assert secret not in str(exc.detail)


def test_ranking_leaderboard_http500_is_structured():
    src = Path(ranking_mod.__file__).read_text(encoding="utf-8")
    assert "ERR_RANKING_LEADERBOARD_FETCH_FAILED" in src
    assert "route_internal_error" in src
    assert 'detail="Failed to fetch rankings leaderboard"' not in src
