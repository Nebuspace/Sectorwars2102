"""Regression pin for WO-RT-ROOM-HOP.

Before this WO, ConnectionManager.sector_connections / team_connections /
region_connections were only ever populated at connect() time and torn down
at disconnect() — nothing in between ever moved a user between rooms.
update_user_location() and update_user_region() existed on ConnectionManager
but had ZERO production call sites (dead on arrival), and no
update_user_team() existed at all. The practical effect: a player who moved
sectors kept receiving the OLD sector's broadcasts forever; a player kicked
from a team kept receiving (and could keep sending) team chat until they
happened to reconnect.

Fixed by:
  - movement_service._broadcast_sector_presence now delegates to
    connection_manager.update_user_location (which both corrects the
    sector-room registry AND emits the player_left_sector /
    player_entered_sector frames itself), instead of raw-broadcasting AND
    leaving the registry stale — the old code had exactly this dedupe bug
    coded around it structurally, this WO collapses it to one emission
    source. It also schedules update_user_region when a move crosses a
    region boundary.
  - player_combat.py's sector-retreat route and hangar_service.py's
    dock/undock/disembark/ride-along region writes now schedule
    update_user_region the same way.
  - ConnectionManager.update_user_team is new; team_service.py's
    create_team/join_team/remove_member/leave_team schedule it after their
    commit, so the ~:921 team-chat revalidation
    (`user_id in connection_manager.team_connections.get(team_id, set())`)
    reads a live registry instead of one frozen at connect time.

Sections (1)-(3) and the AST pins use a FRESH ConnectionManager() instance
(mirrors test_ws_eviction_race.py) so room-registry assertions never touch
process-wide state shared with other test modules. Sections (4)-(5) must
exercise the module-level handle_websocket_message() function, which
hardcodes the real singleton by name — those use the real
`websocket_service.connection_manager` behind an autouse fixture that scrubs
every registry it touches afterward (extends test_ws_singleton_wiring.py's
`_clean_registries` convention to the room dicts that convention doesn't
already cover).

Fully DB-free and network-free throughout.
"""
from __future__ import annotations

import ast
import asyncio
import inspect
import json
import textwrap
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from src.services.websocket_service import ConnectionManager


class FakeWebSocket:
    """Minimal WebSocket stand-in: records send_text() calls instead of
    touching the network (mirrors test_ws_eviction_race.py / test_ws_
    singleton_wiring.py's FakeWebSocket)."""

    def __init__(self):
        self.sent: list[str] = []

    async def accept(self) -> None:
        pass

    async def close(self, code=None, reason=None) -> None:
        pass

    async def send_text(self, data: str) -> None:
        self.sent.append(data)


def _frame_types(ws: FakeWebSocket) -> list[str]:
    return [json.loads(f)["type"] for f in ws.sent]


# --------------------------------------------------------------------------- #
# (1) + (2) — ConnectionManager.update_user_location: sector membership moves,
#     the old sector no longer reaches the mover, the new one does, and
#     exactly one player_left_sector + one player_entered_sector frame fires
#     (the dedupe pin, at the primitive level).
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_update_user_location_moves_sector_membership_and_emits_exactly_one_pair():
    manager = ConnectionManager()
    mover_id, old_bystander_id, new_bystander_id = str(uuid4()), str(uuid4()), str(uuid4())

    mover_ws, old_ws, new_ws = FakeWebSocket(), FakeWebSocket(), FakeWebSocket()

    await manager.connect(mover_ws, mover_id, {"username": "mover", "current_sector": 1})
    await manager.connect(old_ws, old_bystander_id, {"username": "old-bystander", "current_sector": 1})
    await manager.connect(new_ws, new_bystander_id, {"username": "new-bystander", "current_sector": 2})

    # connect() itself fires player_entered_sector to sector-mates — drain
    # that noise so only frames from the move itself are counted below.
    mover_ws.sent.clear()
    old_ws.sent.clear()
    new_ws.sent.clear()

    await manager.update_user_location(mover_id, 2)

    # (1) registry moved: gone from sector 1, present in sector 2.
    assert mover_id in manager.sector_connections[2]
    assert mover_id not in manager.sector_connections.get(1, set())
    assert manager.connection_metadata[mover_id]["current_sector"] == 2

    # (2) exactly one leave + one enter, each naming the mover, each to the
    # correct audience only.
    old_left = [f for f in old_ws.sent if json.loads(f)["type"] == "player_left_sector"]
    new_entered = [f for f in new_ws.sent if json.loads(f)["type"] == "player_entered_sector"]
    assert len(old_left) == 1
    assert json.loads(old_left[0])["user_id"] == mover_id
    assert len(new_entered) == 1
    assert json.loads(new_entered[0])["user_id"] == mover_id

    # Neither sector's audience gets the OTHER event, and the mover never
    # receives its own join/leave echo.
    assert "player_entered_sector" not in _frame_types(old_ws)
    assert "player_left_sector" not in _frame_types(new_ws)
    assert "player_left_sector" not in _frame_types(mover_ws)
    assert "player_entered_sector" not in _frame_types(mover_ws)


@pytest.mark.asyncio
async def test_moved_player_receives_new_sector_broadcasts_not_old():
    """(1) explicitly: broadcast_to_sector(new) reaches the mover post-hop,
    broadcast_to_sector(old) no longer does."""
    manager = ConnectionManager()
    mover_id = str(uuid4())
    mover_ws = FakeWebSocket()
    await manager.connect(mover_ws, mover_id, {"username": "mover", "current_sector": 1})

    await manager.update_user_location(mover_id, 2)
    mover_ws.sent.clear()

    await manager.broadcast_to_sector(2, {"type": "probe_new"})
    assert "probe_new" in _frame_types(mover_ws)

    await manager.broadcast_to_sector(1, {"type": "probe_old"})
    assert "probe_old" not in _frame_types(mover_ws)


# --------------------------------------------------------------------------- #
# (3) — ConnectionManager.update_user_region: cross-region move hops
#     region_connections; a region broadcast reaches the mover post-hop.
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_update_user_region_hops_region_connections_and_broadcast_reaches_mover():
    manager = ConnectionManager()
    mover_id = str(uuid4())
    old_region, new_region = str(uuid4()), str(uuid4())

    mover_ws = FakeWebSocket()
    await manager.connect(mover_ws, mover_id, {
        "username": "mover", "current_sector": 1, "current_region_id": old_region,
    })
    assert mover_id in manager.region_connections[old_region]

    await manager.update_user_region(mover_id, new_region)

    assert mover_id not in manager.region_connections.get(old_region, set())
    assert mover_id in manager.region_connections[new_region]
    assert manager.connection_metadata[mover_id]["current_region"] == new_region

    mover_ws.sent.clear()
    await manager.broadcast_to_region(new_region, {"type": "governance_probe"})
    assert "governance_probe" in _frame_types(mover_ws)

    mover_ws.sent.clear()
    await manager.broadcast_to_region(old_region, {"type": "stale_probe"})
    assert "stale_probe" not in _frame_types(mover_ws)


@pytest.mark.asyncio
async def test_update_user_region_is_a_noop_for_an_unchanged_region():
    manager = ConnectionManager()
    mover_id = str(uuid4())
    region = str(uuid4())
    mover_ws = FakeWebSocket()
    await manager.connect(mover_ws, mover_id, {"username": "mover", "current_region_id": region})

    await manager.update_user_region(mover_id, region)

    assert manager.region_connections[region] == {mover_id}


# --------------------------------------------------------------------------- #
# movement_service._broadcast_sector_presence: proves the CALLER wires the
# primitives correctly (argument correctness via a mocked connection_manager,
# plus an AST pin that it can never regress back to a raw dual-emission
# broadcast). Complements the primitive-level tests above.
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_broadcast_sector_presence_schedules_update_user_location_and_region():
    from src.services.movement_service import MovementService

    fake_cm = MagicMock()
    fake_cm.update_user_location = AsyncMock()
    fake_cm.update_user_region = AsyncMock()

    mover_id = str(uuid4())
    old_region, new_region = uuid4(), uuid4()

    with patch("src.services.websocket_service.connection_manager", fake_cm):
        svc = MovementService(db=None)
        svc._broadcast_sector_presence(
            1, 2, mover_id, old_region_id=old_region, new_region_id=new_region,
        )
        await asyncio.sleep(0)  # let the scheduled create_task run

    fake_cm.update_user_location.assert_awaited_once_with(mover_id, 2)
    fake_cm.update_user_region.assert_awaited_once_with(mover_id, str(new_region))


@pytest.mark.asyncio
async def test_broadcast_sector_presence_skips_region_hop_when_region_unchanged():
    from src.services.movement_service import MovementService

    fake_cm = MagicMock()
    fake_cm.update_user_location = AsyncMock()
    fake_cm.update_user_region = AsyncMock()

    mover_id = str(uuid4())
    same_region = uuid4()

    with patch("src.services.websocket_service.connection_manager", fake_cm):
        svc = MovementService(db=None)
        svc._broadcast_sector_presence(
            1, 2, mover_id, old_region_id=same_region, new_region_id=same_region,
        )
        await asyncio.sleep(0)

    fake_cm.update_user_location.assert_awaited_once_with(mover_id, 2)
    fake_cm.update_user_region.assert_not_awaited()


def test_broadcast_sector_presence_never_raw_broadcasts_to_sector():
    """AST pin: exactly one update_user_location call, at most one
    update_user_region call, and NO broadcast_to_sector call anywhere in the
    method body — the dedupe fix must never regress back to two emission
    sources. Scans only the function body (skips the docstring node) so this
    can't self-defeat on the docstring's own prose describing the change."""
    from src.services.movement_service import MovementService

    source = textwrap.dedent(inspect.getsource(MovementService._broadcast_sector_presence))
    tree = ast.parse(source)
    func_node = tree.body[0]
    assert isinstance(func_node, ast.FunctionDef)

    body = func_node.body
    if body and isinstance(body[0], ast.Expr) and isinstance(getattr(body[0], "value", None), ast.Constant):
        body = body[1:]

    call_attrs = [
        node.func.attr
        for node in ast.walk(ast.Module(body=body, type_ignores=[]))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    ]

    assert call_attrs.count("update_user_location") == 1, call_attrs
    assert call_attrs.count("update_user_region") == 1, call_attrs
    assert "broadcast_to_sector" not in call_attrs, call_attrs


# --------------------------------------------------------------------------- #
# (4) + (5) — team_connections stays live: a kick immediately blocks team
# chat both ways; a join immediately enables it, no reconnect required. Must
# exercise handle_websocket_message(), which hardcodes the real singleton —
# scrub every registry it touches afterward.
# --------------------------------------------------------------------------- #

@pytest.fixture(autouse=True)
def _clean_singleton_registries():
    from src.services.websocket_service import connection_manager
    yield
    connection_manager.active_connections.clear()
    connection_manager.connection_metadata.clear()
    connection_manager.sector_connections.clear()
    connection_manager.team_connections.clear()
    connection_manager.region_connections.clear()
    connection_manager.topic_subscriptions.clear()
    connection_manager.admin_connections.clear()
    connection_manager.admin_metadata.clear()


@pytest.mark.asyncio
async def test_kicked_member_stops_sending_and_receiving_team_chat():
    from src.services.websocket_service import connection_manager, handle_websocket_message

    kicked_id, peer_id, team_id = str(uuid4()), str(uuid4()), str(uuid4())
    kicked_ws, peer_ws = FakeWebSocket(), FakeWebSocket()

    await connection_manager.connect(kicked_ws, kicked_id, {"username": "kicked", "team_id": team_id})
    await connection_manager.connect(peer_ws, peer_id, {"username": "peer", "team_id": team_id})
    assert kicked_id in connection_manager.team_connections[team_id]

    # Simulate remove_member's post-commit room-hop.
    await connection_manager.update_user_team(kicked_id, None)
    assert kicked_id not in connection_manager.team_connections.get(team_id, set())

    kicked_ws.sent.clear()
    peer_ws.sent.clear()

    # The kicked player's own team-chat send never reaches the peer — the
    # ~:921 gate reads a live registry (and this WO clears the sender's own
    # metadata["team_id"] in lockstep, so the branch doesn't even reach the
    # explicit "no longer a member" reply — it silently no-ops, which is the
    # meaningful outcome: no delivery either way).
    await handle_websocket_message(kicked_id, {
        "type": "chat_message", "target_type": "team", "content": "still here?",
    })
    assert peer_ws.sent == []

    # A team-chat message from the peer no longer reaches the kicked player.
    await handle_websocket_message(peer_id, {
        "type": "chat_message", "target_type": "team", "content": "bye!",
    })
    assert kicked_ws.sent == []


@pytest.mark.asyncio
async def test_joined_member_receives_team_chat_without_reconnect():
    from src.services.websocket_service import connection_manager, handle_websocket_message

    joiner_id, peer_id, team_id = str(uuid4()), str(uuid4()), str(uuid4())
    joiner_ws, peer_ws = FakeWebSocket(), FakeWebSocket()

    # joiner connects with NO team — mirrors join_team: they weren't on a
    # team at connect time, and per this WO must not need to reconnect after
    # joining one.
    await connection_manager.connect(joiner_ws, joiner_id, {"username": "joiner"})
    await connection_manager.connect(peer_ws, peer_id, {"username": "peer", "team_id": team_id})
    assert joiner_id not in connection_manager.team_connections.get(team_id, set())

    # Simulate join_team's post-commit room-hop.
    await connection_manager.update_user_team(joiner_id, team_id)
    assert joiner_id in connection_manager.team_connections[team_id]

    joiner_ws.sent.clear()
    peer_ws.sent.clear()

    await handle_websocket_message(peer_id, {
        "type": "chat_message", "target_type": "team", "content": "welcome!",
    })

    assert len(joiner_ws.sent) == 1
    frame = json.loads(joiner_ws.sent[0])
    assert frame["type"] == "chat_message"
    assert frame["content"] == "welcome!"


# --------------------------------------------------------------------------- #
# (6) — production call-site pins: update_user_location / update_user_region
# / update_user_team each now have at least one real caller outside their own
# definition site (they shipped with ZERO before this WO).
# --------------------------------------------------------------------------- #

def _production_call_sites(attr_name: str) -> list[str]:
    src_root = Path(__file__).resolve().parents[2] / "src"
    hits = []
    for path in src_root.rglob("*.py"):
        if path.name == "websocket_service.py":
            continue  # the definition site, not a caller
        text = path.read_text(encoding="utf-8")
        if f"{attr_name}(" not in text:
            continue
        tree = ast.parse(text, filename=str(path))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == attr_name
            ):
                hits.append(f"{path.relative_to(src_root)}:{node.lineno}")
    return hits


def test_update_user_location_has_a_production_call_site():
    hits = _production_call_sites("update_user_location")
    assert hits, "update_user_location has zero call sites outside websocket_service.py"
    assert any("movement_service.py" in h for h in hits), hits


def test_update_user_region_has_production_call_sites_in_every_lane():
    hits = _production_call_sites("update_user_region")
    assert hits, "update_user_region has zero call sites outside websocket_service.py"
    joined = "\n".join(hits)
    assert "movement_service.py" in joined, hits
    assert "hangar_service.py" in joined, hits
    assert "player_combat.py" in joined, hits


def test_update_user_team_has_production_call_sites():
    hits = _production_call_sites("update_user_team")
    assert hits, "update_user_team has zero call sites outside websocket_service.py"
    assert any("team_service.py" in h for h in hits), hits
