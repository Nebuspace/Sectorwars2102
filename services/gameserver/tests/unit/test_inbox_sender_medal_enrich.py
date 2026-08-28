"""LEG-2755 — inbox/conversation sender pinned_medal enrich (DB-free)."""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest

from src.services.message_service import MessageService


class _MedalCountQuery:
    def __init__(self, rows):
        self._rows = rows

    def filter(self, *_a, **_k):
        return self

    def group_by(self, *_a, **_k):
        return self

    def all(self):
        return self._rows


class _PlayerQuery:
    def __init__(self, players):
        self._players = players

    def filter(self, *_a, **_k):
        return self

    def all(self):
        return self._players


class _FakeDb:
    def __init__(self, *, medal_rows=None, players=None):
        self._medal_rows = medal_rows or []
        self._players = players or []

    def query(self, model, *_a, **_k):
        # db.query(PlayerMedal.player_id, func.count(...)) passes a column first
        entity = getattr(model, "class_", None) or model
        name = getattr(entity, "__name__", str(entity))
        if name == "PlayerMedal":
            return _MedalCountQuery(self._medal_rows)
        if name == "Player":
            return _PlayerQuery(self._players)
        raise AssertionError(f"unexpected query model {name!r}")


def _msg(sender_id, sender=None):
    return SimpleNamespace(sender_id=sender_id, sender=sender)


def _player(*, pinned=None, show_count=True):
    privacy = {}
    if pinned is not None:
        privacy["pinned_medal_id"] = pinned
    if show_count is not False:
        privacy["show_count_publicly"] = True
    else:
        privacy["show_count_publicly"] = False
    return SimpleNamespace(
        id=uuid4(),
        settings={"medal_privacy": privacy},
        nickname="Sender",
    )


def test_enrich_adds_sender_medal_fields_when_pin_public():
    sender = _player(pinned="bronze_cluster", show_count=True)
    sender.id = uuid4()
    msg = _msg(sender.id, sender=sender)
    msg_dict = {"id": "1", "sender_id": str(sender.id), "sender_name": "Sender"}
    db = _FakeDb(medal_rows=[(sender.id, 3)])

    out = MessageService._enrich_message_dicts_with_sender_medals(db, [msg], [msg_dict])

    assert out[0]["sender_pinned_medal_id"] == "bronze_cluster"
    assert out[0]["sender_medal_count"] == 3


def test_enrich_hides_count_when_privacy_disabled():
    sender = _player(pinned="bronze_cluster", show_count=False)
    sender.id = uuid4()
    msg = _msg(sender.id, sender=sender)
    msg_dict = {"id": "1"}
    db = _FakeDb(medal_rows=[(sender.id, 9)])

    out = MessageService._enrich_message_dicts_with_sender_medals(db, [msg], [msg_dict])

    assert out[0]["sender_pinned_medal_id"] == "bronze_cluster"
    assert out[0]["sender_medal_count"] is None


def test_enrich_no_pin_yields_null_medal_id():
    sender = _player(pinned=None, show_count=True)
    sender.id = uuid4()
    msg = _msg(sender.id, sender=sender)
    msg_dict = {"id": "1"}
    db = _FakeDb(medal_rows=[])

    out = MessageService._enrich_message_dicts_with_sender_medals(db, [msg], [msg_dict])

    assert out[0]["sender_pinned_medal_id"] is None
    assert out[0]["sender_medal_count"] == 0


def test_enrich_batch_deduplicates_sender_queries():
    sender = _player(pinned="x", show_count=True)
    sender.id = uuid4()
    msgs = [_msg(sender.id, sender=sender), _msg(sender.id, sender=sender)]
    dicts = [{"id": "1"}, {"id": "2"}]
    db = _FakeDb(medal_rows=[(sender.id, 2)])

    out = MessageService._enrich_message_dicts_with_sender_medals(db, msgs, dicts)

    assert out[0]["sender_pinned_medal_id"] == "x"
    assert out[1]["sender_pinned_medal_id"] == "x"
    assert out[0]["sender_medal_count"] == 2
    assert out[1]["sender_medal_count"] == 2


@pytest.mark.asyncio
async def test_get_inbox_includes_sender_medal_fields(monkeypatch):
    sender = _player(pinned="fleet_commander", show_count=True)
    sender.id = uuid4()
    recipient_id = uuid4()
    message_id = uuid4()

    def _to_dict(include_content=True):
        return {
            "id": str(message_id),
            "sender_id": str(sender.id),
            "sender_name": sender.nickname,
        }

    message = SimpleNamespace(
        id=message_id,
        sender_id=sender.id,
        sender=sender,
        to_dict=_to_dict,
    )

    class _InboxQuery:
        def filter(self, *_a, **_k):
            return self

        def count(self):
            return 1

        def options(self, *_a, **_k):
            return self

        def order_by(self, *_a, **_k):
            return self

        def limit(self, *_a, **_k):
            return self

        def offset(self, *_a, **_k):
            return self

        def all(self):
            return [message]

    db = SimpleNamespace(
        query=lambda *_a, **_k: _InboxQuery(),
    )

    real_enrich = MessageService._enrich_message_dicts_with_sender_medals

    def _patched_enrich(inner_db, messages, message_dicts):
        return real_enrich(
            _FakeDb(medal_rows=[(sender.id, 1)]),
            messages,
            message_dicts,
        )

    monkeypatch.setattr(
        MessageService,
        "_enrich_message_dicts_with_sender_medals",
        staticmethod(_patched_enrich),
    )

    result = await MessageService.get_inbox(db, player_id=recipient_id)

    row = result["messages"][0]
    assert row["sender_pinned_medal_id"] == "fleet_commander"
    assert row["sender_medal_count"] == 1
