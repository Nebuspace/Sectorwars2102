"""LEG-2708 — public GET /ranking/leaderboard/public rank_points exposes rank_tier."""
import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

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


def _leaderboard_player(*, rank_points: int = 300):
    """Sergeant tier (300 pts) → rank_tier 'NCO' per RANK_DEFINITIONS."""
    return SimpleNamespace(
        id=uuid.uuid4(),
        username="topgun",
        military_rank="Sergeant",
        rank_points=rank_points,
        is_active=True,
        aria_bonus_multiplier=1.0,
        is_game_complete=False,
        rank_victory_at=None,
        is_suspect=False,
        is_wanted=False,
        aria_total_interactions=0,
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


def test_public_rank_points_leaderboard_includes_rank_tier(ranking_client):
    app.dependency_overrides[get_current_player] = _requesting_player
    app.dependency_overrides[get_db] = lambda: _make_db()
    resp = ranking_client.get(API, params={"category": "rank_points"})
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["category"] == "rank_points"
    assert len(payload["entries"]) == 1
    entry = payload["entries"][0]
    assert entry["rank_tier"] == "NCO"
    assert entry["rank_level"] == 3
    assert entry["military_rank"] == "Sergeant"


def test_public_exploration_leaderboard_omits_rank_tier(ranking_client):
    app.dependency_overrides[get_current_player] = _requesting_player
    app.dependency_overrides[get_db] = lambda: _make_db()
    resp = ranking_client.get(API, params={"category": "exploration"})
    assert resp.status_code == 200
    entry = resp.json()["entries"][0]
    assert entry.get("rank_tier") is None
    assert entry.get("rank_level") is None
