"""
Medal service — relational award lifecycle (ADR-0028).

Medals now live in two relational tables instead of ``Player.settings`` JSONB:

* ``medals`` (catalog)        — seeded from :mod:`src.services.medal_catalog`.
* ``player_medals`` (awards)  — one row per (player, medal); UNIQUE(player_id, medal_id).

The UNIQUE constraint is the idempotency keystone (ADR-0028): a medal can be
awarded at most once per player, and concurrent award attempts are defeated at
the DB layer.

This module exposes:

* :func:`award_medal` — module-level idempotent core award (exact signature per task).
* :func:`check_and_award_combat_medals` — FROZEN dispatcher hook the combat lane
  calls: ``(db, killer_player, context)``. Defensive: never raises into combat.
* Analogous trade / exploration dispatchers.
* :class:`MedalService` — preserves the legacy method surface
  (``get_player_medals``, ``check_combat_medals``, ``check_trading_medals``,
  ``check_exploration_medals``) now backed by the relational tables, so existing
  callers in ``combat_service``, ``trading.py`` and ``ranking.py`` keep working.

Legacy JSONB readers: this module no longer writes ``Player.settings['medals']``.
Any code that *reads* that JSONB will simply see no medals; the only known
readers route through ``MedalService.get_player_medals`` (this module) which is
now relational. See the task report's ``jsonb_readers_handled`` for the grep.
"""

import logging
import uuid
from typing import Dict, Any, Optional, List

from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError

from src.models.player import Player
from src.models.medal import Medal, PlayerMedal
from src.services.medal_catalog import (
    MEDAL_CATALOG,
    LEGACY_KEY_TO_ID,
    get_catalog_entry,
    medals_for_trigger,
    seed_medals,
)

# Re-export so legacy importers of MEDAL_DEFINITIONS keep resolving. The shape
# is the relational catalog now; keys are the stable namespaced ids.
MEDAL_DEFINITIONS = MEDAL_CATALOG

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Core award — module-level, idempotent. Exact signature per task.
# ---------------------------------------------------------------------------
def award_medal(
    db: Session,
    player_id: uuid.UUID,
    medal_id: str,
    *,
    source_event_key: Optional[str] = None,
    source_combat_log_id: Optional[uuid.UUID] = None,
    awarded_via: str = "system",
    context_payload: Optional[Dict[str, Any]] = None,
    awarded_by_user_id: Optional[uuid.UUID] = None,
) -> bool:
    """Award ``medal_id`` to ``player_id``, idempotently.

    Idempotency has two layers (ADR-0028):

    1. Pre-check: ``SELECT`` for an existing (player, medal) row → skip.
    2. ``UNIQUE (player_id, medal_id)``: a concurrent INSERT that races past the
       pre-check raises ``IntegrityError``. We contain that INSERT in a
       SAVEPOINT (``db.begin_nested``) so the violation rolls back ONLY the
       failed award — never the caller's open transaction. This is critical:
       ``award_medal`` is dispatched from inside the combat unit of work (which
       holds an uncommitted CombatLog / ship-destruction / turn-spend), and a
       bare ``db.rollback()`` here would silently discard all of it.

    ``medal_id`` accepts either the stable namespaced id (``combat.bronze_star``)
    or a legacy short key (``bronze_star``), which is resolved to the stable id.

    Returns ``True`` if a new award row was created, ``False`` if already held
    or the medal_id is unknown.
    """
    # Resolve legacy short keys → stable id.
    resolved_id = medal_id if medal_id in MEDAL_CATALOG else LEGACY_KEY_TO_ID.get(medal_id)
    if not resolved_id:
        logger.warning("award_medal: unknown medal_id %r — skipping", medal_id)
        return False

    # Layer 1: pre-check.
    existing = (
        db.query(PlayerMedal)
        .filter(PlayerMedal.player_id == player_id, PlayerMedal.medal_id == resolved_id)
        .first()
    )
    if existing is not None:
        return False

    award = PlayerMedal(
        player_id=player_id,
        medal_id=resolved_id,
        awarded_via=awarded_via,
        source_event_key=source_event_key,
        source_combat_log_id=source_combat_log_id,
        awarded_by_user_id=awarded_by_user_id,
        context_payload=context_payload,
    )
    try:
        # SAVEPOINT-scoped INSERT: the UNIQUE violation (if we lost the race)
        # rolls back to the savepoint ONLY, leaving the caller's transaction —
        # the open combat unit of work — fully intact.
        with db.begin_nested():
            db.add(award)
            db.flush()  # surface the UNIQUE violation here, inside the savepoint
    except IntegrityError:
        # Layer 2: lost the race — another transaction inserted the same pair.
        # begin_nested already rolled back to the savepoint; nothing else lost.
        logger.info(
            "award_medal: %s already held by player %s (race resolved by UNIQUE)",
            resolved_id, player_id,
        )
        return False

    logger.info("Medal awarded: %s -> player %s (via=%s)", resolved_id, player_id, awarded_via)
    return True


# ---------------------------------------------------------------------------
# Stat resolution — read current counter values for a player.
# ---------------------------------------------------------------------------
def _combat_victory_count(db: Session, player_id: uuid.UUID) -> int:
    """Count this player's PvP combat victories (mirrors combat_service)."""
    from src.models.combat_log import CombatLog, CombatOutcome

    return (
        db.query(CombatLog)
        .filter(
            CombatLog.defender_id.isnot(None),
            ((CombatLog.attacker_id == player_id) & (CombatLog.outcome == CombatOutcome.ATTACKER_WIN.value))
            | ((CombatLog.defender_id == player_id) & (CombatLog.outcome == CombatOutcome.DEFENDER_WIN.value)),
        )
        .count()
    )


def _evaluate_and_award(
    db: Session,
    player_id: uuid.UUID,
    trigger_type: str,
    current_value: int,
    *,
    source_event_key: Optional[str] = None,
    source_combat_log_id: Optional[uuid.UUID] = None,
    awarded_via: str = "system",
) -> List[str]:
    """Award every catalog medal for ``trigger_type`` whose threshold is met.

    Returns the list of newly-awarded stable medal ids. Idempotency handled by
    :func:`award_medal`.
    """
    newly: List[str] = []
    for entry in medals_for_trigger(trigger_type):
        threshold = entry["criteria"].get("threshold", 0)
        if current_value >= threshold:
            if award_medal(
                db,
                player_id,
                entry["id"],
                source_event_key=source_event_key,
                source_combat_log_id=source_combat_log_id,
                awarded_via=awarded_via,
                context_payload={"trigger": trigger_type, "value_at_award": current_value},
            ):
                newly.append(entry["id"])
    return newly


# ---------------------------------------------------------------------------
# FROZEN HOOK — the combat lane calls this. EXACT signature required.
# ---------------------------------------------------------------------------
def check_and_award_combat_medals(
    db: Session,
    killer_player: Player,
    context: Dict[str, Any],
) -> List[str]:
    """Dispatcher: evaluate combat medals for ``killer_player`` and award earned.

    ``context`` is a dict like ``{victim_id, combat_log_id, kind}``. Defensive —
    NEVER raises into the combat lane; on any error it logs and returns ``[]``.

    Returns the list of newly-awarded stable medal ids.
    """
    try:
        if killer_player is None:
            return []

        context = context or {}
        combat_log_id = context.get("combat_log_id")
        # Normalize combat_log_id to UUID if a string slipped through.
        if isinstance(combat_log_id, str):
            try:
                combat_log_id = uuid.UUID(combat_log_id)
            except (ValueError, TypeError):
                combat_log_id = None

        victory_count = _combat_victory_count(db, killer_player.id)

        awarded = _evaluate_and_award(
            db,
            killer_player.id,
            "combat_victories",
            victory_count,
            source_event_key=context.get("kind") or "combat.victory",
            source_combat_log_id=combat_log_id,
            awarded_via="combat",
        )

        # Rank-upset medal (Quantum Cross): only when context flags it.
        rank_upset = int(context.get("rank_upset_levels", 0) or 0)
        if rank_upset >= 5:
            awarded += _evaluate_and_award(
                db, killer_player.id, "rank_upset", rank_upset,
                source_event_key="combat.rank_upset",
                source_combat_log_id=combat_log_id,
                awarded_via="combat",
            )

        return awarded
    except Exception as e:  # defensive: never break combat
        logger.error("check_and_award_combat_medals failed for %s: %s", getattr(killer_player, "id", "?"), e)
        return []


def check_and_award_trade_medals(
    db: Session,
    player: Player,
    context: Dict[str, Any],
) -> List[str]:
    """Trade-lane dispatcher (analogous to the combat hook). Defensive.

    ``context`` may carry ``total_trades`` and ``lifetime_credits``; falls back
    to reading the player row's ``credits`` when not supplied.
    """
    try:
        if player is None:
            return []
        context = context or {}
        awarded: List[str] = []

        total_trades = context.get("total_trades")
        if total_trades is not None:
            awarded += _evaluate_and_award(
                db, player.id, "total_trades", int(total_trades),
                source_event_key="trade.completed", awarded_via="trade",
            )

        lifetime_credits = context.get("lifetime_credits")
        if lifetime_credits is None:
            lifetime_credits = getattr(player, "credits", None)
        if lifetime_credits is not None:
            awarded += _evaluate_and_award(
                db, player.id, "lifetime_credits", int(lifetime_credits),
                source_event_key="trade.completed", awarded_via="trade",
            )
        return awarded
    except Exception as e:
        logger.error("check_and_award_trade_medals failed for %s: %s", getattr(player, "id", "?"), e)
        return []


def check_and_award_exploration_medals(
    db: Session,
    player: Player,
    context: Dict[str, Any],
) -> List[str]:
    """Exploration-lane dispatcher (analogous to the combat hook). Defensive."""
    try:
        if player is None:
            return []
        context = context or {}
        awarded: List[str] = []
        for trigger in ("sectors_visited", "planets_created", "planets_colonized"):
            value = context.get(trigger)
            if value is not None:
                awarded += _evaluate_and_award(
                    db, player.id, trigger, int(value),
                    source_event_key="exploration", awarded_via="exploration",
                )
        return awarded
    except Exception as e:
        logger.error("check_and_award_exploration_medals failed for %s: %s", getattr(player, "id", "?"), e)
        return []


# ---------------------------------------------------------------------------
# Legacy-compatible service class — same public surface, relational backing.
# ---------------------------------------------------------------------------
class MedalService:
    def __init__(self, db: Session):
        self.db = db

    # ── Queries ─────────────────────────────────────────────────────
    def get_player_medals(self, player_id: uuid.UUID) -> Dict[str, Any]:
        """Earned (from player_medals) + available (catalog minus earned).

        Preserves the legacy return shape consumed by ranking.py /player/medals.
        """
        try:
            player = self.db.query(Player).filter(Player.id == player_id).first()
            if not player:
                return {"success": False, "error": "Player not found"}

            rows = (
                self.db.query(PlayerMedal, Medal)
                .join(Medal, PlayerMedal.medal_id == Medal.id)
                .filter(PlayerMedal.player_id == player_id)
                .all()
            )
            earned_ids = {pm.medal_id for pm, _ in rows}

            earned = []
            for pm, medal in rows:
                criteria = medal.criteria or {}
                earned.append({
                    "key": medal.id,
                    "name": medal.name,
                    "category": medal.category,
                    "description": medal.description,
                    "icon": criteria.get("icon"),
                    "tier": medal.tier,
                    "awarded_at": pm.awarded_at.isoformat() if pm.awarded_at else None,
                    "awarded_via": pm.awarded_via,
                    "value_at_award": (pm.context_payload or {}).get("value_at_award"),
                })

            available = []
            for medal_id, entry in MEDAL_CATALOG.items():
                if medal_id in earned_ids:
                    continue
                criteria = entry["criteria"]
                available.append({
                    "key": medal_id,
                    "name": entry["name"],
                    "category": entry["category"],
                    "description": entry["description"],
                    "icon": criteria.get("icon"),
                    "tier": entry["tier"],
                    "trigger_type": criteria.get("type"),
                    "threshold": criteria.get("threshold"),
                })

            return {
                "success": True,
                "earned": earned,
                "available": available,
                "total_earned": len(earned),
                "total_available": len(available),
            }
        except Exception as e:
            logger.error(f"Error retrieving medals for player {player_id}: {e}")
            return {"success": False, "error": str(e)}

    # ── Award convenience (legacy-compatible) ───────────────────────
    def check_combat_medals(
        self,
        player_id: uuid.UUID,
        combat_victories: int,
        rank_upset_levels: int = 0,
    ) -> List[str]:
        """Legacy signature preserved (combat_service calls this).

        Backed by the relational award path. Returns newly-awarded medal ids.
        """
        try:
            awarded = _evaluate_and_award(
                self.db, player_id, "combat_victories", combat_victories,
                source_event_key="combat.victory", awarded_via="combat",
            )
            if rank_upset_levels >= 5:
                awarded += _evaluate_and_award(
                    self.db, player_id, "rank_upset", rank_upset_levels,
                    source_event_key="combat.rank_upset", awarded_via="combat",
                )
            return awarded
        except Exception as e:
            logger.error(f"Error checking combat medals for player {player_id}: {e}")
            return []

    def check_trading_medals(
        self,
        player_id: uuid.UUID,
        total_trades: int,
        lifetime_credits: int,
    ) -> List[str]:
        """Legacy signature preserved (trading.py calls this)."""
        try:
            awarded = _evaluate_and_award(
                self.db, player_id, "total_trades", total_trades,
                source_event_key="trade.completed", awarded_via="trade",
            )
            awarded += _evaluate_and_award(
                self.db, player_id, "lifetime_credits", lifetime_credits,
                source_event_key="trade.completed", awarded_via="trade",
            )
            return awarded
        except Exception as e:
            logger.error(f"Error checking trading medals for player {player_id}: {e}")
            return []

    def check_exploration_medals(
        self,
        player_id: uuid.UUID,
        sectors_visited: int,
        planets_created: int,
        planets_colonized: int,
    ) -> List[str]:
        """Legacy signature preserved."""
        try:
            awarded = _evaluate_and_award(
                self.db, player_id, "sectors_visited", sectors_visited,
                source_event_key="exploration", awarded_via="exploration",
            )
            awarded += _evaluate_and_award(
                self.db, player_id, "planets_created", planets_created,
                source_event_key="exploration", awarded_via="exploration",
            )
            awarded += _evaluate_and_award(
                self.db, player_id, "planets_colonized", planets_colonized,
                source_event_key="exploration", awarded_via="exploration",
            )
            return awarded
        except Exception as e:
            logger.error(f"Error checking exploration medals for player {player_id}: {e}")
            return []

    # ── Admin ────────────────────────────────────────────────────────
    def admin_grant(
        self,
        player_id: uuid.UUID,
        medal_id: str,
        granting_user_id: uuid.UUID,
        reason: Optional[str] = None,
    ) -> bool:
        """Admin grant. Returns True if newly awarded, False if already held/unknown."""
        return award_medal(
            self.db, player_id, medal_id,
            awarded_via="admin_grant",
            awarded_by_user_id=granting_user_id,
            source_event_key="admin.grant",
            context_payload={"reason": reason} if reason else None,
        )

    def admin_revoke(self, player_id: uuid.UUID, medal_id: str) -> bool:
        """Admin revoke. Returns True if a row was removed, else False."""
        resolved_id = medal_id if medal_id in MEDAL_CATALOG else LEGACY_KEY_TO_ID.get(medal_id, medal_id)
        row = (
            self.db.query(PlayerMedal)
            .filter(PlayerMedal.player_id == player_id, PlayerMedal.medal_id == resolved_id)
            .first()
        )
        if row is None:
            return False
        self.db.delete(row)
        self.db.flush()
        logger.info("Medal revoked: %s from player %s", resolved_id, player_id)
        return True


__all__ = [
    "MEDAL_DEFINITIONS",
    "MedalService",
    "award_medal",
    "seed_medals",
    "check_and_award_combat_medals",
    "check_and_award_trade_medals",
    "check_and_award_exploration_medals",
]
