"""LEG-59 — medal pin privacy helpers (DB-free)."""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest

from src.services.medal_service import public_medal_identity, set_pinned_medal_id


class _FakeQuery:
    def __init__(self, value):
        self._value = value

    def filter(self, *_a, **_k):
        return self

    def first(self):
        return self._value


class _FakeDb:
    def __init__(self, held=True):
        self.held = held
        self.flush_calls = 0

    def query(self, *_a, **_k):
        return _FakeQuery(object() if self.held else None)

    def flush(self):
        self.flush_calls += 1


def test_public_medal_identity_reads_pin_and_count():
    player = SimpleNamespace(
        settings={"medal_privacy": {"pinned_medal_id": "bronze_cluster", "show_count_publicly": True}}
    )
    out = public_medal_identity(player, medal_count=3)
    assert out == {"pinned_medal_id": "bronze_cluster", "medal_count": 3}


def test_public_medal_identity_hides_count_when_disabled():
    player = SimpleNamespace(
        settings={"medal_privacy": {"pinned_medal_id": "x", "show_count_publicly": False}}
    )
    out = public_medal_identity(player, medal_count=9)
    assert out["pinned_medal_id"] == "x"
    assert out["medal_count"] is None


def test_set_pinned_medal_clears_with_none(monkeypatch):
    player = SimpleNamespace(
        id=uuid4(),
        settings={"medal_privacy": {"pinned_medal_id": "old", "unviewed_awards": []}},
    )
    db = _FakeDb(held=True)
    monkeypatch.setattr("src.services.medal_service.flag_modified", lambda *_a, **_k: None)
    assert set_pinned_medal_id(db, player, None) == ""
    assert player.settings["medal_privacy"]["pinned_medal_id"] is None
    assert db.flush_calls == 1


def test_set_pinned_rejects_unearned(monkeypatch):
    player = SimpleNamespace(id=uuid4(), settings={})
    db = _FakeDb(held=False)

    # Avoid catalog miss — pin a known-looking id that FakeDb says is unheld.
    monkeypatch.setattr(
        "src.services.medal_service.get_catalog_entry",
        lambda mid: {"name": "Test"} if mid else None,
    )
    monkeypatch.setattr(
        "src.services.medal_service.MEDAL_CATALOG",
        {"test_medal": {}},
    )
    with pytest.raises(ValueError, match="not earned"):
        set_pinned_medal_id(db, player, "test_medal")
