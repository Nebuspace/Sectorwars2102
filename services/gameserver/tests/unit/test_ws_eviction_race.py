"""Regression pin for WO-RT-EVICTION-SUPERSEDE and WO-RT-ADMIN-EVICTION.

Duplicate-connect eviction used to be racy: connect() overwrote
active_connections[user_id] with the new socket, but the OLD request
handler's `finally: connection_manager.disconnect(user_id)` ran later and
scrubbed WHICHEVER socket was registered at that point — i.e. the new one,
not the one it actually owned. Eviction also did a bare `.close()` with no
code/reason, so the client treated it as an auth failure and looped through
reconnectWithRefresh, evicting the new tab in turn.

Fixed by:
  - connect() closing the superseded socket with code=4001, reason="superseded".
  - disconnect(user_id, websocket=None): when a websocket is passed, it's a
    no-op unless that exact socket still owns the registration; legacy
    callers passing only user_id (internal send-failure prunes) are
    unaffected.

WO-RT-ADMIN-EVICTION mirrors the identical fix onto connect_admin() /
disconnect_admin(), which had the same race (connect_admin() only did a
bare `.close()`, and disconnect_admin() took no websocket identity check).

Fully DB-free and network-free: FakeWebSocket records accept()/close() calls
in place of real I/O. Uses a FRESH ConnectionManager() instance (not the
process-wide `connection_manager` singleton) so this test can assert on
room-registry internals without touching global state shared with other
test modules — a bare `ConnectionManager()` instantiation here does not
violate test_ws_singleton_wiring.py's guard, which only scans `src/`.
"""
from __future__ import annotations

from uuid import uuid4

import pytest

from src.services.websocket_service import ConnectionManager


class FakeWebSocket:
    """Minimal WebSocket stand-in: records accept()/close() instead of
    touching the network."""

    def __init__(self):
        self.accepted = False
        self.closed_with: tuple[int | None, str | None] | None = None

    async def accept(self) -> None:
        self.accepted = True

    async def close(self, code: int | None = None, reason: str | None = None) -> None:
        self.closed_with = (code, reason)


class FakeAdminWebSocket(FakeWebSocket):
    """FakeWebSocket + send_text.

    connect_admin() unconditionally pushes an initial connection_established
    stats frame to the admin's OWN socket right after registering it — unlike
    the player-side connect(), which only broadcasts to sector-mates
    (exclude_user=user_id skips the connecting user itself). Without
    send_text, that initial send would AttributeError inside
    send_admin_message's try/except, whose failure handler calls
    disconnect_admin(admin_id) with no websocket arg — unconditionally
    undoing the very registration this test is exercising."""

    def __init__(self):
        super().__init__()
        self.sent: list[str] = []

    async def send_text(self, data: str) -> None:
        self.sent.append(data)


@pytest.mark.asyncio
async def test_evicted_handlers_finally_does_not_scrub_the_successor():
    manager = ConnectionManager()
    user_id = str(uuid4())
    sector_id = 7
    team_id = str(uuid4())
    region_id = str(uuid4())

    user_data = {
        "username": "ratbone",
        "current_sector": sector_id,
        "team_id": team_id,
        "current_region_id": region_id,
    }

    socket_a = FakeWebSocket()
    socket_b = FakeWebSocket()

    # Tab 1 connects.
    await manager.connect(socket_a, user_id, user_data)
    assert manager.active_connections[user_id] is socket_a

    # Tab 2 connects as the same user — evicts A with 4001/superseded.
    await manager.connect(socket_b, user_id, user_data)
    assert manager.active_connections[user_id] is socket_b
    assert socket_a.closed_with == (4001, "superseded")

    # A's own request-handler loop unwinds AFTER the eviction race, and its
    # finally fires with A's own socket — must be a no-op now that B owns
    # the registration (the race this WO fixes).
    await manager.disconnect(user_id, socket_a)

    assert manager.active_connections[user_id] is socket_b
    assert user_id in manager.connection_metadata
    assert user_id in manager.sector_connections[sector_id]
    assert user_id in manager.team_connections[team_id]
    assert user_id in manager.region_connections[region_id]

    # B's own finally, later, correctly tears everything down.
    await manager.disconnect(user_id, socket_b)

    assert user_id not in manager.active_connections
    assert user_id not in manager.connection_metadata
    assert sector_id not in manager.sector_connections
    assert team_id not in manager.team_connections
    assert region_id not in manager.region_connections


@pytest.mark.asyncio
async def test_legacy_call_sites_without_a_websocket_arg_still_deregister():
    """Internal prune paths (e.g. send_personal_message's failure handler)
    only ever knew the user_id — disconnect(user_id) with no second arg must
    keep unconditionally deregistering, exactly as before this WO."""
    manager = ConnectionManager()
    user_id = str(uuid4())
    socket_a = FakeWebSocket()

    await manager.connect(socket_a, user_id, {"username": "legacy"})
    assert user_id in manager.active_connections

    await manager.disconnect(user_id)

    assert user_id not in manager.active_connections
    assert user_id not in manager.connection_metadata


@pytest.mark.asyncio
async def test_disconnect_with_the_currently_registered_socket_still_removes_it():
    """A websocket arg that DOES still own the registration (the common,
    non-raced case: single connect/disconnect for one user) must behave
    exactly like the legacy no-arg call."""
    manager = ConnectionManager()
    user_id = str(uuid4())
    socket_a = FakeWebSocket()

    await manager.connect(socket_a, user_id, {"username": "ratbone"})
    await manager.disconnect(user_id, socket_a)

    assert user_id not in manager.active_connections
    assert user_id not in manager.connection_metadata


@pytest.mark.asyncio
async def test_admin_evicted_handlers_finally_does_not_scrub_the_successor():
    """Mirrors test_evicted_handlers_finally_does_not_scrub_the_successor for
    the admin path (WO-RT-ADMIN-EVICTION)."""
    manager = ConnectionManager()
    admin_id = str(uuid4())
    admin_data = {"username": "ratbone-admin", "is_admin": True}

    socket_a = FakeAdminWebSocket()
    socket_b = FakeAdminWebSocket()

    # Tab 1 connects.
    await manager.connect_admin(socket_a, admin_id, admin_data)
    assert manager.admin_connections[admin_id] is socket_a

    # Tab 2 connects as the same admin — evicts A with 4001/superseded.
    await manager.connect_admin(socket_b, admin_id, admin_data)
    assert manager.admin_connections[admin_id] is socket_b
    assert socket_a.closed_with == (4001, "superseded")

    # A's own request-handler loop unwinds AFTER the eviction race, and its
    # finally fires with A's own socket — must be a no-op now that B owns
    # the registration (the race this WO fixes).
    await manager.disconnect_admin(admin_id, socket_a)

    assert manager.admin_connections[admin_id] is socket_b
    assert admin_id in manager.admin_metadata

    # B's own finally, later, correctly tears everything down.
    await manager.disconnect_admin(admin_id, socket_b)

    assert admin_id not in manager.admin_connections
    assert admin_id not in manager.admin_metadata


@pytest.mark.asyncio
async def test_admin_legacy_call_sites_without_a_websocket_arg_still_deregister():
    """Internal prune paths (send_admin_message's failure handler,
    broadcast_to_admins, cleanup_stale_connections) only ever knew the
    admin_id — disconnect_admin(admin_id) with no second arg must keep
    unconditionally deregistering, exactly as before this WO."""
    manager = ConnectionManager()
    admin_id = str(uuid4())
    socket_a = FakeAdminWebSocket()

    await manager.connect_admin(socket_a, admin_id, {"username": "legacy-admin"})
    assert admin_id in manager.admin_connections

    await manager.disconnect_admin(admin_id)

    assert admin_id not in manager.admin_connections
    assert admin_id not in manager.admin_metadata


@pytest.mark.asyncio
async def test_admin_disconnect_with_the_currently_registered_socket_still_removes_it():
    """A websocket arg that DOES still own the registration (the common,
    non-raced case: single connect/disconnect for one admin) must behave
    exactly like the legacy no-arg call."""
    manager = ConnectionManager()
    admin_id = str(uuid4())
    socket_a = FakeAdminWebSocket()

    await manager.connect_admin(socket_a, admin_id, {"username": "ratbone-admin"})
    await manager.disconnect_admin(admin_id, socket_a)

    assert admin_id not in manager.admin_connections
    assert admin_id not in manager.admin_metadata
