"""NPC lodging foundation — barracks/outlaw tables + sector flags + home FKs

Additive schema for WO-BUILD-NPC-LODGING-FOUNDATION:

- ``npc_barracks`` / ``outlaw_bases`` tables (DATA_MODELS/npc-lodging.md)
- ``sectors.is_outlaw_zone`` / ``is_npc_barracks_sector`` (default false)
- ``npc_characters.home_barracks_id`` / ``home_outlaw_base_id`` (nullable FKs)

No backfill — existing NPCs keep NULL home lodging until spawn/roster wiring
assigns one. Raid/capture on OutlawBase stays out of scope.

Revision ID: e4a8c2f91b70
Revises: d7f3a1c9e842
Create Date: 2026-08-10 12:45:00.000000
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "e4a8c2f91b70"
down_revision = "d7f3a1c9e842"
branch_labels = None
depends_on = None


def upgrade() -> None:
    lodging_loc = postgresql.ENUM(
        "station", "sector", name="npc_lodging_location", create_type=False
    )
    lodging_loc.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "npc_barracks",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("location_type", lodging_loc, nullable=False),
        sa.Column("station_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("sector_id", sa.Integer(), nullable=True),
        sa.Column("home_region_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("faction_code", sa.String(length=50), nullable=False),
        sa.Column(
            "archetype",
            postgresql.ENUM(name="npc_archetype", create_type=False),
            nullable=False,
        ),
        sa.Column("capacity", sa.Integer(), nullable=False),
        sa.Column("current_occupants_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "assigned_npc_ids",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "amenities",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["home_region_id"], ["regions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["station_id"], ["stations.id"], ondelete="CASCADE"),
    )
    op.create_index(
        "ix_npc_barracks_region_faction_archetype",
        "npc_barracks",
        ["home_region_id", "faction_code", "archetype"],
    )
    op.create_index("ix_npc_barracks_station_id", "npc_barracks", ["station_id"])
    op.create_index("ix_npc_barracks_sector_id", "npc_barracks", ["sector_id"])
    op.create_index("ix_npc_barracks_faction_code", "npc_barracks", ["faction_code"])
    op.create_index("ix_npc_barracks_home_region_id", "npc_barracks", ["home_region_id"])

    op.create_table(
        "outlaw_bases",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("sector_id", sa.Integer(), nullable=False),
        sa.Column("home_region_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("faction_code", sa.String(length=50), nullable=False),
        sa.Column(
            "archetype",
            postgresql.ENUM(name="npc_archetype", create_type=False),
            nullable=False,
        ),
        sa.Column("capacity", sa.Integer(), nullable=False),
        sa.Column("current_occupants_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "assigned_npc_ids",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "is_player_discoverable",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("discovery_requirements", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "defenses",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "amenities",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["home_region_id"], ["regions.id"], ondelete="CASCADE"),
    )
    op.create_index(
        "ix_outlaw_bases_region_faction",
        "outlaw_bases",
        ["home_region_id", "faction_code"],
    )
    op.create_index("ix_outlaw_bases_sector_id", "outlaw_bases", ["sector_id"])
    op.create_index("ix_outlaw_bases_faction_code", "outlaw_bases", ["faction_code"])
    op.create_index("ix_outlaw_bases_home_region_id", "outlaw_bases", ["home_region_id"])

    op.add_column(
        "sectors",
        sa.Column("is_outlaw_zone", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    op.add_column(
        "sectors",
        sa.Column(
            "is_npc_barracks_sector",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )

    op.add_column(
        "npc_characters",
        sa.Column("home_barracks_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "npc_characters",
        sa.Column("home_outlaw_base_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_npc_characters_home_barracks_id",
        "npc_characters",
        "npc_barracks",
        ["home_barracks_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_npc_characters_home_outlaw_base_id",
        "npc_characters",
        "outlaw_bases",
        ["home_outlaw_base_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_npc_characters_home_barracks_id", "npc_characters", ["home_barracks_id"])
    op.create_index(
        "ix_npc_characters_home_outlaw_base_id", "npc_characters", ["home_outlaw_base_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_npc_characters_home_outlaw_base_id", table_name="npc_characters")
    op.drop_index("ix_npc_characters_home_barracks_id", table_name="npc_characters")
    op.drop_constraint("fk_npc_characters_home_outlaw_base_id", "npc_characters", type_="foreignkey")
    op.drop_constraint("fk_npc_characters_home_barracks_id", "npc_characters", type_="foreignkey")
    op.drop_column("npc_characters", "home_outlaw_base_id")
    op.drop_column("npc_characters", "home_barracks_id")

    op.drop_column("sectors", "is_npc_barracks_sector")
    op.drop_column("sectors", "is_outlaw_zone")

    op.drop_index("ix_outlaw_bases_home_region_id", table_name="outlaw_bases")
    op.drop_index("ix_outlaw_bases_faction_code", table_name="outlaw_bases")
    op.drop_index("ix_outlaw_bases_sector_id", table_name="outlaw_bases")
    op.drop_index("ix_outlaw_bases_region_faction", table_name="outlaw_bases")
    op.drop_table("outlaw_bases")

    op.drop_index("ix_npc_barracks_home_region_id", table_name="npc_barracks")
    op.drop_index("ix_npc_barracks_faction_code", table_name="npc_barracks")
    op.drop_index("ix_npc_barracks_sector_id", table_name="npc_barracks")
    op.drop_index("ix_npc_barracks_station_id", table_name="npc_barracks")
    op.drop_index("ix_npc_barracks_region_faction_archetype", table_name="npc_barracks")
    op.drop_table("npc_barracks")

    op.execute("DROP TYPE IF EXISTS npc_lodging_location")
