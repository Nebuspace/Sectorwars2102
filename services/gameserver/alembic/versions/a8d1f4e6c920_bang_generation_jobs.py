"""bang_generation_jobs table for async galaxy generation

Revision ID: a8d1f4e6c920
Revises: 7c2e91d6f4b8
Create Date: 2026-05-31 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = 'a8d1f4e6c920'
down_revision = '7c2e91d6f4b8'
branch_labels = None
depends_on = None


JOB_STATUS_VALUES = (
    'PENDING',
    'RUNNING',
    'COMPLETE',
    'FAILED',
)


def upgrade() -> None:
    op.create_table(
        'bang_generation_jobs',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            'admin_user_id',
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey('users.id', ondelete='RESTRICT'),
            nullable=False,
        ),
        sa.Column(
            'status',
            sa.Enum(*JOB_STATUS_VALUES, name='bang_generation_job_status'),
            nullable=False,
            server_default='PENDING',
        ),
        sa.Column('params_json', postgresql.JSONB(), nullable=False),
        sa.Column(
            'started_at',
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            'warnings_json',
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column('duration_ms', sa.Integer(), nullable=True),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.Column(
            'log_text',
            sa.Text(),
            nullable=False,
            server_default='',
        ),
    )

    op.create_index(
        'ix_bang_generation_jobs_admin_user_id',
        'bang_generation_jobs',
        ['admin_user_id'],
    )
    # Orphan-recovery query lookup: filter by status, order by started_at.
    op.create_index(
        'ix_bang_generation_jobs_status_started_at',
        'bang_generation_jobs',
        ['status', 'started_at'],
    )


def downgrade() -> None:
    op.drop_index(
        'ix_bang_generation_jobs_status_started_at',
        table_name='bang_generation_jobs',
    )
    op.drop_index(
        'ix_bang_generation_jobs_admin_user_id',
        table_name='bang_generation_jobs',
    )
    op.drop_table('bang_generation_jobs')
    sa.Enum(name='bang_generation_job_status').drop(op.get_bind(), checkfirst=True)
