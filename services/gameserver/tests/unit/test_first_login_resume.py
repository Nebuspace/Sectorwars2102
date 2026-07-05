"""WO-PUX-FLOGIN-RESUME — first-login resume: no silent reset-on-reload.

FirstLoginService.get_session_with_history (first_login_service.py, added
right after get_or_create_session) is the read-only resume lookup that
POST /session (routes/first_login.py) now checks before ever provisioning a
new session or regenerating the initial AI prompt. This asserts:
  1. An in-progress mid-dialogue session resumes AS-IS with its full
     persisted DialogueExchange history and persisted guard_* identity.
  2. A session that has reached its dialogue outcome but hasn't yet called
     /complete ALSO stays resumable — completed_at is set at outcome-scoring
     time, before /complete (_evaluate_dialogue_outcome,
     first_login_service.py:1513-1514), so keying resumability off that
     column instead of state.has_completed_first_login would silently spin
     up a duplicate session (new guard, new ships) right at the outcome
     screen. This is the refresh-to-re-roll exploit the WO closes.
  3. A player with no active session (or whose flow is fully complete)
     gets no resume — the route's fresh-provisioning path takes over.
  4. The route dispatch itself (POST /session, called directly) wires the
     resume payload through unchanged.

DB-free: SimpleNamespace session/state/exchanges + a tiny fake query layer,
in the house style of test_first_login_starter_resistances.py, extended
with a chainable order_by() no-op (fixture exchanges are pre-sorted).
"""
import types
import uuid

import pytest

from src.services.first_login_service import FirstLoginService
from src.api.routes.first_login import start_first_login_session
from src.models.first_login import (
    ShipChoice,
    DialogueOutcome,
    NegotiationSkillLevel,
    FirstLoginSession,
    DialogueExchange,
    PlayerFirstLoginState,
    ShipPresentationOptions,
    ShipRarityConfig,
)


def _guard_session(session_id, player_id, **overrides):
    base = dict(
        id=session_id,
        player_id=player_id,
        guard_name="Chen",
        guard_title="Security Officer",
        guard_trait="Friendly Veteran",
        guard_base_suspicion=0.3,
        guard_description="Experienced officer who's seen it all and can spot a good story",
        ship_claimed=None,
        outcome=None,
        awarded_ship=None,
        negotiation_skill=None,
        final_persuasion_score=None,
        starting_credits=None,
        negotiation_bonus_flag=False,
        notoriety_penalty=False,
    )
    base.update(overrides)
    return types.SimpleNamespace(**base)


def _exchange(session_id, seq, npc_prompt, player_response="", **scores):
    return types.SimpleNamespace(
        id=uuid.uuid4(),
        session_id=session_id,
        sequence_number=seq,
        npc_prompt=npc_prompt,
        player_response=player_response,
        persuasiveness=scores.get("persuasiveness"),
        confidence=scores.get("confidence"),
        consistency=scores.get("consistency"),
    )


def _state(player_id, session_id, has_completed_first_login=False):
    return types.SimpleNamespace(
        player_id=player_id,
        current_session_id=session_id,
        has_completed_first_login=has_completed_first_login,
        attempts=1,
    )


class _FakeQuery:
    """Minimal SQLAlchemy Query stand-in: returns the object(s) registered
    for the model being queried, ignoring filter/filter_by arguments — the
    house pattern from test_first_login_starter_resistances.py, extended
    with a chainable order_by() no-op (fixture data is provided pre-sorted)."""
    def __init__(self, obj):
        self._obj = obj

    def filter(self, *a, **k):
        return self

    def filter_by(self, **k):
        return self

    def order_by(self, *a, **k):
        return self

    def first(self):
        if isinstance(self._obj, list):
            return self._obj[0] if self._obj else None
        return self._obj

    def all(self):
        if self._obj is None:
            return []
        return self._obj if isinstance(self._obj, list) else [self._obj]


class _FakeDB:
    """mapping: {model_class: instance_or_list_to_return}"""
    def __init__(self, mapping):
        self._mapping = mapping
        self.added = []
        self.committed = False

    def query(self, model):
        return _FakeQuery(self._mapping.get(model))

    def add(self, obj):
        self.added.append(obj)

    def flush(self):
        pass

    def commit(self):
        self.committed = True

    def refresh(self, obj):
        pass


def _service(mapping):
    # ai_service truthy sentinel — skips constructing the real
    # AIDialogueService (avoids provider setup unrelated to this test).
    db = _FakeDB(mapping)
    svc = FirstLoginService(db=db, ai_service=object())
    return svc, db


# --- FirstLoginService.get_session_with_history --------------------------

def test_resume_mid_dialogue_returns_full_history_and_guard_identity():
    player_id = uuid.uuid4()
    session_id = uuid.uuid4()
    session = _guard_session(session_id, player_id, ship_claimed=ShipChoice.SCOUT_SHIP)
    exchanges = [
        _exchange(session_id, 1, "Which vessel belongs to you?", "The Scout Ship, obviously.",
                  persuasiveness=0.6, confidence=0.5, consistency=0.8),
        _exchange(session_id, 2, "What's your registration number?", ""),  # pending — the current question
    ]
    ship_options = types.SimpleNamespace(session_id=session_id, available_ships=["ESCAPE_POD", "SCOUT_SHIP"])
    state = _state(player_id, session_id)

    svc, db = _service({
        PlayerFirstLoginState: state,
        FirstLoginSession: session,
        DialogueExchange: exchanges,
        ShipPresentationOptions: ship_options,
    })

    result = svc.get_session_with_history(player_id)

    assert result is not None
    assert result["session"].id == session_id
    assert result["current_step"] == "dialogue"
    assert len(result["dialogue_history"]) == 2
    assert result["dialogue_history"][0]["player_response"] == "The Scout Ship, obviously."
    assert result["dialogue_history"][1]["npc_prompt"] == "What's your registration number?"
    assert result["dialogue_history"][1]["player_response"] == ""
    assert result["npc_prompt"] == "What's your registration number?"
    assert result["exchange_id"] == str(exchanges[1].id)
    assert result["outcome"] is None
    assert result["session"].guard_name == "Chen"
    assert result["session"].guard_title == "Security Officer"
    assert result["session"].guard_trait == "Friendly Veteran"
    assert result["session"].guard_base_suspicion == 0.3
    assert result["session"].guard_description == session.guard_description
    # Pure read — resume must never write (the whole point of "AS-IS").
    assert db.added == []
    assert db.committed is False


def test_resume_at_completion_step_before_complete_endpoint_called():
    """Reload after the dialogue outcome is scored but before /complete —
    completed_at is already set at this point while has_completed_first_login
    is still False. Resume must still return this session, not spin up a
    new one (the refresh-to-re-roll exploit the WO closes)."""
    player_id = uuid.uuid4()
    session_id = uuid.uuid4()
    session = _guard_session(
        session_id, player_id,
        ship_claimed=ShipChoice.SCOUT_SHIP,
        outcome=DialogueOutcome.SUCCESS,
        awarded_ship=ShipChoice.SCOUT_SHIP,
        negotiation_skill=NegotiationSkillLevel.STRONG,
        final_persuasion_score=0.82,
        starting_credits=2000,
        negotiation_bonus_flag=True,
        notoriety_penalty=False,
    )
    exchanges = [
        _exchange(session_id, i, f"Question {i}", f"Answer {i}",
                  persuasiveness=0.8, confidence=0.8, consistency=0.8)
        for i in range(1, 6)
    ]
    ship_options = types.SimpleNamespace(session_id=session_id, available_ships=["ESCAPE_POD", "SCOUT_SHIP"])
    state = _state(player_id, session_id, has_completed_first_login=False)

    svc, db = _service({
        PlayerFirstLoginState: state,
        FirstLoginSession: session,
        DialogueExchange: exchanges,
        ShipPresentationOptions: ship_options,
    })

    result = svc.get_session_with_history(player_id)

    assert result is not None, "outcome-reached-but-not-yet-completed session must stay resumable"
    assert result["current_step"] == "completion"
    assert result["exchange_id"] is None  # nothing pending — dialogue is fully answered
    assert len(result["dialogue_history"]) == 5
    assert result["outcome"]["outcome"] == "SUCCESS"
    assert result["outcome"]["awarded_ship"] == "SCOUT_SHIP"
    assert result["outcome"]["starting_credits"] == 2000
    assert result["outcome"]["negotiation_skill"] == "STRONG"
    assert result["outcome"]["negotiation_bonus"] is True
    assert result["outcome"]["notoriety_penalty"] is False
    # Never re-invokes the AI provider on resume (no cost, no
    # non-determinism); the client falls back to its own canonical message.
    assert result["outcome"]["guard_response"] is None


def test_no_active_session_returns_none():
    player_id = uuid.uuid4()
    state = _state(player_id, session_id=None)
    svc, _db = _service({PlayerFirstLoginState: state})
    assert svc.get_session_with_history(player_id) is None


def test_completed_flow_is_never_resumable():
    player_id = uuid.uuid4()
    session_id = uuid.uuid4()
    state = _state(player_id, session_id, has_completed_first_login=True)
    svc, _db = _service({PlayerFirstLoginState: state})
    assert svc.get_session_with_history(player_id) is None


# --- POST /session route dispatch -----------------------------------------

@pytest.mark.asyncio
async def test_post_session_route_returns_resumed_payload():
    """POST /session, called directly (Admin-list-route direct-call
    pattern): resumed:true, same session id, full history, guard_* fields
    equal to the FirstLoginSession row — the session-provisioning /
    prompt-generation path is never touched."""
    player_id = uuid.uuid4()
    session_id = uuid.uuid4()
    session = _guard_session(session_id, player_id, ship_claimed=ShipChoice.SCOUT_SHIP)
    exchanges = [
        _exchange(session_id, 1, "Which vessel belongs to you?", "The Scout Ship, obviously.",
                  persuasiveness=0.6, confidence=0.5, consistency=0.8),
        _exchange(session_id, 2, "What's your registration number?", ""),
    ]
    ship_options = types.SimpleNamespace(session_id=session_id, available_ships=["ESCAPE_POD", "SCOUT_SHIP"])
    state = _state(player_id, session_id)

    db = _FakeDB({
        PlayerFirstLoginState: state,
        FirstLoginSession: session,
        DialogueExchange: exchanges,
        ShipPresentationOptions: ship_options,
        ShipRarityConfig: [],  # initialize_ship_configs seeds all 8 — harmless no-op here
    })
    player = types.SimpleNamespace(id=player_id)

    result = await start_first_login_session(player=player, db=db, ai_service=object())

    assert result["resumed"] is True
    assert result["session_id"] == str(session_id)
    assert len(result["dialogue_history"]) == 2
    assert result["guard_name"] == "Chen"
    assert result["guard_title"] == "Security Officer"
    assert result["guard_trait"] == "Friendly Veteran"
    assert result["guard_base_suspicion"] == 0.3
    assert result["guard_description"] == session.guard_description
    assert result["current_step"] == "dialogue"
    assert result["exchange_id"] == str(exchanges[1].id)
    assert result["ship_claimed"] == "SCOUT_SHIP"
