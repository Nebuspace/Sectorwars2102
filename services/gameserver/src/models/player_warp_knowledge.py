import uuid
import enum
from datetime import datetime, UTC
from typing import TYPE_CHECKING

from sqlalchemy import (
    Column,
    DateTime,
    Enum,
    ForeignKey,
    UniqueConstraint,
    Index,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from src.core.database import Base

if TYPE_CHECKING:
    from src.models.player import Player


class WarpLayer(enum.Enum):
    """Which storage layer a known warp lives in (ADR-0045 / aria-companion.md
    § Warp discovery). A warp's identity is (warp_layer, warp_id): a direct
    sector-to-sector warp lives in ``sector_warps`` (the association table); a
    warp TUNNEL lives in the ``warp_tunnels`` table."""
    SECTOR_WARPS = "sector_warps"
    WARP_TUNNELS = "warp_tunnels"


class WarpVisibilityState(enum.Enum):
    """Per-player visibility progression for a warp (aria-companion.md):
    ``hidden`` (default for a latent warp the player has never encountered) ->
    ``revealed`` (the player has discovered it via scan/inference/share) ->
    ``traversed`` (the player has actually used it)."""
    HIDDEN = "hidden"
    REVEALED = "revealed"
    TRAVERSED = "traversed"


class WarpRevealedVia(enum.Enum):
    """How the player learned of the warp (aria-companion.md):
    a Warp Jumper long-range scan, a reverse-traversal attempt on a latent
    one-way, a corp-mate's shared scan knowledge, or ARIA's probabilistic
    inference."""
    SCAN = "scan"
    TRAVERSAL_ATTEMPT = "traversal_attempt"
    CORP_SHARE = "corp_share"
    ARIA_INFERENCE = "aria_inference"


class PlayerWarpKnowledge(Base):
    """Per-player record of which warps a player personally knows about
    (ADR-0045 — per-player ARIA-driven warp knowledge; canonical rule in
    FEATURES/gameplay/aria-companion.md § Warp discovery).

    A warp's global ``is_latent`` flag is only the *default* visibility for
    players who have never personally encountered it: a non-latent warp is
    visible to everyone, while a latent warp stays invisible until the player
    holds a ``revealed`` or ``traversed`` row for it. One player's discovery
    never leaks the warp to rivals — knowledge is this per-player layer.
    """
    __tablename__ = "player_warp_knowledge"
    __table_args__ = (
        # One row per (player, layer, warp) — discovering the same warp twice
        # is an idempotent upsert, never a duplicate row.
        UniqueConstraint(
            "player_id", "warp_layer", "warp_id",
            name="uq_player_warp_knowledge_player_layer_warp",
        ),
        # "Which warps does this player know?" is the per-player map read.
        Index("ix_player_warp_knowledge_player", "player_id"),
        # "Who knows about this warp?" (corp-share propagation, admin).
        Index("ix_player_warp_knowledge_warp", "warp_layer", "warp_id"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    player_id = Column(
        UUID(as_uuid=True),
        ForeignKey("players.id", ondelete="CASCADE"),
        nullable=False,
    )
    # (warp_layer, warp_id) jointly identify the warp. warp_id is NOT a FK
    # because it polymorphically references either sector_warps or warp_tunnels
    # depending on warp_layer.
    # WO-SWEEP-WARPLAYER-ENUM: values_callable is REQUIRED on all three enum
    # columns below. Plain SQLAlchemy Enum(PyEnum) serializes the member NAME
    # (e.g. "WARP_TUNNELS") by default -- but migration f1a4d7b2c9e3 created
    # the Postgres enum TYPES from the lowercase VALUES ('sector_warps',
    # 'warp_tunnels', ...). Without values_callable, every INSERT/UPDATE and
    # every enum-compared read against a real (values-built) Postgres DB
    # fails with "invalid input value for enum". A create_all-era dev DB
    # (name-built) masked this indefinitely -- caught live via a fresh-stage
    # POST /player/move 500. Python-side member names (WarpLayer.WARP_TUNNELS
    # etc.) are completely unchanged; every existing call site keeps working.
    warp_layer = Column(
        Enum(WarpLayer, name="warp_layer", values_callable=lambda obj: [e.value for e in obj]),
        nullable=False,
    )
    warp_id = Column(UUID(as_uuid=True), nullable=False)

    visibility_state = Column(
        Enum(
            WarpVisibilityState, name="warp_visibility_state",
            values_callable=lambda obj: [e.value for e in obj],
        ),
        nullable=False,
        default=WarpVisibilityState.REVEALED,
    )
    revealed_via = Column(
        Enum(
            WarpRevealedVia, name="warp_revealed_via",
            values_callable=lambda obj: [e.value for e in obj],
        ),
        nullable=False,
        default=WarpRevealedVia.SCAN,
    )

    discovered_at = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    last_updated = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    # Relationships
    player = relationship("Player", back_populates="warp_knowledge")

    def __repr__(self):
        return (
            f"<PlayerWarpKnowledge player={self.player_id} "
            f"{self.warp_layer.value}:{self.warp_id} "
            f"{self.visibility_state.value} via {self.revealed_via.value}>"
        )

    @property
    def is_known(self) -> bool:
        """True once the player has at least ``revealed`` this warp."""
        return self.visibility_state in (
            WarpVisibilityState.REVEALED,
            WarpVisibilityState.TRAVERSED,
        )
