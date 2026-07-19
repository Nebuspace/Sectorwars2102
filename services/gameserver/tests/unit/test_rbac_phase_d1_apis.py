"""RBAC Phase D1 — read-only audit actions + scope catalog/holders APIs.

DB-free source asserts + lightweight sqlite pagination smoke.
Run with the same env harness as test_rbac_phase_a1.py.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from pathlib import Path

from sqlalchemy import Column, DateTime, String, Text, create_engine, event
from sqlalchemy.orm import declarative_base, sessionmaker
from sqlalchemy.pool import StaticPool

from src.auth.admin_scopes import ALL_SCOPES, AUDIT_VIEW, PLAYERS_VIEW, SCOPES_GRANT
from src.auth.admin_scopes import SCOPE_DESCRIPTIONS

_GS_ROOT = Path(__file__).resolve().parents[2]
_ADMIN_SCOPES_SRC = (_GS_ROOT / "src" / "api" / "routes" / "admin_scopes.py").read_text()
_AUDIT_SRC = (_GS_ROOT / "src" / "api" / "routes" / "audit.py").read_text()


def _extract_route_block(source: str, route_marker: str) -> str:
    return source.split(route_marker, 1)[1].split("@router.", 1)[0]


class TestScopeCatalogDescriptions:
    def test_catalog_covers_all_scopes_with_nonempty_descriptions(self):
        assert len(SCOPE_DESCRIPTIONS) == 27
        assert set(SCOPE_DESCRIPTIONS.keys()) == ALL_SCOPES
        for scope, desc in SCOPE_DESCRIPTIONS.items():
            assert desc.strip(), f"missing description for {scope!r}"

    def test_catalog_route_returns_scope_catalog_items(self):
        assert 'response_model=List[ScopeCatalogItem]' in _ADMIN_SCOPES_SRC
        assert "SCOPE_DESCRIPTIONS" in _ADMIN_SCOPES_SRC
        assert "27-scope catalog" in _ADMIN_SCOPES_SRC
        assert "26-scope" not in _ADMIN_SCOPES_SRC


class TestScopeHoldersGate:
    def test_holders_requires_scopes_grant(self):
        block = _extract_route_block(_ADMIN_SCOPES_SRC, '@router.get("/holders"')
        grant_idx = block.index("require_scope(SCOPES_GRANT)")
        assert grant_idx < block.index("db.query"), "scope gate should precede DB work"

    def test_holders_does_not_use_players_view(self):
        block = _extract_route_block(_ADMIN_SCOPES_SRC, '@router.get("/holders"')
        assert "PLAYERS_VIEW" not in block
        assert "require_scope(SCOPES_GRANT)" in block

    def test_holders_docstring_explains_meta_admin_gate(self):
        block = _extract_route_block(_ADMIN_SCOPES_SRC, '@router.get("/holders"')
        assert "meta-admin" in block.lower() or "scope-management" in block.lower()


class TestAdminActionLogListEndpoint:
    def test_actions_requires_audit_view(self):
        block = _extract_route_block(_AUDIT_SRC, '@router.get("/actions"')
        assert "require_scope(AUDIT_VIEW)" in block
        assert block.index("require_scope(AUDIT_VIEW)") < block.index("get_db")

    def test_actions_queries_admin_action_log_not_audit_log(self):
        block = _extract_route_block(_AUDIT_SRC, '@router.get("/actions"')
        assert "AdminActionLog" in block
        assert "AuditLog" not in block

    def test_actions_is_get_only_no_mutations_on_path(self):
        block = _extract_route_block(_AUDIT_SRC, '@router.get("/actions"')
        assert "@router.post" not in block
        assert "@router.patch" not in block
        assert "@router.delete" not in block
        assert "AdminActionLogPageOut" in block
        assert "order_by(AdminActionLog.at.desc())" in block

    def test_actions_page_is_bounded_and_errors_are_generic(self):
        block = _extract_route_block(_AUDIT_SRC, '@router.get("/actions"')
        assert "le=10000" in block or "le = 10000" in block
        assert 'detail="Failed to list admin actions"' in block
        assert "detail=str(e)" not in block

    def test_legacy_audit_logs_endpoints_untouched(self):
        assert '@router.get("/logs")' in _AUDIT_SRC
        assert '@router.post("/log")' in _AUDIT_SRC


class TestAdminActionLogPaginationSmoke:
    """SQLite harness — filter + pagination on AdminActionLog query shape."""

    def test_filter_and_pagination_newest_first(self):
        Base = declarative_base()

        class ActionRow(Base):
            __tablename__ = "admin_action_logs"
            id = Column(String(36), primary_key=True)
            admin_user_id = Column(String(36), nullable=True)
            scope_used = Column(String(120), nullable=True)
            action = Column(String(200), nullable=False)
            target_type = Column(String(100), nullable=True)
            target_id = Column(String(255), nullable=True)
            payload_snapshot = Column(Text, nullable=True)
            result = Column(String(50), nullable=True)
            failure_reason = Column(Text, nullable=True)
            reviewed_by = Column(String(36), nullable=True)
            reviewed_at = Column(DateTime(timezone=True), nullable=True)
            at = Column(DateTime(timezone=True), nullable=False)

        engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )

        @event.listens_for(engine, "connect")
        def _fk(dbapi_conn, _):
            dbapi_conn.execute("PRAGMA foreign_keys=ON")

        ActionRow.__table__.create(engine)
        Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
        db = Session()

        admin_a = uuid.uuid4()
        admin_b = uuid.uuid4()
        t0 = datetime(2026, 1, 1)
        t1 = datetime(2026, 1, 2)
        t2 = datetime(2026, 1, 3)

        rows = [
            ActionRow(
                id=str(uuid.uuid4()),
                admin_user_id=str(admin_a),
                scope_used=AUDIT_VIEW,
                action="scope_grant",
                target_type="user",
                target_id=str(uuid.uuid4()),
                at=t0,
            ),
            ActionRow(
                id=str(uuid.uuid4()),
                admin_user_id=str(admin_b),
                scope_used=SCOPES_GRANT,
                action="player_suspend",
                target_type="player",
                target_id=str(uuid.uuid4()),
                at=t2,
            ),
            ActionRow(
                id=str(uuid.uuid4()),
                admin_user_id=str(admin_a),
                scope_used=PLAYERS_VIEW,
                action="scope_revoke",
                target_type="user",
                target_id=str(uuid.uuid4()),
                at=t1,
            ),
        ]
        db.add_all(rows)
        db.commit()

        filtered = (
            db.query(ActionRow)
            .filter(ActionRow.admin_user_id == str(admin_a))
            .order_by(ActionRow.at.desc())
        )
        total = filtered.count()
        page_items = filtered.offset(0).limit(1).all()

        assert total == 2
        assert len(page_items) == 1
        assert page_items[0].action == "scope_revoke"
        assert page_items[0].at == t1

        db.close()
        engine.dispose()
