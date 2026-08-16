"""
Faction service for managing faction relationships, reputation, and missions.
"""

from uuid import UUID
from datetime import datetime, timezone
from typing import List, Optional, Dict, Any
from sqlalchemy.orm import Session
from sqlalchemy import and_, func
from sqlalchemy.exc import IntegrityError
import logging

from src.models.faction import Faction, FactionType
from src.models.reputation import Reputation, ReputationLevel
from src.models.player import Player
from src.models.sector_faction_influence import SectorFactionInfluence
from src.services.websocket_service import connection_manager as manager

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

    The READ-side taxonomy is derived on read (``sector_territory_tier``).
    ``patrol_spawn_weight`` is recomputed here from the LEG-34 formula so
    Loop B can read live weights (identity defaults for missing
    ``base_patrol_intensity`` / ``zone_mod`` Faction columns).

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
    # LEG-65 activity clock — decay idle uses this, never updated_at alone.
    influence.last_action_at = datetime.now(timezone.utc)
    apply_patrol_spawn_weight(influence)

    db.flush()
    logger.info(
        "Sector influence for faction %s over sector %s: %.2f -> %.2f (delta %+.2f); "
        "patrol_spawn_weight=%.3f",
        faction_id, sector_id, old_value, influence.influence_percentage, float(delta),
        influence.patrol_spawn_weight or 0.0,
    )
    return influence


# ---------------------------------------------------------------------------
# SectorFactionInfluence READ side (WO-FI / ADR-0021 / LEG-INI-05)
#
# The WRITE half (adjust_sector_influence, above) maintains the canonical
# stored influence_percentage and patrol_spawn_weight. These pure-READ helpers
# consume them for taxonomy / spawn-bias effects.
# ---------------------------------------------------------------------------

# Canon taxonomy thresholds — LEG-34 / DATA_MODELS/gameplay.md § Territory
# taxonomy derivation (0–100 percentage points):
#   >=95 core; >=75 controlled; >=40 contested if rival>=25 else controlled;
#   else uncontrolled.
TERRITORY_CORE_MIN = 95.0
TERRITORY_CONTROLLED_MIN = 75.0
TERRITORY_CONTESTED_BAND_MIN = 40.0
TERRITORY_SECONDARY_PRESENCE_MIN = 25.0

# Patrol spawn weight (gameplay.md § Patrol spawn weight derivation).
# Faction.base_patrol_intensity / zone_mod columns do not exist yet — identity
# defaults (1.0) keep weight = clamp(influence/100, 0, 2) until those land.
PATROL_SPAWN_WEIGHT_MIN = 0.0
PATROL_SPAWN_WEIGHT_MAX = 2.0
DEFAULT_BASE_PATROL_INTENSITY = 1.0
DEFAULT_ZONE_MOD = 1.0


def get_sector_influence(
    db: Session,
    sector_id: UUID,
) -> List[SectorFactionInfluence]:
    """READ all per-faction influence rows for one sector, strongest first.

    Pure read; ties broken DETERMINISTICALLY by ``faction_id`` so the ordering
    (and therefore the derived "dominant" faction) never flaps between calls.
    Returns ``[]`` for a sector with no influence rows — the Uncontrolled,
    reproduce-exactly case.
    """
    if sector_id is None:
        return []
    return (
        db.query(SectorFactionInfluence)
        .filter(SectorFactionInfluence.sector_id == sector_id)
        .order_by(
            SectorFactionInfluence.influence_percentage.desc(),
            SectorFactionInfluence.faction_id.asc(),
        )
        .all()
    )


def sector_territory_tier(rows: List[SectorFactionInfluence]) -> str:
    """Classify a sector into the LEG-34 four-tier taxonomy.

    ``rows`` is the output of ``get_sector_influence`` (already sorted
    strongest-first). Returns one of ``"core"``, ``"controlled"``,
    ``"contested"``, ``"uncontrolled"``.
    """
    if not rows:
        return "uncontrolled"
    top = rows[0].influence_percentage or 0.0
    if top <= 0.0:
        return "uncontrolled"
    if top >= TERRITORY_CORE_MIN:
        return "core"
    if top >= TERRITORY_CONTROLLED_MIN:
        return "controlled"
    if top >= TERRITORY_CONTESTED_BAND_MIN:
        secondary = rows[1].influence_percentage if len(rows) > 1 else 0.0
        secondary = secondary or 0.0
        if secondary >= TERRITORY_SECONDARY_PRESENCE_MIN:
            return "contested"
        return "controlled"
    return "uncontrolled"


def compute_patrol_spawn_weight(
    influence_percentage: float,
    base_patrol_intensity: float = DEFAULT_BASE_PATROL_INTENSITY,
    zone_mod: float = DEFAULT_ZONE_MOD,
) -> float:
    """Derive ``patrol_spawn_weight`` per gameplay.md (LEG-34 formula)."""
    frac = max(0.0, min(100.0, float(influence_percentage))) / 100.0
    raw = frac * float(base_patrol_intensity) * float(zone_mod)
    return max(PATROL_SPAWN_WEIGHT_MIN, min(PATROL_SPAWN_WEIGHT_MAX, raw))


def apply_patrol_spawn_weight(influence: SectorFactionInfluence) -> float:
    """Write derived patrol_spawn_weight onto a row (flush left to caller)."""
    weight = compute_patrol_spawn_weight(influence.influence_percentage or 0.0)
    influence.patrol_spawn_weight = weight
    return weight


def max_patrol_spawn_weight_for_sector(db: Session, sector_uuid: UUID) -> Optional[float]:
    """Highest stored patrol_spawn_weight among factions in a sector.

    Returns ``None`` when the sector has no influence rows so Loop B can keep
    pre-LEG-INI-05 fill cadence (reproduce-exactly for unseeded sectors).
    Returns ``0.0`` when rows exist but every weight is zero (skip fills).
    """
    rows = get_sector_influence(db, sector_uuid)
    if not rows:
        return None
    return max(float(r.patrol_spawn_weight or 0.0) for r in rows)


# LEG-65 / DATA_MODELS/gameplay.md § Daily decay sweep (provisional numbers).
SECTOR_INFLUENCE_IDLE_UTC_DAYS = 3
SECTOR_INFLUENCE_DECAY_PER_IDLE_DAY = 0.5


def _sfi_activity_clock(row: SectorFactionInfluence) -> Optional[datetime]:
    """Prefer ``last_action_at``; fall back to ``updated_at`` for pre-column rows."""
    ts = row.last_action_at or row.updated_at
    if ts is None:
        return None
    if ts.tzinfo is None:
        return ts.replace(tzinfo=timezone.utc)
    return ts


def sector_influence_is_idle(
    row: SectorFactionInfluence,
    now: Optional[datetime] = None,
) -> bool:
    """True when the activity clock is older than 3 UTC calendar days."""
    clock = _sfi_activity_clock(row)
    if clock is None:
        return False
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    idle_days = (now.astimezone(timezone.utc).date() - clock.astimezone(timezone.utc).date()).days
    return idle_days >= SECTOR_INFLUENCE_IDLE_UTC_DAYS


def apply_sector_influence_daily_decay(
    row: SectorFactionInfluence,
    now: Optional[datetime] = None,
) -> bool:
    """Apply one −0.5 pp idle-day decay tick if the row is idle (LEG-65).

    Does NOT write ``last_action_at`` (decay must not reset the idle clock).
    Refreshes ``patrol_spawn_weight`` when influence changes. Rows already at
    0.0 are no-ops. Returns True when influence changed.
    """
    if not sector_influence_is_idle(row, now=now):
        return False
    old = float(row.influence_percentage or 0.0)
    if old <= 0.0:
        return False
    new = max(0.0, old - SECTOR_INFLUENCE_DECAY_PER_IDLE_DAY)
    if new == old:
        return False
    row.influence_percentage = new
    apply_patrol_spawn_weight(row)
    return True


# [NO-CANON] Patrol-versus-pirate spawn multiplier mapping for advisory bias.
# Canon states DIRECTION only; linear mapping keys off dominant influence.
PIRATE_SPAWN_FLOOR = 0.25  # high faction influence suppresses, never zeroes


def sector_spawn_bias(
    db: Session,
    sector_id: UUID,
) -> Dict[str, Any]:
    """READ a sector's influence and derive a patrol-vs-pirate spawn bias.

    Returns a small advisory dict — the NPC spawn layer consults it to TILT
    patrol-versus-pirate weighting without changing canonical roster counts:

        {
          "tier": "core|controlled|contested|uncontrolled",
          "dominant_faction_id": UUID | None,
          "dominant_influence": float,   # 0-100
          "patrol_multiplier": float,    # >= 1.0 as influence rises
          "pirate_multiplier": float,    # <= 1.0 as influence rises (floor)
        }

    Reproduce-exactly: a sector with no influence rows (or 0% dominant) yields
    tier "uncontrolled", patrol x1.0, pirate x1.0 — the pre-existing behavior.
    """
    rows = get_sector_influence(db, sector_id)
    tier = sector_territory_tier(rows)
    if not rows:
        return {
            "tier": tier,
            "dominant_faction_id": None,
            "dominant_influence": 0.0,
            "patrol_multiplier": 1.0,
            "pirate_multiplier": 1.0,
        }
    top = rows[0]
    influence = max(0.0, min(100.0, top.influence_percentage or 0.0))
    frac = influence / 100.0  # 0.0 (neutral) .. 1.0 (Core)
    # Patrols scale UP with influence; pirates scale DOWN toward a floor.
    patrol_multiplier = 1.0 + frac
    pirate_multiplier = max(PIRATE_SPAWN_FLOOR, 1.0 - frac)
    dominant_id = top.faction_id if influence > 0.0 else None
    return {
        "tier": tier,
        "dominant_faction_id": dominant_id,
        "dominant_influence": influence,
        "patrol_multiplier": patrol_multiplier,
        "pirate_multiplier": pirate_multiplier,
    }


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
        
        # Broadcast territory change. There is no scoped "territory" audience
        # (unlike sector/team/region) — every connected client fans out via
        # broadcast_global, same as the other galaxy-wide realtime events.
        await manager.broadcast_global({
            "type": "faction_territory_changed",
            "faction_id": str(faction_id),
            "faction_name": faction.name,
            "sectors": [str(sid) for sid in sector_ids]
        })
        
        return faction