"""Central Nexus Galaxy Generation Service - Creates the 2000-5000 sector galactic hub"""

import asyncio
import random
import math
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Tuple, Optional, Any
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, insert, update, delete
from sqlalchemy.orm import selectinload

from src.core.commodity_economy import base_price as commodity_base_price
from src.core.database import get_async_session
from src.models.sector import Sector, SectorType
from src.models.planet import Planet
from src.models.station import Station
from src.models.warp_tunnel import WarpTunnel, WarpTunnelType, WarpTunnelStatus
from src.models.region import Region, RegionType
from src.models.zone import Zone
from src.models.cluster import Cluster, ClusterType

import logging

logger = logging.getLogger(__name__)


class NexusGenerationService:
    """Service for generating the Central Nexus - a sparse 5000-sector galactic hub organized by clusters"""

    def __init__(self):
        self.total_sectors = 5000  # Central Nexus size (per spec)
        self.cluster_count = 20  # 20 clusters × 250 sectors each
        self.generated_sectors = set()

        # Sparse generation parameters (Central Nexus is mostly empty space)
        self.port_density = 0.05  # 5% of sectors have ports (vs 15% standard)
        self.planet_density = 0.10  # 10% of sectors have planets (vs 25% standard)
        self.warp_density_multiplier = 0.3  # 70% fewer warp tunnels than standard regions
    
    async def generate_central_nexus(self, session: AsyncSession) -> Dict[str, Any]:
        """Generate the complete Central Nexus - a sparse 5000-sector galactic hub

        Architecture:
        - 1 region (Central Nexus, type=CENTRAL_NEXUS)
        - 20 clusters (250 sectors each)
        - 5000 sectors total
        - Sparse infrastructure (5% ports, 10% planets, 0.3x warp density)
        """
        logger.info("Starting Central Nexus galaxy generation...")

        try:
            # Check if Central Nexus already exists
            existing_nexus = await self._check_existing_nexus(session)
            if existing_nexus:
                logger.info("Central Nexus already exists, skipping generation")
                return {"status": "exists", "nexus_id": str(existing_nexus.id)}

            # Create Central Nexus region entry
            nexus_region = await self._create_nexus_region(session)

            # Create "The Expanse" zone for all 5000 sectors
            nexus_zone = await self._create_nexus_zone(session, str(nexus_region.id))

            # Create 20 clusters for organization (250 sectors each)
            nexus_clusters = await self._create_nexus_clusters(session, str(nexus_region.id))

            generation_stats = {
                "total_sectors": 0,
                "total_ports": 0,
                "total_planets": 0,
                "total_warp_tunnels": 0,
                "clusters_created": len(nexus_clusters),
                "generation_time": datetime.utcnow()
            }

            # Generate sectors for each cluster
            sectors_per_cluster = self.total_sectors // self.cluster_count
            # Start Central Nexus sectors at 301 (after Terran Space sectors 1-300)
            current_sector_num = 301

            for idx, cluster in enumerate(nexus_clusters):
                logger.info(f"Generating sectors for cluster {idx + 1}/{self.cluster_count}: {cluster.name}")

                # Calculate sector range for this cluster
                start_sector = current_sector_num
                end_sector = start_sector + sectors_per_cluster - 1

                # Last cluster gets any remaining sectors (extends to sector 5300)
                if idx == len(nexus_clusters) - 1:
                    # Central Nexus ends at sector 5300 (301 + 5000 - 1, since Terran Space ends at 300)
                    end_sector = 300 + self.total_sectors

                cluster_stats = await self._generate_cluster_sectors(
                    session,
                    str(nexus_region.id),
                    str(cluster.id),
                    str(nexus_zone.id),  # Pass zone ID for sector assignment
                    start_sector,
                    end_sector,
                    cluster.type,  # WO-GX1: per-cluster-type seeding biases
                )

                # Update overall stats
                generation_stats["total_sectors"] += cluster_stats["sectors"]
                generation_stats["total_ports"] += cluster_stats["ports"]
                generation_stats["total_planets"] += cluster_stats["planets"]

                current_sector_num = end_sector + 1
                logger.info(f"Cluster {cluster.name} completed: {cluster_stats}")

            # Generate intra-regional warp tunnels with sparse density
            logger.info("Generating warp tunnels for Central Nexus sectors (sparse density)...")
            warp_tunnel_count = await self._generate_warp_tunnels(session, str(nexus_region.id))
            generation_stats["total_warp_tunnels"] = warp_tunnel_count
            logger.info(f"Created {warp_tunnel_count} warp tunnels")

            # Create MarketPrice entries for all generated stations
            logger.info("Creating market prices for Central Nexus stations...")
            market_prices_created = await self._create_market_prices_for_nexus_stations(
                session, str(nexus_region.id)
            )
            generation_stats["market_prices_created"] = market_prices_created
            logger.info(f"Created {market_prices_created} market price entries")

            # NOTE: Don't commit here - let the caller handle transaction management
            # This avoids async/sync context issues when called from sync endpoints
            await session.flush()  # Flush changes to get IDs

            logger.info(f"Central Nexus generation completed: {generation_stats}")

            return {
                "status": "completed",
                "nexus_id": str(nexus_region.id),
                "stats": generation_stats
            }

        except Exception as e:
            logger.error(f"Failed to generate Central Nexus: {e}")
            await session.rollback()
            raise
    
    async def _check_existing_nexus(self, session: AsyncSession) -> Optional[Region]:
        """Check if Central Nexus already exists"""
        result = await session.execute(
            select(Region).where(Region.name == "central-nexus")
        )
        return result.scalar_one_or_none()
    
    async def _create_nexus_region(self, session: AsyncSession) -> Region:
        """Create the Central Nexus region entry with region_type=CENTRAL_NEXUS"""
        nexus_region = Region(
            name="central-nexus",
            display_name="Central Nexus",
            region_type=RegionType.CENTRAL_NEXUS,  # Special region type
            owner_id=None,  # Platform-owned
            subscription_tier="nexus",
            status="active",
            governance_type="autocracy",
            tax_rate=0.05,  # Minimum allowed by valid_tax_rate constraint
            economic_specialization="galactic_hub",
            starting_credits=100,  # Minimum allowed by valid_starting_credits constraint
            starting_ship="none",
            total_sectors=self.total_sectors,
            language_pack={
                "greeting": "Welcome to the Central Nexus - Heart of the Galaxy",
                "currency": "galactic_credits",
                "government": "Galactic Authority"
            },
            aesthetic_theme={
                "primary_color": "#805ad5",
                "secondary_color": "#553c9a",
                "style": "futuristic",
                "atmosphere": "cosmopolitan"
            }
        )

        session.add(nexus_region)
        await session.flush()
        return nexus_region

    async def _create_nexus_zone(self, session: AsyncSession, region_id: str) -> Zone:
        """Create 'The Expanse' zone for Central Nexus (covers all 5000 sectors)"""
        nexus_zone = Zone(
            region_id=region_id,
            name="The Expanse",
            zone_type="EXPANSE",
            start_sector=1,
            end_sector=5000,
            policing_level=3,  # Light policing (sparse region)
            danger_rating=6    # Moderate danger
        )
        session.add(nexus_zone)
        await session.flush()
        logger.info(f"Created 'The Expanse' zone for Central Nexus (sectors 1-5000)")
        return nexus_zone

    async def _create_nexus_clusters(self, session: AsyncSession, region_id: str) -> List[Cluster]:
        """Create 20 clusters for organizing Central Nexus sectors (250 sectors each)

        Cluster Types:
        - Trade Hub clusters (commerce-focused)
        - Population Center clusters (residential/services)
        - Transit Hub clusters (navigation/warp gates)
        - Standard clusters (mixed-use)
        """
        clusters = []
        cluster_types_distribution = [
            ClusterType.TRADE_HUB,          # 1  Commerce Central Hub (ANCHOR: starter, civic-safe)
            ClusterType.POPULATION_CENTER,  # 2  Diplomatic Quarter
            ClusterType.TRADE_HUB,          # 3  Industrial Complex
            ClusterType.RESOURCE_RICH,      # 4  Prospect Belt
            ClusterType.FRONTIER_OUTPOST,   # 5  Drift Reaches
            ClusterType.FRONTIER_OUTPOST,   # 6  Outer Survey Station
            ClusterType.TRADE_HUB,          # 7  Free Trade Zone
            ClusterType.RESOURCE_RICH,      # 8  Lodestar Reach
            ClusterType.STANDARD,           # 9  Quiet Quarter
            ClusterType.STANDARD,           # 10 Gateway Plaza (ANCHOR: Capital, never FRONTIER/RESOURCE)
            ClusterType.POPULATION_CENTER,  # 11 Settlers' Rest
            ClusterType.STANDARD,           # 12 Transit Junction
            ClusterType.RESOURCE_RICH,      # 13 Slag Fields
            ClusterType.TRADE_HUB,          # 14 Starport Complex
            ClusterType.FRONTIER_OUTPOST,   # 15 Marker's Edge
            ClusterType.STANDARD,           # 16 The Bazaar
            ClusterType.FRONTIER_OUTPOST,   # 17 Lonesome Span
            ClusterType.STANDARD,           # 18 Wayfarer Hollow
            ClusterType.STANDARD,           # 19 Merchant's Row
            ClusterType.FRONTIER_OUTPOST    # 20 Frontier Gateway
        ]

        cluster_names = [
            "Commerce Central Hub",
            "Diplomatic Quarter",
            "Industrial Complex",
            "Prospect Belt",
            "Drift Reaches",
            "Outer Survey Station",
            "Free Trade Zone",
            "Lodestar Reach",
            "Quiet Quarter",
            "Gateway Plaza",
            "Settlers' Rest",
            "Transit Junction",
            "Slag Fields",
            "Starport Complex",
            "Marker's Edge",
            "The Bazaar",
            "Lonesome Span",
            "Wayfarer Hollow",
            "Merchant's Row",
            "Frontier Gateway"
        ]

        sectors_per_cluster = self.total_sectors // self.cluster_count

        for i in range(self.cluster_count):
            cluster = Cluster(
                name=cluster_names[i],
                region_id=region_id,  # Changed from zone_id
                type=cluster_types_distribution[i],
                sector_count=sectors_per_cluster,
                is_discovered=True,  # Central Nexus is always discovered
                discovery_requirement={},
                description=f"Central Nexus {cluster_names[i]} - Sector cluster {i + 1}/{self.cluster_count}",
                is_hidden=False,
                warp_stability=0.95,  # Very stable
                economic_value=8,  # High economic value
                resources={},
                faction_influence={},
                nav_hazards=[],
                recommended_ship_class="any",
                x_coord=i % 5,  # 5x4 grid layout
                y_coord=i // 5,
                z_coord=0
            )
            session.add(cluster)
            clusters.append(cluster)

        await session.flush()
        logger.info(f"Created {len(clusters)} clusters for Central Nexus")
        return clusters
    
    async def _generate_cluster_sectors(
        self,
        session: AsyncSession,
        region_id: str,
        cluster_id: str,
        zone_id: str,
        start_sector: int,
        end_sector: int,
        cluster_type: ClusterType = ClusterType.STANDARD,
    ) -> Dict[str, int]:
        """Generate sectors, ports, and planets for a cluster with sparse density

        Central Nexus has minimal infrastructure:
        - 5% station density (vs 15% standard)
        - 10% planet density (vs 25% standard)
        - Sector 1 ALWAYS has both station and planet

        WO-GX1 — per-cluster-type seeding biases (NO-CANON magnitudes):
        - STANDARD: 1.0 baseline (unbiased — byte-identical to the legacy path).
        - RESOURCE_RICH: every sector gets asteroids with +50% yield (×1.5 base).
        - FRONTIER_OUTPOST: ~50% station density (fewer ports); some sectors
          become NEBULA (per-sector nebula_chance) — but NEVER the starter.
        - MILITARY_ZONE: patrol_ships seeded into sector_data['defenses'].
        - CONTESTED: multi-faction overlay (controlling_faction left null /
          uncontrolled — the baseline already leaves it null; the bias is
          explicit non-assignment, so port/planet generation is unchanged).
        The biases only fire for non-STANDARD clusters; STANDARD clusters take
        the exact same code path (and RNG-call sequence) as before this WO, so
        an unbiased Nexus is byte-identical to today.
        """
        stats = {"sectors": 0, "ports": 0, "planets": 0}

        # WO-GX1 bias parameters (NO-CANON magnitudes from the work order)
        is_resource_rich = cluster_type == ClusterType.RESOURCE_RICH
        is_frontier = cluster_type == ClusterType.FRONTIER_OUTPOST
        is_military = cluster_type == ClusterType.MILITARY_ZONE
        # FRONTIER_OUTPOST halves effective station density (fewer ports).
        effective_port_density = (
            self.port_density * 0.5 if is_frontier else self.port_density
        )
        # FRONTIER_OUTPOST scatters nebula sectors (more nebula on the edge).
        nebula_chance = 0.15 if is_frontier else 0.0

        batch_sectors = []
        batch_ports = []
        batch_planets = []

        for sector_num in range(start_sector, end_sector + 1):
            # Generate coordinates for this sector (simple grid layout)
            grid_size = int(math.sqrt(self.total_sectors)) + 1
            x_coord = (sector_num - 1) % grid_size
            y_coord = (sector_num - 1) // grid_size
            z_coord = 0  # Central Nexus is on a flat plane

            # Create sector with ALL required NOT NULL fields
            sector_data = {
                "sector_id": sector_num,  # Required INTEGER NOT NULL
                "name": f"Nexus Sector {sector_num}",  # Required VARCHAR NOT NULL
                "cluster_id": cluster_id,  # Required UUID NOT NULL
                "zone_id": zone_id,  # UUID - Assign to "The Expanse" zone
                "x_coord": x_coord,  # Required INTEGER NOT NULL
                "y_coord": y_coord,  # Required INTEGER NOT NULL
                "z_coord": z_coord,  # Required INTEGER NOT NULL
                "sector_number": sector_num,  # Optional INTEGER (for Central Nexus)
                "region_id": region_id,
                # district field REMOVED - no longer exists
                "security_level": 5,  # Medium security (default)
                "development_level": 3,  # Low development (sparse)
                "traffic_level": 2,  # Low traffic (sparse)
                "created_at": datetime.utcnow()
            }
            # WO-GX1: per-cluster-type seeding biases. These ONLY add keys for
            # non-STANDARD clusters — STANDARD's sector_data is left byte-for-byte
            # identical to the legacy path. Heterogeneous param-dict key-sets are
            # SAFE for bulk insert (SQLAlchemy 2.0 groups dicts by key-set and
            # applies column defaults per group), so keys are added conditionally
            # rather than homogenized; absent keys fall back to the column default.
            if is_resource_rich and sector_num != 1:
                # +50% asteroid yield: has_asteroids + asteroid_yield ×1.5 off a
                # sensible base (ore 1000 / precious_metals 400 / radioactives 200).
                sector_data["resources"] = {
                    "has_asteroids": True,
                    "asteroid_yield": {
                        "ore": int(1000 * 1.5),
                        "precious_metals": int(400 * 1.5),
                        "radioactives": int(200 * 1.5),
                    },
                    "gas_clouds": [],
                    "has_scanned": False,
                }

            if is_military and sector_num != 1:
                # More patrols: seed patrol_ships into the defenses blob.
                # NO-CANON patrol count: 2-4 patrol ships per military sector.
                patrol_count = random.randint(2, 4)
                # WO-GX1 CRITICAL: patrol_ships MUST be a SCALAR INT, never a
                # list-of-dicts — four live consumers read it via int()
                # (combat_service.py:3506, port_ownership_service.py:1792,
                # admin.py:1495, admin_comprehensive.py:970); a list detonates
                # combat + admin in every military sector.
                sector_data["defenses"] = {
                    "defense_drones": 0,
                    "owner_id": None,
                    "owner_name": None,
                    "team_id": None,
                    "mines": 0,
                    "mine_owner_id": None,
                    "patrol_ships": patrol_count,
                }

            # FRONTIER_OUTPOST: more nebula. NEVER the starter (sector 1).
            if is_frontier and sector_num != 1 and random.random() < nebula_chance:
                sector_data["type"] = SectorType.NEBULA

            batch_sectors.append(sector_data)
            stats["sectors"] += 1

            # Generate port - ALWAYS create for Sector 1 (starter sector), otherwise sparse.
            # FRONTIER_OUTPOST halves effective station density (fewer ports);
            # all other cluster types use the baseline 5% density unchanged.
            if sector_num == 1 or random.random() < effective_port_density:
                port_data = self._generate_port_for_sector(sector_num, region_id)
                batch_ports.append(port_data)
                stats["ports"] += 1

            # Generate planet - ALWAYS create for Sector 1 (starter sector), otherwise sparse (10%)
            if sector_num == 1 or random.random() < self.planet_density:
                planet_data = self._generate_planet_for_sector(sector_num, region_id)
                batch_planets.append(planet_data)
                stats["planets"] += 1

        # Bulk insert sectors
        if batch_sectors:
            await session.execute(insert(Sector), batch_sectors)

        # Bulk insert ports
        if batch_ports:
            await session.execute(insert(Station), batch_ports)

        # Bulk insert planets
        if batch_planets:
            await session.execute(insert(Planet), batch_planets)

        return stats

    def _generate_port_for_sector(self, sector_num: int, region_id: str) -> Dict[str, Any]:
        """Generate a port configuration for a sector in Central Nexus

        Sparse generation: Ports are randomly distributed with mixed types.
        Sector 1 always gets a high-quality trading station for starter access.
        """
        from src.models.station import StationClass, StationType, StationStatus

        # Sector 1 gets a special starter station
        if sector_num == 1:
            return {
                "name": "Central Nexus Starport Prime",
                "sector_id": sector_num,
                "region_id": region_id,
                "station_class": StationClass.CLASS_0,  # Highest quality
                "type": StationType.TRADING,
                "status": StationStatus.OPERATIONAL,
                "size": 10,  # Maximum size
                # Starport Prime discriminator (FEATURES/economy/docking-slips):
                # this is THE Central Nexus Starport Prime — 200 transient / 50
                # long-term docking slips, distinct from a regional Capital
                # (also CLASS_0, but 80 / 30). docking_service reads this flag.
                "is_starport_prime": True,
            }

        # Random port types for other sectors
        port_type = random.choice([
            StationType.TRADING,
            StationType.TRADING,  # Trading is most common
            StationType.INDUSTRIAL,
            StationType.DIPLOMATIC,
            StationType.SCIENTIFIC
        ])

        # Random port class (mostly mid-tier)
        port_class = random.choice([
            StationClass.CLASS_4,
            StationClass.CLASS_5,
            StationClass.CLASS_6,
            StationClass.CLASS_7,
            StationClass.CLASS_8
        ])

        # Random size (mostly medium)
        size = random.randint(4, 7)

        return {
            "name": f"Nexus Station {sector_num}",
            "sector_id": sector_num,
            "region_id": region_id,
            "station_class": port_class,
            "type": port_type,
            "status": StationStatus.OPERATIONAL,
            "size": size
        }
    
    def _generate_planet_for_sector(self, sector_num: int, region_id: str) -> Dict[str, Any]:
        """Generate a planet configuration for a sector in Central Nexus

        Sparse generation: Planets are randomly distributed with varied types.
        Sector 1 always gets a high-quality habitable planet for starter access.
        """
        from src.models.planet import PlanetType, PlanetStatus

        # Sector 1 gets a special starter planet
        if sector_num == 1:
            return {
                "name": "Terra Nova Prime",
                "sector_id": sector_num,
                "region_id": region_id,
                "type": PlanetType.TERRAN,
                "status": PlanetStatus.HABITABLE,
                "size": 9,  # Large
                "position": 3,
                "gravity": 1.0,
                "temperature": 20.0,
                "water_coverage": 70.0,
                "habitability_score": 100,
                "resource_richness": 2.0,
                "resources": ["water", "minerals", "agriculture", "technology"],
                # Canon (colonization.md:147 / ADR-0035): max_population = habitability_score × 1,000
                "max_population": 100 * 1000
            }

        # Random planet type
        planet_type = random.choice([
            PlanetType.TERRAN,
            PlanetType.TROPICAL,
            PlanetType.JUNGLE,
            PlanetType.OCEANIC,
            PlanetType.MOUNTAINOUS,
            PlanetType.DESERT,
            PlanetType.BARREN,
            PlanetType.ICE
        ])

        # Determine habitability based on planet type
        habitability_map = {
            PlanetType.TERRAN: random.randint(70, 100),
            PlanetType.TROPICAL: random.randint(80, 100),
            PlanetType.JUNGLE: random.randint(60, 90),
            PlanetType.OCEANIC: random.randint(50, 80),
            PlanetType.MOUNTAINOUS: random.randint(40, 70),
            PlanetType.DESERT: random.randint(30, 60),
            PlanetType.BARREN: random.randint(10, 40),
            PlanetType.ICE: random.randint(20, 50),
            PlanetType.VOLCANIC: random.randint(10, 30)
        }

        habitability_score = habitability_map.get(planet_type, 50)
        status = PlanetStatus.HABITABLE if habitability_score > 50 else PlanetStatus.UNINHABITABLE

        # Determine planet size (visual/resource scale only — not a population factor)
        size = random.randint(4, 9)
        # Canon (colonization.md:147 / ADR-0035): max_population = habitability_score × 1,000
        # (habitability_score is on the 0-100 scale, matching genesis_service)
        max_population = habitability_score * 1000

        # Generate resources
        resources = ["standard_resources"]
        if planet_type == PlanetType.BARREN:
            resources = ["iron_ore", "rare_metals", "industrial_minerals"]
        elif planet_type in [PlanetType.TERRAN, PlanetType.TROPICAL]:
            resources = ["water", "agriculture", "minerals"]

        return {
            "name": f"Nexus Planet {sector_num}",
            "sector_id": sector_num,
            "region_id": region_id,
            "type": planet_type,
            "status": status,
            "size": size,
            "position": random.randint(2, 5),
            "gravity": round(random.uniform(0.7, 1.5), 1),
            "temperature": round(random.uniform(-20, 40), 1),
            "water_coverage": round(random.uniform(0, 80), 1) if planet_type not in [PlanetType.DESERT, PlanetType.VOLCANIC, PlanetType.BARREN] else round(random.uniform(0, 10), 1),
            "habitability_score": habitability_score,
            "resource_richness": round(random.uniform(1.0, 2.5), 1),
            "resources": resources,
            "max_population": max_population
        }
    

    async def _create_market_prices_for_nexus_stations(
        self, session: AsyncSession, region_id: str
    ) -> int:
        """Create MarketPrice entries for all stations in the Central Nexus region.

        The trading endpoint reads from market_prices table, so every station
        needs MarketPrice rows for each commodity it trades.
        Nexus stations are created via bulk insert without commodities JSONB,
        so we derive market prices from the station class trading patterns.
        """
        from src.models.market_transaction import MarketPrice
        from src.models.station import StationClass

        # Query all stations in this region
        result = await session.execute(
            select(Station).where(Station.region_id == region_id)
        )
        stations = result.scalars().all()

        if not stations:
            return 0

        # Base commodity definitions. base_price now derives from the WO-Y /
        # ADR-0082 single source of truth (src.core.commodity_economy), the same
        # table that feeds the trading-engine ranges and the citadel safe credit
        # values — so nexus seeds can no longer drift from the live economy.
        # quantity/capacity remain local bootstrap stock seeds (not price econ).
        # Behaviour-preserving: commodity_base_price() reproduces ore 15 /
        # organics 18 / equipment 35 / fuel 12 / luxury 100 / gourmet 80 /
        # exotic 250 / colonists 50 exactly.
        base_commodities = {
            "ore": {"base_price": commodity_base_price("ore"), "quantity": 1000, "capacity": 5000},
            "organics": {"base_price": commodity_base_price("organics"), "quantity": 800, "capacity": 3000},
            "equipment": {"base_price": commodity_base_price("equipment"), "quantity": 500, "capacity": 2000},
            "fuel": {"base_price": commodity_base_price("fuel"), "quantity": 1500, "capacity": 4000},
            "luxury_goods": {"base_price": commodity_base_price("luxury_goods"), "quantity": 200, "capacity": 800},
            "gourmet_food": {"base_price": commodity_base_price("gourmet_food"), "quantity": 150, "capacity": 600},
            "exotic_technology": {"base_price": commodity_base_price("exotic_technology"), "quantity": 50, "capacity": 200},
            "colonists": {"base_price": commodity_base_price("colonists"), "quantity": 100, "capacity": 500},
        }

        # Trading patterns by station class
        trading_patterns = {
            StationClass.CLASS_0: {"buys": ["special_goods"], "sells": ["special_goods", "colonists"]},
            StationClass.CLASS_1: {"buys": ["ore"], "sells": ["organics", "equipment"]},
            StationClass.CLASS_2: {"buys": ["organics"], "sells": ["ore", "equipment"]},
            StationClass.CLASS_3: {"buys": ["equipment"], "sells": ["ore", "organics"]},
            StationClass.CLASS_4: {"buys": [], "sells": ["ore", "organics", "equipment", "fuel"]},
            StationClass.CLASS_5: {"buys": ["ore", "organics", "equipment", "fuel"], "sells": []},
            StationClass.CLASS_6: {"buys": ["ore", "organics"], "sells": ["equipment", "fuel"]},
            StationClass.CLASS_7: {"buys": ["equipment", "fuel"], "sells": ["ore", "organics"]},
            StationClass.CLASS_8: {"buys": ["ore", "organics", "equipment", "fuel"], "sells": []},
            StationClass.CLASS_9: {"buys": [], "sells": ["ore", "organics", "equipment", "fuel"]},
            StationClass.CLASS_10: {"buys": ["gourmet_food"], "sells": ["luxury_goods", "exotic_technology"]},
            StationClass.CLASS_11: {"buys": ["exotic_technology"], "sells": ["equipment", "fuel"]},
        }

        prices_created = 0

        for station in stations:
            pattern = trading_patterns.get(station.station_class, {"buys": [], "sells": []})
            buys_list = pattern.get("buys", [])
            sells_list = pattern.get("sells", [])

            for commodity_name, commodity_info in base_commodities.items():
                is_buy = commodity_name in buys_list
                is_sell = commodity_name in sells_list

                if not is_buy and not is_sell:
                    continue

                base_price = commodity_info["base_price"]
                quantity = commodity_info["quantity"]

                # Calculate buy/sell prices with spread
                if is_buy and is_sell:
                    buy_price = int(base_price * 0.85)
                    sell_price = int(base_price * 1.15)
                elif is_buy:
                    buy_price = int(base_price * 1.1)
                    sell_price = int(base_price * 1.5)
                    quantity = int(quantity * 0.2)  # Buyers have low stock
                else:
                    buy_price = int(base_price * 0.5)
                    sell_price = int(base_price * 0.9)
                    quantity = int(quantity * random.uniform(0.5, 0.8))

                market_price = MarketPrice(
                    station_id=station.id,
                    commodity=commodity_name,
                    quantity=quantity,
                    buy_price=buy_price,
                    sell_price=sell_price
                )
                session.add(market_price)
                prices_created += 1

        await session.flush()
        return prices_created

    async def _generate_warp_tunnels(self, session: AsyncSession, region_id: str) -> int:
        """Generate warp tunnels for Central Nexus with SPARSE density (0.3x multiplier).

        Central Nexus is mostly empty space with minimal warp tunnels.
        Standard regions: 2-7 tunnels per sector
        Central Nexus: 1-2 tunnels per sector (70% reduction)
        """
        logger.info("Building sectors map for sparse warp tunnel generation...")

        # Query all sectors in this region with their coordinates
        result = await session.execute(
            select(Sector).where(Sector.region_id == region_id)
        )
        all_sectors = result.scalars().all()

        if not all_sectors:
            logger.warning("No sectors found for warp tunnel generation")
            return 0

        # Build sectors_map: sector_id (int) -> Sector object
        sectors_map = {sector.sector_id: sector for sector in all_sectors}
        all_sector_ids = list(sectors_map.keys())

        sector_connections = {sector_id: 0 for sector_id in all_sector_ids}
        created_tunnels = set()

        logger.info(f"Creating SPARSE warp tunnel network for {len(all_sectors)} sectors (0.3x density)")

        # First pass: Ensure every sector has at least 1 connection
        for source_num in all_sector_ids:
            if sector_connections[source_num] == 0:
                # Find a connection for this isolated sector
                available_targets = [s for s in all_sector_ids if s != source_num]
                if available_targets:
                    dest_num = random.choice(available_targets)
                    await self._create_single_warp_tunnel(
                        session, source_num, dest_num, sectors_map, created_tunnels, sector_connections
                    )

        # Second pass: Add MINIMAL additional connections (sparse generation)
        # Central Nexus has 70% fewer warp tunnels than standard regions
        # Most sectors: 1 tunnel (from first pass)
        # Some sectors: 2 tunnels (30% chance)
        for source_num in all_sector_ids:
            current_connections = sector_connections[source_num]

            # 30% chance to add one more connection (resulting in 1-2 tunnels per sector)
            if random.random() < 0.3 and current_connections < 2:
                # Find a suitable destination
                available_targets = [s for s in all_sector_ids
                                   if s != source_num and
                                   (source_num, s) not in created_tunnels and
                                   (s, source_num) not in created_tunnels]

                if not available_targets:
                    continue  # No more available targets

                # Prefer connecting to sectors with fewer connections
                available_targets.sort(key=lambda x: sector_connections[x])

                # Choose from the least connected sectors (with some randomness)
                choice_pool_size = min(5, len(available_targets))
                dest_num = random.choice(available_targets[:choice_pool_size])

                await self._create_single_warp_tunnel(
                    session, source_num, dest_num, sectors_map, created_tunnels, sector_connections
                )

        total_tunnels = len(created_tunnels)
        avg_connections = sum(sector_connections.values()) / len(sector_connections)
        logger.info(f"Created {total_tunnels} SPARSE warp tunnels, average {avg_connections:.1f} connections per sector")

        return total_tunnels

    async def _create_single_warp_tunnel(
        self,
        session: AsyncSession,
        source_num: int,
        dest_num: int,
        sectors_map: Dict[int, Sector],
        created_tunnels: set,
        sector_connections: dict
    ) -> None:
        """Create a single warp tunnel between two sectors."""
        source = sectors_map[source_num]
        dest = sectors_map[dest_num]

        # Calculate distance
        distance = self._calculate_sector_distance(source, dest)

        # Create warp tunnel
        tunnel_name = f"Nexus Warp {source_num}-{dest_num}"
        tunnel_type = self._choose_warp_tunnel_type()

        # Most tunnels are bidirectional (85%), some are one-way (15%)
        is_bidirectional = random.random() > 0.15

        tunnel = WarpTunnel(
            name=tunnel_name,
            origin_sector_id=source.id,  # UUID from sectors.id
            destination_sector_id=dest.id,  # UUID from sectors.id
            type=tunnel_type,
            status=WarpTunnelStatus.ACTIVE,
            is_bidirectional=is_bidirectional,
            stability=self._get_stability_for_tunnel_type(tunnel_type),
            turn_cost=self._get_turn_cost_for_tunnel_type(tunnel_type, distance),
            is_public=True,
            description=f"Warp tunnel connecting Sector {source_num} to Sector {dest_num}"
        )

        session.add(tunnel)
        await session.flush()

        # Track the connection
        created_tunnels.add((source_num, dest_num))
        sector_connections[source_num] += 1

        # If bidirectional, count for destination too
        if is_bidirectional:
            sector_connections[dest_num] += 1

    def _calculate_sector_distance(self, sector1: Sector, sector2: Sector) -> float:
        """Calculate 3D distance between sectors."""
        return ((sector1.x_coord - sector2.x_coord) ** 2 +
                (sector1.y_coord - sector2.y_coord) ** 2 +
                (sector1.z_coord - sector2.z_coord) ** 2) ** 0.5

    def _choose_warp_tunnel_type(self) -> WarpTunnelType:
        """Choose a warp tunnel type randomly."""
        weights = {
            WarpTunnelType.STANDARD: 60,
            WarpTunnelType.QUANTUM: 15,
            WarpTunnelType.ANCIENT: 10,
            WarpTunnelType.ARTIFICIAL: 8,
            WarpTunnelType.UNSTABLE: 7
        }

        choices = []
        for tunnel_type, weight in weights.items():
            choices.extend([tunnel_type] * weight)

        return random.choice(choices)

    def _get_stability_for_tunnel_type(self, tunnel_type: WarpTunnelType) -> float:
        """Get stability value for a warp tunnel type."""
        stability_map = {
            WarpTunnelType.NATURAL: random.uniform(0.8, 0.95),
            WarpTunnelType.ARTIFICIAL: random.uniform(0.8, 0.95),
            WarpTunnelType.STANDARD: random.uniform(0.9, 1.0),
            WarpTunnelType.QUANTUM: random.uniform(0.7, 0.9),
            WarpTunnelType.ANCIENT: random.uniform(0.5, 0.8),
            WarpTunnelType.UNSTABLE: random.uniform(0.3, 0.6)
        }
        return stability_map.get(tunnel_type, 0.8)

    def _get_turn_cost_for_tunnel_type(self, tunnel_type: WarpTunnelType, distance: float) -> int:
        """Calculate turn cost for a warp tunnel."""
        # Base cost based on standard distance
        base_cost = max(1, int(distance / 10))

        # Adjust based on tunnel type
        multiplier_map = {
            WarpTunnelType.NATURAL: 1.0,
            WarpTunnelType.ARTIFICIAL: 0.7,
            WarpTunnelType.STANDARD: 1.0,
            WarpTunnelType.QUANTUM: 0.5,  # Faster
            WarpTunnelType.ANCIENT: 0.8,
            WarpTunnelType.UNSTABLE: 1.5  # Slower, riskier
        }

        adjusted_cost = int(base_cost * multiplier_map.get(tunnel_type, 1.0))
        return max(1, adjusted_cost)  # Ensure at least 1 turn


# Singleton instance
nexus_generation_service = NexusGenerationService()