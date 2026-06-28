import uuid
import enum
from datetime import datetime
from typing import List, Dict, Optional, Any
from sqlalchemy import Boolean, Column, DateTime, String, Integer, Float, ForeignKey, Enum, func
from sqlalchemy.dialects.postgresql import UUID, JSONB, ARRAY
from sqlalchemy.orm import relationship

from src.core.database import Base


class WarpTunnelType(enum.Enum):
    NATURAL = "NATURAL"      # Naturally occurring warp tunnel
    ARTIFICIAL = "ARTIFICIAL"  # Player-created warp tunnel
    STANDARD = "STANDARD"    # Regular warp tunnel
    QUANTUM = "QUANTUM"      # Fast quantum tunnel
    ANCIENT = "ANCIENT"      # Old alien technology
    UNSTABLE = "UNSTABLE"    # Unstable warp tunnel
    # NOTE (ADR-0034): ONE_WAY removed. Directionality lives on is_bidirectional,
    # never on the type enum. A one-way tunnel is is_bidirectional=False.

class WarpTunnelStability(enum.Enum):
    UNSTABLE = "UNSTABLE"    # May collapse or shift
    STABLE = "STABLE"        # Reliable long-term connection


class WarpTunnelStatus(enum.Enum):
    ACTIVE = "ACTIVE"      # Fully operational
    UNSTABLE = "UNSTABLE"  # May fail occasionally 
    DEGRADING = "DEGRADING"  # Will become unstable soon
    COLLAPSED = "COLLAPSED"  # No longer functional
    MAINTENANCE = "MAINTENANCE"  # Temporarily offline
    FORMING = "FORMING"    # Still being formed


class WarpTunnel(Base):
    __tablename__ = "warp_tunnels"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(100), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    last_updated = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    
    # Relationships and structure
    origin_sector_id = Column(UUID(as_uuid=True), ForeignKey("sectors.id", ondelete="CASCADE"), nullable=False)
    destination_sector_id = Column(UUID(as_uuid=True), ForeignKey("sectors.id", ondelete="CASCADE"), nullable=False)
    
    # Type and status - aligned with data definition
    type = Column(Enum(WarpTunnelType, name="warp_tunnel_type"), nullable=False)
    status = Column(Enum(WarpTunnelStatus, name="warp_tunnel_status"), nullable=False, default=WarpTunnelStatus.ACTIVE)
    
    # Properties - aligned with data definition
    is_bidirectional = Column(Boolean, nullable=False, default=True)  # Can be used in reverse
    # ADR-0034: latent one-ways look bidirectional in the raw view until a Warp
    # Jumper scan reveals them. Default false; worldgen flips ~20% of one-ways.
    is_latent = Column(Boolean, nullable=False, default=False)
    stability = Column(Float, nullable=False, default=1.0)  # 0.0-1.0 scale
    stability_enum = Column(Enum(WarpTunnelStability, name="warp_tunnel_stability_enum"), nullable=False, default=WarpTunnelStability.STABLE)
    
    # Tunnel properties - aligned with data definition
    properties = Column(JSONB, nullable=False, default={
        "length": 10.0,  # Distance in light years
        "stability_rating": 75,  # 0-100 numeric stability
        "expected_lifetime": None,  # When artificial tunnel may collapse
        "age": 0,  # Time since creation in days
        "traversal_cost": 1,  # Turn cost to use tunnel
        "cool_down": 0,  # Turns before reuse
        "discovered": True,  # Whether tunnel is known to players
        "discoverer_id": None,  # Player who discovered tunnel
        "discovery_date": None,  # When tunnel was discovered
        "affected_by_storms": True  # Whether storms can disrupt tunnel
    })
    
    # Status information - aligned with data definition
    tunnel_status = Column(JSONB, nullable=False, default={
        "is_active": True,  # Whether tunnel is currently usable
        "disruption": None,  # Current disruption details if any
        "traffic_level": 0,  # 0-100 current usage level
        "last_traversal": None,  # When last ship used tunnel
        "maintenance_status": None  # For artificial tunnels
    })
    
    # Endpoints - aligned with data definition
    source_endpoint = Column(JSONB, nullable=False, default={
        "sector_id": None,
        "cluster_id": None,
        "region_id": None,
        "coordinates": {"x": 0, "y": 0, "z": 0},
        "controlling_faction": None,
        "is_secured": False,
        "access_requirements": None
    })
    
    destination_endpoint = Column(JSONB, nullable=False, default={
        "sector_id": None,
        "cluster_id": None,
        "region_id": None,
        "coordinates": {"x": 0, "y": 0, "z": 0},
        "controlling_faction": None,
        "is_secured": False,
        "access_requirements": None
    })
    
    # For artificial tunnels only - aligned with data definition
    artificial_data = Column(JSONB, nullable=True, default=None)  # Creation and management data
    
    # Usage statistics - aligned with data definition
    total_traversals = Column(Integer, nullable=False, default=0)
    traversal_history = Column(JSONB, nullable=False, default=[])
    
    # Legacy properties for backward compatibility
    turn_cost = Column(Integer, nullable=False, default=1)  # Turns required to traverse
    energy_cost = Column(Integer, nullable=False, default=0)  # Additional energy required
    is_public = Column(Boolean, nullable=False, default=True)  # Whether anyone can use it
    access_requirements = Column(JSONB, nullable=True)  # Requirements to use this tunnel
    created_by_player_id = Column(UUID(as_uuid=True), ForeignKey("players.id"), nullable=True)
    created_by_faction = Column(String, nullable=True)
    max_uses = Column(Integer, nullable=True)  # Number of uses before collapse, null = infinite
    current_uses = Column(Integer, nullable=False, default=0)
    expires_at = Column(DateTime(timezone=True), nullable=True)  # When will it collapse
    special_effects = Column(JSONB, nullable=False, default={})  # Effects on ships using the tunnel
    description = Column(String, nullable=True)
    
    # Relationships
    origin_sector = relationship("Sector", foreign_keys=[origin_sector_id], back_populates="warp_tunnels_origin")
    destination_sector = relationship("Sector", foreign_keys=[destination_sector_id], back_populates="warp_tunnels_destination")
    created_by = relationship("Player", back_populates="created_warp_tunnels")
    
    def __repr__(self):
        return f"<WarpTunnel {self.name}: {self.origin_sector_id} -> {self.destination_sector_id} ({self.type.name})>"