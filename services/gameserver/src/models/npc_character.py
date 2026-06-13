"""NPC data models — NPCCharacter, NPCRoster, NPCDeathLog.

Canon: sw2102-docs/DATA_MODELS/npcs.md, SYSTEMS/npc-scheduler.md (Loops
A/B/C, engagement routing, KIA processing), SYSTEMS/npc-lifecycle.md
(schedules, careers, succession), ADR-0063 (respawn vs recruit-stage,
zero-gap promotion).

Divergences from canon, on purpose, documented:

- ``current_sector_id`` stores the GLOBAL ``sectors.sector_id`` (globally
  unique in this gameserver schema) rather than canon's region-local
  compound ``(home_region_id, current_sector_id)``. ``home_region_id``
  exists and is backfilled from the live sector's region.
- ``bang_roster_ref`` is the spawn idempotency marker (``"<galaxy
  id>:<region_type>[:<kind>]:<bang roster id>"``); NPCRoster rows carry
  the same ref so rosters adopt the NPCs already materialized under it.
- Canon declares ``UNIQUE (region_id, faction_code, role)`` on NPCRoster,
  but BANG emits MULTIPLE pirate rosters per region (one per holding
  anchor), so that constraint cannot hold against the live data shape.
  Uniqueness lives on ``bang_roster_ref`` instead, with a non-unique
  index on the canon triple — divergence FLAGGED for the docs repo.
- Lodging FKs (home_barracks_id / home_outlaw_base_id) and the
  NPCBarracks / OutlawBase tables are deferred to the lodging slice.
- Enum members follow this codebase's UPPERCASE name==value convention
  (see ShipStatus); canon spells the vocabularies lowercase.

Canon gaps flagged for the docs repo (declared here, NOT resolved):

- npcs.md says "nine archetypes" in prose but lists ten in its enum
  section (STATION_SECURITY is the tenth) — canon-internal conflict.
- Respawn semantics: ADR-0063 N-D2's three-timer model (15-min
  same-identity respawn for permitted archetypes vs 7-day recruit STAGE
  on an immediately-spawned successor) supersedes npc-scheduler.md KIA
  step 9 "permanently gone" and police-forces.md's 7-day VACANCY prose.
- ``credits`` (trader wallet) is canon-silent for spot market trades —
  pending decision; only contract escrow is specified ("infinite").
"""

import uuid
import enum

from sqlalchemy import (
    Column,
    DateTime,
    String,
    Integer,
    ForeignKey,
    Enum,
    Index,
    func,
)
from sqlalchemy.dialects.postgresql import UUID, JSONB
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


class NPCActivity(enum.Enum):
    """Canon `activity` vocabulary (SYSTEMS/npc-lifecycle.md schedule
    blocks). Declared in full so later slices never need an enum ALTER;
    the scheduler no-ops gracefully on activities it does not yet drive.
    """

    SLEEP = "SLEEP"
    COMMUTE = "COMMUTE"
    PATROL = "PATROL"
    WORK_STATION = "WORK_STATION"
    SOCIALIZE = "SOCIALIZE"
    DINE = "DINE"
    TRAIN = "TRAIN"
    PERSONAL = "PERSONAL"
    RAID = "RAID"
    SURVEY = "SURVEY"
    ENGAGED = "ENGAGED"
    REASSIGNED = "REASSIGNED"
    SHIFT_HANDOFF = "SHIFT_HANDOFF"
    SHIFT_REROUTE = "SHIFT_REROUTE"
    ERROR_STRANDED = "ERROR_STRANDED"


class NPCLifecycleStage(enum.Enum):
    """Canon `lifecycle_stage` vocabulary (DATA_MODELS/npcs.md).

    ADR-0063: RECRUIT is a 7-canonical-day reduced-stat STAGE on an
    immediately-spawned successor — never a roster vacancy.
    """

    RECRUIT = "RECRUIT"
    ACTIVE = "ACTIVE"
    SENIOR = "SENIOR"
    DECORATED = "DECORATED"
    RETIRED = "RETIRED"
    KIA = "KIA"
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

    # Spawn idempotency marker — "<galaxy id>:<region_type>[:<kind>]:<bang
    # roster id>". NPCRoster rows carry the same ref so a roster adopts
    # the NPC rows already materialized under it.
    bang_roster_ref = Column(String(80), nullable=True, index=True)

    # --- Scheduler / lifecycle columns (DATA_MODELS/npcs.md) ---

    # Canon NN; nullable here because pre-runtime rows may predate the
    # backfill — the scheduler treats NULL as "not yet adopted".
    home_region_id = Column(
        UUID(as_uuid=True),
        ForeignKey("regions.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    # Canon default 'sleep'; spawn paths set it explicitly.
    current_activity = Column(
        Enum(NPCActivity, name="npc_activity"),
        nullable=False,
        default=NPCActivity.SLEEP,
    )
    lifecycle_stage = Column(
        Enum(NPCLifecycleStage, name="npc_lifecycle_stage"),
        nullable=False,
        default=NPCLifecycleStage.RECRUIT,
    )
    # 24h schedule template + weekly overrides (SYSTEMS/npc-lifecycle.md
    # JSONB shape: timezone, shift_offset_hours, blocks[], weekly_overrides[]).
    daily_schedule = Column(JSONB, nullable=False, default=dict)
    # Mutable squad role (ADR-0063): primary_marshal / backup_marshal / ...
    duty_role = Column(String(50), nullable=True)
    # ADR-0063: recruits ARE engagement-eligible; this gates `train`
    # blocks and similar windows only.
    engagement_eligible_at = Column(DateTime(timezone=True), nullable=True)
    promotion_pending_at = Column(DateTime(timezone=True), nullable=True)
    # Succession chain (canon: replaced_by_id) and mentor lore hook.
    replaced_by_id = Column(
        UUID(as_uuid=True),
        ForeignKey("npc_characters.id", ondelete="SET NULL"),
        nullable=True,
    )
    mentor_id = Column(
        UUID(as_uuid=True),
        ForeignKey("npc_characters.id", ondelete="SET NULL"),
        nullable=True,
    )
    # Career arc (current_role, role_history[], decorations[]).
    role_history = Column(JSONB, nullable=False, default=dict)
    # origin / personality_traits / skills (npcs.md backstory shape).
    backstory = Column(JSONB, nullable=False, default=dict)
    # TRADER wallet — canon-silent for spot market trades (pending
    # decision); profit feeds the 100-route career metric.
    credits = Column(Integer, nullable=False, default=0)
    # Notoriety 0–100 (TRADER scruples axis): low = reputable merchant
    # (attacking is a crime — innocent-attack rep penalty), high = unscrupulous
    # smuggler / black-marketeer (a lawful target). Nullable so pre-existing
    # rows backfill at scheduler startup. Maps to the canon Shadow-Syndicate /
    # smuggling reputation surface (ADR-0018/0032, ADR-0042 attack_innocent).
    notoriety = Column(Integer, nullable=True)

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


class NPCRoster(Base):
    """Per-anchor NPC role target (DATA_MODELS/npcs.md NPCRoster).

    Loop B (SYSTEMS/npc-scheduler.md) counts live NPCs per roster and
    spawns replacements toward ``target_count``.

    Documented divergence: canon's ``UNIQUE (region_id, faction_code,
    role)`` cannot hold — BANG emits multiple pirate rosters per region
    (one per holding anchor) — so uniqueness lives on ``bang_roster_ref``
    and the canon triple gets a non-unique index (flagged for docs repo).
    ``host_sector_id`` (global) anchors respawn placement; canon ties
    placement to default lodging, which is deferred with the lodging
    tables.
    """

    __tablename__ = "npc_rosters"
    __table_args__ = (
        Index("ix_npc_rosters_region_faction_role", "region_id", "faction_code", "role"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    region_id = Column(
        UUID(as_uuid=True),
        ForeignKey("regions.id", ondelete="CASCADE"),
        nullable=False,
    )
    faction_code = Column(String(50), nullable=False)
    # Bang roster kinds double as canon roles: pirate_captain,
    # federation_marshal, marshal_captain, nexus_sentinel,
    # sentinel_captain, merchant_captain (gameserver-seeded).
    role = Column(String(50), nullable=False)
    default_archetype = Column(Enum(NPCArchetype, name="npc_archetype"), nullable=False)
    # Base daily_schedule applied (with stagger offsets) to spawned NPCs.
    schedule_template = Column(JSONB, nullable=False, default=dict)
    # Lodging deferred — kept for forward-compat with bang's roster shape
    # (defaultLodgingId is always null today).
    default_lodging_id = Column(UUID(as_uuid=True), nullable=True)
    default_lodging_type = Column(String(20), nullable=True)
    target_count = Column(Integer, nullable=False)
    # {"names": [...]} — bang emits a flat name array; canon's
    # first_names/surnames split is unused by bang output.
    name_pool = Column(JSONB, nullable=False, default=dict)
    # Global sectors.sector_id where this roster's NPCs spawn/respawn.
    host_sector_id = Column(Integer, nullable=False)
    # Adoption link to NPCCharacter.bang_roster_ref (unique — see
    # divergence note). Gameserver-seeded rosters (traders) synthesize
    # their own refs.
    bang_roster_ref = Column(String(80), nullable=False, unique=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    def __repr__(self) -> str:
        return (
            f"<NPCRoster {self.role}/{self.faction_code} x{self.target_count} "
            f"@sector {self.host_sector_id}>"
        )


class NPCDeathLog(Base):
    """Kill audit trail (DATA_MODELS/npcs.md NPCDeathLog) — KIA
    processing step 3 (SYSTEMS/npc-scheduler.md)."""

    __tablename__ = "npc_death_log"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    npc_id = Column(
        UUID(as_uuid=True),
        ForeignKey("npc_characters.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    killed_by_player_id = Column(
        UUID(as_uuid=True),
        ForeignKey("players.id", ondelete="SET NULL"),
        nullable=True,
    )
    # Global sectors.sector_id (same divergence as NPCCharacter).
    sector_id = Column(Integer, nullable=False)
    # Snapshot at kill time (canon NN; nullable for pre-runtime rows).
    home_region_id = Column(
        UUID(as_uuid=True),
        ForeignKey("regions.id", ondelete="SET NULL"),
        nullable=True,
    )
    combat_log_id = Column(
        UUID(as_uuid=True),
        ForeignKey("combat_logs.id", ondelete="SET NULL"),
        nullable=True,
    )
    destruction_cause = Column(String, nullable=True)
    killed_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    def __repr__(self) -> str:
        return f"<NPCDeathLog npc={self.npc_id} at sector {self.sector_id}>"
