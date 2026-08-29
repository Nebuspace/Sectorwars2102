"""Soft-ORDER LEG-2033/#2113 — team-owned station LEADER/OFFICER + member share.

Invent=0: mode=team bind; LEADER|OFFICER configure; member revenue share on
withdraw (90% cushion); live roster = membership-loss clear; team disband →
depreciated force-sell (no insolvency rep).
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Dict, List, Optional
from unittest.mock import patch
from uuid import UUID, uuid4

import pytest

from src.models.player import Player
from src.models.station import Station
from src.models.team import Team
from src.models.team_member import TeamMember, TeamRole
from src.services import port_ownership_service as po
from src.services.port_ownership_service import PortOwnershipError


class _FakeQuery:
    def __init__(self, rows: List[Any], model_name: str = ""):
        self._rows = list(rows)
        self._filters: List[Any] = []
        self.model_name = model_name

    def filter(self, *args: Any) -> "_FakeQuery":
        self._filters.extend(args)
        return self

    def populate_existing(self) -> "_FakeQuery":
        return self

    def with_for_update(self) -> "_FakeQuery":
        return self

    def first(self) -> Any:
        return self._match_one()

    def all(self) -> List[Any]:
        return self._match_many()

    def _attrs(self, obj: Any) -> Dict[str, Any]:
        if isinstance(obj, dict):
            return obj
        return getattr(obj, "__dict__", {})

    def _match_many(self) -> List[Any]:
        # Soft-ORDER scans: TeamMember role.in_ / team_id; Station.all();
        # Player by id. Filters are SQLAlchemy BinaryExpression — compare via
        # string/repr heuristics is fragile, so prefer role/id pre-filtered
        # collections on the session when present; else return all rows.
        return list(self._rows)

    def _match_one(self) -> Any:
        rows = self._match_many()
        return rows[0] if rows else None


class _TeamOwnedFakeSession:
    """Minimal FakeSession for LEG-2033 Soft-ORDER unit paths."""

    def __init__(
        self,
        *,
        stations: Optional[List[Any]] = None,
        players: Optional[Dict[UUID, Any]] = None,
        teams: Optional[Dict[UUID, Any]] = None,
        members: Optional[List[Any]] = None,
    ):
        self.stations = list(stations or [])
        self.players = dict(players or {})
        self.teams = dict(teams or {})
        self.members = list(members or [])
        self.flushed = 0

    def query(self, model: Any) -> _FakeQuery:
        if model is Station:
            return _StationQuery(self.stations, session=self)
        if model is Player:
            return _PlayerQuery(self.players)
        if model is Team:
            return _TeamQuery(self.teams)
        if model is TeamMember:
            return _MemberQuery(self.members)
        raise AssertionError(f"unexpected query for {model!r}")

    def flush(self) -> None:
        self.flushed += 1

    def execute(self, *_a: Any, **_k: Any) -> None:
        return None

    def add(self, *_a: Any, **_k: Any) -> None:
        return None


class _StationQuery(_FakeQuery):
    def __init__(self, rows: List[Any], session: _TeamOwnedFakeSession):
        super().__init__(rows, model_name="Station")
        self._session = session
        self._want_id: Optional[UUID] = None

    def filter(self, *args: Any) -> "_StationQuery":
        # Capture Station.id == X when present on SimpleNamespace stations.
        for a in args:
            left = getattr(a, "left", None)
            right = getattr(a, "right", None)
            key = getattr(left, "key", None) or getattr(left, "name", None)
            if key == "id" and right is not None:
                val = getattr(right, "value", right)
                try:
                    self._want_id = UUID(str(val)) if not isinstance(val, UUID) else val
                except (TypeError, ValueError):
                    self._want_id = val
        return self

    def first(self) -> Any:
        if self._want_id is not None:
            for s in self._rows:
                if getattr(s, "id", None) == self._want_id:
                    return s
            return None
        return self._rows[0] if self._rows else None

    def all(self) -> List[Any]:
        return list(self._rows)


class _PlayerQuery(_FakeQuery):
    def __init__(self, players: Dict[UUID, Any]):
        super().__init__(list(players.values()), model_name="Player")
        self._players = players
        self._want_id: Optional[UUID] = None

    def filter(self, *args: Any) -> "_PlayerQuery":
        for a in args:
            left = getattr(a, "left", None)
            right = getattr(a, "right", None)
            key = getattr(left, "key", None) or getattr(left, "name", None)
            if key == "id" and right is not None:
                val = getattr(right, "value", right)
                try:
                    self._want_id = UUID(str(val)) if not isinstance(val, UUID) else val
                except (TypeError, ValueError):
                    self._want_id = val
        return self

    def first(self) -> Any:
        if self._want_id is not None:
            return self._players.get(self._want_id)
        return None


class _TeamQuery(_FakeQuery):
    def __init__(self, teams: Dict[UUID, Any]):
        super().__init__(list(teams.values()), model_name="Team")
        self._teams = teams
        self._want_id: Optional[UUID] = None

    def filter(self, *args: Any) -> "_TeamQuery":
        for a in args:
            left = getattr(a, "left", None)
            right = getattr(a, "right", None)
            key = getattr(left, "key", None) or getattr(left, "name", None)
            if key == "id" and right is not None:
                val = getattr(right, "value", right)
                try:
                    self._want_id = UUID(str(val)) if not isinstance(val, UUID) else val
                except (TypeError, ValueError):
                    self._want_id = val
        return self

    def first(self) -> Any:
        if self._want_id is not None:
            return self._teams.get(self._want_id)
        return None


def _clause_key(expr: Any) -> Optional[str]:
    left = getattr(expr, "left", None)
    return getattr(left, "key", None) or getattr(left, "name", None)


def _clause_value(expr: Any) -> Any:
    right = getattr(expr, "right", None)
    return getattr(right, "value", right)


def _in_values(expr: Any) -> Optional[set]:
    """Extract values from a SQLAlchemy ``col.in_([...])`` expression."""
    right = getattr(expr, "right", None)
    if right is None:
        return None
    # Clauselist / BindParameter list
    clauses = getattr(right, "clauses", None)
    if clauses is not None:
        out = set()
        for c in clauses:
            out.add(getattr(c, "value", c))
        return out
    if isinstance(right, (list, tuple, set)):
        return set(right)
    val = getattr(right, "value", None)
    if isinstance(val, (list, tuple, set)):
        return set(val)
    return None


class _MemberQuery(_FakeQuery):
    def __init__(self, members: List[Any]):
        super().__init__(members, model_name="TeamMember")
        self._want_team: Optional[UUID] = None
        self._want_player: Optional[UUID] = None
        self._want_roles: Optional[set] = None

    def filter(self, *args: Any) -> "_MemberQuery":
        for a in args:
            key = _clause_key(a)
            op = getattr(a, "operator", None)
            op_name = getattr(op, "__name__", str(op)) if op is not None else ""
            if key == "team_id":
                val = _clause_value(a)
                try:
                    self._want_team = UUID(str(val)) if not isinstance(val, UUID) else val
                except (TypeError, ValueError):
                    self._want_team = val
            elif key == "player_id":
                val = _clause_value(a)
                try:
                    self._want_player = (
                        UUID(str(val)) if not isinstance(val, UUID) else val
                    )
                except (TypeError, ValueError):
                    self._want_player = val
            elif key == "role" or "in_op" in op_name or op_name == "in_op":
                vals = _in_values(a)
                if vals:
                    self._want_roles = {str(v) for v in vals}
            # Belt: string form of IN (MEMBER/RECRUIT)
            clause = str(a)
            if "IN" in clause.upper() and (
                "MEMBER" in clause or "RECRUIT" in clause
            ):
                self._want_roles = {TeamRole.MEMBER.value, TeamRole.RECRUIT.value}
        return self

    def first(self) -> Any:
        for m in self._rows:
            if self._want_team is not None and getattr(m, "team_id", None) != self._want_team:
                continue
            if self._want_player is not None and getattr(m, "player_id", None) != self._want_player:
                continue
            return m
        return None

    def all(self) -> List[Any]:
        out = []
        for m in self._rows:
            if self._want_team is not None and getattr(m, "team_id", None) != self._want_team:
                continue
            if self._want_roles is not None and str(getattr(m, "role", "")) not in self._want_roles:
                continue
            out.append(m)
        return out


def _station(
    *,
    owner_id: UUID,
    ownership: Optional[Dict[str, Any]] = None,
    treasury: int = 100_000,
    station_id: Optional[UUID] = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=station_id or uuid4(),
        name="TeamPort",
        owner_id=owner_id,
        ownership=dict(ownership or {}),
        treasury_balance=treasury,
        # Non-listable defaults — force-sell still clears ownership.
        is_player_ownable=False,
        is_destroyed=False,
        station_class=0,
        is_spacedock=False,
        tradedock_tier=None,
        is_quest_hub=False,
        is_faction_headquarters=False,
        services={},
    )


def _member(team_id: UUID, player_id: UUID, role: str) -> SimpleNamespace:
    return SimpleNamespace(team_id=team_id, player_id=player_id, role=role)


class TestBindStationToTeam:
    def test_leader_binds_solo_to_team(self):
        leader_id = uuid4()
        team_id = uuid4()
        station = _station(owner_id=leader_id)
        leader = SimpleNamespace(id=leader_id, credits=0)
        team = SimpleNamespace(id=team_id)
        members = [_member(team_id, leader_id, TeamRole.LEADER.value)]
        db = _TeamOwnedFakeSession(
            stations=[station],
            players={leader_id: leader},
            teams={team_id: team},
            members=members,
        )
        with patch.object(po, "_lock_station", return_value=station), patch.object(
            po, "flag_modified"
        ):
            result = po.bind_station_to_team(db, station, leader, team_id, member_share_pct=20)
        assert result["mode"] == po.TEAM_MODE
        assert result["team_id"] == str(team_id)
        assert result["member_share_pct"] == 20
        assert station.ownership[po.SYNDICATE_MODE_KEY] == po.TEAM_MODE
        assert station.ownership[po.TEAM_ID_KEY] == str(team_id)

    def test_officer_cannot_bind(self):
        owner_id = uuid4()
        officer_id = uuid4()
        team_id = uuid4()
        station = _station(owner_id=owner_id)
        # Bind requires _require_owner first — officer who is not owner_id fails 403.
        officer = SimpleNamespace(id=officer_id, credits=0)
        db = _TeamOwnedFakeSession(
            stations=[station],
            teams={team_id: SimpleNamespace(id=team_id)},
            members=[_member(team_id, officer_id, TeamRole.OFFICER.value)],
        )
        with patch.object(po, "_lock_station", return_value=station):
            with pytest.raises(PortOwnershipError) as exc:
                po.bind_station_to_team(db, station, officer, team_id, 10)
        assert exc.value.status_code == 403


class TestConfigAuthority:
    def test_officer_can_set_tax_member_cannot(self):
        leader_id = uuid4()
        officer_id = uuid4()
        member_id = uuid4()
        team_id = uuid4()
        station = _station(
            owner_id=leader_id,
            ownership={
                po.SYNDICATE_MODE_KEY: po.TEAM_MODE,
                po.TEAM_ID_KEY: str(team_id),
                po.TEAM_MEMBER_SHARE_PCT_KEY: 25,
            },
        )
        members = [
            _member(team_id, leader_id, TeamRole.LEADER.value),
            _member(team_id, officer_id, TeamRole.OFFICER.value),
            _member(team_id, member_id, TeamRole.MEMBER.value),
        ]
        officer = SimpleNamespace(id=officer_id)
        member = SimpleNamespace(id=member_id)
        db = _TeamOwnedFakeSession(stations=[station], members=members)

        with patch.object(po, "_lock_station", return_value=station), patch.object(
            po, "flag_modified"
        ):
            out = po.set_tax_rate(db, station, officer, 0.10)
        assert out["tax_rate"] == 0.10
        assert station.tax_rate == 0.10

        with patch.object(po, "_lock_station", return_value=station), patch.object(
            po, "flag_modified"
        ):
            with pytest.raises(PortOwnershipError) as exc:
                po.set_tax_rate(db, station, member, 0.12)
        assert exc.value.status_code == 403
        assert "LEADER or OFFICER" in exc.value.detail


class TestTeamWithdrawShare:
    def test_member_share_payout_and_membership_loss_clears(self):
        leader_id = uuid4()
        member_a = uuid4()
        member_b = uuid4()
        team_id = uuid4()
        station = _station(
            owner_id=leader_id,
            treasury=100_000,
            ownership={
                po.SYNDICATE_MODE_KEY: po.TEAM_MODE,
                po.TEAM_ID_KEY: str(team_id),
                po.TEAM_MEMBER_SHARE_PCT_KEY: 40,
            },
        )
        players = {
            leader_id: SimpleNamespace(id=leader_id, credits=0),
            member_a: SimpleNamespace(id=member_a, credits=0),
            member_b: SimpleNamespace(id=member_b, credits=0),
        }
        members = [
            _member(team_id, leader_id, TeamRole.LEADER.value),
            _member(team_id, member_a, TeamRole.MEMBER.value),
            _member(team_id, member_b, TeamRole.MEMBER.value),
        ]
        leader = players[leader_id]
        db = _TeamOwnedFakeSession(
            stations=[station], players=players, members=members
        )

        with patch.object(po, "_lock_station", return_value=station), patch.object(
            po, "flag_modified"
        ):
            result = po.withdraw_treasury(db, station, leader, 10_000)

        assert result["withdrawn"] == 10_000
        assert station.treasury_balance == 90_000
        # 40% = 4000 split equally → 2000 each member; leader keeps 6000
        by_id = {d["player_id"]: d["credits"] for d in result["distributions"]}
        assert by_id[str(member_a)] == 2000
        assert by_id[str(member_b)] == 2000
        assert by_id[str(leader_id)] == 6000
        assert players[member_a].credits == 2000
        assert players[leader_id].credits == 6000

        # Membership loss: drop member_b from live roster → next withdraw skips them.
        db.members = [
            _member(team_id, leader_id, TeamRole.LEADER.value),
            _member(team_id, member_a, TeamRole.MEMBER.value),
        ]
        players[leader_id].credits = 0
        players[member_a].credits = 0
        players[member_b].credits = 0
        station.treasury_balance = 100_000
        with patch.object(po, "_lock_station", return_value=station), patch.object(
            po, "flag_modified"
        ):
            result2 = po.withdraw_treasury(db, station, leader, 10_000)
        by_id2 = {d["player_id"]: d["credits"] for d in result2["distributions"]}
        assert str(member_b) not in by_id2
        assert by_id2[str(member_a)] == 4000  # full 40% pool to sole member
        assert by_id2[str(leader_id)] == 6000


class TestTeamDisbandForceSell:
    def test_force_sell_clears_team_ownership(self):
        leader_id = uuid4()
        team_id = uuid4()
        other_team = uuid4()
        owned = _station(
            owner_id=leader_id,
            ownership={
                po.SYNDICATE_MODE_KEY: po.TEAM_MODE,
                po.TEAM_ID_KEY: str(team_id),
                po.TEAM_MEMBER_SHARE_PCT_KEY: 15,
                "acquisition_cost": 1_000_000,
            },
        )
        other = _station(
            owner_id=leader_id,
            ownership={
                po.SYNDICATE_MODE_KEY: po.TEAM_MODE,
                po.TEAM_ID_KEY: str(other_team),
            },
        )
        solo = _station(owner_id=leader_id, ownership={po.SYNDICATE_MODE_KEY: "solo"})
        db = _TeamOwnedFakeSession(stations=[owned, other, solo])

        with patch.object(
            po,
            "_lock_station",
            side_effect=lambda _db, sid: owned if sid == owned.id else other,
        ), patch.object(po, "flag_modified"):
            results = po.force_sell_stations_for_team_disband(db, team_id)

        assert len(results) == 1
        assert results[0]["station_id"] == str(owned.id)
        assert results[0]["action"] == "depreciated_auto_sell"
        assert owned.owner_id is None
        assert po.TEAM_ID_KEY not in (owned.ownership or {})
        assert (owned.ownership or {}).get("released_reason")
        # Untouched siblings
        assert other.owner_id == leader_id
        assert solo.owner_id == leader_id
