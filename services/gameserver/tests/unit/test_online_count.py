"""WO-ADM-ONLINE-COUNT -- truthful admin online-player count.

The admin real-time analytics `players_online_now` figure used to be a pure
approximation: `AnalyticsService.get_real_time_metrics()` counts
`User.last_login >= now - 1h`, which is wrong in both directions (a player
3h into a live session doesn't count; someone who quit 50 minutes ago does).
The route (`admin_comprehensive.get_real_time_analytics`) now overwrites that
figure with the live Redis presence-set cardinality
(`activity:online_players`, `PlayerActivityService.get_online_player_count`,
OPERATIONS/player-activity.md:30-32) whenever Redis is reachable, and only
falls back to the last_login approximation when it isn't -- never a 500
either way. `players_online_source` ('presence' | 'fallback') is a new,
additive field; `players_online_now` stays the exact key name the admin-ui
depends on (`services/admin-ui/src/components/pages/PlayerAnalytics.tsx:157,
310` reads `analyticsData.players_online_now`;
`services/admin-ui/src/types/playerManagement.ts:205` types it -- the WO's
":139" anchor was stale, per its own warning that this file changed twice
this session).

Two failure modes are treated identically (fallback, never a 500):
  1. `redis_pool is None` (Redis never connected, or `RedisService.connect()`
     failed at startup) -- `PlayerActivityService.get_online_player_count()`
     itself would silently return `0` in this case (see its own
     `if redis.redis_pool: ... return 0` gate), which is indistinguishable
     from "genuinely zero players online" if trusted blindly. The route
     checks `redis_pool` directly via `get_redis_service()` (imported from
     `redis_service.py`, NOT the frozen `player_activity_service.py`
     substrate) *before* trusting a bare 0, and short-circuits to the
     fallback without ever calling `get_online_player_count()`.
  2. An outright exception (pool present but the call itself raises, e.g. a
     mid-request Redis outage) -- caught by the route's own try/except.

NOTE (surfaced, not silently worked around): as of this WO, `RedisService
.connect()` (`init_redis()` in `redis_service.py`) is never invoked anywhere
in `main.py`'s lifespan -- `redis_service.redis_pool` is `None` for the
lifetime of the running process today, so failure mode (1) is not a rare
edge case here, it is the CURRENT steady state in every environment. Until a
follow-up wires `init_redis()` into startup, this route will always report
`players_online_source: 'fallback'` in practice -- correctly defensive, but
not yet "truthful" end-to-end. That startup wiring is out of this WO's three
named lanes (`admin_comprehensive.py` / `analytics_service.py` / this test
file) and touches a shared file outside them, so it is called out here
rather than silently added.

DB-free throughout. `AnalyticsService.get_real_time_metrics()` wraps its
entire body in one outer try/except that returns a hardcoded
`_get_fallback_metrics()` dict on ANY unhandled exception -- a fake DB that
only stubs the `User` query and lets every other query raise would silently
mask the very `players_online_now` value under test behind that hardcoded
fallback, making the pin pass or fail independent of real behavior
(source-grep-test-self-defeat, generalized). `_AnalyticsFakeDB` therefore
gives every OTHER model a safe zero/empty default (`_ZeroQuery`) so the
method's dozen internal queries all complete normally, while the `User`
query specifically goes through `_FilterEvalQuery`, which interprets the
REAL SQLAlchemy `BinaryExpression` (`User.last_login >= one_hour_ago`)
against real candidate objects (fake-query-filter-interpreter-pattern) --
proving the row-exclusion logic itself, not just a pre-baked count. Verified
directly against a real `User.last_login >= one_hour_ago` expression before
writing this: `.left.key == "last_login"`, `.operator is operator.ge`,
`.right.value` is the bound datetime.

Route-level tests use the "Admin list-route direct-call pattern": the async
route handler is called directly with a fake `current_admin` + the same
`_AnalyticsFakeDB`, bypassing `TestClient` and a real DB/Redis entirely.
`get_player_activity_service` / `get_redis_service` are patched at their
SOURCE modules (`player_activity_service.py` / `redis_service.py`), not at
`admin_comprehensive`'s namespace -- the route imports them with a local
`from X import Y` *inside* the try block, which re-resolves the name from
the source module at call time, so patching the source module's attribute is
what actually takes effect.
"""
import ast
import inspect
import sys
import types
from datetime import datetime, timedelta

import pytest

# Local Mac test venv runs Python 3.14 (`.venv-test`); the real deploy target
# is 3.11 (pyproject.toml `python = "^3.11"`). `aioredis` (pinned `^2.0.1`,
# already unmaintained upstream) does `from distutils.version import
# StrictVersion` at import time purely to gate a hiredis-version `<`
# comparison -- `distutils` was removed from the stdlib in 3.12. Nothing in
# this repo currently imports `redis_service.py` (and transitively
# `aioredis`) at module load time except this file's route-level tests
# (`admin_comprehensive.get_real_time_analytics`'s new presence-path import
# is local/call-time, so it's never hit until a test actually invokes that
# branch), so no existing test currently trips this. Test-only shim, 3.11
# untouched, no production file involved.
if "distutils" not in sys.modules:
    class _StrictVersionShim:
        def __init__(self, vstring):
            self._parts = tuple(int(p) for p in vstring.split(".")[:3])

        def __lt__(self, other):
            return self._parts < other._parts

    _distutils_version_shim = types.ModuleType("distutils.version")
    _distutils_version_shim.StrictVersion = _StrictVersionShim
    _distutils_shim = types.ModuleType("distutils")
    _distutils_shim.version = _distutils_version_shim
    sys.modules["distutils"] = _distutils_shim
    sys.modules["distutils.version"] = _distutils_version_shim

import src.api.routes.admin_comprehensive as admin_mod
from src.api.routes.admin_comprehensive import get_real_time_analytics
from src.services.analytics_service import AnalyticsService
from src.models.user import User


# ---------------------------------------------------------------------------
# DB-free AnalyticsService fixture
# ---------------------------------------------------------------------------

class _FilterEvalQuery:
    """Interprets real SQLAlchemy filter conditions against real candidate
    row objects -- proves row-exclusion logic, not just a stubbed count."""

    def __init__(self, rows):
        self._rows = list(rows)

    def filter(self, *conditions):
        rows = self._rows
        for cond in conditions:
            rows = [r for r in rows if self._matches(r, cond)]
        return _FilterEvalQuery(rows)

    @staticmethod
    def _matches(row, cond):
        left_key = getattr(cond.left, "key", None)
        if left_key is None:
            return True  # unrecognized shape -- don't spuriously exclude
        actual = getattr(row, left_key, None)
        if actual is None:
            return False  # SQL NULL-comparison semantics: never matches
        right_val = getattr(cond.right, "value", cond.right)
        return cond.operator(actual, right_val)

    def count(self):
        return len(self._rows)

    def all(self):
        return list(self._rows)

    def first(self):
        return self._rows[0] if self._rows else None


class _ZeroQuery:
    """Safe zero/empty default for every model this suite doesn't pin --
    keeps `get_real_time_metrics()` inside its happy path end-to-end."""

    def filter(self, *a, **k):
        return self

    def filter_by(self, **k):
        return self

    def group_by(self, *a, **k):
        return self

    def order_by(self, *a, **k):
        return self

    def count(self):
        return 0

    def all(self):
        return []

    def scalar(self):
        return 0

    def first(self):
        return None


class _AnalyticsFakeDB:
    """`User` queries route through the real-condition interpreter (the
    online-count pin); every other model gets the safe-zero default."""

    def __init__(self, users):
        self._users = users

    def query(self, *entities):
        if entities and entities[0] is User:
            return _FilterEvalQuery(self._users)
        return _ZeroQuery()


def _user(last_login):
    return types.SimpleNamespace(last_login=last_login)


def _admin():
    return types.SimpleNamespace(username="tester-admin")


# ---------------------------------------------------------------------------
# Section 1 -- AnalyticsService.get_real_time_metrics() own computation
# (Lane 2 was comment-only; this pins that the recent_users figure is
# genuinely unchanged, not just eyeballed from the diff)
# ---------------------------------------------------------------------------

def test_analytics_service_online_count_still_excludes_stale_and_null_logins():
    now = datetime.utcnow()
    users = [
        _user(now - timedelta(minutes=5)),    # within the hour -- counted
        _user(now - timedelta(minutes=59)),   # within the hour -- counted
        _user(now - timedelta(hours=3)),      # stale -- excluded
        _user(None),                          # never logged in -- excluded
    ]
    db = _AnalyticsFakeDB(users)
    service = AnalyticsService(db)

    metrics = service.get_real_time_metrics()

    assert metrics["players_online_now"] == 2, (
        "recent_users must still count exactly the last_login>=1h rows -- a "
        "different figure means either the filter itself regressed or the "
        "outer try/except silently fell through to _get_fallback_metrics(), "
        "either of which the WO's lane-2 comment-only edit must not cause"
    )


def test_analytics_service_online_count_zero_when_nobody_recent():
    now = datetime.utcnow()
    db = _AnalyticsFakeDB([_user(now - timedelta(hours=5)), _user(None)])
    service = AnalyticsService(db)

    metrics = service.get_real_time_metrics()

    assert metrics["players_online_now"] == 0


# ---------------------------------------------------------------------------
# Section 2 -- route-level presence overwrite / fallback behavior
#
# `src.services.redis_service` (hence `player_activity_service`, which
# imports it eagerly at module level) does NOT actually import successfully
# in this repo's own target runtime -- see Section 2a below for the empirical
# proof, done separately from these isolated-logic tests. Rather than lean
# on the distutils shim for every scenario (which only gets past ONE of the
# two independent breaks), these tests inject fully synthetic modules
# straight into `sys.modules["src.services.redis_service"]` /
# `["src.services.player_activity_service"]`. Python's import system
# short-circuits on an already-present `sys.modules` entry and never
# re-executes the real file, so the route's local `from X import Y` picks up
# the fake cleanly, with zero dependency on the real (currently broken)
# aioredis import chain. This tests the route's OWN branching logic in
# isolation, decoupled from that separate, pre-existing dependency defect.
# ---------------------------------------------------------------------------

class _FakeActivityService:
    def __init__(self, count=None, raises=False):
        self._count = count
        self._raises = raises

    async def get_online_player_count(self):
        if self._raises:
            raise RuntimeError("simulated redis scard failure")
        return self._count


class _FakeRedisSvc:
    def __init__(self, pool):
        self.redis_pool = pool


def _inject_fake_redis_service_module(monkeypatch, get_redis_service_fn):
    fake_mod = types.ModuleType("src.services.redis_service")
    fake_mod.get_redis_service = get_redis_service_fn
    monkeypatch.setitem(sys.modules, "src.services.redis_service", fake_mod)


def _inject_fake_activity_service_module(monkeypatch, get_activity_service_fn):
    fake_mod = types.ModuleType("src.services.player_activity_service")
    fake_mod.get_player_activity_service = get_activity_service_fn
    monkeypatch.setitem(
        sys.modules, "src.services.player_activity_service", fake_mod
    )


@pytest.mark.asyncio
async def test_presence_path_overwrites_last_login_figure_regardless_of_recency(monkeypatch):
    now = datetime.utcnow()
    # last_login fallback baseline would be 1 -- the presence count (42) is
    # deliberately a different value so the overwrite is unambiguous.
    db = _AnalyticsFakeDB([_user(now - timedelta(minutes=5))])

    async def fake_get_redis():
        return _FakeRedisSvc(pool=object())

    async def fake_get_activity_service():
        return _FakeActivityService(count=42)

    _inject_fake_redis_service_module(monkeypatch, fake_get_redis)
    _inject_fake_activity_service_module(monkeypatch, fake_get_activity_service)

    result = await get_real_time_analytics(current_admin=_admin(), db=db)

    assert result["success"] is True
    assert result["data"]["players_online_now"] == 42
    assert result["data"]["players_online_source"] == "presence"


@pytest.mark.asyncio
async def test_redis_pool_none_falls_back_without_touching_activity_service(monkeypatch):
    now = datetime.utcnow()
    # fallback baseline == 1 (only the 5-min-ago user is within the hour)
    db = _AnalyticsFakeDB(
        [_user(now - timedelta(minutes=5)), _user(now - timedelta(hours=2))]
    )
    calls = {"activity_service_fetched": False}

    async def fake_get_redis():
        return _FakeRedisSvc(pool=None)

    async def fake_get_activity_service():
        calls["activity_service_fetched"] = True
        return _FakeActivityService(count=99)

    _inject_fake_redis_service_module(monkeypatch, fake_get_redis)
    _inject_fake_activity_service_module(monkeypatch, fake_get_activity_service)

    result = await get_real_time_analytics(current_admin=_admin(), db=db)

    assert result["success"] is True
    assert result["data"]["players_online_now"] == 1
    assert result["data"]["players_online_source"] == "fallback"
    assert calls["activity_service_fetched"] is False, (
        "a disconnected pool must short-circuit to the fallback before ever "
        "calling get_online_player_count() -- that call would silently "
        "return 0 (indistinguishable from 'genuinely nobody online') rather "
        "than raise, so the route must not rely on it to detect this case"
    )


@pytest.mark.asyncio
async def test_activity_service_exception_falls_back_never_raises(monkeypatch):
    now = datetime.utcnow()
    db = _AnalyticsFakeDB([_user(now - timedelta(minutes=1))])  # baseline == 1

    async def fake_get_redis():
        return _FakeRedisSvc(pool=object())

    async def fake_get_activity_service():
        return _FakeActivityService(raises=True)

    _inject_fake_redis_service_module(monkeypatch, fake_get_redis)
    _inject_fake_activity_service_module(monkeypatch, fake_get_activity_service)

    result = await get_real_time_analytics(current_admin=_admin(), db=db)  # must not raise

    assert result["success"] is True
    assert result["data"]["players_online_now"] == 1
    assert result["data"]["players_online_source"] == "fallback"


@pytest.mark.asyncio
async def test_redis_service_lookup_itself_raising_falls_back_never_raises(monkeypatch):
    """The outermost failure mode: even the get_redis_service() lookup
    itself blowing up (not just the scard call) must degrade gracefully."""
    db = _AnalyticsFakeDB([])

    async def fake_get_redis():
        raise ConnectionError("redis unreachable")

    _inject_fake_redis_service_module(monkeypatch, fake_get_redis)

    result = await get_real_time_analytics(current_admin=_admin(), db=db)

    assert result["success"] is True
    assert result["data"]["players_online_now"] == 0
    assert result["data"]["players_online_source"] == "fallback"


@pytest.mark.asyncio
async def test_presence_zero_is_reported_as_presence_not_mistaken_for_down(monkeypatch):
    """A connected pool with a genuinely empty online set must report
    players_online_now=0 with source='presence' -- zero online is a real,
    truthful answer once Redis is actually reachable, not a fallback
    trigger."""
    now = datetime.utcnow()
    db = _AnalyticsFakeDB([_user(now - timedelta(minutes=1))])  # baseline == 1

    async def fake_get_redis():
        return _FakeRedisSvc(pool=object())

    async def fake_get_activity_service():
        return _FakeActivityService(count=0)

    _inject_fake_redis_service_module(monkeypatch, fake_get_redis)
    _inject_fake_activity_service_module(monkeypatch, fake_get_activity_service)

    result = await get_real_time_analytics(current_admin=_admin(), db=db)

    assert result["data"]["players_online_now"] == 0
    assert result["data"]["players_online_source"] == "presence"


# ---------------------------------------------------------------------------
# Section 2a -- the REAL import chain, unfaked: empirical proof of a
# discovered pre-existing defect (surfaced, not fixed -- out of this WO's
# three named lanes).
#
# `redis_service.py` does `import aioredis` unconditionally at module level;
# `player_activity_service.py` does `from src.services.redis_service import
# RedisService, get_redis_service` unconditionally at module level too. On
# this repo's OWN deploy target (`Dockerfile`: `FROM python:3.12-slim`),
# `aioredis`'s `exceptions.py` declares
# `class TimeoutError(asyncio.TimeoutError, builtins.TimeoutError,
# RedisError)` -- and `asyncio.TimeoutError is builtins.TimeoutError` as of
# Python 3.11 (verified directly against a real `/usr/local/bin/python3.12`
# interpreter on this machine, independent of any venv: reproduces `TypeError:
# duplicate base class TimeoutError`). This is NOT a Mac-venv-only artifact --
# it reproduces on the exact Python minor version the Dockerfile ships.
# Practical effect: `redis_service.py` (and therefore `player_activity_
# service.py`) cannot be imported in this repo's real runtime AT ALL today.
# `auth.py`'s `_track_player_login`/`_track_player_logout` already wrap their
# `from src.services.player_activity_service import get_player_activity_
# service` call in a blanket `except Exception:` (logged as a non-fatal
# warning) -- meaning the entire presence-tracking write side
# (`activity:online_players` population on login/logout) has likely never
# actually run in any environment using this aioredis pin, not just this
# WO's read side. A prerequisite fix (replace `aioredis` with the already-
# installed `redis.asyncio`, since `redis = {extras = ["hiredis"], version =
# ">=5.0.1,<9.0.0"}` already ships full async support and would make
# `aioredis` redundant) is a dependency change outside this WO's three named
# lanes and requires sign-off per this repo's "no new external deps without
# sign-off" rule -- flagged here, not silently attempted.
#
# This test proves this route survives that REAL failure today, unfaked: no
# `sys.modules` injection, the actual `redis_service.py` file is really
# imported and really raises. The distutils shim at the top of this file
# only clears a SEPARATE, Mac-venv-only gap (`.venv-test` lacks `setuptools`,
# so `distutils` isn't shimmed the way it would be via a real Poetry/Docker
# build) so this test reaches the SAME real TypeError the deployed container
# hits, rather than stopping short at a local-only artifact.
# ---------------------------------------------------------------------------

def test_aioredis_import_chain_is_actually_broken_on_this_deploy_target():
    """Empirical proof of the discovered defect itself (not just that the
    route tolerates it) -- confirms this isn't a stale/misread claim."""
    with pytest.raises(TypeError, match="duplicate base class"):
        import src.services.redis_service  # noqa: F401


@pytest.mark.asyncio
async def test_route_survives_the_real_broken_import_chain_unfaked():
    """No sys.modules fakery at all here -- the route's own try/except must
    swallow the REAL import-time TypeError from the broken aioredis pin and
    still return the last_login-derived figure, exactly as it would in this
    repo's actual container today."""
    now = datetime.utcnow()
    db = _AnalyticsFakeDB([_user(now - timedelta(minutes=1))])  # baseline == 1

    result = await get_real_time_analytics(current_admin=_admin(), db=db)

    assert result["success"] is True
    assert result["data"]["players_online_now"] == 1
    assert result["data"]["players_online_source"] == "fallback"


# ---------------------------------------------------------------------------
# Section 3 -- payload key-name stability (admin-ui contract)
# ---------------------------------------------------------------------------

def test_players_online_now_and_source_keys_are_byte_stable_in_source():
    """AST-based, not text-grep (source-grep-test-self-defeat): walk the
    route's own source for `metrics["<key>"] = ...` assignment targets."""
    src = inspect.getsource(admin_mod.get_real_time_analytics)
    tree = ast.parse(src)
    assigned_keys = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if (
                    isinstance(target, ast.Subscript)
                    and isinstance(target.value, ast.Name)
                    and target.value.id == "metrics"
                    and isinstance(target.slice, ast.Constant)
                ):
                    assigned_keys.add(target.slice.value)

    assert "players_online_now" in assigned_keys, (
        "players_online_now must remain the exact key name -- "
        "admin-ui/src/components/pages/PlayerAnalytics.tsx:157,310 and "
        "admin-ui/src/types/playerManagement.ts:205 both read it verbatim"
    )
    assert "players_online_source" in assigned_keys
