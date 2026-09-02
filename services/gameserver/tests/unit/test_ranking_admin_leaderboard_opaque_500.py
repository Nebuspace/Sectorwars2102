"""LEG-3733 — admin rankings leaderboard must not echo Exception text."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

from src.api.routes import ranking as ranking_mod
from src.api.routes.ranking import get_rankings_leaderboard


class _BoomDB:
    def query(self, *args, **kwargs):
        raise RuntimeError("secret-leaderboard-count-should-not-leak")


@pytest.mark.asyncio
async def test_get_rankings_leaderboard_service_boom_is_opaque_500():
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
    assert exc.detail == "Failed to fetch rankings leaderboard"
    assert secret not in str(exc.detail)


@pytest.mark.asyncio
async def test_get_rankings_leaderboard_count_boom_is_opaque_500():
    secret = "secret-leaderboard-count-should-not-leak"
    mock_service = MagicMock()
    mock_service.get_leaderboard.return_value = []

    with patch.object(ranking_mod, "RankingService", return_value=mock_service):
        with pytest.raises(HTTPException) as excinfo:
            await get_rankings_leaderboard(
                limit=20,
                admin=SimpleNamespace(),
                db=_BoomDB(),
            )

    exc = excinfo.value
    assert exc.status_code == 500
    assert exc.detail == "Failed to fetch rankings leaderboard"
    assert secret not in str(exc.detail)


def test_ranking_leaderboard_http500_is_opaque():
    """LEG-3733 — static pin: leaderboard 500 detail stays opaque."""
    src = Path(ranking_mod.__file__).read_text(encoding="utf-8")
    assert 'detail="Failed to fetch rankings leaderboard"' in src
    assert "Failed to fetch rankings leaderboard: {str(e)}" not in src
