"""add player trade window tables (WO-P2P-TRADING-SYSTEM v1 kernel)

ADR-0089 bilateral trade window — additive schema only:

  - player_trade_sessions / player_trade_logs / player_tradeable_prices
  - players.open_trade_session_id (nullable FK)

Reversible. No destructive ops. Ship-bundle columns deferred.

Revision ID: b4e8c2a91f70
Revises: a8f3c1e7b2d9
Create Date: 2026-08-03 15:45:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "b4e8c2a91f70"
down_revision = "a8f3c1e7b2d9"
branch_labels = None
depends_on = None


SESSION_STATUS = (
    "PENDING_ACCEPT",
    "OPEN",
    "SETTLED",
    "CANCELLED",
    "EXPIRED",
    "DECLINED",
)


def upgrade() -> None:
    status = postgresql.ENUM(
        *SESSION_STATUS,
        name="player_trade_session_status",
        create_type=False,
    )
    status.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "player_trade_sessions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "initiator_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("players.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "target_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("players.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "status",
            status,
            nullable=False,
            server_default="PENDING_ACCEPT",
        ),
        sa.Column(
            "version",
            sa.Integer(),
            nullable=False,
            server_default="1",
        ),
        sa.Column("initiator_confirmed_version", sa.Integer(), nullable=True),
        sa.Column("target_confirmed_version", sa.Integer(), nullable=True),
        sa.Column("sector_id", sa.Integer(), nullable=False),
        sa.Column(
            "port_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("stations.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "initiator_offer",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "target_offer",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("settled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("terminal_reason", sa.String(length=64), nullable=True),
    )
    op.create_index(
        "ix_player_trade_sessions_status",
        "player_trade_sessions",
        ["status"],
    )
    op.create_index(
        "ix_player_trade_sessions_initiator",
        "player_trade_sessions",
        ["initiator_id"],
    )
    op.create_index(
        "ix_player_trade_sessions_target",
        "player_trade_sessions",
        ["target_id"],
    )

    op.create_table(
        "player_trade_logs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "session_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("player_trade_sessions.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "initiator_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("players.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "target_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("players.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("sector_id", sa.Integer(), nullable=False),
        sa.Column(
            "manifest",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "appraised_value",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "tax_paid",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_player_trade_logs_initiator",
        "player_trade_logs",
        ["initiator_id"],
    )
    op.create_index(
        "ix_player_trade_logs_target",
        "player_trade_logs",
        ["target_id"],
    )

    op.create_table(
        "player_tradeable_prices",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("asset_key", sa.String(length=64), nullable=False),
        sa.Column("unit_value_cr", sa.Integer(), nullable=False),
        sa.Column(
            "category",
            sa.String(length=32),
            nullable=False,
            server_default="commodity",
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "uq_player_tradeable_prices_asset_key",
        "player_tradeable_prices",
        ["asset_key"],
        unique=True,
    )

    # Seed kernel commodity reference prices (ADR-0089 / ADR-0082 bands).
    op.execute(
        sa.text(
            """
            INSERT INTO player_tradeable_prices (id, asset_key, unit_value_cr, category)
            VALUES
              (gen_random_uuid(), 'credits', 1, 'currency'),
              (gen_random_uuid(), 'ore', 15, 'commodity'),
              (gen_random_uuid(), 'fuel_ore', 15, 'commodity'),
              (gen_random_uuid(), 'organics', 18, 'commodity'),
              (gen_random_uuid(), 'equipment', 35, 'commodity'),
              (gen_random_uuid(), 'precious_metals', 120, 'commodity')
            ON CONFLICT (asset_key) DO NOTHING
            """
        )
    )

    op.add_column(
        "players",
        sa.Column(
            "open_trade_session_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
    )
    op.create_foreign_key(
        "fk_players_open_trade_session_id",
        "players",
        "player_trade_sessions",
        ["open_trade_session_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_players_open_trade_session_id",
        "players",
        ["open_trade_session_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("ix_players_open_trade_session_id", table_name="players")
    op.drop_constraint(
        "fk_players_open_trade_session_id",
        "players",
        type_="foreignkey",
    )
    op.drop_column("players", "open_trade_session_id")
    op.drop_index(
        "uq_player_tradeable_prices_asset_key",
        table_name="player_tradeable_prices",
    )
    op.drop_table("player_tradeable_prices")
    op.drop_index("ix_player_trade_logs_target", table_name="player_trade_logs")
    op.drop_index(
        "ix_player_trade_logs_initiator", table_name="player_trade_logs"
    )
    op.drop_table("player_trade_logs")
    op.drop_index(
        "ix_player_trade_sessions_target", table_name="player_trade_sessions"
    )
    op.drop_index(
        "ix_player_trade_sessions_initiator",
        table_name="player_trade_sessions",
    )
    op.drop_index(
        "ix_player_trade_sessions_status", table_name="player_trade_sessions"
    )
    op.drop_table("player_trade_sessions")
    sa.Enum(name="player_trade_session_status").drop(
        op.get_bind(), checkfirst=True
    )
