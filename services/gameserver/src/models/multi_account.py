"""Multi-account detection schema (WO-P7-admin-multiacct-models).

Canon: sw2102-docs/DATA_MODELS/gameplay.md:161-194 (ADR-0056 group-g
reputation-and-multi-account), operational detail in
sw2102-docs/OPERATIONS/multi-account-detection.md.

SCHEMA ONLY. This module is the store for the future
``MultiAccountDetectionService`` sweep and the admin review queue it feeds —
no detection heuristics, no admin decision-making logic, no
``participation_weight`` computation lives here. Those are separate,
later-lane WOs; this WO exists so they have somewhere to write.

- ``MultiAccountCluster`` — one row per detected cluster of accounts likely
  operated by the same human (gameplay.md:161-176).
- ``MultiAccountFlag`` — one row per (player, cluster) membership, read by
  every gated participation surface to compute ``participation_weight``
  (gameplay.md:178-194).

Enum values ('hard'/'soft', 'pending'/'confirmed'/'overridden'/'escalated')
are canon-exact lowercase (gameplay.md:169,171). Python member NAMES follow
this codebase's idiomatic UPPERCASE convention (``MultiAccountSeverity.HARD
== "hard"``) with ``values_callable=lambda obj: [e.value for e in obj]`` on
every enum Column — the established fix (contract.py, bounty_claim.py,
faction.py, player_warp_knowledge.py) for the name-vs-value Postgres enum
serialization defect this project hit repeatedly this session. Without
``values_callable``, SQLAlchemy sends the member NAME ("HARD") instead of
the migration-created lowercase label ("hard") and every insert 500s.

``severity`` is shared vocabulary between both tables — one Postgres enum
type (``multi_account_severity``), two columns.
"""

import enum
import uuid

from sqlalchemy import Boolean, Column, DateTime, Enum, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from src.core.database import Base


class MultiAccountSeverity(enum.Enum):
    """Canon `severity` vocabulary (gameplay.md:169,188) — the severity of
    the most-severe signal. Drives the discount math (gameplay.md:196)."""

    HARD = "hard"
    SOFT = "soft"


class MultiAccountAdminDecision(enum.Enum):
    """Canon `admin_decision` vocabulary (gameplay.md:171)."""

    PENDING = "pending"
    CONFIRMED = "confirmed"
    OVERRIDDEN = "overridden"
    ESCALATED = "escalated"


class MultiAccountCluster(Base):
    """One row per detected cluster of accounts likely operated by the same
    human (gameplay.md:161-176). Built and maintained by the future
    MultiAccountDetectionService sweep; this model is the store only."""

    __tablename__ = "multi_account_clusters"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # `{hard: [...], soft: [...], evidence: {...}}` — the heuristics that
    # fired and the supporting evidence (gameplay.md:168). Always supplied
    # by the detection service; no default.
    signal_summary = Column(JSONB(astext_type=Text()), nullable=False)

    severity = Column(
        Enum(
            MultiAccountSeverity,
            name="multi_account_severity",
            values_callable=lambda obj: [e.value for e in obj],
        ),
        nullable=False,
    )

    # Cached: true iff every member account holds an active paid
    # subscription at the most recent sweep (gameplay.md:170) — the discount
    # layer skips clusters where this is true. Doc states no explicit
    # default; defaults False (not yet swept / not confirmed all-paid) so a
    # freshly-detected cluster is never mistakenly skipped before its first
    # subscription sweep runs.
    all_paid_subscribers = Column(Boolean, nullable=False, default=False, server_default="false")

    admin_decision = Column(
        Enum(
            MultiAccountAdminDecision,
            name="multi_account_admin_decision",
            values_callable=lambda obj: [e.value for e in obj],
        ),
        nullable=False,
        default=MultiAccountAdminDecision.PENDING,
        server_default="pending",
    )
    admin_decision_reason = Column(String, nullable=True)
    admin_decision_at = Column(DateTime(timezone=True), nullable=True)
    admin_decision_by = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    # One-directional (no back_populates) — mirrors region_invite.py's
    # convention so users.py is not touched by this WO.
    admin_decided_by_user = relationship("User", foreign_keys=[admin_decision_by])
    flags = relationship(
        "MultiAccountFlag", back_populates="cluster", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return (
            f"<MultiAccountCluster {self.id} severity={self.severity.value if self.severity else '?'} "
            f"decision={self.admin_decision.value if self.admin_decision else '?'}>"
        )


class MultiAccountFlag(Base):
    """One row per (player, cluster) membership (gameplay.md:178-194). Read
    by every gated participation surface (governance vote, station volume,
    beacon visibility, faction-rep gain) to compute `participation_weight`
    — that computation itself is out of scope for this WO."""

    __tablename__ = "multi_account_flags"
    __table_args__ = (
        UniqueConstraint(
            "player_id", "cluster_id", "signal", name="uq_multi_account_flag_player_cluster_signal"
        ),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    player_id = Column(
        UUID(as_uuid=True),
        ForeignKey("players.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    cluster_id = Column(
        UUID(as_uuid=True),
        ForeignKey("multi_account_clusters.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # The specific signal tying this player to the cluster, e.g.
    # "payment_method", "ip_24h", "device_fingerprint" (gameplay.md:187).
    signal = Column(String, nullable=False)

    # Snapshot of the signal severity at flag-time (gameplay.md:188) — may
    # diverge from the cluster's current `severity` as later signals fire.
    severity = Column(
        Enum(
            MultiAccountSeverity,
            name="multi_account_severity",
            values_callable=lambda obj: [e.value for e in obj],
        ),
        nullable=False,
    )

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    cluster = relationship("MultiAccountCluster", back_populates="flags")
    # One-directional (no back_populates) — mirrors region_invite.py's
    # convention so player.py is not touched by this WO.
    player = relationship("Player", foreign_keys=[player_id])

    def __repr__(self) -> str:
        return (
            f"<MultiAccountFlag player={self.player_id} cluster={self.cluster_id} "
            f"signal={self.signal!r} severity={self.severity.value if self.severity else '?'}>"
        )
