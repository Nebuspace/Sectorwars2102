"""LEG-263 — accept / redact / block moderation actions (messaging.md)."""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from src.api.routes import admin_moderation_messages as mod_routes
from src.services.message_service import MessageService


def _msg(**overrides):
    base = SimpleNamespace(
        id=uuid.uuid4(),
        sender_id=uuid.uuid4(),
        recipient_id=uuid.uuid4(),
        content="nasty text",
        flagged=True,
        flagged_reason="harassment",
        moderated_at=None,
        moderated_by=None,
        moderation_status=None,
    )
    for k, v in overrides.items():
        setattr(base, k, v)
    return base


def test_canon_constants_match_messaging_md():
    assert MessageService.REDACT_BODY == "[Moderated]"
    assert MessageService.REDACT_REP_DELTA == -50
    assert MessageService.BLOCK_REP_DELTA == -100
    assert MessageService.BLOCK_ESCALATION_THRESHOLD == 2
    assert MessageService.BLOCK_ESCALATION_WINDOW_DAYS == 30
    assert "moderated for rule violation" in MessageService.REDACT_NOTIFY
    assert "account restriction" in MessageService.BLOCK_NOTIFY


def test_accept_clears_flag_no_rep():
    msg = _msg()
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = msg

    with patch.object(MessageService, "_apply_sender_rep") as rep:
        with patch.object(MessageService, "_notify_sender_moderation") as notify:
            result = asyncio.run(
                MessageService.moderation_canon_action(
                    db, msg.id, "accept", uuid.uuid4()
                )
            )

    assert result["success"] is True
    assert msg.flagged is False
    assert msg.flagged_reason is None
    assert msg.content == "nasty text"
    assert msg.moderation_status is None
    assert result["rep_delta"] == 0
    rep.assert_not_called()
    notify.assert_not_called()
    db.commit.assert_called_once()


def test_redact_replaces_body_and_penalizes():
    msg = _msg()
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = msg
    moderator = uuid.uuid4()

    with patch.object(MessageService, "_apply_sender_rep") as rep:
        with patch.object(
            MessageService, "_notify_sender_moderation", return_value=True
        ):
            result = asyncio.run(
                MessageService.moderation_canon_action(
                    db, msg.id, "redact", moderator
                )
            )

    assert result["success"] is True
    assert msg.content == "[Moderated]"
    assert result["rep_delta"] == -50
    assert result["sender_notified"] is True
    rep.assert_called_once()
    assert rep.call_args[0][2] == -50


def test_block_hides_and_penalizes():
    msg = _msg()
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = msg

    with patch.object(MessageService, "_apply_sender_rep") as rep:
        with patch.object(
            MessageService, "_notify_sender_moderation", return_value=True
        ):
            with patch.object(
                MessageService,
                "_record_block_and_maybe_escalate",
                return_value=(1, False),
            ) as esc:
                result = asyncio.run(
                    MessageService.moderation_canon_action(
                        db, msg.id, "block", uuid.uuid4()
                    )
                )

    assert result["success"] is True
    assert msg.moderation_status == "blocked"
    assert result["rep_delta"] == -100
    assert result["block_count_30d"] == 1
    assert result["escalation_audit_logged"] is False
    rep.assert_called_once()
    assert rep.call_args[0][2] == -100
    esc.assert_called_once()


def test_block_escalation_at_two_in_window():
    msg = _msg()
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = msg

    with patch.object(MessageService, "_apply_sender_rep"):
        with patch.object(
            MessageService, "_notify_sender_moderation", return_value=True
        ):
            with patch.object(
                MessageService,
                "_record_block_and_maybe_escalate",
                return_value=(2, True),
            ):
                result = asyncio.run(
                    MessageService.moderation_canon_action(
                        db, msg.id, "block", uuid.uuid4()
                    )
                )

    assert result["escalation_audit_logged"] is True
    assert result["block_count_30d"] == 2


def test_record_block_counts_and_escalates():
    moderator = uuid.uuid4()
    sender = uuid.uuid4()
    mid = uuid.uuid4()
    db = MagicMock()

    prior = SimpleNamespace(
        request_body={
            "moderation_action": "block",
            "sender_id": str(sender),
        },
        timestamp=datetime.utcnow() - timedelta(days=1),
    )
    # After log_action adds current block, query returns prior+current
    current = SimpleNamespace(
        request_body={
            "moderation_action": "block",
            "sender_id": str(sender),
        },
        timestamp=datetime.utcnow(),
    )
    q = MagicMock()
    q.filter.return_value = q
    q.all.return_value = [prior, current]
    db.query.return_value = q

    with patch("src.services.audit_service.AuditService.log_action") as log:
        # Bind real method that calls log_action on instance — patch instance path
        pass

    from src.services.audit_service import AuditService

    calls = []

    def _log(self, **kwargs):
        calls.append(kwargs)
        return None

    with patch.object(AuditService, "log_action", _log):
        count, escalated = MessageService._record_block_and_maybe_escalate(
            db,
            moderator_id=moderator,
            sender_id=sender,
            message_id=mid,
            reason="spam",
        )

    assert count == 2
    assert escalated is True
    assert len(calls) == 2  # block + escalation
    assert calls[1]["details"]["moderation_action"] == "block_escalation_threshold"


def test_message_not_found():
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = None
    result = asyncio.run(
        MessageService.moderation_canon_action(
            db, uuid.uuid4(), "accept", uuid.uuid4()
        )
    )
    assert result["success"] is False
    assert result["reason"] == "message_not_found"


def test_routes_registered():
    paths = {
        getattr(r, "path", None)
        for r in mod_routes.router.routes
        if "POST" in (getattr(r, "methods", None) or set())
    }
    assert "/admin/moderation/messages/{message_id}/accept" in paths
    assert "/admin/moderation/messages/{message_id}/redact" in paths
    assert "/admin/moderation/messages/{message_id}/block" in paths


def test_delete_flag_unflag_still_on_admin_messages():
    from src.api.routes import admin_messages as am

    # Existing moderate path untouched
    assert any(
        getattr(r, "path", None) == "/admin/messages/{message_id}/moderate"
        for r in am.router.routes
    )
