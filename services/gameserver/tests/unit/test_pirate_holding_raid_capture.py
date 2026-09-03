"""Unit tests for pirate_ecosystem_service's ADR-0060 raid/capture kernel
(acquire_combat_lock / can_engage / release_combat_lock / capture_holding).

These are the DORMANT kernel functions (no live caller yet, see the
functions' own docstrings and pirate_holding.py's module docstring) added
per ADR-0060 G-F2/G-V1. Uses a small bespoke fake Session/contextmanager
(scoped to exactly this kernel's write shape -- a single db.add +
db.begin_nested() savepoint), following the established pattern in
test_pirate_ecosystem_dynamics.py / test_pirate_ecosystem_foundation.py
rather than a general SQL evaluator.

Player/Team fixtures are lightweight SimpleNamespace stand-ins (exposing
just ``.id`` / ``.team`` / ``.team_id`` / ``.members``) -- matches this
service module's own established duck-typing convention for holding-like
fixtures (score_holdings' docstring: "real ORM rows or lightweight test
fixtures... interchangeably").
"""

import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from types import SimpleNamespace

from src.models.pirate_holding import PirateHolding, PirateHoldingTier
from src.models.pirate_kill_log import PirateKillDisposition, PirateKillLog
from src.services import pirate_ecosystem_service as pes

# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------

def _holding(*, region_id=None, tier=PirateHoldingTier.STRONGHOLD, **overrides):
    return PirateHolding(
        id=uuid.uuid4(),
        region_id=region_id or uuid.uuid4(),
        sector_id=1,
        tier=tier,
        owner_player_id=None,
        current_strength=1.0,
        **overrides,
    )


def _team(*, member_ids=None):
    team_id = uuid.uuid4()
    members = [SimpleNamespace(id=mid) for mid in (member_ids or [])]
    team = SimpleNamespace(id=team_id, members=members)
    for m in members:
        m.team = team
    return team


def _player(*, team=None):
    p = SimpleNamespace(id=uuid.uuid4(), team=team, team_id=team.id if team else None)
    return p


# ---------------------------------------------------------------------------
# Minimal fake Session -- just enough for capture_holding's begin_nested +
# db.add(PirateKillLog) + db.flush() sequence.
# ---------------------------------------------------------------------------

class _FakeSession:
    def __init__(self):
        self.added = []
        self.flush_count = 0
        self.nested_calls = 0

    def add(self, obj):
        if getattr(obj, "id", None) is None:
            obj.id = uuid.uuid4()
        self.added.append(obj)

    def flush(self):
        self.flush_count += 1

    @contextmanager
    def begin_nested(self):
        self.nested_calls += 1
        yield self


# ---------------------------------------------------------------------------
# acquire_combat_lock
# ---------------------------------------------------------------------------

class TestAcquireCombatLock:
    def test_first_engagement_acquires_lock_and_snapshots_team(self):
        holding = _holding()
        db = _FakeSession()
        engager_id = uuid.uuid4()
        mate_id = uuid.uuid4()
        team = _team(member_ids=[engager_id, mate_id])
        engager = SimpleNamespace(id=engager_id, team=team)

        pes.acquire_combat_lock(db, holding, engager)

        assert holding.combat_lock_held_by == engager.id
        assert set(holding.combat_lock_team_snapshot) == {engager.id, mate_id}

    def test_no_team_snapshots_empty_list(self):
        holding = _holding()
        db = _FakeSession()
        engager = _player(team=None)

        pes.acquire_combat_lock(db, holding, engager)

        assert holding.combat_lock_held_by == engager.id
        assert holding.combat_lock_team_snapshot == []

    def test_reengagement_by_holder_is_a_noop(self):
        holding = _holding()
        db = _FakeSession()
        team = _team()
        holder = _player(team=team)
        pes.acquire_combat_lock(db, holding, holder)
        first_snapshot = list(holding.combat_lock_team_snapshot)

        # second call by the same player -- must not re-snapshot or change
        # anything (holding.combat_lock_held_by already set)
        pes.acquire_combat_lock(db, holding, holder)

        assert holding.combat_lock_held_by == holder.id
        assert holding.combat_lock_team_snapshot == first_snapshot

    def test_already_locked_by_unrelated_player_is_a_noop(self):
        holding = _holding()
        db = _FakeSession()
        holder = _player(team=_team())
        pes.acquire_combat_lock(db, holding, holder)

        stranger = _player(team=_team())
        pes.acquire_combat_lock(db, holding, stranger)

        # lock stays with the original holder -- acquisition attempt by the
        # stranger silently fails (caller is expected to check can_engage
        # first; this is the no-op-if-locked contract)
        assert holding.combat_lock_held_by == holder.id


# ---------------------------------------------------------------------------
# can_engage
# ---------------------------------------------------------------------------

class TestCanEngage:
    def test_no_lock_anyone_can_engage(self):
        holding = _holding()
        assert pes.can_engage(holding, uuid.uuid4()) is True

    def test_lock_holder_can_engage(self):
        holder_id = uuid.uuid4()
        holding = _holding(combat_lock_held_by=holder_id, combat_lock_team_snapshot=[])
        assert pes.can_engage(holding, holder_id) is True

    def test_teammate_in_snapshot_can_engage(self):
        holder_id = uuid.uuid4()
        mate_id = uuid.uuid4()
        holding = _holding(
            combat_lock_held_by=holder_id,
            combat_lock_team_snapshot=[holder_id, mate_id],
        )
        assert pes.can_engage(holding, mate_id) is True

    def test_unrelated_player_cannot_engage(self):
        holder_id = uuid.uuid4()
        holding = _holding(combat_lock_held_by=holder_id, combat_lock_team_snapshot=[holder_id])
        stranger_id = uuid.uuid4()
        assert pes.can_engage(holding, stranger_id) is False

    def test_late_joiner_not_in_frozen_snapshot_cannot_engage(self):
        """The exact scenario ADR-0060 G-F2 exists to close: a player who
        joins the team AFTER the snapshot was taken must NOT be able to
        bypass the lock."""
        holder_id = uuid.uuid4()
        original_mate_id = uuid.uuid4()
        holding = _holding(
            combat_lock_held_by=holder_id,
            combat_lock_team_snapshot=[holder_id, original_mate_id],
        )

        late_joiner_id = uuid.uuid4()  # joins the team after the snapshot was frozen

        assert pes.can_engage(holding, late_joiner_id) is False
        # sanity: the ORIGINAL snapshot members are unaffected
        assert pes.can_engage(holding, original_mate_id) is True


# ---------------------------------------------------------------------------
# release_combat_lock
# ---------------------------------------------------------------------------

class TestReleaseCombatLock:
    def test_clears_both_columns(self):
        holder_id = uuid.uuid4()
        holding = _holding(
            combat_lock_held_by=holder_id,
            combat_lock_team_snapshot=[holder_id, uuid.uuid4()],
        )
        db = _FakeSession()

        pes.release_combat_lock(db, holding)

        assert holding.combat_lock_held_by is None
        assert holding.combat_lock_team_snapshot is None

    def test_release_when_not_locked_is_safe(self):
        holding = _holding()
        db = _FakeSession()

        pes.release_combat_lock(db, holding)

        assert holding.combat_lock_held_by is None
        assert holding.combat_lock_team_snapshot is None


# ---------------------------------------------------------------------------
# capture_holding -- atomicity
# ---------------------------------------------------------------------------

class TestCaptureHolding:
    def test_capture_writes_kill_log_and_flips_ownership_atomically(self):
        holder_id = uuid.uuid4()
        holding = _holding(
            combat_lock_held_by=holder_id,
            combat_lock_team_snapshot=[holder_id],
        )
        db = _FakeSession()
        team = _team()
        capturer = _player(team=team)

        kill_log = pes.capture_holding(
            db, holding, capturer,
            kill_log_entry_kwargs=dict(
                region_id=holding.region_id,
                region_id_snapshot=holding.region_id,
                holding_id=holding.id,
                tier=holding.tier,
                kill_weight=10,
                attacker_player_id=capturer.id,
                attacker_team_id=team.id,
            ),
        )

        # 1. kill-log row inserted with CAPTURED disposition
        assert kill_log in db.added
        assert isinstance(kill_log, PirateKillLog)
        assert kill_log.disposition == PirateKillDisposition.CAPTURED
        assert kill_log.holding_id == holding.id

        # 2. ownership flip (player always; team when present)
        assert holding.owner_player_id == capturer.id
        assert holding.owner_team_id == team.id
        assert holding.captured_at is not None
        assert isinstance(holding.captured_at, datetime)
        assert holding.captured_at.tzinfo is not None

        # 3. combat lock released
        assert holding.combat_lock_held_by is None
        assert holding.combat_lock_team_snapshot is None

        # 4. all inside ONE savepoint (this module's established
        # multi-write-atomicity idiom -- npc_spawn_service.py's PirateKillLog
        # feeder), and the caller's own flush count reflects the ONE flush
        # taken inside it
        assert db.nested_calls == 1
        assert db.flush_count == 1

    def test_capture_falls_back_to_bare_team_id_when_capturer_has_no_team_relationship(self):
        """capturer_player fixtures that only expose team_id (not a live
        .team relationship object) still resolve ownership correctly."""
        holding = _holding(combat_lock_held_by=uuid.uuid4())
        db = _FakeSession()
        team_id = uuid.uuid4()
        capturer = SimpleNamespace(id=uuid.uuid4(), team=None, team_id=team_id)

        pes.capture_holding(
            db, holding, capturer,
            kill_log_entry_kwargs=dict(
                region_id=holding.region_id,
                region_id_snapshot=holding.region_id,
                holding_id=holding.id,
                tier=holding.tier,
                kill_weight=10,
                attacker_player_id=capturer.id,
                attacker_team_id=team_id,
            ),
        )

        assert holding.owner_player_id == capturer.id
        assert holding.owner_team_id == team_id

    def test_capture_sets_owner_player_id_when_capturer_has_no_team(self):
        """Solo capturer: owner_player_id set, owner_team_id stays null."""
        holding = _holding(combat_lock_held_by=uuid.uuid4())
        db = _FakeSession()
        capturer = SimpleNamespace(id=uuid.uuid4(), team=None, team_id=None)

        pes.capture_holding(
            db, holding, capturer,
            kill_log_entry_kwargs=dict(
                region_id=holding.region_id,
                region_id_snapshot=holding.region_id,
                holding_id=holding.id,
                tier=holding.tier,
                kill_weight=10,
                attacker_player_id=capturer.id,
                attacker_team_id=None,
            ),
        )

        assert holding.owner_player_id == capturer.id
        assert holding.owner_team_id is None

    def test_capture_emits_holding_captured_exactly_once(self, monkeypatch):
        calls = []
        monkeypatch.setattr(
            pes, "_broadcast_pirate_event",
            lambda region_id, payload: calls.append((region_id, payload)),
        )
        holder_id = uuid.uuid4()
        holding = _holding(
            combat_lock_held_by=holder_id, combat_lock_team_snapshot=[holder_id],
        )
        db = _FakeSession()
        team = _team()
        capturer = _player(team=team)

        pes.capture_holding(
            db, holding, capturer,
            kill_log_entry_kwargs=dict(
                region_id=holding.region_id,
                region_id_snapshot=holding.region_id,
                holding_id=holding.id,
                tier=holding.tier,
                kill_weight=10,
                attacker_player_id=capturer.id,
                attacker_team_id=team.id,
            ),
        )

        assert len(calls) == 1
        region_id, payload = calls[0]
        assert region_id == holding.region_id
        assert payload["type"] == "holding_captured"
        assert payload["region_id"] == str(holding.region_id)
        assert payload["holding_id"] == str(holding.id)
        assert payload["tier"] == holding.tier.value
        assert payload["captured_by_player_id"] == str(capturer.id)
        assert payload["captured_by_team_id"] == str(team.id)

    def test_capture_emits_bare_team_id_when_no_team_relationship(self, monkeypatch):
        calls = []
        monkeypatch.setattr(
            pes, "_broadcast_pirate_event",
            lambda region_id, payload: calls.append((region_id, payload)),
        )
        holding = _holding(combat_lock_held_by=uuid.uuid4())
        db = _FakeSession()
        team_id = uuid.uuid4()
        capturer = SimpleNamespace(id=uuid.uuid4(), team=None, team_id=team_id)

        pes.capture_holding(
            db, holding, capturer,
            kill_log_entry_kwargs=dict(
                region_id=holding.region_id,
                region_id_snapshot=holding.region_id,
                holding_id=holding.id,
                tier=holding.tier,
                kill_weight=10,
                attacker_player_id=capturer.id,
                attacker_team_id=team_id,
            ),
        )

        assert len(calls) == 1
        _region_id, payload = calls[0]
        assert payload["captured_by_team_id"] == str(team_id)


# ---------------------------------------------------------------------------
# maybe_reset_evolution_clock (ADR-0060 G-I1) -- DORMANT, no live caller (see
# the function's own docstring: no damage-vs-holding combat path exists yet,
# and evolution_tick's own LIVE clock still anchors on last_damage_at, not
# this column).
# ---------------------------------------------------------------------------

class TestMaybeResetEvolutionClock:
    def test_damage_at_exact_five_percent_threshold_resets(self):
        holding = _holding(evolution_clock_started_at=None)
        now = datetime(2026, 7, 9, tzinfo=timezone.utc)

        result = pes.maybe_reset_evolution_clock(holding, 5.0, 100.0, now=now)

        assert result is True
        assert holding.evolution_clock_started_at == now

    def test_damage_above_threshold_resets(self):
        holding = _holding(evolution_clock_started_at=None)
        now = datetime(2026, 7, 9, tzinfo=timezone.utc)

        result = pes.maybe_reset_evolution_clock(holding, 50.0, 100.0, now=now)

        assert result is True
        assert holding.evolution_clock_started_at == now

    def test_trivial_scratch_below_threshold_does_not_reset(self):
        """The ADR's own example: a 1-hull-point scratch must not drop the
        clock (:86, 'a 1-hull-point scratch on day 29 no longer drops the
        clock to day 0')."""
        holding = _holding(evolution_clock_started_at=None)

        result = pes.maybe_reset_evolution_clock(holding, 1.0, 100.0)

        assert result is False
        assert holding.evolution_clock_started_at is None

    def test_below_threshold_does_not_clobber_an_existing_clock_start(self):
        existing = datetime(2026, 6, 1, tzinfo=timezone.utc)
        holding = _holding(evolution_clock_started_at=existing)

        result = pes.maybe_reset_evolution_clock(holding, 4.99, 100.0)

        assert result is False
        assert holding.evolution_clock_started_at == existing

    def test_zero_max_hp_is_defensive_noop(self):
        holding = _holding(evolution_clock_started_at=None)

        result = pes.maybe_reset_evolution_clock(holding, 10.0, 0.0)

        assert result is False
        assert holding.evolution_clock_started_at is None
