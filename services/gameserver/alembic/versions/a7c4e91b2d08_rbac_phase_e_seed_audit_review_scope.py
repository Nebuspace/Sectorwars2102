"""RBAC Phase E: seed admin.audit.review (27th scope) to existing admins.

Additive follow-on to the operational expansion (f3a8c2d91e47).  Every User
with ``is_admin = true`` receives ``admin.audit.review`` so oversight is not
locked out on cutover.  Idempotent: skip if an active grant already exists.

Revision ID: a7c4e91b2d08
Revises: f3a8c2d91e47
Create Date: 2026-07-17 20:10:00.000000
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "a7c4e91b2d08"
down_revision = "f3a8c2d91e47"
branch_labels = None
depends_on = None

_NEW_SCOPES = [
    "admin.audit.review",
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
