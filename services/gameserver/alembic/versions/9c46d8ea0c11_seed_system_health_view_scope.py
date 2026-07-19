"""RBAC: seed admin.system.health_view scope to existing admins.

Additive follow-on to the RBAC expansion migrations (f3a8c2d91e47,
a7c4e91b2d08).  Every User with ``is_admin = true`` receives the new
``admin.system.health_view`` scope added when GET /status/database/detailed
migrated off the flat ``require_admin`` check (RBAC route-coverage
completeness tripwire, WO gameserver-CI-fix 2026-07-19).  Idempotent: skip
if already held (active grant with revoked_at IS NULL).

Revision ID: 9c46d8ea0c11
Revises: a7c4e91b2d08
Create Date: 2026-07-19 00:00:00.000000
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "9c46d8ea0c11"
down_revision = "a7c4e91b2d08"
branch_labels = None
depends_on = None

# The 1 new scope -- verbatim from auth/admin_scopes.py at expansion.
_NEW_SCOPES = [
    "admin.system.health_view",
]


def upgrade() -> None:
    connection = op.get_bind()
    admins = connection.execute(
        sa.text("SELECT id FROM users WHERE is_admin = true AND deleted = false")
    ).fetchall()

    if not admins:
        return

    for row in admins:
        user_id = str(row[0])
        for scope in _NEW_SCOPES:
            exists = connection.execute(
                sa.text(
                    "SELECT 1 FROM admin_scope_grants "
                    "WHERE user_id = :uid AND scope = :scope AND revoked_at IS NULL "
                    "LIMIT 1"
                ),
                {"uid": user_id, "scope": scope},
            ).fetchone()
            if not exists:
                connection.execute(
                    sa.text(
                        "INSERT INTO admin_scope_grants "
                        "(id, user_id, scope, granted_by, granted_at) "
                        "VALUES (:id, :user_id, :scope, :granted_by, now())"
                    ),
                    {
                        "id": str(__import__("uuid").uuid4()),
                        "user_id": user_id,
                        "scope": scope,
                        "granted_by": user_id,
                    },
                )


def downgrade() -> None:
    connection = op.get_bind()
    for scope in _NEW_SCOPES:
        connection.execute(
            sa.text(
                "UPDATE admin_scope_grants SET revoked_at = now() "
                "WHERE scope = :scope AND revoked_at IS NULL"
            ),
            {"scope": scope},
        )
