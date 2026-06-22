"""
Emergent Reputation Dispatcher (ADR-0032 — F3 Emergent Faction Reputation
Action Set).

The canonical faction-reputation loop is *emergent-only*: reputation moves
through natural play (combat, trade, exploration, social acts, resource flows)
and never through accepting a mission. ADR-0032 specifies a single server-side
dispatch point — ``apply_emergent_action(player, action, context)`` — that:

  1. looks the action up in the canon trigger table,
  2. fans out to the canon per-faction faction-rep deltas in ONE transaction,
  3. emits the rivalry-cascade fractional negative on positive deltas, and
  4. (future) applies the per-(player, faction) daily throttle.

This module is the dispatcher. It does NOT reinvent the rep mutation: it reuses
``faction_service.apply_faction_rep_delta`` (the proven sync, flush-only,
caller-owns-commit faction-rep primitive) for every delta it applies. The
dispatcher is faction-reputation only; *personal* reputation (the disjoint
signal per ADR-0056 N-D1, mutated by ``PersonalReputationService``) is NOT
touched here and is NOT cross-fed.

Scope of the LIVE table (deliberately narrow — see the module-level
``EMERGENT_ACTIONS`` dict and the WO report): only canon-backed actions whose
trigger sites can be wired WITHOUT duplicating an existing rep change are
included. Actions whose magnitudes are NO-CANON, or whose anti-symmetric /
sector-influence weighting depends on a 📐 Design-only surface that is not yet
implemented (``SectorFactionInfluence``), are intentionally omitted from the
live table and flagged in the report rather than guessed.

Transaction model: every method here is SYNC and FLUSH-ONLY (it delegates to
``apply_faction_rep_delta``, which flushes and never commits). The CALLER owns
the commit — exactly like the existing police-kill faction-rep hook in
``combat_service``. Every public entry point is defensive: a rep hiccup is
logged and swallowed, never raised into the calling gameplay path.
"""

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from src.models.faction import Faction, FactionType
from src.models.player import Player
from src.models.reputation import Reputation
from src.services.faction_service import FACTION_RIVALRIES, apply_faction_rep_delta

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Anti-farm throttle constants (ADR-0032 #per-day-throttle + ADR-0056 N-V1).
#
# Without this layer the dispatcher is un-throttled: a player could grind
# pirate NPCs for unbounded Terran Federation rep. Two independent limits:
#
#   * PER-(player, faction) EVENT cap   — ADR-0032: "each (player, faction)
#     pair caps at 10 events/day". At the cap, the dispatcher SKIPS that
#     faction's award entirely (we apply the simple full-stop variant of the
#     canon "subsequent events at half-rate" — never awarding beyond the cap).
#   * GLOBAL daily REP pool             — ADR-0056 N-V1: a single
#     daily_faction_rep_pool of 100 rep/day applies to the SUM of POSITIVE
#     faction-rep deltas across all factions. When the pool is exhausted the
#     positive award is CLAMPED to the remaining pool (the in-game action
#     still resolved — kill happened — but the rep delta drops, per N-V1).
#
# Negative deltas (the rivalry cascade negatives, any penalty) are NEVER
# throttled (ADR-0056 N-V1: "Negative deltas (rep losses) are not throttled")
# and never consume the global pool or a per-faction event count.
#
# The counters live in an existing JSONB column — ``player.settings`` — under
# the ``emergent_rep_throttle`` key, so this layer needs NO migration. The
# bucket carries the UTC date it was opened; a stored date != today resets it.
# ---------------------------------------------------------------------------
THROTTLE_SETTINGS_KEY = "emergent_rep_throttle"
PER_FACTION_EVENT_CAP_PER_DAY = 10      # ADR-0032
GLOBAL_REP_POOL_PER_DAY = 100           # ADR-0056 N-V1
RETURN_BOOST_MULT = 1.5                 # WO-RE1: ×rep on positive faction gains during the welcome-back window


def _today_utc_str() -> str:
    """Today's date (UTC) as ``YYYY-MM-DD`` — the throttle bucket's key."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _get_throttle_bucket(player: Player) -> Dict[str, Any]:
    """Return the player's emergent-rep throttle bucket, resetting it on a
    new UTC day.

    Shape::

        {"date": "YYYY-MM-DD",
         "per_faction": {faction_code: event_count, ...},
         "global_rep": int}

    The bucket is read off ``player.settings`` (a non-null JSONB column). When
    the stored date is missing or != today, the WHOLE bucket is reset — counts
    and the global pool both roll over together. The caller must persist the
    bucket back via ``_store_throttle_bucket`` and ``flag_modified``.
    """
    settings = player.settings if isinstance(player.settings, dict) else {}
    bucket = settings.get(THROTTLE_SETTINGS_KEY)
    today = _today_utc_str()
    if not isinstance(bucket, dict) or bucket.get("date") != today:
        bucket = {"date": today, "per_faction": {}, "global_rep": 0}
    else:
        # Defensive: normalise sub-structures that may have been corrupted.
        if not isinstance(bucket.get("per_faction"), dict):
            bucket["per_faction"] = {}
        if not isinstance(bucket.get("global_rep"), int):
            try:
                bucket["global_rep"] = int(bucket.get("global_rep") or 0)
            except (TypeError, ValueError):
                bucket["global_rep"] = 0
    return bucket


def _store_throttle_bucket(player: Player, bucket: Dict[str, Any]) -> None:
    """Write the throttle bucket back onto ``player.settings`` and mark the
    JSONB column dirty so SQLAlchemy emits the UPDATE on the caller's flush.

    Reassigns ``player.settings`` to a fresh dict (not an in-place mutation of
    the existing reference) AND calls ``flag_modified`` — belt-and-braces JSONB
    change tracking, matching the pattern in ``apply_faction_rep_delta`` /
    ``trading.py``. No commit: the caller owns the transaction.
    """
    settings = dict(player.settings) if isinstance(player.settings, dict) else {}
    settings[THROTTLE_SETTINGS_KEY] = bucket
    player.settings = settings
    flag_modified(player, "settings")


# ---------------------------------------------------------------------------
# Canon roster faction-code → FactionType map
#
# The roster faction codes (lowercase strings on ``NPCCharacter.faction_code``
# and ``Faction``-seeding) map to the ``FactionType`` enum the rep primitive
# resolves by. Source: auth/admin.py:create_default_factions (the canonical
# 7-row roster) + npc_spawn_service default_faction_code values. Shadow
# Syndicate (SYNDICATE) and Galactic Concord (CONCORD) are declared for
# completeness but are 🚧/📐 un-seeded — a delta routed to them degrades to a
# logged no-op inside apply_faction_rep_delta (no faction row), never an error.
# ---------------------------------------------------------------------------
FACTION_CODE_TO_TYPE: Dict[str, FactionType] = {
    "terran_federation": FactionType.FEDERATION,
    "mercantile_guild": FactionType.MERCHANTS,
    "frontier_coalition": FactionType.INDEPENDENTS,
    "astral_mining_consortium": FactionType.MINING,
    "nova_scientific_institute": FactionType.EXPLORERS,
    "fringe_alliance": FactionType.OUTLAWS,
    "shadow_syndicate": FactionType.SYNDICATE,
    "pirates": FactionType.PIRATES,
    "galactic_concord": FactionType.CONCORD,
}


def _build_type_to_code() -> None:
    """Populate the FactionType -> faction_code reverse map from the canonical
    forward map. First code wins on the (theoretical) duplicate-type case."""
    for code, ftype in FACTION_CODE_TO_TYPE.items():
        _TYPE_TO_FACTION_CODE.setdefault(ftype, code)


# ---------------------------------------------------------------------------
# Rivalry-cascade rule (ADR-0032 / factions-and-teams.md#rivalry-cascade).
#
# Every POSITIVE emergent delta to a faction with a canonical rival emits an
# automatic fractional NEGATIVE to that rival in the SAME transaction. Negative
# deltas do NOT cascade (that path is farmable). The fraction is applied to the
# positive magnitude and rounded toward zero (a fractional cascade never
# escalates beyond what canon specifies).
#
# Only the fully-seeded, currently-live rivalry pairs are wired here:
#   - TF ↔ FA  (0.5×)  — both seeded
#   - MG ↔ SS  (0.5×)  — SS un-seeded (degrades to no-op), kept for when it seeds
# The 📐-promoted FC↔AM and NS↔AM pairs (0.4×) and the one-way Pirate cascades
# are canon but their combined-rep caps are 📐 Design-only; they are omitted
# from the live cascade until the cap machinery exists, and flagged in the
# report rather than half-implemented.
# ---------------------------------------------------------------------------
RIVALRY_CASCADE: Dict[FactionType, "RivalryCascade"] = {}


@dataclass(frozen=True)
class RivalryCascade:
    rival: FactionType
    fraction: float


def _register_rivalry(a: FactionType, b: FactionType, fraction: float) -> None:
    RIVALRY_CASCADE[a] = RivalryCascade(rival=b, fraction=fraction)
    RIVALRY_CASCADE[b] = RivalryCascade(rival=a, fraction=fraction)


_register_rivalry(FactionType.FEDERATION, FactionType.OUTLAWS, 0.5)  # TF ↔ FA
_register_rivalry(FactionType.MERCHANTS, FactionType.SYNDICATE, 0.5)  # MG ↔ SS


# ---------------------------------------------------------------------------
# FactionType → roster faction_code (reverse of FACTION_CODE_TO_TYPE).
#
# Per-faction throttle counts are keyed by the stable lowercase roster code so
# the JSONB bucket survives any future FactionType enum reordering. Built once
# from the canonical forward map below (after FACTION_CODE_TO_TYPE is defined).
# ---------------------------------------------------------------------------
_TYPE_TO_FACTION_CODE: Dict[FactionType, str] = {}


def _faction_code(faction: FactionType) -> str:
    """Stable JSONB key for a faction's throttle count (roster code, else
    the enum name lowercased as a defensive fallback)."""
    return _TYPE_TO_FACTION_CODE.get(faction, faction.name.lower())


# ---------------------------------------------------------------------------
# Combined-rep cap (ADR-0032 rivalry table "Combined-rep cap" column /
# factions-and-teams.md#rivalry-cascade).
#
# The async ``FactionService.update_reputation`` enforces this via
# ``_apply_rivalry_cap``: for a faction with a canonical rival, a POSITIVE
# gain is reduced so ``current + change + rival_value <= max_combined``, but
# ONLY when the rival's standing is itself positive (rival_value > 0). The sync
# ``apply_faction_rep_delta`` primitive does NOT apply this cap (its docstring
# says route positive gains through update_reputation) — so the DISPATCHER must
# replicate the cap before it calls the sync primitive, or it could exceed the
# combined cap that the async path guarantees.
#
# We replicate ``_apply_rivalry_cap`` faithfully but resolve the rival by
# FactionType (the dispatcher's native key) instead of by Faction.name, so no
# async/await is needed in the sync dispatch path. The cap source-of-truth is
# the SAME ``FACTION_RIVALRIES`` table the async method reads (imported above),
# so the two stay in lock-step automatically.
# ---------------------------------------------------------------------------
# faction_code -> FactionType, restricted to the rivalry-capped factions, so we
# can map FACTION_RIVALRIES (code-keyed) onto the dispatcher's FactionType keys.
_RIVALRY_CODE_TO_TYPE: Dict[str, FactionType] = {
    "terran_federation": FactionType.FEDERATION,
    "fringe_alliance": FactionType.OUTLAWS,
    "mercantile_guild": FactionType.MERCHANTS,
    "shadow_syndicate": FactionType.SYNDICATE,
}

# Build the FactionType -> code reverse map now that both maps are defined.
_build_type_to_code()


def _apply_combined_rep_cap(
    db: Session,
    player_id: uuid.UUID,
    faction: FactionType,
    change: int,
) -> int:
    """Clamp a POSITIVE faction-rep gain to the combined-rep cap (ADR-0032).

    Mirrors ``FactionService._apply_rivalry_cap`` synchronously, resolving the
    rival by ``FactionType`` so no await is required. Returns the (possibly
    reduced) ``change``. Only positive ``change`` is capped; negatives pass
    through untouched (the cap only constrains gains — same as the async path).
    """
    if change <= 0:
        return change

    # Resolve this faction's roster code, then its rivalry config (the SAME
    # FACTION_RIVALRIES table FactionService._apply_rivalry_cap consults).
    faction_code = _faction_code(faction)
    rivalry = FACTION_RIVALRIES.get(faction_code)
    if not rivalry:
        return change

    rival_code = rivalry["rival"]
    max_combined = rivalry["max_combined"]
    rival_type = _RIVALRY_CODE_TO_TYPE.get(rival_code)
    if rival_type is None:
        return change

    # Current standing with THIS faction (0 if no row yet).
    cur_rep = _current_rep_value(db, player_id, faction)
    # Current standing with the RIVAL (0 if no row yet).
    rival_value = _current_rep_value(db, player_id, rival_type)

    # Only cap when the rival's standing is itself positive (matches the async
    # method: a hostile/neutral rival imposes no combined ceiling).
    if rival_value <= 0:
        return change

    projected = cur_rep + change
    if projected + rival_value > max_combined:
        allowed = max(0, max_combined - rival_value - cur_rep)
        if allowed < change:
            logger.info(
                "Combined-rep cap (%s<->%s, max %d) limits emergent gain for "
                "player %s: requested +%d, allowed +%d",
                faction_code, rival_code, max_combined, player_id, change, allowed,
            )
            return allowed

    return change


def _current_rep_value(
    db: Session, player_id: uuid.UUID, faction: FactionType
) -> int:
    """Current ``Reputation.current_value`` for (player, faction-type), or 0
    when no faction row or no reputation row exists. Read-only, no flush."""
    faction_row = (
        db.query(Faction).filter(Faction.faction_type == faction).first()
    )
    if faction_row is None:
        return 0
    rep = (
        db.query(Reputation)
        .filter(
            Reputation.player_id == player_id,
            Reputation.faction_id == faction_row.id,
        )
        .first()
    )
    return rep.current_value if rep is not None else 0


@dataclass(frozen=True)
class FactionDelta:
    """One per-faction reputation move that an emergent action produces."""

    faction: FactionType
    delta: int


@dataclass(frozen=True)
class EmergentAction:
    """A canon emergent-reputation action and the faction deltas it applies.

    ``deltas`` are the DIRECT, base per-faction moves from the canon trigger
    table (NOT the anti-symmetric matrix and NOT the rivalry cascade — the
    cascade is computed by the dispatcher from the positive deltas).
    """

    name: str
    deltas: List[FactionDelta] = field(default_factory=list)
    doc_source: str = ""


# ---------------------------------------------------------------------------
# THE LIVE CANON TRIGGER TABLE.
#
# Each entry's magnitude is quoted directly from
# FEATURES/gameplay/factions-and-teams.md#reputation-triggers (ADR-0032).
# Only actions that are (a) canon-numbered and (b) wirable without duplicating
# an existing rep change are present. NO-CANON / 📐-dependent actions are
# OMITTED (and reported), not guessed.
#
# Currently wired-to-a-site:  KILL_PIRATE_NPC.
# Present-but-unwired (defined so the dispatcher is the single tuning point the
# moment their trigger sites land WITHOUT an existing rep hook):
#   BUY_INSURANCE_BASIC / STANDARD / PREMIUM (MG) — one-time per hull.
# These are ✅ canon-numbered but their natural call sites (ship insurance
# purchase) are not part of THIS WO's safe-wiring set; they are registered so
# the table is the source of truth, and left UNWIRED here (no double-fire risk
# because nothing calls them yet).
# ---------------------------------------------------------------------------
EMERGENT_ACTIONS: Dict[str, EmergentAction] = {
    # Terran Federation: "Kill a Pirate or Cabal NPC | +5 | combat resolver
    # post-hook" (factions-and-teams.md TF table). Base per-faction value
    # only — the +10 anti-symmetric value is sector-influence-conditional
    # (Kill Pirate in *Fed-Controlled* sector), and SectorFactionInfluence is
    # 📐 Design-only / unimplemented, so the dispatcher applies the
    # unconditional base +5 and does NOT guess the conditional uplift.
    "KILL_PIRATE_NPC": EmergentAction(
        name="KILL_PIRATE_NPC",
        deltas=[FactionDelta(FactionType.FEDERATION, 5)],
        doc_source="factions-and-teams.md TF: Kill a Pirate or Cabal NPC (+5)",
    ),
    # Mercantile Guild insurance hooks (factions-and-teams.md MG table) —
    # canon-numbered, one-time per hull. Registered for table-completeness;
    # NOT wired by this WO (no caller invokes them yet → no double-fire).
    "BUY_INSURANCE_BASIC": EmergentAction(
        name="BUY_INSURANCE_BASIC",
        deltas=[FactionDelta(FactionType.MERCHANTS, 2)],
        doc_source="factions-and-teams.md MG: Buy BASIC insurance (+2)",
    ),
    "BUY_INSURANCE_STANDARD": EmergentAction(
        name="BUY_INSURANCE_STANDARD",
        deltas=[FactionDelta(FactionType.MERCHANTS, 5)],
        doc_source="factions-and-teams.md MG: Buy STANDARD insurance (+5)",
    ),
    "BUY_INSURANCE_PREMIUM": EmergentAction(
        name="BUY_INSURANCE_PREMIUM",
        deltas=[FactionDelta(FactionType.MERCHANTS, 10)],
        doc_source="factions-and-teams.md MG: Buy PREMIUM insurance (+10)",
    ),
    # Nova Scientific Institute: "First-scan a NEBULA / BLACK_HOLE / ANOMALY /
    # WARP_STORM sector | +15 | research data" (factions-and-teams.md NS table,
    # line 128). CONCRETE-CANON: explicit +15 NS, single static faction (Nova =
    # FactionType.EXPLORERS), no anti-symmetric matrix entry. Wired at the
    # first-visit (first-scan) surface in movement_service._execute_movement.
    #
    # CANON-SUBSET (deliberate, not invention): the canon names four research
    # sector types but only NEBULA and BLACK_HOLE have a populated
    # ``Sector.type`` value in code — ANOMALY and WARP_STORM exist only in the
    # un-columned ``SectorSpecialType`` enum, so they are unrepresentable and the
    # caller fires only on the two that exist. Firing on a faithful subset of the
    # canon set is not a fabricated number; the magnitude (+15) and faction are
    # taken verbatim from canon. The ANOMALY/WARP_STORM coverage is flagged for
    # the orchestrator (no column to gate on).
    "NOVA_FIRST_SCAN_RESEARCH_SECTOR": EmergentAction(
        name="NOVA_FIRST_SCAN_RESEARCH_SECTOR",
        deltas=[FactionDelta(FactionType.EXPLORERS, 15)],
        doc_source=(
            "factions-and-teams.md NS: First-scan a NEBULA / BLACK_HOLE / "
            "ANOMALY / WARP_STORM sector (+15) — NEBULA/BLACK_HOLE subset wired"
        ),
    ),
    # WO-NEBULA: Nova Scientific rep for nebula harvesting — PER-BLOCK (+1 per
    # whole 3 Quantum Shards harvested; quantum_service.harvest_nebula dispatches
    # this action once per earned 3-shard block, mirroring the TRADE_VOLUME
    # per-block model below). Nova == FactionType.EXPLORERS.
    "HARVEST_NEBULA_SHARDS_NS": EmergentAction(
        name="HARVEST_NEBULA_SHARDS_NS",
        deltas=[FactionDelta(FactionType.EXPLORERS, 1)],
        doc_source="quantum-resources.md NS: +1 / 3 Quantum Shards harvested (per-block; WO-NEBULA)",
    ),
    # -----------------------------------------------------------------------
    # WO-CD-2: PER-BLOCK trade-volume actions (one whole rep point per earned
    # 5,000-cr block — see apply_trade_volume_rep below, which owns the
    # accumulation and dispatches THIS action once per earned block).
    #
    # CONCRETE-CANON (factions-and-teams.md per-faction trigger tables):
    #   TF line 79:  "Trade at a Federation-flagged port | +1 / 5,000 cr"
    #   MG line 90:  "Trade at any Guild-flagged station | +1 / 5,000 cr"
    #   FC line 107: "Trade at a Frontier outpost | +1 / 5,000 cr"
    #   FA line 144: "Trade at a Fringe-controlled port (legal goods) | +1 / 5,000 cr"
    #   AM line 117: "Sell raw ore to an AM-flagged refinery | +2 / 5,000 cr"
    #                (double-weighted — registered as +2 per block; the caller
    #                 only awards this action on an ORE SELL at a station whose
    #                 services['refining_facility'] is true, per the canon
    #                 "refinery" qualifier)
    #
    # The per-block magnitude (NOT the per-5,000-cr rate) is what lands here:
    # one earned block = +1 for TF/MG/FC/FA, +2 for AM. The accumulator awards
    # this action N times when N whole blocks were crossed by a single trade.
    "TRADE_VOLUME_TF": EmergentAction(
        name="TRADE_VOLUME_TF",
        deltas=[FactionDelta(FactionType.FEDERATION, 1)],
        doc_source="factions-and-teams.md TF: Trade at a Federation-flagged port (+1 / 5,000 cr)",
    ),
    "TRADE_VOLUME_MG": EmergentAction(
        name="TRADE_VOLUME_MG",
        deltas=[FactionDelta(FactionType.MERCHANTS, 1)],
        doc_source="factions-and-teams.md MG: Trade at any Guild-flagged station (+1 / 5,000 cr)",
    ),
    "TRADE_VOLUME_FC": EmergentAction(
        name="TRADE_VOLUME_FC",
        deltas=[FactionDelta(FactionType.INDEPENDENTS, 1)],
        doc_source="factions-and-teams.md FC: Trade at a Frontier outpost (+1 / 5,000 cr)",
    ),
    "TRADE_VOLUME_FA": EmergentAction(
        name="TRADE_VOLUME_FA",
        deltas=[FactionDelta(FactionType.OUTLAWS, 1)],
        doc_source="factions-and-teams.md FA: Trade at a Fringe-controlled port legal goods (+1 / 5,000 cr)",
    ),
    "TRADE_VOLUME_AM_ORE": EmergentAction(
        name="TRADE_VOLUME_AM_ORE",
        deltas=[FactionDelta(FactionType.MINING, 2)],
        doc_source="factions-and-teams.md AM: Sell raw ore to an AM-flagged refinery (+2 / 5,000 cr)",
    ),
    # -----------------------------------------------------------------------
    # WO-CD-2: Build a PUBLIC toll warp gate — anti-symmetric matrix
    # (factions-and-teams.md line 213): "Build a public toll warp gate |
    # TF 0 | MG +30 | FC +5 | AM 0 | NS +5 | FA 0 | SS 0 | PI 0".
    #
    # CONCRETE-CANON multi-faction fan-out: only the three NON-ZERO deltas are
    # registered (MG +30, FC +5, NS +5); the zero columns are no-ops. The
    # EmergentAction.deltas list already supports a multi-faction event, and the
    # dispatcher fans out each delta (with its own throttle/cap/cascade) in one
    # transaction. Wired at gate-activation (advance_gate) ONLY for a public
    # tunnel (WarpTunnel.is_public). The private/whitelist row (line 214) is
    # PARKED — the private-gate build path does not exist (is_public is always
    # True at creation), so no caller can reach it.
    "BUILD_PUBLIC_WARP_GATE": EmergentAction(
        name="BUILD_PUBLIC_WARP_GATE",
        deltas=[
            FactionDelta(FactionType.MERCHANTS, 30),
            FactionDelta(FactionType.INDEPENDENTS, 5),
            FactionDelta(FactionType.EXPLORERS, 5),
        ],
        doc_source=(
            "factions-and-teams.md anti-symmetric matrix: Build a public toll "
            "warp gate (MG +30, FC +5, NS +5; TF/AM/FA/SS/PI 0)"
        ),
    ),
}


# ---------------------------------------------------------------------------
# WO-CD-2: trade-volume accumulator (CONCRETE-CANON "+N / 5,000 cr").
#
# Canon expresses the faction trade-volume triggers as a RATE — "+1 / 5,000 cr"
# (TF/MG/FC/FA) or "+2 / 5,000 cr" (AM ore→refinery), NOT a flat per-trade
# award. A single sub-5,000-cr trade must award 0 BUT count toward the next
# block; a large trade awards one rep point (or +2 for AM) per completed
# 5,000-cr block and carries the remainder forward. Awarding the flat magnitude
# on every trade would over-pay; ignoring sub-block trades would under-pay.
#
# This helper owns that accumulation. Per (player, faction) cumulative traded
# credits live in player.settings JSONB under ``emergent_trade_volume`` (no
# migration — mirrors the throttle bucket). On each completed trade the caller
# passes the trade's gross credit value + the resolved EMERGENT_ACTIONS key for
# that faction; we add to the running total, compute how many whole 5,000-cr
# blocks were newly crossed, keep the remainder, and dispatch the per-block
# action that many times through the SAME dispatcher (so the award inherits the
# canon throttle, combined-rep cap, global pool, and rivalry cascade). The
# action's registered delta magnitude IS the per-block award (+1 TF/MG/FC/FA,
# +2 AM), so the dispatcher applies the correct per-block value automatically.
#
# Idempotent / safe: only the CALLER (a completed buy/sell, under the trade
# transaction, never on a failed trade) invokes this; flush-only; never raises.
# ---------------------------------------------------------------------------
TRADE_VOLUME_SETTINGS_KEY = "emergent_trade_volume"
TRADE_VOLUME_CREDITS_PER_BLOCK = 5_000  # CONCRETE-CANON: "/ 5,000 cr"


def apply_trade_volume_rep(
    db: Session,
    player: Player,
    action: str,
    credits_traded: int,
    context: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Accrue a completed trade's credit value toward the canon 5,000-cr
    trade-volume blocks for ``action``'s faction, and award the per-block
    emergent action once per newly-crossed block.

    Args:
        db: the caller's SYNC session (flush-only; caller owns the commit).
        player: the trading Player.
        action: one of the ``TRADE_VOLUME_*`` keys in EMERGENT_ACTIONS.
        credits_traded: the trade's gross credit value (total_cost on a buy,
            total_earnings on a sell) — MUST be the value of a COMPLETED trade.
        context: optional event metadata (sector_id) for the rep-history reason.

    Returns ``{"success", "action", "blocks_awarded", "carry_over", "applied"}``.
    Never raises — a rep hiccup must never break the trade that triggered it.

    Accumulation: running per-(player, faction) cumulative credits live in
    ``player.settings['emergent_trade_volume'][<faction_code>]``. After adding
    this trade's value, ``blocks = total // 5,000`` whole blocks are due; we
    dispatch ``action`` that many times (each award is one per-block magnitude,
    routed through ``apply_emergent_action`` so throttle/cap/pool/cascade all
    apply) and store ``total % 5,000`` as the carry-over for the next trade.
    """
    context = context or {}
    spec = EMERGENT_ACTIONS.get(action)
    if spec is None or not spec.deltas:
        logger.warning(
            "apply_trade_volume_rep: unknown/empty action %r — no rep applied",
            action,
        )
        return {"success": False, "action": action, "reason": "unknown_action"}
    if player is None or getattr(player, "id", None) is None:
        logger.warning(
            "apply_trade_volume_rep(%s): no valid player — no rep applied",
            action,
        )
        return {"success": False, "action": action, "reason": "no_player"}
    try:
        credits_traded = int(credits_traded)
    except (TypeError, ValueError):
        credits_traded = 0
    if credits_traded <= 0:
        # A zero/negative-value trade contributes nothing and is not a block.
        return {
            "success": True, "action": action,
            "blocks_awarded": 0, "carry_over": None, "applied": [],
        }

    # The faction is the single primary delta's faction; key the running total
    # by its stable roster code (survives any FactionType enum reordering).
    faction = spec.deltas[0].faction
    fcode = _faction_code(faction)

    try:
        # Load (and normalise) the per-(player, faction) cumulative bucket.
        settings = player.settings if isinstance(player.settings, dict) else {}
        ledger = settings.get(TRADE_VOLUME_SETTINGS_KEY)
        if not isinstance(ledger, dict):
            ledger = {}
        try:
            running = int(ledger.get(fcode, 0))
        except (TypeError, ValueError):
            running = 0
        if running < 0:
            running = 0

        running += credits_traded
        blocks = running // TRADE_VOLUME_CREDITS_PER_BLOCK
        carry_over = running % TRADE_VOLUME_CREDITS_PER_BLOCK

        # Persist the carry-over BEFORE awarding so a mid-loop hiccup can never
        # double-count the same credits on a retry (the credits are "spent" the
        # moment they convert to blocks; only whole blocks pay out).
        ledger[fcode] = int(carry_over)
        new_settings = dict(settings)
        new_settings[TRADE_VOLUME_SETTINGS_KEY] = ledger
        player.settings = new_settings
        flag_modified(player, "settings")

        applied: List[Dict[str, Any]] = []
        if blocks > 0:
            # One emergent action per newly-crossed block; each carries the
            # per-block magnitude registered on the action (the dispatcher
            # applies throttle/cap/pool/cascade per award). A very large trade
            # crossing many blocks is naturally bounded by the dispatcher's
            # daily per-faction event cap and global rep pool.
            for _ in range(int(blocks)):
                result = EmergentReputationService(db).apply_emergent_action(
                    player, action, context
                )
                applied.append(result)
        return {
            "success": True,
            "action": action,
            "blocks_awarded": int(blocks),
            "carry_over": int(carry_over),
            "applied": applied,
        }
    except Exception as e:  # never raise into the trade path
        logger.error(
            "apply_trade_volume_rep(%s) for player %s failed: %s",
            action, getattr(player, "id", None), e,
        )
        return {"success": False, "action": action, "reason": "exception"}


# ---------------------------------------------------------------------------
# WO-CD-2: faction-name → TRADE_VOLUME action-key resolver.
#
# A station's controlling faction is carried as ``Station.faction_affiliation``
# — the faction DISPLAY NAME (matched against ``Faction.name`` everywhere in
# the trade stack: docking_service, construction_service, trading_service). We
# map that name to the right per-faction TRADE_VOLUME_* action key. Only the
# four factions canon gives a GENERIC trade-volume trigger (TF/MG/FC/FA) are
# resolved here; AM's ore-to-refinery trigger is commodity- and venue-specific
# and is resolved separately at the sell site (it is NOT a generic trade entry).
#
# Returns None for an unaffiliated station, an unknown name, or any faction
# without a canon generic trade-volume trigger (no award fires — never a guess).
# ---------------------------------------------------------------------------
_FACTION_NAME_TO_TRADE_VOLUME_ACTION: Dict[str, str] = {
    "Terran Federation": "TRADE_VOLUME_TF",
    "Mercantile Guild": "TRADE_VOLUME_MG",
    "Frontier Coalition": "TRADE_VOLUME_FC",
    "Fringe Alliance": "TRADE_VOLUME_FA",
}


def trade_volume_action_for_faction_name(faction_name: Optional[str]) -> Optional[str]:
    """Resolve a station's ``faction_affiliation`` display name to its generic
    TRADE_VOLUME_* action key, or None when the faction has no canon generic
    trade-volume trigger (TF/MG/FC/FA only). AM is intentionally absent here —
    its ore-to-refinery trigger is handled separately at the sell site."""
    if not faction_name:
        return None
    return _FACTION_NAME_TO_TRADE_VOLUME_ACTION.get(faction_name)


class EmergentReputationService:
    """ADR-0032 dispatcher: the single entry point for emergent faction-rep.

    Build it on the caller's SYNC session. ``apply_emergent_action`` flushes
    only; the caller owns the commit (mirrors the existing combat faction-rep
    hook, which folds the police-kill delta into combat's single commit).
    """

    def __init__(self, db: Session):
        self.db = db

    def apply_emergent_action(
        self,
        player: Player,
        action: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Apply the canon faction-rep deltas for ``action`` to ``player``.

        Args:
            player: the acting Player (must have an ``id``).
            action: a key into ``EMERGENT_ACTIONS``.
            context: optional event metadata (sector_id, witnesses, …). Stored
                in the rep-history reason for audit; reserved for the future
                sector-influence weighting and witness-mark mechanics. Unknown
                keys are ignored.

        Returns a result dict ``{"success", "action", "applied": [...]}``. On
        any failure (unknown action, missing player, rep hiccup) it returns
        ``{"success": False, ...}`` and NEVER raises — emergent rep must never
        break the gameplay path that triggered it.

        FLUSH-ONLY: delegates to ``apply_faction_rep_delta`` which flushes; the
        caller owns the commit.

        ANTI-FARM (ADR-0032 #per-day-throttle + ADR-0056 N-V1): before awarding
        a POSITIVE direct delta the dispatcher (a) skips it if this
        (player, faction) pair has already hit ``PER_FACTION_EVENT_CAP_PER_DAY``
        events today, (b) clamps it to the combined-rep cap, and (c) clamps it
        to the remaining ``GLOBAL_REP_POOL_PER_DAY`` pool. Counters live in
        ``player.settings['emergent_rep_throttle']`` (no migration) and reset on
        a new UTC day. Negative deltas (incl. the rivalry cascade) are never
        throttled and never consume the pool or an event count.
        """
        context = context or {}
        spec = EMERGENT_ACTIONS.get(action)
        if spec is None:
            logger.warning(
                "apply_emergent_action: unknown action %r — no rep applied "
                "(known: %s)",
                action, sorted(EMERGENT_ACTIONS.keys()),
            )
            return {"success": False, "action": action, "reason": "unknown_action"}

        if player is None or getattr(player, "id", None) is None:
            logger.warning(
                "apply_emergent_action(%s): no valid player — no rep applied",
                action,
            )
            return {"success": False, "action": action, "reason": "no_player"}

        player_id: uuid.UUID = player.id
        reason_suffix = ""
        sector_id = context.get("sector_id")
        if sector_id is not None:
            reason_suffix = f" @sector={sector_id}"

        applied: List[Dict[str, Any]] = []
        # Load (and roll-over if stale) the per-player daily throttle bucket.
        bucket = _get_throttle_bucket(player)
        bucket_dirty = False
        try:
            # 1) Direct per-faction deltas from the canon table.
            for fd in spec.deltas:
                reason = f"emergent:{action}{reason_suffix}"

                # --- Anti-farm throttle (POSITIVE direct deltas only) -------
                if fd.delta > 0:
                    fcode = _faction_code(fd.faction)
                    per_faction = bucket["per_faction"]
                    events_today = int(per_faction.get(fcode, 0))

                    # (a) PER-(player, faction) EVENT cap — ADR-0032.
                    if events_today >= PER_FACTION_EVENT_CAP_PER_DAY:
                        logger.info(
                            "Throttle: %s hit %d-event/day cap for player %s "
                            "(faction %s) — skipping award",
                            action, PER_FACTION_EVENT_CAP_PER_DAY,
                            player_id, fcode,
                        )
                        applied.append({
                            "faction": fd.faction.name,
                            "delta": 0,
                            "requested": fd.delta,
                            "applied": False,
                            "direct": True,
                            "throttled": "per_faction_event_cap",
                        })
                        continue

                    # This positive direct delta is a counted EVENT regardless
                    # of how much rep ultimately lands (the player acted).
                    per_faction[fcode] = events_today + 1
                    bucket_dirty = True

                    # WO-RE1: returning-player ×1.5 boost on the AWARD magnitude while the
                    # welcome-back window is open (return_boost_until). The event-count above +
                    # the canon "requested" below stay fd.delta; the combined-rep cap + global
                    # pool still bound the boosted award (boost composes inside the guards).
                    effective_delta = fd.delta
                    rbu = getattr(player, "return_boost_until", None)
                    if rbu is not None:
                        rbu = rbu if rbu.tzinfo else rbu.replace(tzinfo=timezone.utc)
                        if datetime.now(timezone.utc) < rbu:
                            effective_delta = int(round(fd.delta * RETURN_BOOST_MULT))

                    # (b) Combined-rep cap — ADR-0032 (the dispatcher must
                    #     enforce what the sync primitive does NOT).
                    award = _apply_combined_rep_cap(
                        self.db, player_id, fd.faction, effective_delta
                    )
                    cap_clamped = award < effective_delta

                    # (c) GLOBAL daily rep pool — ADR-0056 N-V1. Clamp the
                    #     positive award to the pool that remains today.
                    remaining = GLOBAL_REP_POOL_PER_DAY - int(bucket["global_rep"])
                    if remaining < 0:
                        remaining = 0
                    pool_clamped = award > remaining
                    if pool_clamped:
                        award = remaining

                    if award <= 0:
                        # Event happened (cargo delivered / NPC killed) but the
                        # rep delta drops to 0 (N-V1) or the cap left no room.
                        logger.info(
                            "Throttle: %s rep award for player %s faction %s "
                            "clamped to 0 (cap=%s pool=%s; pool used %d/%d)",
                            action, player_id, fcode, cap_clamped, pool_clamped,
                            int(bucket["global_rep"]), GLOBAL_REP_POOL_PER_DAY,
                        )
                        applied.append({
                            "faction": fd.faction.name,
                            "delta": 0,
                            "requested": fd.delta,
                            "applied": False,
                            "direct": True,
                            "throttled": (
                                "global_pool" if pool_clamped else "combined_cap"
                            ),
                        })
                        continue

                    rep = apply_faction_rep_delta(
                        self.db, player_id, fd.faction, award, reason
                    )
                    bucket["global_rep"] = int(bucket["global_rep"]) + award
                    applied.append({
                        "faction": fd.faction.name,
                        "delta": award,
                        "requested": fd.delta,
                        "applied": rep is not None,
                        "direct": True,
                        "cap_clamped": cap_clamped,
                        "pool_clamped": pool_clamped,
                    })

                    # 2) Rivalry cascade — fires on an actually-awarded
                    #    POSITIVE delta (negative deltas do not reward rivals;
                    #    that path is farmable). The cascade NEGATIVE is itself
                    #    never throttled and never consumes the pool. Scale the
                    #    cascade off the AWARDED magnitude so a clamped award
                    #    yields a proportionally smaller (never larger) penalty.
                    if rep is not None:
                        cascade = RIVALRY_CASCADE.get(fd.faction)
                        if cascade is not None:
                            cascade_delta = -int(award * cascade.fraction)
                            if cascade_delta != 0:
                                crep = apply_faction_rep_delta(
                                    self.db,
                                    player_id,
                                    cascade.rival,
                                    cascade_delta,
                                    f"emergent:{action}:cascade<-{fd.faction.name}",
                                )
                                applied.append({
                                    "faction": cascade.rival.name,
                                    "delta": cascade_delta,
                                    "applied": crep is not None,
                                    "direct": False,
                                    "cascade_from": fd.faction.name,
                                })
                    continue

                # --- Negative (or zero) direct delta: never throttled --------
                rep = apply_faction_rep_delta(
                    self.db, player_id, fd.faction, fd.delta, reason
                )
                applied.append({
                    "faction": fd.faction.name,
                    "delta": fd.delta,
                    "applied": rep is not None,
                    "direct": True,
                })

            # Persist the throttle bucket on the caller's transaction (flush
            # only — apply_faction_rep_delta already flushed; we just mark the
            # JSONB dirty so the UPDATE rides the caller's commit).
            if bucket_dirty:
                _store_throttle_bucket(player, bucket)
        except Exception as e:  # never raise into the gameplay path
            logger.error(
                "apply_emergent_action(%s) for player %s failed: %s",
                action, player_id, e,
            )
            return {
                "success": False,
                "action": action,
                "reason": "exception",
                "applied": applied,
            }

        logger.info(
            "Emergent action %s applied for player %s: %d faction-rep moves",
            action, player_id, len(applied),
        )
        return {"success": True, "action": action, "applied": applied}


def apply_emergent_action(
    db: Session,
    player: Player,
    action: str,
    context: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Module-level convenience wrapper around
    ``EmergentReputationService.apply_emergent_action``.

    Lets call sites fire a single emergent action without constructing the
    service explicitly:

        from src.services.emergent_reputation_service import apply_emergent_action
        apply_emergent_action(self.db, attacker, "KILL_PIRATE_NPC",
                              {"sector_id": sector.sector_id})

    FLUSH-ONLY (caller owns the commit). Never raises.
    """
    return EmergentReputationService(db).apply_emergent_action(player, action, context)


# ---------------------------------------------------------------------------
# Planet-capture faction penalty (DECISIONS planet-assault-reward-model, Max
# 2026-06-20 conditional (c)).
#
# Capturing a FACTION-OWNED planet earns the captor NEGATIVE reputation with
# that owning faction. Unlike the static EMERGENT_ACTIONS table (fixed faction
# per action), the owning faction here is DYNAMIC — it depends on which faction
# held the planet — so this is expressed as a dynamic-faction penalty helper
# wrapping the proven ``apply_faction_rep_delta`` primitive (the same layer the
# dispatcher uses for its own negatives). Keeping the magnitude here makes
# emergent_reputation_service the single tuning surface for capture rep, mirroring
# how the EMERGENT_ACTIONS table is the single surface for fixed-faction rep.
#
# MAGNITUDE IS NO-CANON: −50 is PROPOSED, mirroring the canon
# ``attacked_chartered_planet`` personal-rep penalty (−50). The factions canon
# (factions-and-teams.md reputation-triggers / ADR-0032) does NOT list a
# capture-a-faction-planet trigger, so this number is flagged for Max and is
# the smallest sensible intervention until canon lands.
#
# WIRING REALITY: the Planet model has no faction-owner field (planets are owned
# only by human Players via the player_planets join table, else unowned), so the
# combat-side caller resolves no owning faction today and never invokes this. The
# helper exists so the magnitude/behavior is fixed and ready the instant a
# planet faction-owner signal lands — no double-fire risk because nothing calls
# it yet (matching the EMERGENT_ACTIONS "defined-but-unwired" pattern).
# ---------------------------------------------------------------------------
PLANET_CAPTURE_FACTION_PENALTY = -50  # NO-CANON (proposed; mirrors chartered −50)


def apply_planet_capture_faction_penalty(
    db: Session,
    player: Player,
    owning_faction: FactionType,
    context: Optional[Dict[str, Any]] = None,
) -> Optional[Any]:
    """Apply the capture-vs-owning-faction NEGATIVE reputation penalty.

    Fires ONLY for a faction-owned planet (the caller passes the resolved
    ``owning_faction``). FLUSH-ONLY (delegates to ``apply_faction_rep_delta``,
    which flushes; the caller owns the commit). Never raises — a rep hiccup
    must never break combat resolution.

    Returns the updated Reputation row (or None when no faction row exists / on
    error), matching ``apply_faction_rep_delta``.
    """
    context = context or {}
    if player is None or getattr(player, "id", None) is None:
        logger.warning(
            "apply_planet_capture_faction_penalty: no valid player — skipped"
        )
        return None
    sector_id = context.get("sector_id")
    reason = "emergent:CAPTURE_FACTION_PLANET"
    if sector_id is not None:
        reason += f" @sector={sector_id}"
    try:
        return apply_faction_rep_delta(
            db,
            player.id,
            owning_faction,
            PLANET_CAPTURE_FACTION_PENALTY,
            reason,
        )
    except Exception as e:  # pragma: no cover - defensive; never break combat
        logger.error(
            "apply_planet_capture_faction_penalty failed for player %s: %s",
            getattr(player, "id", None), e,
        )
        return None
