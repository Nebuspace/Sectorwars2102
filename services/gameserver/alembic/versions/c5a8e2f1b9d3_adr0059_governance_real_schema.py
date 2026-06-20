"""ADR-0059 regional-governance real schema (replaces WO-S stop-gaps).

Replaces three WO-S governance stop-gaps with real, ADDITIVE schema:

  1. regions.governance_quorum_pct  (ADR-0059 N-D5 / DATA_MODELS/gameplay.md)
       Numeric(3,2), nullable, server_default '0.33', CHECK 0.25 <= x <= 0.60.
       Replaces the getattr(region,'governance_quorum_pct',0.33) read fallback.

  2. regional_policy_votes  (per-policy yes/no vote ledger)
       Replaces the RegionalPolicy.proposed_changes['_voters'] JSONB stop-gap.
       id, policy_id FK, voter_id FK, support bool, weight Numeric(5,4),
       created_at; UNIQUE(policy_id, voter_id) — one vote per (policy, voter),
       mirroring regional_votes' one_vote_per_election.

  3. regional_treasury_entries  (ADR-0059 N-I4 / DATA_MODELS/gameplay.md)
       Append-only ledger of every Region.treasury_balance-affecting event so
       the running balance is auditable. id, region_id FK, before_balance,
       after_balance, delta, cause_type (enum), cause_id, reason, at.

  4. Election-winner persistence columns (SYSTEMS/regional-governance.md step 84):
       regional_elections.winner_id  -> FK players.id, nullable
       regions.governor_id           -> FK players.id, nullable  (Region.{position}_id)
       regions.ambassador_id         -> FK players.id, nullable  (Region.{position}_id)
       Replaces the winner-not-persisted stop-gap (results JSONB only).
       (council_member is a multi-seat position with no single-occupant column;
        its winner persists to RegionalElection.results / winner_id only.)

ADDITIVE / non-destructive: every change is a new nullable column or a new
table. No populated column is dropped or altered. Idempotent (ADD COLUMN IF NOT
EXISTS + inspector-guarded create_table), so it is safe to re-run on dev WITH
DATA. Chained linearly off the single verified head b4d2f7e9a1c6 (WO-F widen
sector cap); no new branch.

Revision ID: c5a8e2f1b9d3
Revises: b4d2f7e9a1c6
Create Date: 2026-06-19 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = 'c5a8e2f1b9d3'
down_revision = 'b4d2f7e9a1c6'
branch_labels = None
depends_on = None


# ADR-0059 N-I4 cause-type taxonomy for treasury entries.
TREASURY_CAUSE_TYPE_VALUES = (
    'policy_enactment',
    'tax_collection',
    'expenditure',
    'transfer_in',
    'transfer_out',
    'manual_admin_adjustment',
)


def _has_table(name: str) -> bool:
    bind = op.get_bind()
    return sa.inspect(bind).has_table(name)


def upgrade() -> None:
    # ------------------------------------------------------------------
    # 1. regions.governance_quorum_pct (ADR-0059 N-D5) — nullable, default
    #    0.33, CHECK [0.25, 0.60]. Idempotent ADD COLUMN IF NOT EXISTS.
    #    The CHECK is added separately + guarded so a re-run does not error.
    # ------------------------------------------------------------------
    op.execute(
        "ALTER TABLE regions ADD COLUMN IF NOT EXISTS "
        "governance_quorum_pct NUMERIC(3,2) DEFAULT 0.33"
    )
    # Guard the CHECK so the migration is idempotent (Postgres has no
    # ADD CONSTRAINT IF NOT EXISTS for CHECK pre-15; use a catalog probe).
    op.execute(
        "DO $$ BEGIN "
        "IF NOT EXISTS (SELECT 1 FROM pg_constraint "
        "WHERE conname = 'valid_governance_quorum_pct') THEN "
        "ALTER TABLE regions ADD CONSTRAINT valid_governance_quorum_pct "
        "CHECK (governance_quorum_pct IS NULL OR "
        "(governance_quorum_pct >= 0.25 AND governance_quorum_pct <= 0.60)); "
        "END IF; END $$;"
    )

    # ------------------------------------------------------------------
    # 2. Election-winner persistence columns (SYSTEMS step 3).
    #    All nullable FK -> players.id (the winner is a candidate player_id).
    # ------------------------------------------------------------------
    op.execute(
        "ALTER TABLE regional_elections ADD COLUMN IF NOT EXISTS "
        "winner_id UUID REFERENCES players(id) ON DELETE SET NULL"
    )
    op.execute(
        "ALTER TABLE regions ADD COLUMN IF NOT EXISTS "
        "governor_id UUID REFERENCES players(id) ON DELETE SET NULL"
    )
    op.execute(
        "ALTER TABLE regions ADD COLUMN IF NOT EXISTS "
        "ambassador_id UUID REFERENCES players(id) ON DELETE SET NULL"
    )

    # ------------------------------------------------------------------
    # 3. regional_policy_votes — per-policy yes/no ledger (replaces the
    #    proposed_changes['_voters'] JSONB stop-gap). Mirrors regional_votes.
    # ------------------------------------------------------------------
    if not _has_table('regional_policy_votes'):
        op.create_table(
            'regional_policy_votes',
            sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column('policy_id', postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column('voter_id', postgresql.UUID(as_uuid=True), nullable=False),
            # yes/no on the policy referendum.
            sa.Column('support', sa.Boolean(), nullable=False),
            # snapshot of membership.voting_power at cast time (ADR-0059 N-F5).
            sa.Column('weight', sa.Numeric(5, 4), nullable=False, server_default=sa.text('1.0')),
            sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
            sa.ForeignKeyConstraint(['policy_id'], ['regional_policies.id'], ondelete='CASCADE'),
            sa.ForeignKeyConstraint(['voter_id'], ['players.id'], ondelete='CASCADE'),
            sa.PrimaryKeyConstraint('id'),
            # one vote per (policy, voter) — first-vote-sticks backstop.
            sa.UniqueConstraint('policy_id', 'voter_id', name='one_vote_per_policy'),
            sa.CheckConstraint('weight >= 0.0 AND weight <= 5.0', name='valid_policy_vote_weight'),
        )
        op.create_index('ix_regional_policy_votes_policy_id', 'regional_policy_votes', ['policy_id'])
        op.create_index('ix_regional_policy_votes_voter_id', 'regional_policy_votes', ['voter_id'])

    # ------------------------------------------------------------------
    # 4. regional_treasury_entries (ADR-0059 N-I4) — append-only ledger.
    # ------------------------------------------------------------------
    if not _has_table('regional_treasury_entries'):
        cause_enum = postgresql.ENUM(
            *TREASURY_CAUSE_TYPE_VALUES,
            name='region_treasury_cause_type',
            create_type=False,
        )
        # checkfirst makes the enum create idempotent across re-runs.
        cause_enum.create(op.get_bind(), checkfirst=True)
        op.create_table(
            'regional_treasury_entries',
            sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column('region_id', postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column('before_balance', sa.Integer(), nullable=False),
            sa.Column('after_balance', sa.Integer(), nullable=False),
            sa.Column('delta', sa.Integer(), nullable=False),
            sa.Column('cause_type', cause_enum, nullable=False),
            sa.Column('cause_id', postgresql.UUID(as_uuid=True), nullable=True),
            sa.Column('reason', sa.String(length=500), nullable=True),
            sa.Column('at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
            sa.ForeignKeyConstraint(['region_id'], ['regions.id'], ondelete='CASCADE'),
            sa.PrimaryKeyConstraint('id'),
        )
        # chronological treasury feed + reconciliation aggregate (DATA_MODELS).
        op.create_index(
            'ix_regional_treasury_entries_region_at',
            'regional_treasury_entries',
            ['region_id', sa.text('at DESC')],
        )


def downgrade() -> None:
    # New tables dropped (own data; reversible).
    if _has_table('regional_treasury_entries'):
        op.drop_index('ix_regional_treasury_entries_region_at', table_name='regional_treasury_entries')
        op.drop_table('regional_treasury_entries')
        postgresql.ENUM(name='region_treasury_cause_type').drop(op.get_bind(), checkfirst=True)

    if _has_table('regional_policy_votes'):
        op.drop_index('ix_regional_policy_votes_voter_id', table_name='regional_policy_votes')
        op.drop_index('ix_regional_policy_votes_policy_id', table_name='regional_policy_votes')
        op.drop_table('regional_policy_votes')

    # Winner-persistence columns (drop FK columns).
    op.execute("ALTER TABLE regions DROP COLUMN IF EXISTS ambassador_id")
    op.execute("ALTER TABLE regions DROP COLUMN IF EXISTS governor_id")
    op.execute("ALTER TABLE regional_elections DROP COLUMN IF EXISTS winner_id")

    # Quorum column + its CHECK.
    op.execute("ALTER TABLE regions DROP CONSTRAINT IF EXISTS valid_governance_quorum_pct")
    op.execute("ALTER TABLE regions DROP COLUMN IF EXISTS governance_quorum_pct")
