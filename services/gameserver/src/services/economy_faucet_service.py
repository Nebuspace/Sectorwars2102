"""Economy Faucet Service — periodic credit faucets (lifecycle economy Phase 2).

Two faucets run once per canonical week via the NPC scheduler:

  1. REPUTATION STIPEND — every active player receives a small weekly
     credit grant scaled by their personal_reputation tier (the 8-tier
     Villain→Legendary system).  Good-reputation players earn more, giving
     alignment a tangible economic reward beyond the station-price modifier.

  2. SUBSCRIPTION PERK — galactic citizens (``Player.is_galactic_citizen``
     True AND ``User.subscription_tier == "galactic_citizen"``) receive an
     additional weekly credit grant on top of their reputation stipend.

DESIGN — mirrors _run_weekly_decay_sync exactly:
  * Fully synchronous — no asyncio, no AsyncSession, no asyncio.run.
  * DURABLE cadence anchor in ``Galaxy.state`` JSONB (oldest galaxy row),
    advanced in the SAME transaction as the grants so they are atomic.
  * Canonical-week index (not wall-clock or elapsed_seconds) so the week is
    never skipped or double-fired across restarts.
  * xact-advisory-lock-gated on the shared ``_ADVISORY_LOCK_KEY`` — the
    faucet tick runs in its OWN SessionLocal (never inside the lock session
    of _run_due_ticks_sync).
  * Coarse elapsed pre-filter (FAUCET_CHECK_SECONDS in the scheduler) keeps
    us from acquiring the lock + querying Galaxy.state every 60s.

⚠️  NO-CANON NOTE — amounts and cadence are NOT specified in sw2102-docs.
    The constants below are small, defensible defaults flagged for Max to
    ratify (see module docstring summary and the CONSTANTS_FOR_RATIFICATION
    dict at the bottom of this file).

USAGE (from npc_scheduler_service):
    from src.services.economy_faucet_service import run_weekly_faucet_sync
    result = run_weekly_faucet_sync()
    # returns {"stipend_grants": int, "citizen_grants": int,
    #          "total_credits": int, "week": int}
    # or      {"stipend_grants": 0, "citizen_grants": 0,
    #          "total_credits": 0, "week": -1}  (not due / lock elsewhere)
"""

import logging
from datetime import datetime, UTC
from typing import Any, Dict, List

from sqlalchemy import text
from sqlalchemy.orm.attributes import flag_modified

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# ⚠️ NO-CANON — ALL VALUES BELOW REQUIRE MAX'S RATIFICATION
# ---------------------------------------------------------------------------

# Weekly credit stipend per reputation tier.  Keyed by the canonical tier
# name from PersonalReputationService.REPUTATION_TIERS.  Values are small
# enough that a Legendary player earns a meaningful but not game-breaking
# supplement (~the value of a single modest trade run), while Villain players
# receive only a survival floor so they can't be locked into poverty purely
# by alignment choices.
#
# Rationale for the shape: the difference between Neutral and Legendary
# should be noticeable (×4) but not a dominant income source; the negative-
# rep tiers receive diminishing but nonzero grants (alignment shouldn't be a
# poverty trap — it already imposes market penalties).
STIPEND_BY_TIER: Dict[str, int] = {
    "Villain":    250,    # ⚠️ NO-CANON — floor grant; alignment already costly
    "Criminal":   500,    # ⚠️ NO-CANON
    "Outlaw":     750,    # ⚠️ NO-CANON
    "Suspicious": 1_000,  # ⚠️ NO-CANON
    "Neutral":    1_500,  # ⚠️ NO-CANON — baseline for an unaligned spacer
    "Lawful":     2_000,  # ⚠️ NO-CANON
    "Heroic":     3_000,  # ⚠️ NO-CANON
    "Legendary":  5_000,  # ⚠️ NO-CANON — max tier, ~one good trade run/week
}

# Additional weekly credit grant for galactic citizens (subscription perk).
# This is on TOP of the reputation stipend, making citizenship a meaningful
# economic differentiator (~one extra small-haul profit per week).
CITIZEN_WEEKLY_PERK: int = 2_500  # ⚠️ NO-CANON — ~½ of a Neutral stipend

# Galaxy.state JSONB key for the durable cadence anchor — chosen to avoid
# collision with the weekly_decay key ("weekly_decay_last_week").
_FAUCET_STATE_KEY: str = "economy_faucet_last_week"

# Subscription tier string that qualifies for the citizen perk — matches the
# value written by paypal_service._activate_galactic_citizenship.
_CITIZEN_TIER: str = "galactic_citizen"

# Advisory lock key reused from npc_scheduler_service (same literal).
# Defined here as a named constant so it's legible — the value MUST match.
_ADVISORY_LOCK_KEY: int = 0x53573231  # 'SW21'

# ---------------------------------------------------------------------------
# Public: canonical week helpers (re-export-friendly, no circular import)
# ---------------------------------------------------------------------------

_CANONICAL_WEEK_DAYS: int = 7


def _canonical_week_number(now=None) -> int:
    """Monotonic canonical-week index since epoch — identical logic to
    npc_scheduler_service.canonical_week_number (duplicated here to avoid a
    circular import; both call game_time.GAME_TIME_SCALE)."""
    from src.core import game_time

    now = now or datetime.now(UTC)
    canonical_day = int(now.timestamp() * game_time.GAME_TIME_SCALE // 86400)
    return canonical_day // _CANONICAL_WEEK_DAYS


# ---------------------------------------------------------------------------
# Faucet helpers — run inside the caller's single atomic transaction
# ---------------------------------------------------------------------------

def _reputation_tier(player) -> str:
    """Return the cached reputation_tier string, falling back to 'Neutral'
    for any player whose tier column has not yet been populated."""
    tier = getattr(player, "reputation_tier", None) or ""
    return tier if tier in STIPEND_BY_TIER else "Neutral"


def _apply_reputation_stipends(
    db,
    player_ids: List[Any],
) -> int:
    """Credit each eligible player the stipend for their current reputation
    tier.  Returns the total credits distributed.

    Runs inside the caller's single atomic weekly transaction — any raise
    propagates to run_weekly_faucet_sync, which rolls everything back and
    retries next wake (so no week is silently half-applied)."""
    from src.models.player import Player

    total = 0
    for pid in player_ids:
        player = db.query(Player).filter(Player.id == pid).first()
        if player is None:
            continue
        tier = _reputation_tier(player)
        grant = STIPEND_BY_TIER.get(tier, STIPEND_BY_TIER["Neutral"])
        player.credits = (player.credits or 0) + grant
        total += grant
    db.flush()
    return total


def _apply_citizen_perks(
    db,
    player_ids: List[Any],
) -> int:
    """Credit the weekly subscription perk to galactic citizens.

    Citizenship check:
      - Player.is_galactic_citizen must be True (the canonical flag set by
        paypal_service._activate_galactic_citizenship).
      - Additionally verify User.subscription_tier == "galactic_citizen" as
        a belt-and-suspenders guard — prevents a revoked subscription that
        left the player flag stale from still receiving the perk.

    Returns the total credits distributed.  Runs inside the same atomic
    weekly transaction as the stipends."""
    from src.models.player import Player
    from src.models.user import User

    total = 0
    for pid in player_ids:
        player = (
            db.query(Player)
            .filter(Player.id == pid, Player.is_galactic_citizen.is_(True))
            .first()
        )
        if player is None:
            continue
        # Belt-and-suspenders: confirm the User row's subscription_tier too.
        user = db.query(User).filter(User.id == player.user_id).first()
        if user is None or user.subscription_tier != _CITIZEN_TIER:
            continue
        player.credits = (player.credits or 0) + CITIZEN_WEEKLY_PERK
        total += CITIZEN_WEEKLY_PERK
    db.flush()
    return total


def _select_faucet_candidate_ids(db) -> List[Any]:
    """Active player ids eligible for faucet grants.  Mirrors
    _select_decay_candidate_ids — only soft-deactivated accounts excluded."""
    from src.models.player import Player

    rows = (
        db.query(Player.id)
        .filter(Player.is_active.is_(True))
        .all()
    )
    return [r[0] for r in rows]


# ---------------------------------------------------------------------------
# Public entry point — called by npc_scheduler_service
# ---------------------------------------------------------------------------

def run_weekly_faucet_sync() -> Dict[str, int]:
    """Weekly economy faucets — FULLY SYNCHRONOUS, self-gated on a DURABLE
    canonical-week anchor in Galaxy.state.

    Pattern mirrors _run_weekly_decay_sync exactly:
      * Own SessionLocal — never uses an AsyncSession or asyncio.run.
      * pg_try_advisory_xact_lock on _ADVISORY_LOCK_KEY — second instance
        skips its tick instead of double-granting.
      * Anchor read + both faucets + anchor advance in ONE transaction.
      * Any raise rolls back everything; the week retries next wake.

    Returns {stipend_grants, citizen_grants, total_credits, week} on
    success, or {…, week: -1} when not due / lock held elsewhere."""
    from src.core.database import SessionLocal
    from src.models.galaxy import Galaxy

    this_week = _canonical_week_number()
    not_due: Dict[str, int] = {
        "stipend_grants": 0,
        "citizen_grants": 0,
        "total_credits": 0,
        "week": -1,
    }

    db = SessionLocal()
    try:
        got_lock = db.execute(
            text("SELECT pg_try_advisory_xact_lock(:key)"),
            {"key": _ADVISORY_LOCK_KEY},
        ).scalar()
        if not got_lock:
            return not_due

        # Stable anchor: OLDEST galaxy row (same pin as weekly_decay).
        galaxy = (
            db.query(Galaxy).order_by(Galaxy.created_at.asc()).first()
        )
        if galaxy is None:
            return not_due

        state = dict(galaxy.state or {})
        last_week = state.get(_FAUCET_STATE_KEY)
        if last_week is not None and int(last_week) >= this_week:
            return not_due

        player_ids = _select_faucet_candidate_ids(db)

        # Both faucets on the SAME session — any raise propagates, rolling
        # back the whole transaction including the anchor advance.
        stipend_credits = _apply_reputation_stipends(db, player_ids)
        citizen_credits = _apply_citizen_perks(db, player_ids)

        # Advance the durable anchor in the SAME transaction.
        state = dict(galaxy.state or {})
        state[_FAUCET_STATE_KEY] = this_week
        galaxy.state = state
        flag_modified(galaxy, "state")

        db.commit()  # commits grants + anchor atomically, releases the lock

        result: Dict[str, int] = {
            "stipend_grants": len(player_ids),
            "citizen_grants": citizen_credits // CITIZEN_WEEKLY_PERK if CITIZEN_WEEKLY_PERK else 0,
            "total_credits": stipend_credits + citizen_credits,
            "week": this_week,
        }
        logger.info(
            "economy-faucet: canonical week %d — stipend=%d cr over %d "
            "player(s), citizen_perk=%d cr over %d citizen(s); "
            "total injected=%d cr",
            this_week,
            stipend_credits,
            len(player_ids),
            citizen_credits,
            result["citizen_grants"],
            result["total_credits"],
        )
        return result

    except Exception:
        logger.exception("economy-faucet: batch failed — week not advanced")
        db.rollback()
        return not_due
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Constants manifest — for Max's ratification
# ---------------------------------------------------------------------------

CONSTANTS_FOR_RATIFICATION: Dict[str, Any] = {
    "STIPEND_BY_TIER": STIPEND_BY_TIER,
    "CITIZEN_WEEKLY_PERK": CITIZEN_WEEKLY_PERK,
    "cadence": "once per canonical week (canonical_week_number advances at "
               "GAME_TIME_SCALE × 86400s / 7; on dev at scale 144 this is "
               "~70 wall-clock minutes per canonical week)",
    "notes": (
        "All credit amounts are NO-CANON placeholders. "
        "Recommended ratification questions: "
        "(1) Is the Neutral stipend (1500 cr/wk) too generous, too stingy, "
        "or about right relative to a typical trade run yield? "
        "(2) Should the Villain floor (250 cr/wk) exist at all, or should "
        "negative-rep players receive zero (making alignment a harsher sink)? "
        "(3) Is the citizen perk (2500 cr/wk on top of stipend) a meaningful "
        "differentiator for a $5/month subscriber? "
        "(4) Should Regional_Owner / Nexus_Premium tiers receive a higher "
        "citizen perk tier, or is a flat perk for all is_galactic_citizen "
        "players sufficient for now?"
    ),
}
