import uuid
import enum
from datetime import datetime
from typing import List, Dict, Optional, Any
from sqlalchemy import BigInteger, Boolean, Column, DateTime, String, Integer, Enum, func
from sqlalchemy.dialects.postgresql import UUID, JSONB, ARRAY
from sqlalchemy.orm import relationship

from src.core.database import Base


class GalaxyImportState(enum.Enum):
    """Whether the Galaxy row reflects a fully-imported universe.

    Drives the GalaxyStateGuard middleware: player traffic is 503'd while
    a bang generation job is mid-flight (`GENERATING`) or has failed
    (`FAILED`); only `READY` permits gameplay.
    """

    GENERATING = "GENERATING"
    READY = "READY"
    FAILED = "FAILED"


class Galaxy(Base):
    __tablename__ = "galaxies"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(100), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    last_updated = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    
    # NOTE: region_distribution field removed - zones concept eliminated
    # Regions are now the unified concept (Central Nexus, Terran Space, Player-owned)
    # Galaxy is now primarily a metadata container for global statistics and configuration
    
    # Statistics - aligned with data definition
    statistics = Column(JSONB, nullable=False, default={
        "total_sectors": 0,
        "discovered_sectors": 0,
        "station_count": 0,
        "planet_count": 0,
        "player_count": 0,
        "team_count": 0,
        "warp_tunnel_count": 0,
        "genesis_count": 0
    })

    # Density - aligned with data definition
    density = Column(JSONB, nullable=False, default={
        "station_density": 10,           # Percentage of sectors with stations (5-15% per spec)
        "planet_density": 3,          # Percentage of sectors with planets (2-5% per spec)
        "one_way_warp_percentage": 5, # Percentage of one-way warps (2-8% per spec)
        "resource_distribution": {    # Overall resource distribution (canon slugs only —
            # ghost slugs medical_supplies/technology removed WO-ARCH-RES-2I-A; freed 30
            # points redistributed proportionally over the surviving 4, sum stays 100)
            "ore": 36,
            "organics": 29,
            "equipment": 21,
            "luxury_goods": 14
        }
    })
    
    faction_influence = Column(JSONB, nullable=False, default={
        "terran_federation": 30,
        "mercantile_guild": 15,
        "frontier_coalition": 20,
        "astral_mining_consortium": 10,
        "nova_scientific_institute": 5,
        "fringe_alliance": 10,
        "player_controlled": 5,
        "contested": 5
    })
    
    # State and Events
    state = Column(JSONB, nullable=False, default={
        "age_in_days": 0,
        "resource_depletion": 0,
        "economic_health": 100,
        "exploration_percentage": 0,
        "player_wealth_distribution": {
            "top_10_percent": 0,
            "middle_40_percent": 0,
            "bottom_50_percent": 0
        }
    })
    
    events = Column(JSONB, nullable=False, default={
        "active_events": [],
        "scheduled_events": []
    })
    
    # Configuration
    # NOTE (ADR-0006): expansion_enabled and warp_shifts_enabled dropped. The
    # galaxy evolves only via region attachment to the Central Nexus; in-place
    # mutation (warp shifts, edge expansion) is not part of the launch design.
    max_sectors = Column(Integer, nullable=False, default=500)
    resources_regenerate = Column(Boolean, nullable=False, default=True)

    # Game Rules
    default_turns_per_day = Column(Integer, nullable=False, default=1000)
    combat_penalties = Column(JSONB, nullable=False, default={
        "federation": "high",
        "border": "medium",
        "frontier": "none"
    })
    economic_modifiers = Column(JSONB, nullable=False, default={})
    
    # Special Properties
    hidden_sectors = Column(Integer, nullable=False, default=5)
    special_features = Column(ARRAY(String), nullable=False, default=[])
    description = Column(String, nullable=False, default="A standard galaxy with 500 sectors")

    # --- Bang integration audit columns (see bang_integration.md Phase 1B) ---
    # Lifecycle gate read by GalaxyStateGuardMiddleware: GENERATING / READY / FAILED.
    # Existing rows are backfilled to READY by the galaxy_audit_columns migration.
    import_state = Column(
        Enum(GalaxyImportState, name="galaxy_import_state"),
        nullable=False,
        default=GalaxyImportState.READY,
        server_default=GalaxyImportState.READY.value,
    )
    # Pinned bang CLI version (semver) used to produce this galaxy. Drives the
    # admin UI's "current bang differs from generator version" warning.
    bang_version = Column(String(20), nullable=True)
    # Seed passed to bang. BIGINT to preserve uint64 range without JS-number drift.
    bang_seed = Column(BigInteger, nullable=True)
    # Stable hash of the BangConfig used for this galaxy (reproducibility key).
    bang_config_hash = Column(String(64), nullable=True)
    # Verbatim raw Universe JSON (config + npcRosters + provenance) for audit
    # and follow-up NPC materialization. Schema-A per resolved decision Q3.
    bang_snapshot = Column(JSONB, nullable=True)
    # Categorized warnings from bang's stderr + translator's Phase-13 checks.
    generation_warnings = Column(
        JSONB,
        nullable=False,
        default=list,
        server_default="[]",
    )

    def __repr__(self):
        return f"<Galaxy {self.name} - {self.statistics.get('total_sectors', 0)} sectors>"
    
    def update_statistics(self):
        """Update the galaxy statistics based on related entities"""
        from src.models.region import Region as PlayerRegion
        from src.models.cluster import Cluster
        from src.models.sector import Sector
        from src.models.station import Station
        from src.models.planet import Planet
        from src.models.player import Player
        from src.models.team import Team
        from src.models.warp_tunnel import WarpTunnel
        
        # This method would be implemented to update stats in real-time
        pass


# GalaxyZone model removed - zones concept eliminated
# Regions are now the unified concept (Central Nexus, Terran Space, Player-owned)
# Clusters now belong directly to Regions, not to zones within a galaxy