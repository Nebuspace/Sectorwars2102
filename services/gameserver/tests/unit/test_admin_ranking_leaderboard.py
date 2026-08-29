"""LEG-2608 — admin GET /ranking/leaderboard exposes rank_tier (wire-only)."""
import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from src.auth.dependencies import get_current_user
from src.core.database import get_db
from src.main import app

API = "/api/v1/ranking/leaderboard"


def _admin_user():
    return SimpleNamespace(id=uuid.uuid4(), username="admin", is_admin=True)


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
    )


def _make_db(*, players=None, total_players: int = 1):
    players = players if players is not None else [_leaderboard_player()]
    db = MagicMock()

    def _query(model):
        q = MagicMock()
        name = getattr(model, "__name__", str(model))
        model_s = str(model)
        if "AdminScopeGrant" in model_s or name == "AdminScopeGrant":
            q.filter.return_value.first.return_value = (uuid.uuid4(),)
        elif "Player" in model_s or name == "Player":
            chain = q.filter.return_value
            chain.order_by.return_value.limit.return_value.all.return_value = players
            chain.count.return_value = total_players
        return q

    db.query.side_effect = _query
    return db


@pytest.fixture
def ranking_client():
    return TestClient(app, base_url="http://localhost")


def _clear_admin_rate_limit_buckets() -> None:
    from src.api.middleware.security import RateLimitingMiddleware

    stack = getattr(app, "middleware_stack", None)
    if stack is None:
        try:
            stack = app.build_middleware_stack()
            app.middleware_stack = stack
        except Exception:
            return
    seen: set[int] = set()
    cur = stack
    while cur is not None and id(cur) not in seen:
        seen.add(id(cur))
        if isinstance(cur, RateLimitingMiddleware):
            cur.request_counts.clear()
        cur = getattr(cur, "app", None)


@pytest.fixture(autouse=True)
def _isolate_overrides():
    _clear_admin_rate_limit_buckets()
    saved_user = app.dependency_overrides.get(get_current_user)
    saved_db = app.dependency_overrides.get(get_db)
    yield
    for key, saved in ((get_current_user, saved_user), (get_db, saved_db)):
        if saved is not None:
            app.dependency_overrides[key] = saved
        else:
            app.dependency_overrides.pop(key, None)
    _clear_admin_rate_limit_buckets()


def test_admin_leaderboard_includes_rank_tier(ranking_client):
    app.dependency_overrides[get_current_user] = _admin_user
    app.dependency_overrides[get_db] = lambda: _make_db()
    resp = ranking_client.get(API)
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["total_players"] == 1
    assert len(payload["entries"]) == 1
    entry = payload["entries"][0]
    assert entry["rank_tier"] == "NCO"
    assert entry["rank_level"] == 3
    assert entry["military_rank"] == "Sergeant"
