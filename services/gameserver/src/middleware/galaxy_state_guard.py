"""Galaxy import-state guard middleware.

While a bang generation job is mid-flight the Galaxy row exists in
`import_state = GENERATING`. Per the bang integration plan (Phase 1B,
`DOCS/PLANS/bang-integration.md`) player traffic to non-admin routes must
return 503 during that window; only the admin UI keeps working so the
operator can monitor the job.

Implementation: a single Starlette middleware that checks the cached
`Galaxy.import_state` once per request, with a 5-second TTL on the DB
lookup to avoid per-request hits. Admin paths and a small allow-list
(health, root, docs) bypass the check.
"""
from __future__ import annotations

import logging
import time
from typing import Callable, Optional

from fastapi import Request
from sqlalchemy import select
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse, Response

logger = logging.getLogger(__name__)

# Paths that always pass through, regardless of galaxy state.
# `/api/v1/admin/` covers all admin endpoints (auth, galaxy ops, etc.).
# `/api/v1/auth/` is whitelisted so admins can still log in during a job.
# NOTE: root "/" is intentionally NOT a prefix here — every path starts with
# "/", so a prefix-matched "/" would allowlist literally everything and make
# this guard a no-op (WO-LIVE-SUITE-TRIAGE 2026-07-02 caught this live: the
# guard never once returned 503). The bare landing route is handled by
# _EXACT_ALLOWLIST below instead.
_ALLOWLIST_PREFIXES: tuple[str, ...] = (
    "/api/v1/admin/",
    "/api/v1/admin",
    "/api/v1/auth/",
    "/api/v1/auth",
    "/api/admin/",   # legacy / non-versioned admin path used in some routes
    "/api/admin",
    "/health",
    "/docs",
    "/redoc",
    "/openapi.json",
)

# Paths that must match EXACTLY (not as a prefix) to avoid catch-all drift
# like the "/" bug above. Currently just the root landing route.
_EXACT_ALLOWLIST: frozenset[str] = frozenset({"/"})

# State value (string form of Galaxy.import_state enum) that permits traffic.
_READY_STATE = "READY"


class GalaxyStateGuardMiddleware(BaseHTTPMiddleware):
    """Block non-admin traffic while the galaxy is not READY.

    The cached state value lives in module-scope so that consecutive
    requests within a 5-second window share one DB hit. If the DB lookup
    raises (e.g., schema not yet migrated in a fresh dev environment) the
    middleware fails-open — players see normal responses — and logs the
    error. That tradeoff is intentional: a broken middleware should not
    take the whole game down.
    """

    # TTL on the cached state. Tuned to balance "admin sees state change
    # quickly" vs "per-request DB hit on every player action".
    CACHE_TTL_SECONDS: float = 5.0

    def __init__(self, app, session_factory: Optional[Callable] = None) -> None:
        super().__init__(app)
        self._cached_state: Optional[str] = None
        self._cache_expires_at: float = 0.0
        # Allow tests to inject a session factory; default to the app's
        # AsyncSessionLocal at call time (avoids import-time cycles).
        self._session_factory = session_factory

    def _is_allowlisted(self, path: str) -> bool:
        if path in _EXACT_ALLOWLIST:
            return True
        return any(path.startswith(prefix) for prefix in _ALLOWLIST_PREFIXES)

    async def _resolve_state(self) -> Optional[str]:
        """Return cached state or refresh from DB. None on lookup error."""
        now = time.monotonic()
        if self._cached_state is not None and now < self._cache_expires_at:
            return self._cached_state

        try:
            session_factory = self._session_factory
            if session_factory is None:
                # Late import to avoid circular dependency at module load.
                from src.core.database import AsyncSessionLocal
                session_factory = AsyncSessionLocal

            async with session_factory() as session:
                # Schema-tolerant query: import_state is added by the
                # `galaxy_audit_columns` migration. Until that ships in a
                # given environment, this query may fail — we fall through
                # to fail-open below.
                result = await session.execute(
                    select_galaxy_import_state()
                )
                row = result.first()
                if row is None:
                    # No Galaxy yet — let traffic flow (fresh environment).
                    state = _READY_STATE
                else:
                    raw = row[0]
                    # Postgres ENUM comes back as str; SQLAlchemy enum types
                    # may return a Python Enum. Normalize to .name/str.
                    state = getattr(raw, "name", None) or str(raw)
        except Exception as exc:  # pragma: no cover - logged + fail-open
            logger.warning(
                "GalaxyStateGuard: lookup failed, failing open: %s", exc
            )
            return None

        self._cached_state = state
        self._cache_expires_at = now + self.CACHE_TTL_SECONDS
        return state

    async def dispatch(self, request: Request, call_next) -> Response:
        path = request.url.path

        if self._is_allowlisted(path):
            return await call_next(request)

        state = await self._resolve_state()
        # Fail-open: if the lookup failed, don't block traffic.
        if state is None or state == _READY_STATE:
            return await call_next(request)

        return JSONResponse(
            status_code=503,
            content={
                "detail": "Galaxy is initializing",
                "state": state,
            },
        )

    def invalidate_cache(self) -> None:
        """Force-refresh on next request. Called by the job orchestrator."""
        self._cached_state = None
        self._cache_expires_at = 0.0


def select_galaxy_import_state():
    """Return the SELECT for Galaxy.import_state.

    Isolated as a function so tests can monkeypatch the lookup without
    touching the middleware class. Returns the *first* galaxy row's state;
    the current architecture assumes a single Galaxy per environment.
    """
    # Local import to avoid pulling the whole model graph at middleware
    # import time (Starlette middleware classes load early).
    from src.models.galaxy import Galaxy

    return select(Galaxy.import_state).limit(1)
