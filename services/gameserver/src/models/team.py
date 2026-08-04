import uuid
import enum
from typing import TYPE_CHECKING
from sqlalchemy import Boolean, Column, DateTime, String, Integer, Float, Text, func
from sqlalchemy.dialects.postgresql import UUID, JSONB, ARRAY
from sqlalchemy.orm import relationship

from src.core.database import Base

if TYPE_CHECKING:
    pass


class TeamReputationHandling(enum.Enum):
    AVERAGE = "AVERAGE"  # Average of all members (default)
    LOWEST = "LOWEST"    # Use the lowest member reputation
    LEADER = "LEADER"    # Use the team leader's reputation


class TeamRecruitmentStatus(enum.Enum):
    OPEN = "OPEN"              # Anyone can join
    INVITE_ONLY = "INVITE_ONLY"  # Requires invitation
    CLOSED = "CLOSED"          # Not accepting members


class Team(Base):
    __tablename__ = "teams"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(80), nullable=False, unique=True)
    description = Column(Text, nullable=True)
    leader_id = Column(UUID(as_uuid=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    reputation_calculation_method = Column(
        String(20),
        nullable=False,
        default="AVERAGE",
        # DEPRECATED (DECISIONS.md team-reputation-calculation-method-canonical,
        # 2026-08-04): TeamReputation.calculation_method is the sole SSOT.
        # This Team-level duplicate is retained for additive-schema safety
        # (no DROP without Max GO) and must not be read or written by services.
    )

    # Team properties
    tag = Column(String(10), nullable=True)  # Short team tag for display
    logo = Column(String, nullable=True)  # URL to team logo
    is_public = Column(Boolean, nullable=False, default=True)  # Whether the team can be joined without invitation
    max_members = Column(Integer, nullable=False, default=4)  # Maximum team size
    sector_claims = Column(ARRAY(Integer), nullable=False, default=[])  # Sectors claimed by this team
    home_sector_id = Column(Integer, nullable=True)  # Team's home base
    recruitment_status = Column(String(20), nullable=False, default=TeamRecruitmentStatus.OPEN.value)
    
    # Team treasury
    treasury_credits = Column(Integer, nullable=False, default=0)
    treasury_fuel = Column(Integer, nullable=False, default=0)
    treasury_organics = Column(Integer, nullable=False, default=0)
    treasury_equipment = Column(Integer, nullable=False, default=0)
    treasury_technology = Column(Integer, nullable=False, default=0)
    treasury_luxury_items = Column(Integer, nullable=False, default=0)
    treasury_precious_metals = Column(Integer, nullable=False, default=0)
    treasury_raw_materials = Column(Integer, nullable=False, default=0)
    treasury_plasma = Column(Integer, nullable=False, default=0)
    treasury_bio_samples = Column(Integer, nullable=False, default=0)
    treasury_dark_matter = Column(Integer, nullable=False, default=0)
    treasury_quantum_crystals = Column(Integer, nullable=False, default=0)
    
    # Team statistics
    total_credits = Column(Integer, nullable=False, default=0)  # Combined credits of all members
    total_planets = Column(Integer, nullable=False, default=0)  # Number of planets owned by team members
    combat_rating = Column(Float, nullable=False, default=0.0)  # Team's overall combat effectiveness
    trade_rating = Column(Float, nullable=False, default=0.0)  # Team's overall trading effectiveness
    
    # Team management
    join_requirements = Column(JSONB, nullable=False, default={})  # Requirements to join this team
    member_roles = Column(JSONB, nullable=False, default={})  # Roles assigned to members
    resource_sharing = Column(JSONB, nullable=False, default={})  # Resource sharing settings
    invitation_codes = Column(JSONB, nullable=False, default=[])  # Active invitation codes
    
    # Relationships
    members = relationship("Player", back_populates="team")
    team_members = relationship("TeamMember", back_populates="team", cascade="all, delete-orphan")
    reputation = relationship("TeamReputation", back_populates="team", uselist=False, cascade="all, delete-orphan")
    controlled_sectors = relationship("Sector", back_populates="controlling_team")
    drones = relationship("Drone", back_populates="team")
    fleets = relationship("Fleet", back_populates="team", cascade="all, delete-orphan")
    messages = relationship("Message", foreign_keys="[Message.team_id]", back_populates="team")

    def __repr__(self):
        return f"<Team {self.name} (Leader: {self.leader_id})>"

    @property
    def member_count(self) -> int:
        return len(self.members) if self.members else 0
    
    @property
    def is_active(self) -> bool:
        return self.member_count > 0 