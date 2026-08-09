"""WO-ESCALATE-FIRST-COLONY-GUARD-AI-OUTCOME-CALLS-NONEXISTENT-METHOD.

`FirstLoginService._generate_guard_outcome_response_async` called two
nonexistent methods (`self.ai_service.build_outcome_generation_prompt` and
`self.ai_provider_service.generate_outcome`) -- every call raised
AttributeError, silently swallowed by the surrounding `except Exception`,
so the guard's outcome message was ALWAYS the static rule-based fallback,
never real AI-generated text.

Fixed by calling the real, already-shipped
`AIProviderService.generate_outcome_text(...)` (ai_provider_service.py:684)
directly with its 9 named params gathered from the session -- it builds its
own prompt internally, no separate prompt-build step needed.

DB-free: `_generate_guard_outcome_response_async` only touches the DB to
list `DialogueExchange` rows for the session; a fake `db.query(...)` chain
returning an empty list is enough since the fix under test doesn't depend
on conversation history content.
"""
import types
import uuid

import pytest

from src.models.first_login import DialogueOutcome, NegotiationSkillLevel, ShipChoice
from src.services.ai_provider_service import ProviderType
from src.services.first_login_service import FirstLoginService


class _FakeExchangeQuery:
    """`.filter_by(session_id=...).order_by(...).all()` -> always []."""
    def filter_by(self, **kw):
        return self

    def order_by(self, *a):
        return self

    def all(self):
        return []


class _FakeDB:
    def query(self, model):
        return _FakeExchangeQuery()


def _session(
    outcome=DialogueOutcome.SUCCESS,
    ship_claimed=ShipChoice.LIGHT_FREIGHTER,
    awarded_ship=ShipChoice.LIGHT_FREIGHTER,
    negotiation_skill=NegotiationSkillLevel.AVERAGE,
):
    return types.SimpleNamespace(
        id=uuid.uuid4(),
        guard_name="Marsh",
        guard_title="Dockmaster",
        guard_trait="gruff",
        outcome=outcome,
        ship_claimed=ship_claimed,
        awarded_ship=awarded_ship,
        final_persuasion_score=72.5,
        negotiation_skill=negotiation_skill,
    )


def _make_service(ai_provider_service):
    svc = FirstLoginService(db=_FakeDB(), ai_service=object())
    svc.ai_provider_service = ai_provider_service
    return svc


class _RecordingAIProviderService:
    """Stands in for AIProviderService.generate_outcome_text -- records the
    exact kwargs it was called with and returns a scripted (text, provider)
    pair, proving the call reaches the real method's signature rather than
    the deleted nonexistent one."""
    def __init__(self, response, provider):
        self.response = response
        self.provider = provider
        self.calls = []

    async def generate_outcome_text(self, **kwargs):
        self.calls.append(kwargs)
        return self.response, self.provider


@pytest.mark.asyncio
async def test_calls_generate_outcome_text_with_the_real_signature_and_returns_ai_text():
    session = _session()
    ai = _RecordingAIProviderService("Papers check out. Cleared for departure.", ProviderType.ANTHROPIC)
    svc = _make_service(ai)

    result = await svc._generate_guard_outcome_response_async(session)

    assert len(ai.calls) == 1
    call = ai.calls[0]
    assert call["guard_name"] == "Marsh"
    assert call["guard_title"] == "Dockmaster"
    assert call["guard_trait"] == "gruff"
    assert call["outcome_type"] == "SUCCESS"
    assert call["claimed_ship"] == "LIGHT_FREIGHTER"
    assert call["awarded_ship"] == "LIGHT_FREIGHTER"
    assert call["final_score"] == 72.5
    assert call["negotiation_skill"] == "AVERAGE"
    assert call["conversation_history"] == []

    assert result == "[AI-ANTHROPIC] Papers check out. Cleared for departure."


@pytest.mark.asyncio
async def test_ship_claimed_none_falls_back_to_escape_pod_label():
    session = _session(ship_claimed=None)
    ai = _RecordingAIProviderService("text", ProviderType.OPENAI)
    svc = _make_service(ai)

    await svc._generate_guard_outcome_response_async(session)

    assert ai.calls[0]["claimed_ship"] == "ESCAPE_POD"


@pytest.mark.asyncio
async def test_negotiation_skill_none_falls_back_to_average_label():
    session = _session(negotiation_skill=None)
    ai = _RecordingAIProviderService("text", ProviderType.OPENAI)
    svc = _make_service(ai)

    await svc._generate_guard_outcome_response_async(session)

    assert ai.calls[0]["negotiation_skill"] == "AVERAGE"


@pytest.mark.asyncio
async def test_manual_provider_response_falls_back_to_static_text():
    """generate_outcome_text's own documented failure mode is
    (None, ProviderType.MANUAL) when every AI provider is unavailable --
    must route to the static fallback, never return a broken '[AI-MANUAL]
    None' string."""
    session = _session(outcome=DialogueOutcome.SUCCESS, ship_claimed=ShipChoice.ESCAPE_POD)
    ai = _RecordingAIProviderService(None, ProviderType.MANUAL)
    svc = _make_service(ai)

    result = await svc._generate_guard_outcome_response_async(session)

    assert result == svc._generate_guard_outcome_response_fallback(session)
    assert "[AI-" not in result


@pytest.mark.asyncio
async def test_exception_from_ai_provider_falls_back_to_static_text():
    session = _session(outcome=DialogueOutcome.FAILURE, ship_claimed=ShipChoice.ESCAPE_POD)

    class _RaisingAIProviderService:
        async def generate_outcome_text(self, **kwargs):
            raise RuntimeError("boom")

    svc = _make_service(_RaisingAIProviderService())

    result = await svc._generate_guard_outcome_response_async(session)

    assert result == svc._generate_guard_outcome_response_fallback(session)
