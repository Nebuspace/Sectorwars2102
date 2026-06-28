"""Region invite-link onramp models (WO-IL1).

Design brief: audit/design-briefs/invite-link-onramp.md §3 — the
invite-link path of region-citizenship-onramp (DECISIONS.md:473-475).

A region OWNER mints a high-entropy invite ``code``; a new player who starts
the game via that code is placed in ``region_id`` and made an instant voting
citizen there (the Max-gated signup-wiring half lives in WO-IL6, not here).

This module is **additive, auth-free infrastructure** (WO-IL1):

  - ``RegionInvite`` — the mintable, revocable, expiring redeem key. ``status``
    is a String enum-in-string (active | exhausted | revoked | expired),
    mirroring the existing ``RegionStatus`` / ``MembershipType`` choice in
    region.py (no native PG enum, so adding a status value needs no migration).
    Redeemability is derived (no column): status='active' AND uses < max_uses
    AND now() < expires_at AND the region still exists AND the minting owner
    still owns it (re-checked at redeem — brief §5).

  - ``RegionInviteRedemption`` — an append-only audit trail of who redeemed
    what, with hashed IP / device-fingerprint columns that feed the future
    multi-account clustering (ADR-0056). The redemption ROW is written inside
    the Max-gated redeem path; the model + table are buildable now.

Conventions mirror models/region.py exactly: ``Base`` from
``src.core.database``; ``UUID(as_uuid=True)`` PK with Python-side
``default=uuid.uuid4`` (no server_default); ``TIMESTAMP`` columns with
``server_default=func.now()`` for auto-set timestamps; FK ``ondelete`` actions
declared inline on the ForeignKey; constraints in a single ``__table_args__``
tuple. No change to any existing table or row.
"""

from sqlalchemy import (
    Column,
    String,
    Integer,
    TIMESTAMP,
    ForeignKey,
    CheckConstraint,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from enum import Enum
from datetime import datetime
from typing import Optional
import uuid

from src.core.database import Base


class RegionInviteStatus(str, Enum):
    """Lifecycle status of a region invite (String enum-in-string, no PG enum)."""
    ACTIVE = "active"
    EXHAUSTED = "exhausted"
    REVOKED = "revoked"
    EXPIRED = "expired"


class RegionInvite(Base):
    """A region-owner-minted, expiring, revocable invite code.

    Redeeming a valid code places a new player in ``region_id`` and grants
    instant citizenship there (WO-IL6, Max-gated). One-time by default
    (``max_uses=1``); ``expires_at`` is mandatory (no infinitely-reusable link).
    """
    __tablename__ = "region_invites"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # URL-safe high-entropy redeem key (e.g. secrets.token_urlsafe(16)).
    code = Column(String(32), nullable=False, unique=True, index=True)
    # The region the invitee is placed in + made citizen of. CASCADE so a
    # hard-deleted region removes its outstanding invites (brief §5 Threat 4).
    region_id = Column(
        UUID(as_uuid=True),
        ForeignKey("regions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # The region OWNER who minted it (provenance). SET NULL if the user row is
    # deleted — the invite survives for audit but loses its creator pointer.
    created_by = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    max_uses = Column(Integer, nullable=False, default=1, server_default="1")
    uses = Column(Integer, nullable=False, default=0, server_default="0")
    # Mandatory TTL — NO server_default; the minting service must supply it.
    expires_at = Column(TIMESTAMP(timezone=True), nullable=False)
    status = Column(
        String(20),
        nullable=False,
        default=RegionInviteStatus.ACTIVE,
        server_default="active",
    )
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())
    revoked_at = Column(TIMESTAMP(timezone=True), nullable=True)

    # Relationships (one-directional — no back_populates so region.py is not
    # touched by this WO; Region gains its back-pointer in a later coordinated WO).
    region = relationship("Region")
    creator = relationship("User")
    redemptions = relationship(
        "RegionInviteRedemption",
        back_populates="invite",
        cascade="all, delete-orphan",
    )

    # Constraints — defence-in-depth at the DB layer (brief §5 risks):
    #   * status restricted to the known vocabulary,
    #   * uses non-negative, max_uses >= 1, and uses never exceeds max_uses
    #     (the exhaustion check can never be bypassed by a bad write).
    __table_args__ = (
        CheckConstraint(
            "status IN ('active', 'exhausted', 'revoked', 'expired')",
            name="valid_region_invite_status",
        ),
        CheckConstraint(
            "uses >= 0 AND max_uses >= 1 AND uses <= max_uses",
            name="valid_region_invite_uses",
        ),
    )

    def __repr__(self):
        return (
            f"<RegionInvite(code='{self.code}', region_id='{self.region_id}', "
            f"status='{self.status}', uses={self.uses}/{self.max_uses})>"
        )

    @property
    def is_expired(self) -> bool:
        """True if the invite has passed its mandatory TTL."""
        if self.expires_at is None:
            return False
        return self.expires_at < datetime.utcnow()

    @property
    def is_exhausted(self) -> bool:
        """True if the invite has no remaining uses."""
        return self.uses >= self.max_uses

    @property
    def is_redeemable(self) -> bool:
        """Derived redeemability (column-free). NOTE: does NOT re-check that
        the minting owner still owns the region — that ownership re-check is the
        redeem path's responsibility (brief §5 Threat 4), done under a row lock.
        """
        return (
            self.status == RegionInviteStatus.ACTIVE
            and not self.is_exhausted
            and not self.is_expired
        )


class RegionInviteRedemption(Base):
    """Append-only audit trail of invite redemptions.

    One row per successful redeem (written inside the Max-gated redeem path,
    WO-IL6). ``redeemed_by_player_id`` is nullable because the player row is
    created in the same transaction; rows from before that wiring lands carry
    NULL. The hashed IP / device-fingerprint columns feed the future ADR-0056
    multi-account clustering — both nullable, never storing raw values.
    """
    __tablename__ = "region_invite_redemptions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    invite_id = Column(
        UUID(as_uuid=True),
        ForeignKey("region_invites.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # Nullable: set after the player row is created in the same transaction.
    # SET NULL on player deletion keeps the audit row but drops the pointer.
    redeemed_by_player_id = Column(
        UUID(as_uuid=True),
        ForeignKey("players.id", ondelete="SET NULL"),
        nullable=True,
    )
    redeemed_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())
    # Hashed, never raw — privacy + ADR-0056 clustering feed (brief §5, D8).
    ip_hash = Column(String, nullable=True)
    device_fingerprint_hash = Column(String, nullable=True)

    # Relationships
    invite = relationship("RegionInvite", back_populates="redemptions")
    player = relationship("Player")

    def __repr__(self):
        return (
            f"<RegionInviteRedemption(invite_id='{self.invite_id}', "
            f"player_id='{self.redeemed_by_player_id}')>"
        )
