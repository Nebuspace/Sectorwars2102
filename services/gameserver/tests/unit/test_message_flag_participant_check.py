"""Unit coverage for WO-FIX-MESSAGE-FLAG-NO-PARTICIPANT-CHECK.

Before this fix, ``MessageService.flag_message`` looked up a ``Message`` by
ID alone and flagged it -- broadcasting an admin alert containing the
message's content preview -- with no check that the calling player
(``flagged_by``) was actually the sender or recipient of that message. Any
authenticated player could flag (and read the preview of, via the resulting
admin broadcast metadata trail) an arbitrary private DM by guessing/
enumerating message IDs. Sibling functions correctly scope: ``mark_as_read``
filters on ``recipient_id``, ``delete_message`` calls ``is_visible_to``. This
file proves ``flag_message`` now does the same participant check.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.services.message_service import MessageService


def _first_mock(value):
    q = MagicMock()
    q.filter.return_value.first.return_value = value
    return q


def _make_db(*query_results):
    db = MagicMock()
    db.query.side_effect = list(query_results)
    return db


def _message(sender_id=None, recipient_id=None):
    """A real Message instance so the real ``is_visible_to`` executes."""
    from src.models.message import Message

    return Message(
        id=uuid.uuid4(),
        sender_id=sender_id or uuid.uuid4(),
        recipient_id=recipient_id or uuid.uuid4(),
        team_id=None,
        content="private DM content",
        deleted_by_sender=False,
        deleted_by_recipient=False,
        flagged=False,
        flagged_reason=None,
    )


@pytest.mark.asyncio
async def test_sender_can_flag_own_message():
    sender_id, recipient_id = uuid.uuid4(), uuid.uuid4()
    message = _message(sender_id=sender_id, recipient_id=recipient_id)
    db = _make_db(_first_mock(message), _first_mock(SimpleNamespace(username="sender")))

    with patch("src.services.message_service.manager") as manager:
        manager.broadcast_to_admins = AsyncMock()
        result = await MessageService.flag_message(
            db, message_id=message.id, reason="harassment", flagged_by=sender_id,
        )

    assert result is True
    assert message.flagged is True
    assert message.flagged_reason == "harassment"
    manager.broadcast_to_admins.assert_awaited_once()


@pytest.mark.asyncio
async def test_recipient_can_flag_received_message():
    sender_id, recipient_id = uuid.uuid4(), uuid.uuid4()
    message = _message(sender_id=sender_id, recipient_id=recipient_id)
    db = _make_db(_first_mock(message), _first_mock(SimpleNamespace(username="recipient")))

    with patch("src.services.message_service.manager") as manager:
        manager.broadcast_to_admins = AsyncMock()
        result = await MessageService.flag_message(
            db, message_id=message.id, reason="harassment", flagged_by=recipient_id,
        )

    assert result is True
    assert message.flagged is True
    manager.broadcast_to_admins.assert_awaited_once()


@pytest.mark.asyncio
async def test_non_participant_cannot_flag_message():
    sender_id, recipient_id, stranger_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    message = _message(sender_id=sender_id, recipient_id=recipient_id)
    db = _make_db(_first_mock(message))

    with patch("src.services.message_service.manager") as manager:
        manager.broadcast_to_admins = AsyncMock()
        result = await MessageService.flag_message(
            db, message_id=message.id, reason="harassment", flagged_by=stranger_id,
        )

    assert result is False
    # Not flagged, no commit, and critically no admin broadcast -- a
    # non-participant must never trigger the content-preview alert.
    assert message.flagged is False
    assert message.flagged_reason is None
    db.commit.assert_not_called()
    manager.broadcast_to_admins.assert_not_awaited()


@pytest.mark.asyncio
async def test_message_not_found_returns_false():
    db = _make_db(_first_mock(None))

    with patch("src.services.message_service.manager") as manager:
        manager.broadcast_to_admins = AsyncMock()
        result = await MessageService.flag_message(
            db, message_id=uuid.uuid4(), reason="harassment", flagged_by=uuid.uuid4(),
        )

    assert result is False
    manager.broadcast_to_admins.assert_not_awaited()
