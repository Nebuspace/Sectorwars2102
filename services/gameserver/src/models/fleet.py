"""
Fleet and fleet battle models for Sectorwars2102.

Fleets are groups of ships that can engage in large-scale battles.
"""

from uuid import uuid4
from datetime import datetime
from typing import Optional, List
import enum
from sqlalchemy import Column, String, DateTime, Integer, Float, Boolean, ForeignKey, JSON
from sqlalchemy.dialects.postgresql import UUID, ARRAY
from sqlalchemy.orm import relationship

from src.core.database import Base


class FleetRole(str, enum.Enum):
    """Roles that ships can have in a fleet."""
    FLAGSHIP = "flagship"
    ATTACKER = "attacker"
    DEFENDER = "defender"
    SUPPORT = "support"
    SCOUT = "scout"


class FleetStatus(str, enum.Enum):
    """Status of a fleet."""
    FORMING = "forming"
    READY = "ready"
    IN_BATTLE = "in_battle"
    RETREATING = "retreating"
    DISBANDED = "disbanded"


class BattlePhase(str, enum.Enum):
    """Phases of a fleet battle."""
    PREPARATION = "preparation"
    ENGAGEMENT = "engagement"
    MAIN_BATTLE = "main_battle"
    PURSUIT = "pursuit"
    AFTERMATH = "aftermath"


class Fleet(Base):
    """
    A fleet is a group of ships organized for battle.
    """
    __tablename__ = "fleets"
    
    # Primary key
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    
    # Fleet ownership
    team_id = Column(UUID(as_uuid=True), ForeignKey("teams.id", ondelete="CASCADE"), nullable=False, index=True)
    commander_id = Column(UUID(as_uuid=True), ForeignKey("players.id", ondelete="SET NULL"), nullable=True)
    
    # Fleet properties
    name = Column(String(100), nullable=False)
    status = Column(String(50), default=FleetStatus.FORMING.value, nullable=False)
    formation = Column(String(50), default="standard")  # standard, defensive, aggressive, etc.
    
    # Location
    sector_id = Column(UUID(as_uuid=True), ForeignKey("sectors.id", ondelete="SET NULL"), nullable=True, index=True)
    
    # Fleet stats (aggregated from members)
    total_ships = Column(Integer, default=0, nullable=False)
    total_firepower = Column(Integer, default=0, nullable=False)
    total_shields = Column(Integer, default=0, nullable=False)
    total_hull = Column(Integer, default=0, nullable=False)
    average_speed = Column(Float, default=0.0, nullable=False)
    
    # Battle readiness
    morale = Column(Integer, default=100, nullable=False)  # 0-100
    supply_level = Column(Integer, default=100, nullable=False)  # 0-100
    # Large-scale-combat coordination multiplier applied to aggregated firepower.
    coordination_bonus = Column(Float, default=0.0, nullable=False)
    
    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow)
    disbanded_at = Column(DateTime, nullable=True)
    last_battle = Column(DateTime, nullable=True)
    
    # Relationships
    team = relationship("Team", back_populates="fleets")
    commander = relationship("Player", back_populates="commanded_fleets")
    sector = relationship("Sector", back_populates="fleets")
    members = relationship("FleetMember", back_populates="fleet", cascade="all, delete-orphan")
    
    def __repr__(self):
        return f"<Fleet(id={self.id}, name={self.name}, team={self.team_id}, ships={self.total_ships})>"
    
    def calculate_stats(self):
        """Recalculate fleet stats from member ships."""
        if not self.members:
            self.total_ships = 0
            self.total_firepower = 0
            self.total_shields = 0
            self.total_hull = 0
            self.average_speed = 0
            return
            
        self.total_ships = len(self.members)
        self.total_firepower = sum(m.ship.guns for m in self.members if m.ship)
        self.total_shields = sum(m.ship.shields for m in self.members if m.ship)
        self.total_hull = sum(m.ship.armor for m in self.members if m.ship)
        speeds = [m.ship.speed for m in self.members if m.ship]
        self.average_speed = sum(speeds) / len(speeds) if speeds else 0


class FleetMember(Base):
    """
    Individual ship membership in a fleet.
    """
    __tablename__ = "fleet_members"
    
    # Primary key
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    
    # References
    fleet_id = Column(UUID(as_uuid=True), ForeignKey("fleets.id", ondelete="CASCADE"), nullable=False, index=True)
    ship_id = Column(UUID(as_uuid=True), ForeignKey("ships.id", ondelete="CASCADE"), nullable=False, index=True)
    player_id = Column(UUID(as_uuid=True), ForeignKey("players.id", ondelete="CASCADE"), nullable=False, index=True)
    
    # Role in fleet
    role = Column(String(50), default=FleetRole.ATTACKER.value, nullable=False)
    position = Column(Integer, default=0)  # Position in formation
    
    # Status
    joined_at = Column(DateTime, default=datetime.utcnow)
    ready_status = Column(Boolean, default=False)
    
    # Relationships
    fleet = relationship("Fleet", back_populates="members")
    ship = relationship("Ship", back_populates="fleet_membership")
    player = relationship("Player", back_populates="fleet_memberships")
    
    def __repr__(self):
        return f"<FleetMember(fleet={self.fleet_id}, ship={self.ship_id}, role={self.role})>"


class FleetBattle(Base):
    """
    Record of a battle between two fleets.
    """
    __tablename__ = "fleet_battles"
    
    # Primary key
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    
    # Battle participants
    attacker_fleet_id = Column(UUID(as_uuid=True), ForeignKey("fleets.id", ondelete="SET NULL"), nullable=True)
    defender_fleet_id = Column(UUID(as_uuid=True), ForeignKey("fleets.id", ondelete="SET NULL"), nullable=True)
    sector_id = Column(UUID(as_uuid=True), ForeignKey("sectors.id", ondelete="SET NULL"), nullable=True)
    
    # Battle details
    phase = Column(String(50), default=BattlePhase.PREPARATION.value)
    started_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    ended_at = Column(DateTime, nullable=True)
    
    # Initial fleet sizes
    attacker_ships_initial = Column(Integer, default=0)
    defender_ships_initial = Column(Integer, default=0)
    
    # Outcome
    winner = Column(String(20))  # "attacker", "defender", "draw"
    attacker_ships_destroyed = Column(Integer, default=0)
    defender_ships_destroyed = Column(Integer, default=0)
    attacker_ships_retreated = Column(Integer, default=0)
    defender_ships_retreated = Column(Integer, default=0)
    
    # Damage statistics
    total_damage_dealt = Column(Integer, default=0)
    attacker_damage_dealt = Column(Integer, default=0)
    defender_damage_dealt = Column(Integer, default=0)
    
    # Battle events log (JSON array)
    battle_log = Column(JSON, default=list)
    
    # Loot and rewards
    credits_looted = Column(Integer, default=0)
    resources_looted = Column(JSON, default=dict)  # {"type": amount}
    
    # Relationships
    attacker_fleet = relationship("Fleet", foreign_keys=[attacker_fleet_id])
    defender_fleet = relationship("Fleet", foreign_keys=[defender_fleet_id])
    sector = relationship("Sector")
    ship_casualties = relationship("FleetBattleCasualty", back_populates="battle", cascade="all, delete-orphan")
    
    def __repr__(self):
        return f"<FleetBattle(id={self.id}, attacker={self.attacker_fleet_id}, defender={self.defender_fleet_id})>"


class FleetBattleCasualty(Base):
    """
    Record of individual ship casualties in a fleet battle.
    """
    __tablename__ = "fleet_battle_casualties"
    
    # Primary key
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    
    # References
    battle_id = Column(UUID(as_uuid=True), ForeignKey("fleet_battles.id", ondelete="CASCADE"), nullable=False, index=True)
    ship_id = Column(UUID(as_uuid=True), ForeignKey("ships.id", ondelete="SET NULL"), nullable=True)
    player_id = Column(UUID(as_uuid=True), ForeignKey("players.id", ondelete="SET NULL"), nullable=True)
    fleet_id = Column(UUID(as_uuid=True), ForeignKey("fleets.id", ondelete="SET NULL"), nullable=True)
    
    # Casualty details
    ship_name = Column(String(100))  # Store name in case ship is deleted
    ship_type = Column(String(50))
    was_attacker = Column(Boolean, default=True)
    
    # Outcome
    destroyed = Column(Boolean, default=False)
    retreated = Column(Boolean, default=False)
    damage_taken = Column(Integer, default=0)
    damage_dealt = Column(Integer, default=0)
    kills = Column(Integer, default=0)
    
    # Time of casualty
    casualty_time = Column(DateTime, default=datetime.utcnow)
    battle_phase = Column(String(50))
    
    # Relationships
    battle = relationship("FleetBattle", back_populates="ship_casualties")
    ship = relationship("Ship")
    player = relationship("Player")
    fleet = relationship("Fleet")
    
    def __repr__(self):
        return f"<FleetBattleCasualty(battle={self.battle_id}, ship={self.ship_name}, destroyed={self.destroyed})>"