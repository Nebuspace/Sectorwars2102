"""
Player warp-gate construction models (ADR-0029 + FEATURES/galaxy/warp-gates.md).

A gate is built as a three-phase ritual:
  Phase 1 — a WarpGateBeacon is deployed in the source sector (status DEPLOYED,
            48h invulnerability/expiry window, 1 Quantum Crystal sunk).
  Phase 2 — the Warp Jumper travels to the destination (no rows change).
  Phase 3 — anchor-focus creates a WarpGate row (HARMONIZING) plus the
            WarpTunnel row (FORMING); one canonical hour later the lazy
            advance consumes the Warp Jumper hull and flips everything ACTIVE
            (beacon -> MATCHED).

Sector references use the human-readable Integer sector number — the same
convention as Ship.sector_id and Player.current_sector_id. The WarpTunnel row
(which needs UUID sector FKs) is resolved by the service layer.
"""
import uuid
import enum

from sqlalchemy import Column, DateTime, Integer, ForeignKey, Enum, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from src.core.database import Base


class WarpGateBeaconStatus(enum.Enum):
    DEPLOYED = "DEPLOYED"    # Phase 1 complete, awaiting the focus anchor
    MATCHED = "MATCHED"      # Harmonization completed — gate is live
    EXPIRED = "EXPIRED"      # 48h window lapsed with no completed anchor
    CANCELLED = "CANCELLED"  # Owner abandoned the project at Phase 1


class WarpGateStatus(enum.Enum):
    HARMONIZING = "HARMONIZING"  # Phase 3 Step A committed, 1h timer running
    ACTIVE = "ACTIVE"            # Harmonization completed — traversable
    CANCELLED = "CANCELLED"      # Owner aborted mid-harmonization (refunded)
    COLLAPSED = "COLLAPSED"      # Destroyed (combat / cascade)


class WarpGateBeacon(Base):
    """Source-sector structure deployed at Phase 1."""
    __tablename__ = "warp_gate_beacons"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    player_id = Column(UUID(as_uuid=True), ForeignKey("players.id", ondelete="CASCADE"), nullable=False)
    # Human-readable sector numbers (Ship.sector_id convention).
    source_sector_id = Column(Integer, nullable=False)
    destination_sector_id = Column(Integer, nullable=False)
    status = Column(
        Enum(WarpGateBeaconStatus, name="warp_gate_beacon_status"),
        nullable=False,
        default=WarpGateBeaconStatus.DEPLOYED,
    )
    # ADR-0011: doubles as the 48h expiry window for an unmatched beacon.
    # Cleared when the gate goes ACTIVE (beacon -> MATCHED).
    invulnerable_until = Column(DateTime(timezone=True), nullable=True)
    hp = Column(Integer, nullable=False, default=5000)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    player = relationship("Player")
    gates = relationship("WarpGate", back_populates="beacon")

    def __repr__(self):
        return (
            f"<WarpGateBeacon {self.id} {self.source_sector_id}->"
            f"{self.destination_sector_id} ({self.status.name})>"
        )


class WarpGate(Base):
    """Phase 3 gate-in-progress / finished gate. The traversable connection
    itself is the linked WarpTunnel row (type=ARTIFICIAL, one-way, 0 turns)."""
    __tablename__ = "warp_gates"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    beacon_id = Column(UUID(as_uuid=True), ForeignKey("warp_gate_beacons.id", ondelete="CASCADE"), nullable=False)
    player_id = Column(UUID(as_uuid=True), ForeignKey("players.id", ondelete="CASCADE"), nullable=False)
    # Nullable: a cancelled gate has its tunnel row deleted.
    warp_tunnel_id = Column(UUID(as_uuid=True), ForeignKey("warp_tunnels.id", ondelete="SET NULL"), nullable=True)
    status = Column(
        Enum(WarpGateStatus, name="warp_gate_status"),
        nullable=False,
        default=WarpGateStatus.HARMONIZING,
    )
    # warp-gates.md "Combat & destruction" + ADR-0011: the focus structure soaks
    # at 5,000 HP while HARMONIZING; once the gate harmonizes ACTIVE the merged
    # gate has its own 10,000-HP pool (set in warp_gate_service.advance_gate).
    # The beacon/focus 5,000-HP default stays on WarpGateBeacon.hp above.
    hp = Column(Integer, nullable=False, default=5000)
    harmonization_completes_at = Column(DateTime(timezone=True), nullable=True)
    # The Warp Jumper consumed at harmonization completion (ADR-0029).
    anchor_ship_id = Column(UUID(as_uuid=True), ForeignKey("ships.id", ondelete="SET NULL"), nullable=True)
    # Build-cost snapshot for the region-termination 50% refund
    # (warp-gates.md "Region-termination cascade").
    construction_cost = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    beacon = relationship("WarpGateBeacon", back_populates="gates")
    player = relationship("Player")
    warp_tunnel = relationship("WarpTunnel")
    anchor_ship = relationship("Ship")

    def __repr__(self):
        return f"<WarpGate {self.id} beacon={self.beacon_id} ({self.status.name})>"
