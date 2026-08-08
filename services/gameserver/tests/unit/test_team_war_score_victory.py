"""Unit coverage for WO-BUILD-TEAM-WAR-SCORE-HOOK-VICTORY.

DB-free: real Team ORM rows in a tiny fake session that honors
with_for_update + ascending lock order, mirrors declare_war tests.
"""
from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

from src.models.team import Team
from src.services import team_war_service as tws


class _FakeQuery:
    def __init__(self, session, model):
        self._session = session
        self._model = model
        self._filters = {}
        self._for_update = False

    def filter(self, *conds):
        for cond in conds:
            key = getattr(getattr(cond, "left", None), "key", None)
            val = getattr(getattr(cond, "right", None), "value", None)
            if key is not None:
                self._filters[key] = val
        return self

    def populate_existing(self):
        return self

    def with_for_update(self):
        self._for_update = True
        return self

    def first(self):
        if self._model is Team:
            tid = self._filters.get("id")
            row = self._session.teams.get(tid)
            if row is not None and self._for_update:
                self._session.lock_log.append(tid)
            return row
        return None


class _FakeSession:
    def __init__(self, teams):
        self.teams = {t.id: t for t in teams}
        self.lock_log = []

    def query(self, model):
        return _FakeQuery(self, model)


def _team(*, team_id=None, wars=None):
    t = Team(
        id=team_id or uuid4(),
        name=f"team-{uuid4()}",
        leader_id=uuid4(),
        member_roles={"active_wars": list(wars or [])},
    )
    return t


def _player(team_id):
    return SimpleNamespace(id=uuid4(), team_id=team_id)


def _mutual_wars(a_id, b_id, a_score=None, b_score=None):
    a_score = a_score or {"us": 0, "them": 0}
    b_score = b_score or {"us": 0, "them": 0}
    return (
        [{
            "target_team_id": str(b_id),
            "status": "active",
            "score": dict(a_score),
            "declared_by": "x",
            "declared_at": "t",
            "reason": "",
        }],
        [{
            "target_team_id": str(a_id),
            "status": "active",
            "score": dict(b_score),
            "declared_by": "x",
            "declared_at": "t",
            "reason": "",
        }],
    )


def test_record_pvp_kill_noop_without_teams():
    db = _FakeSession([])
    assert tws.record_pvp_kill(db, _player(None), _player(uuid4())) is None


def test_record_pvp_kill_noop_without_active_war():
    a_id, b_id = uuid4(), uuid4()
    a = _team(team_id=a_id, wars=[])
    b = _team(team_id=b_id, wars=[])
    db = _FakeSession([a, b])
    assert tws.record_pvp_kill(db, _player(a_id), _player(b_id)) is None


def test_record_pvp_kill_increments_score():
    a_id, b_id = uuid4(), uuid4()
    a_wars, b_wars = _mutual_wars(a_id, b_id)
    a = _team(team_id=a_id, wars=a_wars)
    b = _team(team_id=b_id, wars=b_wars)
    db = _FakeSession([a, b])
    result = tws.record_pvp_kill(db, _player(a_id), _player(b_id))
    assert result is not None
    assert result["attacker_score_us"] == 1
    assert result["victory"] is False
    assert a.member_roles["active_wars"][0]["score"]["us"] == 1
    assert b.member_roles["active_wars"][0]["score"]["them"] == 1
    # Ascending lock order
    assert db.lock_log == sorted([a_id, b_id], key=str)


def test_record_pvp_kill_victory_at_threshold():
    a_id, b_id = uuid4(), uuid4()
    n = tws.VICTORY_KILL_THRESHOLD - 1
    a_wars, b_wars = _mutual_wars(
        a_id, b_id,
        a_score={"us": n, "them": 0},
        b_score={"us": 0, "them": n},
    )
    a = _team(team_id=a_id, wars=a_wars)
    b = _team(team_id=b_id, wars=b_wars)
    db = _FakeSession([a, b])
    result = tws.record_pvp_kill(db, _player(a_id), _player(b_id))
    assert result["victory"] is True
    assert result["attacker_score_us"] == tws.VICTORY_KILL_THRESHOLD
    assert result["winner_team_id"] == str(a_id)
    assert result["loser_team_id"] == str(b_id)
    assert result["victory_at"]
    a_war = a.member_roles["active_wars"][0]
    b_war = b.member_roles["active_wars"][0]
    assert a_war["status"] == "ceased"
    assert b_war["status"] == "ceased"
    assert a_war["cease_reason"] == "victory"
    assert a_war["winner_team_id"] == str(a_id)
    assert a_war["loser_team_id"] == str(b_id)
    assert a_war["victory_at"] == result["victory_at"]
    assert b_war["victory_at"] == result["victory_at"]
    assert a_war["ceased_at"] == a_war["victory_at"]
    event = result["event"]
    assert event["type"] == "team_war_victory"
    assert event["winner_team_id"] == str(a_id)
    assert event["loser_team_id"] == str(b_id)
    assert event["victory_at"] == result["victory_at"]
    assert event["score"]["winner_us"] == tws.VICTORY_KILL_THRESHOLD
    assert event["threshold"] == tws.VICTORY_KILL_THRESHOLD
