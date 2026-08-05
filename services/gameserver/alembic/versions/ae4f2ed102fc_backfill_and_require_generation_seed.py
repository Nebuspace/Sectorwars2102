r"""Backfill Region.generation_seed and make it NOT NULL

WO-FIX-GENERATION-SEED-WRITER-AND-BACKFILL. Canon (galaxy.md:92,
ADR-0050:177) marks generation_seed NOT NULL; it shipped nullable because
no writer existed at any Region-creation site (verify-first, 2026-08-04:
grep -rn "generation_seed\s*=" across src/ returned zero hits before this
WO's sibling code change wired all 4 creation sites -- nexus_generation_
service.py, paypal_service.py, bang_galaxy.py x2). This migration is
SECOND in the required sequence -- the writer fix must land first, or
every future region-creation insert would immediately violate this NOT
NULL constraint.

Backfill formula per ADR-0050:177 ("backfill existing rows from
bigint(uuid) of Region.id"): a deterministic bigint derived from each
row's own id via md5 hash, masked to 63 bits so every backfilled value
stays in the same uint64-positive range the bang-import seed (BangConfig.
seed, ge=0) and the server-generated fallback (random.getrandbits(63))
both already use -- no distinguishable "backfilled vs real" value range.

Revision ID: ae4f2ed102fc
Revises: d8b4231fd582
Create Date: 2026-08-04

"""
from alembic import op
import sqlalchemy as sa


revision = "ae4f2ed102fc"
down_revision = "d8b4231fd582"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE regions
        SET generation_seed = (
            ('x' || substr(md5(id::text), 1, 16))::bit(63)::bigint
        )
        WHERE generation_seed IS NULL
        """
    )
    # A server_default (not just the Python-level model default) is a
    # deliberate safety net: 16 test fixtures construct Region() rows
    # directly without generation_seed, and any FUTURE write site that
    # forgets to set it (mirrors exactly how this column went un-set for
    # 4 live sites before this WO) would otherwise start failing INSERTs
    # the moment NOT NULL lands. Same md5-of-random derivation as the
    # backfill above, evaluated per-row at insert time.
    op.execute(
        """
        ALTER TABLE regions
        ALTER COLUMN generation_seed
        SET DEFAULT (
            ('x' || substr(md5(random()::text || clock_timestamp()::text), 1, 16))::bit(63)::bigint
        )
        """
    )
    op.alter_column(
        "regions",
        "generation_seed",
        existing_type=sa.BigInteger(),
        nullable=False,
    )


def downgrade() -> None:
    op.execute("ALTER TABLE regions ALTER COLUMN generation_seed DROP DEFAULT")
    op.alter_column(
        "regions",
        "generation_seed",
        existing_type=sa.BigInteger(),
        nullable=True,
    )
    # Backfilled values are NOT reverted -- they're synthetic (md5-derived,
    # not a real recorded seed) either way, and leaving them in place is
    # harmless (a real row can always be manually nulled if ever needed).
