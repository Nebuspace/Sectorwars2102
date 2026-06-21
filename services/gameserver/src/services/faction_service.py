"""
Faction service for managing faction relationships, reputation, and missions.
"""

from uuid import UUID
from typing import List, Optional, Dict, Any
from datetime import datetime, timedelta
from sqlalchemy.orm import Session
from sqlalchemy import and_, or_, func
from sqlalchemy.exc import IntegrityError
import logging

from src.models.faction import Faction, FactionType, FactionMission
from src.models.reputation import Reputation, ReputationLevel
from src.models.player import Player
from src.models.sector import Sector
from src.models.sector_faction_influence import SectorFactionInfluence
from src.services.websocket_service import ConnectionManager

logger = logging.getLogger(__name__)


def _dispatch_faction_medals(db: Session, player_id: UUID) -> None:
    """Fire the medals-lane faction hook
    ``medal_service.check_and_award_faction_medals(db, player_id)`` after a
    reputation transition reaches HONORED (diplomatic.peacemaker @3 /
    ambassadors_star @10 — faction_honored count).

    Defensive: resolved by ``getattr`` (the medals lane may be absent),
    idempotent on the medals side, and any failure is logged and swallowed — a
    medal hiccup must NEVER break a reputation adjustment."""
    try:
        import src.services.medal_service as _medal_module
        hook = getattr(_medal_module, "check_and_award_faction_medals", None)
        if callable(hook):
            hook(db, player_id)
    except Exception as e:  # never let a medal hiccup break reputation
        logger.error("Faction medal dispatch hook failed: %s", e)
manager = ConnectionManager()

# Faction rivalry configuration: paired factions have a combined reputation cap.
# Gaining standing with one faction limits how high you can go with its rival.
FACTION_RIVALRIES = {
    "terran_federation": {"rival": "fringe_alliance", "max_combined": 800},
    "fringe_alliance": {"rival": "terran_federation", "max_combined": 800},
    "mercantile_guild": {"rival": "shadow_syndicate", "max_combined": 600},
    "shadow_syndicate": {"rival": "mercantile_guild", "max_combined": 600},
}

# Trade price multipliers keyed by reputation thresholds (checked high-to-low).
# Positive reputation = discount, negative = surcharge.
TRADE_MODIFIERS = [
    (700, 0.85),    # EXALTED: 15% discount
    (500, 0.90),    # REVERED: 10% discount
    (300, 0.95),    # HONORED: 5% discount
    (100, 0.97),    # FRIENDLY: 3% discount
    (-99, 1.00),    # NEUTRAL: no change (covers -99 to +99)
    (-299, 1.05),   # UNFRIENDLY: 5% surcharge
    (-499, 1.15),   # HOSTILE: 15% surcharge
    (-699, 1.30),   # HATED: 30% surcharge
]
TRADE_MODIFIER_PUBLIC_ENEMY = 1.50  # Fallback for -700 and below


def apply_faction_rep_delta(
    db: Session,
    player_id: UUID,
    faction_type: FactionType,
    delta: int,
    reason: str,
) -> Optional[Reputation]:
    """Apply a faction reputation delta from a SYNC, caller-owned transaction.

    Built for in-transaction hooks (e.g. combat_service applying the
    Marshal-kill −250 Federation delta, police-forces.md) where the async
    ``FactionService.update_reputation`` cannot be used: it awaits, commits
    internally mid-transaction, and fires WebSocket sends — calling it from
    a sync combat path would double-commit and break the combat
    transaction. This helper get-or-creates the Reputation row, clamps to
    the model's documented [-800, +800] range, appends a history entry,
    and FLUSHES ONLY — the caller owns the commit.

    The faction is resolved by FactionType (the Faction model has no
    ``code`` column, so roster faction codes like "terran_federation" need
    an explicit mapping by the caller). Returns None — with an error log,
    never an exception — when no faction row of that type exists, so a
    missing seed degrades to a lost rep delta rather than a failed combat.

    No rivalry cap is applied: the cap only constrains positive gains and
    this helper exists for penalty hooks; route positive gains through
    ``FactionService.update_reputation``.
    """
    faction = (
        db.query(Faction)
        .filter(Faction.faction_type == faction_type)
        .first()
    )
    if faction is None:
        logger.error(
            "apply_faction_rep_delta: no %s faction row exists — delta %+d "
            "for player %s dropped (reason: %s). Seed the faction "
            "(npc_spawn_service._ensure_federation_faction).",
            faction_type.name, delta, player_id, reason,
        )
        return None

    reputation = (
        db.query(Reputation)
        .filter(
            and_(
                Reputation.player_id == player_id,
                Reputation.faction_id == faction.id,
            )
        )
        .first()
    )
    if reputation is None:
        # Mirror initialize_player_reputations defaults for the new row.
        reputation = Reputation(
            player_id=player_id,
            faction_id=faction.id,
            current_value=0,
            current_level=ReputationLevel.NEUTRAL,
            title="Neutral",
            trade_modifier=0.0,
            port_access_level=0,
            combat_response="neutral",
            history=[],
        )
        db.add(reputation)

    svc = FactionService(db)
    old_value = reputation.current_value
    reputation.current_value = max(-800, min(800, reputation.current_value + delta))
    reputation.current_level = svc._calculate_reputation_level(reputation.current_value)
    reputation.title = svc._get_reputation_title(reputation.current_level)
    reputation.trade_modifier = svc._calculate_trade_modifier(reputation.current_value)
    reputation.port_access_level = svc._calculate_port_access_level(reputation.current_value)
    reputation.combat_response = svc._calculate_combat_response(reputation.current_value)

    # Reassign (not in-place append) so SQLAlchemy detects the JSONB change.
    history = list(reputation.history or [])
    history.append({
        "timestamp": datetime.utcnow().isoformat(),
        "old_value": old_value,
        "new_value": reputation.current_value,
        "change": reputation.current_value - old_value,
        "reason": reason,
    })
    reputation.history = history
    reputation.last_updated = datetime.utcnow()

    db.flush()
    logger.info(
        "Faction rep delta for player %s with %s (%s): %d -> %d (%s)",
        player_id, faction.name, faction_type.name,
        old_value, reputation.current_value, reason,
    )
    return reputation


# Clamp range for per-sector faction influence (ADR-0021: 0-100% taxonomy input).
SECTOR_INFLUENCE_MIN = 0.0
SECTOR_INFLUENCE_MAX = 100.0


def adjust_sector_influence(
    db: Session,
    sector_id: UUID,
    faction_id: UUID,
    delta: float,
) -> Optional[SectorFactionInfluence]:
    """UPSERT one faction's influence over one sector by ``delta`` (ADR-0021).

    The WRITE half of SectorFactionInfluence: get-or-create the
    ``(sector_id, faction_id)`` row, add ``delta`` to ``influence_percentage``,
    CLAMP the stored value to [0, 100], and FLUSH only — the caller owns the
    commit (built for in-transaction hooks like the colony-establish and
    warp-gate-build paths, mirroring ``apply_faction_rep_delta``).

    The READ-side taxonomy / patrol-spawn effects (ADR-0021) are deliberately
    NOT computed here (Max-gated) — this only maintains the canonical stored
    influence value. ``patrol_spawn_weight`` is left at its model default and is
    untouched until the read-side lands.

    Defensive: a ``None`` faction (or sector) is a no-op returning ``None`` so a
    missing-faction hook degrades to a dropped influence delta rather than an
    exception that breaks the caller's primary action.
    """
    if faction_id is None or sector_id is None:
        return None

    influence = (
        db.query(SectorFactionInfluence)
        .filter(
            and_(
                SectorFactionInfluence.sector_id == sector_id,
                SectorFactionInfluence.faction_id == faction_id,
            )
        )
        .first()
    )
    if influence is None:
        # Contain the INSERT in a SAVEPOINT: under concurrency two callers can
        # both miss the SELECT above and race to insert the same
        # (sector_id, faction_id) — the table's UniqueConstraint makes the loser
        # raise IntegrityError on flush. Without the savepoint that error would
        # poison the whole session and the caller's later db.commit() (the colony
        # founding / gate completion) would abort with PendingRollbackError. The
        # savepoint rolls back ONLY the failed INSERT; we then re-SELECT the row
        # that won the race and apply the delta to it. (Same guard medal_service
        # uses for its identical get-or-create.)
        try:
            with db.begin_nested():
                influence = SectorFactionInfluence(
                    sector_id=sector_id,
                    faction_id=faction_id,
                    influence_percentage=0.0,
                )
                db.add(influence)
                db.flush()
        except IntegrityError:
            influence = (
                db.query(SectorFactionInfluence)
                .filter(
                    and_(
                        SectorFactionInfluence.sector_id == sector_id,
                        SectorFactionInfluence.faction_id == faction_id,
                    )
                )
                .first()
            )
            if influence is None:
                # Lost the race yet still can't see the winner — degrade to a
                # dropped delta rather than re-raise into the caller's txn.
                return None

    old_value = influence.influence_percentage or 0.0
    influence.influence_percentage = max(
        SECTOR_INFLUENCE_MIN,
        min(SECTOR_INFLUENCE_MAX, old_value + float(delta)),
    )

    db.flush()
    logger.info(
        "Sector influence for faction %s over sector %s: %.2f -> %.2f (delta %+.2f)",
        faction_id, sector_id, old_value, influence.influence_percentage, float(delta),
    )
    return influence


def dominant_reputation_faction_id(db: Session, player_id: UUID) -> Optional[UUID]:
    """Resolve the faction the player has the HIGHEST personal reputation with.

    There is no dedicated "dominant faction" column on the player, so the
    canonical signal is the player's strongest standing: the ``Reputation`` row
    with the greatest ``current_value`` (ties broken DETERMINISTICALLY by
    ``faction_id`` so the credited faction never flaps between calls). Returns
    ``None`` when the player has no reputation rows or their best standing is not
    positive — only a genuinely allied faction should be credited with sector
    influence, not a merely-least-hostile one.
    """
    if player_id is None:
        return None
    top = (
        db.query(Reputation)
        .filter(Reputation.player_id == player_id)
        .order_by(Reputation.current_value.desc(), Reputation.faction_id.asc())
        .first()
    )
    if top is None or (top.current_value or 0) <= 0:
        return None
    return top.faction_id


class FactionService:
    """Service for managing faction-related operations."""

    def __init__(self, db: Session):
        self.db = db
    
    async def get_all_factions(self) -> List[Faction]:
        """Get all factions in the game."""
        return self.db.query(Faction).all()
    
    async def get_faction_by_id(self, faction_id: UUID) -> Optional[Faction]:
        """Get a specific faction by ID."""
        return self.db.query(Faction).filter(Faction.id == faction_id).first()
    
    async def get_faction_by_type(self, faction_type: FactionType) -> Optional[Faction]:
        """Get a faction by its type."""
        return self.db.query(Faction).filter(Faction.faction_type == faction_type).first()
    
    async def get_player_reputation(self, player_id: UUID, faction_id: UUID) -> Optional[Reputation]:
        """Get a player's reputation with a specific faction."""
        return self.db.query(Reputation).filter(
            and_(
                Reputation.player_id == player_id,
                Reputation.faction_id == faction_id
            )
        ).first()
    
    async def get_all_player_reputations(self, player_id: UUID) -> List[Reputation]:
        """Get all reputation records for a player."""
        return self.db.query(Reputation).filter(
            Reputation.player_id == player_id
        ).all()
    
    async def initialize_player_reputations(self, player_id: UUID) -> List[Reputation]:
        """Initialize reputation records for a new player with all factions."""
        factions = await self.get_all_factions()
        reputations = []
        
        for faction in factions:
            # Check if reputation already exists
            existing = await self.get_player_reputation(player_id, faction.id)
            if existing:
                reputations.append(existing)
                continue
            
            # Create new reputation record
            reputation = Reputation(
                player_id=player_id,
                faction_id=faction.id,
                current_value=0,
                current_level=ReputationLevel.NEUTRAL,
                title="Neutral",
                trade_modifier=0.0,
                port_access_level=0,
                combat_response="neutral"
            )
            self.db.add(reputation)
            reputations.append(reputation)
        
        self.db.commit()
        return reputations
    
    async def update_reputation(
        self, 
        player_id: UUID, 
        faction_id: UUID, 
        change: int,
        reason: str = "Unknown"
    ) -> Reputation:
        """
        Update a player's reputation with a faction.
        
        Args:
            player_id: The player's ID
            faction_id: The faction's ID
            change: The reputation change (positive or negative)
            reason: The reason for the change
            
        Returns:
            Updated reputation record
        """
        reputation = await self.get_player_reputation(player_id, faction_id)
        if not reputation:
            # Initialize if doesn't exist
            await self.initialize_player_reputations(player_id)
            reputation = await self.get_player_reputation(player_id, faction_id)
        
        old_value = reputation.current_value
        old_level = reputation.current_level

        # Enforce faction rivalry cap when increasing reputation
        if change > 0:
            change = self._apply_rivalry_cap(player_id, faction_id, reputation.current_value, change)

        # Update reputation value (clamped between -800 and +800)
        reputation.current_value = max(-800, min(800, reputation.current_value + change))
        
        # Update reputation level based on new value
        reputation.current_level = self._calculate_reputation_level(reputation.current_value)
        reputation.title = self._get_reputation_title(reputation.current_level)
        
        # Update effects
        reputation.trade_modifier = self._calculate_trade_modifier(reputation.current_value)
        reputation.port_access_level = self._calculate_port_access_level(reputation.current_value)
        reputation.combat_response = self._calculate_combat_response(reputation.current_value)
        
        # Add to history — reassign (not in-place append) so SQLAlchemy
        # detects the JSONB change; in-place mutation is not change-tracked.
        history = list(reputation.history or [])
        history.append({
            "timestamp": datetime.utcnow().isoformat(),
            "old_value": old_value,
            "new_value": reputation.current_value,
            "change": change,
            "reason": reason
        })
        reputation.history = history
        
        reputation.last_updated = datetime.utcnow()

        # Medal: diplomatic.peacemaker (3) / ambassadors_star (10) — count of
        # factions at HONORED. Fires only on a level transition that REACHES
        # HONORED (the genuine earning event). Dispatched BEFORE the commit below
        # so the medal INSERT rides this transaction's commit (the durable
        # pattern the other wired medals use — dispatch into the caller's open
        # unit of work, never after it has already committed). Idempotent on the
        # medals side; defensive dispatcher — never breaks the reputation
        # adjustment. (Simplified faction_honored count; the docs'
        # "mutually-rivalrous factions simultaneously" nuance for Ambassador's
        # Star is NO-CANON here and is NOT enforced.)
        if (old_level != reputation.current_level
                and reputation.current_level == ReputationLevel.HONORED):
            _dispatch_faction_medals(self.db, player_id)

        self.db.commit()

        # Send WebSocket notification if reputation level changed
        if old_level != reputation.current_level:
            recipient = self.db.query(Player).filter(Player.id == player_id).first()
            if not (recipient and recipient.user_id):
                return
            await manager.send_personal_message(str(recipient.user_id), {
                "type": "reputation_changed",
                "faction_id": str(faction_id),
                "faction_name": reputation.faction.name,
                "old_level": old_level.value,
                "new_level": reputation.current_level.value,
                "old_value": old_value,
                "new_value": reputation.current_value,
                "title": reputation.title
            })
        
        logger.info(f"Updated reputation for player {player_id} with faction {faction_id}: {old_value} -> {reputation.current_value}")
        return reputation
    
    # ------------------------------------------------------------------
    # Rivalry, decay, and trade modifier helpers
    # ------------------------------------------------------------------

    def _apply_rivalry_cap(
        self,
        player_id: UUID,
        faction_id: UUID,
        current_value: int,
        change: int
    ) -> int:
        """
        Enforce faction rivalry limits on a positive reputation change.

        When a faction has a defined rival, the player's combined reputation
        with both factions cannot exceed the configured max_combined cap.
        If necessary the change is reduced so the cap is respected.

        Returns the (possibly reduced) change value.
        """
        # Resolve faction name for the target faction
        faction = self.db.query(Faction).filter(Faction.id == faction_id).first()
        if not faction:
            return change

        faction_name = faction.name.lower().replace(" ", "_")
        rivalry = FACTION_RIVALRIES.get(faction_name)
        if not rivalry:
            return change

        # Look up the rival faction by name pattern
        rival_name = rivalry["rival"]
        max_combined = rivalry["max_combined"]

        rival_faction = self.db.query(Faction).filter(
            func.lower(func.replace(Faction.name, ' ', '_')) == rival_name
        ).first()
        if not rival_faction:
            return change

        rival_rep = self.db.query(Reputation).filter(
            and_(
                Reputation.player_id == player_id,
                Reputation.faction_id == rival_faction.id
            )
        ).first()

        rival_value = rival_rep.current_value if rival_rep else 0

        # Only cap when both reputations are positive
        if rival_value <= 0:
            return change

        # Projected new value after the change
        projected = current_value + change
        if projected + rival_value > max_combined:
            allowed = max(0, max_combined - rival_value - current_value)
            if allowed < change:
                logger.info(
                    f"Rivalry between {faction_name} and {rival_name} limits reputation gain "
                    f"for player {player_id}: requested +{change}, allowed +{allowed}"
                )
                return allowed

        return change

    async def apply_reputation_decay(self, player_id: UUID) -> List[Dict[str, Any]]:
        """
        Apply time-based reputation decay for a player.

        Reputations above +100 or below -100 that have not been updated in
        over 30 days decay by 1 point per inactive day, up to a maximum of
        -50 total decay per call.  Reputations flagged with ``decay_paused``
        are skipped.

        Returns a list of dicts describing each decayed faction for caller
        visibility / WebSocket notification.
        """
        reputations = self.db.query(Reputation).filter(
            Reputation.player_id == player_id
        ).all()

        now = datetime.utcnow()
        decay_threshold = timedelta(days=30)
        max_decay = 50  # absolute cap on total decay applied per invocation
        results: List[Dict[str, Any]] = []

        for rep in reputations:
            # Skip locked or paused reputations
            if rep.decay_paused or rep.is_locked:
                continue

            # Only decay reputations outside the neutral band
            if -100 <= rep.current_value <= 100:
                continue

            # Check inactivity window
            last = rep.last_updated.replace(tzinfo=None) if rep.last_updated.tzinfo else rep.last_updated
            inactive_days = (now - last).days
            if inactive_days <= 30:
                continue

            decay_days = inactive_days - 30
            decay_amount = min(decay_days, max_decay)

            old_value = rep.current_value
            if rep.current_value > 100:
                # Decay toward zero but not below +100
                rep.current_value = max(100, rep.current_value - decay_amount)
            elif rep.current_value < -100:
                # Decay toward zero but not above -100
                rep.current_value = min(-100, rep.current_value + decay_amount)

            if rep.current_value != old_value:
                rep.current_level = self._calculate_reputation_level(rep.current_value)
                rep.title = self._get_reputation_title(rep.current_level)
                rep.trade_modifier = self._calculate_trade_modifier(rep.current_value)
                rep.port_access_level = self._calculate_port_access_level(rep.current_value)
                rep.combat_response = self._calculate_combat_response(rep.current_value)

                # Record decay in history
                if not rep.history:
                    rep.history = []
                rep.history = rep.history + [{
                    "timestamp": now.isoformat(),
                    "old_value": old_value,
                    "new_value": rep.current_value,
                    "change": rep.current_value - old_value,
                    "reason": f"Inactivity decay ({decay_days} days idle)"
                }]

                results.append({
                    "faction_id": str(rep.faction_id),
                    "old_value": old_value,
                    "new_value": rep.current_value,
                    "decay_applied": old_value - rep.current_value if old_value > 0 else rep.current_value - old_value,
                    "inactive_days": inactive_days
                })

                logger.info(
                    f"Reputation decay for player {player_id}, faction {rep.faction_id}: "
                    f"{old_value} -> {rep.current_value} ({inactive_days} days inactive)"
                )

        if results:
            self.db.commit()

        return results

    async def get_trade_modifier(self, player_id: UUID, faction_id: UUID) -> float:
        """
        Return a price multiplier for a player at a faction-controlled port.

        The multiplier is derived from the player's current reputation value
        with the faction using the TRADE_MODIFIERS lookup table:

            EXALTED  (+700+): 0.85  (15% discount)
            REVERED  (+500) : 0.90
            HONORED  (+300) : 0.95
            FRIENDLY (+100) : 0.97
            NEUTRAL         : 1.00
            UNFRIENDLY(-100): 1.05
            HOSTILE  (-300) : 1.15
            HATED    (-500) : 1.30
            PUBLIC_ENEMY(-700): 1.50

        Returns 1.0 (no modifier) when no reputation record exists.
        """
        reputation = await self.get_player_reputation(player_id, faction_id)
        if not reputation:
            return 1.0

        value = reputation.current_value
        for threshold, modifier in TRADE_MODIFIERS:
            if value >= threshold:
                return modifier

        return TRADE_MODIFIER_PUBLIC_ENEMY

    def _calculate_reputation_level(self, value: int) -> ReputationLevel:
        """Calculate reputation level from numeric value."""
        if value >= 700:
            return ReputationLevel.EXALTED
        elif value >= 600:
            return ReputationLevel.REVERED
        elif value >= 500:
            return ReputationLevel.HONORED
        elif value >= 400:
            return ReputationLevel.VALUED
        elif value >= 300:
            return ReputationLevel.RESPECTED
        elif value >= 200:
            return ReputationLevel.TRUSTED
        elif value >= 100:
            return ReputationLevel.ACKNOWLEDGED
        elif value >= 50:
            return ReputationLevel.RECOGNIZED
        elif value >= -50:
            return ReputationLevel.NEUTRAL
        elif value >= -100:
            return ReputationLevel.QUESTIONABLE
        elif value >= -200:
            return ReputationLevel.SUSPICIOUS
        elif value >= -300:
            return ReputationLevel.UNTRUSTWORTHY
        elif value >= -400:
            return ReputationLevel.SMUGGLER
        elif value >= -500:
            return ReputationLevel.PIRATE
        elif value >= -600:
            return ReputationLevel.OUTLAW
        elif value >= -700:
            return ReputationLevel.CRIMINAL
        else:
            return ReputationLevel.PUBLIC_ENEMY
    
    def _get_reputation_title(self, level: ReputationLevel) -> str:
        """Get display title for reputation level."""
        titles = {
            ReputationLevel.EXALTED: "Exalted",
            ReputationLevel.REVERED: "Revered",
            ReputationLevel.HONORED: "Honored",
            ReputationLevel.VALUED: "Valued",
            ReputationLevel.RESPECTED: "Respected",
            ReputationLevel.TRUSTED: "Trusted",
            ReputationLevel.ACKNOWLEDGED: "Acknowledged",
            ReputationLevel.RECOGNIZED: "Recognized",
            ReputationLevel.NEUTRAL: "Neutral",
            ReputationLevel.QUESTIONABLE: "Questionable",
            ReputationLevel.SUSPICIOUS: "Suspicious",
            ReputationLevel.UNTRUSTWORTHY: "Untrustworthy",
            ReputationLevel.SMUGGLER: "Smuggler",
            ReputationLevel.PIRATE: "Pirate",
            ReputationLevel.OUTLAW: "Outlaw",
            ReputationLevel.CRIMINAL: "Criminal",
            ReputationLevel.PUBLIC_ENEMY: "Public Enemy"
        }
        return titles.get(level, "Unknown")
    
    def _calculate_trade_modifier(self, value: int) -> float:
        """Calculate trade price modifier based on reputation."""
        # Linear scale from -30% to +30% based on reputation
        return round(value / 800 * 0.3, 2)
    
    def _calculate_port_access_level(self, value: int) -> int:
        """Calculate port access level based on reputation."""
        if value >= 600:
            return 3  # Full access
        elif value >= 200:
            return 2  # Standard access
        elif value >= -200:
            return 1  # Limited access
        else:
            return 0  # No access
    
    def _calculate_combat_response(self, value: int) -> str:
        """Calculate NPC combat response based on reputation."""
        if value >= 400:
            return "friendly"
        elif value >= -200:
            return "neutral"
        else:
            return "hostile"
    
    async def get_faction_pricing_modifier(
        self, 
        player_id: UUID, 
        faction_id: UUID
    ) -> float:
        """
        Get the pricing modifier for a player at faction-controlled ports.
        
        Returns:
            Float multiplier for prices (e.g., 0.8 = 20% discount)
        """
        faction = await self.get_faction_by_id(faction_id)
        if not faction:
            return 1.0
        
        reputation = await self.get_player_reputation(player_id, faction_id)
        if not reputation:
            return faction.base_pricing_modifier
        
        return faction.get_pricing_modifier(reputation.current_value)
    
    async def check_territory_access(
        self, 
        player_id: UUID, 
        sector_id: UUID
    ) -> Dict[str, Any]:
        """
        Check if a player can access a faction-controlled sector.
        
        Returns:
            Dict with 'allowed' boolean and 'reason' string
        """
        # Find which faction controls this sector
        controlling_faction = None
        factions = await self.get_all_factions()
        
        for faction in factions:
            if sector_id in (faction.territory_sectors or []):
                controlling_faction = faction
                break
        
        if not controlling_faction:
            # Sector is not faction-controlled
            return {"allowed": True, "reason": "Neutral territory"}
        
        # Check player reputation
        reputation = await self.get_player_reputation(player_id, controlling_faction.id)
        if not reputation:
            # No reputation record, treat as hostile
            return {
                "allowed": False, 
                "reason": f"No standing with {controlling_faction.name}"
            }
        
        if controlling_faction.can_access_territory(reputation.current_value):
            return {"allowed": True, "reason": "Good standing"}
        else:
            return {
                "allowed": False, 
                "reason": f"Insufficient reputation with {controlling_faction.name}"
            }
    
    async def get_available_missions(
        self, 
        player_id: UUID, 
        faction_id: Optional[UUID] = None
    ) -> List[FactionMission]:
        """
        Get available missions for a player.
        
        Args:
            player_id: The player's ID
            faction_id: Optional specific faction to filter by
            
        Returns:
            List of available missions
        """
        # Get player info
        player = self.db.query(Player).filter(Player.id == player_id).first()
        if not player:
            return []
        
        # Base query
        query = self.db.query(FactionMission).filter(
            and_(
                FactionMission.is_active == 1,
                or_(
                    FactionMission.expires_at.is_(None),
                    FactionMission.expires_at > datetime.utcnow()
                )
            )
        )
        
        # Filter by faction if specified
        if faction_id:
            query = query.filter(FactionMission.faction_id == faction_id)
        
        missions = query.all()
        available_missions = []
        
        for mission in missions:
            # Check reputation requirement
            reputation = await self.get_player_reputation(player_id, mission.faction_id)
            # A missing reputation row means neutral (0), not hidden missions —
            # otherwise players without initialized rows see an empty board.
            rep_value = reputation.current_value if reputation else 0
            if rep_value >= mission.min_reputation:
                # Check level requirement (you'll need to implement player level)
                # For now, assume all players meet level requirements
                available_missions.append(mission)
        
        return available_missions
    
    async def create_mission(
        self,
        faction_id: UUID,
        title: str,
        description: str,
        mission_type: str,
        credit_reward: int,
        reputation_reward: int,
        **kwargs
    ) -> FactionMission:
        """Create a new faction mission."""
        mission = FactionMission(
            faction_id=faction_id,
            title=title,
            description=description,
            mission_type=mission_type,
            credit_reward=credit_reward,
            reputation_reward=reputation_reward,
            min_reputation=kwargs.get('min_reputation', -800),
            min_level=kwargs.get('min_level', 1),
            item_rewards=kwargs.get('item_rewards', []),
            target_sector_id=kwargs.get('target_sector_id'),
            cargo_type=kwargs.get('cargo_type'),
            cargo_quantity=kwargs.get('cargo_quantity'),
            target_faction_id=kwargs.get('target_faction_id'),
            expires_at=kwargs.get('expires_at'),
            is_active=1
        )
        
        self.db.add(mission)
        self.db.commit()
        self.db.refresh(mission)
        
        return mission
    
    async def update_faction_territory(
        self,
        faction_id: UUID,
        sector_ids: List[UUID]
    ) -> Faction:
        """Update the territory controlled by a faction."""
        faction = await self.get_faction_by_id(faction_id)
        if not faction:
            raise ValueError(f"Faction {faction_id} not found")
        
        faction.territory_sectors = sector_ids
        faction.updated_at = datetime.utcnow()
        
        self.db.commit()
        self.db.refresh(faction)
        
        # Broadcast territory change
        await manager.broadcast({
            "type": "faction_territory_changed",
            "faction_id": str(faction_id),
            "faction_name": faction.name,
            "sectors": [str(sid) for sid in sector_ids]
        })
        
        return faction