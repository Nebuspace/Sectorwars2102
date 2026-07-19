"""Regression pin for WO-NEON-NH7-SENDER-NICKNAME.

Pins two admin_messages.py behaviors against a MagicMock db (no real DB —
these are query-shape/payload pins, not data-integrity tests):

1. get_message_statistics's most_active_senders join (:141-155) emits BOTH
   player_id and nickname per entry, and the nickname resolves via LEFT join
   to Player/User (coalesce(Player.nickname, User.username) — the same
   nickname-or-username rule as Player.username) even for an orphaned
   sender_id with no matching players row (nickname None, player_id intact —
   the outer-join, not inner-join, is load-bearing).
2. get_all_messages (:38) eager-loads Message.sender via joinedload so
   to_dict's sender_name (message.py:88-90) is deterministic instead of
   depending on whatever happened to already be in the session's identity
   map.

DB-free: each db.query(...) call in the route returns a purpose-built
MagicMock chain stubbed only for the methods the route actually calls, in
call order (matches the sibling pin's style in
tests/unit/test_message_thread_cap.py).
"""
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from src.api.routes.admin_messages import get_all_messages, get_message_statistics
from src.models.message import Message


def _scalar_mock(value):
    """A MagicMock .query(...) chain: optional .filter() -> .scalar() -> value."""
    q = MagicMock()
    q.filter.return_value = q
    q.scalar.return_value = value
    return q


def _active_senders_mock(rows):
    """A MagicMock .query(...) chain ending in the active-senders .all()."""
    q = MagicMock()
    q.outerjoin.return_value = q
    q.group_by.return_value = q
    q.order_by.return_value = q
    q.limit.return_value = q
    q.all.return_value = rows
    return q


FAKE_ADMIN = SimpleNamespace(id=uuid4())


# --------------------------------------------------------------------------- #
# (1) most_active_senders emits {player_id, nickname, message_count} per
#     entry, nickname resolved via the Player/User outer join
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_active_senders_emits_player_id_and_nickname():
    sender_with_nickname = uuid4()
    sender_without_players_row = uuid4()

    db = MagicMock()
    db.query.side_effect = [
        _scalar_mock(42),   # total_messages
        _scalar_mock(3),    # messages_today
        _scalar_mock(10),   # messages_this_week
        _scalar_mock(1),    # flagged_messages
        _active_senders_mock([
            (sender_with_nickname, "Nova", 7),       # nickname-or-username resolved
            (sender_without_players_row, None, 2),   # orphaned sender_id: outer join keeps the row
        ]),
    ]

    result = await get_message_statistics(admin=FAKE_ADMIN, db=db)

    assert result["most_active_senders"] == [
        {"player_id": str(sender_with_nickname), "nickname": "Nova", "message_count": 7},
        {"player_id": str(sender_without_players_row), "nickname": None, "message_count": 2},
    ]
    for entry in result["most_active_senders"]:
        assert "player_id" in entry and "nickname" in entry  # backward-compat key kept


# --------------------------------------------------------------------------- #
# (2) get_all_messages eager-loads Message.sender via joinedload
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_get_all_messages_eager_loads_sender():
    q = MagicMock()
    q.options.return_value = q
    q.filter.return_value = q
    q.count.return_value = 0
    q.order_by.return_value = q
    q.limit.return_value = q
    q.offset.return_value = q
    q.all.return_value = []

    db = MagicMock()
    db.query.return_value = q

    with patch("src.api.routes.admin_messages.joinedload") as mock_joinedload:
        mock_joinedload.return_value = "SENTINEL_LOADER_OPTION"
        result = await get_all_messages(page=1, flagged=None, admin=FAKE_ADMIN, db=db)

    mock_joinedload.assert_called_once_with(Message.sender)
    q.options.assert_called_once_with("SENTINEL_LOADER_OPTION")
    assert result["messages"] == []
