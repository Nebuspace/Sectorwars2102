"""CargoWreck — salvageable cargo debris left when a ship is destroyed.

Canon: DATA_MODELS/cargo-wrecks.md (+ ADR-0007 grace/Suspect, ADR-0055 S-F2
killing-blow attribution).

A wreck is created when a ship is destroyed (COMBAT / HAZARD / non-combat
ABANDONMENT_EXPIRED). It holds the destroyed hull's cargo as a
``{commodity_name: int}`` JSONB map that DECREMENTS as salvagers pull
commodities out. There is NO decay timer: the row is DELETED the instant the
cargo map becomes empty (``{}``) — empty is the only terminal state.

Note on causes that do NOT spawn a wreck:
  - SELF_DESTRUCT — canonically never leaves a wreck (the value is reserved in
    the enum for completeness, but no spawn path uses it).
  - WARP_GATE_ANCHOR destructions — spawn no wreck at all, so WARP_GATE_ANCHOR
    is deliberately NOT a wreck_cause value.

GRACE / SUSPECT (ADR-0007): for 1 hour from ``created_at`` the wreck is the
"property" of three exempt parties — the ``original_owner_id``, any CURRENT
team-mate of ``original_team_id``, and the ``killing_blow_pilot_id`` (ADR-0055
S-F2). They salvage freely. An OUTSIDE-team salvager may still salvage during
that window, but doing so flags them Suspect (the salvage service sets the
EXISTING Player.is_suspect=True + Player.suspect_declared_at=now). After the
hour elapses anyone salvages freely with no Suspect flag.
"""

import uuid
import enum

from sqlalchemy import Column, DateTime, ForeignKey, Enum, Index, func
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship

from src.core.database import Base
# Reuse the canonical ShipType enum — do NOT redefine it here.
from src.models.ship import ShipType


class WreckCause(enum.Enum):
    """Why the ship was destroyed — drives nothing mechanically beyond
    analytics/audit (the grace window is identical for every spawning cause).

    SELF_DESTRUCT is reserved but never actually spawns a wreck (canon);
    WARP_GATE_ANCHOR is intentionally absent because anchor destructions
    spawn no wreck.
    """
    COMBAT = "COMBAT"
    HAZARD = "HAZARD"
    SELF_DESTRUCT = "SELF_DESTRUCT"
    ABANDONMENT_EXPIRED = "ABANDONMENT_EXPIRED"


class CargoWreck(Base):
    __tablename__ = "cargo_wrecks"
    __table_args__ = (
        # Sector lookup: "what's salvageable in the sector I'm sitting in?"
        Index("ix_cargo_wrecks_sector", "sector_id"),
        # Owner grace lookup: an owner finding their own recent wrecks.
        Index("ix_cargo_wrecks_owner_created", "original_owner_id", "created_at"),
        # Killing-blow grace lookup (ADR-0055 S-F2): a killer finding the
        # wrecks they may exemptly salvage during the grace window.
        Index("ix_cargo_wrecks_killer_created", "killing_blow_pilot_id", "created_at"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # Host sector. CASCADE: a deleted sector takes its debris with it.
    sector_id = Column(
        UUID(as_uuid=True),
        ForeignKey("sectors.id", ondelete="CASCADE"),
        nullable=False,
    )

    # Destroyed ship's player (null if the hull was an NPC). SET NULL: the
    # wreck survives the owner being purged, just loses attribution.
    original_owner_id = Column(
        UUID(as_uuid=True),
        ForeignKey("players.id", ondelete="SET NULL"),
        nullable=True,
    )
    # Owner's team SNAPSHOT at destruction — team-mates inherit grace via the
    # CURRENT team membership of this team id.
    original_team_id = Column(
        UUID(as_uuid=True),
        ForeignKey("teams.id", ondelete="SET NULL"),
        nullable=True,
    )
    # ADR-0055 S-F2: the pilot who landed the killing blow inherits grace.
    # Null for non-combat causes (HAZARD / ABANDONMENT_EXPIRED).
    killing_blow_pilot_id = Column(
        UUID(as_uuid=True),
        ForeignKey("players.id", ondelete="SET NULL"),
        nullable=True,
    )

    # The destroyed hull (null once that ship row is gone / for NPC hulls).
    destroyed_ship_id = Column(
        UUID(as_uuid=True),
        ForeignKey("ships.id", ondelete="SET NULL"),
        nullable=True,
    )
    # The destroyed hull's type — reuses the canonical ShipType enum.
    destroyed_ship_type = Column(
        Enum(ShipType, name="ship_type"),
        nullable=False,
    )

    # {commodity_name: int}. Decrements as salvaged; row deleted when it == {}.
    cargo = Column(JSONB, nullable=False)

    # Wreck birth — the 1-hour grace window is computed from this.
    created_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    cause = Column(
        Enum(WreckCause, name="wreck_cause"),
        nullable=False,
    )

    # Relationships (both nullable to mirror the FKs).
    original_owner = relationship("Player", foreign_keys=[original_owner_id])
    killing_blow_pilot = relationship("Player", foreign_keys=[killing_blow_pilot_id])
    destroyed_ship = relationship("Ship", foreign_keys=[destroyed_ship_id])

    def __repr__(self) -> str:
        return (
            f"<CargoWreck {self.destroyed_ship_type} sector={self.sector_id} "
            f"cause={self.cause} owner={self.original_owner_id}>"
        )
