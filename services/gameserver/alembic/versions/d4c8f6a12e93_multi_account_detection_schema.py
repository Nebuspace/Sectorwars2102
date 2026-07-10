"""Multi-account detection schema (WO-P7-admin-multiacct-models).

Purely additive: two new tables, two new Postgres enum types. Nothing
existing is touched.

Canon: DATA_MODELS/gameplay.md:161-194 (ADR-0056 group-g
reputation-and-multi-account). SCHEMA ONLY -- no detection heuristics, no
admin decision-making logic, no participation_weight computation. Those are
separate, later-lane WOs; this migration exists so they have a store.

`multi_account_severity` (hard/soft) is shared vocabulary between both
tables -- one enum type, two columns. Created once explicitly
(`create_type=False` + `.create(checkfirst=True)`) and referenced by name in
both `create_table` calls to avoid a duplicate `CREATE TYPE`.

Revision ID: d4c8f6a12e93
Revises: b7e4a29f1c68
Create Date: 2026-07-10 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = 'd4c8f6a12e93'
down_revision = 'b7e4a29f1c68'
branch_labels = None
depends_on = None


# Canon-exact lowercase values (gameplay.md:169,171,188).
SEVERITY_VALUES = ('hard', 'soft')
ADMIN_DECISION_VALUES = ('pending', 'confirmed', 'overridden', 'escalated')


def upgrade() -> None:
    severity_enum = postgresql.ENUM(
        *SEVERITY_VALUES, name='multi_account_severity', create_type=False,
    )
    severity_enum.create(op.get_bind(), checkfirst=True)

    admin_decision_enum = postgresql.ENUM(
        *ADMIN_DECISION_VALUES, name='multi_account_admin_decision', create_type=False,
    )
    admin_decision_enum.create(op.get_bind(), checkfirst=True)

    op.create_table(
        'multi_account_clusters',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('signal_summary', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            'severity',
            postgresql.ENUM(*SEVERITY_VALUES, name='multi_account_severity', create_type=False),
            nullable=False,
        ),
        sa.Column(
            'all_paid_subscribers', sa.Boolean(), nullable=False, server_default=sa.text('false'),
        ),
        sa.Column(
            'admin_decision',
            postgresql.ENUM(
                *ADMIN_DECISION_VALUES, name='multi_account_admin_decision', create_type=False,
            ),
            nullable=False,
            server_default='pending',
        ),
        sa.Column('admin_decision_reason', sa.String(), nullable=True),
        sa.Column('admin_decision_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            'admin_decision_by',
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey('users.id', ondelete='SET NULL'),
            nullable=True,
        ),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    op.create_table(
        'multi_account_flags',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            'player_id',
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey('players.id', ondelete='CASCADE'),
            nullable=False,
        ),
        sa.Column(
            'cluster_id',
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey('multi_account_clusters.id', ondelete='CASCADE'),
            nullable=False,
        ),
        sa.Column('signal', sa.String(), nullable=False),
        sa.Column(
            'severity',
            postgresql.ENUM(*SEVERITY_VALUES, name='multi_account_severity', create_type=False),
            nullable=False,
        ),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint(
            'player_id', 'cluster_id', 'signal', name='uq_multi_account_flag_player_cluster_signal',
        ),
    )

    op.create_index('ix_multi_account_flags_player_id', 'multi_account_flags', ['player_id'])
    op.create_index('ix_multi_account_flags_cluster_id', 'multi_account_flags', ['cluster_id'])


def downgrade() -> None:
    op.drop_index('ix_multi_account_flags_cluster_id', table_name='multi_account_flags')
    op.drop_index('ix_multi_account_flags_player_id', table_name='multi_account_flags')
    op.drop_table('multi_account_flags')
    op.drop_table('multi_account_clusters')
    postgresql.ENUM(name='multi_account_admin_decision').drop(op.get_bind(), checkfirst=True)
    postgresql.ENUM(name='multi_account_severity').drop(op.get_bind(), checkfirst=True)
