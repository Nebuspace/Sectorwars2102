"""WO-DECLARE-WAR-LOCK-ORDER -- ``declare_war`` (api/routes/teams.py) now
acquires its two ``Team`` ``with_for_update()`` locks in ASCENDING-id order,
never caller-order (declaring-team-then-target). Pure lock-REORDER fix --
no war-storage/response semantics changed anywhere; the existing 400/403/404
status codes still fire for the same inputs, and the success payload shape
is untouched.

Before this fix, ``declare_war`` locked the URL-param declaring team FIRST
then the body's target team SECOND -- caller-order, not ascending-id. That
is an AB-BA deadlock against ``fleet_service._lock_teams_ascending``'s
ascending-id Team locks taken every fleet-battle round (WO-FLEET-TREASURY-
LOCK): a concurrent ``declare_war`` where the higher-id team declares war on
the lower-id team would lock high-then-low while a battle round on the same
pair locks low-then-high.

DB-free fake-session convention (mirrors test_bounty_dual_lock_order.py's
``_FakeQuery``/``_FakeSession`` + ``player_lock_log`` instrumentation,
adapted to ``Team``): real transient ``Team`` ORM instances (so
``flag_modified`` -- imported and called for real inside the route --
operates on genuine mapped instances, not stand-ins) held in a small
id-keyed fake session that records every ``.with_for_update()`` acquisition,
in call order, so the ordering property is asserted directly rather than
merely inferred from behavior.

Deadlock-freedom under real concurrent Postgres transactions cannot be
demonstrated by a single-threaded fake -- that is the orchestrator's live-
Postgres leg. What IS fully provable here is the ORDERING property itself:
the necessary precondition for that guarantee.
"""
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException

from src.api.routes.teams import DeclareWarRequest, declare_war
from src.models.team import Team


# --------------------------------------------------------------------------- #
# DB-free fake session -- records Team lock-acquisition order
# --------------------------------------------------------------------------- #

class _FakeTeamQuery:
    """Routes a ``Team.id == <literal>`` filter to the matching seeded row
    and records the id ``.with_for_update()`` was called with, in call
    order -- mirrors test_bounty_dual_lock_order.py's ``_FakeQuery``."""

    def __init__(self, teams: dict, lock_log: list) -> None:
        self._teams = teams
        self._lock_log = lock_log
        self._match_id = None

    def filter(self, cond):
        rhs = getattr(cond, "right", None)
        self._match_id = getattr(rhs, "value", None)
        return self

    def populate_existing(self):
        return self

    def with_for_update(self, *a, **k):
        if self._match_id is not None:
            self._lock_log.append(self._match_id)
        return self

    def first(self):
        return self._teams.get(self._match_id)


class _FakeSession:
    """Keyed-by-id Team store; records lock order + commit."""

    def __init__(self, *teams: Team) -> None:
        self._teams = {t.id: t for t in teams}
        self.team_lock_log: list = []
        self.committed = False

    def query(self, model):
        assert model is Team
        return _FakeTeamQuery(self._teams, self.team_lock_log)

    def commit(self):
        self.committed = True


def make_team(*, team_id=None, leader_id=None, member_roles=None) -> Team:
    return Team(
        id=team_id or uuid4(),
        name=f"team-{uuid4()}",
        leader_id=leader_id,
        member_roles=member_roles if member_roles is not None else {},
    )


def make_player(player_id=None) -> SimpleNamespace:
    return SimpleNamespace(id=player_id or uuid4())


def _run(coro):
    import asyncio

    return asyncio.run(coro)


# --------------------------------------------------------------------------- #
# Ascending-id lock order, regardless of which side is declarer vs target
# --------------------------------------------------------------------------- #

class TestDeclareWarLockOrder:
    def test_locks_ascending_when_declaring_team_has_the_lower_id(self) -> None:
        low_id, high_id = sorted([uuid4(), uuid4()])
        player = make_player()
        declaring = make_team(team_id=low_id, leader_id=player.id)
        target = make_team(team_id=high_id)
        db = _FakeSession(declaring, target)

        result = _run(declare_war(
            team_id=low_id,
            request=DeclareWarRequest(target_team_id=str(high_id)),
            current_player=player,
            db=db,
        ))

        assert result["success"] is True
        assert db.team_lock_log == [low_id, high_id]

    def test_locks_ascending_when_declaring_team_has_the_higher_id(self) -> None:
        """The role-reversal that would deadlock against the case above if
        locking simply went "declaring-team first" unconditionally -- a
        concurrent fleet-battle round on this SAME pair of teams locks
        strictly ascending-id, regardless of which team is attacker/
        defender."""
        low_id, high_id = sorted([uuid4(), uuid4()])
        player = make_player()
        declaring = make_team(team_id=high_id, leader_id=player.id)
        target = make_team(team_id=low_id)
        db = _FakeSession(declaring, target)

        result = _run(declare_war(
            team_id=high_id,
            request=DeclareWarRequest(target_team_id=str(low_id)),
            current_player=player,
            db=db,
        ))

        assert result["success"] is True
        assert db.team_lock_log == [low_id, high_id]


# --------------------------------------------------------------------------- #
# Preserved status codes -- same inputs, same codes, now reordered around the
# ascending-id lock stage
# --------------------------------------------------------------------------- #

class TestPreservedStatusCodes:
    def test_invalid_target_uuid_returns_400_before_any_lock(self) -> None:
        player = make_player()
        db = _FakeSession()  # no teams seeded -- a lock attempt would 404, not 400

        with pytest.raises(HTTPException) as exc:
            _run(declare_war(
                team_id=uuid4(),
                request=DeclareWarRequest(target_team_id="not-a-uuid"),
                current_player=player,
                db=db,
            ))

        assert exc.value.status_code == 400
        assert exc.value.detail == "Invalid target team ID"
        assert db.team_lock_log == []  # never reached the lock stage

    def test_self_war_returns_400_before_any_lock(self) -> None:
        player = make_player()
        team_id = uuid4()
        db = _FakeSession()  # no teams seeded -- a lock attempt would 404, not 400

        with pytest.raises(HTTPException) as exc:
            _run(declare_war(
                team_id=team_id,
                request=DeclareWarRequest(target_team_id=str(team_id)),
                current_player=player,
                db=db,
            ))

        assert exc.value.status_code == 400
        assert exc.value.detail == "Cannot declare war on your own team"
        assert db.team_lock_log == []  # never reached the lock stage

    def test_declaring_team_not_found_returns_404(self) -> None:
        player = make_player()
        target_id = uuid4()
        target = make_team(team_id=target_id)
        db = _FakeSession(target)  # declaring team absent

        with pytest.raises(HTTPException) as exc:
            _run(declare_war(
                team_id=uuid4(),
                request=DeclareWarRequest(target_team_id=str(target_id)),
                current_player=player,
                db=db,
            ))

        assert exc.value.status_code == 404
        assert exc.value.detail == "Team not found"

    def test_target_team_not_found_returns_404(self) -> None:
        player = make_player()
        team_id = uuid4()
        declaring = make_team(team_id=team_id, leader_id=player.id)
        db = _FakeSession(declaring)  # target team absent

        with pytest.raises(HTTPException) as exc:
            _run(declare_war(
                team_id=team_id,
                request=DeclareWarRequest(target_team_id=str(uuid4())),
                current_player=player,
                db=db,
            ))

        assert exc.value.status_code == 404
        assert exc.value.detail == "Target team not found"

    def test_non_leader_returns_403(self) -> None:
        player = make_player()
        team_id = uuid4()
        target_id = uuid4()
        declaring = make_team(team_id=team_id, leader_id=uuid4())  # NOT player.id
        target = make_team(team_id=target_id)
        db = _FakeSession(declaring, target)

        with pytest.raises(HTTPException) as exc:
            _run(declare_war(
                team_id=team_id,
                request=DeclareWarRequest(target_team_id=str(target_id)),
                current_player=player,
                db=db,
            ))

        assert exc.value.status_code == 403
        assert exc.value.detail == "Only team leader can declare war"

    def test_already_at_war_returns_400(self) -> None:
        player = make_player()
        team_id = uuid4()
        target_id = uuid4()
        declaring = make_team(
            team_id=team_id, leader_id=player.id,
            member_roles={"active_wars": [{"target_team_id": str(target_id)}]},
        )
        target = make_team(team_id=target_id)
        db = _FakeSession(declaring, target)

        with pytest.raises(HTTPException) as exc:
            _run(declare_war(
                team_id=team_id,
                request=DeclareWarRequest(target_team_id=str(target_id)),
                current_player=player,
                db=db,
            ))

        assert exc.value.status_code == 400
        assert exc.value.detail == "Already at war with this team"

    def test_success_records_war_on_both_teams_and_commits(self) -> None:
        player = make_player()
        team_id = uuid4()
        target_id = uuid4()
        declaring = make_team(team_id=team_id, leader_id=player.id)
        target = make_team(team_id=target_id)
        db = _FakeSession(declaring, target)

        result = _run(declare_war(
            team_id=team_id,
            request=DeclareWarRequest(target_team_id=str(target_id), reason="border dispute"),
            current_player=player,
            db=db,
        ))

        assert result["success"] is True
        assert result["message"] == "War declared"
        assert result["war"]["target_team_id"] == str(target_id)
        assert declaring.member_roles["active_wars"][0]["target_team_id"] == str(target_id)
        assert target.member_roles["active_wars"][0]["target_team_id"] == str(team_id)
        assert db.committed is True
