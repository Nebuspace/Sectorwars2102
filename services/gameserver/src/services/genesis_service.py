"""
Genesis Device Service - Full implementation of the genesis device system.

Handles device tiers, 48-hour formation timers, probabilistic planet type
selection, rate limiting, and ship cargo capacity for genesis devices.
"""

import os
import random
import logging
from typing import Dict, Any, List, Optional
from uuid import UUID, uuid4
from datetime import datetime, timedelta, timezone
from collections import deque
from sqlalchemy.orm import Session
from sqlalchemy import and_, func, select

from src.models.player import Player
from src.models.planet import Planet, PlanetType, PlanetStatus, player_planets
from src.models.ship import Ship, ShipType
from src.models.sector import Sector, sector_warps
from src.models.region import Region

logger = logging.getLogger(__name__)


def _dispatch_exploration_medals(db: Session, player: Player, context: Dict[str, Any]) -> None:
    """Fire the medals-lane frozen hook
    ``medal_service.check_and_award_exploration_medals(db, player, context)``
    after a genesis deploy (ADR-0028 medal storage). Mirrors the wired combat
    dispatcher (``combat_service._dispatch_combat_medals``).

    ``context`` carries the player's current exploration statistics (here
    ``{planets_created}``). The hook is idempotent on the medals-lane side
    (UNIQUE(player_id, medal_id) + threshold gating); this dispatcher is
    defensive: resolved by ``getattr`` (the hook may be absent in a deployment
    where the medals lane hasn't landed), and any failure is logged and
    swallowed — a medal hiccup must NEVER break a genesis deploy. py_compile-safe:
    no parse-time reference to a not-yet-existing symbol."""
    try:
        import src.services.medal_service as _medal_module
        module_hook = getattr(_medal_module, "check_and_award_exploration_medals", None)
        if callable(module_hook):
            module_hook(db, player, context)
    except Exception as e:  # never let a medal hiccup break genesis
        logger.error("Exploration medal dispatch hook failed: %s", e)

# --- Genesis deploy restrictions (ADR-0088, ratified 2026-06-16) ------------
# Federation reputation gate. ADR-0088 said "level >= 8"; Max set the bar at the
# Heroic tier (personal_reputation >= 250) so it is reachable through normal play
# rather than the near-unreachable top tier (Legendary >= 500). personal_reputation
# is THE Federation-standing scalar (ADR-0084); the per-faction ReputationLevel
# enum (EXALTED) was rejected as a dead/unreachable gate. NOTE: the peaceful rep
# triggers (complete_trade, destroy_pirate_drones) are defined-but-unwired, so
# this bar only becomes comfortably reachable once they are wired (Phase 1).
GENESIS_MIN_REPUTATION = 250
# Deploy must be >= 5 jumps from Federation Space, i.e. NO Federation-Zone sector
# within (5 - 1) = 4 warp jumps of the target sector.
GENESIS_MIN_JUMPS_FROM_FEDERATION = 5
# Deploy must be >= 2 sectors from any other planet, i.e. no planet in the target
# sector or any sector within (2 - 1) = 1 warp jump.
GENESIS_MIN_SECTORS_FROM_PLANET = 2
# A single player may own at most ~25% of a region's planets (the first planet a
# player places in a region is always allowed; see _enforce_deploy_restrictions).
GENESIS_MAX_REGION_OWNERSHIP_FRACTION = 0.25
# Safety cap on BFS exploration so a deploy can never walk the whole galaxy.
_GENESIS_BFS_NODE_CAP = 4000

# Default formation time in hours. Env-configurable so dev can accelerate it
# (the deployment formation timer is wall-clock, NOT GAME_TIME_SCALE-driven).
GENESIS_FORMATION_HOURS = float(os.getenv("GENESIS_FORMATION_HOURS", "48"))

# Device tier definitions
GENESIS_TIERS = {
    # PlanetType.TERRA(N) is reserved for the Capital-welcome planet
    # (ADR-0014) and is never genesis-rollable; the rollable set is
    # OCEANIC / DESERT / ICE / VOLCANIC / MOUNTAINOUS.
    "basic": {
        "cost": 25000,
        "requires_ship_sacrifice": False,
        "planet_type_weights": {
            PlanetType.OCEANIC: 50,
            PlanetType.DESERT: 20,
            PlanetType.ICE: 15,
            PlanetType.VOLCANIC: 10,
            PlanetType.MOUNTAINOUS: 5,
        },
        "habitability_range": (40, 60),
        "resource_richness_range": (0.5, 1.0),
        "size_range": (3, 6),
    },
    "enhanced": {
        "cost": 75000,
        "requires_ship_sacrifice": False,
        "planet_type_weights": {
            PlanetType.OCEANIC: 60,
            PlanetType.DESERT: 20,
            PlanetType.ICE: 10,
            PlanetType.VOLCANIC: 5,
            PlanetType.MOUNTAINOUS: 5,
        },
        "habitability_range": (55, 75),
        "resource_richness_range": (0.8, 1.5),
        "size_range": (4, 7),
    },
    "advanced": {
        "cost": 250000,
        "requires_ship_sacrifice": True,
        "sacrifice_ship_type": ShipType.COLONY_SHIP,
        "planet_type_weights": {
            PlanetType.OCEANIC: 80,
            PlanetType.DESERT: 10,
            PlanetType.ICE: 5,
            PlanetType.VOLCANIC: 3,
            PlanetType.MOUNTAINOUS: 2,
        },
        "habitability_range": (70, 90),
        "resource_richness_range": (1.2, 2.0),
        "size_range": (5, 9),
    },
}

# Genesis device capacity by ship type
GENESIS_CAPACITY_BY_SHIP = {
    ShipType.LIGHT_FREIGHTER: 0,  # ADR/DECISIONS 2026-06-16: aligned to doc + seeder (0); genesis needs a larger hull
    ShipType.CARGO_HAULER: 2,
    ShipType.COLONY_SHIP: 3,
    ShipType.DEFENDER: 3,
    ShipType.CARRIER: 5,
    ShipType.WARP_JUMPER: 1,
}

# Canon device consumption per tier (genesis-devices.md "Formation process" /
# tier matrix): basic spends 1 device, enhanced fuses 3, advanced spends 1
# (plus the sacrificed Colony Ship).
GENESIS_DEVICE_COST = {
    "basic": 1,
    "enhanced": 3,
    "advanced": 1,
}

# Maximum genesis device purchases per week per player
MAX_PURCHASES_PER_WEEK = 3

# Maximum planets per sector
MAX_PLANETS_PER_SECTOR = 5


class GenesisService:
    """Service for the full genesis device system."""

    def __init__(self, db: Session, formation_hours: float = GENESIS_FORMATION_HOURS):
        self.db = db
        self.formation_hours = formation_hours

    # ------------------------------------------------------------------ #
    #  Public API methods
    # ------------------------------------------------------------------ #

    def _bfs_distances(self, start_uuid: UUID, max_jumps: int) -> Dict[UUID, int]:
        """Map each sector UUID within ``max_jumps`` warp-jumps of ``start_uuid``
        to its jump distance, over the bidirectional ``sector_warps`` graph.

        Level-batched BFS (one pair of queries per depth, not per node) and capped
        at ``_GENESIS_BFS_NODE_CAP`` so a deploy can never traverse the whole galaxy.
        Edge model mirrors movement_service: forward source->dest edges plus
        reverse dest->source edges for bidirectional warps.
        """
        distances: Dict[UUID, int] = {start_uuid: 0}
        frontier: List[UUID] = [start_uuid]
        for depth in range(1, max_jumps + 1):
            if not frontier or len(distances) >= _GENESIS_BFS_NODE_CAP:
                break
            fwd = self.db.execute(
                select(sector_warps.c.destination_sector_id)
                .where(sector_warps.c.source_sector_id.in_(frontier))
            ).scalars().all()
            rev = self.db.execute(
                select(sector_warps.c.source_sector_id)
                .where(and_(
                    sector_warps.c.destination_sector_id.in_(frontier),
                    sector_warps.c.is_bidirectional.is_(True),
                ))
            ).scalars().all()
            nxt: List[UUID] = []
            for nbr in list(fwd) + list(rev):
                if nbr not in distances:
                    distances[nbr] = depth
                    nxt.append(nbr)
            frontier = nxt
        return distances

    def _federation_sectors_among(self, sector_uuids) -> set:
        """Return the subset of ``sector_uuids`` that are Federation Space.

        Federation Space (police-forces.md, mirrors npc_engagement_service.
        jurisdiction_of): an entire Terran-Space region, or the first
        FEDERATION_ZONE_FRACTION (33%) of a player region's sectors by region-local
        ``sector_id``. Central Nexus is Sentinel jurisdiction, NOT Federation.
        Batched (a few region-grouped queries) rather than per-sector.
        """
        from src.services.npc_engagement_service import FEDERATION_ZONE_FRACTION

        uuids = list(sector_uuids)
        if not uuids:
            return set()
        rows = self.db.execute(
            select(Sector.id, Sector.sector_id, Sector.region_id).where(Sector.id.in_(uuids))
        ).all()
        region_ids = {r.region_id for r in rows if r.region_id is not None}
        if not region_ids:
            return set()
        regions = {
            reg.id: reg for reg in self.db.execute(
                select(Region).where(Region.id.in_(region_ids))
            ).scalars().all()
        }
        # Per-region (min sector_id, count) for the player-region 33% threshold.
        bounds = {
            rid: (mn, cnt)
            for rid, mn, cnt in self.db.execute(
                select(Sector.region_id, func.min(Sector.sector_id), func.count(Sector.id))
                .where(Sector.region_id.in_(region_ids))
                .group_by(Sector.region_id)
            ).all()
        }
        fed = set()
        for r in rows:
            region = regions.get(r.region_id)
            if region is None:
                continue
            if getattr(region, "is_terran_space", False):
                fed.add(r.id)
                continue
            if getattr(region, "is_central_nexus", False):
                continue  # Sentinel jurisdiction, not Federation
            mn, cnt = bounds.get(r.region_id, (None, 0))
            if mn is not None and cnt and r.sector_id is not None:
                local_position = r.sector_id - mn + 1
                if local_position <= int(FEDERATION_ZONE_FRACTION * cnt):
                    fed.add(r.id)
        return fed

    def _enforce_deploy_restrictions(self, player: Player, sector: Sector) -> None:
        """Enforce the ratified genesis deploy gates (ADR-0088). Raises ValueError
        with a ``genesis_blocked_*`` reason on the first failed gate."""
        # Gate 1 — Federation reputation tier 8 (Legendary, >= 500).
        rep = player.personal_reputation or 0
        if rep < GENESIS_MIN_REPUTATION:
            raise ValueError(
                "genesis_blocked_reputation: Genesis deployment requires Federation "
                f"reputation tier 7 (Heroic, >= {GENESIS_MIN_REPUTATION}); your "
                f"standing is {rep}."
            )

        # One bounded BFS serves both spatial gates.
        max_jumps = max(
            GENESIS_MIN_JUMPS_FROM_FEDERATION - 1,
            GENESIS_MIN_SECTORS_FROM_PLANET - 1,
        )
        distances = self._bfs_distances(sector.id, max_jumps)

        # Gate 2 — >= 5 jumps from Federation Space (no Federation sector within <=4).
        fed_uuids = self._federation_sectors_among(distances.keys())
        fed_dist = [distances[u] for u in fed_uuids if distances[u] < GENESIS_MIN_JUMPS_FROM_FEDERATION]
        if fed_dist:
            raise ValueError(
                "genesis_blocked_proximity: Genesis deployment must be at least "
                f"{GENESIS_MIN_JUMPS_FROM_FEDERATION} jumps from Federation Space "
                f"(nearest Federation sector is {min(fed_dist)} jump(s) away)."
            )

        # Gate 3 — >= 2 sectors from any other planet (no planet within <=1 jump,
        # including the target sector itself).
        spacing_radius = GENESIS_MIN_SECTORS_FROM_PLANET - 1
        near_uuids = [u for u, d in distances.items() if d <= spacing_radius]
        if near_uuids:
            nearby_planets = self.db.execute(
                select(func.count(Planet.id)).where(Planet.sector_uuid.in_(near_uuids))
            ).scalar() or 0
            if nearby_planets > 0:
                raise ValueError(
                    "genesis_blocked_proximity: Genesis deployment must be at least "
                    f"{GENESIS_MIN_SECTORS_FROM_PLANET} sectors from any other planet."
                )

        # Gate 4 — anti-monopoly: <= ~25% of a region's planets per player. The
        # first planet a player places in a region is always allowed (max_allowed
        # floors at 1), so early colonisation is never blocked.
        if sector.region_id is not None:
            # Resolve a planet's region through its sector (Planet.region_id is
            # unreliable — genesis/colonized planets historically left it NULL),
            # so the count is robust regardless of that column.
            total = self.db.execute(
                select(func.count(Planet.id))
                .select_from(Planet)
                .join(Sector, Planet.sector_uuid == Sector.id)
                .where(Sector.region_id == sector.region_id)
            ).scalar() or 0
            owned = self.db.execute(
                select(func.count(Planet.id))
                .select_from(Planet)
                .join(Sector, Planet.sector_uuid == Sector.id)
                .join(player_planets, Planet.id == player_planets.c.planet_id)
                .where(and_(
                    Sector.region_id == sector.region_id,
                    player_planets.c.player_id == player.id,
                ))
            ).scalar() or 0
            total_after = total + 1
            max_allowed = max(1, int(GENESIS_MAX_REGION_OWNERSHIP_FRACTION * total_after))
            if owned + 1 > max_allowed:
                pct = int(GENESIS_MAX_REGION_OWNERSHIP_FRACTION * 100)
                raise ValueError(
                    f"genesis_blocked_monopoly: You already own {owned} of {total} planets in "
                    f"this region; a single player may hold at most ~{pct}% of a region's planets."
                )

    def deploy_genesis_device(
        self,
        player_id: UUID,
        sector_id: int,
        tier: str,
        name: Optional[str] = None,
        registration: str = "registered",
    ) -> Dict[str, Any]:
        """
        Deploy a genesis device to create a new planet.

        Validates credits, rate limits, ship capacity, and sector limits.
        The planet enters a "forming" state for the configured formation period.
        For the advanced tier, the player's colony ship is sacrificed.

        The `registration` argument controls the planet's Colonial Registry
        visibility (FROZEN registry contract): "registered" (default, visible,
        10,000 cr), "clandestine" (hidden from the registry lookup, 60,000 cr),
        or "chartered" (publicly protected; fee scales with reputation and the
        founding grants +25 personal reputation). The registration fee is
        charged ON TOP of the device tier sequence cost.

        Returns deployment result with formation timing information.
        """
        # --- Validate tier ---
        tier = tier.lower()
        if tier not in GENESIS_TIERS:
            raise ValueError(f"Invalid genesis device tier: {tier}. Must be one of: {list(GENESIS_TIERS.keys())}")

        tier_config = GENESIS_TIERS[tier]

        # --- Load player with lock to prevent concurrent purchase race ---
        player = self.db.query(Player).filter(Player.id == player_id).with_for_update().first()
        if not player:
            raise ValueError("Player not found")

        # --- Load player's current ship ---
        if not player.current_ship_id:
            raise ValueError("You must be in a ship to deploy a genesis device")

        ship = self.db.query(Ship).filter(Ship.id == player.current_ship_id).first()
        if not ship:
            raise ValueError("Current ship not found")

        # --- Check player is in the target sector ---
        if player.current_sector_id != sector_id:
            raise ValueError(
                f"You are in sector {player.current_sector_id}, "
                f"but the target sector is {sector_id}"
            )

        # --- Check the player is not docked or landed ---
        if player.is_docked:
            raise ValueError("You must undock from the station before deploying a genesis device")
        if player.is_landed:
            raise ValueError("You must leave the planet before deploying a genesis device")

        # --- Verify sector exists ---
        sector = self.db.query(Sector).filter(Sector.sector_id == sector_id).first()
        if not sector:
            raise ValueError(f"Sector {sector_id} not found")

        # --- Genesis deploy restrictions (ADR-0088): reputation tier, distance
        # from Federation Space, planet spacing, and per-region anti-monopoly. ---
        self._enforce_deploy_restrictions(player, sector)

        # --- Check sector planet limit ---
        existing_planet_count = self.db.query(func.count(Planet.id)).filter(
            Planet.sector_id == sector_id
        ).scalar()
        if existing_planet_count >= MAX_PLANETS_PER_SECTOR:
            raise ValueError(
                f"Sector {sector_id} already has the maximum number of planets ({MAX_PLANETS_PER_SECTOR})"
            )

        # The weekly rate limit is enforced at ACQUISITION (POST /player/genesis/
        # purchase), not here — deploying a device you already own is not a
        # "purchase" (canon: "max 3 device purchases per week"). We still read
        # the count for the informational fields in the response.
        purchases_this_week = self._get_weekly_purchase_count(player)

        # --- Validate registration tier ---
        registration = (registration or "registered").lower()
        if registration not in ("clandestine", "registered", "chartered"):
            raise ValueError(
                f"Invalid registration status: {registration}. "
                "Must be one of: clandestine, registered, chartered"
            )

        # --- Check credits (device tier sequence cost + registration fee) ---
        cost = tier_config["cost"]
        # Registry fees (FROZEN contract): Registered 10,000; Clandestine 60,000;
        # Chartered = 10,000 + 40,000 * (1 - clamp(rep/1000, 0, 1) * 0.75).
        # The player row is already locked above, so personal_reputation is safe
        # to read for the Chartered curve.
        if registration == "clandestine":
            registration_fee = 60000
        elif registration == "chartered":
            rep = player.personal_reputation or 0
            rep_factor = max(0.0, min(1.0, rep / 1000.0))
            registration_fee = int(10000 + 40000 * (1 - rep_factor * 0.75))
        else:  # registered
            registration_fee = 10000

        total_cost = cost + registration_fee
        if player.credits < total_cost:
            raise ValueError(
                f"Insufficient credits. You have {player.credits:,} but need "
                f"{total_cost:,} ({cost:,} for the {tier} sequence + "
                f"{registration_fee:,} {registration} registration fee)"
            )

        # --- Check ship genesis capacity ---
        ship_capacity = self._get_ship_genesis_capacity(ship)
        if ship_capacity <= 0:
            raise ValueError(
                f"Your ship ({ship.type.value}) cannot carry genesis devices"
            )
        # Non-sacrifice tiers consume one loaded genesis device. Require the
        # player to actually carry one (purchased at a genesis dealer) — the
        # old escape hatch let deploy proceed with zero devices and never
        # decremented the count, so a single device could found unlimited
        # planets.
        current_devices_on_ship = ship.genesis_devices or 0
        device_cost = GENESIS_DEVICE_COST.get(tier, 1)
        if not tier_config.get("requires_ship_sacrifice") and current_devices_on_ship < device_cost:
            raise ValueError(
                f"The {tier} genesis sequence needs {device_cost} device"
                f"{'s' if device_cost != 1 else ''}; you have {current_devices_on_ship} loaded. "
                f"Buy more at a genesis dealer (SpaceDock) or choose a lower tier."
            )

        # --- Advanced tier: require colony ship sacrifice ---
        if tier_config.get("requires_ship_sacrifice"):
            required_type = tier_config["sacrifice_ship_type"]
            if ship.type != required_type:
                raise ValueError(
                    f"Advanced genesis deployment requires sacrificing a {required_type.value}. "
                    f"Your current ship is a {ship.type.value}."
                )

        # --- Select random planet type based on tier probabilities ---
        planet_type = self._select_planet_type(tier_config["planet_type_weights"])

        # --- Roll habitability ---
        hab_min, hab_max = tier_config["habitability_range"]
        habitability = random.randint(hab_min, hab_max)

        # --- Roll resource richness ---
        rr_min, rr_max = tier_config["resource_richness_range"]
        resource_richness = round(random.uniform(rr_min, rr_max), 2)

        # --- Roll planet size ---
        size_min, size_max = tier_config["size_range"]
        planet_size = random.randint(size_min, size_max)

        # --- Planet name: honor the player's chosen name, else generate one ---
        chosen = (name or "").strip()
        planet_name = chosen if chosen else self._generate_planet_name(sector_id, planet_type)

        # --- Calculate formation timestamps ---
        now = datetime.now(timezone.utc)
        formation_complete_at = now + timedelta(hours=self.formation_hours)

        # --- Create planet in forming state ---
        planet = Planet(
            name=planet_name,
            auto_name=planet_name,  # ADR-0073: generated default; discoverer may override
            sector_id=sector_id,
            sector_uuid=sector.id,
            region_id=sector.region_id,  # ADR-0088: anti-monopoly counts planets per region
            type=planet_type,
            planet_type=planet_type.value.lower(),
            status=PlanetStatus.TERRAFORMING,
            size=planet_size,
            habitability_score=habitability,
            resource_richness=resource_richness,
            genesis_created=True,
            genesis_tier=tier,
            formation_status="forming",
            formation_started_at=now,
            formation_complete_at=formation_complete_at,
            owner_id=player.id,
            # Start with zero resources until formation completes
            fuel_ore=0,
            organics=0,
            equipment=0,
            colonists=0,
            # Canon dual ceilings (ADR-0035): L1 workforce cap = 1,000;
            # demographic ceiling = habitability_score * 1,000.
            max_colonists=1000,
            population=0,
            max_population=habitability * 1000,
        )

        self.db.add(planet)

        # --- Store the Colonial Registry status (FROZEN contract) ---
        # JSONB dict-reassign pattern (mirrors citadel_service._set_defense_buildings):
        # read active_events, shallow-copy if a dict, set the key, reassign so
        # SQLAlchemy detects the mutation. A freshly-created planet has no
        # active_events yet, so this initializes the dict.
        events = planet.active_events
        if not isinstance(events, dict):
            events = {"legacy_events": events} if events else {}
        events = dict(events)
        events["registration_status"] = registration
        planet.active_events = events

        # --- Deduct credits (tier sequence cost + registration fee) ---
        player.credits -= total_cost

        # Chartering a planet is a public, lawful act: +25 personal reputation
        # (FROZEN contract). adjust_reputation takes a player_id (UUID), not a
        # Player object; the player row is already locked in this txn.
        if registration == "chartered":
            from src.services.personal_reputation_service import PersonalReputationService
            PersonalReputationService(self.db).adjust_reputation(
                player.id, 25, "planet_chartered"
            )

        # --- Consume the tier's loaded genesis devices ---
        # basic = 1, enhanced = 3, advanced = 1 (canon GENESIS_DEVICE_COST).
        ship.genesis_devices = max(0, current_devices_on_ship - device_cost)

        # (Purchases are recorded at acquisition, not at deploy — see the rate-
        # limit note above; deploying a held device is not a purchase.)

        # --- Handle ship sacrifice for advanced tier ---
        sacrifice_info = None
        if tier_config.get("requires_ship_sacrifice"):
            sacrifice_info = {
                "ship_name": ship.name,
                "ship_type": ship.type.value,
            }
            # Eject the pilot into an escape pod via the canon destruction path
            # (creates/relocates the pod, transfers cargo, no insurance payout on
            # a voluntary sacrifice). Replaces the old stub that left the player
            # with current_ship_id = None and no pod.
            from src.services.ship_service import ShipService
            ShipService(self.db).destroy_ship(ship, cause="genesis_sacrifice")
            # Advanced planets form INSTANTLY at Settlement (citadel L2, 5,000
            # colonists, +10% production via the L2 citadel bonus, 4 automated
            # turrets, basic shield generator) — genesis-devices.md "Advanced tier".
            self._complete_formation(planet)
            planet.formation_complete_at = now
            planet.defense_turrets = 4
            planet.defense_shields = 1
            # Register the 4 seeded turrets in the citadel defense-buildings store
            # (Phase 1 / audit fix): previously they lived only in the flat
            # defense_turrets column and never counted as a turret_network in
            # citadel_service. Mirror the JSONB dict-reassign pattern.
            ev = planet.active_events if isinstance(planet.active_events, dict) else {}
            ev = dict(ev)
            buildings = dict(ev.get("defense_buildings", {}))
            buildings["turret_network"] = buildings.get("turret_network", 0) + 4
            ev["defense_buildings"] = buildings
            planet.active_events = ev
            logger.info(
                f"Advanced genesis: Colony Ship '{sacrifice_info['ship_name']}' sacrificed; "
                f"instant Settlement colony on planet {planet.id} by player {player_id}"
            )

        # --- Add planet ownership ---
        self.db.flush()  # Ensure planet.id is assigned

        self.db.execute(
            player_planets.insert().values(
                player_id=player.id,
                planet_id=planet.id,
            )
        )

        # Exploration medal dispatch hook (ADR-0028 / medals lane): a successful
        # genesis-device deploy is the trigger for the Genesis Award
        # (planets_created >= 25, where "created" means genesis-created). The
        # count of this player's genesis_created planets (counted AFTER the
        # planet above is added/flushed) is the player's planets_created
        # statistic. We dispatch BEFORE db.commit() below so the medal-award
        # SAVEPOINT folds into this method's single commit, exactly like the
        # combat medal hook. Best-effort + idempotent on the medals-lane side
        # (UNIQUE(player_id, medal_id) + threshold gating) — a medal hiccup must
        # never break a genesis deploy, and the hook no-ops on every deploy
        # except the one that first reaches 25 planets.
        try:
            genesis_planet_count = (
                self.db.query(func.count(Planet.id))
                .filter(
                    Planet.owner_id == player.id,
                    Planet.genesis_created.is_(True),
                )
                .scalar()
                or 0
            )
            _dispatch_exploration_medals(
                self.db,
                player,
                {"planets_created": genesis_planet_count},
            )
        except Exception as e:
            logger.error("Exploration medal dispatch failed on genesis deploy: %s", e)

        self.db.commit()
        self.db.refresh(planet)

        logger.info(
            f"Genesis device deployed: tier={tier}, sector={sector_id}, "
            f"planet_type={planet_type.value}, habitability={habitability}, "
            f"player={player_id}, formation_hours={self.formation_hours}"
        )

        result = {
            "success": True,
            "planet_id": str(planet.id),
            "planet_name": planet.name,
            "planet_type": planet_type.value,
            "genesis_tier": tier,
            "habitability_score": habitability,
            "resource_richness": resource_richness,
            "size": planet_size,
            # Advanced completes instantly; basic/enhanced form over 48h.
            "formation_status": planet.formation_status,
            "formation_started_at": now.isoformat(),
            "formation_complete_at": formation_complete_at.isoformat(),
            "formation_hours_remaining": 0 if planet.formation_status == "complete" else self.formation_hours,
            # The legacy route maps these to the camelCase keys the client
            # reads (genesisDevicesRemaining / deploymentTime). After a sacrifice
            # the player is in a fresh escape pod with no devices.
            "genesis_devices_remaining": 0 if sacrifice_info else (ship.genesis_devices or 0),
            "deployment_seconds": 0 if planet.formation_status == "complete" else int(self.formation_hours * 3600),
            "credits_spent": total_cost,
            "credits_remaining": player.credits,
            # Colonial Registry outcome (FROZEN registry contract)
            "registration_status": registration,
            "registration_fee": registration_fee,
            "genesis_purchases_this_week": purchases_this_week + 1,
            "genesis_purchases_remaining": MAX_PURCHASES_PER_WEEK - (purchases_this_week + 1),
        }

        if sacrifice_info:
            result["ship_sacrificed"] = sacrifice_info

        return result

    def check_formation_status(self, planet_id: UUID, player_id: UUID) -> Dict[str, Any]:
        """
        Check the formation status of a genesis-created planet.

        Returns time remaining and current status.
        If formation is complete, transitions the planet to usable state.
        """
        planet = self.db.query(Planet).filter(Planet.id == planet_id).first()
        if not planet:
            raise ValueError("Planet not found")

        if not planet.genesis_created:
            raise ValueError("This planet was not created by a genesis device")

        # Verify the player owns the planet
        ownership = self.db.execute(
            player_planets.select().where(
                and_(
                    player_planets.c.player_id == player_id,
                    player_planets.c.planet_id == planet_id,
                )
            )
        ).first()
        if not ownership and planet.owner_id != player_id:
            raise ValueError("You do not own this planet")

        now = datetime.now(timezone.utc)

        # If already complete, return the completed status
        if planet.formation_status == "complete":
            return {
                "planet_id": str(planet.id),
                "planet_name": planet.name,
                "planet_type": planet.type.value,
                "genesis_tier": planet.genesis_tier,
                "formation_status": "complete",
                "formation_started_at": planet.formation_started_at.isoformat() if planet.formation_started_at else None,
                "formation_completed_at": planet.formation_complete_at.isoformat() if planet.formation_complete_at else None,
                "hours_remaining": 0,
                "seconds_remaining": 0,
                "progress_percent": 100.0,
                "is_usable": True,
            }

        # Check if formation should complete
        if planet.formation_complete_at and now >= planet.formation_complete_at:
            # Transition planet to usable state
            self._complete_formation(planet)
            self.db.commit()
            self.db.refresh(planet)

            return {
                "planet_id": str(planet.id),
                "planet_name": planet.name,
                "planet_type": planet.type.value,
                "genesis_tier": planet.genesis_tier,
                "formation_status": "complete",
                "formation_started_at": planet.formation_started_at.isoformat() if planet.formation_started_at else None,
                "formation_completed_at": now.isoformat(),
                "hours_remaining": 0,
                "seconds_remaining": 0,
                "progress_percent": 100.0,
                "is_usable": True,
            }

        # Still forming - calculate remaining time
        if planet.formation_complete_at:
            remaining = planet.formation_complete_at - now
            seconds_remaining = max(0, int(remaining.total_seconds()))
            hours_remaining = round(seconds_remaining / 3600, 2)
        else:
            seconds_remaining = 0
            hours_remaining = 0

        # Calculate progress percentage
        if planet.formation_started_at and planet.formation_complete_at:
            total_duration = (planet.formation_complete_at - planet.formation_started_at).total_seconds()
            elapsed = (now - planet.formation_started_at).total_seconds()
            progress = min(100.0, round((elapsed / total_duration) * 100, 2)) if total_duration > 0 else 0
        else:
            progress = 0

        return {
            "planet_id": str(planet.id),
            "planet_name": planet.name,
            "planet_type": planet.type.value,
            "genesis_tier": planet.genesis_tier,
            "formation_status": "forming",
            "formation_started_at": planet.formation_started_at.isoformat() if planet.formation_started_at else None,
            "formation_complete_at": planet.formation_complete_at.isoformat() if planet.formation_complete_at else None,
            "hours_remaining": hours_remaining,
            "seconds_remaining": seconds_remaining,
            "progress_percent": progress,
            "is_usable": False,
        }

    def complete_due_formations(self, player_id: UUID) -> int:
        """Lazily complete any of the player's genesis planets whose formation
        timer has elapsed. Called on the owned-planets fetch so a colony the
        player founded actually becomes usable when they next check the
        Colonial Registry — without it, formation completion (lazy poll-on-GET)
        was never triggered by any client. Returns the count completed."""
        now = datetime.now(timezone.utc)
        due = (
            self.db.query(Planet)
            .filter(
                Planet.owner_id == player_id,
                Planet.genesis_created == True,  # noqa: E712
                Planet.formation_status == "forming",
                Planet.formation_complete_at.isnot(None),
                Planet.formation_complete_at <= now,
            )
            .all()
        )
        for planet in due:
            self._complete_formation(planet)
        if due:
            self.db.commit()
        return len(due)

    def complete_all_due_formations(self) -> int:
        """Complete EVERY forming genesis planet whose formation timer has
        elapsed, regardless of owner — the periodic (scheduler-driven)
        counterpart to ``complete_due_formations`` (which is lazy and scoped to
        one player's owned-planets fetch).

        Without this, a colony whose owner never re-opens the Colonial Registry
        — or a forming planet that has been abandoned/unowned — would stay
        "forming" forever even after its 48h timer passed, because completion
        only ever settled lazily on a per-player read. This sweep makes the
        formation timer authoritative: a deployed device always finishes.

        Idempotent (only ``forming`` rows past their timer are touched) and safe
        to run repeatedly. Returns the number of planets completed."""
        now = datetime.now(timezone.utc)
        due = (
            self.db.query(Planet)
            .filter(
                Planet.genesis_created == True,  # noqa: E712
                Planet.formation_status == "forming",
                Planet.formation_complete_at.isnot(None),
                Planet.formation_complete_at <= now,
            )
            .all()
        )
        for planet in due:
            self._complete_formation(planet)
        if due:
            self.db.commit()
        return len(due)

    def get_available_purchases(self, player_id: UUID) -> Dict[str, Any]:
        """
        Get how many genesis devices the player can still buy this week,
        along with tier information and costs.
        """
        player = self.db.query(Player).filter(Player.id == player_id).first()
        if not player:
            raise ValueError("Player not found")

        purchases_this_week = self._get_weekly_purchase_count(player)
        remaining = max(0, MAX_PURCHASES_PER_WEEK - purchases_this_week)

        # Get ship capacity info
        ship_capacity = 0
        ship_type_name = None
        if player.current_ship_id:
            ship = self.db.query(Ship).filter(Ship.id == player.current_ship_id).first()
            if ship:
                ship_capacity = self._get_ship_genesis_capacity(ship)
                ship_type_name = ship.type.value

        # Check if player has a colony ship for advanced tier
        has_colony_ship = False
        if player.current_ship_id:
            ship = self.db.query(Ship).filter(Ship.id == player.current_ship_id).first()
            if ship and ship.type == ShipType.COLONY_SHIP:
                has_colony_ship = True

        # Build tier info
        tiers = {}
        for tier_name, tier_config in GENESIS_TIERS.items():
            can_afford = player.credits >= tier_config["cost"]
            can_deploy = remaining > 0 and can_afford

            tier_info = {
                "cost": tier_config["cost"],
                "can_afford": can_afford,
                "habitability_range": list(tier_config["habitability_range"]),
                "requires_ship_sacrifice": tier_config.get("requires_ship_sacrifice", False),
            }

            if tier_config.get("requires_ship_sacrifice"):
                tier_info["sacrifice_ship_type"] = tier_config["sacrifice_ship_type"].value
                tier_info["has_required_ship"] = has_colony_ship
                can_deploy = can_deploy and has_colony_ship

            tier_info["can_deploy"] = can_deploy
            tiers[tier_name] = tier_info

        return {
            "purchases_this_week": purchases_this_week,
            "purchases_remaining": remaining,
            "max_purchases_per_week": MAX_PURCHASES_PER_WEEK,
            "player_credits": player.credits,
            "current_ship_type": ship_type_name,
            "ship_genesis_capacity": ship_capacity,
            "formation_hours": self.formation_hours,
            "tiers": tiers,
        }

    # ------------------------------------------------------------------ #
    #  Internal helpers
    # ------------------------------------------------------------------ #

    def _select_planet_type(self, weights: Dict[PlanetType, int]) -> PlanetType:
        """Select a random planet type based on weighted probabilities."""
        types = list(weights.keys())
        type_weights = list(weights.values())
        return random.choices(types, weights=type_weights, k=1)[0]

    def _get_weekly_purchase_count(self, player: Player) -> int:
        """Count how many genesis devices the player has purchased this week."""
        settings = player.settings or {}
        purchases = settings.get("genesis_purchases", [])

        if not purchases:
            return 0

        # Calculate the start of the current week (Monday 00:00 UTC)
        now = datetime.now(timezone.utc)
        days_since_monday = now.weekday()
        week_start = now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=days_since_monday)

        count = 0
        for purchase in purchases:
            ts = purchase.get("timestamp")
            if ts:
                try:
                    if isinstance(ts, str):
                        purchase_time = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                    else:
                        purchase_time = ts
                    if purchase_time >= week_start:
                        count += 1
                except (ValueError, TypeError):
                    continue

        return count

    def _record_genesis_purchase(self, player: Player, tier: str) -> None:
        """Record a genesis device purchase in the player's settings JSONB."""
        settings = dict(player.settings) if player.settings else {}
        purchases = list(settings.get("genesis_purchases", []))

        purchases.append({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "tier": tier,
        })

        # Clean up old entries (older than 2 weeks) to prevent JSONB bloat
        two_weeks_ago = datetime.now(timezone.utc) - timedelta(weeks=2)
        cleaned = []
        for p in purchases:
            ts = p.get("timestamp")
            if ts:
                try:
                    if isinstance(ts, str):
                        purchase_time = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                    else:
                        purchase_time = ts
                    if purchase_time >= two_weeks_ago:
                        cleaned.append(p)
                except (ValueError, TypeError):
                    cleaned.append(p)

        settings["genesis_purchases"] = cleaned
        player.settings = settings

    def _get_ship_genesis_capacity(self, ship: Ship) -> int:
        """Get the genesis device capacity for a ship based on its type."""
        return GENESIS_CAPACITY_BY_SHIP.get(ship.type, 0)

    def _complete_formation(self, planet: Planet) -> None:
        """Transition a forming planet to a usable completed state."""
        planet.formation_status = "complete"
        planet.status = PlanetStatus.COLONIZED

        # Set initial resources based on tier
        tier = planet.genesis_tier or "basic"
        tier_config = GENESIS_TIERS.get(tier, GENESIS_TIERS["basic"])

        # Give starting resources scaled by habitability and resource richness
        base_resources = int(planet.habitability_score * planet.resource_richness * 2)
        planet.fuel_ore = base_resources
        planet.organics = base_resources
        planet.equipment = int(base_resources * 0.5)

        # Set initial population based on tier
        if tier == "advanced":
            # Spec: Advanced genesis creates a Settlement-level colony
            # with a Level 2 (Settlement) citadel and 5000 colonists
            planet.colonists = 5000
            planet.population = 5000
            planet.max_colonists = max(planet.max_colonists, 5000)

            # Initialize citadel at Level 2 (Settlement) with proper stats
            # Import citadel level config to stay consistent with citadel_service
            from src.services.citadel_service import CITADEL_LEVELS
            settlement_config = CITADEL_LEVELS[2]
            planet.citadel_level = 2
            planet.citadel_safe_credits = 0
            planet.citadel_safe_max = settlement_config["safe_storage"]
            planet.citadel_drone_capacity = settlement_config["drone_capacity"]
            planet.citadel_max_population = settlement_config["max_population"]

            logger.info(
                f"Advanced genesis: Settlement-level colony created with L2 citadel "
                f"(Settlement) and 5000 colonists on planet {planet.id}"
            )
        elif tier == "enhanced":
            # Enhanced tier gets a modest head start over basic
            planet.colonists = 500
            planet.population = 500
        else:
            # Basic tier: minimal starting population
            planet.colonists = 100
            planet.population = 100

        logger.info(
            f"Planet {planet.id} ({planet.name}) formation complete. "
            f"Tier: {tier}, Type: {planet.type.value}, Habitability: {planet.habitability_score}"
        )

    def _generate_planet_name(self, sector_id: int, planet_type: PlanetType) -> str:
        """Generate a name for a genesis-created planet."""
        prefixes = {
            PlanetType.TERRAN: ["New Eden", "Terra Nova", "Gaia", "Verdant", "Haven"],
            PlanetType.OCEANIC: ["Aquarius", "Neptune's", "Tidefall", "Deep Blue", "Coral"],
            PlanetType.DESERT: ["Arrakis", "Sahara", "Dune", "Scorched", "Arid"],
            PlanetType.ICE: ["Frostholm", "Glacius", "Cryo", "Frozen", "Tundra"],
            PlanetType.VOLCANIC: ["Vulcan", "Magmus", "Inferno", "Ember", "Igneous"],
            PlanetType.MOUNTAINOUS: ["Ironpeak", "Highspire", "Craghold", "Summit", "Stonereach"],
        }

        prefix_list = prefixes.get(planet_type, ["Genesis"])
        prefix = random.choice(prefix_list)
        suffix = f"-{sector_id}-{random.randint(100, 999)}"
        return f"{prefix}{suffix}"
