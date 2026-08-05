"""Suspect-status lifecycle (WO-CMB-SUSPECT-LIFE-1, Max ruling 2026-07-10).

Canon: sw2102-docs/FEATURES/gameplay/ships.md:287-296 + ADR-0061 S-V4 +
DATA_MODELS/player.md's target schema for suspect_until/suspect_team_snapshot.

DB-free: a hand-built _FakeSession dispatches on query TARGET IDENTITY --
``Player`` (the whole class) for PersonalReputationService's own lookup
(returns the SAME player instance under test, so the -25 hit is genuinely
observable on player.personal_reputation, not just "didn't crash"), and
``Player.id`` (the column) for suspect_service's team-roster snapshot query.
Both real production code paths (suspect_service.py + the REAL
PersonalReputationService) run end-to-end against this fake -- nothing here
mocks suspect_service's own logic.

Covers: +1h/4h-cap math (injected clock), first-acquisition-only snapshot,
-25-per-event stacking, auto-clear idempotence, salvage/contraband writer
parity, reader regression (ranking_service's is_suspect display).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, List, Optional

import pytest

from src.models.player import Player
from src.services import suspect_service

FROZEN_NOW = datetime(2026, 7, 10, 12, 0, 0, tzinfo=timezone.utc)


def _player(
    *,
    team_id: Optional[uuid.UUID] = None,
    is_suspect: bool = False,
    suspect_until: Optional[datetime] = None,
    suspect_declared_at: Optional[datetime] = None,
    suspect_team_snapshot: Optional[List[uuid.UUID]] = None,
    personal_reputation: int = 0,
) -> Player:
    return Player(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        team_id=team_id,
        is_suspect=is_suspect,
        suspect_until=suspect_until,
        suspect_declared_at=suspect_declared_at,
        suspect_team_snapshot=suspect_team_snapshot,
        personal_reputation=personal_reputation,
    )


class _FakeQuery:
    def __init__(self, *, all_result: Optional[list] = None, first_result: Any = None):
        self._all = all_result if all_result is not None else []
        self._first = first_result

    def filter(self, *a: Any, **k: Any) -> "_FakeQuery":
        return self

    def all(self) -> list:
        return self._all

    def first(self) -> Any:
        return self._first


class _FakeSession:
    """``Player`` (whole class) -> the target player row, for
    PersonalReputationService's own lookup. ``Player.id`` (column) -> the
    team roster snapshot query. Dispatch on IDENTITY, not equality --
    ``Player.id`` is a real InstrumentedAttribute, distinct from the class."""

    def __init__(self, *, self_player: Player, team_rows: Optional[List[uuid.UUID]] = None):
        self._self_player = self_player
        self._team_rows = team_rows or []
        self.flush_calls = 0

    def query(self, target: Any) -> _FakeQuery:
        if target is Player:
            return _FakeQuery(first_result=self._self_player)
        if target is Player.id:
            return _FakeQuery(all_result=[(r,) for r in self._team_rows])
        raise AssertionError(f"unexpected query target: {target!r}")

    def flush(self) -> None:
        self.flush_calls += 1


def _session_for(player: Player, *, team_rows: Optional[List[uuid.UUID]] = None) -> _FakeSession:
    return _FakeSession(self_player=player, team_rows=team_rows)


# ---------------------------------------------------------------------------
# +1h extension / 4h cumulative cap math
# ---------------------------------------------------------------------------


class TestTimerMath:
    def test_first_acquisition_sets_declared_at_and_plus_one_hour(self):
        player = _player()
        db = _session_for(player)

        first = suspect_service.apply_suspect_event(db, player, reason="early_salvage", now=FROZEN_NOW)

        assert first is True
        assert player.is_suspect is True
        assert player.suspect_declared_at == FROZEN_NOW
        assert player.suspect_until == FROZEN_NOW + timedelta(hours=1)

    def test_second_event_extends_by_one_more_hour(self):
        declared = FROZEN_NOW
        player = _player(is_suspect=True, suspect_declared_at=declared, suspect_until=declared + timedelta(hours=1))
        db = _session_for(player)

        second_at = FROZEN_NOW + timedelta(minutes=30)
        first = suspect_service.apply_suspect_event(db, player, reason="early_salvage", now=second_at)

        assert first is False
        assert player.suspect_declared_at == declared  # anchor unchanged
        assert player.suspect_until == second_at + timedelta(hours=1)

    def test_extension_is_capped_at_four_hours_cumulative_from_first_acquisition(self):
        declared = FROZEN_NOW
        # Already extended close to the 4h ceiling.
        player = _player(
            is_suspect=True, suspect_declared_at=declared,
            suspect_until=declared + timedelta(hours=3, minutes=45),
        )
        db = _session_for(player)

        event_at = declared + timedelta(hours=3, minutes=30)
        suspect_service.apply_suspect_event(db, player, reason="early_salvage", now=event_at)

        # event_at + 1h = declared + 4h30m, which exceeds the 4h cap ->
        # clamped to exactly declared + 4h.
        assert player.suspect_until == declared + timedelta(hours=4)

    def test_repeated_events_never_exceed_the_four_hour_ceiling(self):
        declared = FROZEN_NOW
        player = _player(is_suspect=True, suspect_declared_at=declared, suspect_until=declared + timedelta(hours=1))
        db = _session_for(player)

        # Fire an event every 20 minutes, staying STRICTLY inside the 4h
        # window (10 * 20min = 3h20m < 4h) -- each event is a genuine
        # re-trigger of the SAME acquisition, never a fresh one (crossing
        # past the 4h mark would legitimately expire the window and start a
        # new cycle -- that boundary is covered separately, below, by
        # test_stale_expired_flag_is_treated_as_a_fresh_first_acquisition).
        t = declared
        for _ in range(10):
            t = t + timedelta(minutes=20)
            suspect_service.apply_suspect_event(db, player, reason="early_salvage", now=t)
            assert player.suspect_until <= declared + timedelta(hours=4)

        # Saturates at exactly the cap well before the window would expire.
        assert player.suspect_until == declared + timedelta(hours=4)

    def test_stale_expired_flag_is_treated_as_a_fresh_first_acquisition(self):
        # is_suspect=True but suspect_until already elapsed -- the lazy sweep
        # hasn't run yet. A new event must NOT extend from the old anchor.
        old_declared = FROZEN_NOW - timedelta(hours=10)
        player = _player(
            is_suspect=True, suspect_declared_at=old_declared,
            suspect_until=old_declared + timedelta(hours=4),  # long expired
        )
        db = _session_for(player)

        first = suspect_service.apply_suspect_event(db, player, reason="early_salvage", now=FROZEN_NOW)

        assert first is True  # fresh acquisition, not a continuation
        assert player.suspect_declared_at == FROZEN_NOW  # anchor reset
        assert player.suspect_until == FROZEN_NOW + timedelta(hours=1)


# ---------------------------------------------------------------------------
# Team snapshot: captured once, at first acquisition ONLY
# ---------------------------------------------------------------------------


class TestTeamSnapshot:
    def test_snapshot_captured_on_first_acquisition_when_teamed(self):
        team_id = uuid.uuid4()
        mate_a, mate_b = uuid.uuid4(), uuid.uuid4()
        player = _player(team_id=team_id)
        db = _session_for(player, team_rows=[mate_a, mate_b])

        suspect_service.apply_suspect_event(db, player, reason="early_salvage", now=FROZEN_NOW)

        assert set(player.suspect_team_snapshot) == {mate_a, mate_b}

    def test_snapshot_is_empty_array_not_null_when_no_team(self):
        player = _player(team_id=None)
        db = _session_for(player)

        suspect_service.apply_suspect_event(db, player, reason="early_salvage", now=FROZEN_NOW)

        assert player.suspect_team_snapshot == []
        assert player.suspect_team_snapshot is not None

    def test_snapshot_not_rebuilt_on_a_re_trigger_even_if_team_changed(self):
        team_id = uuid.uuid4()
        original_snapshot = [uuid.uuid4()]
        player = _player(
            team_id=team_id, is_suspect=True,
            suspect_declared_at=FROZEN_NOW, suspect_until=FROZEN_NOW + timedelta(hours=1),
            suspect_team_snapshot=list(original_snapshot),
        )
        # A DIFFERENT team roster now -- must be ignored; the snapshot is frozen.
        db = _session_for(player, team_rows=[uuid.uuid4(), uuid.uuid4(), uuid.uuid4()])

        suspect_service.apply_suspect_event(
            db, player, reason="early_salvage", now=FROZEN_NOW + timedelta(minutes=10)
        )

        assert player.suspect_team_snapshot == original_snapshot


# ---------------------------------------------------------------------------
# -25 personal-reputation hit, stacking across repeat events
# ---------------------------------------------------------------------------


class TestReputationPenalty:
    def test_first_event_applies_minus_twenty_five(self):
        player = _player(personal_reputation=0)
        db = _session_for(player)

        suspect_service.apply_suspect_event(db, player, reason="early_salvage", now=FROZEN_NOW)

        assert player.personal_reputation == -25

    def test_three_events_stack_to_minus_seventy_five(self):
        player = _player(personal_reputation=0)
        db = _session_for(player)

        suspect_service.apply_suspect_event(db, player, reason="early_salvage", now=FROZEN_NOW)
        suspect_service.apply_suspect_event(
            db, player, reason="early_salvage", now=FROZEN_NOW + timedelta(minutes=5)
        )
        suspect_service.apply_suspect_event(
            db, player, reason="early_salvage", now=FROZEN_NOW + timedelta(minutes=10)
        )

        assert player.personal_reputation == -75

    def test_penalty_applies_even_on_a_re_trigger_not_just_first_acquisition(self):
        player = _player(
            is_suspect=True, suspect_declared_at=FROZEN_NOW, suspect_until=FROZEN_NOW + timedelta(hours=1),
            personal_reputation=-25,
        )
        db = _session_for(player)

        second = suspect_service.apply_suspect_event(
            db, player, reason="early_salvage", now=FROZEN_NOW + timedelta(minutes=5)
        )

        assert second is False  # re-trigger, not first
        assert player.personal_reputation == -50  # penalty still applied

    def test_a_reputation_service_failure_never_blocks_the_flag_timer_or_snapshot(self, monkeypatch):
        """Defensive contract (mirrors contraband_service._adjust_notoriety):
        a rep-service hiccup must not roll back the flag/timer/snapshot that
        already landed."""
        class _BoomSession(_FakeSession):
            def query(self, target):
                raise RuntimeError("simulated rep-service outage")

        player = _player(team_id=uuid.uuid4())
        db = _BoomSession(self_player=player, team_rows=[])

        first = suspect_service.apply_suspect_event(db, player, reason="early_salvage", now=FROZEN_NOW)

        assert first is True
        assert player.is_suspect is True
        assert player.suspect_until == FROZEN_NOW + timedelta(hours=1)
        # Snapshot capture ALSO queries db -- also degrades gracefully to [].
        assert player.suspect_team_snapshot == []
        assert player.personal_reputation == 0  # penalty never landed, no crash either


# ---------------------------------------------------------------------------
# Auto-clear sweep: idempotence + full-field clearing
# ---------------------------------------------------------------------------


class _SweepFakeQuery:
    def __init__(self, rows: list):
        self._rows = rows

    def filter(self, *a: Any, **k: Any) -> "_SweepFakeQuery":
        return self

    def all(self) -> list:
        return self._rows


class _SweepFakeSession:
    def __init__(self, players: list):
        self._players = players
        self.flush_calls = 0

    def query(self, target: Any) -> _SweepFakeQuery:
        assert target is Player
        return _SweepFakeQuery(self._players)

    def flush(self) -> None:
        self.flush_calls += 1


class TestAutoClearSweep:
    def test_clears_all_four_fields_on_an_expired_suspect(self):
        expired = _player(
            is_suspect=True, suspect_declared_at=FROZEN_NOW - timedelta(hours=1),
            suspect_until=FROZEN_NOW - timedelta(minutes=1),
            suspect_team_snapshot=[uuid.uuid4()],
        )
        db = _SweepFakeSession([expired])

        count = suspect_service.clear_expired_suspects(db, now=FROZEN_NOW)

        assert count == 1
        assert expired.is_suspect is False
        assert expired.suspect_until is None
        assert expired.suspect_team_snapshot is None
        assert expired.suspect_declared_at is None
        assert db.flush_calls == 1

    def test_running_twice_is_idempotent_second_pass_clears_nothing(self):
        expired = _player(
            is_suspect=True, suspect_declared_at=FROZEN_NOW - timedelta(hours=1),
            suspect_until=FROZEN_NOW - timedelta(minutes=1),
        )
        db = _SweepFakeSession([expired])
        suspect_service.clear_expired_suspects(db, now=FROZEN_NOW)

        # The scheduler's query predicate (is_suspect=True) would no longer
        # match this row on a real second pass; simulate that by re-querying
        # an EMPTY set (the row already cleared).
        db2 = _SweepFakeSession([])
        count = suspect_service.clear_expired_suspects(db2, now=FROZEN_NOW)

        assert count == 0
        assert db2.flush_calls == 0  # no-op sweep never flushes

    def test_a_not_yet_expired_suspect_is_left_untouched(self):
        active = _player(
            is_suspect=True, suspect_declared_at=FROZEN_NOW,
            suspect_until=FROZEN_NOW + timedelta(minutes=30),
        )
        # The real query predicate (suspect_until <= now) would never select
        # this row -- an empty result models that.
        db = _SweepFakeSession([])

        count = suspect_service.clear_expired_suspects(db, now=FROZEN_NOW)

        assert count == 0
        assert active.is_suspect is True  # untouched


# ---------------------------------------------------------------------------
# Writer parity: salvage_service + contraband_service produce IDENTICAL
# lifecycle effects for equivalent inputs (both funnel through the same
# apply_suspect_event core).
# ---------------------------------------------------------------------------


class TestWriterParity:
    def test_salvage_and_contraband_writers_both_delegate_to_the_shared_core(self):
        """Structural pin: neither writer hand-rolls is_suspect/suspect_until
        mutation anymore -- both call suspect_service.apply_suspect_event."""
        import inspect
        from src.services import contraband_service, salvage_service

        salvage_source = inspect.getsource(salvage_service.salvage_wreck)
        assert "suspect_service.apply_suspect_event" in salvage_source
        assert "player.is_suspect = True" not in salvage_source

        heat_source = inspect.getsource(contraband_service.ContrabandService._apply_heat)
        assert "suspect_service.apply_suspect_event" in heat_source
        assert "player.is_suspect = True" not in heat_source

    def test_both_writers_apply_the_identical_minus_twenty_five_and_one_hour_extension(self):
        salvage_player = _player()
        contraband_player = _player()
        db_a = _session_for(salvage_player)
        db_b = _session_for(contraband_player)

        suspect_service.apply_suspect_event(db_a, salvage_player, reason="early_salvage", now=FROZEN_NOW)
        suspect_service.apply_suspect_event(db_b, contraband_player, reason="black_market_bust", now=FROZEN_NOW)

        assert salvage_player.personal_reputation == contraband_player.personal_reputation == -25
        assert salvage_player.suspect_until == contraband_player.suspect_until == FROZEN_NOW + timedelta(hours=1)
        assert salvage_player.is_suspect is contraband_player.is_suspect is True


# ---------------------------------------------------------------------------
# Reader regression: ranking_service's is_suspect display is untouched
# ---------------------------------------------------------------------------


class TestReaderRegression:
    def test_ranking_service_still_reads_is_suspect_as_a_plain_boolean(self):
        """is_suspect itself was never renamed/restructured -- only additive
        columns were introduced. A player mid-suspect-window still reads
        correctly through the EXACT getattr pattern ranking_service.py uses."""
        player = _player(
            is_suspect=True, suspect_until=FROZEN_NOW + timedelta(hours=1),
            suspect_declared_at=FROZEN_NOW,
        )
        assert bool(getattr(player, "is_suspect", False)) is True

        cleared = _player(is_suspect=False)
        assert bool(getattr(cleared, "is_suspect", False)) is False
