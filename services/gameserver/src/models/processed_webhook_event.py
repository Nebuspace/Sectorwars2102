from sqlalchemy import Column, DateTime, String, func

from src.core.database import Base


class ProcessedWebhookEvent(Base):
    """Idempotency ledger for inbound payment-provider webhooks (ADR-0058).

    One row per provider event id. The ``event_id`` primary key gives an atomic
    "process exactly once" guarantee: the webhook handler inserts the row in the
    same transaction as the subscription mutation, so a duplicate delivery
    (PayPal retries aggressively) hits the unique constraint and is skipped
    rather than re-applied.
    """

    __tablename__ = "processed_webhook_events"

    # The provider's globally-unique event id (PayPal ``id`` field). Primary key
    # so the dedup check is the insert itself — no separate SELECT race window.
    event_id = Column(String(255), primary_key=True)
    event_type = Column(String(100), nullable=True)
    processed_at = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
