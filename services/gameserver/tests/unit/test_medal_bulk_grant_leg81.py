"""LEG-81 — medal bulk-grant dry-run / commit (DB-free)."""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest

from src.services import medal_service as ms
from src.services.medal_service import (
    BULK_GRANT_MAX_RECIPIENTS,
    execute_bulk_grant,
    plan_bulk_grant,
    resolve_bulk_recipient_token,
)


class _FakeQuery:
    def __init__(self, rows=None, scalar=None):
        self._rows = list(rows or [])
        self._scalar = scalar

    def filter(self, *a, **k):
        return self

    def outerjoin(self, *a, **k):
        return self

    def first(self):
        return self._rows[0] if self._rows else None

    def all(self):
        return list(self._rows)

    def scalar(self):
        return self._scalar


class _BulkDb:
    """Minimal session stand-in for resolve + plan (no real SQL)."""

    def __init__(self, *, players_by_id=None, by_username=None, held=None):
        self.players_by_id = players_by_id or {}
        self.by_username = {k.lower(): v for k, v in (by_username or {}).items()}
        self.held = set(held or [])
        self.grants = []

    def query(self, *entities):
        # Player.id lookups / PlayerMedal.player_id aggregates
        name = getattr(entities[0], "key", None) or getattr(entities[0], "name", None)
        # Player.id column → existence check
        if hasattr(entities[0], "class_") and entities[0].class_.__name__ == "Player":
            # nickname/username path uses outerjoin; return via first()
            return self

        # Fallback: treat as PlayerMedal.player_id list for held check
        return _FakeHeldQuery(self.held)


class _FakeHeldQuery:
    def __init__(self, held):
        self._held = held

    def filter(self, *a, **k):
        return self

    def all(self):
        return [(pid,) for pid in self._held]


def test_resolve_empty_token():
    db = SimpleNamespace()
    pid, err = resolve_bulk_recipient_token(db, "  ")
    assert pid is None and err == "empty"


def test_plan_rejects_unknown_medal(monkeypatch):
    monkeypatch.setattr(ms, "get_catalog_entry", lambda mid: None)
    with pytest.raises(ValueError, match="Unknown medal"):
        plan_bulk_grant(SimpleNamespace(), "nope", ["x"])


def test_plan_rejects_over_cap(monkeypatch):
    monkeypatch.setattr(ms, "get_catalog_entry", lambda mid: {"name": "X"})
    monkeypatch.setattr(ms, "MEDAL_CATALOG", {"x": {}})
    with pytest.raises(ValueError, match="Too many"):
        plan_bulk_grant(SimpleNamespace(), "x", ["a"] * (BULK_GRANT_MAX_RECIPIENTS + 1))


def test_plan_dry_run_invalid_mix_no_mutation(monkeypatch):
    p1 = uuid4()
    p2 = uuid4()

    def fake_resolve(db, token):
        t = token.strip()
        if t == str(p1):
            return p1, None
        if t == "good-user":
            return p2, None
        if t == "already":
            return p1, None  # same as p1 — dedupe
        return None, "unknown_username"

    monkeypatch.setattr(ms, "resolve_bulk_recipient_token", fake_resolve)
    monkeypatch.setattr(ms, "get_catalog_entry", lambda mid: {"name": "Bronze"})
    monkeypatch.setattr(ms, "MEDAL_CATALOG", {"combat.bronze_star": {}})
    monkeypatch.setattr(ms, "LEGACY_KEY_TO_ID", {})

    class Db:
        def query(self, *a, **k):
            return _FakeHeldQuery({p1})  # p1 already holds

    plan = plan_bulk_grant(
        Db(),
        "combat.bronze_star",
        [str(p1), "good-user", "bad-user", "already", ""],
    )
    assert plan["valid_count"] == 2  # p1 + p2
    assert plan["already_held_count"] == 1
    assert plan["grantable_count"] == 1
    assert plan["invalid_count"] == 2  # bad-user + empty
    assert any(s["reason"] == "unknown_username" for s in plan["invalid_samples"])
    assert "grantable_player_ids" in plan


def test_execute_assigns_shared_batch_id(monkeypatch):
    p1, p2 = uuid4(), uuid4()
    batch_seen = []

    monkeypatch.setattr(
        ms,
        "plan_bulk_grant",
        lambda db, mid, rec: {
            "medal_id": "combat.bronze_star",
            "valid_count": 2,
            "invalid_count": 0,
            "already_held_count": 0,
            "grantable_count": 2,
            "invalid_samples": [],
            "grantable_player_ids": [p1, p2],
        },
    )

    class FakeSvc:
        def __init__(self, db):
            self.db = db

        def admin_grant(self, player_id, medal_id, granting_user_id, reason=None, **kw):
            batch_seen.append(kw.get("grant_batch_id"))
            assert kw.get("awarded_via") == "bulk"
            assert kw.get("suppress_toast") is False  # 2 <= 50
            return True

    monkeypatch.setattr(ms, "MedalService", FakeSvc)
    out = execute_bulk_grant(SimpleNamespace(), "combat.bronze_star", ["a", "b"], uuid4())
    assert out["granted_count"] == 2
    assert out["grant_batch_id"]
    assert len(set(batch_seen)) == 1
    assert str(batch_seen[0]) == out["grant_batch_id"]
    assert out["toast_suppressed"] is False


def test_execute_suppresses_toast_when_grantable_over_50(monkeypatch):
    """medals.md: personal toasts suppressed for batch sizes > 50."""
    ids = [uuid4() for _ in range(51)]
    suppress_flags = []

    monkeypatch.setattr(
        ms,
        "plan_bulk_grant",
        lambda db, mid, rec: {
            "medal_id": "combat.bronze_star",
            "valid_count": 51,
            "invalid_count": 0,
            "already_held_count": 0,
            "grantable_count": 51,
            "invalid_samples": [],
            "grantable_player_ids": ids,
        },
    )

    class FakeSvc:
        def __init__(self, db):
            self.db = db

        def admin_grant(self, player_id, medal_id, granting_user_id, reason=None, **kw):
            suppress_flags.append(kw.get("suppress_toast"))
            return True

    monkeypatch.setattr(ms, "MedalService", FakeSvc)
    out = execute_bulk_grant(SimpleNamespace(), "combat.bronze_star", ["x"] * 51, uuid4())
    assert out["granted_count"] == 51
    assert out["toast_suppressed"] is True
    assert all(flag is True for flag in suppress_flags)
