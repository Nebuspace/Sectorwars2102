"""LEG-2593 — public leaderboard medal identity fields (#1207)."""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

from src.api.routes.ranking import PublicLeaderboardEntry, _enrich_public_leaderboard_medal_identity


class _FakeQuery:
    def __init__(self, players, medal_rows):
        self._players = players
        self._medal_rows = medal_rows
        self._model = None

    def filter(self, *args, **kwargs):
        return self

    def group_by(self, *_a, **_k):
        return self

    def all(self):
        if self._model is not None and "PlayerMedal" in str(self._model):
            return self._medal_rows
        return self._players


class _FakeDb:
    def __init__(self, players, medal_rows):
        self._players = players
        self._medal_rows = medal_rows

    def query(self, model, *args):
        q = _FakeQuery(self._players, self._medal_rows)
        q._model = model
        return q


def test_enrich_attaches_pinned_medal_id_and_count():
    player_id = uuid4()
    player = SimpleNamespace(
        id=player_id,
        settings={
            "medal_privacy": {
                "pinned_medal_id": "bronze_cluster",
                "show_count_publicly": True,
            }
        },
    )
    db = _FakeDb(players=[player], medal_rows=[(player_id, 3)])
    entries = [
        PublicLeaderboardEntry(
            position=1,
            player_id=str(player_id),
            nickname="Ace",
            military_rank="Lieutenant",
            score=100,
        )
    ]

    out = _enrich_public_leaderboard_medal_identity(db, entries)

    assert len(out) == 1
    assert out[0].pinned_medal_id == "bronze_cluster"
    assert out[0].medal_count == 3


def test_enrich_hides_medal_count_when_privacy_disabled():
    player_id = uuid4()
    player = SimpleNamespace(
        id=player_id,
        settings={
            "medal_privacy": {
                "pinned_medal_id": "silver_star",
                "show_count_publicly": False,
            }
        },
    )
    db = _FakeDb(players=[player], medal_rows=[(player_id, 9)])
    entries = [
        PublicLeaderboardEntry(
            position=1,
            player_id=str(player_id),
            nickname="Ghost",
            military_rank="Captain",
            score=50,
        )
    ]

    out = _enrich_public_leaderboard_medal_identity(db, entries)

    assert out[0].pinned_medal_id == "silver_star"
    assert out[0].medal_count is None


def test_enrich_empty_entries_is_noop():
    db = _FakeDb(players=[], medal_rows=[])
    assert _enrich_public_leaderboard_medal_identity(db, []) == []
