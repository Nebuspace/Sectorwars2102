"""Wanted-status fixed-duration timer for non-stolen-ship triggers
(WO-BUILD-WANTED-UNTIL-TIMER, 2026-08-04).

Canon: sw2102-docs/FEATURES/gameplay/ranking.md#wanted-status names two
Wanted triggers with condition-based auto-clears -- stolen-ship-piloting
(clears on eject/destroy/impound/retraction) and personal_reputation < -500
(clears on recovery >= -500) -- neither of which is wired as an `is_wanted`
writer anywhere in this codebase today (verified: `grep -rn "\\.is_wanted"
src/services/`). The ONLY live writer is contraband_service.py's
`_apply_heat` (a Severe-severity black-market bust), which sets
`is_wanted = True` and `wanted_declared_at` but had NO clearing path at
all -- a standing bug, not a documented mechanic: Wanted from a bust
persisted forever. DATA_MODELS/player.md's `wanted_until` column (until
now schema-only) is the fix, mirroring `suspect_until`'s pattern
(suspect_service.py) but WITHOUT that mechanic's timer-extension/cumulative-
cap complexity -- ranking.md never describes escalating Wanted duration for
repeat busts, so each bust simply refreshes a flat window.

NO-CANON: the duration itself. No shipped or design-only doc gives a
number for this specific trigger. WANTED_DURATION borrows the 24-hour
magnitude already used elsewhere in the SAME canon file (black-market.md's
"Fence closure on raid: 24 real-time hours") as the closest available
precedent for a serious-consequence duration, rather than inventing an
unrelated number. Flagged here and in FEATURES/gameplay/ranking.md for a
future DECISIONS.md ruling.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from sqlalchemy.orm import Session

from src.models.player import Player

logger = logging.getLogger(__name__)

# NO-CANON (see module docstring): 24h borrows black-market.md's fence-
# closure-on-raid precedent as the nearest documented serious-consequence
# duration in this same feature area.
WANTED_DURATION = timedelta(hours=24)


def _now(now: Optional[datetime]) -> datetime:
    return now if now is not None else datetime.now(timezone.utc)


def is_live_wanted(player: Player, *, now: Optional[datetime] = None) -> bool:
    """True iff ``player`` is EFFECTIVELY wanted right now via the timer
    trigger -- checks ``wanted_until`` against the clock, not just the
    ``is_wanted`` boolean (which can be stale until the lazy auto-clear
    sweep runs). Mirrors ``suspect_service.is_live_suspect``."""
    now = _now(now)
    return bool(player.is_wanted) and player.wanted_until is not None and player.wanted_until > now


def apply_wanted_event(db: Session, player: Player, *, now: Optional[datetime] = None) -> bool:
    """Apply one Wanted-flagging event (a Severe black-market bust) to
    ``player``: flips ``is_wanted``, stamps ``wanted_declared_at`` only on
    first acquisition (mirrors suspect_declared_at's contract), and always
    refreshes ``wanted_until`` to ``now + WANTED_DURATION`` -- no cumulative
    cap, since ranking.md describes no escalating-duration mechanic for
    repeat busts. NO flush, no commit -- pure in-memory mutation; the
    caller (contraband_service.py's ``_apply_heat``) owns its own
    transaction boundary. Returns True iff this was a FIRST acquisition."""
    now = _now(now)
    first_acquisition = not is_live_wanted(player, now=now)
    if first_acquisition:
        player.is_wanted = True
        player.wanted_declared_at = now
    player.wanted_until = now + WANTED_DURATION
    return first_acquisition


def clear_expired_wanted(db: Session, *, now: Optional[datetime] = None) -> int:
    """Auto-clear sweep, mirrors ``suspect_service.clear_expired_suspects``.
    Clears ``is_wanted``, ``wanted_until``, and ``wanted_declared_at`` for
    every player whose ``wanted_until`` has elapsed. Only ever touches
    players flagged via THIS timer trigger (``wanted_until`` non-NULL) --
    a future stolen-ship/reputation writer would leave ``wanted_until``
    NULL and is untouched by this sweep, matching those triggers' own
    condition-based clear paths. FLUSH only -- the scheduler wrapper owns
    SessionLocal + commit. Returns the count cleared."""
    now = _now(now)
    expired: List[Player] = (
        db.query(Player)
        .filter(
            Player.is_wanted.is_(True),
            Player.wanted_until.isnot(None),
            Player.wanted_until <= now,
        )
        .all()
    )
    for player in expired:
        player.is_wanted = False
        player.wanted_until = None
        player.wanted_declared_at = None

    if expired:
        db.flush()
    return len(expired)


__all__ = [
    "WANTED_DURATION",
    "is_live_wanted",
    "apply_wanted_event",
    "clear_expired_wanted",
]
