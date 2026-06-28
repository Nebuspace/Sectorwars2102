"""
Port (station) ownership models: listings, sealed-bid offers, and economic
takeover campaigns.

Canon reference: FEATURES/economy/port-ownership (sw2102-docs).

  * StationListing — a purchasable station goes on the market with a fixed
    list price and a 24 canonical-hour grace window. One offer = sale at
    list price; multiple offers = sealed-bid auction resolved LAZILY at the
    first read past grace expiry (no scheduler exists).
  * PurchaseOffer — a buyer's escrowed bid against a listing. Credits are
    debited at offer time and refunded if the offer loses.
  * TakeoverCampaign — an economic-takeover attempt: a challenger who holds
    >50% of a station's monthly trade volume with hostile pricing for 3
    CONSECUTIVE scaled months (1 month = 30 canonical days) becomes
    'eligible'; the owner then has a 7 canonical-day counter window to
    accept / match / dispute before a forced sale.

All durations are CANONICAL and pass through src.core.game_time, so
GAME_TIME_SCALE compresses every window uniformly on dev.

These are new tables; `Base.metadata.create_all` (run at startup) covers all
environments — no Alembic migration is needed.
"""
import uuid

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID

from src.core.database import Base


class StationListing(Base):
    """One station offered for sale at a fixed list price."""
    __tablename__ = "station_listings"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    station_id = Column(
        UUID(as_uuid=True),
        ForeignKey("stations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # Fixed list price in credits (canon formula, clamped 250k-2M).
    price = Column(Integer, nullable=False)
    listed_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    # Absolute wall-clock end of the 24 canonical-hour grace window
    # (computed through game_time.scaled_deadline).
    grace_expires_at = Column(DateTime(timezone=True), nullable=False)
    # 'open' -> 'sold' | 'cancelled'
    status = Column(String(20), nullable=False, default="open", index=True)

    def __repr__(self) -> str:
        return (
            f"<StationListing station={self.station_id} price={self.price} "
            f"status={self.status}>"
        )


class PurchaseOffer(Base):
    """A buyer's sealed bid against a listing; credits escrowed at offer time."""
    __tablename__ = "station_purchase_offers"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    listing_id = Column(
        UUID(as_uuid=True),
        ForeignKey("station_listings.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    player_id = Column(
        UUID(as_uuid=True),
        ForeignKey("players.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # Escrowed bid amount in credits (debited from the player at offer time).
    bid = Column(Integer, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    # 'pending' -> 'won' | 'refunded'
    status = Column(String(20), nullable=False, default="pending", index=True)

    __table_args__ = (
        # One offer per player per listing (canon: sealed single bid).
        UniqueConstraint("listing_id", "player_id", name="uq_purchase_offer_listing_player"),
    )

    def __repr__(self) -> str:
        return (
            f"<PurchaseOffer listing={self.listing_id} player={self.player_id} "
            f"bid={self.bid} status={self.status}>"
        )


class TakeoverCampaign(Base):
    """An economic-takeover attempt against an owned station.

    Evaluated LAZILY month by month (1 month = 30 canonical days from
    `started_at`): a month counts when the challenger holds >50% of the
    station's trade volume AND prices hostilely; 3 consecutive satisfied
    months make the campaign 'eligible' and open the owner's 7 canonical-day
    counter window.
    """
    __tablename__ = "station_takeover_campaigns"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    station_id = Column(
        UUID(as_uuid=True),
        ForeignKey("stations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    challenger_id = Column(
        UUID(as_uuid=True),
        ForeignKey("players.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    started_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    # Consecutive months satisfied so far (resets to 0 on a failed month).
    months_satisfied = Column(Integer, nullable=False, default=0)
    # COUNT of completed scaled months already evaluated (0 = none yet);
    # the lazy engine catches up months [last_evaluated_month, current).
    last_evaluated_month = Column(Integer, nullable=False, default=0)
    # 'building' -> 'eligible' -> 'countered' | 'disputed' | 'transferred' | 'failed'
    status = Column(String(20), nullable=False, default="building", index=True)
    # Absolute wall-clock end of the owner's 7 canonical-day counter window
    # (set when the campaign becomes 'eligible').
    counter_expires_at = Column(DateTime(timezone=True), nullable=True)
    # Per-month evaluation records: list of {month, station_volume,
    # challenger_volume, share, hostile, satisfied}.
    monthly_history = Column(JSONB, nullable=False, default=list)
    dispute_reason = Column(String(500), nullable=True)

    def __repr__(self) -> str:
        return (
            f"<TakeoverCampaign station={self.station_id} "
            f"challenger={self.challenger_id} status={self.status} "
            f"months={self.months_satisfied}>"
        )
