"""LEG-415 — owner-scoped ARIA export + reset (aria-companion.md:173-175).

Pins:
1. Export decrypts via recall_memories (same Tier-1 shape as GET /ai/memories).
2. Reset deletes only the caller's rows across the six personal tables.
3. Routes take get_current_player only — no player-id spoof parameter.
4. POST /ai/system/cleanup source is unchanged (not imported here).
"""
from __future__ import annotations

import inspect
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.sql.dml import Delete

from src.api.routes import enhanced_ai
from src.models.aria_personal_intelligence import (
    ARIAExplorationMap,
    ARIAMarketIntelligence,
    ARIAPersonalMemory,
    ARIAQuantumCache,
    ARIASecurityLog,
    ARIATradingObservation,
)
from src.services.aria_personal_intelligence_service import (
    ARIAPersonalIntelligenceService,
)

PLAYER_A = uuid.uuid4()
PLAYER_B = uuid.uuid4()

_MODELS = (
    ARIAPersonalMemory,
    ARIAMarketIntelligence,
    ARIAExplorationMap,
    ARIATradingObservation,
    ARIAQuantumCache,
    ARIASecurityLog,
)
_TABLE = {m.__tablename__: m for m in _MODELS}


def _eval_where(where, row):
    if where is None:
        return True
    if hasattr(where, "clauses"):
        return all(_eval_where(c, row) for c in where.clauses)
    key = where.left.key
    value = getattr(row, key, None)
    rhs = where.right.value if hasattr(where.right, "value") else where.right
    opname = getattr(where.operator, "__name__", None)
    if opname == "eq":
        return str(value) == str(rhs)
    raise NotImplementedError(where.operator)


class _FakeResult:
    def __init__(self, rows=(), rowcount=0, scalar=None):
        self._rows = list(rows)
        self.rowcount = rowcount
        self._scalar = scalar

    def scalars(self):
        return self

    def all(self):
        return list(self._rows)

    def scalar_one(self):
        return self._scalar


class FakePersonalStoreSession:
    """AsyncSession double for recall/export/reset statement shapes."""

    def __init__(self, rows_by_model):
        self.rows = {m: list(rs) for m, rs in rows_by_model.items()}
        for model in _MODELS:
            self.rows.setdefault(model, [])

    async def execute(self, stmt):
        if isinstance(stmt, Delete):
            model = _TABLE[stmt.table.name]
            kept, removed = [], []
            for row in self.rows[model]:
                if _eval_where(stmt.whereclause, row):
                    removed.append(row)
                else:
                    kept.append(row)
            self.rows[model] = kept
            return _FakeResult(rowcount=len(removed))

        froms = stmt.get_final_froms()
        table_name = froms[0].name if froms else None
        entity = stmt.column_descriptions[0]["entity"]
        if entity is None and table_name:
            model = _TABLE[table_name]
            matched = [r for r in self.rows[model] if _eval_where(stmt.whereclause, r)]
            return _FakeResult(scalar=len(matched))

        assert entity is ARIAPersonalMemory
        matched = [
            r for r in self.rows[ARIAPersonalMemory]
            if _eval_where(stmt.whereclause, r)
        ]
        return _FakeResult(rows=matched)

    async def commit(self):
        pass


def _memory(player_id, memory_content, memory_type="market"):
    return ARIAPersonalMemory(
        id=uuid.uuid4(),
        player_id=player_id,
        memory_type=memory_type,
        importance_score=0.7,
        confidence_level=0.9,
        memory_content=memory_content,
        memory_hash=uuid.uuid4().hex,
        created_at=datetime.now(UTC),
        access_count=0,
    )


def _intel(player_id):
    return ARIAMarketIntelligence(
        id=uuid.uuid4(),
        player_id=player_id,
        sector_id=uuid.uuid4(),
        commodity="ore",
    )


@pytest.fixture()
def service() -> ARIAPersonalIntelligenceService:
    return ARIAPersonalIntelligenceService()


@pytest.mark.asyncio
async def test_export_decrypts_owner_memories_only(service):
    content_a = {"event": "trade_transaction", "commodity": "organics"}
    content_b = {"event": "should_not_leak"}
    row_a = _memory(PLAYER_A, {"encrypted": service._encrypt_memory(content_a)})
    row_b = _memory(PLAYER_B, {"encrypted": service._encrypt_memory(content_b)})
    intel_a = _intel(PLAYER_A)
    db = FakePersonalStoreSession({
        ARIAPersonalMemory: [row_a, row_b],
        ARIAMarketIntelligence: [intel_a, _intel(PLAYER_B)],
    })

    payload = await service.export_personal_store(str(PLAYER_A), db)

    assert [m["content"] for m in payload["memories"]] == [content_a]
    assert payload["player_id"] == str(PLAYER_A)
    assert payload["related_row_counts"]["aria_personal_memories"] == 1
    assert payload["related_row_counts"]["aria_market_intelligence"] == 1


@pytest.mark.asyncio
async def test_reset_deletes_only_caller_rows_then_recall_empty(service):
    content_a = {"event": "keep_or_wipe_a"}
    content_b = {"event": "keep_b"}
    row_a = _memory(PLAYER_A, {"encrypted": service._encrypt_memory(content_a)})
    row_b = _memory(PLAYER_B, {"encrypted": service._encrypt_memory(content_b)})
    db = FakePersonalStoreSession({
        ARIAPersonalMemory: [row_a, row_b],
        ARIAMarketIntelligence: [_intel(PLAYER_A), _intel(PLAYER_B)],
        ARIATradingObservation: [],
    })

    deleted = await service.reset_personal_store(str(PLAYER_A), db)

    assert deleted["aria_personal_memories"] == 1
    assert deleted["aria_market_intelligence"] == 1
    recalled_a = await service.recall_memories(str(PLAYER_A), db)
    recalled_b = await service.recall_memories(str(PLAYER_B), db)
    assert recalled_a == []
    assert len(recalled_b) == 1
    assert recalled_b[0]["content"] == content_b


def test_dump_path_avoids_admin_report_export_marker():
    export_route = next(
        r for r in enhanced_ai.router.routes
        if getattr(r, "path", "").endswith("/memories/dump")
    )
    assert "/export" not in export_route.path
    assert "/exports" not in export_route.path


def test_export_and_reset_routes_have_no_player_id_parameter():
    export_params = inspect.signature(enhanced_ai.export_aria_personal_store).parameters
    reset_params = inspect.signature(enhanced_ai.reset_aria_personal_store).parameters
    assert "player_id" not in export_params
    assert "player_id" not in reset_params
    assert "current_player" in export_params
    assert "current_player" in reset_params


def test_cleanup_handler_still_uses_validate_ai_access_and_global_wipe():
    src = inspect.getsource(enhanced_ai.cleanup_ai_data)
    assert "validate_ai_access" in src
    assert "cleanup_expired_data" in src
    assert "reset_personal_store" not in src


@pytest.mark.asyncio
async def test_export_route_passes_jwt_player_id_only():
    player = SimpleNamespace(id=PLAYER_A)
    db = AsyncMock()
    payload = {"player_id": str(PLAYER_A), "memories": [], "related_row_counts": {}}
    svc = MagicMock()
    svc.export_personal_store = AsyncMock(return_value=payload)

    with patch(
        "src.services.aria_personal_intelligence_service.get_aria_intelligence_service",
        return_value=svc,
    ):
        result = await enhanced_ai.export_aria_personal_store(
            current_player=player, db=db,
        )

    assert result == payload
    svc.export_personal_store.assert_awaited_once_with(str(PLAYER_A), db)
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_reset_route_then_memories_handler_sees_empty(service):
    content_a = {"event": "wipe_me"}
    row_a = _memory(PLAYER_A, {"encrypted": service._encrypt_memory(content_a)})
    db = FakePersonalStoreSession({ARIAPersonalMemory: [row_a]})
    player = SimpleNamespace(id=PLAYER_A)

    with patch(
        "src.services.aria_personal_intelligence_service.get_aria_intelligence_service",
        return_value=service,
    ):
        reset_result = await enhanced_ai.reset_aria_personal_store(
            current_player=player, db=db,
        )
        memories = await enhanced_ai.get_aria_memories(
            memory_type=None, limit=50, current_player=player, db=db,
        )

    assert reset_result["status"] == "success"
    assert reset_result["deleted"]["aria_personal_memories"] == 1
    assert memories == []
