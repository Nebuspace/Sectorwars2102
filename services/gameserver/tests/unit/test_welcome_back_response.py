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
4. OAuth-callback parity (github/google/steam): previously a documented gap
   in ``_track_player_login``'s own docstring -- OAuth logins never called
   the chokepoint, so the welcome-back bonus and Redis online-session
   tracking only ever fired for password/JSON logins. Pins that all three
   callbacks now call it, in the same relative position the password routes
   do (after tokens/last-login are set, before the response is built).

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
from unittest.mock import MagicMock

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


# ---------------------------------------------------------------------------
# 4. OAuth callback parity -- github/google/steam now hit the SAME chokepoint
#    the password/JSON routes do. DB-free: every external touchpoint
#    (provider exchange, get_oauth_user, token issuance, the single-use
#    exchange-code store, base/frontend URLs) is monkeypatched on the module-
#    level names auth.py imports, mirroring test #3's pattern above -- not a
#    fake OAuth harness, the same "patch the name the route calls" technique.
# ---------------------------------------------------------------------------

class _FakeOAuthUser:
    def __init__(self):
        self.id = uuid.uuid4()
        self.username = "octocat"


class _FakeOAuthRequest:
    def __init__(self):
        self.url = "http://testserver/api/v1/auth/github/callback"
        self.client = types.SimpleNamespace(host="127.0.0.1")


def _patch_common_oauth_bits(monkeypatch, call_order):
    """Shared stubs every OAuth callback test needs: token issuance,
    last-login stamping, the single-use exchange code, the api/frontend base
    URLs, and a call-order-recording ``_track_player_login`` spy.
    Provider-specific pieces (GitHubOAuth/GoogleOAuth/SteamAuth,
    get_oauth_user) are patched per-test."""
    monkeypatch.setattr(auth_mod, "create_tokens", lambda uid, db: ("tok-a", "tok-r"))

    def _update_last_login(db, uid):
        call_order.append(("update_user_last_login", uid))
    monkeypatch.setattr(auth_mod, "update_user_last_login", _update_last_login)

    async def _spy_track_login(db, user_id):
        call_order.append(("_track_player_login", user_id))
        return None
    monkeypatch.setattr(auth_mod, "_track_player_login", _spy_track_login)

    def _store_code(payload):
        call_order.append(("store_auth_code", payload["user_id"]))
        return "auth-code-xyz"
    monkeypatch.setattr(auth_mod, "store_auth_code", _store_code)

    # get_api_base_url/detect_environment/get_frontend_url are methods on the
    # Settings CLASS (not pydantic fields), so patch the class, not the
    # instance -- pydantic's __setattr__ rejects attribute names that aren't
    # declared fields. API_V1_STR IS a declared field, so the instance-level
    # setattr works for that one.
    settings_cls = type(auth_mod.settings)
    monkeypatch.setattr(settings_cls, "get_api_base_url", lambda self: "http://api.test/api/v1")
    monkeypatch.setattr(auth_mod.settings, "API_V1_STR", "/api/v1")
    monkeypatch.setattr(settings_cls, "detect_environment", lambda self: "test")
    monkeypatch.setattr(settings_cls, "get_frontend_url", lambda self: "http://frontend.test")


def _assert_tracked_between_login_and_response(call_order, expected_user_id):
    names = [c[0] for c in call_order]
    assert "_track_player_login" in names, "OAuth callback never called _track_player_login"
    assert names.index("_track_player_login") > names.index("update_user_last_login")
    assert names.index("_track_player_login") < names.index("store_auth_code")
    assert call_order[names.index("_track_player_login")][1] == expected_user_id


@pytest.mark.asyncio
async def test_github_callback_fires_track_player_login_after_tokens_before_response(monkeypatch):
    call_order = []
    _patch_common_oauth_bits(monkeypatch, call_order)
    monkeypatch.setattr(auth_mod, "_validate_oauth_state", lambda state: True)

    fake_user = _FakeOAuthUser()

    async def _exchange(code, redirect_uri):
        return "provider-token"

    async def _get_user_info(token):
        return "provider-uid-1", {"login": "octocat"}

    monkeypatch.setattr(auth_mod.GitHubOAuth, "exchange_code_for_token", staticmethod(_exchange))
    monkeypatch.setattr(auth_mod.GitHubOAuth, "get_user_info", staticmethod(_get_user_info))

    async def _get_oauth_user(db, provider, provider_user_id):
        return fake_user  # existing user -- skip create_oauth_user entirely

    monkeypatch.setattr(auth_mod, "get_oauth_user", _get_oauth_user)

    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = MagicMock()  # existing Player row

    response = await auth_mod.github_callback(
        request=_FakeOAuthRequest(), code="ghcode", state="valid-state", invite=None, db=db,
    )

    assert response.status_code == 307
    assert "error=oauth_failed" not in response.headers["location"]
    _assert_tracked_between_login_and_response(call_order, fake_user.id)


@pytest.mark.asyncio
async def test_google_callback_fires_track_player_login_after_tokens_before_response(monkeypatch):
    call_order = []
    _patch_common_oauth_bits(monkeypatch, call_order)
    monkeypatch.setattr(auth_mod, "_validate_oauth_state", lambda state: True)

    fake_user = _FakeOAuthUser()

    async def _exchange(code, redirect_uri):
        return {"access_token": "provider-token"}

    async def _get_user_info(token_data):
        return "provider-uid-2", {"email": "a@b.com"}

    monkeypatch.setattr(auth_mod.GoogleOAuth, "exchange_code_for_token", staticmethod(_exchange))
    monkeypatch.setattr(auth_mod.GoogleOAuth, "get_user_info", staticmethod(_get_user_info))

    async def _get_oauth_user(db, provider, provider_user_id):
        return fake_user

    monkeypatch.setattr(auth_mod, "get_oauth_user", _get_oauth_user)

    response = await auth_mod.google_callback(
        request=_FakeOAuthRequest(), code="gcode", state="valid-state", invite=None, db=MagicMock(),
    )

    assert response.status_code == 307
    _assert_tracked_between_login_and_response(call_order, fake_user.id)


@pytest.mark.asyncio
async def test_steam_callback_fires_track_player_login_after_tokens_before_response(monkeypatch):
    call_order = []
    _patch_common_oauth_bits(monkeypatch, call_order)

    fake_user = _FakeOAuthUser()

    async def _verify_response(request):
        return "steam-id-1"

    async def _get_user_info(steam_id):
        return {"personaname": "steamer"}

    monkeypatch.setattr(auth_mod.SteamAuth, "verify_response", staticmethod(_verify_response))
    monkeypatch.setattr(auth_mod.SteamAuth, "get_user_info", staticmethod(_get_user_info))

    async def _get_oauth_user(db, provider, provider_user_id):
        return fake_user

    monkeypatch.setattr(auth_mod, "get_oauth_user", _get_oauth_user)

    response = await auth_mod.steam_callback(
        request=_FakeOAuthRequest(), invite=None, db=MagicMock(),
    )

    assert response.status_code == 307
    _assert_tracked_between_login_and_response(call_order, fake_user.id)
