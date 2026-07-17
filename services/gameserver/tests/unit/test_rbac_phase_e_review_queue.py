"""RBAC Phase E — review-queue GET + retention policy (view half; no mark-reviewed).

DB-free source asserts + sqlite ordering smoke for stale-first / HIGH_IMPACT filter.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import Column, DateTime, String, Text, case, create_engine, event
from sqlalchemy.orm import declarative_base, sessionmaker
from sqlalchemy.pool import StaticPool

from src.auth.admin_scopes import (
    AUDIT_VIEW,
    HIGH_IMPACT_SCOPES,
    PLAYERS_VIEW,
    SCOPES_GRANT,
)

_GS_ROOT = Path(__file__).resolve().parents[2]
_AUDIT_SRC = (_GS_ROOT / "src" / "api" / "routes" / "audit.py").read_text()
_MODEL_SRC = (_GS_ROOT / "src" / "models" / "admin_action_log.py").read_text()
_SRC_ROOT = _GS_ROOT / "src"


def _extract_route_block(source: str, route_marker: str) -> str:
    return source.split(route_marker, 1)[1].split("@router.", 1)[0]


class TestReviewQueueEndpointSource:
    def test_requires_audit_view(self):
        block = _extract_route_block(_AUDIT_SRC, '@router.get("/review-queue"')
        assert "require_scope(AUDIT_VIEW)" in block
        assert block.index("require_scope(AUDIT_VIEW)") < block.index("get_db")

    def test_filters_high_impact_unreviewed_only(self):
        block = _extract_route_block(_AUDIT_SRC, '@router.get("/review-queue"')
        assert "HIGH_IMPACT_SCOPES" in block
        assert "reviewed_at.is_(None)" in block
        assert "stale" in block
        assert "REVIEW_STALE_AFTER" in _AUDIT_SRC

    def test_no_mutation_verbs_on_review_queue_surface(self):
        block = _extract_route_block(_AUDIT_SRC, '@router.get("/review-queue"')
        assert "@router.post" not in block
        assert "@router.patch" not in block
        assert "@router.put" not in block
        assert "@router.delete" not in block
        assert "db.commit" not in block
        # Response shape only — no write of reviewed_* on this GET.
        assert "reviewed_at =" not in block
        assert "reviewed_by =" not in block

    def test_errors_are_generic(self):
        block = _extract_route_block(_AUDIT_SRC, '@router.get("/review-queue"')
        assert 'detail="Failed to list review queue"' in block
        assert "detail=str(e)" not in block

    def test_page_bounded(self):
        block = _extract_route_block(_AUDIT_SRC, '@router.get("/review-queue"')
        assert "le=10000" in block or "le = 10000" in block


class TestRetentionPolicy:
    def test_model_documents_five_year_retention(self):
        assert "5 years" in _MODEL_SRC or "five years" in _MODEL_SRC.lower()
        assert "no application purge" in _MODEL_SRC.lower() or "no purge" in _MODEL_SRC.lower()

    def test_no_src_helper_deletes_admin_action_log(self):
        """No app-layer DELETE / purge of AdminActionLog (Phase E retention)."""
        offenders: list[str] = []
        for path in _SRC_ROOT.rglob("*.py"):
            if "versions" in path.parts or path.name.startswith("alembic"):
                continue
            # Policy docstring lives on the model — skip that file for purge-word scan.
            if path.name == "admin_action_log.py" and path.parent.name == "models":
                text = path.read_text()
                # Still forbid executable delete on the model module.
                if "session.delete" in text or ".query(AdminActionLog).delete" in text:
                    offenders.append(str(path.relative_to(_GS_ROOT)))
                continue
            text = path.read_text()
            if "AdminActionLog" not in text and "admin_action_logs" not in text:
                continue
            # Executable delete patterns only (not comments about the DB trigger).
            for line in text.splitlines():
                stripped = line.strip()
                if stripped.startswith("#") or stripped.startswith('"""') or stripped.startswith("'''"):
                    continue
                compact = stripped.replace(" ", "").lower()
                if "delete(adminactionlog" in compact:
                    offenders.append(f"{path.relative_to(_GS_ROOT)}:{stripped[:80]}")
                if "query(adminactionlog)" in compact and ".delete(" in compact:
                    offenders.append(f"{path.relative_to(_GS_ROOT)}:{stripped[:80]}")
                if "session.delete(" in compact and "adminaction" in compact:
                    offenders.append(f"{path.relative_to(_GS_ROOT)}:{stripped[:80]}")
        assert offenders == [], f"AdminActionLog delete/purge helpers: {offenders}"


class TestMarkReviewedEndpointSource:
    def test_requires_audit_review_not_audit_view(self):
        block = _extract_route_block(_AUDIT_SRC, '@router.post("/actions/{action_id}/review"')
        assert "require_scope(AUDIT_REVIEW)" in block
        assert "require_scope(AUDIT_VIEW)" not in block
        from src.auth.admin_scopes import AUDIT_REVIEW, HIGH_IMPACT_SCOPES

        assert AUDIT_REVIEW not in HIGH_IMPACT_SCOPES
        assert AUDIT_REVIEW == "admin.audit.review"

    def test_logs_before_commit_same_txn(self):
        block = _extract_route_block(_AUDIT_SRC, '@router.post("/actions/{action_id}/review"')
        assert "log_admin_action" in block
        assert block.index("log_admin_action") < block.index("db.commit()")
        assert 'action="audit_review"' in block
        assert "scope_used=AUDIT_REVIEW" in block

    def test_idempotent_already_reviewed_skips_log(self):
        block = _extract_route_block(_AUDIT_SRC, '@router.post("/actions/{action_id}/review"')
        assert "already_reviewed" in block
        assert "reviewed_at is not None" in block

    def test_rejects_non_high_impact(self):
        block = _extract_route_block(_AUDIT_SRC, '@router.post("/actions/{action_id}/review"')
        assert "HIGH_IMPACT_SCOPES" in block
        assert "400" in block


class TestReviewQueueOrderingSmoke:
    """SQLite — HIGH_IMPACT + unreviewed filter; stale-first then newest."""

    def test_stale_high_impact_unreviewed_ordering(self):
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

        now = datetime(2026, 7, 17, tzinfo=timezone.utc)
        stale_before = now - timedelta(days=30)
        fresh = now - timedelta(days=2)
        stale = now - timedelta(days=45)
        older_stale = now - timedelta(days=60)

        hi = SCOPES_GRANT
        assert hi in HIGH_IMPACT_SCOPES
        lo = PLAYERS_VIEW
        assert lo not in HIGH_IMPACT_SCOPES

        rows = [
            ActionRow(
                id="fresh-hi",
                scope_used=hi,
                action="scope_grant",
                at=fresh,
            ),
            ActionRow(
                id="stale-hi",
                scope_used=hi,
                action="scope_revoke",
                at=stale,
            ),
            ActionRow(
                id="older-stale-hi",
                scope_used=hi,
                action="galaxy_manage",
                at=older_stale,
            ),
            ActionRow(
                id="reviewed-hi",
                scope_used=hi,
                action="scope_grant",
                reviewed_at=now,
                at=stale,
            ),
            ActionRow(
                id="unreviewed-lo",
                scope_used=lo,
                action="players_view",
                at=stale,
            ),
            ActionRow(
                id="audit-view-hi-shape",
                scope_used=AUDIT_VIEW,
                action="audit_view",
                at=stale,
            ),
        ]
        db.add_all(rows)
        db.commit()

        assert AUDIT_VIEW not in HIGH_IMPACT_SCOPES

        base = db.query(ActionRow).filter(
            ActionRow.reviewed_at.is_(None),
            ActionRow.scope_used.in_(list(HIGH_IMPACT_SCOPES)),
        )
        assert base.count() == 3

        stale_rank = case((ActionRow.at < stale_before, 1), else_=0)
        ordered = (
            base.order_by(stale_rank.desc(), ActionRow.at.desc()).all()
        )
        ids = [r.id for r in ordered]
        # Stale bucket first (newest-within-stale), then fresh.
        assert ids == ["stale-hi", "older-stale-hi", "fresh-hi"]

        for row in ordered:
            at = row.at
            if at.tzinfo is None:
                at = at.replace(tzinfo=timezone.utc)
            is_stale = at < stale_before
            if row.id == "fresh-hi":
                assert not is_stale
            else:
                assert is_stale

        db.close()
        engine.dispose()
