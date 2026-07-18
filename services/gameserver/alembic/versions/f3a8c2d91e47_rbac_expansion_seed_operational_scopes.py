"""RBAC expansion: seed 7 operational scopes to existing admins.

Additive follow-on to Phase A1 (e2a7f3c8b5d1).  Every User with
``is_admin = true`` receives the 7 Max-ruled operational scopes added
in the 19→26 catalog expansion.  Idempotent: skip scopes already held
(active grant with revoked_at IS NULL).

Revision ID: f3a8c2d91e47
Revises: e2a7f3c8b5d1
Create Date: 2026-07-17 14:30:00.000000
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "f3a8c2d91e47"
down_revision = "e2a7f3c8b5d1"
branch_labels = None
depends_on = None

# The 7 new operational scopes — verbatim from auth/admin_scopes.py at expansion.
_NEW_SCOPES = [
    "admin.galaxy.manage",
    "admin.players.adjust_credits",
    "admin.ships.manage",
    "admin.combat.intervene",
    "admin.economy.intervene",
    "admin.security.act",
    "admin.disputes.resolve",
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
