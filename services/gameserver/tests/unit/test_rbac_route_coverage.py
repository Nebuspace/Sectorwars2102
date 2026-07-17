"""RBAC Phase B — route coverage for the first admin-route sweep batch.

DB-free.  Imports ``app`` from ``src.main`` (same env harness as
``test_rbac_phase_a2.py`` / ``test_admin_multi_account.py``).

For each HTTP route in the swept modules whose path or tags look admin-facing,
assert the fully-merged FastAPI dependant tree includes ``require_scope``.
The admin WebSocket in ``websocket.py`` uses token auth + inline
``user_has_active_scope(..., AUDIT_VIEW)`` instead — verified by source grep.
"""

from __future__ import annotations

import inspect
from pathlib import Path
from typing import Callable, Iterable, Set

import pytest
from fastapi.routing import APIRoute, APIWebSocketRoute

from src.main import app

_GAMESERVER_ROOT = Path(__file__).resolve().parents[2]

# Phase B batch 1 — modules swept from require_admin → require_scope
_SWEPT_MODULES: frozenset[str] = frozenset(
    {
        "src.api.routes.admin_multi_account",
        "src.api.routes.admin_reports",
        "src.api.routes.admin_contract_disputes",
        "src.api.routes.admin_first_login",
        "src.api.routes.websocket",
    }
)

_ADMIN_TAG_HINTS = frozenset({"admin", "admin-reports", "admin-multi-account", "admin-contract-disputes", "admin-scopes"})


def _is_admin_route(route: APIRoute | APIWebSocketRoute) -> bool:
    path = getattr(route, "path", "") or ""
    if "/admin" in path:
        return True
    tags = getattr(route, "tags", None) or []
    return any(str(t).lower() in _ADMIN_TAG_HINTS or "admin" in str(t).lower() for t in tags)


def _collect_dep_calls(dependant, seen: Set[int] | None = None) -> list[Callable]:
    """Depth-first walk of a FastAPI Dependant tree."""
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
    """Yield APIRoute / APIWebSocketRoute from app and nested mounts."""
    stack = list(fastapi_app.routes)
    while stack:
        route = stack.pop()
        if isinstance(route, (APIRoute, APIWebSocketRoute)):
            yield route
        elif hasattr(route, "routes"):
            stack.extend(route.routes)


def _endpoint_module(route: APIRoute | APIWebSocketRoute) -> str | None:
    endpoint = getattr(route, "endpoint", None)
    if endpoint is None:
        return None
    return getattr(endpoint, "__module__", None)


class TestPhaseBBatch1Coverage:
    """Swept admin HTTP routes must declare require_scope in merged deps."""

    def test_swept_admin_http_routes_have_require_scope(self):
        missing: list[str] = []
        for route in _iter_app_routes(app):
            if not isinstance(route, APIRoute):
                continue
            mod = _endpoint_module(route)
            if mod not in _SWEPT_MODULES:
                continue
            if not _is_admin_route(route):
                continue
            calls = _collect_dep_calls(route.dependant)
            if not _has_require_scope(calls):
                methods = ",".join(sorted(route.methods or []))
                missing.append(f"{methods} {route.path} ({mod})")
        assert not missing, (
            "Swept admin HTTP routes missing require_scope in merged dependencies:\n"
            + "\n".join(f"  - {m}" for m in sorted(missing))
        )

    def test_admin_websocket_uses_audit_view_scope_check(self):
        """Token-auth WS cannot use Depends(require_scope) — inline check instead."""
        src_path = _GAMESERVER_ROOT / "src/api/routes/websocket.py"
        src = src_path.read_text(encoding="utf-8")
        assert "user_has_active_scope" in src
        assert "AUDIT_VIEW" in src
        assert "user.is_admin" not in src.split("admin_websocket_endpoint")[1].split("def get_websocket_stats")[0]

    def test_admin_multi_account_has_zero_require_admin(self):
        src_path = _GAMESERVER_ROOT / "src/api/routes/admin_multi_account.py"
        src = src_path.read_text(encoding="utf-8")
        assert "require_admin" not in src
        assert "get_current_admin" not in src


class TestRequireScopeHookPresent:
    """Sanity: require_scope factory still stamps __require_scope__ for audits."""

    def test_require_scope_stamps_coverage_hook(self):
        from src.auth.admin_scopes import PLAYERS_VIEW
        from src.auth.dependencies import require_scope

        dep = require_scope(PLAYERS_VIEW)
        assert getattr(dep, "__require_scope__") == PLAYERS_VIEW
