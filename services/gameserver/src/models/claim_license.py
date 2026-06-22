"""ClaimLicense — Astral Mining Consortium mining claim license.

A claim license grants a player the right to mine an AM-claimed
``ASTEROID_FIELD`` sector without incurring a reputation penalty, per
``FEATURES/economy/mining.md`` § Astral Mining Consortium claim licenses.

Scope: a single (player, region, sector) triple. Duration: 24 real-time
hours per purchase (``expires_at = purchased_at + 24h``). Cost: ``500 cr ×
richness_tier`` (canon § License cost). Only one *active* license per
(player, region, sector) at a time — enforced by the unique constraint;
renewals insert a fresh row (the prior row's ``expires_at`` is honoured for
any overlap window).

Greenfield (WO-MINING) — additive only; no existing table is changed.
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    Column,
    DateTime,
    String,
    Integer,
    ForeignKey,
    Index,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID

from src.core.database import Base


class ClaimLicense(Base):
    """An AM mining claim license held by a player for one sector."""

    __tablename__ = "claim_licenses"
    __table_args__ = (
        # One active license per (player, region, sector) at a time. Renewals
        # insert a fresh row; the unique triple guards against duplicate
        # concurrent holds (canon § License model).
        UniqueConstraint(
            "player_id",
            "region_id",
            "sector_number",
            name="uq_claim_license_player_region_sector",
        ),
        # "Which licenses does this player hold?" — the harvest-time lookup.
        Index("ix_claim_licenses_player", "player_id"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    player_id = Column(
        UUID(as_uuid=True),
        ForeignKey("players.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # Compound sector identity (region + sector_number). Nullable for
    # region-less (single-region / legacy) sectors; the unique constraint
    # still distinguishes by sector_number in that case.
    region_id = Column(
        UUID(as_uuid=True),
        ForeignKey("regions.id", ondelete="CASCADE"),
        nullable=True,
    )
    sector_number = Column(Integer, nullable=False)
    # Issuing faction snake-code — always "astral_mining_consortium" at launch
    # (matches Sector.controlling_faction / FactionType.MINING, ADR-0033).
    faction_code = Column(
        String(50),
        nullable=False,
        default="astral_mining_consortium",
    )
    purchased_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    # purchased_at + 24h (server-clock authoritative; canon § License model).
    expires_at = Column(DateTime, nullable=False)
    # 500 cr × richness_tier (canon § License cost).
    cost_paid_cr = Column(Integer, nullable=False)

    @property
    def is_active(self) -> bool:
        """True while the license has not yet expired (server-clock)."""
        return self.expires_at > datetime.utcnow()

    def __repr__(self) -> str:
        return (
            f"<ClaimLicense player={self.player_id} "
            f"region={self.region_id} sector={self.sector_number} "
            f"expires={self.expires_at.isoformat()}>"
        )
