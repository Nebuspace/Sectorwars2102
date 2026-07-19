"""WO-ARIA-TRUST-PERSIST -- the AISecurityService violation ladder
(trust_score / violation_count / is_blocked+block_expires) now seeds from,
and writes through to, Player.aria_trust_score / aria_violation_count /
aria_blocked_until. THE FALSIFIER this suite is built to prove: a blocked
abuser must STAY blocked across a process restart -- before this WO the
ladder was purely in-memory and every restart silently amnestied every
blocked player.

No DB needed for the AISecurityService-level tests: seeding takes a plain
object exposing the three attributes (SimpleNamespace stands in for a real
Player row -- get_or_create_player_profile only ever reads getattr()).
The admin-route test uses this codebase's established direct-call +
FakeSession pattern (real SQLAlchemy filter-clause interpretation).
"""
from __future__ import annotations

import operator
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any, List, Optional

import pytest

from src.services.ai_security_service import (
    AISecurityService,
    SecurityViolationType,
)

_NOW = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


def _player_row(**overrides: Any) -> SimpleNamespace:
    base = dict(
        id=uuid.uuid4(),
        aria_trust_score=1.0,
        aria_violation_count=0,
        aria_blocked_until=None,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


@pytest.mark.unit
class TestSeeding:
    def test_seed_from_none_gives_bare_default(self) -> None:
        """Regression pin -- every pre-existing caller that never passes
        seed_from gets EXACTLY the old bare-default behavior."""
        svc = AISecurityService()
        profile = svc.get_or_create_player_profile("player-1")
        assert profile.trust_score == 1.0
        assert profile.violation_count == 0
        assert profile.is_blocked is False

    def test_seed_hydrates_from_a_player_row(self) -> None:
        svc = AISecurityService()
        row = _player_row(aria_trust_score=0.4, aria_violation_count=7)
        profile = svc.get_or_create_player_profile("player-1", seed_from=row)
        assert profile.trust_score == 0.4
        assert profile.violation_count == 7

    def test_seed_with_a_future_blocked_until_marks_the_profile_blocked(self) -> None:
        svc = AISecurityService()
        future = _NOW + timedelta(hours=5)  # tz-aware, matches a real Postgres round-trip
        row = _player_row(aria_blocked_until=future)
        profile = svc.get_or_create_player_profile("player-1", seed_from=row)
        assert profile.is_blocked is True
        # Normalized to naive on the way in (see get_or_create_player_
        # profile's own fix) -- every OTHER comparison in this class uses
        # naive datetime.utcnow(), so the seeded value must match that,
        # not the original aware input.
        assert profile.block_expires == future.replace(tzinfo=None)

    def test_seed_only_applies_on_first_touch(self) -> None:
        """A second seed_from on an ALREADY-touched profile is ignored --
        in-process state is more current than a possibly-stale re-read."""
        svc = AISecurityService()
        svc.get_or_create_player_profile("player-1", seed_from=_player_row(aria_trust_score=0.9))
        stale_row = _player_row(aria_trust_score=0.1)  # would clobber if not ignored
        profile = svc.get_or_create_player_profile("player-1", seed_from=stale_row)
        assert profile.trust_score == 0.9  # unchanged -- stale seed ignored


@pytest.mark.unit
class TestGetTrustColumns:
    def test_reflects_current_state_for_a_write_through(self) -> None:
        svc = AISecurityService()
        svc.apply_security_penalty("player-1", SecurityViolationType.PROMPT_INJECTION)
        columns = svc.get_trust_columns("player-1")
        assert columns["aria_trust_score"] == pytest.approx(0.8)
        assert columns["aria_violation_count"] == 1
        assert columns["aria_blocked_until"] is None  # one prompt-injection alone doesn't block

    def test_blocked_state_surfaces_block_expires_as_aria_blocked_until(self) -> None:
        svc = AISecurityService()
        svc.apply_security_penalty("player-1", SecurityViolationType.SYSTEM_COMMAND)  # immediate 24h block
        columns = svc.get_trust_columns("player-1")
        assert columns["aria_blocked_until"] is not None

    def test_unblocked_state_surfaces_none_not_a_stale_timestamp(self) -> None:
        svc = AISecurityService()
        profile = svc.get_or_create_player_profile("player-1")
        # A block that already expired IN-PROCESS (is_player_blocked's own
        # existing auto-clear) must not leak a stale block_expires into
        # the write-through columns. block_expires is internally NAIVE
        # (matches this class's own datetime.utcnow() convention --
        # only the seed_from boundary normalizes an aware input; setting
        # it directly here, as production code paths in this class do,
        # uses a naive value too).
        profile.is_blocked = True
        profile.block_expires = _NOW.replace(tzinfo=None) - timedelta(hours=1)
        assert svc.is_player_blocked("player-1") is False  # auto-clears
        columns = svc.get_trust_columns("player-1")
        assert columns["aria_blocked_until"] is None


@pytest.mark.unit
class TestPenaltyMagnitudesByteUnchanged:
    """Pin -- WO-ARIA-TRUST-PERSIST adds persistence, it does NOT change
    any ladder number. Dispatch: 'do not change the numbers'."""

    @pytest.mark.parametrize(
        "violation_type,expected_drop",
        [
            (SecurityViolationType.XSS_ATTEMPT, 0.3),
            (SecurityViolationType.SQL_INJECTION, 0.3),
            (SecurityViolationType.PROMPT_INJECTION, 0.2),
            (SecurityViolationType.JAILBREAK_ATTEMPT, 0.4),
            (SecurityViolationType.SYSTEM_COMMAND, 0.5),
            (SecurityViolationType.CODE_INJECTION, 0.4),
            (SecurityViolationType.RATE_LIMIT_EXCEEDED, 0.1),
            (SecurityViolationType.COST_ABUSE, 0.3),
        ],
    )
    def test_trust_drop_per_violation_type(self, violation_type: SecurityViolationType, expected_drop: float) -> None:
        svc = AISecurityService()
        svc.apply_security_penalty("player-1", violation_type)
        profile = svc.get_or_create_player_profile("player-1")
        assert profile.trust_score == pytest.approx(1.0 - expected_drop)

    def test_severe_violation_immediate_24h_block(self) -> None:
        svc = AISecurityService()
        svc.apply_security_penalty("player-1", SecurityViolationType.XSS_ATTEMPT)
        profile = svc.get_or_create_player_profile("player-1")
        assert profile.is_blocked is True
        # 24h, within a tolerant window (real-clock call inside apply_security_penalty).
        remaining = profile.block_expires - datetime.utcnow()
        assert timedelta(hours=23, minutes=55) < remaining <= timedelta(hours=24)

    def test_five_violations_trigger_6h_block(self) -> None:
        svc = AISecurityService()
        for _ in range(5):
            svc.apply_security_penalty("player-1", SecurityViolationType.RATE_LIMIT_EXCEEDED)
        profile = svc.get_or_create_player_profile("player-1")
        assert profile.is_blocked is True
        remaining = profile.block_expires - datetime.utcnow()
        assert timedelta(hours=5, minutes=55) < remaining <= timedelta(hours=6)

    def test_three_violations_trigger_1h_block(self) -> None:
        svc = AISecurityService()
        for _ in range(3):
            svc.apply_security_penalty("player-1", SecurityViolationType.RATE_LIMIT_EXCEEDED)
        profile = svc.get_or_create_player_profile("player-1")
        assert profile.is_blocked is True
        remaining = profile.block_expires - datetime.utcnow()
        assert timedelta(minutes=55) < remaining <= timedelta(hours=1)


@pytest.mark.unit
class TestTrustDecayRecoveryAgainstPersistedValue:
    def test_a_new_violation_drops_from_the_persisted_baseline_not_from_1_0(self) -> None:
        svc = AISecurityService()
        row = _player_row(aria_trust_score=0.7)  # partially recovered/decayed persisted state
        svc.get_or_create_player_profile("player-1", seed_from=row)

        svc.apply_security_penalty("player-1", SecurityViolationType.PROMPT_INJECTION)  # -0.2

        profile = svc.get_or_create_player_profile("player-1")
        assert profile.trust_score == pytest.approx(0.5)  # 0.7 - 0.2, NOT 1.0 - 0.2

    def test_trust_never_drops_below_zero_regardless_of_persisted_baseline(self) -> None:
        svc = AISecurityService()
        row = _player_row(aria_trust_score=0.1)
        svc.get_or_create_player_profile("player-1", seed_from=row)
        svc.apply_security_penalty("player-1", SecurityViolationType.SYSTEM_COMMAND)  # -0.5
        profile = svc.get_or_create_player_profile("player-1")
        assert profile.trust_score == 0.0  # clamped, not negative


@pytest.mark.unit
class TestSimulatedRestartHonorsPersistedBlock:
    """THE FALSIFIER. Two SEPARATE AISecurityService instances model two
    process lifetimes -- nothing but the fake Player row's persisted
    columns crosses between them, exactly like a real process restart."""

    def test_a_blocked_player_stays_blocked_after_a_simulated_restart(self) -> None:
        # --- "process 1": the abuse happens, the block is written through ---
        process_1 = AISecurityService()
        process_1.apply_security_penalty("player-1", SecurityViolationType.SYSTEM_COMMAND)
        persisted = _player_row(**process_1.get_trust_columns("player-1"))
        assert persisted.aria_blocked_until is not None  # confirms the setup actually blocked

        # --- simulated restart: a BRAND NEW instance, zero in-memory carryover ---
        process_2 = AISecurityService()
        assert "player-1" not in process_2.player_profiles  # nothing survived except the row

        # First touch this new "process" seeds from the persisted row.
        process_2.get_or_create_player_profile("player-1", seed_from=persisted)

        # Before this WO: a fresh AISecurityService() had no memory of the
        # block at all -- is_player_blocked would have returned False,
        # silently amnestying the abuser. Now it must return True.
        assert process_2.is_player_blocked("player-1") is True

    def test_an_expired_persisted_block_correctly_unblocks_on_first_use(self) -> None:
        past = _NOW - timedelta(hours=1)
        persisted = _player_row(aria_blocked_until=past)

        process_2 = AISecurityService()
        process_2.get_or_create_player_profile("player-1", seed_from=persisted)

        # is_player_blocked's existing expiry check (unchanged by this WO)
        # clears a persisted-but-now-past block exactly like an in-process one.
        assert process_2.is_player_blocked("player-1") is False

    def test_an_unblocked_player_stays_unblocked_after_a_simulated_restart(self) -> None:
        process_1 = AISecurityService()
        process_1.apply_security_penalty("player-1", SecurityViolationType.PROMPT_INJECTION)  # not severe -- no block
        persisted = _player_row(**process_1.get_trust_columns("player-1"))
        assert persisted.aria_blocked_until is None

        process_2 = AISecurityService()
        process_2.get_or_create_player_profile("player-1", seed_from=persisted)
        assert process_2.is_player_blocked("player-1") is False


@pytest.mark.unit
class TestAdminSecurityActionWritesThrough:
    """Direct-call test (this codebase's established admin-route pattern)
    -- proves the admin block/unblock/reset endpoints, not just the
    ladder's own internal calls, write through and commit."""

    def _fake_admin_db(self, player_row: SimpleNamespace) -> Any:
        class _FakeQuery:
            def __init__(self, row: SimpleNamespace, criteria: Optional[List[Any]] = None) -> None:
                self._row = row
                self._criteria = criteria or []

            def filter(self, *conditions: Any) -> "_FakeQuery":
                return _FakeQuery(self._row, self._criteria + list(conditions))

            def first(self) -> Optional[SimpleNamespace]:
                for cond in self._criteria:
                    if cond.operator is operator.eq and getattr(self._row, cond.left.key) != cond.right.value:
                        return None
                return self._row

        class _FakeDb:
            def __init__(self, row: SimpleNamespace) -> None:
                self._row = row
                self.commit_calls = 0

            def query(self, model: Any) -> _FakeQuery:
                return _FakeQuery(self._row)

            def commit(self) -> None:
                self.commit_calls += 1

        return _FakeDb(player_row)

    def test_unblock_action_clears_aria_blocked_until_and_commits(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import asyncio

        import src.api.routes.admin_comprehensive as admin_module
        from src.api.routes.admin_comprehensive import PlayerSecurityAction, take_security_action

        svc = AISecurityService()
        player_id = uuid.uuid4()
        # Player was blocked BEFORE this admin call (persisted state).
        row = _player_row(id=player_id, aria_blocked_until=_NOW + timedelta(hours=10))
        svc.get_or_create_player_profile(str(player_id), seed_from=row)
        monkeypatch.setattr(admin_module, "get_security_service", lambda: svc)

        db = self._fake_admin_db(row)
        admin = SimpleNamespace(username="root-admin")

        result = asyncio.run(
            take_security_action(
                player_id=str(player_id),
                action=PlayerSecurityAction(action="unblock"),
                current_admin=admin,
                db=db,
            )
        )

        assert result["success"] is True
        assert row.aria_blocked_until is None  # written through onto the row
        assert db.commit_calls == 1
