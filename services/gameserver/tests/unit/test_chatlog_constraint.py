"""WO-SWEEP-CHATLOG-CONSTRAINT -- ai_conversation_logs has
CheckConstraint(response_time_ms > 0, name="positive_response_time")
(models/enhanced_ai_models.py:721, c138b33baec4:964). Fast template-path
replies measure 0ms (perf_counter int-truncation on a sub-millisecond
response) -- a live red-team probe hit this: the prompt-defense SECURITY
VERDICT was correct, then the conversation-logging INSERT died with a
CheckViolation, surfacing as a 500 instead of the clean 400 the caller
already decided on. Two independent pins:

1. The clamp: response_time_ms=max(1, elapsed_ms) at the write site (0 was
   never a legitimate value to store -- semantically honest, not a
   workaround).

2. The contract fix: _log_conversation's DB write is now SAVEPOINT-
   isolated (async with self.db.begin_nested(): ...; await self.db.flush())
   so ANY future logging-side DB error -- this constraint or a different
   one -- logs a warning and the caller's response still completes,
   instead of only surfacing later at the outer commit.

Mock session, not real SQLite: AIConversationLog declares Postgres-only
JSONB columns (conversation_context, ai_state_snapshot), same class of
UnsupportedCompilationError block as Player.reputation hit in
test_aria_mi_column_savepoint.py -- a real __table__.create() against
SQLite isn't viable here without dialect-swapping surgery outside this
codebase's established pattern. This proves exception-swallowing control
flow (the WO's own explicit ask, "mock or real-SQLite, your call"), not a
genuine cross-statement transaction-poisoning scenario the way the sync
SAVEPOINT falsifiers elsewhere in this suite do -- said explicitly, not
oversold (mirrors the reference_sqlite_no_read_poisoning lesson's honesty
requirement even though this uses a mock rather than SQLite).
"""
from __future__ import annotations

import uuid
from typing import Any, Dict

import pytest
from sqlalchemy.exc import IntegrityError

from src.models.enhanced_ai_models import SecurityLevel
from src.services.enhanced_ai_service import ConversationContext, EnhancedAIService


class _FakeNestedTxn:
    """Async context manager standing in for AsyncSession.begin_nested().
    No real transactional backing -- a raised exception inside the `with`
    block propagates via Python's own async-context-manager protocol;
    _log_conversation's own try/except is what actually absorbs it.
    Mirrors _NoOpSavepoint's sync sibling (test_aria_market_observation.py /
    test_aria_observation_log.py)."""

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False  # never swallow here -- let the SUT's own try/except handle it


class _FakeLogSession:
    def __init__(self, *, flush_raises: Exception | None = None):
        self.added: list = []
        self.flush_raises = flush_raises
        self.flush_calls = 0

    def add(self, obj):
        self.added.append(obj)

    def begin_nested(self):
        return _FakeNestedTxn()

    async def flush(self):
        self.flush_calls += 1
        if self.flush_raises is not None:
            raise self.flush_raises


def _context(**overrides) -> ConversationContext:
    defaults: Dict[str, Any] = dict(
        session_id=str(uuid.uuid4()),
        conversation_type="query",
        player_id=str(uuid.uuid4()),
        assistant_id=str(uuid.uuid4()),
        security_level=SecurityLevel.STANDARD,
        current_topic="trading",
        conversation_history=[],
    )
    defaults.update(overrides)
    return ConversationContext(**defaults)


@pytest.mark.unit
class TestChatlogConstraintClamp:
    @pytest.mark.asyncio
    async def test_zero_elapsed_ms_is_stored_as_one_not_zero(self) -> None:
        """The falsifier for the clamp: a 0ms template-path reply must
        never reach the DB as response_time_ms=0 (violates
        positive_response_time)."""
        db = _FakeLogSession()
        service = EnhancedAIService(db)
        context = _context()

        await service._log_conversation(
            uuid.uuid4(), "hello", "hi there", context, elapsed_ms=0,
        )

        assert len(db.added) == 1
        assert db.added[0].response_time_ms == 1
        assert db.flush_calls == 1

    @pytest.mark.asyncio
    async def test_normal_nonzero_elapsed_ms_is_stored_unclamped(self) -> None:
        """Companion happy-path pin -- the clamp introduces no behavior
        change for a real, measured elapsed time."""
        db = _FakeLogSession()
        service = EnhancedAIService(db)
        context = _context()

        await service._log_conversation(
            uuid.uuid4(), "hello", "hi there", context, elapsed_ms=42,
        )

        assert len(db.added) == 1
        assert db.added[0].response_time_ms == 42
        assert db.flush_calls == 1


@pytest.mark.unit
class TestChatlogWriteIsBestEffort:
    @pytest.mark.asyncio
    async def test_integrity_error_in_the_log_write_does_not_propagate(self, caplog) -> None:
        """The WO's own falsifier: a forced CheckViolation-shaped
        IntegrityError inside the savepoint must be absorbed here -- the
        caller (the route that already decided on a 200/400 verdict) must
        never see this method raise. An unguarded write would let this
        propagate straight out and turn a correct verdict into a 500."""
        integrity_error = IntegrityError(
            "INSERT INTO ai_conversation_logs ...", {}, Exception("CheckViolation: positive_response_time"),
        )
        db = _FakeLogSession(flush_raises=integrity_error)
        service = EnhancedAIService(db)
        context = _context()

        # Must not raise -- if _log_conversation propagates, this call
        # itself fails the test with the uncaught IntegrityError.
        with caplog.at_level("ERROR"):
            await service._log_conversation(
                uuid.uuid4(), "hello", "hi there", context, elapsed_ms=0,
            )

        assert db.flush_calls == 1
        assert any("Failed to log conversation" in record.message for record in caplog.records)

    @pytest.mark.asyncio
    async def test_normal_path_logging_is_unaffected_by_the_savepoint_wrap(self) -> None:
        """Companion happy-path pin -- wrapping the write in
        begin_nested()/flush() introduces no behavior change when nothing
        raises: the row is still added and flushed exactly once."""
        db = _FakeLogSession()
        service = EnhancedAIService(db)
        context = _context()

        await service._log_conversation(
            uuid.uuid4(), "hello", "hi there", context, elapsed_ms=15,
        )

        assert len(db.added) == 1
        assert db.added[0].response_time_ms == 15
        assert db.flush_calls == 1
