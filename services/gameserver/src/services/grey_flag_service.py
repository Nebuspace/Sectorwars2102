"""Grey-flag PvP status service (WO-BL).

The "grey" flag is a temporary, expiring "open season" mark a player earns by
aggressing on a LAWFUL target. It REPLACES the old combat-driven is_suspect /
is_wanted auto-set (which had wrong triggers and no expiry) — those columns are
left in place for the canon-correct cargo-wreck / stolen-ship triggers, but the
combat path no longer touches them; grey is the combat-aggression consequence.

Design (Max-ruled):

  - Attacking a GOOD-STANDING player  → attacker goes GREY for 1 HOUR
    (kind="player_attack"). While the attacker is grey, GOOD-STANDING players may
    attack the grey player with NO reputation penalty (it is lawful to bring an
    aggressor to justice).
  - Attacking a STATION → attacker goes GREY for 1 DAY (kind="station_attack").
    While grey, ANY player (good or evil) may attack the grey player penalty-free
    (assaulting infrastructure makes you everyone's fair game).
  - Clear: auto-expires at grey_until, OR pay a FINE to clear early.

The penalty-free distinction (player-grey = only good-standing attackers go
penalty-free; station-grey = anyone goes penalty-free) is driven by the cached
``grey_kind`` column, so the predicate never has to reconstruct the offense.

⚠️ NO-CANON NUMBERS (proposed kernel — flagged for Max / DECISIONS.md
"grey-flag-pvp-status"):
  - GOOD-STANDING threshold    : personal_reputation >= 0
  - player-attack grey duration: 3600 s   (1 hour)
  - station-attack grey duration: 86400 s  (1 day)
  - fine to clear player-grey  : 10,000 cr
  - fine to clear station-grey : 50,000 cr
  - both grey kinds are fine-clearable.
A longer remaining grey is NOT shortened by a lesser later offense — set_grey
takes MAX(existing grey_until, new grey_until).
"""

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Dict, Any, Optional

from sqlalchemy.orm import Session

from src.models.player import Player

logger = logging.getLogger(__name__)

# Grey kinds (cached in Player.grey_kind).
GREY_KIND_PLAYER_ATTACK = "player_attack"
GREY_KIND_STATION_ATTACK = "station_attack"

# ⚠️ NO-CANON — proposed kernel, flagged for Max.
GOOD_STANDING_MIN_REPUTATION = 0  # personal_reputation >= 0 == "good standing"

GREY_DURATION_SECONDS = {
    GREY_KIND_PLAYER_ATTACK: 3600,     # 1 hour
    GREY_KIND_STATION_ATTACK: 86400,   # 1 day
}

GREY_CLEAR_FINE_CREDITS = {
    GREY_KIND_PLAYER_ATTACK: 10_000,
    GREY_KIND_STATION_ATTACK: 50_000,
}


def _now() -> datetime:
    """Timezone-aware UTC now (grey_until is a tz-aware DateTime)."""
    return datetime.now(timezone.utc)


def _as_aware(dt: Optional[datetime]) -> Optional[datetime]:
    """Coerce a possibly-naive DateTime read back from the DB to tz-aware UTC.

    Postgres ``timestamptz`` round-trips as aware, but a defensive coercion keeps
    the comparisons below correct even if a naive value ever slips in (mixing
    naive and aware datetimes raises TypeError)."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def is_good_standing(player: Player) -> bool:
    """A player is in GOOD STANDING iff personal_reputation >= the threshold.

    NO-CANON: threshold == 0 (proposed). Used both to decide whether attacking a
    target sets the attacker grey (only attacks on good-standing players do) and
    whether an attacker qualifies for the player-grey penalty-free exemption."""
    return (player.personal_reputation or 0) >= GOOD_STANDING_MIN_REPUTATION


class GreyFlagService:
    """Set / query / clear the grey-flag PvP status on a player.

    Stateless beyond the injected session. All mutators flush() (never commit) so
    they fold into the caller's single locked transaction — combat resolution
    commits once at the end, exactly like PersonalReputationService.adjust_reputation.
    """

    def __init__(self, db: Session):
        self.db = db

    # --- queries ---------------------------------------------------------

    def is_grey(self, player: Player) -> bool:
        """True iff the player currently has a live (un-expired) grey flag."""
        grey_until = _as_aware(player.grey_until)
        return grey_until is not None and grey_until > _now()

    def grey_status(self, player: Player) -> Dict[str, Any]:
        """Structured grey status for API exposure.

        Returns is_grey, kind, grey_until (ISO8601 or None), remaining_seconds
        (0 when not grey), and the fine that would clear it early (None when not
        grey). An EXPIRED grey reports is_grey=False with remaining_seconds 0 — it
        is treated as cleared even though the column has not yet been NULLed (the
        is_grey predicate already ignores expired rows; lazy auto-clear below
        NULLs the columns opportunistically)."""
        grey_until = _as_aware(player.grey_until)
        live = self.is_grey(player)
        if not live:
            return {
                "is_grey": False,
                "kind": None,
                "grey_until": None,
                "remaining_seconds": 0,
                "clear_fine_credits": None,
            }
        remaining = int((grey_until - _now()).total_seconds())
        kind = player.grey_kind
        return {
            "is_grey": True,
            "kind": kind,
            "grey_until": grey_until.isoformat(),
            "remaining_seconds": max(0, remaining),
            "clear_fine_credits": GREY_CLEAR_FINE_CREDITS.get(kind),
        }

    # --- mutators --------------------------------------------------------

    def set_grey(self, player: Player, kind: str) -> Dict[str, Any]:
        """Mark a player grey for the given offense kind.

        kind ∈ {"player_attack" (1h), "station_attack" (1 day)}.

        Takes MAX(existing grey_until, new grey_until): a longer remaining grey is
        NEVER shortened by a lesser later offense. When the new offense extends the
        flag past the existing expiry the grey_kind is updated to the new (longer)
        offense's kind; when the existing flag already runs longer the existing
        kind is preserved (it is the more severe / longer-lived status, and
        downgrading kind would weaken the penalty-free rule against it).

        flush()-only — folds into the caller's transaction. Returns the resulting
        status dict (see grey_status)."""
        if kind not in GREY_DURATION_SECONDS:
            logger.error("set_grey called with unknown kind=%r — ignored", kind)
            return self.grey_status(player)

        new_until = _now() + timedelta(seconds=GREY_DURATION_SECONDS[kind])
        existing_until = _as_aware(player.grey_until)

        # Only an expiry STRICTLY beyond the live existing one extends the flag.
        if existing_until is None or existing_until <= _now() or new_until > existing_until:
            player.grey_until = new_until
            player.grey_kind = kind
        # else: existing live grey runs longer — keep it (kind + until), MAX rule.

        self.db.flush()
        logger.info(
            "Grey flag set for player %s: kind=%s grey_until=%s (effective)",
            player.id, player.grey_kind, player.grey_until,
        )
        return self.grey_status(player)

    def clear_grey(self, player: Player) -> None:
        """Unconditionally clear the grey flag (set both columns NULL).

        Used by the lazy auto-clear path and after a paid fine. flush()-only."""
        player.grey_until = None
        player.grey_kind = None
        self.db.flush()

    def auto_clear_if_expired(self, player: Player) -> bool:
        """Opportunistically NULL the columns if the grey flag has expired.

        Returns True if a clear happened. Keeps the predicate (is_grey) the single
        source of truth — this is purely cosmetic column hygiene so a stale
        grey_until/grey_kind pair doesn't linger forever after expiry."""
        grey_until = _as_aware(player.grey_until)
        if grey_until is not None and grey_until <= _now():
            self.clear_grey(player)
            return True
        return False

    def clear_grey_by_fine(self, player_id: uuid.UUID) -> Dict[str, Any]:
        """Charge the early-clear fine under a row lock and clear the grey flag.

        Re-fetches the player FOR UPDATE (the caller passes an id, not a row) so
        the credit debit + grey clear is serialized against concurrent
        spends/clears. Commits at the end (this is the route's own transaction).

        Returns {success, ...}. Failure modes (each leaves credits + grey
        untouched): player not found; not currently grey; insufficient credits.
        """
        player = (
            self.db.query(Player)
            .filter(Player.id == player_id)
            .populate_existing()
            .with_for_update()
            .first()
        )
        if not player:
            return {"success": False, "message": "Player not found"}

        # Auto-clear an expired flag first so a player can't be charged to clear a
        # grey that has already lapsed.
        if self.auto_clear_if_expired(player):
            self.db.commit()
            return {"success": False, "message": "Your grey status has already expired"}

        if not self.is_grey(player):
            return {"success": False, "message": "You are not currently grey"}

        kind = player.grey_kind
        fine = GREY_CLEAR_FINE_CREDITS.get(kind)
        if fine is None:
            # Defensive: a live grey with an unknown kind has no defined fine.
            logger.error(
                "clear_grey_by_fine: player %s grey with unknown kind=%r — no fine",
                player.id, kind,
            )
            return {"success": False, "message": "This grey status cannot be cleared by fine"}

        if (player.credits or 0) < fine:
            return {
                "success": False,
                "message": f"Not enough credits to pay the fine (need {fine:,})",
                "fine_credits": fine,
                "credits": player.credits or 0,
            }

        player.credits = (player.credits or 0) - fine
        self.clear_grey(player)  # flush
        self.db.commit()

        logger.info(
            "Player %s paid %d cr fine to clear %s grey flag",
            player.id, fine, kind,
        )
        return {
            "success": True,
            "message": f"Grey status cleared — fine of {fine:,} cr paid",
            "fine_paid": fine,
            "credits_remaining": player.credits,
        }


def attack_is_penalty_free(attacker: Player, target: Player) -> bool:
    """Predicate: should the attacker's reputation penalty be SKIPPED for killing
    this target?

    True iff the target is currently grey AND the attacker qualifies for that
    grey kind's exemption:

      - station_attack grey → ANY attacker is penalty-free (the target made
        themselves everyone's fair game by assaulting infrastructure).
      - player_attack grey  → only a GOOD-STANDING attacker (personal_reputation
        >= threshold) is penalty-free — a fellow aggressor gunning down a grey
        player is NOT acting as justice and still eats the penalty.

    Pure function over already-loaded rows (no DB access) so it can be evaluated
    inline at the rep-penalty point without an extra query/lock. The grey-live
    check is inlined (mirrors GreyFlagService.is_grey) to keep it dependency-free.
    """
    if target is None or attacker is None:
        return False

    grey_until = _as_aware(getattr(target, "grey_until", None))
    if grey_until is None or grey_until <= _now():
        return False  # target not grey (or expired) — penalty applies normally

    kind = getattr(target, "grey_kind", None)
    if kind == GREY_KIND_STATION_ATTACK:
        return True  # anyone may bring a station-attacker to justice
    if kind == GREY_KIND_PLAYER_ATTACK:
        return is_good_standing(attacker)  # only good-standing players go free
    return False  # unknown / NULL kind on a live grey — be conservative
