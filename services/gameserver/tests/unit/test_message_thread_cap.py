"""Regression pin for the message-thread depth cap (WO-SECA-PIN-TESTS Lane D).

Pins MessageService.send_message (message_service.py:82) against the
canonical thread-depth rule (messaging.md:45): a thread holds at most
THREAD_MESSAGE_CAP messages; the 51st send/reply is REJECTED (409
thread_limit_exceeded) before Message creation (:160-168, :171) — nothing is
trimmed. A forged/unauthorized reply_to_id can't splice into (or be capped
by) someone else's thread (:132-137).

DB-free: `db` is a MagicMock whose .query(...) chains are stubbed per call,
in the exact order send_message issues them (sender lookup, recipient
lookup, optional reply_to_id lookup, optional thread-depth count).
MessageService._send_notification is stubbed out (AsyncMock) — the delivery
fan-out is NotificationService's concern, not this pin's.
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from fastapi import HTTPException

from src.services import message_service as ms
from src.services.message_service import MessageService, THREAD_LIMIT_EXCEEDED, THREAD_MESSAGE_CAP


@pytest.fixture(autouse=True)
def _stub_notification(monkeypatch):
    """Isolate the thread-cap logic from NotificationService's own DB reads —
    delivery fan-out is a separate concern (notification_service.py)."""
    monkeypatch.setattr(ms.MessageService, "_send_notification", AsyncMock())


def _first_mock(value):
    """A MagicMock .query(...) chain: filter().first() -> value."""
    q = MagicMock()
    q.filter.return_value.first.return_value = value
    return q


def _count_mock(value):
    """A MagicMock .query(...) chain: filter().count() -> value."""
    q = MagicMock()
    q.filter.return_value.count.return_value = value
    return q


def make_db(*query_results):
    """A MagicMock Session whose db.query(...) returns `query_results` in
    call order (send_message never queries the same model shape twice with
    ambiguous routing, so a flat ordered list is sufficient)."""
    db = MagicMock()
    db.query.side_effect = list(query_results)
    return db


# --------------------------------------------------------------------------- #
# (1) Explicit thread_id, already at the cap -> reject, nothing persisted
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_explicit_thread_at_cap_rejects_before_message_creation():
    sender_id, recipient_id, thread_id = uuid4(), uuid4(), uuid4()
    db = make_db(
        _first_mock(SimpleNamespace(id=sender_id)),      # sender lookup
        _first_mock(SimpleNamespace(id=recipient_id)),   # recipient lookup
        _count_mock(THREAD_MESSAGE_CAP),                  # thread depth check
    )
    with pytest.raises(HTTPException) as exc_info:
        await MessageService.send_message(
            db, sender_id=sender_id, recipient_id=recipient_id,
            content="hi", thread_id=thread_id,
        )
    assert exc_info.value.status_code == 409
    assert exc_info.value.detail == THREAD_LIMIT_EXCEEDED
    db.add.assert_not_called()


# --------------------------------------------------------------------------- #
# (2) Explicit thread_id, one below the cap -> send proceeds
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_explicit_thread_one_below_cap_allows_send():
    sender_id, recipient_id, thread_id = uuid4(), uuid4(), uuid4()
    db = make_db(
        _first_mock(SimpleNamespace(id=sender_id)),
        _first_mock(SimpleNamespace(id=recipient_id)),
        _count_mock(THREAD_MESSAGE_CAP - 1),
    )
    msg = await MessageService.send_message(
        db, sender_id=sender_id, recipient_id=recipient_id,
        content="hi", thread_id=thread_id,
    )
    db.add.assert_called_once_with(msg)
    assert msg.thread_id == thread_id


# --------------------------------------------------------------------------- #
# (3) Brand-new thread -> cap-exempt, the count query is never even issued
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_brand_new_thread_is_cap_exempt_and_skips_the_count_query():
    sender_id, recipient_id = uuid4(), uuid4()
    db = make_db(
        _first_mock(SimpleNamespace(id=sender_id)),
        _first_mock(SimpleNamespace(id=recipient_id)),
        # No 3rd mock: if the cap check regressed into firing here anyway,
        # the exhausted side_effect raises StopIteration and fails loudly.
    )
    msg = await MessageService.send_message(
        db, sender_id=sender_id, recipient_id=recipient_id, content="hi",
    )
    db.add.assert_called_once_with(msg)
    assert msg.thread_id is not None


# --------------------------------------------------------------------------- #
# (4) Authorized reply inherits the parent thread and IS capped
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_authorized_reply_inherits_thread_and_sends_under_cap():
    sender_id, original_sender_id, reply_to_id, parent_thread_id = uuid4(), uuid4(), uuid4(), uuid4()
    # sender_id is the RECIPIENT of the original message — a genuine reply
    # from the person who received it.
    original = SimpleNamespace(
        id=reply_to_id, sender_id=original_sender_id, recipient_id=sender_id,
        thread_id=parent_thread_id,
    )
    db = make_db(
        _first_mock(SimpleNamespace(id=sender_id)),           # sender lookup
        _first_mock(SimpleNamespace(id=original_sender_id)),  # recipient lookup
        _first_mock(original),                                 # reply_to_id lookup
        _count_mock(3),                                        # comfortably under cap
    )
    msg = await MessageService.send_message(
        db, sender_id=sender_id, recipient_id=original_sender_id, content="reply",
        reply_to_id=reply_to_id,
    )
    assert msg.thread_id == parent_thread_id
    assert msg.reply_to_id == reply_to_id
    db.add.assert_called_once_with(msg)


@pytest.mark.asyncio
async def test_authorized_reply_at_cap_is_still_rejected():
    sender_id, original_sender_id, reply_to_id, parent_thread_id = uuid4(), uuid4(), uuid4(), uuid4()
    original = SimpleNamespace(
        id=reply_to_id, sender_id=original_sender_id, recipient_id=sender_id,
        thread_id=parent_thread_id,
    )
    db = make_db(
        _first_mock(SimpleNamespace(id=sender_id)),
        _first_mock(SimpleNamespace(id=original_sender_id)),
        _first_mock(original),
        _count_mock(THREAD_MESSAGE_CAP),
    )
    with pytest.raises(HTTPException) as exc_info:
        await MessageService.send_message(
            db, sender_id=sender_id, recipient_id=original_sender_id, content="reply",
            reply_to_id=reply_to_id,
        )
    assert exc_info.value.status_code == 409
    assert exc_info.value.detail == THREAD_LIMIT_EXCEEDED
    db.add.assert_not_called()


# --------------------------------------------------------------------------- #
# (5) Forged reply_to_id -> link dropped, fresh thread, cap cannot be
#     weaponized against the (uninvolved) sender/recipient pair
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_forged_reply_drops_link_and_starts_a_cap_exempt_fresh_thread():
    sender_id, recipient_id = uuid4(), uuid4()
    reply_to_id, stranger_a, stranger_b = uuid4(), uuid4(), uuid4()
    original = SimpleNamespace(
        id=reply_to_id, sender_id=stranger_a, recipient_id=stranger_b,
        thread_id=uuid4(),
    )
    db = make_db(
        _first_mock(SimpleNamespace(id=sender_id)),
        _first_mock(SimpleNamespace(id=recipient_id)),
        _first_mock(original),
        # No count mock: proves the cap check never fires for a dropped link.
    )
    msg = await MessageService.send_message(
        db, sender_id=sender_id, recipient_id=recipient_id, content="hi",
        reply_to_id=reply_to_id,
    )
    assert msg.reply_to_id is None
    assert msg.thread_id != original.thread_id
    db.add.assert_called_once_with(msg)
