"""Mining service — server-authoritative asteroid harvest + AM claim licenses.

Implements the buildable kernel of ``FEATURES/economy/mining.md`` (canon) and
``audit/design-briefs/mining.md`` (working brief). Greenfield (WO-MINING):
additive only — no existing file is changed by this module.

The two entry points:

* ``MiningService.harvest(ship_id, player_id)`` — one atomic 5-turn harvest in an
  ``ASTEROID_FIELD`` sector, gated by a Mining Laser + (in AM-claimed sectors) an
  Astral Mining Consortium claim license. Yields ``ore`` primarily, with rare
  ``precious_metals`` and trace ``quantum_shards``. Decrements the sector's
  depletion pool, grants commodities to the ship's cargo, spends 5 turns, and
  applies AM faction-reputation deltas — all in ONE transaction under a
  player+ship row lock.
* ``MiningService.purchase_license(ship_id, player_id)`` — buy/renew a 24-hour
  AM claim license for the player's current sector at ``500 cr × richness_tier``.

Numbers (yield matrix, license fee table, rep deltas) are copied VERBATIM from
canon — see the matrix constants below. RNG is ``secrets``-backed (canon
§ Anti-cheat: cryptographically secure rolls).

LOCK ORDER (canon § Anti-cheat + WO frozen contract): lock the player row then
the ship row ``FOR UPDATE`` (mirroring ShipUpgradeService._get_ship_and_player),
``regenerate_turns`` then ``spend_turns`` INSIDE the lock, faction rep via the
sync ``apply_faction_rep_delta`` helper in the SAME transaction. The route owns
the commit; this service flushes only.
"""

import logging
import secrets
import uuid
from datetime import datetime, timedelta
from typing import Any, Dict, Optional, Tuple

from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from src.models.player import Player
from src.models.ship import Ship, ShipStatus, effective_cargo_capacity
from src.models.sector import Sector, SectorType
from src.models.faction import FactionType
from src.models.claim_license import ClaimLicense
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

# § Output — precious_metals rare-drop: 5% base + 2% per laser level, cap 11%.
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

# § asteroid_richness derivation — resource_regeneration → tier mapping (canon
# "asteroid_richness derivation" table). Used by the lazy backfill when a sector
# predates the asteroid_richness JSONB key.
_RICHNESS_TIER_NAMES = {1: "depleted", 3: "moderate", 4: "rich", 5: "abundant"}

# § Asteroid depletion — yield modifier by fraction of the pool consumed (canon
# "Asteroid depletion" table). Pool size scales with tier: tier × 100 (canon
# § frozen contract (D); canon "tier 1 = 100 units, tier 5 = 500 units").
DEPLETION_POOL_PER_TIER = 100


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

    # ------------------------------------------------------------------
    # Harvest
    # ------------------------------------------------------------------
    def harvest(self, ship_id: uuid.UUID, player_id: uuid.UUID) -> Dict[str, Any]:
        """Resolve ONE atomic asteroid harvest. Returns the frozen-contract (B)
        shape::

            {success, reason, ore, precious_metals, quantum_shards, turns_spent,
             depletion_state, am_rep_delta, remaining_turns}

        Resolution order (canon § Resolution + brief §1.4):
          lock player+ship → precondition gates → set MINING → regenerate_turns →
          affordability (turns) → backfill richness/pool → roll ore band
          (richness_tier × laser_level) → ×laser efficiency → ×depletion modifier
          → 1-ore hard floor → roll precious_metals → roll quantum_shards →
          decrement depletion_pool → grant to cargo → spend_turns(5) →
          MINING→IN_SPACE → AM rep deltas → flush.
        """
        empty = {
            "ore": 0,
            "precious_metals": 0,
            "quantum_shards": 0,
            "turns_spent": 0,
            "depletion_state": {},
            "am_rep_delta": 0,
        }

        player, ship, reason = self._lock_player_and_ship(ship_id, player_id)
        if reason:
            return {"success": False, "reason": reason, "remaining_turns": 0, **empty}

        # --- precondition gates -----------------------------------------
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

        # Undocked: a docked ship cannot mine (canon precondition 3).
        if getattr(player, "is_docked", False) or ship.status == ShipStatus.DOCKED:
            return {
                "success": False,
                "reason": "must_be_undocked",
                "remaining_turns": player.turns or 0,
                **empty,
            }

        # Cargo capacity: need >= 1 free unit (canon precondition 5). The cargo
        # JSONB convention (matches trading.py) is {'used','capacity','contents'}
        # with a 50-unit default when 'capacity' is absent. There is NO
        # Ship.max_cargo column — max_cargo lives on ShipSpecification — so the
        # capacity must come from the JSONB (or the 50 default), never ship.*.
        # The ceiling MUST honor the Cargo-Hold ship-mod bonus, so read the
        # effective (post-bonus) capacity, not the raw base (ship.py:166).
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

        # --- set MINING status (momentary; reset to IN_SPACE before return) ----
        ship.status = ShipStatus.MINING

        # --- turns: regenerate (frozen hook) INSIDE the lock, then check afford -
        regenerate_turns(self.db, player)
        if (player.turns or 0) < HARVEST_TURN_COST:
            ship.status = ShipStatus.IN_SPACE
            return {
                "success": False,
                "reason": "insufficient_turns",
                "remaining_turns": player.turns or 0,
                **empty,
            }

        # --- richness + depletion (lazy backfill if absent) -------------
        richness = self._ensure_asteroid_richness(sector)
        tier = int(richness.get("richness_tier", 3))
        tier = max(1, min(5, tier))
        resources = sector.resources if isinstance(sector.resources, dict) else {}
        pool_size = tier * DEPLETION_POOL_PER_TIER
        depletion_pool = resources.get("depletion_pool", pool_size)
        if not isinstance(depletion_pool, int):
            depletion_pool = pool_size

        # --- license gate (AM-claimed sectors) -------------------------
        am_claimed = self._is_am_claimed(sector)
        has_license = False
        if am_claimed:
            license_row = self._find_active_license(player_id, sector)
            has_license = license_row is not None

        # --- ore band roll: richness_tier × laser_level ----------------
        laser_col = max(0, min(3, laser_level))
        lo, hi = _YIELD_MATRIX[tier][laser_col]
        base_ore = _RNG.randint(lo, hi)

        # ×laser mining_efficiency multiplier (canon § Resolution step 4).
        efficiency = self._laser_efficiency_multiplier(laser_col)

        # ×depletion modifier (canon § Asteroid depletion). The fraction consumed
        # is measured against the tier's full pool size.
        consumed = max(0, pool_size - depletion_pool)
        consumed_fraction = (consumed / pool_size) if pool_size > 0 else 0.0
        depletion_mod = _depletion_yield_modifier(consumed_fraction)

        ore = int(base_ore * efficiency * depletion_mod)
        # 1-ore hard floor (canon § Asteroid depletion: "at least 1 ore per
        # attempt"). When the floor fires the pool is left unchanged for that
        # attempt (canon: "the depletion pool is unchanged for that attempt").
        floor_fired = False
        if ore < 1:
            ore = 1
            floor_fired = True

        # Clamp ore to available free cargo so we never overfill (precious_metals
        # / quantum_shards also draw from the same cargo space).
        ore = max(1, min(ore, free_cargo))
        free_after_ore = free_cargo - ore

        # --- precious_metals rare drop: 5% + 2%/laser_level, cap 11% ----
        precious_metals = 0
        if free_after_ore > 0:
            pm_rate = min(
                PRECIOUS_METALS_CAP,
                PRECIOUS_METALS_BASE_RATE + PRECIOUS_METALS_PER_LEVEL * laser_col,
            )
            if _RNG.random() < pm_rate:
                pm = _RNG.randint(*PRECIOUS_METALS_YIELD)
                precious_metals = min(pm, free_after_ore)
        free_after_pm = free_after_ore - precious_metals

        # --- quantum_shards trace drop: 1%, only deep asteroids & laser >= 2 ----
        quantum_shards = 0
        if (
            free_after_pm > 0
            and resources.get("has_deep_asteroids", False)
            and laser_col >= QUANTUM_SHARDS_MIN_LASER_LEVEL
            and _RNG.random() < QUANTUM_SHARDS_RATE
        ):
            quantum_shards = 1  # § Output: "Yield 1 shard per drop."

        # --- decrement depletion_pool by ore yield (NOT pm/shards; canon
        # § Asteroid depletion: "decremented by the rolled ore yield; precious
        # metals and quantum shards do not count"). Floor-fired attempts leave the
        # pool unchanged. Never below 0. ----------------------------------------
        if not floor_fired:
            depletion_pool = max(0, depletion_pool - ore)
            resources["depletion_pool"] = depletion_pool
            sector.resources = resources
            flag_modified(sector, "resources")

        # --- grant commodities to cargo (mirror trading.py cargo structure) ----
        contents = cargo.get("contents", {})
        if not isinstance(contents, dict):
            contents = {}
        granted_total = ore + precious_metals + quantum_shards
        contents["ore"] = contents.get("ore", 0) + ore
        if precious_metals:
            contents["precious_metals"] = contents.get("precious_metals", 0) + precious_metals
        if quantum_shards:
            contents["quantum_shards"] = contents.get("quantum_shards", 0) + quantum_shards
        cargo["contents"] = contents
        cargo["used"] = cargo_used + granted_total
        if "capacity" not in cargo:
            cargo["capacity"] = cargo_capacity
        ship.cargo = cargo
        flag_modified(ship, "cargo")

        # --- spend turns (affordability already checked) ----------------
        spend_turns(player, HARVEST_TURN_COST)

        # --- MINING → IN_SPACE (momentary status) -----------------------
        ship.status = ShipStatus.IN_SPACE

        # --- AM faction reputation (same transaction; sync flush-only helper) ---
        am_rep_delta = 0
        if am_claimed:
            if has_license:
                # Licensed: base +1 stacks with the +1 licensed bonus → +2.
                am_rep_delta = AM_REP_BASE + AM_REP_LICENSED_BONUS
            else:
                # Unlicensed extraction in AM space: −10 (canon; the base +1 tick
                # is overridden by the penalty per canon's per-action table).
                am_rep_delta = AM_REP_UNLICENSED
        else:
            # Unclaimed asteroid sector: base +1 AM tick (Mining Laser equipped).
            am_rep_delta = AM_REP_BASE

        if am_rep_delta != 0:
            apply_faction_rep_delta(
                self.db,
                player_id,
                FactionType.MINING,
                am_rep_delta,
                reason="mining_harvest",
            )

        self.db.flush()

        depletion_state = {
            "depletion_pool": depletion_pool,
            "pool_size": pool_size,
            "consumed_fraction": round(consumed_fraction, 4),
            "yield_modifier": depletion_mod,
            "richness_tier": tier,
            "floored": floor_fired,
        }

        logger.info(
            "Player %s harvested sector %s (tier %d, laser L%d): ore=%d pm=%d qs=%d "
            "am_rep=%+d turns_left=%d",
            player_id, sector.sector_id, tier, laser_col, ore, precious_metals,
            quantum_shards, am_rep_delta, player.turns or 0,
        )

        return {
            "success": True,
            "reason": None,
            "ore": ore,
            "precious_metals": precious_metals,
            "quantum_shards": quantum_shards,
            "turns_spent": HARVEST_TURN_COST,
            "depletion_state": depletion_state,
            "am_rep_delta": am_rep_delta,
            "remaining_turns": player.turns or 0,
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
