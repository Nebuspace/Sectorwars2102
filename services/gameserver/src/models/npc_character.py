"""NPCCharacter — named, persistent NPC identity (v1 subset).

Canon: sw2102-docs/DATA_MODELS/npcs.md (NPCCharacter schema). The full
canon schema (daily_schedule, role_history, lifecycle_stage, lodging FKs,
mentor/succession chain, backstory, NPCDeathLog, NPCRoster) is Design-only;
this v1 model carries only the columns the static pirate-captain spawn
slice needs. Upgrade path: SYSTEMS/npc-scheduler.md (movement, engagement
routing, Loop B respawn) and SYSTEMS/npc-lifecycle.md.

Divergences from canon, on purpose, documented:

- ``current_sector_id`` stores the GLOBAL ``sectors.sector_id`` (globally
  unique in this gameserver schema) rather than canon's region-local
  compound ``(home_region_id, current_sector_id)`` — ``home_region_id``
  is deferred with the rest of the lifecycle columns.
- ``bang_roster_ref`` is a v1 idempotency marker (``"<galaxy
  id>:<region_type>:<bang roster id>"`` — galaxy-scoped so two galaxies
  sharing region types and roster ids never collide) standing in for the
  canon ``NPCRoster`` FK until that table lands.
- Enum members follow this codebase's UPPERCASE name==value convention
  (see ShipStatus); canon spells the npc_status vocabulary lowercase.

Canon gaps flagged for the docs repo (declared here, NOT resolved):

- npcs.md says "nine archetypes" in prose but lists ten in its enum
  section (STATION_SECURITY is the tenth) — canon-internal conflict.
- Respawn vs permanent KIA: npcs.md ADR-0063 N-D2 treats named-NPC death
  as permanent, while npc-scheduler.md "KIA processing" step 9 describes
  a RESPAWNING path with cooldown — canon-internal tension. Both enum
  members are declared so either reading can be implemented without an
  enum migration once canon settles it.
"""

import uuid
import enum

from sqlalchemy import Column, DateTime, String, Integer, ForeignKey, Enum, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from src.core.database import Base


class NPCArchetype(enum.Enum):
    """Canon archetypes (DATA_MODELS/npcs.md `npc_archetype`).

    Canon gap: npcs.md's prose says "nine archetypes" but its enum
    section lists ten (including STATION_SECURITY). We declare all ten;
    the prose/enum conflict is flagged for the docs repo, not resolved.

    v1 only spawns HOSTILE_RAIDER (pirate captains); the full vocabulary
    is declared up front so later slices don't need a Postgres enum ALTER.
    """

    LAW_ENFORCEMENT = "LAW_ENFORCEMENT"
    FACTION_PATROL = "FACTION_PATROL"
    HOSTILE_RAIDER = "HOSTILE_RAIDER"
    FACTION_LEADER = "FACTION_LEADER"
    STATION_OFFICIAL = "STATION_OFFICIAL"
    STATION_SECURITY = "STATION_SECURITY"
    MISSION_GIVER = "MISSION_GIVER"
    TRADER = "TRADER"
    RESEARCHER = "RESEARCHER"
    CIVILIAN = "CIVILIAN"


class NPCStatus(enum.Enum):
    """Canon `npc_status` vocabulary (DATA_MODELS/npcs.md).

    Canon gap: npcs.md ADR-0063 N-D2 (permanent KIA for named NPCs) is in
    tension with npc-scheduler.md "KIA processing" step 9 (RESPAWNING
    with cooldown). RESPAWNING is declared so either reading works
    without an enum migration; the tension is flagged for the docs repo,
    not resolved here.

    v1 uses ON_DUTY / ENGAGED / KIA only; the rest are declared so the
    scheduler slices can use them without an enum migration.
    """

    ON_DUTY = "ON_DUTY"
    OFF_DUTY = "OFF_DUTY"
    ENGAGED = "ENGAGED"
    ENGAGED_PENDING_ARRIVAL = "ENGAGED_PENDING_ARRIVAL"
    KIA = "KIA"
    RESPAWNING = "RESPAWNING"
    RETIRED = "RETIRED"
    REASSIGNED = "REASSIGNED"


class NPCCharacter(Base):
    __tablename__ = "npc_characters"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # Identity (canon: name non-unique; title rendered before name in UI)
    name = Column(String(100), nullable=False)
    title = Column(String(50), nullable=True)
    faction_code = Column(String(50), nullable=False, index=True)
    archetype = Column(Enum(NPCArchetype, name="npc_archetype"), nullable=False)
    status = Column(
        Enum(NPCStatus, name="npc_status"),
        nullable=False,
        default=NPCStatus.ON_DUTY,
    )

    # Location — GLOBAL sectors.sector_id (see module docstring divergence
    # note). NULL = deceased / between assignments.
    current_sector_id = Column(Integer, nullable=True, index=True)

    # 1:1 piloted ship (canon "Single-pilot" invariant). SET NULL so the
    # character row survives ship-row deletion.
    ship_id = Column(
        UUID(as_uuid=True),
        ForeignKey("ships.id", ondelete="SET NULL"),
        nullable=True,
    )

    # v1 idempotency marker — "<galaxy id>:<region_type>:<bang roster id>".
    # Replaced by an NPCRoster FK when DATA_MODELS/npcs.md's roster table
    # lands.
    bang_roster_ref = Column(String(80), nullable=True, index=True)

    # Lifecycle timestamps (canon names)
    spawned_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    last_seen_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    destroyed_at = Column(DateTime(timezone=True), nullable=True)
    respawn_eligible_at = Column(DateTime(timezone=True), nullable=True)

    # Relationships
    ship = relationship("Ship", foreign_keys=[ship_id])

    def __repr__(self) -> str:
        label = f"{self.title} {self.name}" if self.title else self.name
        return f"<NPCCharacter {label} ({self.faction_code}/{self.status.name})>"

    @property
    def display_name(self) -> str:
        """Player-facing name — canon renders title before name."""
        return f"{self.title} {self.name}" if self.title else self.name
