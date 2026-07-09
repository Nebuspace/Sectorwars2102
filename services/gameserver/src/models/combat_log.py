import uuid
from datetime import datetime
from typing import Optional, Dict, Any
from enum import Enum
from sqlalchemy import Column, DateTime, String, Integer, Float, ForeignKey, Text, JSON, Boolean
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship
from sqlalchemy import func

from src.core.database import Base


class CombatOutcome(str, Enum):
    """Combat outcome enumeration."""
    ONGOING = "ongoing"
    ATTACKER_WIN = "attacker_win"
    DEFENDER_WIN = "defender_win"
    DRAW = "draw"
    ESCAPED = "escaped"


class CombatLog(Base):
    __tablename__ = "combat_logs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    
    # Combat participants
    attacker_id = Column(UUID(as_uuid=True), ForeignKey("players.id", ondelete="SET NULL"), nullable=True)
    defender_id = Column(UUID(as_uuid=True), ForeignKey("players.id", ondelete="SET NULL"), nullable=True)
    
    # Ship information at time of combat
    attacker_ship_id = Column(UUID(as_uuid=True), ForeignKey("ships.id", ondelete="SET NULL"), nullable=True)
    defender_ship_id = Column(UUID(as_uuid=True), ForeignKey("ships.id", ondelete="SET NULL"), nullable=True)
    
    attacker_ship_name = Column(String(100), nullable=True)
    defender_ship_name = Column(String(100), nullable=True)
    attacker_ship_type = Column(String(50), nullable=True)
    defender_ship_type = Column(String(50), nullable=True)
    
    # Combat location
    sector_id = Column(Integer, nullable=True)  # Human-readable sector number (1, 2, 3, etc.)
    sector_uuid = Column(UUID(as_uuid=True), ForeignKey("sectors.id", ondelete="SET NULL"), nullable=True)
    port_id = Column(UUID(as_uuid=True), ForeignKey("stations.id", ondelete="SET NULL"), nullable=True)
    planet_id = Column(UUID(as_uuid=True), ForeignKey("planets.id", ondelete="SET NULL"), nullable=True)

    # Region-deletion handling (ADR-0050 SK24, DATA_MODELS/combat.md
    # "Region-deletion handling"): a plain UUID snapshot of the sector's
    # region at combat time, deliberately WITHOUT a ForeignKey. sector_uuid
    # above already SETs NULL when its sector cascades away on region
    # regeneration/termination; this column is what lets the row still say
    # WHICH region the fight happened in after that sector row is gone, so
    # it must survive the region itself being deleted (an FK to regions.id
    # would either block that deletion or SET NULL too, defeating the point).
    region_id_snapshot = Column(UUID(as_uuid=True), nullable=True)
    
    # Combat details
    combat_type = Column(String(50), nullable=False, default="ship_to_ship")  # ship_to_ship, port_attack, planet_defense
    outcome = Column(String(20), nullable=False)  # attacker_win, defender_win, draw, escaped
    
    # Forces at combat start
    attacker_drones = Column(Integer, nullable=False, default=0)
    defender_drones = Column(Integer, nullable=False, default=0)
    attacker_attack_drones = Column(Integer, nullable=False, default=0)
    attacker_defense_drones = Column(Integer, nullable=False, default=0)
    defender_attack_drones = Column(Integer, nullable=False, default=0)
    defender_defense_drones = Column(Integer, nullable=False, default=0)

    # Combat results
    attacker_damage_dealt = Column(Integer, nullable=False, default=0)
    defender_damage_dealt = Column(Integer, nullable=False, default=0)
    attacker_drones_lost = Column(Integer, nullable=False, default=0)
    defender_drones_lost = Column(Integer, nullable=False, default=0)
    
    # Loot and rewards
    credits_looted = Column(Integer, nullable=False, default=0)
    cargo_looted = Column(JSONB, nullable=True)  # {commodity: quantity, ...}
    experience_gained = Column(Integer, nullable=False, default=0)
    
    # Combat metadata
    combat_duration = Column(Float, nullable=False, default=0.0)  # in seconds
    rounds = Column(Integer, nullable=False, default=1)
    combat_log = Column(Text, nullable=True)  # Detailed combat log text
    
    # Timestamps
    timestamp = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)  # Primary timestamp for queries
    started_at = Column(DateTime(timezone=True), nullable=False, default=func.now())
    ended_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    
    # Admin resolution fields
    admin_notes = Column(Text, nullable=True)
    admin_resolved = Column(Boolean, nullable=False, default=False)
    admin_resolved_at = Column(DateTime(timezone=True), nullable=True)
    
    # Flags
    disputed = Column(Boolean, nullable=False, default=False)
    resolved = Column(Boolean, nullable=False, default=True)
    admin_reviewed = Column(Boolean, nullable=False, default=False)
    
    # Relationships
    attacker = relationship("Player", foreign_keys=[attacker_id], back_populates="combat_logs_as_attacker")
    defender = relationship("Player", foreign_keys=[defender_id], back_populates="combat_logs_as_defender")
    attacker_ship = relationship("Ship", foreign_keys=[attacker_ship_id])
    defender_ship = relationship("Ship", foreign_keys=[defender_ship_id])
    sector = relationship("Sector")
    station = relationship("Station")
    planet = relationship("Planet")


class CombatStats(Base):
    __tablename__ = "combat_stats"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    
    # Date for stats aggregation
    date = Column(DateTime(timezone=True), nullable=False, unique=True)
    
    # Daily combat statistics
    total_combats = Column(Integer, nullable=False, default=0)
    ship_combats = Column(Integer, nullable=False, default=0)
    port_attacks = Column(Integer, nullable=False, default=0)
    planet_defenses = Column(Integer, nullable=False, default=0)
    
    # Combat outcomes
    attacker_wins = Column(Integer, nullable=False, default=0)
    defender_wins = Column(Integer, nullable=False, default=0)
    draws = Column(Integer, nullable=False, default=0)
    escapes = Column(Integer, nullable=False, default=0)
    
    # Economic impact
    total_credits_looted = Column(Integer, nullable=False, default=0)
    total_cargo_looted_value = Column(Integer, nullable=False, default=0)
    average_loot_per_combat = Column(Float, nullable=False, default=0.0)
    
    # Combat efficiency
    total_drones_lost = Column(Integer, nullable=False, default=0)
    average_combat_duration = Column(Float, nullable=False, default=0.0)
    most_effective_ship_type = Column(String(50), nullable=True)
    
    # Player statistics
    most_active_attacker_id = Column(UUID(as_uuid=True), ForeignKey("players.id", ondelete="SET NULL"), nullable=True)
    most_active_defender_id = Column(UUID(as_uuid=True), ForeignKey("players.id", ondelete="SET NULL"), nullable=True)
    unique_combatants = Column(Integer, nullable=False, default=0)
    
    # Metadata
    calculated_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    
    # Relationships
    most_active_attacker = relationship("Player", foreign_keys=[most_active_attacker_id])
    most_active_defender = relationship("Player", foreign_keys=[most_active_defender_id])


# Add combat relationships to existing models
# This would be added to the Player model:
# combat_logs_as_attacker = relationship("CombatLog", foreign_keys="CombatLog.attacker_id", back_populates="attacker")
# combat_logs_as_defender = relationship("CombatLog", foreign_keys="CombatLog.defender_id", back_populates="defender")