"""LEG-4131 — GET /ranking/leaderboard/public passes through is_wanted/is_suspect."""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from src.api.routes.ranking import PublicLeaderboardEntry
from src.auth.dependencies import get_current_player
from src.core.database import get_db
from src.main import app

API = "/api/v1/ranking/leaderboard/public"


def _requesting_player():
    return SimpleNamespace(
        id=uuid.uuid4(),
        username="viewer",
        rank_points=0,
        aria_total_interactions=0,
        is_active=True,
    )


def _leaderboard_player(*, is_suspect: bool = False, is_wanted: bool = False):
    return SimpleNamespace(
        id=uuid.uuid4(),
        username="lawful-or-not",
        military_rank="Sergeant",
        rank_points=300,
        is_active=True,
        aria_bonus_multiplier=1.0,
        is_game_complete=False,
        rank_victory_at=None,
        is_suspect=is_suspect,
        is_wanted=is_wanted,
        aria_total_interactions=5,
        settings={},
    )


def _make_db(*, players=None, total_players: int = 1):
    players = players if players is not None else [_leaderboard_player()]
    db = MagicMock()

    def _query(model, *args):
        q = MagicMock()
        name = getattr(model, "__name__", str(model))
        model_s = str(model)
        if "PlayerMedal" in model_s or name == "PlayerMedal":
            chain = q.filter.return_value
            chain.group_by.return_value.all.return_value = []
        elif "Player" in model_s or name == "Player":
            chain = q.filter.return_value
            chain.order_by.return_value.limit.return_value.all.return_value = players
            chain.filter.return_value.all.return_value = players
            chain.count.return_value = total_players
            chain.scalar.return_value = 0
        return q

    db.query.side_effect = _query
    return db


@pytest.fixture
def ranking_client():
    return TestClient(app, base_url="http://localhost")


@pytest.fixture(autouse=True)
def _isolate_overrides():
    saved_player = app.dependency_overrides.get(get_current_player)
    saved_db = app.dependency_overrides.get(get_db)
    yield
    for key, saved in ((get_current_player, saved_player), (get_db, saved_db)):
        if saved is not None:
            app.dependency_overrides[key] = saved
        else:
            app.dependency_overrides.pop(key, None)


def test_public_leaderboard_entry_defaults_law_flags_false():
    entry = PublicLeaderboardEntry(
        position=1,
        player_id=str(uuid.uuid4()),
        nickname="recruit",
        military_rank="Recruit",
        score=0,
    )
    assert entry.is_suspect is False
    assert entry.is_wanted is False


@pytest.mark.parametrize(
    "is_suspect,is_wanted",
    [
        (True, False),
        (False, True),
        (True, True),
        (False, False),
    ],
)
def test_public_rank_points_leaderboard_passthrough_matches_player(
    ranking_client, is_suspect, is_wanted
):
    listed = _leaderboard_player(is_suspect=is_suspect, is_wanted=is_wanted)
    app.dependency_overrides[get_current_player] = _requesting_player
    app.dependency_overrides[get_db] = lambda: _make_db(players=[listed])
    resp = ranking_client.get(API, params={"category": "rank_points"})
    assert resp.status_code == 200
    entry = resp.json()["entries"][0]
    assert entry["is_suspect"] is is_suspect
    assert entry["is_wanted"] is is_wanted
    assert entry["is_suspect"] is bool(listed.is_suspect)
    assert entry["is_wanted"] is bool(listed.is_wanted)


@pytest.mark.parametrize(
    "is_suspect,is_wanted",
    [
        (True, False),
        (False, True),
    ],
)
def test_public_exploration_leaderboard_passthrough_matches_player(
    ranking_client, is_suspect, is_wanted
):
    listed = _leaderboard_player(is_suspect=is_suspect, is_wanted=is_wanted)
    app.dependency_overrides[get_current_player] = _requesting_player
    app.dependency_overrides[get_db] = lambda: _make_db(players=[listed])
    resp = ranking_client.get(API, params={"category": "exploration"})
    assert resp.status_code == 200
    entry = resp.json()["entries"][0]
    assert entry["is_suspect"] is is_suspect
    assert entry["is_wanted"] is is_wanted
