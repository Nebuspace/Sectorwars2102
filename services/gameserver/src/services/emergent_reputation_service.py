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
}


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

                    # (b) Combined-rep cap — ADR-0032 (the dispatcher must
                    #     enforce what the sync primitive does NOT).
                    award = _apply_combined_rep_cap(
                        self.db, player_id, fd.faction, fd.delta
                    )
                    cap_clamped = award < fd.delta

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
