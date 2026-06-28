"""
Drone model for the Sectorwars2102 game.

Drones are deployable units that can defend sectors, attack other drones,
and provide area control for players and teams.
"""

from uuid import uuid4
from datetime import datetime
from typing import Optional
from sqlalchemy import Column, String, DateTime, Integer, Float, Boolean, ForeignKey, Enum as SQLEnum
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
import enum

from src.core.database import Base


class DroneType(str, enum.Enum):
    """Types of drones in the game."""
    ATTACK = "attack"
    DEFENSE = "defense"
    SCOUT = "scout"
    MINING = "mining"
    REPAIR = "repair"


class DroneStatus(str, enum.Enum):
    """Status of a drone."""
    IDLE = "idle"  # Created but not deployed to any sector
    DEPLOYED = "deployed"
    COMBAT = "combat"
    RETURNING = "returning"
    DESTROYED = "destroyed"
    DAMAGED = "damaged"


class Drone(Base):
    """
    Individual drone unit that can be deployed by players.
    
    Drones provide area control, defense, and offensive capabilities.
    They can be deployed to sectors and engage in combat with other drones.
    """
    __tablename__ = "drones"
    
    # Primary key
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    
    # Ownership
    player_id = Column(UUID(as_uuid=True), ForeignKey("players.id", ondelete="CASCADE"), nullable=False, index=True)
    team_id = Column(UUID(as_uuid=True), ForeignKey("teams.id", ondelete="SET NULL"), nullable=True, index=True)
    
    # Drone properties
    drone_type = Column(String(50), nullable=False)
    name = Column(String(100))  # Optional custom name
    level = Column(Integer, default=1, nullable=False)
    health = Column(Integer, default=100, nullable=False)
    max_health = Column(Integer, default=100, nullable=False)
    attack_power = Column(Integer, default=10, nullable=False)
    defense_power = Column(Integer, default=10, nullable=False)
    speed = Column(Float, default=1.0, nullable=False)
    
    # Deployment information
    status = Column(String(50), default=DroneStatus.IDLE.value)
    sector_id = Column(UUID(as_uuid=True), ForeignKey("sectors.id", ondelete="SET NULL"), nullable=True, index=True)
    deployed_at = Column(DateTime, nullable=True)
    last_action = Column(DateTime, nullable=True)
    
    # Combat stats
    kills = Column(Integer, default=0, nullable=False)
    damage_dealt = Column(Integer, default=0, nullable=False)
    damage_taken = Column(Integer, default=0, nullable=False)
    battles_fought = Column(Integer, default=0, nullable=False)
    
    # Special abilities (JSON for flexibility)
    abilities = Column(String(255))  # Comma-separated list of ability IDs
    
    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow)
    destroyed_at = Column(DateTime, nullable=True)
    
    # Relationships
    player = relationship("Player", back_populates="drones")
    team = relationship("Team", back_populates="drones")
    sector = relationship("Sector", back_populates="deployed_drones")
    deployments = relationship("DroneDeployment", back_populates="drone", cascade="all, delete-orphan")
    
    def __repr__(self):
        return f"<Drone(id={self.id}, type={self.drone_type}, player={self.player_id}, status={self.status})>"
    
    def take_damage(self, damage: int) -> bool:
        """
        Apply damage to the drone.
        
        Args:
            damage: Amount of damage to apply
            
        Returns:
            True if drone is destroyed, False otherwise
        """
        self.health = max(0, self.health - damage)
        self.damage_taken += damage
        
        if self.health == 0:
            self.status = DroneStatus.DESTROYED.value
            self.destroyed_at = datetime.utcnow()
            return True
            
        elif self.health < self.max_health * 0.3:
            self.status = DroneStatus.DAMAGED.value
            
        return False
    
    def repair(self, amount: int) -> None:
        """Repair the drone by the specified amount."""
        self.health = min(self.max_health, self.health + amount)
        if self.health > self.max_health * 0.3 and self.status == DroneStatus.DAMAGED.value:
            self.status = DroneStatus.DEPLOYED.value


class DroneDeployment(Base):
    """
    Record of drone deployments for tracking history and sector control.
    """
    __tablename__ = "drone_deployments"
    
    # Primary key
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    
    # References
    drone_id = Column(UUID(as_uuid=True), ForeignKey("drones.id", ondelete="CASCADE"), nullable=False, index=True)
    player_id = Column(UUID(as_uuid=True), ForeignKey("players.id", ondelete="CASCADE"), nullable=False, index=True)
    sector_id = Column(UUID(as_uuid=True), ForeignKey("sectors.id", ondelete="CASCADE"), nullable=False, index=True)
    
    # Deployment details
    deployed_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    recalled_at = Column(DateTime, nullable=True)
    is_active = Column(Boolean, default=True, nullable=False)
    
    # Purpose/mission
    deployment_type = Column(String(50), default="defense")  # defense, patrol, mining, etc.
    target_id = Column(UUID(as_uuid=True), nullable=True)  # Optional target (e.g., specific port/planet)
    
    # Stats during this deployment
    enemies_destroyed = Column(Integer, default=0)
    resources_collected = Column(Integer, default=0)
    damage_prevented = Column(Integer, default=0)
    
    # Relationships
    drone = relationship("Drone", back_populates="deployments")
    player = relationship("Player", back_populates="drone_deployments")
    sector = relationship("Sector", back_populates="drone_deployments")
    
    def __repr__(self):
        return f"<DroneDeployment(id={self.id}, drone={self.drone_id}, sector={self.sector_id}, active={self.is_active})>"


class DroneCombat(Base):
    """
    Record of drone combat encounters.
    """
    __tablename__ = "drone_combats"
    
    # Primary key
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    
    # Combat participants
    attacker_drone_id = Column(UUID(as_uuid=True), ForeignKey("drones.id", ondelete="SET NULL"), nullable=True)
    defender_drone_id = Column(UUID(as_uuid=True), ForeignKey("drones.id", ondelete="SET NULL"), nullable=True)
    sector_id = Column(UUID(as_uuid=True), ForeignKey("sectors.id", ondelete="SET NULL"), nullable=True)
    
    # Combat details
    started_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    ended_at = Column(DateTime, nullable=True)
    rounds = Column(Integer, default=0)
    
    # Outcome
    winner_drone_id = Column(UUID(as_uuid=True), nullable=True)
    attacker_damage_dealt = Column(Integer, default=0)
    defender_damage_dealt = Column(Integer, default=0)
    
    # Combat log (JSON for detailed round-by-round data)
    combat_log = Column(String(2000))  # JSON string with combat details
    
    # Relationships
    attacker_drone = relationship("Drone", foreign_keys=[attacker_drone_id])
    defender_drone = relationship("Drone", foreign_keys=[defender_drone_id])
    sector = relationship("Sector")
    
    def __repr__(self):
        return f"<DroneCombat(id={self.id}, attacker={self.attacker_drone_id}, defender={self.defender_drone_id})>"