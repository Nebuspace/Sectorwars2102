"""Unit tests for RBAC Phase A1 — scope catalog, models, seed logic.

All tests are DB-free: no live Postgres, no ORM session.  Run with:

    GAMESERVER_CI_DB_FREE=1 ENVIRONMENT=testing \
        DATABASE_URL="postgresql://dummy:dummy@localhost:5432/dummy" \
        JWT_SECRET="$(python -c "import secrets; print(secrets.token_hex(32))")" \
        ADMIN_USERNAME=sysadmin ADMIN_PASSWORD=sysadmin-dev-only \
        ARIA_ENCRYPTION_KEY="$(python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())")" \
        pytest services/gameserver/tests/unit/test_rbac_phase_a1.py -v
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock, patch

import pytest

from src.auth import admin_scopes
from src.auth.admin_scopes import (
    ALL_SCOPES,
    HIGH_IMPACT_SCOPES,
    META_SCOPES,
    SCOPES_GRANT,
    SCOPES_REVOKE,
    AUDIT_VIEW,
    SUBSCRIPTIONS_VIEW,
    SUBSCRIPTIONS_MODIFY,
    SUBSCRIPTIONS_REFUND,
    WEBHOOKS_REPLAY,
    REGIONS_TERMINATE,
)
from src.models.admin_scope_grant import AdminScopeGrant
from src.models.admin_action_log import AdminActionLog


# ---------------------------------------------------------------------------
# Scope catalog
# ---------------------------------------------------------------------------

class TestScopeCatalog:
    """The catalog is the authoritative scope vocabulary (ADR-0058 + staged ops)."""

    def test_catalog_size(self):
        # ADR-0058: 19 platform + 7 operational + admin.audit.review (26→27)
        # + admin.system.health_view (27→28, WO gameserver-CI-fix 2026-07-19).
        assert len(ALL_SCOPES) == 28

    def test_all_scopes_start_with_admin_prefix(self):
        for scope in ALL_SCOPES:
            assert scope.startswith("admin."), f"Scope missing prefix: {scope!r}"

    def test_high_impact_subset_is_subset_of_all(self):
        assert HIGH_IMPACT_SCOPES <= ALL_SCOPES

    def test_meta_subset_is_subset_of_all(self):
        assert META_SCOPES <= ALL_SCOPES

    def test_high_impact_contains_expected_members(self):
        assert SUBSCRIPTIONS_VIEW in HIGH_IMPACT_SCOPES
        assert SUBSCRIPTIONS_MODIFY in HIGH_IMPACT_SCOPES
        assert SUBSCRIPTIONS_REFUND in HIGH_IMPACT_SCOPES
        assert WEBHOOKS_REPLAY in HIGH_IMPACT_SCOPES
        assert REGIONS_TERMINATE in HIGH_IMPACT_SCOPES
        assert SCOPES_GRANT in HIGH_IMPACT_SCOPES
        assert SCOPES_REVOKE in HIGH_IMPACT_SCOPES
        assert admin_scopes.DISPUTES_RESOLVE in HIGH_IMPACT_SCOPES
        assert admin_scopes.GALAXY_MANAGE in HIGH_IMPACT_SCOPES
        assert admin_scopes.PLAYERS_ADJUST_CREDITS in HIGH_IMPACT_SCOPES
        assert admin_scopes.SHIPS_MANAGE in HIGH_IMPACT_SCOPES

    def test_high_impact_count(self):
        # subscriptions.* (3) + webhooks.replay + regions.terminate + scopes.* (2)
        # + disputes.resolve + galaxy.manage + players.adjust_credits + ships.manage
        assert len(HIGH_IMPACT_SCOPES) == 11

    def test_meta_scopes_contains_three_members(self):
        assert len(META_SCOPES) == 3
        assert SCOPES_GRANT in META_SCOPES
        assert SCOPES_REVOKE in META_SCOPES
        assert AUDIT_VIEW in META_SCOPES

    def test_all_scope_values_are_strings(self):
        for scope in ALL_SCOPES:
            assert isinstance(scope, str)

    def test_no_duplicate_scope_values(self):
        # frozenset is de-duped by construction; verify the constants themselves
        expected = [
            admin_scopes.PLAYERS_VIEW,
            admin_scopes.PLAYERS_SUSPEND,
            admin_scopes.PLAYERS_ADJUST_REP,
            admin_scopes.PLAYERS_TRANSFER_ASSETS,
            admin_scopes.SUBSCRIPTIONS_VIEW,
            admin_scopes.SUBSCRIPTIONS_MODIFY,
            admin_scopes.SUBSCRIPTIONS_REFUND,
            admin_scopes.WEBHOOKS_VIEW,
            admin_scopes.WEBHOOKS_REPLAY,
            admin_scopes.REGIONS_VIEW,
            admin_scopes.REGIONS_CREATE,
            admin_scopes.REGIONS_TERMINATE,
            admin_scopes.REGIONS_TRANSFER_OWNERSHIP,
            admin_scopes.ARIA_AUDIT,
            admin_scopes.MULTI_ACCOUNT_REVIEW,
            admin_scopes.BANG_REGENERATE,
            admin_scopes.SCOPES_GRANT,
            admin_scopes.SCOPES_REVOKE,
            admin_scopes.AUDIT_VIEW,
            admin_scopes.AUDIT_REVIEW,
            admin_scopes.GALAXY_MANAGE,
            admin_scopes.PLAYERS_ADJUST_CREDITS,
            admin_scopes.SHIPS_MANAGE,
            admin_scopes.COMBAT_INTERVENE,
            admin_scopes.ECONOMY_INTERVENE,
            admin_scopes.SECURITY_ACT,
            admin_scopes.DISPUTES_RESOLVE,
            admin_scopes.SYSTEM_HEALTH_VIEW,
        ]
        assert len(expected) == len(set(expected)), "Duplicate scope value in catalog constants"
        assert set(expected) == ALL_SCOPES


# ---------------------------------------------------------------------------
# AdminScopeGrant model (import + property behaviour)
# ---------------------------------------------------------------------------

class TestAdminScopeGrantModel:
    """Model-level tests — no DB required."""

    def _make_grant(self, revoked_at=None) -> AdminScopeGrant:
        return AdminScopeGrant(
            id=uuid.uuid4(),
            user_id=uuid.uuid4(),
            scope=admin_scopes.PLAYERS_VIEW,
            granted_by=None,
            revoked_at=revoked_at,
            revoked_by=None,
        )

    def test_is_active_when_not_revoked(self):
        g = self._make_grant(revoked_at=None)
        assert g.is_active is True

    def test_is_not_active_when_revoked(self):
        from datetime import datetime, timezone
        g = self._make_grant(revoked_at=datetime.now(timezone.utc))
        assert g.is_active is False

    def test_repr_contains_scope(self):
        g = self._make_grant()
        assert admin_scopes.PLAYERS_VIEW in repr(g)

    def test_model_imports_cleanly(self):
        # verifies no circular import at module load
        import importlib
        mod = importlib.import_module("src.models.admin_scope_grant")
        assert hasattr(mod, "AdminScopeGrant")

    def test_tablename(self):
        assert AdminScopeGrant.__tablename__ == "admin_scope_grants"

    def test_has_expected_columns(self):
        cols = {c.key for c in AdminScopeGrant.__table__.columns}
        assert cols >= {"id", "user_id", "scope", "granted_by", "granted_at",
                        "revoked_at", "revoked_by"}

    def test_active_grant_index_is_unique_partial(self):
        """Cipher #3: ix_admin_scope_grants_active must be UNIQUE WHERE revoked_at IS NULL."""
        idx = next(
            i for i in AdminScopeGrant.__table__.indexes
            if i.name == "ix_admin_scope_grants_active"
        )
        assert idx.unique is True
        assert [c.name for c in idx.columns] == ["user_id", "scope"]
        # SQLAlchemy stores the partial predicate on dialect_options / kwargs
        where = idx.dialect_options.get("postgresql", {}).get("where")
        if where is None:
            where = getattr(idx, "kwargs", {}).get("postgresql_where")
        assert where is not None
        assert "revoked_at IS NULL" in str(where)


# ---------------------------------------------------------------------------
# AdminActionLog model
# ---------------------------------------------------------------------------

class TestAdminActionLogModel:
    def test_model_imports_cleanly(self):
        import importlib
        mod = importlib.import_module("src.models.admin_action_log")
        assert hasattr(mod, "AdminActionLog")

    def test_tablename(self):
        assert AdminActionLog.__tablename__ == "admin_action_logs"

    def test_has_expected_columns(self):
        cols = {c.key for c in AdminActionLog.__table__.columns}
        assert cols >= {
            "id", "admin_user_id", "scope_used", "action",
            "target_type", "target_id", "payload_snapshot",
            "result", "failure_reason", "reviewed_by", "reviewed_at", "at",
        }

    def test_admin_user_id_fk_ondelete_set_null(self):
        """Cipher: deleting a user must NOT cascade-delete their audit trail."""
        fk = next(
            fk for fk in AdminActionLog.__table__.c.admin_user_id.foreign_keys
        )
        assert fk.onupdate is None
        # ondelete is stored on the ForeignKeyConstraint
        fk_constraint = next(
            c for c in AdminActionLog.__table__.constraints
            if hasattr(c, "elements")
            and any(e.parent.key == "admin_user_id" for e in getattr(c, "elements", []))
        )
        assert fk_constraint.ondelete.upper() == "SET NULL"

    def test_repr(self):
        log = AdminActionLog(
            id=uuid.uuid4(),
            admin_user_id=uuid.uuid4(),
            action="suspend_player",
            scope_used=admin_scopes.PLAYERS_SUSPEND,
        )
        r = repr(log)
        assert "suspend_player" in r
        assert admin_scopes.PLAYERS_SUSPEND in r


# ---------------------------------------------------------------------------
# Seed logic (unit-testable helper extracted from the migration)
# ---------------------------------------------------------------------------

def _seed_helper(admin_user_ids: List[str], existing_active_grants: Dict[str, List[str]]) -> Dict[str, List[str]]:
    """
    Pure-Python replica of the migration's seed logic.

    Returns a dict of user_id → list[scope] that WOULD be inserted
    (i.e. scopes not already active).

    ``existing_active_grants``: {user_id: [scope, ...]} of pre-existing active grants.
    """
    to_insert: Dict[str, List[str]] = {}
    all_scopes = list(admin_scopes.ALL_SCOPES)
    for uid in admin_user_ids:
        existing = set(existing_active_grants.get(uid, []))
        missing = [s for s in all_scopes if s not in existing]
        if missing:
            to_insert[uid] = missing
    return to_insert


class TestSeedLogic:
    """Test the seed idempotency logic in isolation."""

    def test_fresh_admin_gets_all_catalog_scopes(self):
        uid = str(uuid.uuid4())
        result = _seed_helper([uid], {})
        assert set(result[uid]) == admin_scopes.ALL_SCOPES

    def test_two_fresh_admins_each_get_all_catalog_scopes(self):
        uid1, uid2 = str(uuid.uuid4()), str(uuid.uuid4())
        result = _seed_helper([uid1, uid2], {})
        assert set(result[uid1]) == admin_scopes.ALL_SCOPES
        assert set(result[uid2]) == admin_scopes.ALL_SCOPES

    def test_already_seeded_admin_gets_nothing(self):
        uid = str(uuid.uuid4())
        existing = {uid: list(admin_scopes.ALL_SCOPES)}
        result = _seed_helper([uid], existing)
        assert uid not in result or result[uid] == []

    def test_partially_seeded_admin_gets_remainder(self):
        uid = str(uuid.uuid4())
        have = [admin_scopes.PLAYERS_VIEW, admin_scopes.PLAYERS_SUSPEND]
        existing = {uid: have}
        result = _seed_helper([uid], existing)
        inserted = set(result.get(uid, []))
        assert admin_scopes.PLAYERS_VIEW not in inserted
        assert admin_scopes.PLAYERS_SUSPEND not in inserted
        # all remaining scopes are inserted (catalog size − already held)
        assert len(inserted) == len(admin_scopes.ALL_SCOPES) - len(have)

    def test_bootstrap_gets_meta_scopes_via_all_catalog(self):
        """Bootstrap (is_admin=true) gets full catalog which includes the 3 meta-scopes."""
        uid = str(uuid.uuid4())
        result = _seed_helper([uid], {})
        inserted = set(result.get(uid, []))
        assert admin_scopes.META_SCOPES <= inserted

    def test_no_admin_under_seeded(self):
        """Cipher: every admin in the input must end up with all catalog scopes after seed."""
        uids = [str(uuid.uuid4()) for _ in range(5)]
        # Simulate a partial pre-existing state (some already have some scopes)
        existing = {uids[2]: [admin_scopes.PLAYERS_VIEW]}
        result = _seed_helper(uids, existing)
        for uid in uids:
            pre = set(existing.get(uid, []))
            post = pre | set(result.get(uid, []))
            assert post == admin_scopes.ALL_SCOPES, (
                f"User {uid} ends up with {len(post)} scopes, expected {len(admin_scopes.ALL_SCOPES)}"
            )

    def test_empty_admin_list_produces_no_inserts(self):
        result = _seed_helper([], {})
        assert result == {}


# ---------------------------------------------------------------------------
# models/__init__.py registration
# ---------------------------------------------------------------------------

class TestModelsRegistration:
    """AdminScopeGrant and AdminActionLog must be importable from src.models."""

    def test_admin_scope_grant_importable_from_models(self):
        from src.models import AdminScopeGrant as Imported
        assert Imported is AdminScopeGrant

    def test_admin_action_log_importable_from_models(self):
        from src.models import AdminActionLog as Imported
        assert Imported is AdminActionLog


# ---------------------------------------------------------------------------
# A1-HARDEN — migration source assertions (DB-free)
# ---------------------------------------------------------------------------

class TestA1HardenMigrationSource:
    """Cipher #3/#4: not-yet-applied migration must ship unique + append-only."""

    @staticmethod
    def _migration_text() -> str:
        from pathlib import Path
        path = (
            Path(__file__).resolve().parents[2]
            / "alembic"
            / "versions"
            / "e2a7f3c8b5d1_rbac_phase_a1_admin_scope_tables.py"
        )
        return path.read_text()

    def test_active_grant_index_unique_true_in_migration(self):
        text = self._migration_text()
        assert 'unique=True' in text
        assert "ix_admin_scope_grants_active" in text
        assert 'revoked_at IS NULL' in text

    def test_append_only_trigger_in_migration(self):
        text = self._migration_text()
        assert "admin_action_logs_append_only" in text
        assert "trg_admin_action_logs_append_only" in text
        assert "only reviewed_by/reviewed_at may change" in text
        assert "DELETE forbidden" in text

    def test_app_role_revoke_and_column_grant_in_migration(self):
        text = self._migration_text()
        assert "REVOKE DELETE ON TABLE admin_action_logs FROM sectorwars_app" in text
        assert "REVOKE UPDATE ON TABLE admin_action_logs FROM sectorwars_app" in text
        assert "GRANT UPDATE (reviewed_by, reviewed_at)" in text
