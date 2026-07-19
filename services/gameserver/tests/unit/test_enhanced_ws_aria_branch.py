"""Unit coverage for EnhancedWebSocketService._handle_aria_chat (WO-ARIA-WS-
DEADBRANCH).

Bug pinned: the branch imported ``get_enhanced_ai_service`` from
``enhanced_ai_service.py`` and called
``self.enhanced_ai_service.process_player_query(...)`` -- neither symbol
exists anywhere in the codebase. The ImportError was swallowed by the
branch's own except-clause, so every ``aria_chat`` message over
``/ws/trading`` silently produced "ARIA is temporarily unavailable" instead
of ever reaching ARIA's real (deterministic, template-based) response
engine, ``EnhancedAIService.process_natural_language_query``.

Pure Python -- ``EnhancedAIService`` and ``AsyncSessionLocal`` are faked at
the names ``enhanced_websocket_service`` imports them under, so no live DB
is needed and the real template-generation logic in
``enhanced_ai_service.py`` is out of scope for this file (it already has
its own coverage).
"""
from __future__ import annotations

import inspect
from typing import Any, Dict, List
from uuid import UUID, uuid4

import pytest

import src.services.enhanced_websocket_service as ews_module
from src.services.enhanced_websocket_service import EnhancedWebSocketService


class _FakeAriaDB:
    """Stands in for the AsyncSession EnhancedAIService binds at
    construction. Only .commit() is exercised by the branch under test."""

    def __init__(self) -> None:
        self.committed = False

    async def commit(self) -> None:
        self.committed = True


class _FakeAsyncSessionLocalCM:
    def __init__(self, db: _FakeAriaDB) -> None:
        self._db = db

    async def __aenter__(self) -> _FakeAriaDB:
        return self._db

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        return False


def _fake_async_session_local_factory(fake_dbs: List[_FakeAriaDB]):
    """Mimics AsyncSessionLocal: calling it returns a fresh async context
    manager, same as the real `AsyncSessionLocal()`."""

    def _factory():
        db = _FakeAriaDB()
        fake_dbs.append(db)
        return _FakeAsyncSessionLocalCM(db)

    return _factory


class _FakeEnhancedAIService:
    """Replaces the real EnhancedAIService with something that returns the
    exact dict shape process_natural_language_query produces on success
    (see enhanced_ai_service.py:835-840), so the WS branch's response
    mapping is exercised against the real contract without a live DB."""

    last_instance: "_FakeEnhancedAIService | None" = None

    def __init__(self, db_session: _FakeAriaDB) -> None:
        self.db_session = db_session
        self.calls: List[Dict[str, Any]] = []
        _FakeEnhancedAIService.last_instance = self

    async def process_natural_language_query(self, player_id, user_input, conversation_id=None):
        self.calls.append(
            {
                "player_id": player_id,
                "user_input": user_input,
                "conversation_id": conversation_id,
            }
        )
        return {
            "response": (
                "Based on current market analysis, here are my top trading "
                "recommendations:\n\n1. Ore run to sector 12\n"
            ),
            "intent": {
                "primary_intent": "trading",
                "confidence": 0.9,
                "all_intents": {"trading": 2},
                "entities": {"sectors": [], "commodities": [], "numbers": [], "actions": []},
                "original_input": user_input,
                "sanitized_input": user_input,
            },
            "conversation_id": conversation_id or "generated-convo-id",
            "response_time": "2026-07-09T00:00:00+00:00",
        }


class _BoomEnhancedAIService:
    """Simulates the real service genuinely failing (e.g. DB error) so the
    branch's own except-clause / fallback is still pinned."""

    def __init__(self, db_session: _FakeAriaDB) -> None:
        pass

    async def process_natural_language_query(self, *args, **kwargs):
        raise RuntimeError("boom")


def test_no_phantom_symbols_remain_in_module_source():
    """get_enhanced_ai_service / process_player_query must not appear
    anywhere in the module -- not as a live call, not as a stray reference."""
    source = inspect.getsource(ews_module)
    assert "get_enhanced_ai_service" not in source
    assert "process_player_query" not in source


@pytest.mark.unit
@pytest.mark.asyncio
class TestAriaChatBranchWiring:
    async def test_aria_chat_yields_real_template_response_not_unavailable(self, monkeypatch):
        fake_dbs: List[_FakeAriaDB] = []
        monkeypatch.setattr(ews_module, "EnhancedAIService", _FakeEnhancedAIService)
        monkeypatch.setattr(
            ews_module, "AsyncSessionLocal", _fake_async_session_local_factory(fake_dbs)
        )

        service = EnhancedWebSocketService(redis_client=None)
        player_id = str(uuid4())
        conversation_id = str(uuid4())

        sent: List[Any] = []

        async def _recording_send_message(pid, payload):
            sent.append((pid, payload))

        monkeypatch.setattr(service, "send_message", _recording_send_message)

        message = {
            "content": "What are the best trading opportunities right now?",
            "conversation_id": conversation_id,
            "context": "trading",
        }

        await service._handle_aria_chat(player_id, message, db=None, session=None)

        assert len(sent) == 1
        sent_player_id, payload = sent[0]
        assert sent_player_id == player_id
        assert payload["type"] == "aria_response"
        assert payload["data"]["message"] != "ARIA is temporarily unavailable"
        assert "Based on current market analysis" in payload["data"]["message"]
        assert payload["data"]["confidence"] == 0.9
        assert payload["data"]["context_used"] == "trading"
        assert payload["data"]["actions"] == []
        assert payload["conversation_id"] == conversation_id

        # Constructed with the fresh per-call session, not the connection's
        # long-lived `db` (which was None here) -- the whole point of the fix.
        assert _FakeEnhancedAIService.last_instance.db_session is fake_dbs[0]
        assert fake_dbs[0].committed is True

        call = _FakeEnhancedAIService.last_instance.calls[0]
        assert call["player_id"] == UUID(player_id)
        assert call["user_input"] == message["content"]
        assert call["conversation_id"] == conversation_id

    async def test_aria_chat_still_reports_unavailable_on_real_failure(self, monkeypatch):
        """Regression: when the AI service genuinely raises, the player
        still gets the existing fallback via send_error -- not a raw
        traceback and not a silently dropped message."""
        monkeypatch.setattr(ews_module, "EnhancedAIService", _BoomEnhancedAIService)
        monkeypatch.setattr(ews_module, "AsyncSessionLocal", _fake_async_session_local_factory([]))

        service = EnhancedWebSocketService(redis_client=None)
        player_id = str(uuid4())

        errors: List[Any] = []

        async def _recording_send_error(pid, msg, code="ERROR"):
            errors.append((pid, msg, code))

        monkeypatch.setattr(service, "send_error", _recording_send_error)

        message = {"content": "hello", "conversation_id": str(uuid4())}
        await service._handle_aria_chat(player_id, message, db=None, session=None)

        assert errors == [(player_id, "ARIA is temporarily unavailable", "ERROR")]
