"""ADR-0050 SK22 — Phase 14 attachment retry bookkeeping.

Additive-only, nullable columns:

- ``regions.nexus_attach_attempt_count`` (Integer, default 0) — number of
  failed Phase-14 (Nexus-warp cross-region attachment) attempts recorded
  so far.
- ``regions.nexus_attach_next_retry_at`` (TIMESTAMPTZ, nullable) — when the
  next exponential-backoff retry is due; NULL when no retry is scheduled
  (either never attempted, already succeeded, or retries exhausted).
- ``regions.nexus_attach_operator_confirmed`` (Boolean, default false) —
  operator-set flag gating the ``attachment_pending`` refund path (SK22
  "Refund only fires ... after all retries exhausted AND a persistent
  operator-confirmed infrastructure issue"). Never set automatically.
- ``regions.nexus_attach_completed_at`` (TIMESTAMPTZ, nullable) — when the
  Phase-14 cross-region WarpTunnel insert actually succeeded. Deliberately
  separate from the pre-existing ``regions.nexus_warp_sector`` (which marks
  the region's chosen Frontier Gateway Plaza *landing sector*, set during
  region-content generation regardless of whether the cross-region tunnel
  to Nexus has actually landed) — conflating the two would make the retry
  sweep's "still pending" query silently wrong.
- ``warp_tunnels.idempotency_key`` (String(120), nullable, unique) —
  ``region.id + attempt_n`` key backing the SK22
  ``INSERT ... ON CONFLICT DO NOTHING`` idempotent Phase-14 attach.

Revision ID: c4f9a2e17b83
Revises: b3e8c1a94d70
Create Date: 2026-08-05 19:00:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = "c4f9a2e17b83"
down_revision = "b3e8c1a94d70"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "regions",
        sa.Column(
            "nexus_attach_attempt_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column(
        "regions",
        sa.Column("nexus_attach_next_retry_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )
    op.add_column(
        "regions",
        sa.Column(
            "nexus_attach_operator_confirmed",
            sa.Boolean(),
            nullable=False,
            server_default="false",
        ),
    )
    op.add_column(
        "regions",
        sa.Column("nexus_attach_completed_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )
    op.add_column(
        "warp_tunnels",
        sa.Column("idempotency_key", sa.String(length=120), nullable=True),
    )
    op.create_unique_constraint(
        "uq_warp_tunnels_idempotency_key",
        "warp_tunnels",
        ["idempotency_key"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_warp_tunnels_idempotency_key", "warp_tunnels", type_="unique"
    )
    op.drop_column("warp_tunnels", "idempotency_key")
    op.drop_column("regions", "nexus_attach_completed_at")
    op.drop_column("regions", "nexus_attach_operator_confirmed")
    op.drop_column("regions", "nexus_attach_next_retry_at")
    op.drop_column("regions", "nexus_attach_attempt_count")
