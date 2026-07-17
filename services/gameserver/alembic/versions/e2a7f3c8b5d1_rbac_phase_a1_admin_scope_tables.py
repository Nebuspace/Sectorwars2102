"""RBAC Phase A1: admin_scope_grants + admin_action_logs tables + seed.

Purely additive: two new tables, indexes.  Nothing existing is touched.

SEED semantics
--------------
1. Every User with ``is_admin = true`` receives an active grant for ALL 19
   canonical scopes (granted_by = NULL = bootstrap / system grant).
2. The bootstrap superadmin (``username = settings.ADMIN_USERNAME``) is
   covered by rule 1 — they hold is_admin = true so they get all 19 scopes,
   which includes the 3 meta-scopes (scopes.grant, scopes.revoke, audit.view).
3. Seed is idempotent: ``INSERT … ON CONFLICT DO NOTHING`` on the
   (user_id, scope) pair for active grants (where revoked_at IS NULL).
   A unique partial index enforces no duplicate active grant per (user, scope).

Cipher compliance
-----------------
- No admin under-seeded: the ``WHERE is_admin = true`` predicate covers
  every current admin row including bootstrap → zero lockout path.
- AdminActionLog FK uses SET NULL → deleting a user does not cascade-wipe
  their audit trail.
- A1-HARDEN: UNIQUE partial index on active grants; AdminActionLog
  append-only via BEFORE DELETE/UPDATE trigger + REVOKE from app role
  with column-scoped UPDATE (reviewed_by, reviewed_at) for Phase E ack.

Revision ID: e2a7f3c8b5d1
Revises: d4f8b16a92c1
Create Date: 2026-07-17 12:00:00.000000
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

# revision identifiers, used by Alembic.
revision = 'e2a7f3c8b5d1'
down_revision = 'd4f8b16a92c1'
branch_labels = None
depends_on = None

# The 19 canonical scopes — verbatim copy from auth/admin_scopes.py.
# Duplicated here so the migration is self-contained (imports are unsafe in
# migration scripts as the application module evolves).
_ALL_SCOPES = [
    "admin.players.view",
    "admin.players.suspend",
    "admin.players.adjust_rep",
    "admin.players.transfer_assets",
    "admin.subscriptions.view",
    "admin.subscriptions.modify",
    "admin.subscriptions.refund",
    "admin.webhooks.view",
    "admin.webhooks.replay",
    "admin.regions.view",
    "admin.regions.create",
    "admin.regions.terminate",
    "admin.regions.transfer_ownership",
    "admin.aria.audit",
    "admin.multi_account.review",
    "admin.bang.regenerate",
    "admin.scopes.grant",
    "admin.scopes.revoke",
    "admin.audit.view",
]


def upgrade() -> None:
    # ------------------------------------------------------------------
    # admin_scope_grants
    # ------------------------------------------------------------------
    op.create_table(
        "admin_scope_grants",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("scope", sa.String(120), nullable=False),
        sa.Column(
            "granted_by",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "granted_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "revoked_by",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )

    # Unique partial index: at most one active grant per (user, scope);
    # also the fast per-request lookup path (WHERE revoked_at IS NULL).
    op.create_index(
        "ix_admin_scope_grants_active",
        "admin_scope_grants",
        ["user_id", "scope"],
        unique=True,
        postgresql_where=sa.text("revoked_at IS NULL"),
    )
    op.create_index(
        "ix_admin_scope_grants_user_id",
        "admin_scope_grants",
        ["user_id"],
    )

    # ------------------------------------------------------------------
    # admin_action_logs
    # ------------------------------------------------------------------
    op.create_table(
        "admin_action_logs",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        # SET NULL — preserves audit trail if the user row is deleted
        sa.Column(
            "admin_user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("scope_used", sa.String(120), nullable=True),
        sa.Column("action", sa.String(200), nullable=False),
        sa.Column("target_type", sa.String(100), nullable=True),
        sa.Column("target_id", sa.String(255), nullable=True),
        sa.Column("payload_snapshot", JSONB, nullable=True),
        sa.Column("result", sa.String(50), nullable=True),
        sa.Column("failure_reason", sa.Text, nullable=True),
        sa.Column(
            "reviewed_by",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_admin_action_logs_admin_user_id",
        "admin_action_logs",
        ["admin_user_id"],
    )
    op.create_index(
        "ix_admin_action_logs_at",
        "admin_action_logs",
        ["at"],
    )
    op.create_index(
        "ix_admin_action_logs_scope_reviewed",
        "admin_action_logs",
        ["scope_used", "reviewed_at"],
    )

    # ------------------------------------------------------------------
    # A1-HARDEN Cipher #4: DB-enforce append-only on admin_action_logs
    # ------------------------------------------------------------------
    # Trigger is the hard guarantee (fires even for the table owner).
    # REVOKE is belt-and-suspenders for sectorwars_app when that role exists
    # and is not the owner — column-scoped GRANT keeps Phase E ack writable.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION admin_action_logs_append_only()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $fn$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION
                    'admin_action_logs is append-only: DELETE forbidden';
            END IF;
            IF TG_OP = 'UPDATE' THEN
                IF NEW.id IS DISTINCT FROM OLD.id
                   OR NEW.admin_user_id IS DISTINCT FROM OLD.admin_user_id
                   OR NEW.scope_used IS DISTINCT FROM OLD.scope_used
                   OR NEW.action IS DISTINCT FROM OLD.action
                   OR NEW.target_type IS DISTINCT FROM OLD.target_type
                   OR NEW.target_id IS DISTINCT FROM OLD.target_id
                   OR NEW.payload_snapshot IS DISTINCT FROM OLD.payload_snapshot
                   OR NEW.result IS DISTINCT FROM OLD.result
                   OR NEW.failure_reason IS DISTINCT FROM OLD.failure_reason
                   OR NEW.at IS DISTINCT FROM OLD.at
                THEN
                    RAISE EXCEPTION
                        'admin_action_logs is append-only: only reviewed_by/reviewed_at may change';
                END IF;
            END IF;
            RETURN NEW;
        END;
        $fn$;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_admin_action_logs_append_only
            BEFORE UPDATE OR DELETE ON admin_action_logs
            FOR EACH ROW
            EXECUTE PROCEDURE admin_action_logs_append_only();
        """
    )
    op.execute(
        """
        DO $do$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'sectorwars_app') THEN
                REVOKE DELETE ON TABLE admin_action_logs FROM sectorwars_app;
                REVOKE UPDATE ON TABLE admin_action_logs FROM sectorwars_app;
                GRANT UPDATE (reviewed_by, reviewed_at)
                    ON TABLE admin_action_logs TO sectorwars_app;
            END IF;
        END
        $do$;
        """
    )

    # ------------------------------------------------------------------
    # Seed: every is_admin=true user gets all 19 scopes
    # ON CONFLICT DO NOTHING makes this idempotent.
    # ------------------------------------------------------------------
    connection = op.get_bind()
    admins = connection.execute(
        sa.text("SELECT id FROM users WHERE is_admin = true AND deleted = false")
    ).fetchall()

    if admins:
        rows = [
            {
                "id": str(__import__("uuid").uuid4()),
                "user_id": str(row[0]),
                "scope": scope,
                # Canon: granted_by NOT NULL — bootstrap/system seed is a
                # self-grant (ADR-0058 allows superadmin self-grant).
                "granted_by": str(row[0]),
            }
            for row in admins
            for scope in _ALL_SCOPES
        ]
        # Idempotent: skip if an active grant for (user_id, scope) already exists.
        # We can't use ON CONFLICT because there is no UNIQUE constraint on
        # (user_id, scope) — revoked grants are kept, so multiple rows with
        # the same pair may exist (one revoked, one active).  Instead we check
        # the partial index predicate manually.
        for r in rows:
            exists = connection.execute(
                sa.text(
                    "SELECT 1 FROM admin_scope_grants "
                    "WHERE user_id = :uid AND scope = :scope AND revoked_at IS NULL "
                    "LIMIT 1"
                ),
                {"uid": r["user_id"], "scope": r["scope"]},
            ).fetchone()
            if not exists:
                connection.execute(
                    sa.text(
                        "INSERT INTO admin_scope_grants (id, user_id, scope, granted_by, granted_at) "
                        "VALUES (:id, :user_id, :scope, :granted_by, now())"
                    ),
                    r,
                )


def downgrade() -> None:
    op.execute(
        """
        DO $do$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'sectorwars_app') THEN
                GRANT UPDATE ON TABLE admin_action_logs TO sectorwars_app;
                GRANT DELETE ON TABLE admin_action_logs TO sectorwars_app;
            END IF;
        END
        $do$;
        """
    )
    op.execute("DROP TRIGGER IF EXISTS trg_admin_action_logs_append_only ON admin_action_logs")
    op.execute("DROP FUNCTION IF EXISTS admin_action_logs_append_only()")

    op.drop_index("ix_admin_action_logs_scope_reviewed", table_name="admin_action_logs")
    op.drop_index("ix_admin_action_logs_at", table_name="admin_action_logs")
    op.drop_index("ix_admin_action_logs_admin_user_id", table_name="admin_action_logs")
    op.drop_table("admin_action_logs")

    op.drop_index("ix_admin_scope_grants_user_id", table_name="admin_scope_grants")
    op.drop_index("ix_admin_scope_grants_active", table_name="admin_scope_grants")
    op.drop_table("admin_scope_grants")
