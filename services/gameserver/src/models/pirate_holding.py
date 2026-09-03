"""PirateHolding — the ecosystem's population unit (WO-PIRATE-ECO-1).

Canon: sw2102-docs/SYSTEMS/pirate-ecosystem.md (ADR-0048, population score
:45-64), pirate-holding-raid.md (ADR-0047, strength-state fields :71-89).

Scope: this is the FOUNDATION slice plus LEG-4177's lodging-anchor kernel.
Fields needed by ``pirate_ecosystem_service``'s population-score / target /
cap / cleansed math and by the eligible-sector finder.

- ``outlaw_base_id`` FK — nullable UUID to ``outlaw_bases.id`` (ON DELETE
  SET NULL). Canon DATA_MODELS/pirate-holdings.md lists this as NOT NULL
  1:1; existing holdings have no base rows to attach, so this slice is
  nullable with no backfill. Unique on the column (Postgres allows many
  NULLs) is the 1:1 guard. OutlawBase→NPCBarracks conversion stays out of
  scope (ADR-0060 G-V2 still has no conversion path).
- Deliberately OMITTED: ``interior_sector_ids`` / ``parent_holding_id`` /
  ``composition`` — spawn-algorithm state per pirate-holding-raid.md.

ADR-0060 (Group A pirate-ecosystem/holdings hardening, G-F2/G-V1/R-F1) adds
the raid/capture kernel's columns — ``combat_lock_held_by``,
``combat_lock_team_snapshot``, ``owner_team_id``, ``captured_at``,
``evolution_clock_started_at`` — plus ``formation_id`` (needed by R-F1's
Stronghold-formation CHECK constraint, itself no-code-change per canon's
G-D1). These are a DORMANT KERNEL: fully wired at the model + service layer
(see ``pirate_ecosystem_service.acquire_combat_lock`` /
``can_engage`` / ``release_combat_lock`` / ``capture_holding``), zero live
callers — no player-facing raid/capture entry point exists anywhere in the
codebase yet (verify-first confirmed, orchestrator-ruled 2026-08-07). Awaits
WO-PIRATE-ECO-3-ATTEMPT-CAPTURE to wire a real raid-initiation route. This
mirrors the established dormant-kernel pattern elsewhere in this codebase
(see e.g. structures.py's CRT-spine kernels, planet_grid.py's K1b2
terraform-grid kernel).

ADR-0060 G-V2 (abandoned-holding re-seeding race guard, "explicit
``combat_lock_held_by IS NULL`` predicate ... The OutlawBase/NPCBarracks
conversion path runs only when no active combat is in progress"): NOT
implemented, on purpose -- verify-first grep (2026-08-06, this WO) of the
whole ``services/gameserver/src`` tree found NO OutlawBase/NPCBarracks
conversion path, and no re-seeding/abandoned-holding mechanism of ANY kind,
live or dormant. Only ``npc_character.py``'s own module docstring and
``npc_tick_loops.py``'s scheduler note reference OutlawBase/NPCBarracks, both
saying the lodging tables are "deferred to the lodging slice" -- there is no
query, service function, or scheduler sweep this WO could add a
``combat_lock_held_by IS NULL`` predicate TO. G-V2 has no buildable kernel
yet; it is blocked on the lodging slice landing first (the tables + the
re-seed mechanism itself), not merely dormant-for-a-caller like the
raid/capture kernel above. Flagged for DECISIONS/BACKLOG rather than
inventing a re-seed mechanism this WO has no canon basis to design.

Divergences from canon, on purpose, documented:

- Enum members follow this codebase's UPPERCASE name==value convention (see
  ShipStatus / NPCArchetype); canon spells the tier vocabulary lowercase
  (camp/outpost/stronghold).
- ``sector_id`` stores the GLOBAL ``sectors.sector_id`` integer (mirrors
  ``Station.sector_id`` / ``NPCCharacter.current_sector_id``), not a UUID FK
  to ``sectors.id``. Canon's raid doc calls this field ``anchor_sector_id``;
  this foundation slice uses the WO's literal ``sector_id`` name. Only the
  anchor is modeled here — canon's multi-sector Outpost/Stronghold
  ``interior_sector_ids`` is deferred with the raid-mechanics fields above.
- ``tier_recovery_rate`` (pirate-holding-raid.md:77, Camp 0.25/Outpost
  0.10/Stronghold 0.03 per day) is COMPUTED from tier, not stored — there is
  no recovery-tick service yet to read a stored column, and a computed
  property can't drift from the canon table.
"""

import enum
import uuid

from sqlalchemy import (
    Column,
    DateTime,
    Float,
    ForeignKey,
    CheckConstraint,
    Enum,
    Index,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy import Integer

from src.core.database import Base


class PirateHoldingTier(enum.Enum):
    """Canon tier vocabulary (pirate-ecosystem.md:49-53 population weights;
    pirate-holding-raid.md:113-118 respawn tiers). UPPERCASE name==value —
    see module docstring divergence note."""

    CAMP = "CAMP"
    OUTPOST = "OUTPOST"
    STRONGHOLD = "STRONGHOLD"


# Per-day recovery rate by tier (pirate-holding-raid.md:77, :96
# TIER_RECOVERY_RATE). Backs the computed `tier_recovery_rate` property —
# there is no recovery-tick service yet (deferred), but the rate itself is
# canon-fixed and safe to expose now.
_TIER_RECOVERY_RATE = {
    PirateHoldingTier.CAMP: 0.25,
    PirateHoldingTier.OUTPOST: 0.10,
    PirateHoldingTier.STRONGHOLD: 0.03,
}


class PirateHolding(Base):
    __tablename__ = "pirate_holdings"
    __table_args__ = (
        CheckConstraint(
            "current_strength >= 0.0 AND current_strength <= 1.0",
            name="valid_pirate_holding_current_strength",
        ),
        # ADR-0060 R-F1, verbatim. Enforced at insert + update time; the two
        # existing tier-promotion paths (capture-evolution and worldgen
        # pre-seed) already set formation_id in the same statement, so the
        # constraint is defensive per the ADR's own framing.
        CheckConstraint(
            "tier != 'STRONGHOLD' OR formation_id IS NOT NULL",
            name="pirate_holdings_stronghold_requires_formation",
        ),
        Index("ix_pirate_holdings_region_owner", "region_id", "owner_player_id"),
        # LEG-4177: 1:1 lodging anchor without NOT NULL. Multiple NULLs are
        # allowed; a non-NULL outlaw_base_id may appear on at most one holding.
        Index(
            "uq_pirate_holdings_outlaw_base_id",
            "outlaw_base_id",
            unique=True,
        ),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    region_id = Column(
        UUID(as_uuid=True),
        ForeignKey("regions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # GLOBAL sectors.sector_id — see module docstring divergence note.
    sector_id = Column(Integer, nullable=False, index=True)

    # LEG-4177 lodging kernel. Nullable — no backfill. Unique index above.
    outlaw_base_id = Column(
        UUID(as_uuid=True),
        ForeignKey("outlaw_bases.id", ondelete="SET NULL"),
        nullable=True,
    )

    tier = Column(Enum(PirateHoldingTier, name="pirate_holding_tier"), nullable=False)

    # Non-NULL = player-captured (pirate-ecosystem.md:59 "not player-captured"
    # exclusion in compute_population_score). SET NULL so a deleted player
    # doesn't cascade-delete a holding row; ownership just clears.
    owner_player_id = Column(
        UUID(as_uuid=True),
        ForeignKey("players.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    current_strength = Column(Float, nullable=False, default=1.0)
    last_damage_at = Column(DateTime(timezone=True), nullable=True)

    # R-F1's CHECK-constraint dependency. Deferred-then-added: ADR-0060's
    # module docstring above formerly listed this as a raid-lane omission;
    # R-F1's constraint text names it directly, so it ships here rather than
    # staying absent while the constraint referenced a nonexistent column.
    formation_id = Column(
        UUID(as_uuid=True),
        ForeignKey("special_formations.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # --- ADR-0060 raid/capture kernel (G-F2/G-V1) — DORMANT, see module
    # docstring. Nullable; NULL == "no active raid" / "not captured". ---

    combat_lock_held_by = Column(
        UUID(as_uuid=True),
        ForeignKey("players.id", ondelete="SET NULL"),
        nullable=True,
    )
    # Frozen at first team-mate engagement (G-F2 snapshot semantics) — NOT
    # live team membership, closing the late-join exploit the ADR names.
    combat_lock_team_snapshot = Column(ARRAY(UUID(as_uuid=True)), nullable=True)

    # Non-NULL == team-captured (mirrors owner_player_id's player-capture
    # marker above; the two are mutually exclusive at the raid-service
    # layer, not enforced by a DB constraint — ADR-0060 doesn't specify one).
    owner_team_id = Column(
        UUID(as_uuid=True),
        ForeignKey("teams.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    captured_at = Column(DateTime(timezone=True), nullable=True)

    # G-I1 evolution-clock reset threshold (>=5% single-event citadel
    # damage). Dormant alongside the rest of this kernel — no live writer
    # until the raid/capture entry point (ECO-3) exists.
    evolution_clock_started_at = Column(DateTime(timezone=True), nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    def __repr__(self) -> str:
        owner = f" owner={self.owner_player_id}" if self.owner_player_id else ""
        return f"<PirateHolding {self.tier.name if self.tier else '?'} @sector {self.sector_id}{owner}>"

    @property
    def tier_recovery_rate(self) -> float:
        """Per-day recovery rate for this holding's tier (pirate-holding-raid.md:77).
        Computed, not stored — see module docstring."""
        return _TIER_RECOVERY_RATE[self.tier]

    @property
    def is_pirate_controlled(self) -> bool:
        """True when NOT player-captured (pirate-ecosystem.md:59)."""
        return self.owner_player_id is None
