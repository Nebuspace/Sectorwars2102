"""WO-PUX-FLOGIN-NICKNAME G2 — complete_first_login's nickname-confirmation
gating, replacing the retired unconditional
`player.nickname = session.extracted_player_name` write.

Canon: sw2102-docs/SYSTEMS/first-login.md:249-255 — Player.nickname is
written ONLY when the client explicitly confirmed the callsign AND
nickname_validation_service.validate_nickname passes; declining or failing
validation must never block completion.

DB-free: SimpleNamespace session/player/spec/state + the fake query layer
from test_first_login_starter_resistances.py, extended with a
`_FakePlayerLikeQuery` that dispatches on which SQLAlchemy Query method is
called — `.filter_by(id=...)` (the existing "fetch this player/session row"
lookups) vs `.filter(condition)` (the `func.lower(...) == <str>` uniqueness
scan validate_nickname builds, chainable with the `Player.id !=
exclude_player_id` own-row exclusion from AC-3) — so a single fake can serve
both call sites against the same model class in one method body (fake-
query-filter-interpreter-pattern, dispatching on condition.operator rather
than canned-returning).
"""
import operator
import types
import uuid

import pytest
from fastapi import HTTPException

from src.api.routes.first_login import complete_first_login as complete_first_login_route
from src.services.first_login_service import FirstLoginService, FirstLoginCompletionError
from src.models.first_login import ShipChoice, FirstLoginSession, PlayerFirstLoginState
from src.models.ship import Ship, ShipType, ShipSpecification
from src.models.player import Player
from src.models.user import User


def _session(player_id, extracted_player_name=None, awarded_ship=ShipChoice.LIGHT_FREIGHTER):
    return types.SimpleNamespace(
        id=uuid.uuid4(),
        player_id=player_id,
        completed_at=None,
        starting_credits=1000,
        extracted_player_name=extracted_player_name,
        awarded_ship=awarded_ship,
        negotiation_bonus_flag=False,
        notoriety_penalty=False,
    )


def _player(player_id, username="tester"):
    return types.SimpleNamespace(
        id=player_id,
        username=username,
        nickname=None,
        credits=0,
        current_sector_id=1,
        current_ship_id=None,
        settings={},
        reputation={},
        first_login={},
        aria_relationship_score=0,
        aria_total_interactions=0,
    )


def _state(player_id):
    return types.SimpleNamespace(
        player_id=player_id,
        has_completed_first_login=False,
        received_resources=False,
        attempts=0,
    )


def _spec():
    return types.SimpleNamespace(
        type=ShipType.LIGHT_FREIGHTER,
        shield_resistance=0.02,
        armor_rating=0.03,
    )


class _Result:
    def __init__(self, obj):
        self._obj = obj

    def first(self):
        return self._obj


class _FakePlayerLikeQuery:
    """Serves `.filter_by(id=...)` (returns the pre-registered `single` row)
    and `.filter(condition)` (interprets the real condition against `rows`,
    the uniqueness-collision candidates) — chainable, so a second
    `.filter(Player.id != exclude_player_id)` (AC-3 own-row exclusion)
    narrows `rows` again rather than terminating the chain."""
    def __init__(self, single, rows, column_name):
        self._single = single
        self._rows = rows
        self._column_name = column_name

    def filter_by(self, **kwargs):
        return _Result(self._single)

    def filter(self, condition):
        target = condition.right.value
        if condition.operator is operator.ne:
            matches = [r for r in self._rows if getattr(r, "id", None) != target]
        else:
            matches = [
                r for r in self._rows
                if (getattr(r, self._column_name) or "").lower() == target
            ]
        return _FakePlayerLikeQuery(self._single, matches, self._column_name)

    def first(self):
        return self._rows[0] if self._rows else None


class _FakeQuery:
    """House pattern from test_first_login_starter_resistances.py."""
    def __init__(self, obj):
        self._obj = obj

    def filter(self, *a, **k):
        return self

    def filter_by(self, **k):
        return self

    def first(self):
        return self._obj

    def all(self):
        if self._obj is None:
            return []
        return self._obj if isinstance(self._obj, list) else [self._obj]


class _FakeDB:
    def __init__(self, mapping, other_nicknames=None, other_usernames=None):
        self._mapping = mapping
        self._other_nicknames = other_nicknames or []
        self._other_usernames = other_usernames or []
        self.added = []
        self.deleted = []
        self.committed = False

    def query(self, model):
        if model is Player:
            return _FakePlayerLikeQuery(
                single=self._mapping.get(Player), rows=self._other_nicknames, column_name="nickname"
            )
        if model is User:
            return _FakePlayerLikeQuery(single=None, rows=self._other_usernames, column_name="username")
        return _FakeQuery(self._mapping.get(model))

    def add(self, obj):
        self.added.append(obj)

    def delete(self, obj):
        self.deleted.append(obj)

    def flush(self):
        pass

    def commit(self):
        self.committed = True

    def refresh(self, obj):
        pass


def _row(**kw):
    return types.SimpleNamespace(**kw)


def _make_service(session, player, other_nicknames=None, other_usernames=None):
    state = _state(player.id)
    db = _FakeDB(
        {
            FirstLoginSession: session,
            Player: player,
            Ship: [],
            ShipSpecification: _spec(),
            PlayerFirstLoginState: state,
        },
        other_nicknames=other_nicknames,
        other_usernames=other_usernames,
    )
    svc = FirstLoginService(db=db, ai_service=object())
    return svc, db


# --- decline / no confirmation → nickname stays null, completion succeeds --

def test_declined_confirmation_leaves_nickname_null_and_completes():
    player_id = uuid.uuid4()
    session = _session(player_id, extracted_player_name="Voidrunner")
    player = _player(player_id)
    svc, db = _make_service(session, player)

    result = svc.complete_first_login(session.id, nickname_confirmed=False)

    assert player.nickname is None
    assert result["nickname"] is None
    assert result["nickname_rejected_reason"] is None
    assert db.committed is True


def test_default_call_with_no_confirmation_args_matches_declined_behavior():
    """A pre-existing caller that never passes the new kwargs at all must
    behave exactly like an explicit decline — nickname stays null."""
    player_id = uuid.uuid4()
    session = _session(player_id, extracted_player_name="Voidrunner")
    player = _player(player_id)
    svc, _db = _make_service(session, player)

    result = svc.complete_first_login(session.id)

    assert player.nickname is None
    assert result["nickname"] is None


# --- confirmed + valid → written -------------------------------------------

def test_confirmed_valid_extracted_name_is_written():
    player_id = uuid.uuid4()
    session = _session(player_id, extracted_player_name="Voidrunner")
    player = _player(player_id)
    svc, _db = _make_service(session, player)

    result = svc.complete_first_login(session.id, nickname_confirmed=True)

    assert player.nickname == "Voidrunner"
    assert result["nickname"] == "Voidrunner"
    assert result["nickname_rejected_reason"] is None


def test_confirmed_with_override_validates_and_writes_the_override_not_the_extracted_name():
    player_id = uuid.uuid4()
    session = _session(player_id, extracted_player_name="Voidrunner")
    player = _player(player_id)
    svc, _db = _make_service(session, player)

    result = svc.complete_first_login(
        session.id, nickname_confirmed=True, nickname_override="StarFox"
    )

    assert player.nickname == "StarFox"
    assert result["nickname"] == "StarFox"


# --- confirmed + validation failure → reason surfaced, never blocks -------

def test_confirmed_duplicate_nickname_rejected_but_completion_still_succeeds():
    player_id = uuid.uuid4()
    session = _session(player_id, extracted_player_name="Voidrunner")
    player = _player(player_id)
    svc, db = _make_service(
        session, player, other_nicknames=[_row(nickname="Voidrunner")]
    )

    result = svc.complete_first_login(session.id, nickname_confirmed=True)

    assert player.nickname is None, "a rejected candidate must never be written"
    assert result["nickname_rejected_reason"] == "taken"
    assert result["nickname"] is None
    # Completion side-effects still ran — a ship was granted.
    new_ships = [o for o in db.added if isinstance(o, Ship)]
    assert len(new_ships) == 1


def test_confirmed_override_colliding_with_existing_username_rejected_as_taken():
    player_id = uuid.uuid4()
    session = _session(player_id, extracted_player_name="Voidrunner")
    player = _player(player_id)
    svc, _db = _make_service(
        session, player, other_usernames=[_row(username="StarCaptain")]
    )

    result = svc.complete_first_login(
        session.id, nickname_confirmed=True, nickname_override="starcaptain"
    )

    assert player.nickname is None
    assert result["nickname_rejected_reason"] == "taken"


def test_confirmed_profane_candidate_rejected_with_reason_profanity():
    player_id = uuid.uuid4()
    session = _session(player_id, extracted_player_name="admin")
    player = _player(player_id)
    svc, _db = _make_service(session, player)

    result = svc.complete_first_login(session.id, nickname_confirmed=True)

    assert player.nickname is None
    assert result["nickname_rejected_reason"] == "profanity"


def test_confirmed_too_short_candidate_rejected_with_reason_length():
    player_id = uuid.uuid4()
    session = _session(player_id, extracted_player_name="ab")
    player = _player(player_id)
    svc, _db = _make_service(session, player)

    result = svc.complete_first_login(session.id, nickname_confirmed=True)

    assert player.nickname is None
    assert result["nickname_rejected_reason"] == "length"


# --- own-name idempotency wiring (AC-3) -------------------------------------

def test_confirmed_nickname_matching_players_own_existing_row_passes():
    """complete_first_login must pass exclude_player_id=player.id through to
    validate_nickname — re-confirming a name that already belongs to THIS
    player's own row must not self-reject as "taken"."""
    player_id = uuid.uuid4()
    session = _session(player_id, extracted_player_name="Voidrunner")
    player = _player(player_id)
    svc, _db = _make_service(
        session, player,
        other_nicknames=[_row(id=player_id, nickname="Voidrunner")],
    )

    result = svc.complete_first_login(session.id, nickname_confirmed=True)

    assert player.nickname == "Voidrunner"
    assert result["nickname_rejected_reason"] is None


def test_confirmed_nickname_matching_a_different_players_row_still_rejected():
    """The exclusion is scoped to this player's own id — a collision with a
    DIFFERENT player's row must still reject as "taken"."""
    player_id = uuid.uuid4()
    other_player_id = uuid.uuid4()
    session = _session(player_id, extracted_player_name="Voidrunner")
    player = _player(player_id)
    svc, _db = _make_service(
        session, player,
        other_nicknames=[_row(id=other_player_id, nickname="Voidrunner")],
    )

    result = svc.complete_first_login(session.id, nickname_confirmed=True)

    assert player.nickname is None
    assert result["nickname_rejected_reason"] == "taken"


# --- escape-pod hard-fail regression ---------------------------------------

def test_escape_pod_hard_fail_session_never_gets_a_nickname():
    """The auto-approved escape-pod path never surfaces an extracted name to
    the client (auto_approve_escape_pod's outcome dict omits the key), so
    the client always calls complete() with nickname_confirmed left false
    for this flow. Ship naming falls back to Player.username (the real
    Player.username property already implements nickname-or-user.username;
    this fake stands in for that with a plain attribute, matching the house
    convention in test_first_login_starter_resistances.py)."""
    player_id = uuid.uuid4()
    session = _session(
        player_id, extracted_player_name=None, awarded_ship=ShipChoice.ESCAPE_POD
    )
    player = _player(player_id, username="tester")
    svc, db = _make_service(session, player)

    result = svc.complete_first_login(session.id, nickname_confirmed=False)

    assert player.nickname is None
    assert result["nickname"] is None
    new_ships = [o for o in db.added if isinstance(o, Ship)]
    assert "tester's" in new_ships[0].name


# --- idempotency: a second /complete for an already-completed flow ---------
# WO-PUX-FLOGIN-IDEMPOTENT. has_completed_first_login is the only reliable
# marker (see complete_first_login's guard comment): it is set exactly once,
# at the very end of a successful call, after every side effect has already
# run -- unlike session.completed_at, which _evaluate_dialogue_outcome stamps
# before /complete is ever reached, making that column unusable as a guard.

def test_second_complete_call_raises_and_causes_zero_side_effects():
    """A repeat complete_first_login call for a player whose flow already
    finished must raise FirstLoginCompletionError and mutate NOTHING: no new
    ship, no deletion of the already-granted ship, no credit re-grant, no
    ARIA reset, no nickname change."""
    player_id = uuid.uuid4()
    session = _session(player_id, extracted_player_name="Voidrunner")
    player = _player(player_id)
    svc, db = _make_service(session, player)

    svc.complete_first_login(session.id, nickname_confirmed=True)
    first_ships = [o for o in db.added if isinstance(o, Ship)]
    assert len(first_ships) == 1

    # Snapshot everything a second call must leave untouched.
    credits_after_first = player.credits
    ship_id_after_first = player.current_ship_id
    nickname_after_first = player.nickname
    aria_score_after_first = player.aria_relationship_score
    aria_interactions_after_first = player.aria_total_interactions

    # A re-query after the first call's commit would now surface the
    # just-created ship as an "existing" row -- register it so a guard
    # failure would be caught red-handed trying to delete it.
    db._mapping[Ship] = first_ships

    with pytest.raises(FirstLoginCompletionError) as exc_info:
        svc.complete_first_login(session.id, nickname_confirmed=True)

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "First login already completed"

    assert [o for o in db.added if isinstance(o, Ship)] == first_ships, "no new ship created"
    assert db.deleted == [], "the already-granted ship must never be queued for deletion"
    assert player.credits == credits_after_first
    assert player.current_ship_id == ship_id_after_first
    assert player.nickname == nickname_after_first
    assert player.aria_relationship_score == aria_score_after_first
    assert player.aria_total_interactions == aria_interactions_after_first


def test_first_call_still_succeeds_when_state_starts_uncompleted():
    """Regression guard: the new early check must not fire on a legitimate
    first call (has_completed_first_login starts False)."""
    player_id = uuid.uuid4()
    session = _session(player_id, extracted_player_name="Voidrunner")
    player = _player(player_id)
    svc, db = _make_service(session, player)

    result = svc.complete_first_login(session.id, nickname_confirmed=True)

    assert result["nickname"] == "Voidrunner"
    assert db.committed is True
    assert len([o for o in db.added if isinstance(o, Ship)]) == 1


@pytest.mark.asyncio
async def test_route_translates_completion_error_to_http_400():
    """The /complete route (Admin-list-route direct-call pattern) must
    surface FirstLoginCompletionError as a clean HTTP 400 with zero writes,
    not an unhandled 500."""
    player_id = uuid.uuid4()
    session = _session(player_id, extracted_player_name="Voidrunner")
    player = _player(player_id)
    state = types.SimpleNamespace(
        player_id=player_id,
        current_session_id=session.id,
        answered_questions=True,
        has_completed_first_login=True,  # flow already finished
        received_resources=True,
        attempts=1,
    )
    db = _FakeDB({
        FirstLoginSession: session,
        Player: player,
        Ship: [],
        ShipSpecification: _spec(),
        PlayerFirstLoginState: state,
    })

    with pytest.raises(HTTPException) as exc_info:
        await complete_first_login_route(request=None, player=player, db=db, ai_service=object())

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "First login already completed"
    assert db.added == []
    assert db.deleted == []
