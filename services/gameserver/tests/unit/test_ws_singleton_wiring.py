"""Regression pin for WO-RT-SINGLETON-WIRE.

message_service.py and faction_service.py each used to instantiate a private
`manager = ConnectionManager()` at import time — dead on arrival, since every
real client registers on the ONE live registry at
`websocket_service.connection_manager` (:840, wired via api/routes/
websocket.py's connect()/connect_admin()). That meant `new_message` priority
delivery, `reputation_changed`, the flagged-message admin alert, and
`faction_territory_changed` never reached any connected socket.
faction_service.py additionally called a nonexistent `manager.broadcast(...)`
(only broadcast_to_sector/to_team/to_region/global/to_admins exist), which
raised AttributeError on every admin territory PUT — AFTER the commit had
already landed.

Fully DB-free and socket-free: FakeWebSocket collects frames instead of
touching the network, registered directly into the real singleton's
active_connections / admin_connections dicts (the connect()/connect_admin()
handshake itself is out of scope for this pin — only the wiring is).
"""
from __future__ import annotations

import ast
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from src.services import message_service, faction_service
from src.services.websocket_service import connection_manager


class FakeWebSocket:
    """Minimal WebSocket stand-in: records every frame sent to it."""

    def __init__(self):
        self.sent: list[str] = []

    async def send_text(self, data: str) -> None:
        self.sent.append(data)


@pytest.fixture(autouse=True)
def _clean_registries():
    """Each test registers its own fake sockets on the real, process-wide
    singleton — scrub them afterward so nothing leaks across tests."""
    yield
    connection_manager.active_connections.clear()
    connection_manager.admin_connections.clear()


def _first_mock(value):
    """A MagicMock .query(...) chain: filter().first() -> value."""
    q = MagicMock()
    q.filter.return_value.first.return_value = value
    return q


def make_db(*query_results):
    """A MagicMock Session whose db.query(...) returns `query_results` in
    call order."""
    db = MagicMock()
    db.query.side_effect = list(query_results)
    return db


# --------------------------------------------------------------------------- #
# (1) Both services' module-level `manager` IS the real singleton
# --------------------------------------------------------------------------- #

def test_message_service_manager_is_the_real_singleton():
    assert message_service.manager is connection_manager


def test_faction_service_manager_is_the_real_singleton():
    assert faction_service.manager is connection_manager


# --------------------------------------------------------------------------- #
# (2) Source-scan: exactly ONE `ConnectionManager()` instantiation in
#     gameserver src — guards the whole class of bug, not just these two
#     files. AST-based (not a text grep) so a docstring/comment that merely
#     mentions the literal can't produce a false positive.
# --------------------------------------------------------------------------- #

def test_exactly_one_connectionmanager_instantiation_in_src():
    src_root = Path(__file__).resolve().parents[2] / "src"
    hits = []
    for path in src_root.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if "ConnectionManager(" not in text:
            continue
        tree = ast.parse(text, filename=str(path))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "ConnectionManager"
            ):
                hits.append(f"{path.relative_to(src_root)}:{node.lineno}")

    assert len(hits) == 1, f"expected exactly one ConnectionManager() instantiation, found: {hits}"
    assert hits[0].startswith("services/websocket_service.py:"), hits


# --------------------------------------------------------------------------- #
# (3) A recipient registered on the real singleton receives a `new_message`
#     frame through MessageService.send_message (the full public API, not
#     just the manager reference).
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_send_message_delivers_new_message_frame_to_registered_recipient():
    sender_id, recipient_id, recipient_user_id = uuid4(), uuid4(), uuid4()

    sender_obj = SimpleNamespace(id=sender_id, nickname="Ava", user=None)
    recipient_for_validation = SimpleNamespace(id=recipient_id)
    recipient_for_notify = SimpleNamespace(id=recipient_id, user_id=recipient_user_id)

    db = make_db(
        _first_mock(sender_obj),               # sender lookup (send_message)
        _first_mock(recipient_for_validation),  # recipient lookup (send_message)
        _first_mock(recipient_for_notify),      # recipient lookup (notify_new_message)
    )

    fake_socket = FakeWebSocket()
    connection_manager.active_connections[str(recipient_user_id)] = fake_socket

    await message_service.MessageService.send_message(
        db, sender_id=sender_id, recipient_id=recipient_id, content="hi there",
    )

    assert len(fake_socket.sent) == 1
    frame = json.loads(fake_socket.sent[0])
    assert frame["type"] == "new_message"
    assert frame["priority"] == "normal"


# --------------------------------------------------------------------------- #
# (4) update_faction_territory completes without AttributeError and a fake
#     player socket (broadcast_global has no scoped "territory" audience —
#     it fans out to everyone connected) receives faction_territory_changed.
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_update_faction_territory_broadcasts_without_attributeerror():
    faction_id, sector_a, sector_b = uuid4(), uuid4(), uuid4()
    faction_obj = SimpleNamespace(id=faction_id, name="Terran Federation", territory_sectors=[])

    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = faction_obj

    player_user_id = uuid4()
    fake_socket = FakeWebSocket()
    connection_manager.active_connections[str(player_user_id)] = fake_socket

    service = faction_service.FactionService(db)
    result = await service.update_faction_territory(faction_id, [sector_a, sector_b])

    assert result is faction_obj
    assert len(fake_socket.sent) == 1
    frame = json.loads(fake_socket.sent[0])
    assert frame["type"] == "faction_territory_changed"
    assert frame["faction_id"] == str(faction_id)
    assert set(frame["sectors"]) == {str(sector_a), str(sector_b)}


# --------------------------------------------------------------------------- #
# (5) Flagging a message delivers `flagged_message_alert` to a fake admin
#     socket registered on admin_connections (not active_connections).
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_flag_message_broadcasts_to_admins():
    message_id, flagged_by = uuid4(), uuid4()
    message_obj = SimpleNamespace(
        id=message_id, content="spam spam spam", sender_id=uuid4(),
        flagged=False, flagged_reason=None,
    )
    flagging_player_obj = SimpleNamespace(username="ratbone")

    db = make_db(
        _first_mock(message_obj),          # message lookup
        _first_mock(flagging_player_obj),  # flagging_player lookup
    )

    admin_user_id = uuid4()
    admin_socket = FakeWebSocket()
    connection_manager.admin_connections[str(admin_user_id)] = admin_socket

    result = await message_service.MessageService.flag_message(
        db, message_id=message_id, reason="spam", flagged_by=flagged_by,
    )

    assert result is True
    assert message_obj.flagged is True
    assert len(admin_socket.sent) == 1
    frame = json.loads(admin_socket.sent[0])
    assert frame["type"] == "flagged_message_alert"
    assert frame["message_id"] == str(message_id)
    assert frame["flagged_by_name"] == "ratbone"
