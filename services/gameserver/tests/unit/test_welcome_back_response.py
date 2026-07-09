"""WO-PUX-WBACK-SURFACE -- surface the welcome-back turn bonus on login.

Before this WO, ``turn_service.welcome_back()``'s outcome dict was computed at
the ``auth.py`` login chokepoint (``_track_player_login``, WO-F4) and then
discarded after a ``logger.info`` call -- the client never learned a bonus had
been granted. This test suite pins three things DB-free:

1. ``_track_player_login`` now RETURNS the outcome dict instead of ``None``,
   for the three cases the WO calls out: a qualifying (>7-day) gap grants and
   returns ``granted=True``; a sub-threshold gap returns ``granted=False``
   (not ``None`` -- the shape is always present when a Player was found and
   evaluation succeeded); and a bonus-evaluation failure (DB commit raises)
   returns ``None`` while NOT propagating the exception (login must still
   succeed).
2. ``AuthResponse``'s new ``welcome_back`` field trims the internal outcome
   dict (which also carries ``old_turns``/``new_turns``) down to the public
   ``{granted, bonus, days_inactive}`` contract, and defaults to ``None``.
3. A login route (``login_json``) threads whatever ``_track_player_login``
   returns into its response dict's ``welcome_back`` key WITHOUT disturbing
   any of the existing token/user_id fields -- the auth-surface declaration's
   "read-only data field, zero change to token issuance" claim, proven by
   diffing the response dict against a no-welcome-back baseline.

DB-free throughout: ``welcome_back()`` itself takes a transient (unpersisted)
``Player()`` ORM instance and mutates it in memory -- no engine, no session,
no Redis. The activity-service call inside ``_track_player_login`` is
monkeypatched to a no-op async stand-in (module-level ``get_player_activity_
service``, per [[sys-modules-injection-for-broken-import-testing]]-adjacent
technique: patch the attribute the function's lazy `from ... import` resolves
at call time, not the module reference in `auth.py`).
"""
import sys
import types
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from src.api.routes import auth as auth_mod
from src.models.player import Player
from src.schemas.auth import AuthResponse, LoginForm

# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

class _FakePlayerQuery:
    """Mimics ``db.query(Player).filter(...).first()`` for a single row."""

    def __init__(self, player):
        self._player = player

    def filter(self, *args, **kwargs):
        return self

    def first(self):
        return self._player


class _FakeSession:
    """Stand-in Session: query returns a fixed Player, commit optionally raises."""

    def __init__(self, player, commit_raises=False):
        self._player = player
        self._commit_raises = commit_raises
        self.committed = False
        self.rolled_back = False

    def query(self, model):
        assert model is Player
        return _FakePlayerQuery(self._player)

    def commit(self):
        if self._commit_raises:
            raise RuntimeError("simulated DB commit failure")
        self.committed = True

    def rollback(self):
        self.rolled_back = True


class _FakeActivityService:
    def __init__(self):
        self.track_login_calls = []

    async def track_login(self, player_id, db=None):
        self.track_login_calls.append(player_id)
        return {}


def _make_player(*, last_game_login, turns=100):
    """A transient (unpersisted) Player -- fields set explicitly since
    Column(default=...) never fires without a real flush (see
    [[fake-orm-flush-defaults-gap]])."""
    player = Player()
    player.id = uuid.uuid4()
    player.user_id = uuid.uuid4()
    player.turns = turns
    player.max_turns = 1000
    player.military_rank = "Recruit"  # RankingService bonus = 0 -> max_turns stays 1000
    player.last_game_login = last_game_login
    return player


def _patch_activity_service(monkeypatch, service=None):
    """``_track_player_login`` does a call-time ``from src.services.
    player_activity_service import get_player_activity_service``, and that
    module transitively imports ``aioredis`` -- broken in this venv (see
    [[aioredis-py312-timeouterror-broken]]). ``monkeypatch.setattr`` on the
    string path would re-trigger the real (broken) import to resolve its
    target, so inject a synthetic module into ``sys.modules`` instead (see
    [[sys-modules-injection-for-broken-import-testing]]) -- Python's import
    system short-circuits on an already-present dotted name.
    """
    service = service or _FakeActivityService()

    async def _fake_get_service():
        return service

    fake_mod = types.ModuleType("src.services.player_activity_service")
    fake_mod.get_player_activity_service = _fake_get_service
    monkeypatch.setitem(sys.modules, "src.services.player_activity_service", fake_mod)
    return service


# ---------------------------------------------------------------------------
# 1. _track_player_login outcome-threading
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_track_player_login_returns_granted_outcome_for_qualifying_gap(monkeypatch):
    _patch_activity_service(monkeypatch)
    now = datetime.now(timezone.utc)
    player = _make_player(last_game_login=now - timedelta(days=8, hours=1), turns=100)
    db = _FakeSession(player)

    outcome = await auth_mod._track_player_login(db, player.user_id)

    assert outcome is not None
    assert outcome["granted"] is True
    assert outcome["bonus"] == 400  # min(500, 8 * 50)
    assert outcome["days_inactive"] == 8
    assert player.turns == 500  # 100 + 400, under max_turns=1000
    assert db.committed is True


@pytest.mark.asyncio
async def test_track_player_login_immediate_second_login_not_granted(monkeypatch):
    _patch_activity_service(monkeypatch)
    now = datetime.now(timezone.utc)
    # Sub-threshold gap (well under 7 days) -- e.g. a second login an hour later.
    player = _make_player(last_game_login=now - timedelta(hours=1), turns=100)
    db = _FakeSession(player)

    outcome = await auth_mod._track_player_login(db, player.user_id)

    # Shape is always present (a Player was found, evaluation succeeded) --
    # granted=False, NOT None. None is reserved for "nothing to surface".
    assert outcome is not None
    assert outcome["granted"] is False
    assert outcome["bonus"] == 0
    assert outcome["days_inactive"] == 0
    assert player.turns == 100  # untouched
    assert db.committed is True


@pytest.mark.asyncio
async def test_track_player_login_bonus_failure_returns_null_and_login_still_succeeds(monkeypatch):
    activity_service = _patch_activity_service(monkeypatch)
    now = datetime.now(timezone.utc)
    player = _make_player(last_game_login=now - timedelta(days=30), turns=100)
    db = _FakeSession(player, commit_raises=True)  # bonus evaluation's db.commit() blows up

    # Must not raise -- a bonus failure is defensive/non-fatal by contract.
    outcome = await auth_mod._track_player_login(db, player.user_id)

    assert outcome is None
    assert db.rolled_back is True
    # Login-adjacent activity tracking still ran (unaffected by the bonus failure).
    assert activity_service.track_login_calls == [str(player.id)]


@pytest.mark.asyncio
async def test_track_player_login_no_player_returns_none(monkeypatch):
    _patch_activity_service(monkeypatch)
    db = _FakeSession(player=None)  # admin/non-player user -- no matching Player row

    outcome = await auth_mod._track_player_login(db, uuid.uuid4())

    assert outcome is None


# ---------------------------------------------------------------------------
# 2. AuthResponse.welcome_back contract (trims to the public shape)
# ---------------------------------------------------------------------------

def test_auth_response_trims_welcome_back_to_public_shape():
    raw = {
        "access_token": "tok-a",
        "refresh_token": "tok-r",
        "token_type": "bearer",
        "user_id": "abc-123",
        "welcome_back": {
            "granted": True,
            "bonus": 400,
            "days_inactive": 8,
            "old_turns": 100,   # server-internal -- must be dropped
            "new_turns": 500,   # server-internal -- must be dropped
        },
    }
    dumped = AuthResponse(**raw).model_dump()
    assert dumped["welcome_back"] == {"granted": True, "bonus": 400, "days_inactive": 8}


def test_auth_response_welcome_back_defaults_to_null():
    raw = {
        "access_token": "tok-a",
        "refresh_token": "tok-r",
        "token_type": "bearer",
        "user_id": "abc-123",
    }
    dumped = AuthResponse(**raw).model_dump()
    assert dumped["welcome_back"] is None


# ---------------------------------------------------------------------------
# 3. Route wiring: welcome_back rides the response without disturbing the
#    existing token/auth fields (the auth-surface declaration's core claim).
# ---------------------------------------------------------------------------

class _FakeUser:
    def __init__(self):
        self.id = uuid.uuid4()


@pytest.mark.asyncio
async def test_login_json_threads_welcome_back_without_touching_token_fields(monkeypatch):
    fake_user = _FakeUser()
    monkeypatch.setattr(auth_mod, "authenticate_admin", lambda db, u, p: fake_user)
    monkeypatch.setattr(auth_mod, "create_tokens", lambda user_id, db: ("tok-access", "tok-refresh"))
    monkeypatch.setattr(auth_mod.settings, "DEBUG", False)

    login_form = LoginForm(username="commander", password="pw-does-not-matter")

    # Baseline: no welcome-back outcome (the common case -- every login where
    # no bonus is due).
    async def _no_bonus(db, user_id):
        return None
    monkeypatch.setattr(auth_mod, "_track_player_login", _no_bonus)
    baseline = await auth_mod.login_json(json_data=login_form, db=object())

    # Same call, but this login DID grant a bonus.
    async def _granted_bonus(db, user_id):
        return {"granted": True, "bonus": 250, "days_inactive": 12, "old_turns": 50, "new_turns": 300}
    monkeypatch.setattr(auth_mod, "_track_player_login", _granted_bonus)
    granted = await auth_mod.login_json(json_data=login_form, db=object())

    # Token/auth fields are byte-identical across both calls -- welcome_back
    # is purely additive, exactly as the auth-surface declaration requires.
    for key in ("access_token", "refresh_token", "token_type", "user_id"):
        assert baseline[key] == granted[key], f"{key} diverged when welcome_back was present"
    assert baseline["access_token"] == "tok-access"
    assert baseline["refresh_token"] == "tok-refresh"
    assert baseline["user_id"] == str(fake_user.id)

    assert baseline["welcome_back"] is None
    assert granted["welcome_back"] == {
        "granted": True, "bonus": 250, "days_inactive": 12, "old_turns": 50, "new_turns": 300,
    }
    # Round-tripped through the response schema, the internal keys drop out.
    assert AuthResponse(**granted).model_dump()["welcome_back"] == {
        "granted": True, "bonus": 250, "days_inactive": 12,
    }
