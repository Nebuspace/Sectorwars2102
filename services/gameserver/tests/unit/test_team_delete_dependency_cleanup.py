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

from src.models.fleet import Fleet, FleetStatus
from src.models.message import Message
from src.models.player import Player
from src.models.sector import Sector
from src.models.team import Team
from src.models.team_member import TeamMember, TeamRole
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
    if op_name == "ne":
        # leave_team's new_leader lookup filters TeamMember.player_id !=
        # player_id (WO-TEAM-DELETE-FLEET-GUARD-REVISE part 2/3).
        return row_val != cond.right.value
    raise NotImplementedError(f"unsupported operator {cond.operator!r}")


class _FakeQuery:
    """``model_name``/``lock_log`` (WO-TEAM-DELETE-FLEET-GUARD) let this same
    query stand in for the two NEW Fleet query shapes ``delete_team`` issues
    (an unlocked enumeration + a per-id ``.populate_existing().
    with_for_update()`` re-select, mirroring fleet_service._lock_fleets_
    ascending) and record a global, ordered lock/mutation log -- shared
    across every model queried through the owning ``_FakeSession`` -- so the
    Fleet-lock-before-Player-update ordering can be pinned directly (same
    technique as test_declare_war_lock_order.py's ``team_lock_log``)."""

    def __init__(
        self, rows: List[Any], criteria: Optional[List[Any]] = None, *,
        model_name: Optional[str] = None, lock_log: Optional[List[Any]] = None,
    ) -> None:
        self._rows = rows
        self._criteria = criteria or []
        self._model_name = model_name
        self._lock_log = lock_log if lock_log is not None else []

    def filter(self, *conditions: Any) -> "_FakeQuery":
        return _FakeQuery(
            self._rows, self._criteria + list(conditions),
            model_name=self._model_name, lock_log=self._lock_log,
        )

    def _matching(self) -> List[Any]:
        return [row for row in self._rows if all(_match(row, c) for c in self._criteria)]

    def populate_existing(self) -> "_FakeQuery":
        return self

    def order_by(self, *args: Any, **kwargs: Any) -> "_FakeQuery":
        # leave_team's new_leader lookup chains .order_by(...) before
        # .first() (WO-TEAM-DELETE-FLEET-GUARD-REVISE part 2/3). Every
        # scenario this file exercises produces at most one matching
        # TeamMember row for that query shape (a solo leader has ZERO
        # other members), so sort order is never actually observed --
        # a no-op is sufficient and doesn't need to interpret .desc()/
        # multi-key ordering.
        return self

    def with_for_update(self, *args: Any, **kwargs: Any) -> "_FakeQuery":
        for row in self._matching():
            self._lock_log.append((self._model_name, "LOCK", getattr(row, "id", None)))
        return self

    def first(self) -> Any:
        matches = self._matching()
        return matches[0] if matches else None

    def all(self) -> List[Any]:
        return self._matching()

    def update(self, values: dict, synchronize_session: Any = None) -> int:
        matches = self._matching()
        if matches:
            self._lock_log.append((self._model_name, "UPDATE", len(matches)))
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
        fleets=None, team_members=None, fail_commit: bool = False,
    ) -> None:
        self.teams = list(teams or [])
        self.players = list(players or [])
        self.sectors = list(sectors or [])
        self.messages = list(messages or [])
        self.fleets = list(fleets or [])
        self.team_members = list(team_members or [])
        self.added: List[Any] = []
        self.deleted: List[Any] = []
        self.committed = False
        self.rolled_back = False
        self._fail_commit = fail_commit
        # Ordered (model_name, event, id/count) log shared by every
        # _FakeQuery this session hands out -- WO-TEAM-DELETE-FLEET-GUARD's
        # lock-order pin reads this directly.
        self.lock_log: List[Any] = []

    def query(self, model: Any) -> Any:
        if model is Team:
            return _FakeQuery(self.teams, model_name="Team", lock_log=self.lock_log)
        if model is Player:
            return _FakeQuery(self.players, model_name="Player", lock_log=self.lock_log)
        if model is Sector:
            return _FakeQuery(self.sectors, model_name="Sector", lock_log=self.lock_log)
        if model is Message:
            return _FakeQuery(self.messages, model_name="Message", lock_log=self.lock_log)
        if model is Fleet:
            return _FakeQuery(self.fleets, model_name="Fleet", lock_log=self.lock_log)
        if model is TeamMember:
            return _FakeQuery(self.team_members, model_name="TeamMember", lock_log=self.lock_log)
        raise AssertionError(f"unexpected query for {model!r}")

    def add(self, obj: Any) -> None:
        self.added.append(obj)

    def delete(self, obj: Any) -> None:
        self.deleted.append(obj)
        if obj in self.teams:
            self.teams.remove(obj)
        if obj in self.team_members:
            self.team_members.remove(obj)

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
    base = dict(id=uuid.uuid4(), team_id=None, user_id=uuid.uuid4(), nickname="pilot")
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


def _fleet(**overrides: Any) -> SimpleNamespace:
    base = dict(id=uuid.uuid4(), team_id=None, status=FleetStatus.FORMING.value)
    base.update(overrides)
    return SimpleNamespace(**base)


def _team_member(**overrides: Any) -> SimpleNamespace:
    base = dict(
        id=uuid.uuid4(), team_id=None, player_id=None,
        role=TeamRole.MEMBER.value, joined_at=None,
    )
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


# --------------------------------------------------------------------------- #
# WO-TEAM-DELETE-FLEET-GUARD -- the griefing-exploit guard: delete_team must
# not silently cascade-delete a Fleet that is mid-battle. Without this, the
# cascade (Fleet.team_id is ON DELETE CASCADE) SET-NULLs the live
# FleetBattle's attacker_fleet_id/defender_fleet_id (that FK is ON DELETE
# SET NULL, not RESTRICT -- no IntegrityError to catch), permanently
# orphaning the battle and crashing the surviving player's next
# round-simulate call with an uncaught AttributeError. Mirrors fleet_
# service.disband_fleet's own existing guard shape (fleet_service.py:446-
# 447) and lock idiom (fleet_service.py:652-686 _lock_fleets_ascending).
# --------------------------------------------------------------------------- #

@pytest.mark.unit
class TestDeleteTeamFleetGuard:
    def test_blocked_when_fleet_in_battle(self) -> None:
        leader_id = uuid.uuid4()
        team = _team(leader_id=leader_id)
        member = _player(team_id=team.id)
        fleet = _fleet(team_id=team.id, status=FleetStatus.IN_BATTLE.value)
        db = _FakeSession(teams=[team], players=[member], fleets=[fleet])
        svc = TeamService(db)

        with pytest.raises(
            ValueError, match="Cannot delete team while a fleet is in an active battle"
        ):
            svc.delete_team(team.id, leader_id)

        # True early-exit -- the guard fires BEFORE any other mutation, so
        # nothing downstream of it (Player null, cascade delete, commit) ran.
        assert db.committed is False
        assert db.deleted == []
        assert team in db.teams
        assert member.team_id == team.id  # never nulled

    def test_blocked_when_any_of_several_fleets_is_in_battle(self) -> None:
        """Falsifiability pair for the ``any(...)`` guard: two idle fleets
        plus ONE in-battle fleet must still block -- proving the check
        isn't accidentally scoped to "the first fleet" or requiring ALL
        fleets to be in battle."""
        leader_id = uuid.uuid4()
        team = _team(leader_id=leader_id)
        idle_a = _fleet(team_id=team.id, status=FleetStatus.FORMING.value)
        in_battle = _fleet(team_id=team.id, status=FleetStatus.IN_BATTLE.value)
        idle_b = _fleet(team_id=team.id, status=FleetStatus.READY.value)
        db = _FakeSession(teams=[team], fleets=[idle_a, in_battle, idle_b])
        svc = TeamService(db)

        with pytest.raises(ValueError, match="active battle"):
            svc.delete_team(team.id, leader_id)

        assert db.committed is False
        assert team in db.teams

    def test_succeeds_with_only_idle_fleets(self) -> None:
        """Negative control (the other half of the falsifiability pair) --
        proves the guard doesn't false-positive on a team whose fleets are
        merely FORMING/READY, never IN_BATTLE."""
        leader_id = uuid.uuid4()
        team = _team(leader_id=leader_id)
        idle_a = _fleet(team_id=team.id, status=FleetStatus.FORMING.value)
        idle_b = _fleet(team_id=team.id, status=FleetStatus.READY.value)
        db = _FakeSession(teams=[team], fleets=[idle_a, idle_b])
        svc = TeamService(db)

        result = svc.delete_team(team.id, leader_id)

        assert result is True
        assert db.committed is True
        assert team in db.deleted

    def test_fleet_lock_acquired_before_player_update(self) -> None:
        """Lock-order pin (mirrors test_declare_war_lock_order.py's
        ``team_lock_log`` technique): the new Fleet FOR-UPDATE lock must be
        the FIRST thing this method acquires -- strictly before the
        Player.team_id bulk-null -- so "Fleet-before-everything" holds
        (no caller anywhere locks Team-then-Fleet, so this can't introduce
        a new AB-BA)."""
        leader_id = uuid.uuid4()
        team = _team(leader_id=leader_id)
        member = _player(team_id=team.id)
        # Idle, not IN_BATTLE -- must pass the guard to reach the Player
        # update at all, or there's nothing to compare ordering against.
        fleet = _fleet(team_id=team.id, status=FleetStatus.FORMING.value)
        db = _FakeSession(teams=[team], players=[member], fleets=[fleet])
        svc = TeamService(db)

        result = svc.delete_team(team.id, leader_id)

        assert result is True
        fleet_lock_idx = next(
            i for i, entry in enumerate(db.lock_log) if entry[:2] == ("Fleet", "LOCK")
        )
        player_update_idx = next(
            i for i, entry in enumerate(db.lock_log) if entry[:2] == ("Player", "UPDATE")
        )
        assert fleet_lock_idx < player_update_idx


# --------------------------------------------------------------------------- #
# WO-TEAM-DELETE-FLEET-GUARD-REVISE part 1 -- mack HIGH: the initial
# team_fleet_ids gather is an UNLOCKED, point-in-time .all() snapshot taken
# BEFORE any lock is held. A fleet created for the team strictly AFTER that
# snapshot is never in team_fleet_ids, so the per-id lock loop never touches
# it -- delete_team needs a SEPARATE, later check (scoped directly to
# team_id + status == IN_BATTLE) to still catch it before the cascade.
# --------------------------------------------------------------------------- #

class _RaceInjectingSession(_FakeSession):
    """Reproduces the TOCTOU precisely: the FIRST Fleet query delete_team
    issues (its unlocked ``.all()`` gather) sees the fleet set as it stood
    BEFORE a concurrent fleet was created and entered battle; every Fleet
    query AFTER that (the per-id lock loop, and -- the one under test --
    the final team_id+IN_BATTLE recheck) sees the CURRENT fleets list,
    which already includes the race fleet. A single-threaded fake can't
    model true concurrency, but this reproduces the exact before/after
    snapshot asymmetry a real interleaving would produce, without needing
    threads or a real Postgres lock."""

    def __init__(self, *, race_fleet: Any, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._race_fleet = race_fleet
        self._fleet_query_count = 0

    def query(self, model: Any) -> Any:
        if model is Fleet:
            self._fleet_query_count += 1
            if self._fleet_query_count == 1:
                # The initial gather -- the race fleet doesn't exist "yet".
                visible = [f for f in self.fleets if f is not self._race_fleet]
                return _FakeQuery(visible, model_name="Fleet", lock_log=self.lock_log)
        return super().query(model)


@pytest.mark.unit
class TestDeleteTeamFleetGuardTOCTOU:
    def test_fleet_created_after_initial_gather_still_caught(self) -> None:
        """A fleet that didn't exist yet at delete_team's initial unlocked
        gather, but IS in_battle by the time of the final recheck, must
        still block the delete -- not just fleets the gather already knew
        about. Proves the SEPARATE final recheck (not the per-id loop) is
        what closes this window: team_fleet_ids comes back empty from the
        first query, so the loop's short-circuit never fires here at all."""
        leader_id = uuid.uuid4()
        team = _team(leader_id=leader_id)
        race_fleet = _fleet(team_id=team.id, status=FleetStatus.IN_BATTLE.value)
        db = _RaceInjectingSession(teams=[team], fleets=[race_fleet], race_fleet=race_fleet)
        svc = TeamService(db)

        with pytest.raises(
            ValueError, match="Cannot delete team while a fleet is in an active battle"
        ):
            svc.delete_team(team.id, leader_id)

        assert db.committed is False
        assert team in db.teams
        assert team not in db.deleted


# --------------------------------------------------------------------------- #
# WO-TEAM-DELETE-FLEET-GUARD-REVISE part 2 (cipher CONFIRMED HIGH) + part 3
# (orchestrator MANDATE, the un-masking regression test) -- leave_team's
# solo-leader disband branch now calls delete_team instead of an inline,
# unguarded db.delete(team), porting the Fleet IN_BATTLE guard + Part-1
# TOCTOU close + Sector/Message FK-cleanup + IntegrityError backstop + its
# own DELETE audit log atomically. Before this, the branch was a
# structurally-identical duplicate of the exact griefing exploit
# delete_team was hardened against -- AND it accidentally fail-closed today
# (a "Member Left" Message INSERT would FK-violate before the DELETE,
# raw-500ing every solo-leave that had ever sent team chat), so porting
# ONLY the FK-cleanup half without the guard would have UN-MASKED the
# exploit (see missing-commit-masks-a-race: fixing one bug can silently
# re-open a different one it was accidentally suppressing).
# --------------------------------------------------------------------------- #

@pytest.mark.unit
class TestLeaveTeamSoloDisbandReuse:
    def test_solo_leader_leave_disbands_via_delete_team(self) -> None:
        """Happy path: the reuse actually works end-to-end, including
        porting the Sector/Message FK-cleanup delete_team already had --
        the SAME raw-500 bug WO-TEAM-DELETE-GUARD fixed there is now ALSO
        fixed here, for free, via the shared code path."""
        leader_id = uuid.uuid4()
        team = _team(leader_id=leader_id)
        leader_player = _player(id=leader_id, team_id=team.id)
        leader_member = _team_member(
            team_id=team.id, player_id=leader_id, role=TeamRole.LEADER.value
        )
        controlled = _sector(controlling_team_id=team.id)
        team_msg = _message(team_id=team.id, content="gg")
        db = _FakeSession(
            teams=[team], players=[leader_player], team_members=[leader_member],
            sectors=[controlled], messages=[team_msg],
        )
        svc = TeamService(db)

        result = svc.leave_team(leader_id)

        assert result is True
        # Ported straight from delete_team -- the team itself is gone...
        assert team in db.deleted
        assert team not in db.teams
        assert db.committed is True
        assert leader_player.team_id is None
        # ...and the FK cleanup that ONLY delete_team used to have now
        # ran here too.
        assert controlled.controlling_team_id is None
        assert team_msg.team_id is None
        assert team_msg not in db.deleted

    def test_solo_leader_leave_blocked_when_fleet_in_battle(self) -> None:
        """THE un-masking invariant (orchestrator MANDATE). Exactly cipher's
        flagged scenario: a solo-leader's fleet is IN_BATTLE and they call
        leave_team. This MUST be rejected with the same guard delete_team
        enforces -- no disband, no orphaned FleetBattle, team survives.
        Falsifiability-checked by hand: with team_service.py's IN_BATTLE
        guard temporarily neutralized, this exact test fails (the disband
        proceeds and `team in db.deleted` becomes true) -- see the
        SendMessage report for the before/after run."""
        leader_id = uuid.uuid4()
        team = _team(leader_id=leader_id)
        leader_player = _player(id=leader_id, team_id=team.id)
        leader_member = _team_member(
            team_id=team.id, player_id=leader_id, role=TeamRole.LEADER.value
        )
        fleet = _fleet(team_id=team.id, status=FleetStatus.IN_BATTLE.value)
        db = _FakeSession(
            teams=[team], players=[leader_player], team_members=[leader_member],
            fleets=[fleet],
        )
        svc = TeamService(db)

        with pytest.raises(
            ValueError, match="Cannot delete team while a fleet is in an active battle"
        ):
            svc.leave_team(leader_id)

        # No disband happened -- the battle isn't orphaned, the team
        # survives, and nothing committed.
        assert db.committed is False
        assert team in db.teams
        assert team not in db.deleted
        assert leader_member not in db.deleted
        assert leader_player.team_id == team.id  # never nulled
