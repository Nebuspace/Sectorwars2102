"""LEG-28 — admin re-engagement queue routes (DB-free).

Pins summary counts, serializer shape, CONTACTED/RESOLVED writes, and
the no-reopen-RESOLVED guard. List filtering is exercised via the
serializer + status vocabulary constants rather than SQLAlchemy expr trees.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException

from src.api.routes import admin_re_engagement as route


class _FakeQuery:
    """Minimal query chain for update_re_engagement_status (filter → options → first)."""

    def __init__(self, rows):
        self._rows = list(rows)

    def options(self, *args, **kwargs):
        return self

    def filter(self, *args, **kwargs):
        # Match by entry id when the BinaryExpression carries a UUID on the right.
        for arg in args:
            right = getattr(arg, "right", None)
            value = getattr(right, "value", None) if right is not None else None
            if value is not None:
                self._rows = [r for r in self._rows if r.id == value]
        return self

    def first(self):
        return self._rows[0] if self._rows else None


class _SummaryQuery:
    def __init__(self, pairs):
        self._pairs = pairs

    def group_by(self, *args, **kwargs):
        return self

    def all(self):
        return self._pairs


class _FakeDB:
    def __init__(self, rows):
        self.rows = rows
        self.committed = False

    def query(self, *cols):
        if len(cols) == 2:
            counts: dict[str, int] = {}
            for r in self.rows:
                counts[r.status] = counts.get(r.status, 0) + 1
            return _SummaryQuery(list(counts.items()))
        return _FakeQuery(self.rows)

    def commit(self):
        self.committed = True

    def refresh(self, row):
        return row


def _run(coro):
    return asyncio.run(coro)


def _row(*, status="OPEN", signals=None, player_nickname="AtRisk"):
    pid = uuid4()
    player = SimpleNamespace(nickname=player_nickname)
    return SimpleNamespace(
        id=uuid4(),
        player_id=pid,
        player=player,
        signals=signals or ["dormant_session"],
        signal_detail={"dormant_session": {"threshold": 7}},
        status=status,
        computed_at=datetime(2026, 8, 16, tzinfo=timezone.utc),
        computed_day=1,
        resolved_at=None,
    )


def test_summary_counts_by_status():
    rows = [
        _row(status="OPEN"),
        _row(status="OPEN"),
        _row(status="CONTACTED"),
        _row(status="RESOLVED"),
    ]
    db = _FakeDB(rows)
    result = _run(route.re_engagement_summary(admin=SimpleNamespace(), db=db))
    assert result["open"] == 2
    assert result["contacted"] == 1
    assert result["resolved"] == 1
    assert result["total"] == 4
    assert result["open_share"] == 0.5


def test_serialize_row_shape():
    row = _row()
    payload = route._serialize_row(row)
    assert payload["id"] == str(row.id)
    assert payload["player_id"] == str(row.player_id)
    assert payload["player_nickname"] == "AtRisk"
    assert payload["signals"] == ["dormant_session"]
    assert payload["status"] == "OPEN"
    assert payload["computed_at"].startswith("2026-08-16")


def test_update_marks_contacted(monkeypatch):
    entry = _row(status="OPEN")
    db = _FakeDB([entry])
    logged = {}

    def _log(db_arg, **kwargs):
        logged.update(kwargs)
        return None

    monkeypatch.setattr(route, "log_admin_action", _log)

    body = route.ReEngagementStatusUpdate(status="CONTACTED", note="pinged")
    result = _run(
        route.update_re_engagement_status(
            entry_id=entry.id,
            body=body,
            admin=SimpleNamespace(id=uuid4()),
            db=db,
        )
    )
    assert result["status"] == "CONTACTED"
    assert db.committed is True
    assert logged["action"] == "re_engagement_status_update"
    assert logged["payload"]["new_status"] == "CONTACTED"
    assert logged["payload"]["note"] == "pinged"


def test_update_resolve_sets_resolved_at(monkeypatch):
    entry = _row(status="CONTACTED")
    db = _FakeDB([entry])
    monkeypatch.setattr(route, "log_admin_action", lambda *a, **k: None)

    body = route.ReEngagementStatusUpdate(status="RESOLVED")
    result = _run(
        route.update_re_engagement_status(
            entry_id=entry.id,
            body=body,
            admin=SimpleNamespace(id=uuid4()),
            db=db,
        )
    )
    assert result["status"] == "RESOLVED"
    assert entry.resolved_at is not None


def test_update_rejects_reopen_resolved(monkeypatch):
    entry = _row(status="RESOLVED")
    entry.resolved_at = datetime(2026, 8, 1, tzinfo=timezone.utc)
    db = _FakeDB([entry])
    monkeypatch.setattr(route, "log_admin_action", lambda *a, **k: None)

    body = route.ReEngagementStatusUpdate(status="CONTACTED")
    with pytest.raises(HTTPException) as exc:
        _run(
            route.update_re_engagement_status(
                entry_id=entry.id,
                body=body,
                admin=SimpleNamespace(id=uuid4()),
                db=db,
            )
        )
    assert exc.value.status_code == 400


def test_update_404_when_missing(monkeypatch):
    db = _FakeDB([])
    monkeypatch.setattr(route, "log_admin_action", lambda *a, **k: None)
    body = route.ReEngagementStatusUpdate(status="CONTACTED")
    with pytest.raises(HTTPException) as exc:
        _run(
            route.update_re_engagement_status(
                entry_id=uuid4(),
                body=body,
                admin=SimpleNamespace(id=uuid4()),
                db=db,
            )
        )
    assert exc.value.status_code == 404
