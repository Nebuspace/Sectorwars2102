"""Mining service — server-authoritative asteroid harvest + AM claim licenses.

Implements the buildable kernel of ``FEATURES/economy/mining.md`` (canon).
WO-MINING-ASYNC-HARVEST: harvest is start → PENDING row → scheduler resolve
(or interrupt), so ``Ship.status = MINING`` spans requests for PvP.

Entry points:

* ``MiningService.harvest(ship_id, player_id)`` — start an async harvest:
  gates, prepaid 5 turns, ``Ship.status = MINING``, insert
  ``MiningHarvest`` PENDING with ``resolves_at``. Yields apply on resolve.
* ``MiningService.resolve_harvest(harvest_id)`` — complete a due PENDING
  row (yield roll, cargo, depletion, AM rep, MINING→IN_SPACE).
* ``MiningService.resolve_due_harvests()`` — scheduler sweep helper.
* ``MiningService.interrupt_harvest(...)`` — cancel + 50% turn refund
  (kernel for WO-MINING-PVP-INTERRUPT).
* ``MiningService.purchase_license(...)`` — unchanged AM claim license buy.

LOCK ORDER: player → ship ``FOR UPDATE``; harvest row locked on resolve/
interrupt. Route owns commit; this service flushes only on player paths.
"""

import logging
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from src.models.player import Player
from src.models.ship import Ship, ShipStatus, effective_cargo_capacity
from src.models.sector import Sector, SectorType
from src.models.faction import FactionType
from src.models.claim_license import ClaimLicense
from src.models.mining_harvest import MiningHarvest, MiningHarvestStatus
from src.services.faction_service import apply_faction_rep_delta
from src.services.turn_service import regenerate_turns, spend_turns

logger = logging.getLogger(__name__)

# Cryptographically secure RNG — canon § Anti-cheat ("Yield rolls use a
# cryptographically secure RNG"). Module-level instance reused per the stdlib
# guidance for SystemRandom.
_RNG = secrets.SystemRandom()

# ---------------------------------------------------------------------------
# Canon constants (copied VERBATIM from FEATURES/economy/mining.md).
# ---------------------------------------------------------------------------

# § Harvest action — turn cost.
HARVEST_TURN_COST = 5

# Interruptible MINING window before the scheduler may complete the row.
# Canon has no real-time cooldown (turn cost is the throttle); this duration
# exists solely so peers can attack mid-harvest (mining.md § PvP). Provisional
# until a DECISION pins a different number — 30s + 60s tick ≈ ≤90s worst case.
HARVEST_WINDOW_SECONDS = 30

# AM faction snake-code — matches Sector.controlling_faction / FactionType.MINING
# (ADR-0033; canon § Astral Mining Consortium claim licenses).
AM_FACTION_CODE = "astral_mining_consortium"

# § Yield matrix — base ore band by (richness_tier, laser_level). The (lo, hi)
# pair is the inclusive band rolled per harvest. Copied VERBATIM from canon's
# "Yield matrix" table (richness tier rows 1-5 × Laser L0/L1/L2/L3 columns).
# Tier 2 and 4 rows are also present in the table; tier 0 is impossible.
_YIELD_MATRIX: Dict[int, Dict[int, Tuple[int, int]]] = {
    1: {0: (2, 4), 1: (3, 5), 2: (3, 6), 3: (4, 8)},
    2: {0: (4, 8), 1: (5, 10), 2: (6, 12), 3: (8, 16)},
    3: {0: (6, 12), 1: (8, 15), 2: (9, 18), 3: (12, 24)},
    4: {0: (10, 18), 1: (13, 23), 2: (15, 27), 3: (20, 36)},
    5: {0: (15, 25), 1: (19, 31), 2: (23, 38), 3: (30, 50)},
}

# § License cost by richness tier (canon "License cost" table): 500 cr × tier.
# Stored as the canonical per-tier fee so it reads VERBATIM against the doc.
_LICENSE_FEE_BY_TIER: Dict[int, int] = {
    1: 500,
    2: 1000,
    3: 1500,
    4: 2000,
    5: 2500,
}
LICENSE_COST_PER_TIER = 500  # § License cost: "500 cr × richness_tier"
LICENSE_DURATION_HOURS = 24  # § License model: "24 real-time hours per purchase"
LICENSE_RENEWAL_FACTOR = 0.8  # § License cost: renewal at 80% of base (400 × tier)
# License panel "recently expired" window (LEG-435 / coord pin): one ClaimLicense
# duration — expired_at within the last LICENSE_DURATION_HOURS. Not multi-week.
RECENTLY_EXPIRED_LICENSE_HOURS = LICENSE_DURATION_HOURS

# § Output — precious_metals rare-drop: 5% base + 2% per laser level, frozen
# at the Laser L2 value (9%) per ADR-0062 E-F3 vs Laser L3 ruling, 2026-08-04
# (DECISIONS.md adr-0062-ef3-vs-mining-laser-l3) -- Laser L3 grants no further
# precious-metals bump; see the pm_laser_level clamp at its use site. CAP is
# retained as a safety ceiling above the effective L2-frozen rate.
PRECIOUS_METALS_BASE_RATE = 0.05
PRECIOUS_METALS_PER_LEVEL = 0.02
PRECIOUS_METALS_CAP = 0.11
PRECIOUS_METALS_YIELD = (1, 3)  # § Output: "Yield 1–3 units per drop."

# § Output — quantum_shards trace-drop: 1% per harvest, only in has_deep_asteroids
# sectors AND a Mining Laser at level >= 2.
QUANTUM_SHARDS_RATE = 0.01
QUANTUM_SHARDS_MIN_LASER_LEVEL = 2

# § Faction reputation hooks (canon table). AM deltas only — the Frontier
# Coalition +5 hook (canon line "Mine in Frontier-zone unclaimed asteroid") is
# NOT buildable in the kernel: there is no FactionType.FRONTIER / Frontier
# Coalition faction row to apply it to (only FEDERATION/MINING/etc. exist). It is
# deferred to the faction-roster expansion and flagged in the WO report.
AM_REP_BASE = 1            # "+1 / harvest" base tick (all asteroid sectors)
AM_REP_LICENSED_BONUS = 1  # "+1 / harvest" licensed bonus (stacks → +2 total)
AM_REP_UNLICENSED = -10    # "−10 / extraction" unlicensed penalty in AM space
AM_REP_LICENSE_PURCHASE = 15  # "+15 / purchase" single-shot on license buy

# § Repeated unlicensed AM enforcement (mining.md — "trips the AM faction
# enforcement path (NPC patrol response in MILITARY_ZONE overlap clusters)").
# Canon does NOT pin N or the rolling window — these are PROVISIONAL (LEG-119;
# N≤5). Do not invent other game-economy numbers beyond this trigger.
AM_UNLICENSED_ENFORCEMENT_THRESHOLD = 3  # provisional: harvests in window
AM_UNLICENSED_ENFORCEMENT_WINDOW_HOURS = 24  # provisional rolling window
AM_UNLICENSED_OFFENSE_TYPE = "unlicensed_am_mining"

# § asteroid_richness derivation — resource_regeneration → tier mapping (canon
# "asteroid_richness derivation" table). Used by the lazy backfill when a sector
# predates the asteroid_richness JSONB key.
_RICHNESS_TIER_NAMES = {1: "depleted", 3: "moderate", 4: "rich", 5: "abundant"}

# § Asteroid depletion — yield modifier by fraction of the pool consumed (canon
# "Asteroid depletion" table). Pool size scales with tier: tier × 100 (canon
# § frozen contract (D); canon "tier 1 = 100 units, tier 5 = 500 units").
DEPLETION_POOL_PER_TIER = 100
# Real-time auto-replenishment (mining.md § Asteroid depletion) — full restore
# after last depleting harvest, not a gradual drip:
#   Light/Moderate (<50% consumed) → 24h wall-clock
#   Heavy (≥50% consumed) → 7 days wall-clock
DEPLETION_REPLENISH_LIGHT_HOURS = 24
DEPLETION_REPLENISH_HEAVY_HOURS = 24 * 7
DEPLETION_HEAVY_CONSUMED_FRACTION = 0.50
DEPLETION_LAST_HARVEST_AT_KEY = "depletion_last_harvest_at"


def _derive_richness_tier(resource_regeneration: Optional[float]) -> int:
    """Map a sector's ``resource_regeneration`` float to a richness tier per the
    canon "asteroid_richness derivation" table (≥0.9→5, 0.6-0.89→4, 0.3-0.59→3,
    <0.3→1). Tier 2 is not produced by the canon derivation table (the matrix
    still defines it for explicitly-authored richness)."""
    rr = float(resource_regeneration) if resource_regeneration is not None else 1.0
    if rr >= 0.9:
        return 5
    if rr >= 0.6:
        return 4
    if rr >= 0.3:
        return 3
    return 1


def _depletion_yield_modifier(pool_consumed_fraction: float) -> float:
    """Yield multiplier by fraction of the depletion pool already consumed,
    per the canon "Asteroid depletion" table:
        Fresh/Light (<5%) → 1.0×, Moderate (5-50%) → 0.75×, Heavy (50-90%) → 0.5×,
        Exhausted (>90%) → floor (handled separately as the 1-ore floor).
    """
    if pool_consumed_fraction < 0.05:
        return 1.0
    if pool_consumed_fraction <= 0.50:
        return 0.75
    if pool_consumed_fraction <= 0.90:
        return 0.5
    return 0.0  # Exhausted — the 1-ore hard floor takes over in harvest().


def _depletion_replenish_hours(pool_size: int, depletion_pool: int) -> int:
    """Wall-clock hours until a full restore, from current consumed fraction.

    Aligns with ``_depletion_yield_modifier``: Moderate is ≤50% consumed
    (24h restore); Heavy is >50% (7-day restore).
    """
    if pool_size <= 0:
        return DEPLETION_REPLENISH_LIGHT_HOURS
    consumed_fraction = max(0.0, (pool_size - depletion_pool) / pool_size)
    if consumed_fraction > DEPLETION_HEAVY_CONSUMED_FRACTION:
        return DEPLETION_REPLENISH_HEAVY_HOURS
    return DEPLETION_REPLENISH_LIGHT_HOURS


def _parse_depletion_last_harvest_at(raw: Any) -> Optional[datetime]:
    if raw is None:
        return None
    if isinstance(raw, datetime):
        return raw if raw.tzinfo else raw.replace(tzinfo=timezone.utc)
    if isinstance(raw, str):
        try:
            # Accept trailing Z from isoformat().
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return None
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    return None


def _maybe_replenish_depletion_pool(
    resources: Dict[str, Any],
    *,
    tier: int,
    now: Optional[datetime] = None,
) -> bool:
    """Full-restore the depletion pool when the idle window since last depleting
    harvest has elapsed. Mutates ``resources`` in place. Returns True if changed.

    Canon (mining.md § Asteroid depletion): Light/Moderate → 24h after last
    harvest; Heavy → 7 real-time days. Restore is atomic to full pool size —
    not a gradual drip.
    """
    pool_size = tier * DEPLETION_POOL_PER_TIER
    depletion_pool = resources.get("depletion_pool", pool_size)
    if not isinstance(depletion_pool, int):
        depletion_pool = pool_size
    if depletion_pool >= pool_size:
        # Already full — drop a stale timer stamp if present.
        if DEPLETION_LAST_HARVEST_AT_KEY in resources:
            resources.pop(DEPLETION_LAST_HARVEST_AT_KEY, None)
            return True
        return False

    last_at = _parse_depletion_last_harvest_at(
        resources.get(DEPLETION_LAST_HARVEST_AT_KEY)
    )
    if last_at is None:
        return False

    now = now or datetime.now(timezone.utc)
    need = timedelta(hours=_depletion_replenish_hours(pool_size, depletion_pool))
    if now - last_at < need:
        return False

    resources["depletion_pool"] = pool_size
    resources.pop(DEPLETION_LAST_HARVEST_AT_KEY, None)
    return True


class MiningService:
    """Server-authoritative mining: asteroid harvest + AM claim licenses."""

    def __init__(self, db: Session):
        self.db = db

    # ------------------------------------------------------------------
    # Shared lookups
    # ------------------------------------------------------------------
    def _lock_player_and_ship(
        self, ship_id: uuid.UUID, player_id: uuid.UUID
    ) -> Tuple[Optional[Player], Optional[Ship], Optional[str]]:
        """Lock the player row then the ship row ``FOR UPDATE`` (LOCK ORDER —
        mirrors ShipUpgradeService._get_ship_and_player). Returns
        ``(player, ship, reason)`` — reason is a stable code on failure."""
        player = (
            self.db.query(Player)
            .filter(Player.id == player_id)
            .populate_existing()
            .with_for_update()
            .first()
        )
        if not player:
            return None, None, "player_not_found"

        ship = (
            self.db.query(Ship)
            .filter(Ship.id == ship_id)
            .with_for_update()
            .first()
        )
        if not ship:
            return None, None, "ship_not_found"
        if ship.owner_id != player_id:
            return None, None, "not_your_ship"
        if ship.is_destroyed:
            return None, None, "ship_destroyed"
        return player, ship, None

    def _resolve_current_sector(self, player: Player) -> Optional[Sector]:
        """Resolve the player's current sector. ``Player.current_sector_id`` is
        the GLOBAL ``Sector.sector_id`` integer (NOT the UUID PK)."""
        if player.current_sector_id is None:
            return None
        return (
            self.db.query(Sector)
            .filter(Sector.sector_id == player.current_sector_id)
            .first()
        )

    @staticmethod
    def _laser_level(ship: Ship) -> Optional[int]:
        """The ship's installed Mining Laser level, or None if no Mining Laser is
        equipped. The laser lives in ``equipment_slots["mining_laser"]``; the
        ladder stores ``level: int`` inside that slot value (frozen contract (F);
        default 0 for a stock laser)."""
        slots = getattr(ship, "equipment_slots", None) or {}
        slot = slots.get("mining_laser")
        if slot is None:
            return None
        if isinstance(slot, dict):
            try:
                return int(slot.get("level", 0))
            except (TypeError, ValueError):
                return 0
        # Legacy/non-dict slot value — treat as a stock (level 0) laser.
        return 0

    @staticmethod
    def _laser_efficiency_multiplier(laser_level: int) -> float:
        """Mining Laser ``mining_efficiency`` multiplier by upgrade level (canon
        § Mining Laser upgrade ladder: L0 1.0×, L1 1.25×, L2 1.5×, L3 2.0×)."""
        return {0: 1.0, 1: 1.25, 2: 1.5, 3: 2.0}.get(laser_level, 1.0)

    def _ensure_asteroid_richness(self, sector: Sector) -> Dict[str, Any]:
        """LAZY BACKFILL (frozen contract (D)): read ``resources.asteroid_richness``
        and ``resources.depletion_pool``, deriving + persisting them from
        ``Sector.resource_regeneration`` if the keys are absent so the kernel
        works on sectors generated before the mining JSONB extensions existed.

        Returns the (possibly newly-derived) asteroid_richness sub-object. Mutates
        ``sector.resources`` in place + flags the JSONB modified when it backfills;
        the caller owns the commit.
        """
        resources = sector.resources if isinstance(sector.resources, dict) else {}
        mutated = False

        richness = resources.get("asteroid_richness")
        if not isinstance(richness, dict) or "richness_tier" not in richness:
            tier = _derive_richness_tier(sector.resource_regeneration)
            band = _YIELD_MATRIX[tier][0]  # L0 band is the stored canonical band
            richness = {
                "richness": _RICHNESS_TIER_NAMES.get(tier, "moderate"),
                "richness_tier": tier,
                "yield_band": [band[0], band[1]],
                "harvest_cooldown_hours": 24 if tier == 1 else None,
            }
            resources["asteroid_richness"] = richness
            mutated = True

        tier = int(richness.get("richness_tier", 3))

        if "depletion_pool" not in resources or not isinstance(
            resources.get("depletion_pool"), int
        ):
            # Pool size = richness_tier × 100 (frozen contract (D); canon
            # § Asteroid depletion "tier 1 = 100 units, tier 5 = 500 units").
            resources["depletion_pool"] = tier * DEPLETION_POOL_PER_TIER
            mutated = True

        if "has_deep_asteroids" not in resources:
            # Default false — only worldgen/import flips deep-asteroid sectors.
            resources["has_deep_asteroids"] = False
            mutated = True

        # Real-time auto-replenishment (WO-BUILD-MINING-DEPLETION-POOL-
        # REPLENISH-TIMER): lazy apply so a visit/harvest after the idle
        # window sees a full pool without requiring a separate scheduler.
        if _maybe_replenish_depletion_pool(resources, tier=tier):
            mutated = True

        if mutated:
            sector.resources = resources
            flag_modified(sector, "resources")

        return richness

    def _find_active_license(
        self, player_id: uuid.UUID, sector: Sector
    ) -> Optional[ClaimLicense]:
        """The player's currently-active claim license for this sector, or None.
        Matches by the compound (player, region, sector_number) identity and
        checks the server-clock expiry."""
        now = datetime.utcnow()
        return (
            self.db.query(ClaimLicense)
            .filter(
                ClaimLicense.player_id == player_id,
                ClaimLicense.region_id == sector.region_id,
                ClaimLicense.sector_number == sector.sector_number,
                ClaimLicense.expires_at > now,
            )
            .order_by(ClaimLicense.expires_at.desc())
            .first()
        )

    @staticmethod
    def _is_am_claimed(sector: Sector) -> bool:
        """True when the sector is controlled by the Astral Mining Consortium
        (canon: ``Sector.controlling_faction == "astral_mining_consortium"``)."""
        return sector.controlling_faction == AM_FACTION_CODE

    def count_recent_unlicensed_am_harvests(
        self,
        player_id: uuid.UUID,
        *,
        now: Optional[datetime] = None,
        window_hours: int = AM_UNLICENSED_ENFORCEMENT_WINDOW_HOURS,
    ) -> int:
        """COMPLETED unlicensed AM harvests for ``player_id`` in the rolling
        window (includes the just-flushed row when called from resolve)."""
        when = now or datetime.now(timezone.utc)
        cutoff = when - timedelta(hours=window_hours)
        return (
            self.db.query(MiningHarvest)
            .filter(
                MiningHarvest.player_id == player_id,
                MiningHarvest.status == MiningHarvestStatus.COMPLETED,
                MiningHarvest.am_claimed.is_(True),
                MiningHarvest.has_license.is_(False),
                MiningHarvest.resolved_at.isnot(None),
                MiningHarvest.resolved_at >= cutoff,
            )
            .count()
        )

    def _maybe_trip_am_unlicensed_enforcement(
        self, player: Player, sector: Sector
    ) -> bool:
        """Dispatch LAW_ENFORCEMENT when the provisional repeated-unlicensed
        threshold is met. Returns True when a patrol path was invoked.

        Primary path: ``npc_engagement_service.route_engagement`` (existing
        PendingEngagement / Marshal-or-Sentinel machinery). Fallback when
        out of jurisdiction or no officers: relocate one ON_DUTY
        LAW_ENFORCEMENT from a same-region ``MILITARY_ZONE`` cluster into
        the harvest sector (canon "MILITARY_ZONE overlap" phrasing).
        Soft-fail: never raises into the harvest transaction.
        """
        try:
            count = self.count_recent_unlicensed_am_harvests(player.id)
            if count < AM_UNLICENSED_ENFORCEMENT_THRESHOLD:
                return False

            from src.services import npc_engagement_service

            engagement = npc_engagement_service.route_engagement(
                self.db,
                player,
                AM_UNLICENSED_OFFENSE_TYPE,
                sector,
            )
            if engagement is not None:
                logger.info(
                    "AM unlicensed enforcement: route_engagement "
                    "offense=%s player=%s sector=%s incidents=%d",
                    AM_UNLICENSED_OFFENSE_TYPE,
                    player.id,
                    sector.sector_id,
                    count,
                )
                return True

            relocated = self._relocate_military_zone_patrol_into(sector)
            if relocated:
                logger.info(
                    "AM unlicensed enforcement: MILITARY_ZONE LE relocated "
                    "into sector %s (player=%s, incidents=%d)",
                    sector.sector_id,
                    player.id,
                    count,
                )
            return relocated
        except Exception:
            logger.exception(
                "AM unlicensed enforcement failed for player %s sector %s "
                "(harvest still completed)",
                getattr(player, "id", None),
                getattr(sector, "sector_id", None),
            )
            return False

    def _relocate_military_zone_patrol_into(self, sector: Sector) -> bool:
        """Best-effort: pull one ON_DUTY LAW_ENFORCEMENT from a same-region
        MILITARY_ZONE cluster into ``sector`` via existing relocate machinery."""
        from src.models.cluster import Cluster, ClusterType
        from src.models.npc_character import NPCArchetype, NPCCharacter, NPCStatus
        from src.services.npc_movement_service import _relocate_npc

        if sector.region_id is None:
            return False

        candidate = (
            self.db.query(NPCCharacter)
            .join(Sector, Sector.sector_id == NPCCharacter.current_sector_id)
            .join(Cluster, Cluster.id == Sector.cluster_id)
            .filter(
                NPCCharacter.archetype == NPCArchetype.LAW_ENFORCEMENT,
                NPCCharacter.status == NPCStatus.ON_DUTY,
                Sector.region_id == sector.region_id,
                Cluster.type == ClusterType.MILITARY_ZONE,
                NPCCharacter.current_sector_id != sector.sector_id,
            )
            .first()
        )
        if candidate is None:
            return False
        return bool(_relocate_npc(self.db, candidate, sector.sector_id))

    # ------------------------------------------------------------------
    # Harvest — start (async) / resolve / interrupt
    # ------------------------------------------------------------------
    def harvest(self, ship_id: uuid.UUID, player_id: uuid.UUID) -> Dict[str, Any]:
        """Start an interruptible asteroid harvest (PENDING until resolve).

        Returns the frozen-contract shape with yields still zero while
        ``status == "in_progress"``::

            {success, reason, status, harvest_id, resolves_at,
             ore, precious_metals, quantum_shards, turns_spent,
             depletion_state, am_rep_delta, remaining_turns}

        Start order: lock player+ship → gates → regenerate_turns → afford →
        spend_turns(5) → MINING → insert MiningHarvest PENDING → flush.
        Yields / depletion / AM rep land on ``resolve_harvest``.
        """
        empty = {
            "ore": 0,
            "precious_metals": 0,
            "quantum_shards": 0,
            "turns_spent": 0,
            "depletion_state": {},
            "am_rep_delta": 0,
            "status": None,
            "harvest_id": None,
            "resolves_at": None,
        }

        player, ship, reason = self._lock_player_and_ship(ship_id, player_id)
        if reason:
            return {"success": False, "reason": reason, "remaining_turns": 0, **empty}

        # Reject a second in-flight harvest on this hull.
        existing = (
            self.db.query(MiningHarvest)
            .filter(
                MiningHarvest.ship_id == ship.id,
                MiningHarvest.status == MiningHarvestStatus.PENDING,
            )
            .with_for_update()
            .first()
        )
        if existing is not None or ship.status == ShipStatus.MINING:
            return {
                "success": False,
                "reason": "already_mining",
                "remaining_turns": player.turns or 0,
                **empty,
            }

        sector = self._resolve_current_sector(player)
        if sector is None or sector.type != SectorType.ASTEROID_FIELD:
            return {
                "success": False,
                "reason": "not_an_asteroid_field",
                "remaining_turns": player.turns or 0,
                **empty,
            }

        laser_level = self._laser_level(ship)
        if laser_level is None:
            return {
                "success": False,
                "reason": "no_mining_laser",
                "remaining_turns": player.turns or 0,
                **empty,
            }

        if getattr(player, "is_docked", False) or ship.status == ShipStatus.DOCKED:
            return {
                "success": False,
                "reason": "must_be_undocked",
                "remaining_turns": player.turns or 0,
                **empty,
            }

        cargo = ship.cargo if isinstance(ship.cargo, dict) else {}
        cargo_used = cargo.get("used", 0) or 0
        cargo_capacity = effective_cargo_capacity(ship)
        free_cargo = cargo_capacity - cargo_used
        if free_cargo < 1:
            return {
                "success": False,
                "reason": "cargo_full",
                "remaining_turns": player.turns or 0,
                **empty,
            }

        regenerate_turns(self.db, player)
        if (player.turns or 0) < HARVEST_TURN_COST:
            return {
                "success": False,
                "reason": "insufficient_turns",
                "remaining_turns": player.turns or 0,
                **empty,
            }

        richness = self._ensure_asteroid_richness(sector)
        tier = max(1, min(5, int(richness.get("richness_tier", 3))))
        am_claimed = self._is_am_claimed(sector)
        has_license = False
        if am_claimed:
            has_license = self._find_active_license(player_id, sector) is not None

        spend_turns(player, HARVEST_TURN_COST)
        ship.status = ShipStatus.MINING

        now = datetime.now(timezone.utc)
        resolves_at = now + timedelta(seconds=HARVEST_WINDOW_SECONDS)
        row = MiningHarvest(
            id=uuid.uuid4(),
            player_id=player_id,
            ship_id=ship.id,
            sector_id=sector.sector_id,
            region_id=sector.region_id,
            status=MiningHarvestStatus.PENDING,
            turns_spent=HARVEST_TURN_COST,
            laser_level=max(0, min(3, laser_level)),
            richness_tier=tier,
            am_claimed=am_claimed,
            has_license=has_license,
            started_at=now,
            resolves_at=resolves_at,
        )
        self.db.add(row)
        self.db.flush()

        logger.info(
            "Player %s started harvest %s sector %s (tier %d, laser L%d) "
            "resolves_at=%s turns_left=%d",
            player_id,
            row.id,
            sector.sector_id,
            tier,
            row.laser_level,
            resolves_at.isoformat(),
            player.turns or 0,
        )

        return {
            "success": True,
            "reason": None,
            "status": "in_progress",
            "harvest_id": str(row.id),
            "resolves_at": resolves_at.isoformat(),
            "ore": 0,
            "precious_metals": 0,
            "quantum_shards": 0,
            "turns_spent": HARVEST_TURN_COST,
            "depletion_state": {},
            "am_rep_delta": 0,
            "remaining_turns": player.turns or 0,
        }

    def resolve_harvest(self, harvest_id: uuid.UUID) -> Dict[str, Any]:
        """Complete a PENDING harvest: yield roll, cargo, depletion, AM rep."""
        empty = {
            "ore": 0,
            "precious_metals": 0,
            "quantum_shards": 0,
            "turns_spent": 0,
            "depletion_state": {},
            "am_rep_delta": 0,
        }

        # Peek without lock to learn lock-order keys (player → ship → harvest).
        peek = (
            self.db.query(MiningHarvest)
            .filter(MiningHarvest.id == harvest_id)
            .first()
        )
        if peek is None:
            return {"success": False, "reason": "harvest_not_found", **empty}
        if peek.status != MiningHarvestStatus.PENDING:
            return {"success": False, "reason": "harvest_not_pending", **empty}

        player, ship, reason = self._lock_player_and_ship(
            peek.ship_id, peek.player_id
        )
        if reason:
            return {"success": False, "reason": reason, **empty}

        row = (
            self.db.query(MiningHarvest)
            .filter(MiningHarvest.id == harvest_id)
            .with_for_update()
            .first()
        )
        if row is None:
            return {"success": False, "reason": "harvest_not_found", **empty}
        if row.status != MiningHarvestStatus.PENDING:
            return {"success": False, "reason": "harvest_not_pending", **empty}

        sector = (
            self.db.query(Sector)
            .filter(Sector.sector_id == row.sector_id)
            .with_for_update()
            .first()
        )
        if sector is None:
            return self._fail_harvest_row(
                row, ship, "sector_gone", empty
            )

        # Re-check cargo at resolve (hold may have filled mid-flight).
        cargo = ship.cargo if isinstance(ship.cargo, dict) else {}
        cargo_used = cargo.get("used", 0) or 0
        cargo_capacity = effective_cargo_capacity(ship)
        free_cargo = cargo_capacity - cargo_used
        if free_cargo < 1:
            return self._fail_harvest_row(
                row, ship, "cargo_full", empty, refund_turns=True
            )

        self._ensure_asteroid_richness(sector)
        tier = max(1, min(5, int(row.richness_tier)))
        laser_col = max(0, min(3, int(row.laser_level)))
        resources = sector.resources if isinstance(sector.resources, dict) else {}
        pool_size = tier * DEPLETION_POOL_PER_TIER
        depletion_pool = resources.get("depletion_pool", pool_size)
        if not isinstance(depletion_pool, int):
            depletion_pool = pool_size

        lo, hi = _YIELD_MATRIX[tier][laser_col]
        base_ore = _RNG.randint(lo, hi)
        efficiency = self._laser_efficiency_multiplier(laser_col)
        consumed = max(0, pool_size - depletion_pool)
        consumed_fraction = (consumed / pool_size) if pool_size > 0 else 0.0
        depletion_mod = _depletion_yield_modifier(consumed_fraction)

        ore = int(base_ore * efficiency * depletion_mod)
        floor_fired = False
        if ore < 1:
            ore = 1
            floor_fired = True
        ore = max(1, min(ore, free_cargo))
        free_after_ore = free_cargo - ore

        precious_metals = 0
        if free_after_ore > 0:
            # ADR-0062 E-F3 vs mining.md Laser L3 contradiction, ruled
            # 2026-08-04 (DECISIONS.md adr-0062-ef3-vs-mining-laser-l3,
            # Option 2 -- match canon): precious_metals scaling freezes at
            # the L2 value; L3 grants no further precious-metals bump.
            # Ore-efficiency (_laser_efficiency_multiplier) and the
            # quantum_shards L3 gate are untouched by this ruling.
            pm_laser_level = min(laser_col, 2)
            pm_rate = min(
                PRECIOUS_METALS_CAP,
                PRECIOUS_METALS_BASE_RATE + PRECIOUS_METALS_PER_LEVEL * pm_laser_level,
            )
            if _RNG.random() < pm_rate:
                pm = _RNG.randint(*PRECIOUS_METALS_YIELD)
                precious_metals = min(pm, free_after_ore)
        free_after_pm = free_after_ore - precious_metals

        quantum_shards = 0
        if (
            free_after_pm > 0
            and resources.get("has_deep_asteroids", False)
            and laser_col >= QUANTUM_SHARDS_MIN_LASER_LEVEL
            and _RNG.random() < QUANTUM_SHARDS_RATE
        ):
            quantum_shards = 1

        if not floor_fired:
            depletion_pool = max(0, depletion_pool - ore)
            resources["depletion_pool"] = depletion_pool
            # Stamp last depleting harvest for the 24h / 7d full-restore timer.
            resources[DEPLETION_LAST_HARVEST_AT_KEY] = datetime.now(
                timezone.utc
            ).isoformat()
            sector.resources = resources
            flag_modified(sector, "resources")

        contents = cargo.get("contents", {})
        if not isinstance(contents, dict):
            contents = {}
        granted_total = ore + precious_metals + quantum_shards
        contents["ore"] = contents.get("ore", 0) + ore
        if precious_metals:
            contents["precious_metals"] = (
                contents.get("precious_metals", 0) + precious_metals
            )
        if quantum_shards:
            contents["quantum_shards"] = (
                contents.get("quantum_shards", 0) + quantum_shards
            )
        cargo["contents"] = contents
        cargo["used"] = cargo_used + granted_total
        if "capacity" not in cargo:
            cargo["capacity"] = cargo_capacity
        ship.cargo = cargo
        flag_modified(ship, "cargo")

        if ship.status == ShipStatus.MINING:
            ship.status = ShipStatus.IN_SPACE

        am_rep_delta = 0
        if row.am_claimed:
            if row.has_license:
                am_rep_delta = AM_REP_BASE + AM_REP_LICENSED_BONUS
            else:
                am_rep_delta = AM_REP_UNLICENSED
        else:
            am_rep_delta = AM_REP_BASE

        if am_rep_delta != 0:
            apply_faction_rep_delta(
                self.db,
                row.player_id,
                FactionType.MINING,
                am_rep_delta,
                reason="mining_harvest",
            )

        now = datetime.now(timezone.utc)
        row.status = MiningHarvestStatus.COMPLETED
        row.resolved_at = now
        row.ore_yield = ore
        row.precious_metals_yield = precious_metals
        row.quantum_shards_yield = quantum_shards
        row.am_rep_delta = am_rep_delta
        self.db.flush()

        # LEG-119: after the −10 is on the ledger, trip patrol when the
        # provisional repeated-unlicensed threshold is crossed. Soft-fail —
        # enforcement must never void a completed harvest.
        if row.am_claimed and not row.has_license:
            self._maybe_trip_am_unlicensed_enforcement(player, sector)

        depletion_state = {
            "depletion_pool": depletion_pool,
            "pool_size": pool_size,
            "consumed_fraction": round(consumed_fraction, 4),
            "yield_modifier": depletion_mod,
            "richness_tier": tier,
            "floored": floor_fired,
        }

        logger.info(
            "Harvest %s completed sector %s: ore=%d pm=%d qs=%d am_rep=%+d",
            row.id,
            row.sector_id,
            ore,
            precious_metals,
            quantum_shards,
            am_rep_delta,
        )

        return {
            "success": True,
            "reason": None,
            "status": "completed",
            "harvest_id": str(row.id),
            "ore": ore,
            "precious_metals": precious_metals,
            "quantum_shards": quantum_shards,
            "turns_spent": row.turns_spent,
            "depletion_state": depletion_state,
            "am_rep_delta": am_rep_delta,
            "remaining_turns": player.turns or 0,
            "player_id": str(row.player_id),
        }

    def interrupt_pending_for_ship(
        self,
        ship_id: uuid.UUID,
        *,
        reason_code: str = "pvp_attack",
        leave_in_combat: bool = True,
    ) -> Optional[Dict[str, Any]]:
        """Cancel the ship's PENDING harvest if one exists (WO-MINING-PVP-INTERRUPT).

        Returns the ``interrupt_harvest`` result, or ``None`` when no PENDING
        row exists (no-op for non-mining defenders). Callers that already hold
        player/ship row locks (combat) may invoke this safely — Postgres
        re-acquires the same locks in-transaction.
        """
        pending = (
            self.db.query(MiningHarvest)
            .filter(
                MiningHarvest.ship_id == ship_id,
                MiningHarvest.status == MiningHarvestStatus.PENDING,
            )
            .first()
        )
        if pending is None:
            return None
        return self.interrupt_harvest(
            pending.id,
            reason_code=reason_code,
            leave_in_combat=leave_in_combat,
        )

    def interrupt_harvest(
        self,
        harvest_id: uuid.UUID,
        *,
        reason_code: str = "interrupted",
        leave_in_combat: bool = False,
    ) -> Dict[str, Any]:
        """Cancel a PENDING harvest with 50% turn refund (mining.md § PvP).

        Kernel for WO-MINING-PVP-INTERRUPT — combat/evacuation callers wire in.
        """
        peek = (
            self.db.query(MiningHarvest)
            .filter(MiningHarvest.id == harvest_id)
            .first()
        )
        if peek is None:
            return {"success": False, "reason": "harvest_not_found"}
        if peek.status != MiningHarvestStatus.PENDING:
            return {"success": False, "reason": "harvest_not_pending"}

        player, ship, lock_reason = self._lock_player_and_ship(
            peek.ship_id, peek.player_id
        )
        if lock_reason:
            return {"success": False, "reason": lock_reason}

        row = (
            self.db.query(MiningHarvest)
            .filter(MiningHarvest.id == harvest_id)
            .with_for_update()
            .first()
        )
        if row is None:
            return {"success": False, "reason": "harvest_not_found"}
        if row.status != MiningHarvestStatus.PENDING:
            return {"success": False, "reason": "harvest_not_pending"}

        refund = row.turns_spent // 2  # 50% of prepaid 5 → 2 turns
        if player is not None and refund > 0:
            player.turns = (player.turns or 0) + refund

        if ship is not None and ship.status == ShipStatus.MINING:
            ship.status = (
                ShipStatus.IN_COMBAT if leave_in_combat else ShipStatus.IN_SPACE
            )

        row.status = MiningHarvestStatus.INTERRUPTED
        row.resolved_at = datetime.now(timezone.utc)
        row.terminal_reason = reason_code
        self.db.flush()

        logger.info(
            "Harvest %s interrupted (%s); refunded %d turns",
            row.id,
            reason_code,
            refund,
        )
        return {
            "success": True,
            "reason": None,
            "status": "interrupted",
            "harvest_id": str(row.id),
            "turns_refunded": refund,
            "remaining_turns": (player.turns or 0) if player else 0,
            "player_id": str(row.player_id),
        }

    def resolve_due_harvests(
        self, now: Optional[datetime] = None
    ) -> List[Dict[str, Any]]:
        """Complete every PENDING harvest whose ``resolves_at`` has elapsed."""
        when = now or datetime.now(timezone.utc)
        due_ids = [
            hid
            for (hid,) in (
                self.db.query(MiningHarvest.id)
                .filter(
                    MiningHarvest.status == MiningHarvestStatus.PENDING,
                    MiningHarvest.resolves_at <= when,
                )
                .order_by(MiningHarvest.resolves_at.asc())
                .all()
            )
        ]
        results: List[Dict[str, Any]] = []
        for hid in due_ids:
            # Fresh lock per row so a mid-sweep interrupt can win.
            results.append(self.resolve_harvest(hid))
        return results

    def _fail_harvest_row(
        self,
        row: MiningHarvest,
        ship: Optional[Ship],
        reason_code: str,
        empty: Dict[str, Any],
        *,
        refund_turns: bool = False,
    ) -> Dict[str, Any]:
        """Terminal-fail a PENDING row without granting commodities."""
        if refund_turns:
            player = (
                self.db.query(Player)
                .filter(Player.id == row.player_id)
                .with_for_update()
                .first()
            )
            if player is not None:
                player.turns = (player.turns or 0) + (row.turns_spent // 2)
        if ship is not None and ship.status == ShipStatus.MINING:
            ship.status = ShipStatus.IN_SPACE
        row.status = MiningHarvestStatus.CANCELLED
        row.resolved_at = datetime.now(timezone.utc)
        row.terminal_reason = reason_code
        self.db.flush()
        return {"success": False, "reason": reason_code, **empty}

    # ------------------------------------------------------------------
    # License list (active + recently expired)
    # ------------------------------------------------------------------
    def list_player_licenses(self, player_id: uuid.UUID) -> Dict[str, Any]:
        """Owner-scoped ClaimLicense rows for the license panel.

        Returns active licenses plus those whose ``expires_at`` falls within
        the last ``RECENTLY_EXPIRED_LICENSE_HOURS`` (24h — one ClaimLicense
        duration; FEATURES/economy/mining.md license panel / LEG-435).
        Rows expired older than that window are omitted. Never returns another
        player's rows.
        """
        now = datetime.utcnow()
        cutoff = now - timedelta(hours=RECENTLY_EXPIRED_LICENSE_HOURS)
        rows = (
            self.db.query(ClaimLicense)
            .filter(
                ClaimLicense.player_id == player_id,
                ClaimLicense.expires_at > cutoff,
            )
            .order_by(ClaimLicense.expires_at.desc())
            .all()
        )
        items = []
        for row in rows:
            expires_at = row.expires_at
            purchased_at = row.purchased_at
            items.append(
                {
                    "id": str(row.id),
                    "region_id": str(row.region_id) if row.region_id else None,
                    "sector_number": int(row.sector_number),
                    "expires_at": expires_at.isoformat() if expires_at else None,
                    "purchased_at": (
                        purchased_at.isoformat() if purchased_at else None
                    ),
                    "cost_paid_cr": int(row.cost_paid_cr or 0),
                    "is_active": bool(expires_at and expires_at > now),
                }
            )
        return {
            "items": items,
            "total": len(items),
            "recently_expired_window_hours": RECENTLY_EXPIRED_LICENSE_HOURS,
        }

    # ------------------------------------------------------------------
    # License purchase / renewal
    # ------------------------------------------------------------------
    def purchase_license(
        self, ship_id: uuid.UUID, player_id: uuid.UUID
    ) -> Dict[str, Any]:
        """Buy (or renew) a 24-hour AM claim license for the player's current
        sector at ``500 cr × richness_tier`` (canon § License cost). Returns the
        frozen-contract (B) shape::

            {success, license_id, expires_at, cost_paid_cr, reason}

        Locks player + ship FOR UPDATE (LOCK ORDER), charges credits, and inserts
        a fresh ClaimLicense row (renewal = a new row; the prior row's expiry is
        honoured for any overlap, canon § License model). +15 AM rep on purchase.
        """
        fail = {"license_id": None, "expires_at": None, "cost_paid_cr": 0}

        player, ship, reason = self._lock_player_and_ship(ship_id, player_id)
        if reason:
            return {"success": False, "reason": reason, **fail}

        sector = self._resolve_current_sector(player)
        if sector is None or sector.type != SectorType.ASTEROID_FIELD:
            return {"success": False, "reason": "not_an_asteroid_field", **fail}

        richness = self._ensure_asteroid_richness(sector)
        tier = max(1, min(5, int(richness.get("richness_tier", 3))))

        # The claim_licenses UNIQUE (player, region, sector_number) triple means
        # at most one persistent row per sector — a repeat purchase RENEWS that
        # row in place (a fresh INSERT would collide on the unique triple and
        # 500 on flush). Lock the row FOR UPDATE so concurrent renews serialize
        # (lock order: player → ship → sector → license).
        existing = (
            self.db.query(ClaimLicense)
            .filter(
                ClaimLicense.player_id == player_id,
                ClaimLicense.region_id == sector.region_id,
                ClaimLicense.sector_number == sector.sector_number,
            )
            .with_for_update()
            .first()
        )
        is_renewal = existing is not None

        # 500 cr × richness_tier on a new license; 80% of that (400 × tier) on a
        # renewal of an existing row (canon § License cost / renewal).
        base = _LICENSE_FEE_BY_TIER.get(tier, LICENSE_COST_PER_TIER * tier)
        cost = int(LICENSE_RENEWAL_FACTOR * base) if is_renewal else base

        if (player.credits or 0) < cost:
            return {"success": False, "reason": "insufficient_credits", **fail}

        now = datetime.utcnow()
        expires_at = now + timedelta(hours=LICENSE_DURATION_HOURS)

        # Charge credits and UPSERT the license: renew the existing triple in
        # place (fresh 24h window) or insert a new row.
        player.credits -= cost
        if is_renewal:
            existing.faction_code = AM_FACTION_CODE
            existing.purchased_at = now
            existing.expires_at = expires_at
            existing.cost_paid_cr = cost
            license_row = existing
        else:
            license_row = ClaimLicense(
                player_id=player_id,
                region_id=sector.region_id,
                sector_number=sector.sector_number,
                faction_code=AM_FACTION_CODE,
                purchased_at=now,
                expires_at=expires_at,
                cost_paid_cr=cost,
            )
            self.db.add(license_row)
        self.db.flush()  # assign the PK before we read license_row.id

        # +15 AM rep on license purchase (canon § Faction reputation hooks).
        apply_faction_rep_delta(
            self.db,
            player_id,
            FactionType.MINING,
            AM_REP_LICENSE_PURCHASE,
            reason="claim_license_purchase",
        )

        logger.info(
            "Player %s purchased AM claim license for sector %s (tier %d) for %d cr; "
            "expires %s",
            player_id, sector.sector_id, tier, cost, expires_at.isoformat(),
        )

        return {
            "success": True,
            "reason": None,
            "license_id": str(license_row.id),
            "expires_at": expires_at.isoformat(),
            "cost_paid_cr": cost,
        }
