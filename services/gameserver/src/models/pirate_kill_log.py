"""PirateKillLog — rolling 30-day kill-weight feed (WO-PIRATE-ECO-1).

Canon: sw2102-docs/SYSTEMS/pirate-ecosystem.md:95-120 (table shape,
``sum_kill_weights``), pirate-holding-raid.md:159-169 (capture inserts this
row atomically with the ownership flip — that wiring is a separate lane/WO;
this module is the model only).

Per canon's verbatim framing (:95-96, "A new table tracks each holding-CLEAR
event"), every row is HOLDING-shaped, not ship-kill-shaped — the feeder
(npc_spawn_service.py, a sibling lane) fires only on holding-linked final-KIA
events (a holding always in hand), never on a generic/unrelated NPC kill.
``region_id`` is therefore always resolvable (``PirateHolding.region_id`` is
NOT NULL) and this model follows canon exactly on that point — see the
CORRECTED note below (an earlier draft wrongly relaxed it for a since-refuted
every-ship-kill premise).

Divergences from canon, on purpose, documented:

- ``attacker_player_id`` is NULLABLE (canon: FK players.id, unmarked but
  implied required by the capture-transaction pseudocode at :162-169). Made
  defensively nullable — mirrors ``NPCDeathLog.killed_by_player_id`` — since
  ``sum_kill_weights`` never reads this column; only future medal-attribution
  (``top_attackers_by_kill_weight``, ECO-2+) will, and that lane can treat a
  NULL attacker as "no medal eligible" (an NPC-vs-NPC or environmental final
  KIA with no credited player) rather than the feeder failing. Flagged for
  the docs repo's DOC-GAP list, not silently added.
- Enum members follow this codebase's UPPERCASE name==value convention (see
  ``PirateHoldingTier`` in pirate_holding.py); canon spells both vocabularies
  lowercase.

CORRECTED (lane-A review): an earlier draft of this model made ``region_id``
nullable and added an out-of-canon ``sector_id`` column, both premised on the
feeder firing for every generic NPC kill (a "must never fail on missing
context" concern). That premise was refuted on canon re-read — the log is
holding-CLEAR-event-shaped, always carries a holding, and therefore always
has a region. ``region_id`` is now canon-exact (NOT NULL); ``sector_id`` is
dropped entirely (not in canon's table; sector, when needed, resolves via
``holding_id`` -> ``PirateHolding.sector_id``).
"""

import enum
import uuid

from sqlalchemy import Column, DateTime, ForeignKey, Enum, Index, Integer, func

from sqlalchemy.dialects.postgresql import UUID

from src.core.database import Base
from src.models.pirate_holding import PirateHoldingTier


class PirateKillDisposition(enum.Enum):
    """Canon `disposition` vocabulary (pirate-ecosystem.md:108)."""

    CAPTURED = "CAPTURED"
    CLEARED = "CLEARED"


class PirateKillLog(Base):
    __tablename__ = "pirate_kill_log"
    __table_args__ = (
        # Rolling-30-day aggregate read path (pirate-ecosystem.md:111,
        # sum_kill_weights :113-120).
        Index("ix_pirate_kill_log_region_created", "region_id", "created_at"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # Canon-exact NOT NULL — every row is a holding-CLEAR event, and
    # PirateHolding.region_id is itself NOT NULL. See module docstring.
    region_id = Column(
        UUID(as_uuid=True),
        ForeignKey("regions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    holding_id = Column(
        UUID(as_uuid=True),
        ForeignKey("pirate_holdings.id", ondelete="SET NULL"),
        nullable=True,
    )

    tier = Column(Enum(PirateHoldingTier, name="pirate_holding_tier"), nullable=False)
    # Snapshot of TIER_WEIGHT[tier] at kill time (1/3/10) — kept as its own
    # column (not derived) so a later TIER_WEIGHT rebalance never rewrites
    # history, matching canon's explicit snapshot framing (:105).
    kill_weight = Column(Integer, nullable=False)

    # NULLABLE — see module docstring divergence note.
    attacker_player_id = Column(
        UUID(as_uuid=True),
        ForeignKey("players.id", ondelete="SET NULL"),
        nullable=True,
    )
    attacker_team_id = Column(
        UUID(as_uuid=True),
        ForeignKey("teams.id", ondelete="SET NULL"),
        nullable=True,
    )

    disposition = Column(
        Enum(PirateKillDisposition, name="pirate_kill_disposition"), nullable=False
    )

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    def __repr__(self) -> str:
        return (
            f"<PirateKillLog {self.disposition.name if self.disposition else '?'} "
            f"tier={self.tier.name if self.tier else '?'} weight={self.kill_weight}>"
        )
