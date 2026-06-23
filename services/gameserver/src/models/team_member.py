import uuid
import enum
from datetime import datetime
from sqlalchemy import Column, DateTime, String, ForeignKey, Boolean, func
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship

from src.core.database import Base


class TeamRole(enum.Enum):
    LEADER = "LEADER"
    OFFICER = "OFFICER"
    MEMBER = "MEMBER"
    RECRUIT = "RECRUIT"


class TeamMember(Base):
    """Association table for team members with roles and permissions"""
    __tablename__ = "team_members"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    team_id = Column(UUID(as_uuid=True), ForeignKey("teams.id", ondelete="CASCADE"), nullable=False)
    player_id = Column(UUID(as_uuid=True), ForeignKey("players.id", ondelete="CASCADE"), nullable=False)
    role = Column(String(20), nullable=False, default=TeamRole.MEMBER.value)
    joined_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    
    # Permissions and settings
    permissions = Column(JSONB, nullable=False, default={})  # Specific permissions for this member
    can_invite = Column(Boolean, nullable=False, default=False)
    can_kick = Column(Boolean, nullable=False, default=False)
    can_manage_treasury = Column(Boolean, nullable=False, default=False)
    # can_manage_missions: a RESERVED, not-yet-gating team permission. It is set by
    # the role logic (LEADER/OFFICER get it in team_service) and surfaced in the
    # teams API, but no team-mission feature reads it as a gate yet — there is no
    # TeamMission model/route. (Distinct from the now-removed NPC faction-mission
    # surface; this is the player-team mission permission for a planned feature.)
    # Kept rather than dropped because removing the DB column requires a migration;
    # left in place + documented until the team-mission feature lands or is cut.
    can_manage_missions = Column(Boolean, nullable=False, default=False)
    can_manage_alliances = Column(Boolean, nullable=False, default=False)
    
    # Activity tracking
    last_active = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    contribution_credits = Column(JSONB, nullable=False, default={})  # Track member contributions
    
    # Relationships
    team = relationship("Team", back_populates="team_members")
    player = relationship("Player", back_populates="team_membership")

    def __repr__(self):
        return f"<TeamMember {self.player_id} in team {self.team_id} as {self.role}>"