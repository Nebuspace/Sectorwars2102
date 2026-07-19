"""Economy Faucet Service — periodic credit faucets (lifecycle economy Phase 2).

Two credit faucets, on DIFFERENT cadences (Max's final ruling 2026-06-20):

  1. SUBSCRIPTION PERK — WEEKLY.  Galactic citizens
     (``Player.is_galactic_citizen`` True AND
     ``User.subscription_tier == "galactic_citizen"``) receive a weekly credit
     grant.  This is a PAID benefit (payments-signed-off) and KEEPS its weekly
     cadence — it is the ONLY thing the weekly faucet path pays now.

  2. REPUTATION STIPEND — DAILY + ACTIVE-GATED + PER-FACTION GUILD PAYOUT.
     Every player who logged in THAT UTC day receives a "guild stipend": for
     EACH faction the player is in GOOD STANDING with, a small daily amount
     scaled by the player's reputation TIER WITH THAT FACTION
     (``ReputationLevel``, from the ``Reputation`` rows per (player, faction)),
     SUMMED across all such factions.  An idle day pays 0.  This rewards earning
     standing with the in-game factions/guilds, giving alignment a tangible
     economic reward beyond the station-price modifier.

     ⚠️  BALANCE CAP (Max's KEY constraint): the SUMMED daily total is capped at
     ``GLOBAL_DAILY_STIPEND_CAP`` so a player favored by MANY factions can never
     out-earn the paid weekly citizen perk (~10,000 cr/mo).  The cap is set well
     under ~333 cr/day (the perk's daily-equivalent) — see the constant below.
     The grant is ``min(sum_over_factions, GLOBAL_DAILY_STIPEND_CAP)``.

WHY THE PER-FACTION MODEL (Max's final ruling 2026-06-20): the reputation
stipend used to ride the weekly faucet alongside the citizen perk, paying a
single PERSONAL-rep-tier weekly amount to every active player.  That coupled a
free reward to a paid subscription cadence and ignored the per-faction guild
standing the game actually tracks.  The stipend is now DAILY (gated on actually
playing that day — rewarding engagement), scaled PER FACTION by guild standing
(rewarding deliberate faction alignment), SUMMED, and hard-CAPPED so it can
never out-earn the paid citizen perk.

DESIGN — both paths mirror the proven scheduler sweep discipline:
  * Fully synchronous — no asyncio, no AsyncSession, no asyncio.run.
  * WEEKLY path (run_weekly_faucet_sync): DURABLE cadence anchor in
    ``Galaxy.state`` JSONB (oldest galaxy row), advanced in the SAME transaction
    as the citizen grants so they are atomic; canonical-week index so the week
    is never skipped or double-fired across restarts; xact-advisory-lock-gated
    on ``_WEEKLY_FAUCET_LOCK_KEY`` in its OWN SessionLocal.
  * DAILY path (apply_daily_rep_stipend_for_player): a durable PER-PLAYER
    UTC-date anchor in ``Player.settings`` JSONB advanced in the SAME
    per-player transaction as the credit (the scheduler sweep
    _run_daily_stipend_sweep_sync owns the SessionLocal, advisory lock, and
    per-row with_for_update — see npc_scheduler_service).  The amount is the
    capped per-faction guild sum (see daily_stipend_amount), read from the
    player's already-locked ``Reputation`` rows via the SAME session (no new
    session is opened).  Idempotent across restarts: a re-run within the same
    UTC day re-reads the anchor and skips, so the stipend NEVER double-pays.

⚠️  NO-CANON NOTE — amounts and cadence are NOT specified in sw2102-docs.
    The constants below are small, defensible defaults flagged for Max to
    ratify (see module docstring summary and the CONSTANTS_FOR_RATIFICATION
    dict at the bottom of this file).

USAGE (from npc_scheduler_service):
    # Weekly citizen perk:
    from src.services.economy_faucet_service import run_weekly_faucet_sync
    result = run_weekly_faucet_sync()
    # returns {"citizen_grants": int, "total_credits": int, "week": int}
    # or      {"citizen_grants": 0, "total_credits": 0, "week": -1}

    # Daily rep stipend (called per-player by the scheduler's daily sweep,
    # inside the sweep's own per-player locked transaction):
    from src.services.economy_faucet_service import apply_daily_rep_stipend_for_player
    granted = apply_daily_rep_stipend_for_player(player, today_str)
    # returns the credits granted (0 = idle that day / already paid / 0-tier)
"""

import logging
from datetime import datetime, UTC
from typing import Any, Dict, List

from sqlalchemy import text
from sqlalchemy.orm import object_session
from sqlalchemy.orm.attributes import flag_modified

from src.services.npc_scheduler_service import _mnemonic_lock_key

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# ⚠️ NO-CANON — ALL VALUES BELOW REQUIRE MAX'S RATIFICATION
# ---------------------------------------------------------------------------

# ⚠️ NO-CANON — PER-FACTION DAILY guild stipend, keyed by the player's
# ReputationLevel WITH ONE FACTION (Max's final per-faction ruling 2026-06-20).
# For each faction the player is in GOOD STANDING with (see
# _GOOD_STANDING_MIN_LEVEL), this table gives that faction's daily contribution;
# the contributions are SUMMED across factions, then CAPPED at
# GLOBAL_DAILY_STIPEND_CAP (below).  Paid ONCE PER UTC DAY to each player who
# logged in THAT day (idle day = 0).
#
# Keyed by ReputationLevel.value (the enum's string name) so it survives DB
# round-trips and any enum re-ordering.  Only the POSITIVE (good-standing) levels
# appear — neutral/negative standing contributes 0 (it is below the good-standing
# floor and never enters the sum).  Values START LOW / conservative: a single
# EXALTED faction pays 50 cr/day (~1,500/mo); the cap (below) governs the rest.
PER_FACTION_DAILY_BY_LEVEL: Dict[str, int] = {
    "RECOGNIZED":    5,  # ⚠️ NO-CANON — good-standing floor, ~150/mo per faction
    "ACKNOWLEDGED":  8,  # ⚠️ NO-CANON — ~240/mo per faction
    "TRUSTED":      12,  # ⚠️ NO-CANON — ~360/mo per faction
    "RESPECTED":    16,  # ⚠️ NO-CANON — ~480/mo per faction
    "VALUED":       22,  # ⚠️ NO-CANON — ~660/mo per faction
    "HONORED":      30,  # ⚠️ NO-CANON — ~900/mo per faction
    "REVERED":      40,  # ⚠️ NO-CANON — ~1,200/mo per faction
    "EXALTED":      50,  # ⚠️ NO-CANON — ~1,500/mo per faction (single-faction top)
}

# ⚠️ NO-CANON — GOOD-STANDING THRESHOLD.  A faction contributes to the stipend
# only when the player's standing with it is AT OR ABOVE this ReputationLevel.
# RECOGNIZED (Reputation.current_value >= 50, per
# FactionService._calculate_reputation_level) is the first POSITIVE tier above
# NEUTRAL — i.e. the player has earned recognition, not merely "not hostile".
# NEUTRAL ([-50, 50)) and all negative tiers are NOT good standing → 0.  Encoded
# as the numeric_level (NEUTRAL=0, RECOGNIZED=+1 … EXALTED=+8) so the comparison
# is a simple ordinal >=.
_GOOD_STANDING_MIN_NUMERIC_LEVEL: int = 1  # RECOGNIZED (the first positive tier)

# ⚠️ NO-CANON — GLOBAL DAILY CAP (Max's KEY balance constraint).  The SUMMED
# per-faction stipend is clamped to this so a player favored by MANY factions can
# never out-earn the paid weekly citizen perk (~10,000 cr/mo ⇒ ~333 cr/day
# equivalent).  100 cr/day ⇒ ~3,000 cr/mo at the cap — comfortably under the
# perk, and reached only by a player in good standing with several factions
# (e.g. 2× EXALTED, or 3–4 mid-tier factions).  Starts LOW per Max's directive;
# a single-faction player is below the cap and gets the raw sum.
GLOBAL_DAILY_STIPEND_CAP: int = 100  # ⚠️ NO-CANON — ~3,000/mo at cap, under the perk

# Weekly credit grant for galactic citizens (subscription perk).  This is now
# the ONLY thing the weekly faucet path pays.  ~10,000 cr/mo (2,500 × ~4 weeks)
# — a meaningful, PAID economic differentiator that stays strictly above any
# tier's free daily stipend monthly sum.
CITIZEN_WEEKLY_PERK: int = 2_500  # ⚠️ NO-CANON — paid perk, ~10,000 cr/mo

# Player.settings JSONB key for the durable PER-PLAYER daily-stipend anchor —
# holds the UTC date string (YYYY-MM-DD) of the last day this player was paid
# the daily rep stipend.  Leading underscore namespaces it apart from real
# player-settings keys (bounties / trade_bonus / …), mirroring the
# cargo['_capacity_bonus_percent'] meta-key convention.  Additive JSONB only;
# NO migration, NO new table.
_DAILY_STIPEND_ANCHOR_KEY: str = "_daily_stipend_last_utc_date"

# Galaxy.state JSONB key for the durable cadence anchor — chosen to avoid
# collision with the weekly_decay key ("weekly_decay_last_week").
_FAUCET_STATE_KEY: str = "economy_faucet_last_week"

# Subscription tier string that qualifies for the citizen perk — matches the
# value written by paypal_service._activate_galactic_citizenship.
_CITIZEN_TIER: str = "galactic_citizen"

# Advisory lock key for the weekly faucet sweep. Pre-de-globalization this
# reused npc_scheduler_service's global _ADVISORY_LOCK_KEY literal (same
# value, "MUST match" by design) — that meant a long-held main-tick lock
# could starve the faucet's own sweep. Post-0fe103a de-globalization, every
# sweep serializes only against another instance of ITSELF, never the main
# tick or any unrelated sweep — so this key is now deliberately DISTINCT.
# Derived via npc_scheduler_service's established mnemonic-pack idiom
# (_mnemonic_lock_key) rather than a hand-picked literal, so it inherits the
# same byte-for-byte collision-freedom guarantee as every other per-sweep key.
_WEEKLY_FAUCET_LOCK_KEY: int = _mnemonic_lock_key("WFCT")

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

def daily_stipend_amount(player) -> int:
    """The DAILY guild stipend (credits) for ``player``: the per-faction
    contribution SUMMED over every faction the player is in GOOD STANDING with,
    then clamped to ``GLOBAL_DAILY_STIPEND_CAP``.

    PER-FACTION MODEL (Max's final ruling 2026-06-20): the player's standing with
    each faction lives in the ``Reputation`` rows (one per (player, faction),
    with a ``current_level``/``current_value``).  For each row at or above the
    good-standing floor (``_GOOD_STANDING_MIN_NUMERIC_LEVEL`` = RECOGNIZED), look
    up that level's daily contribution in ``PER_FACTION_DAILY_BY_LEVEL`` and add
    it; sum across factions; return ``min(sum, GLOBAL_DAILY_STIPEND_CAP)``.

    SESSION DISCIPLINE (mandatory): the player's faction reputations are read via
    the SAME session that owns the (locked) ``player`` row — never a new session.
    We resolve that session with ``object_session(player)`` and query the
    ``Reputation`` rows by ``player_id``; this rides the sweep's per-row locked
    transaction.  If the player is somehow session-detached, fall back to the
    relationship collection (``player.faction_reputations``), which is harmless
    here because the caller already holds the row and the relationship would
    lazy-load on the same session.

    Pure of MUTATION (no credit change, no anchor write); does a READ only.
    Returns 0 when the player has no good-standing faction (the common new-player
    case)."""
    reps = _good_standing_reputations(player)

    total = 0
    for rep in reps:
        level = getattr(rep, "current_level", None)
        # numeric_level: NEUTRAL=0, RECOGNIZED=+1 … EXALTED=+8 / negatives < 0.
        numeric = _reputation_numeric_level(rep)
        if numeric is None or numeric < _GOOD_STANDING_MIN_NUMERIC_LEVEL:
            continue
        level_name = getattr(level, "value", None) or getattr(level, "name", None)
        if not isinstance(level_name, str):
            continue
        total += PER_FACTION_DAILY_BY_LEVEL.get(level_name, 0)

    return min(total, GLOBAL_DAILY_STIPEND_CAP)


def _reputation_numeric_level(rep) -> int | None:
    """Ordinal standing for a ``Reputation`` row: NEUTRAL=0, RECOGNIZED=+1 …
    EXALTED=+8, negatives < 0.  Prefers the model's own ``numeric_level``
    property; returns None if the row's level can't be resolved."""
    try:
        return int(rep.numeric_level)
    except Exception:
        return None


def _good_standing_reputations(player) -> List[Any]:
    """Return the player's ``Reputation`` rows, read through the SAME session
    that owns ``player`` (object_session) — never opening a new session.  Falls
    back to the eager relationship collection only when the object is detached.
    No filtering here beyond fetching the rows; good-standing/level filtering is
    applied by the caller so the threshold logic lives in one place."""
    from src.models.reputation import Reputation

    session = object_session(player)
    if session is not None:
        try:
            return (
                session.query(Reputation)
                .filter(Reputation.player_id == player.id)
                .all()
            )
        except Exception:
            logger.exception(
                "daily-stipend: faction-reputation query failed for player %s; "
                "falling back to relationship collection",
                getattr(player, "id", "?"),
            )
    # Detached / query failed: use the relationship (lazy-loads on the player's
    # own session if attached; a plain list if eagerly loaded).
    return list(getattr(player, "faction_reputations", None) or [])


def apply_daily_rep_stipend_for_player(player, today_str: str) -> int:
    """Credit ``player`` their DAILY guild stipend for the UTC day identified by
    ``today_str`` (an ISO ``YYYY-MM-DD`` string), advancing the durable
    per-player anchor in ``Player.settings`` IN THE SAME SESSION as the credit so
    the grant and its idempotency mark commit (or roll back) together.

    The grant amount is the CAPPED PER-FACTION GUILD SUM computed by
    ``daily_stipend_amount`` (the sum of each good-standing faction's
    level-scaled contribution, clamped to ``GLOBAL_DAILY_STIPEND_CAP``).  The
    faction reputations are read through the player's OWN session (the locked
    row's session) — see daily_stipend_amount; no new session is opened.

    The CALLER (the scheduler's daily sweep) owns the SessionLocal, the advisory
    lock, the per-player ``with_for_update`` re-read, and the per-player commit;
    this helper performs only the in-session read-modify-write on the locked
    ``player`` row and returns the credits granted (0 = nothing to do).  It does
    NOT commit and does NOT flush — the caller commits the row.  SIGNATURE,
    anchor, idempotency, and the active-gate contract are UNCHANGED — only the
    amount computation changed (now the per-faction guild sum, not a single
    personal-rep-tier lookup).

    IDEMPOTENCY (mandatory — this is a credit faucet): the per-player anchor
    ``Player.settings[_DAILY_STIPEND_ANCHOR_KEY]`` holds the UTC date last paid.
    If that anchor is already >= ``today_str`` (lexicographic == chronological
    for ISO dates) the player has already been paid today — return 0 without
    touching credits.  A restart, duplicate wake, or re-run within the same UTC
    day re-reads the anchor and skips → NEVER a double-credit.

    NOTE: this helper does NOT check the active-that-day gate — the caller gates
    on ``User.last_login`` UTC date == today BEFORE calling, so an idle day pays
    0 (the player is simply not a candidate).  A player with NO good-standing
    faction (the common new-player case) earns 0 but is still anchored for today,
    so the cheap path short-circuits on subsequent same-day wakes."""
    settings = player.settings if isinstance(player.settings, dict) else {}
    last_str = settings.get(_DAILY_STIPEND_ANCHOR_KEY)
    if isinstance(last_str, str) and last_str >= today_str:
        return 0  # already paid (or anchored) today — idempotent skip

    amount = daily_stipend_amount(player)
    if amount:
        player.credits = int(player.credits or 0) + amount

    # Advance the durable per-player anchor in the SAME transaction as the
    # credit.  We anchor even when amount == 0 (no good-standing faction) so
    # repeated same-day wakes short-circuit on the cheap lexicographic check
    # above instead of re-querying the player's reputations every wake.
    new_settings = dict(settings)
    new_settings[_DAILY_STIPEND_ANCHOR_KEY] = today_str
    player.settings = new_settings
    flag_modified(player, "settings")
    return amount


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
    """WEEKLY economy faucet — galactic-citizen SUBSCRIPTION PERK ONLY.

    Max's ruling (2026-06-20) SPLIT the old two-faucet weekly job: the rep
    stipend moved to the DAILY active-gated path
    (apply_daily_rep_stipend_for_player, driven by the scheduler's daily sweep),
    and THIS weekly path now pays ONLY the paid citizen perk.  Everything else is
    unchanged — same durable canonical-week anchor, same advisory lock, same
    atomic commit.

    FULLY SYNCHRONOUS, self-gated on a DURABLE canonical-week anchor in
    Galaxy.state.  Pattern mirrors _run_weekly_decay_sync exactly:
      * Own SessionLocal — never uses an AsyncSession or asyncio.run.
      * pg_try_advisory_xact_lock on _WEEKLY_FAUCET_LOCK_KEY — second
        instance skips its tick instead of double-granting.
      * Anchor read + citizen perks + anchor advance in ONE transaction.
      * Any raise rolls back everything; the week retries next wake.

    Returns {citizen_grants, total_credits, week} on success, or {…, week: -1}
    when not due / lock held elsewhere."""
    from src.core.database import SessionLocal
    from src.models.galaxy import Galaxy

    this_week = _canonical_week_number()
    not_due: Dict[str, int] = {
        "citizen_grants": 0,
        "total_credits": 0,
        "week": -1,
    }

    db = SessionLocal()
    try:
        got_lock = db.execute(
            text("SELECT pg_try_advisory_xact_lock(:key)"),
            {"key": _WEEKLY_FAUCET_LOCK_KEY},
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

        # Citizen perk only — the rep stipend is now the DAILY sweep's job.
        # Any raise propagates, rolling back the whole transaction including
        # the anchor advance.
        citizen_credits = _apply_citizen_perks(db, player_ids)

        # Advance the durable anchor in the SAME transaction.
        state = dict(galaxy.state or {})
        state[_FAUCET_STATE_KEY] = this_week
        galaxy.state = state
        flag_modified(galaxy, "state")

        db.commit()  # commits grants + anchor atomically, releases the lock

        result: Dict[str, int] = {
            "citizen_grants": citizen_credits // CITIZEN_WEEKLY_PERK if CITIZEN_WEEKLY_PERK else 0,
            "total_credits": citizen_credits,
            "week": this_week,
        }
        logger.info(
            "economy-faucet (weekly): canonical week %d — citizen_perk=%d cr "
            "over %d citizen(s) [rep stipend now on the DAILY sweep]",
            this_week,
            citizen_credits,
            result["citizen_grants"],
        )
        return result

    except Exception:
        logger.exception("economy-faucet (weekly): batch failed — week not advanced")
        db.rollback()
        return not_due
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Constants manifest — for Max's ratification
# ---------------------------------------------------------------------------

CONSTANTS_FOR_RATIFICATION: Dict[str, Any] = {
    "PER_FACTION_DAILY_BY_LEVEL": PER_FACTION_DAILY_BY_LEVEL,
    "PER_FACTION_DAILY_BY_LEVEL_monthly_equiv_single_faction": {
        level: amount * 30 for (level, amount) in PER_FACTION_DAILY_BY_LEVEL.items()
    },
    "GLOBAL_DAILY_STIPEND_CAP": GLOBAL_DAILY_STIPEND_CAP,
    "GLOBAL_DAILY_STIPEND_CAP_monthly_equiv": GLOBAL_DAILY_STIPEND_CAP * 30,  # ~3,000/mo at cap
    "GOOD_STANDING_MIN_LEVEL": "RECOGNIZED (numeric_level >= 1; "
                               "Reputation.current_value >= 50)",
    "CITIZEN_WEEKLY_PERK": CITIZEN_WEEKLY_PERK,
    "CITIZEN_WEEKLY_PERK_monthly_equiv": CITIZEN_WEEKLY_PERK * 4,  # ~10,000/mo
    "weekly_cadence": "citizen perk: once per canonical week "
                      "(canonical_week_number advances at GAME_TIME_SCALE × "
                      "86400s / 7; on dev at scale 144 this is ~70 wall-clock "
                      "minutes per canonical week)",
    "daily_cadence": "guild stipend: once per UTC DAY per player, gated on the "
                     "player having logged in (User.last_login) THAT UTC day; "
                     "idle day = 0; durable per-player UTC-date anchor in "
                     "Player.settings (no migration)",
    "model": "PER-FACTION GUILD PAYOUT (Max's final ruling 2026-06-20): for each "
             "faction the player is in GOOD STANDING with (standing >= "
             "RECOGNIZED), add PER_FACTION_DAILY_BY_LEVEL[level]; SUM across "
             "factions; grant min(sum, GLOBAL_DAILY_STIPEND_CAP).",
    "notes": (
        "All credit amounts + thresholds are NO-CANON placeholders requiring "
        "Max's ratification. BALANCE CAP satisfied: cap 100 cr/day ⇒ ~3,000/mo, "
        "UNDER the citizen perk's ~10,000/mo even for a player favored by many "
        "factions; a single-faction EXALTED player earns 50 cr/day (~1,500/mo). "
        "Recommended ratification questions: "
        "(1) Is RECOGNIZED (current_value >= 50) the right GOOD-STANDING floor, "
        "or should it be higher (e.g. TRUSTED) / lower (any value > 0)? "
        "(2) Is the GLOBAL_DAILY_STIPEND_CAP of 100 cr/day (~3,000/mo) the right "
        "ceiling below the paid citizen perk (~10,000/mo)? "
        "(3) Are the per-faction-per-level rates (RECOGNIZED 5 … EXALTED 50 "
        "cr/day) appropriately conservative for the number of factions in play? "
        "(4) Is the active-that-day gate (User.last_login UTC date == today) the "
        "right definition of 'active', or should it be turns-spent / actions? "
        "(5) Should the citizen perk vary by subscription tier (Regional_Owner / "
        "Nexus_Premium), or is a flat perk for all is_galactic_citizen players "
        "sufficient for now?"
    ),
}
