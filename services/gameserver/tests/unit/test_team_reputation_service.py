"""Unit tests for team_reputation_service (WO-RT-TEAM-REP).

DB-free, bespoke fake Session (same established pattern as
test_pirate_ecosystem_dynamics.py's ``_FakeQuery``/``_FakeSession`` +
``_eval_clause``) scoped to this module's actual query surface:
TeamReputation, Team, TeamMember, Reputation, Faction.

Telemetry is proven the same way as pirate_ecosystem_service's own tests: a
``_capture_broadcasts(monkeypatch)`` helper replaces the ONE shared
``_broadcast_team_event`` with a plain recorder, bypassing asyncio entirely
(a plain sync test has no running event loop, so the real transport always
silently no-ops there anyway -- see
reference_ws_telemetry_test_via_shared_broadcast_helper.md).
"""

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from src.models.faction import Faction, FactionType
from src.models.reputation import Reputation, ReputationLevel, TeamReputation
from src.models.team import Team, TeamReputationHandling
from src.models.team_member import TeamMember
from src.services import team_reputation_service as trs


# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------

def _team(*, leader_id=None, team_id=None):
    return Team(
        id=team_id or uuid.uuid4(),
        name=f"t-{uuid.uuid4().hex[:8]}",
        leader_id=leader_id,
    )


def _team_member(*, team_id, player_id):
    return TeamMember(id=uuid.uuid4(), team_id=team_id, player_id=player_id)


def _faction(*, faction_id=None, name="Terran Federation"):
    return Faction(id=faction_id or uuid.uuid4(), name=name, faction_type=FactionType.FEDERATION)


def _reputation(*, player_id, faction_id, current_value=0):
    return Reputation(
        id=uuid.uuid4(), player_id=player_id, faction_id=faction_id,
        current_value=current_value, current_level=ReputationLevel.NEUTRAL,
    )


def _team_reputation(
    *, team_id, method="AVERAGE", faction_reputation=None, history=None,
    last_recalculated=None, next_recalculation=None, pending_notifications=None,
):
    now = datetime.now(timezone.utc)
    return TeamReputation(
        id=uuid.uuid4(), team_id=team_id, calculation_method=method,
        faction_reputation=faction_reputation or {}, history=history or [],
        last_recalculated=last_recalculated or now,
        next_recalculation=next_recalculation or now,
        pending_notifications=pending_notifications or [],
    )


def _capture_broadcasts(monkeypatch):
    """Bypasses asyncio entirely -- monkeypatches the ONE shared
    ``_broadcast_team_event`` helper to a plain recorder."""
    calls: list = []
    monkeypatch.setattr(
        trs, "_broadcast_team_event",
        lambda team_id, payload: calls.append((team_id, payload)),
    )
    return calls


# ---------------------------------------------------------------------------
# Bespoke fake Session -- scoped to team_reputation_service's actual query
# surface (TeamReputation / Team / TeamMember / Reputation / Faction).
# Mirrors test_pirate_ecosystem_dynamics.py's _eval_clause / _FakeQuery /
# _FakeSession pattern.
# ---------------------------------------------------------------------------

def _table_name(entity):
    tbl = getattr(entity, "__table__", None)
    if tbl is not None:
        return tbl.name
    cls = getattr(entity, "class_", None)
    if cls is not None:
        return cls.__table__.name
    tbl2 = getattr(entity, "table", None)
    if tbl2 is not None and hasattr(tbl2, "name"):
        return tbl2.name
    for child in entity.get_children():
        found = _table_name(child)
        if found:
            return found
    return None


def _eval_clause(cond, row):
    key = cond.left.key
    value = getattr(row, key, None)
    opname = getattr(cond.operator, "__name__", None)
    rhs = cond.right.value if hasattr(cond.right, "value") else cond.right

    if opname == "eq":
        return value == rhs
    if opname == "in_op":
        return value in rhs
    if opname == "ge":
        return value is not None and value >= rhs
    if opname == "le":
        return value is not None and value <= rhs
    if opname == "is_":
        return value is None
    if opname == "is_not":
        return value is not None
    raise NotImplementedError(f"fake query: unsupported operator {cond.operator!r}")


class _FakeQuery:
    def __init__(self, rows, entities):
        self._rows = rows
        self._entities = entities
        self._criteria = []

    def filter(self, *criteria):
        self._criteria.extend(criteria)
        return self

    def _matches(self, row):
        return all(_eval_clause(c, row) for c in self._criteria)

    def all(self):
        matched = [r for r in self._rows if self._matches(r)]
        if (
            len(self._entities) == 1
            and hasattr(self._entities[0], "key")
            and hasattr(self._entities[0], "class_")
        ):
            key = self._entities[0].key
            return [(getattr(r, key),) for r in matched]
        return matched

    def first(self):
        matched = [r for r in self._rows if self._matches(r)]
        return matched[0] if matched else None


class _FakeSession:
    def __init__(self, *, team_reputations=None, teams=None, team_members=None,
                 reputations=None, factions=None):
        self._by_table = {
            "team_reputations": list(team_reputations or []),
            "teams": list(teams or []),
            "team_members": list(team_members or []),
            "reputations": list(reputations or []),
            "factions": list(factions or []),
        }
        self.flush_count = 0

    def query(self, *entities):
        name = _table_name(entities[0])
        return _FakeQuery(self._by_table[name], entities)

    def add(self, obj):
        if getattr(obj, "id", None) is None:
            obj.id = uuid.uuid4()
        self._by_table[obj.__table__.name].append(obj)

    def flush(self):
        self.flush_count += 1


# ---------------------------------------------------------------------------
# _reputation_level_for (pure)
# ---------------------------------------------------------------------------

class TestReputationLevelFor:
    def test_matches_faction_service_thresholds(self):
        assert trs._reputation_level_for(0) == ReputationLevel.NEUTRAL
        assert trs._reputation_level_for(50) == ReputationLevel.RECOGNIZED
        assert trs._reputation_level_for(-50) == ReputationLevel.NEUTRAL
        assert trs._reputation_level_for(-51) == ReputationLevel.QUESTIONABLE
        assert trs._reputation_level_for(700) == ReputationLevel.EXALTED
        assert trs._reputation_level_for(-800) == ReputationLevel.PUBLIC_ENEMY


# ---------------------------------------------------------------------------
# recalculate_team -- the falsifiable three-method matrix
# ---------------------------------------------------------------------------

class TestRecalculateTeamMethodMatrix:
    def _build_three_member_team(self):
        """leader=200, m2=100, m3=600 -- AVERAGE=300, LOWEST=100, LEADER=200.
        Three DISTINCT values, so the three methods are mutually
        distinguishable (the WO's explicit falsifiability requirement)."""
        leader_id, m2_id, m3_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        team = _team(leader_id=leader_id)
        faction = _faction()
        members = [
            _team_member(team_id=team.id, player_id=leader_id),
            _team_member(team_id=team.id, player_id=m2_id),
            _team_member(team_id=team.id, player_id=m3_id),
        ]
        reputations = [
            _reputation(player_id=leader_id, faction_id=faction.id, current_value=200),
            _reputation(player_id=m2_id, faction_id=faction.id, current_value=100),
            _reputation(player_id=m3_id, faction_id=faction.id, current_value=600),
        ]
        return team, faction, members, reputations

    def test_average_method(self, monkeypatch):
        _capture_broadcasts(monkeypatch)
        team, faction, members, reputations = self._build_three_member_team()
        team_rep = _team_reputation(team_id=team.id, method="AVERAGE")
        db = _FakeSession(
            team_reputations=[team_rep], teams=[team], team_members=members,
            reputations=reputations, factions=[faction],
        )
        result = trs.recalculate_team(db, team)
        assert result["standings"][str(faction.id)]["value"] == 300  # (200+100+600)/3

    def test_lowest_method(self, monkeypatch):
        _capture_broadcasts(monkeypatch)
        team, faction, members, reputations = self._build_three_member_team()
        team_rep = _team_reputation(team_id=team.id, method="LOWEST")
        db = _FakeSession(
            team_reputations=[team_rep], teams=[team], team_members=members,
            reputations=reputations, factions=[faction],
        )
        result = trs.recalculate_team(db, team)
        assert result["standings"][str(faction.id)]["value"] == 100

    def test_leader_method(self, monkeypatch):
        _capture_broadcasts(monkeypatch)
        team, faction, members, reputations = self._build_three_member_team()
        team_rep = _team_reputation(team_id=team.id, method="LEADER")
        db = _FakeSession(
            team_reputations=[team_rep], teams=[team], team_members=members,
            reputations=reputations, factions=[faction],
        )
        result = trs.recalculate_team(db, team)
        assert result["standings"][str(faction.id)]["value"] == 200

    def test_leader_method_with_no_leader_reputation_row_is_neutral(self, monkeypatch):
        _capture_broadcasts(monkeypatch)
        leader_id, m2_id = uuid.uuid4(), uuid.uuid4()
        team = _team(leader_id=leader_id)
        faction = _faction()
        members = [
            _team_member(team_id=team.id, player_id=leader_id),
            _team_member(team_id=team.id, player_id=m2_id),
        ]
        # Only m2 has a Reputation row -- the leader has never interacted
        # with this faction.
        reputations = [_reputation(player_id=m2_id, faction_id=faction.id, current_value=500)]
        team_rep = _team_reputation(team_id=team.id, method="LEADER")
        db = _FakeSession(
            team_reputations=[team_rep], teams=[team], team_members=members,
            reputations=reputations, factions=[faction],
        )
        result = trs.recalculate_team(db, team)
        assert result["standings"][str(faction.id)]["value"] == 0
        assert result["standings"][str(faction.id)]["level"] == ReputationLevel.NEUTRAL.value

    def test_empty_team_degrades_to_neutral(self, monkeypatch):
        _capture_broadcasts(monkeypatch)
        team = _team(leader_id=None)
        faction = _faction()
        team_rep = _team_reputation(team_id=team.id, method="AVERAGE")
        db = _FakeSession(team_reputations=[team_rep], teams=[team], factions=[faction])
        result = trs.recalculate_team(db, team)
        assert result["standings"][str(faction.id)]["value"] == 0

    def test_missing_member_reputation_counts_as_neutral(self, monkeypatch):
        """A member with NO Reputation row for a faction contributes 0
        (NEUTRAL), not exclusion from the average."""
        _capture_broadcasts(monkeypatch)
        leader_id, m2_id = uuid.uuid4(), uuid.uuid4()
        team = _team(leader_id=leader_id)
        faction = _faction()
        members = [
            _team_member(team_id=team.id, player_id=leader_id),
            _team_member(team_id=team.id, player_id=m2_id),
        ]
        reputations = [_reputation(player_id=leader_id, faction_id=faction.id, current_value=200)]
        team_rep = _team_reputation(team_id=team.id, method="AVERAGE")
        db = _FakeSession(
            team_reputations=[team_rep], teams=[team], team_members=members,
            reputations=reputations, factions=[faction],
        )
        result = trs.recalculate_team(db, team)
        assert result["standings"][str(faction.id)]["value"] == 100  # (200+0)/2


class TestRecalculateTeamPersistenceAndLazyInit:
    def test_lazy_creates_team_reputation_row_when_missing(self, monkeypatch):
        _capture_broadcasts(monkeypatch)
        team = _team(leader_id=None)
        faction = _faction()
        db = _FakeSession(teams=[team], factions=[faction])  # no TeamReputation row at all

        assert db.flush_count == 0
        result = trs.recalculate_team(db, team)

        assert db.flush_count >= 1
        assert len(db._by_table["team_reputations"]) == 1
        assert result["method"] == TeamReputationHandling.AVERAGE.value

    def test_next_recalculation_advances_by_the_interval(self, monkeypatch):
        _capture_broadcasts(monkeypatch)
        team = _team(leader_id=None)
        faction = _faction()
        team_rep = _team_reputation(team_id=team.id)
        db = _FakeSession(team_reputations=[team_rep], teams=[team], factions=[faction])
        now = datetime(2026, 7, 9, tzinfo=timezone.utc)

        trs.recalculate_team(db, team, now=now)

        assert team_rep.next_recalculation == now + trs.RECALCULATION_INTERVAL
        assert team_rep.last_recalculated == now

    def test_history_appends_a_recalculation_entry(self, monkeypatch):
        _capture_broadcasts(monkeypatch)
        team = _team(leader_id=None)
        faction = _faction()
        team_rep = _team_reputation(team_id=team.id)
        db = _FakeSession(team_reputations=[team_rep], teams=[team], factions=[faction])

        assert team_rep.history == []
        trs.recalculate_team(db, team)

        assert len(team_rep.history) == 1
        assert team_rep.history[0]["kind"] == "recalculation"
        assert team_rep.history[0]["method"] == "AVERAGE"

    def test_faction_reputation_jsonb_is_reassigned_not_mutated_in_place(self, monkeypatch):
        """JSONB mutation-tracking trap: the column must be REASSIGNED (a
        new dict object), not mutated in place, or SQLAlchemy would never
        detect the change on a real session."""
        _capture_broadcasts(monkeypatch)
        team = _team(leader_id=None)
        faction = _faction()
        team_rep = _team_reputation(team_id=team.id)
        original_dict_id = id(team_rep.faction_reputation)
        db = _FakeSession(team_reputations=[team_rep], teams=[team], factions=[faction])

        trs.recalculate_team(db, team)

        assert id(team_rep.faction_reputation) != original_dict_id
        assert str(faction.id) in team_rep.faction_reputation


class TestRecalculateTeamTierChangeNotifications:
    def test_tier_change_appends_notification_and_emits_frame_exactly_once(self, monkeypatch):
        calls = _capture_broadcasts(monkeypatch)
        leader_id = uuid.uuid4()
        team = _team(leader_id=leader_id)
        faction = _faction()
        # Prior snapshot: NEUTRAL (0). New computation: 300 -> RESPECTED.
        prior_standings = {
            str(faction.id): {
                "faction_id": str(faction.id), "faction_name": faction.name,
                "value": 0, "level": ReputationLevel.NEUTRAL.value,
            }
        }
        team_rep = _team_reputation(
            team_id=team.id, method="LEADER", faction_reputation=prior_standings,
        )
        members = [_team_member(team_id=team.id, player_id=leader_id)]
        reputations = [_reputation(player_id=leader_id, faction_id=faction.id, current_value=300)]
        db = _FakeSession(
            team_reputations=[team_rep], teams=[team], team_members=members,
            reputations=reputations, factions=[faction],
        )

        result = trs.recalculate_team(db, team)

        assert len(result["changed"]) == 1
        assert result["changed"][0]["old_level"] == ReputationLevel.NEUTRAL.value
        assert result["changed"][0]["new_level"] == ReputationLevel.RESPECTED.value
        assert len(team_rep.pending_notifications) == 1

        assert len(calls) == 1
        team_id, payload = calls[0]
        assert team_id == team.id
        assert payload["type"] == "team_reputation_changed"
        assert payload["faction_id"] == str(faction.id)
        assert payload["old_level"] == ReputationLevel.NEUTRAL.value
        assert payload["new_level"] == ReputationLevel.RESPECTED.value

    def test_first_ever_computation_is_not_a_change(self, monkeypatch):
        """A faction with no PRIOR snapshot entry (first computation ever)
        must never be reported as a tier "change" -- there's nothing to
        change FROM."""
        calls = _capture_broadcasts(monkeypatch)
        leader_id = uuid.uuid4()
        team = _team(leader_id=leader_id)
        faction = _faction()
        team_rep = _team_reputation(team_id=team.id, method="LEADER")  # empty faction_reputation
        members = [_team_member(team_id=team.id, player_id=leader_id)]
        reputations = [_reputation(player_id=leader_id, faction_id=faction.id, current_value=300)]
        db = _FakeSession(
            team_reputations=[team_rep], teams=[team], team_members=members,
            reputations=reputations, factions=[faction],
        )

        result = trs.recalculate_team(db, team)

        assert result["changed"] == []
        assert calls == []

    def test_same_tier_recompute_emits_nothing(self, monkeypatch):
        calls = _capture_broadcasts(monkeypatch)
        leader_id = uuid.uuid4()
        team = _team(leader_id=leader_id)
        faction = _faction()
        prior_standings = {
            str(faction.id): {
                "faction_id": str(faction.id), "faction_name": faction.name,
                "value": 300, "level": ReputationLevel.RESPECTED.value,
            }
        }
        team_rep = _team_reputation(
            team_id=team.id, method="LEADER", faction_reputation=prior_standings,
        )
        members = [_team_member(team_id=team.id, player_id=leader_id)]
        # Same tier (still RESPECTED at a different but same-bucket value).
        reputations = [_reputation(player_id=leader_id, faction_id=faction.id, current_value=350)]
        db = _FakeSession(
            team_reputations=[team_rep], teams=[team], team_members=members,
            reputations=reputations, factions=[faction],
        )

        result = trs.recalculate_team(db, team)

        assert result["changed"] == []
        assert calls == []


# ---------------------------------------------------------------------------
# get_team_reputation -- read path + cold-start self-heal
# ---------------------------------------------------------------------------

class TestGetTeamReputation:
    def test_fresh_team_self_heals_into_a_real_computation(self, monkeypatch):
        _capture_broadcasts(monkeypatch)
        leader_id = uuid.uuid4()
        team = _team(leader_id=leader_id)
        faction = _faction()
        members = [_team_member(team_id=team.id, player_id=leader_id)]
        reputations = [_reputation(player_id=leader_id, faction_id=faction.id, current_value=150)]
        db = _FakeSession(teams=[team], team_members=members, reputations=reputations, factions=[faction])

        result = trs.get_team_reputation(db, team)

        assert result["standings"][str(faction.id)]["value"] == 150

    def test_not_due_returns_stored_snapshot_without_recomputing(self, monkeypatch):
        calls = _capture_broadcasts(monkeypatch)
        team = _team(leader_id=None)
        faction = _faction()
        now = datetime(2026, 7, 9, tzinfo=timezone.utc)
        stored_standings = {
            str(faction.id): {
                "faction_id": str(faction.id), "faction_name": faction.name,
                "value": 42, "level": ReputationLevel.RECOGNIZED.value,
            }
        }
        team_rep = _team_reputation(
            team_id=team.id, faction_reputation=stored_standings,
            last_recalculated=now - timedelta(hours=1),
            next_recalculation=now + timedelta(hours=23),  # not due yet
        )
        # A live Reputation row that DISAGREES with the stored snapshot --
        # if this were recomputed, the value would change. It must NOT be
        # recomputed since next_recalculation is in the future.
        db = _FakeSession(team_reputations=[team_rep], teams=[team], factions=[faction])

        result = trs.get_team_reputation(db, team, now=now)

        assert result["standings"][str(faction.id)]["value"] == 42
        assert calls == []  # no recalculation -> no telemetry


# ---------------------------------------------------------------------------
# switch_method -- leader-only, 7-day cooldown
# ---------------------------------------------------------------------------

class TestSwitchMethod:
    def test_non_leader_is_rejected(self, monkeypatch):
        _capture_broadcasts(monkeypatch)
        leader_id, intruder_id = uuid.uuid4(), uuid.uuid4()
        team = _team(leader_id=leader_id)
        db = _FakeSession(teams=[team])

        with pytest.raises(trs.TeamReputationPermissionError):
            trs.switch_method(db, team, "LOWEST", intruder_id)

    def test_unknown_method_raises_base_error(self, monkeypatch):
        _capture_broadcasts(monkeypatch)
        leader_id = uuid.uuid4()
        team = _team(leader_id=leader_id)
        db = _FakeSession(teams=[team])

        with pytest.raises(trs.TeamReputationError):
            trs.switch_method(db, team, "NOT_A_REAL_METHOD", leader_id)

    def test_first_switch_has_no_cooldown_and_forces_a_recalc(self, monkeypatch):
        _capture_broadcasts(monkeypatch)
        leader_id = uuid.uuid4()
        team = _team(leader_id=leader_id)
        faction = _faction()
        members = [_team_member(team_id=team.id, player_id=leader_id)]
        reputations = [_reputation(player_id=leader_id, faction_id=faction.id, current_value=200)]
        db = _FakeSession(teams=[team], team_members=members, reputations=reputations, factions=[faction])

        result = trs.switch_method(db, team, "LOWEST", leader_id)

        assert result["method"] == "LOWEST"
        team_rep = db._by_table["team_reputations"][0]
        assert team_rep.calculation_method == "LOWEST"
        kinds = [e["kind"] for e in team_rep.history]
        assert kinds == ["method_switch", "recalculation"]

    def test_within_cooldown_raises_with_retry_after(self, monkeypatch):
        _capture_broadcasts(monkeypatch)
        leader_id = uuid.uuid4()
        team = _team(leader_id=leader_id)
        now = datetime(2026, 7, 9, tzinfo=timezone.utc)
        team_rep = _team_reputation(
            team_id=team.id,
            history=[{"kind": "method_switch", "timestamp": (now - timedelta(days=2)).isoformat(),
                      "method": "AVERAGE", "actor_player_id": str(leader_id)}],
        )
        db = _FakeSession(team_reputations=[team_rep], teams=[team])

        with pytest.raises(trs.TeamReputationCooldownError) as exc_info:
            trs.switch_method(db, team, "LOWEST", leader_id, now=now)

        expected_retry = (now - timedelta(days=2)) + trs.METHOD_SWITCH_COOLDOWN
        assert exc_info.value.retry_after == expected_retry

    def test_after_cooldown_window_succeeds(self, monkeypatch):
        calls = _capture_broadcasts(monkeypatch)
        leader_id = uuid.uuid4()
        team = _team(leader_id=leader_id)
        faction = _faction()
        now = datetime(2026, 7, 9, tzinfo=timezone.utc)
        team_rep = _team_reputation(
            team_id=team.id,
            history=[{"kind": "method_switch", "timestamp": (now - timedelta(days=8)).isoformat(),
                      "method": "AVERAGE", "actor_player_id": str(leader_id)}],
        )
        members = [_team_member(team_id=team.id, player_id=leader_id)]
        reputations = [_reputation(player_id=leader_id, faction_id=faction.id, current_value=100)]
        db = _FakeSession(
            team_reputations=[team_rep], teams=[team], team_members=members,
            reputations=reputations, factions=[faction],
        )

        result = trs.switch_method(db, team, "LEADER", leader_id, now=now)

        assert result["method"] == "LEADER"
        # No PRIOR faction_reputation snapshot existed (fresh row) -- the
        # forced recalc's own first-ever-computation rule means no tier
        # "change" fires, so no telemetry either. Confirms switch_method's
        # forced recalc obeys the same first-computation rule as a normal
        # recalculate_team call, not a special-cased "always notify" path.
        assert calls == []


# ---------------------------------------------------------------------------
# sweep_due_team_reputations -- the HELD scheduler-sweep core
# ---------------------------------------------------------------------------

class TestSweepDueTeamReputations:
    def test_touches_only_due_rows_and_advances_next_recalculation(self, monkeypatch):
        _capture_broadcasts(monkeypatch)
        now = datetime(2026, 7, 9, tzinfo=timezone.utc)

        due_team = _team(leader_id=None)
        due_faction = _faction()
        due_rep = _team_reputation(
            team_id=due_team.id, next_recalculation=now - timedelta(hours=1),  # due
        )

        not_due_team = _team(leader_id=None)
        not_due_rep = _team_reputation(
            team_id=not_due_team.id, next_recalculation=now + timedelta(hours=1),  # not due
        )
        not_due_original_next = not_due_rep.next_recalculation

        db = _FakeSession(
            team_reputations=[due_rep, not_due_rep],
            teams=[due_team, not_due_team],
            factions=[due_faction],
        )

        result = trs.sweep_due_team_reputations(db, now=now)

        assert result == {"due": 1, "recalculated": 1}
        assert due_rep.next_recalculation == now + trs.RECALCULATION_INTERVAL
        assert not_due_rep.next_recalculation == not_due_original_next  # untouched

    def test_no_due_rows_is_a_clean_no_op(self, monkeypatch):
        _capture_broadcasts(monkeypatch)
        now = datetime(2026, 7, 9, tzinfo=timezone.utc)
        team = _team(leader_id=None)
        team_rep = _team_reputation(team_id=team.id, next_recalculation=now + timedelta(days=1))
        db = _FakeSession(team_reputations=[team_rep], teams=[team])

        result = trs.sweep_due_team_reputations(db, now=now)

        assert result == {"due": 0, "recalculated": 0}


# ---------------------------------------------------------------------------
# Advisory lock key -- pre-declared, mnemonic-packed, distinct from any
# global scheduler key.
# ---------------------------------------------------------------------------

class TestAdvisoryLockKey:
    def test_key_is_stable_and_non_negative(self):
        assert trs.TEAM_REPUTATION_SWEEP_LOCK_KEY == int.from_bytes(b"TREP", "big")
        assert 0 <= trs.TEAM_REPUTATION_SWEEP_LOCK_KEY < (1 << 63)
