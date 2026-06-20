"""Numerical haggling engine — ADR-0079 (Accepted, Max 2026-06-14).

A deterministic 4-round price negotiation between a player and a station's NPC
trader. The agreed per-unit price replaces the normal posted price for that one
buy/sell transaction (still clamped to the commodity's [0.80, 1.20] × fair band).

ADR-0079 math — each numbered point below maps to its implementation:

1. **Counter = midpoint(player_offer, fair_price)** when the offer is outside the
   acceptance band but not a reject — see ``_resolve_offer`` (the COUNTER branch).
2. **Band narrows 20% per round** (round 1 widest → round 4 tightest) —
   ``_round_band_scale`` = ``0.8 ** (round_index - 1)``.
3. **Rank modifier: +1% per rank tier, capped at +12%** — ``_rank_band_factor``.
4. **Rep-tier multipliers:** faction hostile ×1.05 → allied ×0.97; personal
   disliked ×1.05 → trusted ×0.95 — ``_faction_band_factor`` / ``_personal_band_factor``.
5. **Difficulty 1–10 → band-mult ×0.85 (easy) … ×1.25 (hard)**, linear —
   ``_difficulty_band_factor``.
6. **Modifiers adjust the ACCEPTANCE BAND ONLY**, never the perceived fair price.
   The fair price comes straight from ``trading_service`` (trading.md price stack,
   already containing the rank/rep modifiers) — we do NOT re-apply them to it here.
7. **Session:** after a non-reject close a 5-min re-entry COOLDOWN applies; a
   REJECT hard-locks that commodity for the docking session. Reputation deltas = 0.
   Final realized price clamped to [0.80, 1.20] × fair.

Session/lock/cooldown state lives in ``Player.settings["haggle"]`` (JSONB — no
migration). Per-(station, player) memory + trust lives in
``Station.trader_personality["player_memory"][player_id]`` (JSONB — no migration).
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, UTC
from typing import Any, Dict, Optional, Tuple

from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from src.core import trader_personalities as tp
from src.models.player import Player
from src.models.station import Station

logger = logging.getLogger(__name__)


# ── ADR-0079 constants ───────────────────────────────────────────────────────
MAX_ROUNDS = 4                       # point: 4-round limit
ROUND_NARROW_PER_ROUND = 0.8         # point 2: band narrows 20% / round
PRICE_CLAMP_LO = 0.80                # point 7: final realized price floor
PRICE_CLAMP_HI = 1.20                # point 7: final realized price ceiling

# Base acceptance half-band, expressed as a fraction of fair price. haggling.md's
# round-1 bands are: BUY accept at >= fair*0.97 (a 3% distance), reject below
# fair*0.80 (a 20% distance). The "accept" half-band (how close to fair an offer
# must be to be accepted on a wide round) is 3% of fair; the "reject" half-band
# (beyond which the offer is dismissed) is 20% of fair. The COUNTER zone sits
# between them. These are the round-1 (widest) values; round-narrowing and the
# modifiers scale them.
BASE_ACCEPT_HALF_BAND = 0.03         # haggling.md: BUY >= fair*0.97 / SELL <= fair*1.03
BASE_REJECT_HALF_BAND = 0.20         # haggling.md: BUY < fair*0.80 / SELL > fair*1.20 = reject

# Point 3: rank modifier — +1% per rank tier, capped at +12%.
RANK_PCT_PER_TIER = 0.01
RANK_PCT_CAP = 0.12

# Point 5: difficulty 1..10 → band-multiplier 0.85..1.25 (linear).
DIFFICULTY_BAND_MIN = 0.85           # difficulty 1 (easy / generous → wider band for player)
DIFFICULTY_BAND_MAX = 1.25           # difficulty 10 (hard / tight)

# Point 4: faction-rep band multiplier endpoints (hostile ×1.05 → allied ×0.97).
FACTION_BAND_HOSTILE = 1.05
FACTION_BAND_ALLIED = 0.97
# Personal-rep band multiplier endpoints (disliked ×1.05 → trusted ×0.95).
PERSONAL_BAND_DISLIKED = 1.05
PERSONAL_BAND_TRUSTED = 0.95

# Point 7: re-entry cooldown after a non-reject close. ADR-0079 says "draft 5 min"
# — confirmed at 5 min (NO-CANON micro-confirm, flagged to the orchestrator).
REENTRY_COOLDOWN_SECONDS = 5 * 60

# Per-player haggle MEMORY horizon. Max ruled (sw2102-docs/DECISIONS.md
# "haggling-personality-reconciliation", Decided 2026-06-20): the numerical-mode
# per-player memory contract is 90 days UNIFORM, regardless of the per-archetype
# ``memory_duration_days`` (Federation 30 / Frontier 14 / Black Market 7). That
# archetype field stays on the personality for the narrative-mode embedding window
# only; per-player memory pruning uses this uniform value.
HAGGLE_MEMORY_DAYS = 90

# ── Orange-Cat Society leniency (WO-CG: PUBLISHED +15%, EXEMPT from the cap) ───
# Holders of the Orange Cat Society badge (medal special.orange_cat_society) get a
# more lenient NPC. FINAL spec (medal-effects-spec.md:251, Max ruling): the
# PUBLISHED +15% haggle ease, EXEMPT from the +0.08 medal cap, applied through
# THIS dedicated lever (NOT the capped generic get_active_medal_bonuses fold — its
# catalog effect is kind "special" precisely so the generic folder never
# double-applies it). A +15% band ease = a band multiplier of 0.85 (M < 1.0 =
# EASIER; band width shrinks 15%). Trivially tunable here.
ORANGE_CAT_MEDAL_ID = "special.orange_cat_society"
ORANGE_CAT_BAND_FACTOR = 0.85

# ── Trust accrual (Max #7 step D) ────────────────────────────────────────────
# trust_level lives on the [-1000, 1000] scale (jsonb-schema). Successful trades
# raise it, failed haggling (a REJECT close) erodes it; high trust eases the band.
# Magnitudes are NO-CANON micro-numbers (jsonb-schema only says "accumulated via
# repeated successful trades, eroded by failed haggling"); chosen conservative so
# the [-1000,1000] band takes ~tens of interactions to traverse. Flagged.
TRUST_ON_ACCEPT = 15
TRUST_ON_TIMEOUT = 3                 # a 4-round non-reject close still ends amicably
TRUST_ON_REJECT = -10
# High trust eases the band. Maps trust in [-1000, 1000] linearly to a band
# multiplier in [TRUST_EASE_MAX (distrust → harder) .. TRUST_EASE_MIN (trust →
# easier)]. Endpoints NO-CANON (jsonb only says "high trust eases difficulty").
TRUST_BAND_AT_MIN_TRUST = 1.05       # trust -1000 → +5% harder
TRUST_BAND_AT_MAX_TRUST = 0.95       # trust +1000 → -5% easier


class HaggleError(Exception):
    """Raised for client-correctable haggle errors (mapped to HTTP 400 by the route)."""


# ──────────────────────────────────────────────────────────────────────────────
# Seeding backfill (WO-BO step B) — run at startup
# ──────────────────────────────────────────────────────────────────────────────
def seed_trader_personalities(db: Session, batch_size: int = 500) -> Dict[str, int]:
    """Backfill archetype-driven trader personalities onto existing stations.

    100% of live stations carry the BORDER model-default (ADR-0079 flagged this:
    archetype difficulty is a no-op until a seed pass derives real personalities
    keyed off the station class). This idempotent pass rewrites only stations whose
    personality is a no-op default or carries the legacy shape, deriving the
    class-correct archetype and preserving any real per-player haggle memory/trust.

    Returns ``{"scanned": N, "reseeded": M}``. Safe to run on every boot (rows
    already class-correct + canonical-shape are skipped). Commits in batches.

    Idempotent + boot-safe: any per-row failure is logged and skipped; the pass
    never raises so a seeding hiccup can't crash startup.
    """
    scanned = 0
    reseeded = 0
    try:
        # Materialize all station IDs up front, then re-query each batch fresh.
        # Do NOT iterate a yield_per() server-side cursor while committing per
        # batch — committing mid-iteration invalidates the psycopg2 named cursor
        # ("named cursor isn't valid anymore") and the loop dies after batch 1,
        # leaving later stations un-reconciled. IDs in memory are cheap.
        station_ids = [row[0] for row in db.query(Station.id).all()]
        for start in range(0, len(station_ids), batch_size):
            batch_ids = station_ids[start:start + batch_size]
            stations = db.query(Station).filter(Station.id.in_(batch_ids)).all()
            pending = 0
            for station in stations:
                scanned += 1
                try:
                    class_value = (
                        station.station_class.value
                        if station.station_class is not None
                        else 5
                    )
                    if tp.needs_reseed(station.trader_personality, class_value):
                        station.trader_personality = tp.reseed_personality(
                            station.trader_personality, class_value
                        )
                        flag_modified(station, "trader_personality")
                        reseeded += 1
                        pending += 1
                except Exception:
                    logger.warning(
                        "trader-personality reseed failed for station %s",
                        getattr(station, "id", "?"),
                        exc_info=True,
                    )
            if pending:
                db.commit()
    except Exception:
        db.rollback()
        logger.error("trader-personality seeding pass failed", exc_info=True)
    logger.info(
        "trader-personality seeding: scanned=%d reseeded=%d", scanned, reseeded
    )
    return {"scanned": scanned, "reseeded": reseeded}


# ──────────────────────────────────────────────────────────────────────────────
# Band-modifier helpers (point 3/4/5 — all adjust the BAND, never the fair price)
# ──────────────────────────────────────────────────────────────────────────────
def _round_band_scale(round_index: int) -> float:
    """Point 2: 0.8 ** (round-1). Round 1 = 1.0 (widest); round 4 = 0.512 (tightest)."""
    r = max(1, min(MAX_ROUNDS, int(round_index)))
    return ROUND_NARROW_PER_ROUND ** (r - 1)


def _difficulty_band_factor(haggling_difficulty: int) -> float:
    """Point 5: difficulty 1..10 → 0.85..1.25, linear."""
    d = max(tp.DIFFICULTY_MIN, min(tp.DIFFICULTY_MAX, int(haggling_difficulty)))
    t = (d - tp.DIFFICULTY_MIN) / (tp.DIFFICULTY_MAX - tp.DIFFICULTY_MIN)  # 0..1
    return DIFFICULTY_BAND_MIN + t * (DIFFICULTY_BAND_MAX - DIFFICULTY_BAND_MIN)


def _rank_band_factor(player: Player) -> float:
    """Point 3: +1% per rank tier, capped at +12%. Higher rank → tighter band
    (the station expects a shrewder offer — cuts both ways). Returns a multiplier
    >= 1.0 that NARROWS the band (a tighter band is a smaller half-band, so we
    DIVIDE the band by this — see _compute_band_half_widths)."""
    try:
        from src.services.ranking_service import RANK_DEFINITIONS, LEGACY_RANK_MAP

        name = LEGACY_RANK_MAP.get(player.military_rank, player.military_rank)
        level = 0
        for rd in RANK_DEFINITIONS:
            if rd["name"] == name:
                level = int(rd.get("level", 0))
                break
        pct = min(RANK_PCT_CAP, level * RANK_PCT_PER_TIER)
        return 1.0 + pct
    except Exception:
        logger.warning("haggle rank-band factor failed; using neutral", exc_info=True)
        return 1.0


def _faction_band_factor(db: Session, player: Player, station: Station) -> float:
    """Point 4: faction-rep band multiplier, hostile ×1.05 → allied ×0.97 linear
    across [-1000, 1000]. Wider band (better deal reach) for good standing means a
    multiplier < 1.0 widens via division — but ADR phrases it as "rep multipliers"
    on the band; we apply hostile=harder(×1.05), allied=easier(×0.97) DIRECTLY as a
    band-width multiplier (>1.0 = harder/narrower). Defensive → neutral on failure."""
    try:
        faction_name = getattr(station, "faction_affiliation", None)
        if not faction_name:
            return 1.0
        from src.models.faction import Faction
        from src.models.reputation import Reputation

        faction = db.query(Faction).filter(Faction.name == faction_name).first()
        if faction is None:
            return 1.0
        rep = (
            db.query(Reputation)
            .filter(Reputation.player_id == player.id, Reputation.faction_id == faction.id)
            .first()
        )
        if rep is None:
            return 1.0
        value = max(-1000, min(1000, int(rep.current_value)))
        return _lerp_by_value(value, FACTION_BAND_HOSTILE, FACTION_BAND_ALLIED)
    except Exception:
        logger.warning("haggle faction-band factor failed; using neutral", exc_info=True)
        return 1.0


def _personal_band_factor(player: Player) -> float:
    """Point 4: personal-rep band multiplier, disliked ×1.05 → trusted ×0.95
    linear across the [-1000, 1000] personal-reputation score scale. Reads the
    player's numeric personal reputation; defensive → neutral on failure."""
    try:
        # Player.personal_reputation is the numeric [-1000, 1000] personal score
        # (Player.reputation is a JSONB of FACTION reputations — not this).
        score = getattr(player, "personal_reputation", None)
        if score is None:
            # Fall back to the cached tier-name midpoints if the numeric score is
            # absent (matches personal_reputation_service.REPUTATION_TIERS bands).
            tier = getattr(player, "reputation_tier", "Neutral") or "Neutral"
            score = {
                "Villain": -875, "Criminal": -625, "Outlaw": -375,
                "Suspicious": -125, "Neutral": 0,
                "Lawful": 125, "Heroic": 375, "Legendary": 750,
            }.get(tier, 0)
        score = max(-1000, min(1000, int(score)))
        return _lerp_by_value(score, PERSONAL_BAND_DISLIKED, PERSONAL_BAND_TRUSTED)
    except Exception:
        logger.warning("haggle personal-band factor failed; using neutral", exc_info=True)
        return 1.0


def _trust_band_factor(trust_level: int) -> float:
    """Step D: high trust eases the band. trust [-1000,1000] → [1.05 .. 0.95]."""
    t = max(tp.TRUST_MIN, min(tp.TRUST_MAX, int(trust_level)))
    return _lerp_by_value(t, TRUST_BAND_AT_MIN_TRUST, TRUST_BAND_AT_MAX_TRUST)


def _lerp_by_value(value: int, at_min: float, at_max: float) -> float:
    """Linear-interpolate ``at_min`` (value=-1000) → ``at_max`` (value=+1000)."""
    t = (value - (-1000)) / 2000.0  # 0..1 across [-1000, 1000]
    t = max(0.0, min(1.0, t))
    return at_min + t * (at_max - at_min)


def _orange_cat_band_factor(db: Session, player: Player) -> float:
    """Orange-Cat Society leniency (NO-CANON, proposed ×0.95). Returns 1.0 if the
    player does not hold the badge. Defensive → neutral on any lookup failure."""
    try:
        from src.models.medal import PlayerMedal

        held = (
            db.query(PlayerMedal.id)
            .filter(
                PlayerMedal.player_id == player.id,
                PlayerMedal.medal_id == ORANGE_CAT_MEDAL_ID,
            )
            .first()
        )
        return ORANGE_CAT_BAND_FACTOR if held is not None else 1.0
    except Exception:
        logger.warning("orange-cat band factor lookup failed; using neutral", exc_info=True)
        return 1.0


def _medal_band_factor(db: Session, player: Player) -> float:
    """WO-CG — the generic, capped medal ``haggle_band`` ease folded into the band
    multiplier (Ambassador's Star, Peacemaker, Honorary Tabby, …).

    ``get_active_medal_bonuses`` returns a per-hook EASE magnitude (positive =
    more lenient), summed across the holder's passive haggle medals and clamped
    to the blessed +0.08 cap. The band axis is "higher = harder, lower = easier",
    so an ease of ``b`` maps to a factor of ``1.0 - b`` (e.g. +0.05 ease → ×0.95).

    Orange Cat is EXEMPT and handled by :func:`_orange_cat_band_factor` (its
    catalog effect kind is "special", so it is NOT in this summed bonus) — the two
    factors multiply independently, never double-applying. Returns 1.0 (neutral)
    if the player holds no haggle medal or on any lookup failure (defensive)."""
    try:
        from src.services.medal_service import get_active_medal_bonuses

        bonuses = get_active_medal_bonuses(db, player.id) or {}
        ease = float(bonuses.get("haggle_band", 0.0) or 0.0)
        ease = max(0.0, ease)  # ease never tightens the band
        return max(0.01, 1.0 - ease)
    except Exception:
        logger.warning("medal haggle-band factor lookup failed; using neutral", exc_info=True)
        return 1.0


# ──────────────────────────────────────────────────────────────────────────────
# Core band computation
# ──────────────────────────────────────────────────────────────────────────────
def _aggregate_band_multiplier(
    db: Session, player: Player, station: Station, personality: Dict[str, Any]
) -> float:
    """Compose every band-width modifier (points 3/4/5 + trust + Orange-Cat).

    Returns a single multiplier ``M`` applied to the base half-band widths.
    M > 1.0 ⇒ a HARDER negotiation (narrower accept zone / closer reject line);
    M < 1.0 ⇒ EASIER. None of these touch the fair price (point 6).

    NOTE: rank tightens the band (M up), so the rank factor (>= 1.0) multiplies in.
    Difficulty/faction/personal/trust/orange-cat are all expressed on the same
    "higher = harder" axis, so they all multiply together.
    """
    difficulty = personality.get("haggling_difficulty", 5)
    trust = personality.get("trust_level", 0)
    m = 1.0
    m *= _difficulty_band_factor(difficulty)
    m *= _rank_band_factor(player)
    m *= _faction_band_factor(db, player, station)
    m *= _personal_band_factor(player)
    m *= _trust_band_factor(trust)
    m *= _orange_cat_band_factor(db, player)
    # WO-CG: generic capped medal haggle_band ease (excludes Orange Cat, which is
    # the dedicated cap-exempt lever above). The two multiply independently.
    m *= _medal_band_factor(db, player)
    return m


def _compute_band(
    fair_price: float,
    side: str,
    round_index: int,
    band_multiplier: float,
) -> Dict[str, float]:
    """Compute the round's accept / counter / reject thresholds around fair_price.

    Combines: round-narrowing (point 2) × the aggregate band multiplier (points
    3/4/5 + trust + cat). A harder band (multiplier > 1.0) makes the ACCEPT zone
    SMALLER (player must come closer to fair) and the REJECT line CLOSER to fair.

    Implementation: the *accept half-band* shrinks as the band tightens (divide by
    M), and the *reject half-band* also shrinks (divide by M) — a tighter trader
    both demands closer offers AND dismisses bad offers sooner. Round-narrowing
    scales BOTH half-bands by 0.8**(round-1).

    For a BUY (player buying, lower offer = better for player):
        accept  : offer >= fair * (1 - accept_half)
        counter : fair * (1 - reject_half) <= offer < fair * (1 - accept_half)
        reject  : offer < fair * (1 - reject_half)
    For a SELL (player selling, higher offer = better for player) the bands mirror
    above fair price.
    """
    scale = _round_band_scale(round_index)
    m = max(0.01, band_multiplier)
    accept_half = (BASE_ACCEPT_HALF_BAND * scale) / m
    reject_half = (BASE_REJECT_HALF_BAND * scale) / m
    # Guard: accept zone must sit inside the reject zone.
    accept_half = min(accept_half, reject_half)

    if side == "buy":
        return {
            "accept_threshold": fair_price * (1.0 - accept_half),
            "reject_threshold": fair_price * (1.0 - reject_half),
            "accept_half": accept_half,
            "reject_half": reject_half,
        }
    else:  # sell
        return {
            "accept_threshold": fair_price * (1.0 + accept_half),
            "reject_threshold": fair_price * (1.0 + reject_half),
            "accept_half": accept_half,
            "reject_half": reject_half,
        }


def _resolve_offer(
    offer: float, fair_price: float, side: str, band: Dict[str, float]
) -> Tuple[str, Optional[float]]:
    """Apply the band table to one offer. Returns (verdict, counter_price|None).

    verdict ∈ {"accept", "counter", "reject"}.
    Point 1: a COUNTER is the midpoint between the player's offer and fair price.
    """
    if side == "buy":
        # Player wants to pay LESS. accept_threshold < fair; reject_threshold lower.
        if offer >= band["accept_threshold"]:
            return "accept", None
        if offer >= band["reject_threshold"]:
            counter = (offer + fair_price) / 2.0  # point 1: split-the-difference
            return "counter", counter
        return "reject", None
    else:  # sell — player wants to be paid MORE. accept_threshold > fair.
        if offer <= band["accept_threshold"]:
            return "accept", None
        if offer <= band["reject_threshold"]:
            counter = (offer + fair_price) / 2.0  # point 1
            return "counter", counter
        return "reject", None


def _clamp_realized(price: float, fair_price: float) -> float:
    """Point 7: final realized price clamped to [0.80, 1.20] × fair."""
    lo = fair_price * PRICE_CLAMP_LO
    hi = fair_price * PRICE_CLAMP_HI
    return max(lo, min(hi, price))


# ──────────────────────────────────────────────────────────────────────────────
# Fair-price source (point 6 — comes from trading.md stack, NOT recomputed here)
# ──────────────────────────────────────────────────────────────────────────────
def _fair_price(
    db: Session, player: Player, station: Station, commodity: str, side: str
) -> float:
    """The POSTED per-unit price this player would pay/receive un-haggled.

    ADR-0079 point 6 + haggling.md:13/:70: the haggle outcome MULTIPLIES the
    POSTED price. The posted price is the full trading.md stack — supply/demand
    base × rank discount × faction/personal-rep × region tariff × station lever,
    clamped to the commodity hard band — i.e. the EXACT price the buy/sell route
    would charge THIS player un-haggled. We negotiate off that, so a rep/rank-
    discounted player who haggles never ends up worse than not haggling.

    Point 6's "modifiers adjust the band, not the price" means the engine's BAND
    modifiers (rank / faction / personal / difficulty) are NOT re-baked into the
    price (that re-application is what made a discounted player pay MORE) — it
    does NOT mean discard the trading.md posted-price stack, which IS the price.

    Reuses ``routes.trading.compute_effective_unit_price`` so haggle fair_price
    is byte-for-byte the route's posted price. For a BUY the player negotiates
    down from the station's SELL price (what it charges); for a SELL up from the
    station's BUY price (what it pays). Defensive: degrades to the raw dynamic
    price on any failure so a hiccup can never block opening a session.
    """
    from src.services.trading_service import TradingService

    ts = TradingService(db)
    txn_type = "sell" if side == "buy" else "buy"
    base = ts.calculate_dynamic_price(station, commodity, txn_type)
    base = int(max(1, base))
    try:
        from src.api.routes.trading import compute_effective_unit_price

        posted = compute_effective_unit_price(db, player, station, commodity, side, base)
        return float(max(1, posted))
    except Exception:
        logger.warning(
            "haggle posted-price computation failed; using raw dynamic price",
            exc_info=True,
        )
        return float(base)


# ──────────────────────────────────────────────────────────────────────────────
# Session state (Player.settings["haggle"]) — point 7 cooldown + reject lock
# ──────────────────────────────────────────────────────────────────────────────
def _now() -> datetime:
    return datetime.now(UTC)


def _session_key(station_id: Any, commodity: str, side: str) -> str:
    return f"{station_id}:{commodity}:{side}"


def _get_haggle_state(player: Player) -> Dict[str, Any]:
    settings = player.settings or {}
    state = settings.get("haggle")
    if not isinstance(state, dict):
        state = {"sessions": {}, "locks": {}, "cooldowns": {}}
    state.setdefault("sessions", {})
    state.setdefault("locks", {})       # commodity-locked-for-docking-session (reject)
    state.setdefault("cooldowns", {})   # ISO timestamp until re-entry allowed
    return state


def _save_haggle_state(player: Player, state: Dict[str, Any]) -> None:
    settings = player.settings or {}
    settings["haggle"] = state
    player.settings = settings
    flag_modified(player, "settings")


def clear_docking_session_haggles(player: Player) -> None:
    """Undock hook: a REJECT lock is for "the docking session", so clear locks
    and in-flight sessions on undock. Cooldowns are real-time and persist."""
    state = _get_haggle_state(player)
    state["sessions"] = {}
    state["locks"] = {}
    _save_haggle_state(player, state)


# ──────────────────────────────────────────────────────────────────────────────
# Memory + trust (Station.trader_personality["player_memory"][player_id])
# ──────────────────────────────────────────────────────────────────────────────
def _prune_expired_memory(personality: Dict[str, Any]) -> None:
    """Drop per-player memory entries older than the UNIFORM 90-day horizon.

    Max ruled (DECISIONS.md haggling-personality-reconciliation, Decided
    2026-06-20): the numerical-mode per-player memory contract is 90 days
    UNIFORM. The per-archetype ``memory_duration_days`` (Federation 30 /
    Frontier 14 / Black Market 7) governs the narrative-mode embedding window
    ONLY — it must NOT prune per-player numerical memory early. So we prune at
    HAGGLE_MEMORY_DAYS regardless of the archetype field."""
    mem = personality.get("player_memory")
    if not isinstance(mem, dict) or not mem:
        return
    cutoff = _now() - timedelta(days=HAGGLE_MEMORY_DAYS)
    drop = []
    for pid, entry in mem.items():
        try:
            last = entry.get("last_seen_at")
            if last and datetime.fromisoformat(last) < cutoff:
                drop.append(pid)
        except Exception:
            continue
    for pid in drop:
        mem.pop(pid, None)


def _record_memory(
    station: Station,
    personality: Dict[str, Any],
    player_id: str,
    outcome: str,
    commodity: str,
    realized_price: Optional[float],
) -> None:
    """Update per-(station, player) memory + trust after a session close.

    outcome ∈ {"accept", "timeout", "reject"}. Mutates personality in place and
    flags the column dirty on the station so SQLAlchemy persists the JSONB."""
    mem = personality.setdefault("player_memory", {})
    entry = mem.get(player_id) or {
        "session_count": 0,
        "accepted_count": 0,
        "rejected_count": 0,
        "last_outcome": None,
        "last_commodity": None,
        "last_seen_at": None,
    }
    entry["session_count"] = int(entry.get("session_count", 0)) + 1
    if outcome == "accept":
        entry["accepted_count"] = int(entry.get("accepted_count", 0)) + 1
    elif outcome == "reject":
        entry["rejected_count"] = int(entry.get("rejected_count", 0)) + 1
    entry["last_outcome"] = outcome
    entry["last_commodity"] = commodity
    entry["last_seen_at"] = _now().isoformat()
    mem[player_id] = entry

    # Trust accrual (step D) — per-player, but jsonb-schema stores trust_level at
    # the personality level as the station's standing toward "a given player".
    # We keep a per-player trust under the memory entry AND mirror the most-recent
    # actor's value into the documented trust_level field so reads see real trust.
    delta = {
        "accept": TRUST_ON_ACCEPT,
        "timeout": TRUST_ON_TIMEOUT,
        "reject": TRUST_ON_REJECT,
    }.get(outcome, 0)
    per_player_trust = int(entry.get("trust", 0)) + delta
    per_player_trust = max(tp.TRUST_MIN, min(tp.TRUST_MAX, per_player_trust))
    entry["trust"] = per_player_trust
    personality["trust_level"] = per_player_trust  # documented field reflects this player

    _prune_expired_memory(personality)
    station.trader_personality = personality
    flag_modified(station, "trader_personality")


def _player_trust(personality: Dict[str, Any], player_id: str) -> int:
    """Return the station's stored trust toward this specific player (0 if none)."""
    mem = personality.get("player_memory") or {}
    entry = mem.get(player_id) or {}
    try:
        return int(entry.get("trust", 0))
    except (TypeError, ValueError):
        return 0


# ──────────────────────────────────────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────────────────────────────────────
class HaggleService:
    """Numerical haggling engine. One instance per request (holds the db session)."""

    def __init__(self, db: Session):
        self.db = db

    # -- open -----------------------------------------------------------------
    def open_session(
        self, player: Player, station: Station, commodity: str, side: str, quantity: int
    ) -> Dict[str, Any]:
        """Open a numerical haggle session for (station, commodity, side, quantity).

        Enforces the reject-lock (point 7) and the re-entry cooldown (point 7).
        Raises HaggleError on a locked commodity, an active cooldown, or an invalid
        commodity/side. Returns the opening session card (round 1 expectations)."""
        side = side.lower()
        if side not in ("buy", "sell"):
            raise HaggleError("side must be 'buy' or 'sell'")
        if quantity <= 0:
            raise HaggleError("quantity must be positive")

        commodities = station.commodities or {}
        cfg = commodities.get(commodity)
        if cfg is None:
            raise HaggleError("station does not trade this commodity")
        if side == "buy" and not cfg.get("sells", False):
            raise HaggleError("station does not sell this commodity")
        if side == "sell" and not cfg.get("buys", False):
            raise HaggleError("station does not buy this commodity")

        key = _session_key(station.id, commodity, side)
        state = _get_haggle_state(player)

        # Reject-lock: a prior reject hard-locks this commodity for the session.
        if state["locks"].get(f"{station.id}:{commodity}"):
            raise HaggleError(
                "this commodity is locked for the rest of this docking session "
                "(a prior offer was rejected)"
            )

        # Re-entry cooldown after a non-reject close.
        cd = state["cooldowns"].get(key)
        if cd:
            try:
                until = datetime.fromisoformat(cd)
                if _now() < until:
                    remaining = int((until - _now()).total_seconds())
                    raise HaggleError(
                        f"haggling for this commodity is on cooldown for {remaining}s"
                    )
            except HaggleError:
                raise
            except Exception:
                pass  # malformed cooldown → ignore

        personality = tp.normalize_personality(station.trader_personality)
        fair = _fair_price(self.db, player, station, commodity, side)
        band_mult = _aggregate_band_multiplier(self.db, player, station, personality)
        band = _compute_band(fair, side, 1, band_mult)

        session = {
            "key": key,
            "station_id": str(station.id),
            "commodity": commodity,
            "side": side,
            "quantity": int(quantity),
            "round": 1,
            "fair_price": fair,
            "band_multiplier": band_mult,
            "status": "open",
            "agreed_price": None,
            "opened_at": _now().isoformat(),
        }
        state["sessions"][key] = session
        _save_haggle_state(player, state)

        return self._card(session, band, personality)

    # -- offer ----------------------------------------------------------------
    def submit_offer(
        self, player: Player, station: Station, commodity: str, side: str, offer: float
    ) -> Dict[str, Any]:
        """Submit a per-unit offer for the open session. Returns the round result.

        Resolves the offer against the current round's band (accept/counter/reject),
        advances or closes the session, and on a close applies cooldown / reject-lock
        + records memory & trust. On accept the agreed (clamped) price is stored on
        the session for the trade to consume."""
        side = side.lower()
        key = _session_key(station.id, commodity, side)
        state = _get_haggle_state(player)
        session = state["sessions"].get(key)
        if not session or session.get("status") != "open":
            raise HaggleError("no open haggle session for this commodity — open one first")

        try:
            offer = float(offer)
        except (TypeError, ValueError):
            raise HaggleError("offer must be a number")
        if offer <= 0:
            raise HaggleError("offer must be positive")

        personality = tp.normalize_personality(station.trader_personality)
        fair = float(session["fair_price"])
        round_index = int(session["round"])
        band_mult = float(session["band_multiplier"])
        band = _compute_band(fair, side, round_index, band_mult)

        verdict, counter = _resolve_offer(offer, fair, side, band)
        pid = str(player.id)

        if verdict == "accept":
            agreed = _clamp_realized(offer, fair)
            session["status"] = "accepted"
            session["agreed_price"] = agreed
            self._set_cooldown(state, key)             # point 7: non-reject → cooldown
            _record_memory(station, personality, pid, "accept", commodity, agreed)
            _save_haggle_state(player, state)
            return self._result(session, "accept", round_index, agreed_price=agreed)

        if verdict == "reject":
            session["status"] = "rejected"
            # point 7: reject HARD-LOCKS the commodity for the docking session.
            state["locks"][f"{station.id}:{commodity}"] = True
            # A reject does NOT consume the round count toward the 4-round limit
            # per haggling.md ("player loses one round but session continues")?
            # ADR-0079 point 7 says a reject hard-locks the commodity and ends the
            # session here (the lock makes continuing impossible). We close on reject.
            _record_memory(station, personality, pid, "reject", commodity, None)
            _save_haggle_state(player, state)
            return self._result(session, "reject", round_index)

        # counter — advance the round, or time out at the 4-round limit.
        clamped_counter = _clamp_realized(float(counter), fair)
        if round_index >= MAX_ROUNDS:
            # 4 rounds elapsed without acceptance → timeout close. Point 7: a
            # timeout is a NON-REJECT close → cooldown applies, no lock.
            session["status"] = "closed"
            session["agreed_price"] = None
            self._set_cooldown(state, key)
            _record_memory(station, personality, pid, "timeout", commodity, None)
            _save_haggle_state(player, state)
            return self._result(
                session, "timeout", round_index, counter_price=clamped_counter
            )

        session["round"] = round_index + 1
        next_band = _compute_band(fair, side, session["round"], band_mult)
        _save_haggle_state(player, state)
        result = self._result(
            session, "counter", round_index, counter_price=clamped_counter
        )
        result["next_round"] = session["round"]
        result["next_band"] = self._band_view(next_band, side, fair)
        return result

    # -- status ---------------------------------------------------------------
    def get_status(
        self, player: Player, station: Station, commodity: str, side: str
    ) -> Dict[str, Any]:
        """Return the current session/lock/cooldown status for a commodity/side."""
        side = side.lower()
        key = _session_key(station.id, commodity, side)
        state = _get_haggle_state(player)
        session = state["sessions"].get(key)
        lock = bool(state["locks"].get(f"{station.id}:{commodity}"))
        cooldown_until = state["cooldowns"].get(key)
        cooldown_remaining = 0
        if cooldown_until:
            try:
                until = datetime.fromisoformat(cooldown_until)
                cooldown_remaining = max(0, int((until - _now()).total_seconds()))
            except Exception:
                cooldown_remaining = 0
        return {
            "commodity": commodity,
            "side": side,
            "locked": lock,
            "cooldown_remaining_seconds": cooldown_remaining,
            "session": {
                "status": session.get("status") if session else None,
                "round": session.get("round") if session else None,
                "max_rounds": MAX_ROUNDS,
                "agreed_price": session.get("agreed_price") if session else None,
            }
            if session
            else None,
        }

    # -- trade integration ----------------------------------------------------
    def consume_agreed_price(
        self, player: Player, station_id: Any, commodity: str, side: str
    ) -> Optional[float]:
        """Return + clear the agreed per-unit price for a completed accept, if any.

        Called by the buy/sell route: if the player just accepted a haggle for this
        (station, commodity, side), that per-unit price replaces the posted price
        for THIS transaction (single-use). Returns None when no agreed price exists
        (the trade then uses the normal posted-price path). The agreed price is
        consumed so it can't be reused across multiple trades from one negotiation."""
        side = side.lower()
        key = _session_key(station_id, commodity, side)
        state = _get_haggle_state(player)
        session = state["sessions"].get(key)
        if not session or session.get("status") != "accepted":
            return None
        agreed = session.get("agreed_price")
        if agreed is None:
            return None
        # Single-use: mark consumed so a second trade falls back to posted price.
        session["status"] = "consumed"
        session["agreed_price"] = None
        _save_haggle_state(player, state)
        try:
            return float(agreed)
        except (TypeError, ValueError):
            return None

    # -- helpers --------------------------------------------------------------
    def _set_cooldown(self, state: Dict[str, Any], key: str) -> None:
        until = _now() + timedelta(seconds=REENTRY_COOLDOWN_SECONDS)
        state["cooldowns"][key] = until.isoformat()

    def _band_view(self, band: Dict[str, float], side: str, fair: float) -> Dict[str, Any]:
        return {
            "fair_price": round(fair, 2),
            "accept_threshold": round(band["accept_threshold"], 2),
            "reject_threshold": round(band["reject_threshold"], 2),
            "side": side,
        }

    def _card(
        self, session: Dict[str, Any], band: Dict[str, float], personality: Dict[str, Any]
    ) -> Dict[str, Any]:
        return {
            "status": "open",
            "commodity": session["commodity"],
            "side": session["side"],
            "quantity": session["quantity"],
            "round": session["round"],
            "max_rounds": MAX_ROUNDS,
            "personality_type": personality.get("type"),
            "haggling_difficulty": personality.get("haggling_difficulty"),
            "band": self._band_view(band, session["side"], session["fair_price"]),
            "price_clamp": {
                "min": round(session["fair_price"] * PRICE_CLAMP_LO, 2),
                "max": round(session["fair_price"] * PRICE_CLAMP_HI, 2),
            },
        }

    def _result(
        self,
        session: Dict[str, Any],
        verdict: str,
        round_index: int,
        agreed_price: Optional[float] = None,
        counter_price: Optional[float] = None,
    ) -> Dict[str, Any]:
        out = {
            "verdict": verdict,
            "round": round_index,
            "max_rounds": MAX_ROUNDS,
            "commodity": session["commodity"],
            "side": session["side"],
            "status": session["status"],
            "fair_price": round(float(session["fair_price"]), 2),
        }
        if agreed_price is not None:
            out["agreed_price"] = round(agreed_price, 2)
        if counter_price is not None:
            out["counter_price"] = round(counter_price, 2)
        return out
