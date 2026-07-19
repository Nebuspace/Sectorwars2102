"""PirateHolding — the ecosystem's population unit (WO-PIRATE-ECO-1).

Canon: sw2102-docs/SYSTEMS/pirate-ecosystem.md (ADR-0048, population score
:45-64), pirate-holding-raid.md (ADR-0047, strength-state fields :71-89).

Scope: this is the FOUNDATION slice only. Fields needed by
``pirate_ecosystem_service``'s population-score / target / cap / cleansed
math and by the eligible-sector finder. Deliberately OMITTED (deferred to the
raid/capture WO and to ECO-2's growth tick, which is entirely unbuilt at
HEAD):

- ``outlaw_base_id`` FK — the OutlawBase/NPCBarracks lodging tables don't
  exist yet (see npc_character.py's module docstring, same deferral).
- ``owner_team_id`` / ``captured_at`` / ``combat_lock_*`` /
  ``special_formation_id`` / ``interior_sector_ids`` / ``parent_holding_id`` /
  ``composition`` — all raid/capture/spawn-algorithm state per
  pirate-holding-raid.md; nothing in this WO's scope writes or reads them.

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
from sqlalchemy.dialects.postgresql import UUID
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
        Index("ix_pirate_holdings_region_owner", "region_id", "owner_player_id"),
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
