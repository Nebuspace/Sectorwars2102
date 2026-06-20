"""Per-IP sliding-window rate limit for the account-creation auth endpoints
(WO-IL6, Review correction #1).

WHY THIS IS BUILT, NOT "REUSED"
-------------------------------
The brief's Review correction #1 is correct: there is no STRICT, register-
specific rate limit live today. Two stacks exist —
  * ``src/middleware/rate_limit.py`` has a correct 3/5-min rule for /register
    but is **never mounted** in main.py (dead);
  * ``src/api/middleware/security.py`` (RateLimitingMiddleware, mounted) covers
    any "/auth" path at a shared 10/min bucket — no register/exchange-specific cap.

So the brute-force / mass-redemption defense for the invite redeem path must be
**added**. This module is a small, dependency-injected, in-process sliding-window
limiter mirroring the proven pattern in ``api/routes/websocket.py``
(``_check_ws_rate_limit``): a per-key deque of timestamps, prune older than the
window, reject at the cap. It is applied as a ``Depends(...)`` on POST /register
and POST /exchange so the cap is enforced at the route, independent of whichever
global middleware is mounted.

PROPOSED NUMBERS (NO-CANON — flag for Max, brief D-style):
  * /register : 5 attempts / 5 minutes / IP  (conservative; allows a couple of
                422 retries for a fat-fingered form, blocks swarm registration).
  * /exchange : 30 attempts / 5 minutes / IP  (the SPA exchanges its OAuth code
                once immediately; a higher bound tolerates retries/refreshes but
                still caps abuse of the one-time-code endpoint).
These mirror (and slightly relax for human retry) the dead stack-1 register rule
(3/5min). Adjust per Max's ruling — they are module constants, one edit each.

CAVEATS (carried forward, not hidden):
  * In-process + per-worker (same caveat the OAuth state store and the websocket
    limiter already carry — `oauth.py:15-17`). For multi-instance production this
    should move to Redis; that is a deploy-topology decision for Max, out of this
    WO's scope. Documented here so it is not mistaken for a complete solution.
  * Client IP is read from the same trusted-proxy chain the rest of the app uses
    (request.client.host, with X-Forwarded-For honored only behind the trusted
    proxy). A spoofable header would let an attacker rotate keys; the global
    middleware remains a second layer.
"""

import time
import logging
from collections import defaultdict, deque
from typing import Deque, Dict

from fastapi import HTTPException, Request, status

logger = logging.getLogger(__name__)

# --- PROPOSED caps (NO-CANON; flag for Max) ---------------------------------
REGISTER_MAX_ATTEMPTS = 5
REGISTER_WINDOW_SECONDS = 300  # 5 minutes

EXCHANGE_MAX_ATTEMPTS = 30
EXCHANGE_WINDOW_SECONDS = 300  # 5 minutes

# Per-bucket, per-key timestamp deques. Bounded growth: keys are pruned lazily
# (empty deques are dropped) so a flood of distinct IPs cannot grow this forever
# beyond the active-within-window set.
_buckets: Dict[str, Dict[str, Deque[float]]] = defaultdict(lambda: defaultdict(deque))


def _client_key(request: Request) -> str:
    """Best-effort per-client key. Key order (Max-ruled 2026-06-20):
    ``cf-connecting-ip`` → ``X-Forwarded-For`` first hop → socket peer.

    The stack sits behind Cloudflare → nginx in every non-local env. Cloudflare
    sets ``CF-Connecting-IP`` to the real client IP and STRIPS any client-supplied
    copy, so it cannot be spoofed; prefer it. A raw client-supplied
    ``X-Forwarded-For`` first hop IS forgeable (an attacker rotates the header to
    get a fresh per-request bucket and bypass the cap) — so XFF is only the
    second-choice fallback for envs without Cloudflare, and the socket peer is the
    last resort. Never raises — a missing client yields a constant key so the
    limit still applies (fail-closed-ish: shared bucket rather than no bucket)."""
    cf_ip = request.headers.get("cf-connecting-ip")
    if cf_ip:
        return cf_ip.strip()
    xff = request.headers.get("x-forwarded-for")
    if xff:
        # First hop is the original client per the proxy convention (forgeable
        # without Cloudflare in front — see above; CF-Connecting-IP is preferred).
        return xff.split(",")[0].strip()
    client = request.client
    return client.host if client else "unknown"


def _check(bucket: str, key: str, max_attempts: int, window: float) -> bool:
    """Sliding-window check + record. Returns True if allowed, False if over.

    Mirrors websocket.py._check_ws_rate_limit: prune timestamps older than the
    window, reject at the cap, else record now and allow.
    """
    now = time.monotonic()
    timestamps = _buckets[bucket][key]
    # Prune from the left (oldest) — deque keeps insertion order.
    while timestamps and now - timestamps[0] >= window:
        timestamps.popleft()
    if len(timestamps) >= max_attempts:
        return False
    timestamps.append(now)
    # Drop the deque reference cleanup is unnecessary; an empty deque stays
    # cheap. (Lazy GC of idle keys could be added if memory ever matters.)
    return True


def _retry_after(window: float) -> int:
    return int(window)


def register_rate_limit(request: Request) -> None:
    """FastAPI dependency: cap POST /register attempts per IP. 429 when over.

    Runs BEFORE the route body, so a swarm is rejected before any DB work (and
    before any invite lookup). Conventional 429 + Retry-After.
    """
    key = _client_key(request)
    if not _check("register", key, REGISTER_MAX_ATTEMPTS, REGISTER_WINDOW_SECONDS):
        logger.warning("register rate limit exceeded for client key=%s", key[:32])
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many registration attempts. Please try again later.",
            headers={"Retry-After": str(_retry_after(REGISTER_WINDOW_SECONDS))},
        )


def exchange_rate_limit(request: Request) -> None:
    """FastAPI dependency: cap POST /exchange attempts per IP. 429 when over."""
    key = _client_key(request)
    if not _check("exchange", key, EXCHANGE_MAX_ATTEMPTS, EXCHANGE_WINDOW_SECONDS):
        logger.warning("exchange rate limit exceeded for client key=%s", key[:32])
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many code-exchange attempts. Please try again later.",
            headers={"Retry-After": str(_retry_after(EXCHANGE_WINDOW_SECONDS))},
        )


def _reset_for_tests() -> None:
    """Test hook: clear all buckets so a test's attempts don't bleed across cases."""
    _buckets.clear()
