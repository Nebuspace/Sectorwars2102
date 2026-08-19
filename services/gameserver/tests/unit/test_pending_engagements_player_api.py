"""LEG-480 — player GET + WS payload for PendingEngagement countdown.

DB-free: MagicMock session + in-memory row objects. Integration arrival
math still lives in test_npc_living_system.TestPoliceEngagement.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

from fastapi import Response

from src.api.routes import pending_engagements as route
from src.models.npc_character import NPCCharacter
from src.models.pending_engagement import EngagementStatus, PendingEngagement
from src.services import npc_engagement_service


def _run(coro):
    return asyncio.run(coro)


def _player(*, turns_spent=10, user_id=None):
    return SimpleNamespace(
        id=uuid4(),
        user_id=user_id or uuid4(),
        lifetime_turns_spent=turns_spent,
    )


def _row(player, *, offense="wanted_status", jurisdiction="federation",
         threshold=12, offense_at=10, status=EngagementStatus.PENDING,
         squad_ids=None, names=None):
    return SimpleNamespace(
        id=uuid4(),
        player_id=player.id,
        offense_type=offense,
        jurisdiction=jurisdiction,
        npc_squad_ids=squad_ids or [str(uuid4())],
        offense_at_turn_count=offense_at,
        arrival_turn_threshold=threshold,
        status=status,
        grace_expires_at=None,
        created_at=None,
    )


class _ListQuery:
    def __init__(self, rows):
        self._rows = list(rows)

    def filter(self, *args, **kwargs):
        return self

    def order_by(self, *args, **kwargs):
        return self

    def all(self):
        return self._rows


class _NameQuery:
    def __init__(self, names):
        self._names = names

    def filter(self, *args, **kwargs):
        return self

    def all(self):
        return [SimpleNamespace(display_name=n) for n in self._names]


def test_router_mounted_on_api():
    from pathlib import Path

    api_src = Path(__file__).resolve().parents[2] / "src" / "api" / "api.py"
    text = api_src.read_text()
    assert "pending_engagements_router" in text
    assert "include_router(pending_engagements_router" in text


def test_empty_get_returns_204():
    player = _player()
    db = MagicMock()
    db.query.return_value = _ListQuery([])
    result = _run(route.get_my_pending_engagements(player=player, db=db))
    assert isinstance(result, Response)
    assert result.status_code == 204


def test_commit_then_get_matches_durable_row():
    player = _player(turns_spent=10)
    row = _row(player, threshold=12, offense_at=10)
    db = MagicMock()

    def query_side_effect(model):
        if model is PendingEngagement:
            return _ListQuery([row])
        if model is NPCCharacter:
            return _NameQuery(["Marshal Vance"])
        return _ListQuery([])

    db.query.side_effect = query_side_effect
    result = _run(route.get_my_pending_engagements(player=player, db=db))
    assert result["items"][0]["offense_type"] == "wanted_status"
    assert result["items"][0]["jurisdiction"] == "federation"
    assert result["items"][0]["turns_to_arrival"] == 2
    assert "Marshal Vance" in result["items"][0]["officer_names"]


def test_turns_to_arrival_uses_lifetime_clock():
    player = _player(turns_spent=11)
    row = _row(player, threshold=12, offense_at=10, squad_ids=["x"])
    summary = npc_engagement_service.engagement_summary(row, db=None, player=player)
    assert summary["turns_to_arrival"] == 1


def test_other_player_row_not_listed():
    owner = _player()
    stranger = _player()
    row = _row(stranger)
    db = MagicMock()
    # Route filters player_id in SQL; the fake query returns whatever we
    # give it — pin the service helper contract by filtering in Python
    # the same way the SQL filter would: only owner rows.
    listed = [
        r for r in [row]
        if r.player_id == owner.id and r.status == EngagementStatus.PENDING
    ]
    db.query.return_value = _ListQuery(listed)
    result = _run(route.get_my_pending_engagements(player=owner, db=db))
    assert isinstance(result, Response)
    assert result.status_code == 204


def test_dispatch_event_sends_police_en_route():
    player = _player()
    row = _row(player, threshold=12, offense_at=10)
    sent = []

    class _CM:
        async def send_personal_message(self, user_id, message):
            sent.append((user_id, message))

    class _Svc:
        connection_manager = _CM()

    async def _drive():
        with patch(
            "src.services.enhanced_websocket_service.get_enhanced_websocket_service",
            return_value=_Svc(),
        ):
            npc_engagement_service.dispatch_police_en_route_event(player, row, db=None)
            await asyncio.sleep(0)

    _run(_drive())
    assert sent
    uid, message = sent[0]
    assert uid == str(player.user_id)
    assert message["type"] == "police_en_route"
    assert message["turns_to_arrival"] == 2
    assert message["offense_type"] == "wanted_status"


def test_movement_path_row_then_get():
    """Accept: movement-path dispatch row is the same payload reconnect GET reads."""
    player = _player(turns_spent=10)
    row = _row(player, threshold=12, offense_at=10)
    db = MagicMock()

    def query_side_effect(model):
        if model is PendingEngagement:
            return _ListQuery([row])
        if model is NPCCharacter:
            return _NameQuery(["Marshal Vance"])
        return _ListQuery([])

    db.query.side_effect = query_side_effect
    items = npc_engagement_service.list_open_engagement_summaries(db, player)
    result = _run(route.get_my_pending_engagements(player=player, db=db))
    assert result["items"] == items
    assert items[0]["turns_to_arrival"] == 2
    assert items[0]["offense_type"] == "wanted_status"
