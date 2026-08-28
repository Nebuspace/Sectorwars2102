"""LEG-2689 — flagged admin list exposes sender_block_count_30d."""

from __future__ import annotations

import uuid
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from src.api.routes.admin_messages import _list_admin_messages
from src.models.audit_log import AuditLog
from src.models.message import Message
from src.services.message_service import MessageService


def _msg(**overrides):
    msg_id = overrides.pop("id", uuid.uuid4())
    sender_id = overrides.pop("sender_id", uuid.uuid4())
    base = SimpleNamespace(
        id=msg_id,
        sender_id=sender_id,
        recipient_id=uuid.uuid4(),
        subject="hi",
        content="spam",
        sent_at=datetime.utcnow(),
        read_at=None,
        message_type="direct",
        priority="normal",
        thread_id=None,
        reply_to_id=None,
        flagged=True,
        moderation_status=None,
        sender=None,
    )
    for key, value in overrides.items():
        setattr(base, key, value)

    def to_dict(include_content=True):
        return {
            "id": str(base.id),
            "sender_id": str(base.sender_id),
            "flagged": base.flagged,
            "content": base.content if include_content else None,
        }

    base.to_dict = to_dict
    return base


def _audit_row(sender_id: uuid.UUID, moderation_action: str) -> SimpleNamespace:
    return SimpleNamespace(
        request_body={
            "moderation_action": moderation_action,
            "sender_id": str(sender_id),
        }
    )


def _mock_flagged_db(flagged_message, audit_rows):
    messages_query = MagicMock()
    messages_query.options.return_value = messages_query
    messages_query.filter.return_value = messages_query
    messages_query.count.return_value = 1
    messages_query.order_by.return_value = messages_query
    messages_query.limit.return_value = messages_query
    messages_query.offset.return_value = messages_query
    messages_query.all.return_value = [flagged_message]

    audit_query = MagicMock()
    audit_query.filter.return_value = audit_query
    audit_query.all.return_value = audit_rows

    db = MagicMock()
    db.query.side_effect = lambda model: (
        messages_query if model is Message else audit_query
    )
    return db


@pytest.mark.asyncio
async def test_flagged_list_includes_sender_block_count_30d():
    sender_id = uuid.uuid4()
    flagged = _msg(sender_id=sender_id)
    other_sender = uuid.uuid4()
    audit_rows = [
        _audit_row(sender_id, "block"),
        _audit_row(sender_id, "block"),
        _audit_row(other_sender, "block"),
    ]
    db = _mock_flagged_db(flagged, audit_rows)

    result = await _list_admin_messages(page=1, flagged=True, db=db)

    assert len(result["messages"]) == 1
    row = result["messages"][0]
    assert row["sender_block_count_30d"] == 2
    assert row["sender_escalation_logged"] is False


@pytest.mark.asyncio
async def test_flagged_list_marks_prior_escalation_audit():
    sender_id = uuid.uuid4()
    flagged = _msg(sender_id=sender_id)
    audit_rows = [
        _audit_row(sender_id, "block"),
        _audit_row(sender_id, "block"),
        _audit_row(sender_id, "block_escalation_threshold"),
    ]
    db = _mock_flagged_db(flagged, audit_rows)

    result = await _list_admin_messages(page=1, flagged=True, db=db)

    row = result["messages"][0]
    assert row["sender_block_count_30d"] == 2
    assert row["sender_escalation_logged"] is True


def test_batch_sender_block_stats_30d_empty_sender_list():
    db = MagicMock()
    assert MessageService.batch_sender_block_stats_30d(db, []) == {}
    db.query.assert_not_called()
