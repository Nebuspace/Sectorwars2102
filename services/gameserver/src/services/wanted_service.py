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

Duration ratified 2026-08-06 (DECISIONS.md `wanted-black-market-bust-duration`):
flat 24h window matching black-market.md's fence-closure-on-raid precedent.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from sqlalchemy.orm import Session

from src.models.player import Player

logger = logging.getLogger(__name__)

# Ratified 2026-08-06 (DECISIONS.md wanted-black-market-bust-duration).
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


def _is_piloting_stolen_ship(db: Session, player: Player) -> bool:
    """True iff ``player`` is the current pilot of any ``Ship`` with
    ``stolen_status = True`` (ship-registry.md "Wanted Status (pilot of a
    stolen ship)"). A live query rather than a cached flag -- the pilot
    relationship changes at several independent write sites (ship purchase,
    ``set-active``, eject/escape-pod) and re-deriving from ``Ship`` is the
    single source of truth ``sync_current_pilot`` already maintains."""
    from src.models.ship import Ship

    return (
        db.query(Ship.id)
        .filter(Ship.current_pilot_id == player.id, Ship.stolen_status.is_(True))
        .first()
        is not None
    )


# personal_reputation threshold below which the reputation-based Wanted
# trigger fires (ranking.md "Villain / Criminal tiers", DATA_MODELS/
# player.md wanted-trigger union). Auto-clears on recovery >= this value.
WANTED_REPUTATION_THRESHOLD = -500


def recompute_is_wanted(db: Session, player: Player, *, now: Optional[datetime] = None) -> bool:
    """Single source of truth for ``Player.is_wanted``, OR-ing the three
    documented triggers (ranking.md "Wanted status" trigger set): (1) the
    Severe black-market-bust timer (``wanted_until``), (2) piloting a
    stolen-flagged ship, (3) ``personal_reputation < WANTED_REPUTATION_
    THRESHOLD``. Any ONE live trigger keeps ``is_wanted`` True; ``is_wanted``
    only clears once ALL three are false, so e.g. a reputation recovery
    while still piloting a stolen ship correctly stays Wanted.

    Call after anything that changes one of the three underlying signals:
    a stolen report filed/retracted (``ship_registry_service``), a
    reputation adjustment (``personal_reputation_service``), or a pilot
    change on a stolen ship (the daily stolen-ship rep-penalty sweep, which
    already iterates exactly this candidate set).

    ``wanted_declared_at`` is stamped only on a true first-acquisition
    (mirrors ``apply_wanted_event``/``suspect_declared_at``'s contract) and
    left untouched while already-Wanted from a different trigger. FLUSH
    only -- the caller owns commit. Returns True iff this call flipped
    ``is_wanted`` (either direction)."""
    now = _now(now)
    bust_active = is_live_wanted(player, now=now)
    stolen_active = _is_piloting_stolen_ship(db, player)
    rep_active = (
        player.personal_reputation is not None
        and player.personal_reputation < WANTED_REPUTATION_THRESHOLD
    )
    should_be_wanted = bust_active or stolen_active or rep_active

    if should_be_wanted and not player.is_wanted:
        player.is_wanted = True
        player.wanted_declared_at = now
        return True

    if not should_be_wanted and player.is_wanted:
        player.is_wanted = False
        player.wanted_declared_at = None
        # wanted_until is left alone here on purpose: a live bust timer is
        # one of the OR terms above, so should_be_wanted is already False
        # only once wanted_until has elapsed (or was never set).
        return True

    return False


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
    "WANTED_REPUTATION_THRESHOLD",
    "is_live_wanted",
    "apply_wanted_event",
    "clear_expired_wanted",
    "recompute_is_wanted",
]
