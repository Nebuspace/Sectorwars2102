"""WO-TEAM-DELETE-GUARD -- ``TeamService.delete_team`` no longer raw-500s on
foreseeable FK dependencies.

Root cause (confirmed via the model/migration FK map before writing this
fix): most ``teams.id`` references are already orphan-safe --
``TeamMember``/``TeamReputation``/``Fleet`` cascade-delete via the ``Team``
model's own ``cascade="all, delete-orphan"`` relationships (mirrored by an
``ON DELETE CASCADE`` at the DB level), and ``Player.team_id`` /
``Drone.team_id`` / ``PirateKillLog.attacker_team_id`` /
``CargoWreck.original_team_id`` all carry ``ON DELETE SET NULL``. Two did
NOT: ``Sector.controlling_team_id`` and ``Message.team_id`` were both
created with no ``ondelete`` action at all (initial-schema migration
``c138b33baec4``, confirmed against the live FK constraints, not just the
ORM model) -- Postgres's default ``NO ACTION`` rejects the ``DELETE FROM
teams`` with an uncaught ``IntegrityError`` the instant a team controls a
sector or has ever sent a team-chat message, which the route's blanket
``except Exception -> 500`` turns into a raw 500.

DB-free, bespoke fake Session (same established pattern as
test_message_beacon_lifecycle.py's ``_FakeQuery``/``_FakeSession`` +
row-matching interpreter): real service function
(``TeamService.delete_team``), fake rows, real SQLAlchemy column
expressions doing the filtering.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any, List, Optional

import pytest
from sqlalchemy.exc import IntegrityError

from src.models.message import Message
from src.models.player import Player
from src.models.sector import Sector
from src.models.team import Team
from src.services.team_service import TeamService


# --------------------------------------------------------------------------- #
# DB-free fake session
# --------------------------------------------------------------------------- #

def _match(row: Any, cond: Any) -> bool:
    col_name = cond.left.key
    row_val = getattr(row, col_name, None)
    op_name = getattr(cond.operator, "__name__", None)
    if op_name == "eq":
        return row_val == cond.right.value
    raise NotImplementedError(f"unsupported operator {cond.operator!r}")


class _FakeQuery:
    def __init__(self, rows: List[Any], criteria: Optional[List[Any]] = None) -> None:
        self._rows = rows
        self._criteria = criteria or []

    def filter(self, *conditions: Any) -> "_FakeQuery":
        return _FakeQuery(self._rows, self._criteria + list(conditions))

    def _matching(self) -> List[Any]:
        return [row for row in self._rows if all(_match(row, c) for c in self._criteria)]

    def first(self) -> Any:
        matches = self._matching()
        return matches[0] if matches else None

    def all(self) -> List[Any]:
        return self._matching()

    def update(self, values: dict, synchronize_session: Any = None) -> int:
        matches = self._matching()
        for row in matches:
            for col, val in values.items():
                # team_service.py's own .update() calls pass plain string
                # keys (e.g. {"team_id": None}), not Column objects.
                col_name = col.key if hasattr(col, "key") else col
                setattr(row, col_name, val)
        return len(matches)


class _FakeSession:
    def __init__(
        self, *, teams=None, players=None, sectors=None, messages=None,
        fail_commit: bool = False,
    ) -> None:
        self.teams = list(teams or [])
        self.players = list(players or [])
        self.sectors = list(sectors or [])
        self.messages = list(messages or [])
        self.added: List[Any] = []
        self.deleted: List[Any] = []
        self.committed = False
        self.rolled_back = False
        self._fail_commit = fail_commit

    def query(self, model: Any) -> Any:
        if model is Team:
            return _FakeQuery(self.teams)
        if model is Player:
            return _FakeQuery(self.players)
        if model is Sector:
            return _FakeQuery(self.sectors)
        if model is Message:
            return _FakeQuery(self.messages)
        raise AssertionError(f"unexpected query for {model!r}")

    def add(self, obj: Any) -> None:
        self.added.append(obj)

    def delete(self, obj: Any) -> None:
        self.deleted.append(obj)
        if obj in self.teams:
            self.teams.remove(obj)

    def commit(self) -> None:
        if self._fail_commit:
            raise IntegrityError("DELETE FROM teams", {}, Exception("fk violation"))
        self.committed = True

    def rollback(self) -> None:
        self.rolled_back = True


# --------------------------------------------------------------------------- #
# Fixture builders
# --------------------------------------------------------------------------- #

def _team(**overrides: Any) -> SimpleNamespace:
    base = dict(id=uuid.uuid4(), name=f"team-{uuid.uuid4()}", leader_id=uuid.uuid4())
    base.update(overrides)
    return SimpleNamespace(**base)


def _player(**overrides: Any) -> SimpleNamespace:
    base = dict(id=uuid.uuid4(), team_id=None)
    base.update(overrides)
    return SimpleNamespace(**base)


def _sector(**overrides: Any) -> SimpleNamespace:
    base = dict(id=uuid.uuid4(), controlling_team_id=None)
    base.update(overrides)
    return SimpleNamespace(**base)


def _message(**overrides: Any) -> SimpleNamespace:
    base = dict(id=uuid.uuid4(), team_id=None, content="gg")
    base.update(overrides)
    return SimpleNamespace(**base)


# --------------------------------------------------------------------------- #
# The confirmed bug's fix: no raw 500, no orphaned FK
# --------------------------------------------------------------------------- #

@pytest.mark.unit
class TestDeleteTeamDependencyCleanup:
    def test_relinquishes_sector_control_and_detaches_messages_then_succeeds(self) -> None:
        leader_id = uuid.uuid4()
        team = _team(leader_id=leader_id)
        member = _player(team_id=team.id)
        controlled = _sector(controlling_team_id=team.id)
        untouched_sector = _sector(controlling_team_id=uuid.uuid4())
        team_msg = _message(team_id=team.id, content="gg all")
        other_team_msg = _message(team_id=uuid.uuid4())
        db = _FakeSession(
            teams=[team], players=[member], sectors=[controlled, untouched_sector],
            messages=[team_msg, other_team_msg],
        )
        svc = TeamService(db)

        result = svc.delete_team(team.id, leader_id)

        assert result is True
        # No raw exception escaped -- this is the "no raw 500" proof.
        assert db.committed is True
        # Member's team_id cleared (pre-existing behavior, preserved).
        assert member.team_id is None
        # NEW: sector control relinquished, not left dangling.
        assert controlled.controlling_team_id is None
        # NEW: an unrelated sector's control is untouched.
        assert untouched_sector.controlling_team_id is not None
        # NEW: team chat detached (FK cleared) but the row -- and its
        # content -- survives, per messaging.md's audit-trail preservation.
        assert team_msg.team_id is None
        assert team_msg.content == "gg all"
        assert team_msg not in db.deleted
        # An unrelated team's message is untouched.
        assert other_team_msg.team_id is not None
        # The team itself is gone.
        assert team in db.deleted
        assert team not in db.teams

    def test_no_dependencies_still_succeeds(self) -> None:
        """A team with none of the four dependency tables populated must
        keep working exactly as before this fix."""
        leader_id = uuid.uuid4()
        team = _team(leader_id=leader_id)
        db = _FakeSession(teams=[team])
        svc = TeamService(db)

        result = svc.delete_team(team.id, leader_id)

        assert result is True
        assert db.committed is True
        assert team in db.deleted


# --------------------------------------------------------------------------- #
# Preserved behavior -- authorization + not-found untouched
# --------------------------------------------------------------------------- #

@pytest.mark.unit
class TestPreservedAuthorizationAndNotFound:
    def test_team_not_found_raises_value_error(self) -> None:
        db = _FakeSession()  # no teams seeded
        svc = TeamService(db)

        with pytest.raises(ValueError, match="Team not found"):
            svc.delete_team(uuid.uuid4(), uuid.uuid4())

        assert db.committed is False
        assert db.deleted == []

    def test_non_leader_raises_value_error(self) -> None:
        team = _team(leader_id=uuid.uuid4())
        db = _FakeSession(teams=[team])
        svc = TeamService(db)

        with pytest.raises(ValueError, match="Only team leader can delete the team"):
            svc.delete_team(team.id, uuid.uuid4())  # NOT the leader

        assert db.committed is False
        assert db.deleted == []
        assert team in db.teams  # never touched


# --------------------------------------------------------------------------- #
# Belt-and-suspenders: any UNANTICIPATED FK dependency still can't leak a raw
# 500 -- IntegrityError on commit becomes a clean ValueError (route already
# maps ValueError -> a 4xx).
# --------------------------------------------------------------------------- #

@pytest.mark.unit
class TestUnhandledDependencyBackstop:
    def test_integrity_error_on_commit_becomes_value_error_not_raw_500(self) -> None:
        leader_id = uuid.uuid4()
        team = _team(leader_id=leader_id)
        db = _FakeSession(teams=[team], fail_commit=True)
        svc = TeamService(db)

        with pytest.raises(ValueError, match="dependent records"):
            svc.delete_team(team.id, leader_id)

        assert db.rolled_back is True
        assert db.committed is False
