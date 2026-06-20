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
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from src.models.faction import FactionType
from src.models.player import Player
from src.services.faction_service import apply_faction_rep_delta

logger = logging.getLogger(__name__)


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
        try:
            # 1) Direct per-faction deltas from the canon table.
            for fd in spec.deltas:
                reason = f"emergent:{action}{reason_suffix}"
                rep = apply_faction_rep_delta(
                    self.db, player_id, fd.faction, fd.delta, reason
                )
                applied.append(
                    {
                        "faction": fd.faction.name,
                        "delta": fd.delta,
                        "applied": rep is not None,
                        "direct": True,
                    }
                )

                # 2) Rivalry cascade — POSITIVE deltas only (negative deltas
                #    do not reward rivals; that path is farmable).
                if fd.delta > 0:
                    cascade = RIVALRY_CASCADE.get(fd.faction)
                    if cascade is not None:
                        # Round the fractional cascade toward zero so it never
                        # exceeds canon, and skip a zeroed cascade.
                        cascade_delta = -int(fd.delta * cascade.fraction)
                        if cascade_delta != 0:
                            crep = apply_faction_rep_delta(
                                self.db,
                                player_id,
                                cascade.rival,
                                cascade_delta,
                                f"emergent:{action}:cascade<-{fd.faction.name}",
                            )
                            applied.append(
                                {
                                    "faction": cascade.rival.name,
                                    "delta": cascade_delta,
                                    "applied": crep is not None,
                                    "direct": False,
                                    "cascade_from": fd.faction.name,
                                }
                            )
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
