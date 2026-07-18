"""WO-DRIFT-aria-rt-mem-readpath-dead (add-on D) -- ARIAPersonalMemory has
been write-only since inception: content lands encrypted via
``_encrypt_memory`` (record_trade_memory / record_combat_memory / etc.)
but ``_decrypt_memory`` had zero callers. This pins the new
``ARIAPersonalIntelligenceService.recall_memories`` read path:

1. TestRecallRoundTrips -- a memory encrypted via ``_encrypt_memory`` and
   recalled via ``recall_memories`` decrypts back to the exact original
   content, plus memory_type filtering and the corrupt-row skip-not-raise
   behavior.
2. TestRecallIsolation -- THE LOAD-BEARING SECURITY PIN (ADR-0016, OWASP
   A01). Two players' rows sit in the same fake session; recalling for
   player A must return ONLY player A's decrypted content and never
   player B's, and vice versa -- proving the isolation guard lives in the
   WHERE-clause filter ``recall_memories`` builds, not a post-fetch
   check. A player with no rows at all recalls empty (the "or empty" half
   of the WO's "403 (or empty)" contract -- the route itself supplies the
   403-equivalent by construction: there is no player-id parameter to
   spoof, ``current_player.id`` is the only id ever passed in).

DB-free throughout, following [[reference_async_fake_session_column_
descriptions]]: FakeMemorySession decodes the real
``select(ARIAPersonalMemory).where(...).order_by(...).limit(...)``
statement ``recall_memories`` issues via ``column_descriptions``/
``whereclause`` against real ORM instances -- verified against the actual
API via a throwaway python3 -c probe before writing this fake (order_by/
limit do not change ``.whereclause`` shape).
"""
from __future__ import annotations

import base64
import uuid
from datetime import UTC, datetime

import pytest
from cryptography.fernet import Fernet

from src.models.aria_personal_intelligence import ARIAPersonalMemory
from src.services.aria_personal_intelligence_service import (
    ARIAPersonalIntelligenceService,
)

PLAYER_A = uuid.uuid4()
PLAYER_B = uuid.uuid4()


# ---------------------------------------------------------------------------
# Fake AsyncSession -- ARIAPersonalMemory select(...).where(...) only
# ---------------------------------------------------------------------------

def _eval_where(where, row):
    if where is None:
        return True
    if hasattr(where, "clauses"):
        return all(_eval_where(c, row) for c in where.clauses)  # and_()
    key = where.left.key
    value = getattr(row, key, None)
    rhs = where.right.value if hasattr(where.right, "value") else where.right
    opname = getattr(where.operator, "__name__", None)
    if opname == "eq":
        return value == rhs
    raise NotImplementedError(f"fake session: unsupported operator {where.operator!r}")


class _FakeResult:
    def __init__(self, rows):
        self._rows = list(rows)

    def scalars(self):
        return self

    def all(self):
        return list(self._rows)


class FakeMemorySession:
    """Minimal AsyncSession double for
    ``select(ARIAPersonalMemory).where(...).order_by(...).limit(...)``.
    order_by/limit are chained onto the statement by ``recall_memories``
    but don't change ``.whereclause``'s shape, so this fake evaluates the
    WHERE clause only and returns every matching row -- sufficient for
    this file's pins, none of which seed more than a handful of rows."""

    def __init__(self, rows=()):
        self.rows = list(rows)
        self.executed = 0

    async def execute(self, stmt):
        self.executed += 1
        entity = stmt.column_descriptions[0]["entity"]
        assert entity is ARIAPersonalMemory
        matched = [r for r in self.rows if _eval_where(stmt.whereclause, r)]
        return _FakeResult(matched)

    async def commit(self):
        pass


def _memory(player_id, memory_content, memory_type="market", **overrides):
    kwargs = dict(
        id=uuid.uuid4(),
        player_id=str(player_id),
        memory_type=memory_type,
        importance_score=0.7,
        confidence_level=0.9,
        memory_content=memory_content,
        memory_hash=uuid.uuid4().hex,
        created_at=datetime.now(UTC),
        access_count=0,
    )
    kwargs.update(overrides)
    return ARIAPersonalMemory(**kwargs)


@pytest.fixture()
def service() -> ARIAPersonalIntelligenceService:
    return ARIAPersonalIntelligenceService()


# ---------------------------------------------------------------------------
# 1. Round trip + filtering + corrupt-row resilience
# ---------------------------------------------------------------------------

class TestRecallRoundTrips:
    @pytest.mark.asyncio
    async def test_recall_decrypts_back_to_original_content(self, service):
        content = {"event": "trade_transaction", "commodity": "organics", "profit": 500}
        encrypted = service._encrypt_memory(content)
        memory = _memory(PLAYER_A, {"encrypted": encrypted})
        db = FakeMemorySession(rows=[memory])

        recalled = await service.recall_memories(str(PLAYER_A), db)

        assert len(recalled) == 1
        assert recalled[0]["content"] == content
        assert recalled[0]["id"] == str(memory.id)
        assert recalled[0]["memory_type"] == "market"

    @pytest.mark.asyncio
    async def test_recall_bumps_access_tracking_fields(self, service):
        encrypted = service._encrypt_memory({"event": "sector_exploration"})
        memory = _memory(PLAYER_A, {"encrypted": encrypted}, access_count=2)
        db = FakeMemorySession(rows=[memory])

        await service.recall_memories(str(PLAYER_A), db)

        assert memory.access_count == 3

    @pytest.mark.asyncio
    async def test_memory_type_filter_narrows_results(self, service):
        market = _memory(
            PLAYER_A, {"encrypted": service._encrypt_memory({"event": "trade"})}, memory_type="market"
        )
        combat = _memory(
            PLAYER_A, {"encrypted": service._encrypt_memory({"event": "fight"})}, memory_type="threat.combat"
        )
        db = FakeMemorySession(rows=[market, combat])

        recalled = await service.recall_memories(str(PLAYER_A), db, memory_type="threat.combat")

        assert len(recalled) == 1
        assert recalled[0]["content"] == {"event": "fight"}

    @pytest.mark.asyncio
    async def test_no_memories_recalls_empty_list(self, service):
        db = FakeMemorySession(rows=[])
        recalled = await service.recall_memories(str(PLAYER_A), db)
        assert recalled == []

    @pytest.mark.asyncio
    async def test_corrupt_row_is_skipped_not_raised(self, service):
        """A row whose content fails to decrypt (mismatched key generation,
        truncated ciphertext) must be skipped, not crash the whole recall
        -- proven with a NEGATIVE control (a genuinely different key) so
        this test is discriminating, mirroring test_aria_encryption_key.py's
        mismatched-key pattern."""
        good_content = {"event": "trade_transaction"}
        good = _memory(PLAYER_A, {"encrypted": service._encrypt_memory(good_content)})

        other_key = Fernet.generate_key()
        bad_ciphertext = Fernet(other_key).encrypt(b'{"event": "unreadable"}')
        bad = _memory(PLAYER_A, {"encrypted": base64.b64encode(bad_ciphertext).decode()})

        db = FakeMemorySession(rows=[good, bad])

        recalled = await service.recall_memories(str(PLAYER_A), db)

        assert len(recalled) == 1
        assert recalled[0]["content"] == good_content


# ---------------------------------------------------------------------------
# 2. Isolation -- the load-bearing security pin
# ---------------------------------------------------------------------------

class TestRecallIsolation:
    @pytest.mark.asyncio
    async def test_player_a_recall_excludes_player_b_content(self, service):
        secret_a = {"secret": "trade route to Sol", "owner": "A"}
        secret_b = {"secret": "trade route to Rylan", "owner": "B"}
        memory_a = _memory(PLAYER_A, {"encrypted": service._encrypt_memory(secret_a)})
        memory_b = _memory(PLAYER_B, {"encrypted": service._encrypt_memory(secret_b)})
        db = FakeMemorySession(rows=[memory_a, memory_b])

        recalled_for_a = await service.recall_memories(str(PLAYER_A), db)

        assert len(recalled_for_a) == 1
        assert recalled_for_a[0]["content"] == secret_a
        # Discriminating: player A's recall must never surface player B's
        # plaintext anywhere in the result, not just "the count is right".
        assert not any(m["content"] == secret_b for m in recalled_for_a)
        assert not any(m["id"] == str(memory_b.id) for m in recalled_for_a)

    @pytest.mark.asyncio
    async def test_player_b_recall_excludes_player_a_content(self, service):
        """Mirror of the above -- proves the filter isn't accidentally
        one-directional (e.g. hardcoded to always exclude memory_b)."""
        secret_a = {"secret": "trade route to Sol", "owner": "A"}
        secret_b = {"secret": "trade route to Rylan", "owner": "B"}
        memory_a = _memory(PLAYER_A, {"encrypted": service._encrypt_memory(secret_a)})
        memory_b = _memory(PLAYER_B, {"encrypted": service._encrypt_memory(secret_b)})
        db = FakeMemorySession(rows=[memory_a, memory_b])

        recalled_for_b = await service.recall_memories(str(PLAYER_B), db)

        assert len(recalled_for_b) == 1
        assert recalled_for_b[0]["content"] == secret_b
        assert not any(m["content"] == secret_a for m in recalled_for_b)

    @pytest.mark.asyncio
    async def test_stranger_with_no_rows_recalls_empty_not_error(self, service):
        """The "or empty" half of the WO's 403/empty contract: a player_id
        with zero rows in the store (the route can never construct a
        request for anyone but current_player, so this is the only shape
        a "wrong" request can take) recalls an empty list, never raises
        and never leaks another player's rows."""
        memory_a = _memory(PLAYER_A, {"encrypted": service._encrypt_memory({"secret": "A only"})})
        db = FakeMemorySession(rows=[memory_a])

        stranger_id = uuid.uuid4()
        recalled = await service.recall_memories(str(stranger_id), db)

        assert recalled == []

    @pytest.mark.asyncio
    async def test_query_level_filter_not_post_fetch(self, service):
        """Pins the isolation mechanism itself: the fake session's execute()
        only ever sees rows matching the WHERE clause (that's what
        FakeMemorySession.execute does) -- if recall_memories filtered
        AFTER fetching all rows instead of building the WHERE clause,
        this test's own db.rows (both players mixed) would still produce
        the right per-call filtering, so we additionally assert the
        statement's whereclause literally carries player_id as a
        top-level eq condition, not something recall_memories forgot to
        wire in and got right only via the fake's evaluation of it."""
        memory_a = _memory(PLAYER_A, {"encrypted": service._encrypt_memory({"secret": "A"})})
        db = FakeMemorySession(rows=[memory_a])

        captured = {}
        real_execute = db.execute

        async def _spy_execute(stmt):
            captured["whereclause"] = stmt.whereclause
            return await real_execute(stmt)

        db.execute = _spy_execute

        await service.recall_memories(str(PLAYER_A), db)

        where = captured["whereclause"]
        conditions = list(where.clauses) if hasattr(where, "clauses") else [where]
        player_conditions = [c for c in conditions if getattr(c.left, "key", None) == "player_id"]
        assert len(player_conditions) == 1
        assert player_conditions[0].right.value == str(PLAYER_A)
