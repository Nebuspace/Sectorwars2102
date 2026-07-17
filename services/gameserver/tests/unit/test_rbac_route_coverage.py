"""RBAC Phase B — FULL route-coverage completeness (security property).

Hub: coverage cipher runs at sweep-COMPLETE.  Completeness =
  (1) ``grep -c require_admin`` across ``api/routes/*.py`` → 0
  (2) every HTTP route whose path contains ``/admin`` has
      ``require_scope`` in the fully-merged dependant tree
  (3) admin WebSocket uses inline ``user_has_active_scope`` (not flat is_admin)

DB-free.  Same env harness as other RBAC unit tests.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Callable, Iterable, Set

from fastapi.routing import APIRoute, APIWebSocketRoute

from src.main import app

_GAMESERVER_ROOT = Path(__file__).resolve().parents[2]
_ROUTES_DIR = _GAMESERVER_ROOT / "src" / "api" / "routes"

# Modules that gate admin ops but may not use /admin in the URL path.
_EXTRA_ADMIN_MODULES: frozenset[str] = frozenset(
    {
        "src.api.routes.users",
        "src.api.routes.mfa",
        "src.api.routes.events",
        "src.api.routes.debug",
        "src.api.routes.test",
        "src.api.routes.translation",
        "src.api.routes.first_login",
        "src.api.routes.ranking",
        "src.api.routes.medals",
        "src.api.routes.nexus",
    }
)


def _collect_dep_calls(dependant, seen: Set[int] | None = None) -> list[Callable]:
    if seen is None:
        seen = set()
    out: list[Callable] = []
    for sub in dependant.dependencies:
        if sub.call is not None:
            cid = id(sub.call)
            if cid not in seen:
                seen.add(cid)
                out.append(sub.call)
        out.extend(_collect_dep_calls(sub, seen))
    return out


def _has_require_scope(calls: Iterable[Callable]) -> bool:
    for call in calls:
        if getattr(call, "__require_scope__", None) is not None:
            return True
        name = getattr(call, "__name__", "")
        if name.startswith("require_scope"):
            return True
    return False


def _iter_app_routes(fastapi_app):
    """Yield APIRoute / APIWebSocketRoute, unwrapping FastAPI ``_IncludedRouter``."""
    stack = list(fastapi_app.routes)
    while stack:
        route = stack.pop()
        if isinstance(route, (APIRoute, APIWebSocketRoute)):
            yield route
        elif type(route).__name__ == "_IncludedRouter":
            stack.extend(route.original_router.routes)
        elif hasattr(route, "routes"):
            stack.extend(route.routes)


def _endpoint_module(route) -> str | None:
    endpoint = getattr(route, "endpoint", None)
    if endpoint is None:
        return None
    return getattr(endpoint, "__module__", None)


def _is_admin_http_route(route: APIRoute) -> bool:
    methods = getattr(route, "methods", None) or set()
    if methods == {"OPTIONS"}:
        return False  # CORS preflight — no auth gate
    path = getattr(route, "path", "") or ""
    if "/admin" in path:
        return True
    mod = _endpoint_module(route)
    if mod in _EXTRA_ADMIN_MODULES:
        # Only endpoints that already carry require_scope (post-sweep) OR
        # whole-router admin modules (users/events/debug/test).
        if mod in {
            "src.api.routes.users",
            "src.api.routes.events",
            "src.api.routes.debug",
            "src.api.routes.test",
        }:
            return True
        calls = _collect_dep_calls(route.dependant)
        return _has_require_scope(calls)
    return False


class TestRequireAdminTripwire:
    def test_zero_require_admin_symbols_in_api_routes(self):
        """Blunt tripwire: no bare require_admin / get_current_admin* left."""
        hits: list[str] = []
        pat = re.compile(
            r"\b(require_admin|get_current_admin_user|get_current_admin)\b"
        )
        for path in sorted(_ROUTES_DIR.glob("*.py")):
            text = path.read_text(encoding="utf-8")
            for i, line in enumerate(text.splitlines(), 1):
                if line.strip().startswith("#"):
                    continue
                # Allow mentions inside admin_scopes.py docstring? none.
                if pat.search(line):
                    # dependencies re-exports aliases still live in auth/,
                    # not here.  Comments already skipped.
                    hits.append(f"{path.name}:{i}:{line.strip()[:100]}")
        assert not hits, "require_admin tripwire failed:\n" + "\n".join(hits)


class TestAdminRouteCompleteness:
    def test_all_admin_http_routes_have_require_scope(self):
        missing: list[str] = []
        checked = 0
        for route in _iter_app_routes(app):
            if not isinstance(route, APIRoute):
                continue
            if not _is_admin_http_route(route):
                continue
            checked += 1
            calls = _collect_dep_calls(route.dependant)
            if not _has_require_scope(calls):
                methods = ",".join(sorted(route.methods or []))
                mod = _endpoint_module(route)
                missing.append(f"{methods} {route.path} ({mod})")
        assert checked >= 50, f"expected a large admin surface, only checked {checked}"
        assert not missing, (
            "Admin HTTP routes missing require_scope:\n"
            + "\n".join(f"  - {m}" for m in sorted(missing))
        )

    def test_admin_websocket_uses_scope_not_flat_is_admin(self):
        src = (_ROUTES_DIR / "websocket.py").read_text(encoding="utf-8")
        # Isolate the admin WS endpoint body
        start = src.index("async def admin_websocket_endpoint")
        end = src.index("async def get_websocket_stats", start)
        body = src[start:end]
        assert "user_has_active_scope" in body
        assert "AUDIT_VIEW" in body
        assert "user.is_admin" not in body


class TestRequireScopeHookPresent:
    def test_require_scope_stamps_coverage_hook(self):
        from src.auth.admin_scopes import PLAYERS_VIEW, BANG_REGENERATE
        from src.auth.dependencies import (
            require_scope,
            require_scope_from_header_or_query,
        )

        dep = require_scope(PLAYERS_VIEW)
        assert getattr(dep, "__require_scope__") == PLAYERS_VIEW
        sse = require_scope_from_header_or_query(BANG_REGENERATE)
        assert getattr(sse, "__require_scope__") == BANG_REGENERATE
