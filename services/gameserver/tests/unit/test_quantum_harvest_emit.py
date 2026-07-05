"""WO-SB-QH2 Lane B — post-commit quantum-harvest WebSocket emit.

Canon Resolution step 6 (sw2102-docs quantum-resources.md § Resolution):
"Emit a real-time event on the WebSocket bus so the client UI updates
without polling." Covers the route-level wiring in POST /quantum/harvest
(quantum.py): a successful, COMMITTED harvest sends exactly one
'quantum_harvest' personal WS frame carrying sector_id/nebula_type/
shards/crit; a rejected (QuantumError, rolled back) harvest sends zero; and
a dead socket never turns an already-committed harvest into a 500.

Pure Python + monkeypatched quantum_service.harvest_nebula and
connection_manager.send_personal_message — no DB, no live socket.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

import src.api.routes.quantum as quantum_route
from src.services import websocket_service
from src.services.quantum_service import QuantumError


def _fake_player() -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        current_sector_id=42,
    )


class _FakeSession:
    """Stands in for the SQLAlchemy Session the route commits/rolls back —
    the route owns the commit, so the test only needs to observe which of
    the two was called."""

    def __init__(self) -> None:
        self.committed = False
        self.rolled_back = False

    def commit(self) -> None:
        self.committed = True

    def rollback(self) -> None:
        self.rolled_back = True


_HARVEST_RESULT = {
    "shard_yield": 2,
    "crit": False,
    "nebula_type": "crimson",
    "quantum_shards": 7,
    "turns_spent": 8,
    "remaining_turns": 92,
    "harvest_cooldown_until": "2026-07-05T00:00:00+00:00",
}


@pytest.fixture
def mock_send(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    """Patches the SAME connection_manager singleton
    ``_emit_quantum_harvest`` locally imports at call time."""
    mock = AsyncMock(return_value=True)
    monkeypatch.setattr(
        websocket_service.connection_manager, "send_personal_message", mock
    )
    return mock


@pytest.mark.unit
class TestQuantumHarvestEmit:
    @pytest.mark.asyncio
    async def test_success_emits_exactly_one_quantum_harvest_message(
        self, monkeypatch: pytest.MonkeyPatch, mock_send: AsyncMock
    ) -> None:
        player = _fake_player()
        db = _FakeSession()
        monkeypatch.setattr(
            quantum_route.quantum_service,
            "harvest_nebula",
            lambda db_arg, player_id: dict(_HARVEST_RESULT),
        )

        response = await quantum_route.quantum_harvest(
            request=None, player=player, db=db
        )

        assert response == _HARVEST_RESULT
        assert db.committed is True
        mock_send.assert_awaited_once()
        (sent_user_id, payload), _ = mock_send.await_args
        assert sent_user_id == str(player.user_id)
        assert payload["type"] == "quantum_harvest"
        assert payload["sector_id"] == player.current_sector_id
        assert payload["nebula_type"] == "crimson"
        assert payload["shards"] == 2
        assert payload["crit"] is False
        assert "timestamp" in payload

    @pytest.mark.asyncio
    async def test_rejected_harvest_emits_nothing(
        self, monkeypatch: pytest.MonkeyPatch, mock_send: AsyncMock
    ) -> None:
        player = _fake_player()
        db = _FakeSession()

        def _raise(db_arg, player_id):
            raise QuantumError("on_cooldown: the harvester is recharging")

        monkeypatch.setattr(quantum_route.quantum_service, "harvest_nebula", _raise)

        with pytest.raises(HTTPException) as exc_info:
            await quantum_route.quantum_harvest(request=None, player=player, db=db)

        assert exc_info.value.status_code == 400
        assert db.rolled_back is True
        assert db.committed is False
        mock_send.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_dead_socket_does_not_fail_an_already_committed_harvest(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A send_personal_message failure must be swallowed inside the
        emit helper — it must never surface as a 500 on a harvest whose
        commit already succeeded."""
        broken_send = AsyncMock(side_effect=RuntimeError("socket closed"))
        monkeypatch.setattr(
            websocket_service.connection_manager, "send_personal_message", broken_send
        )
        player = _fake_player()
        db = _FakeSession()
        monkeypatch.setattr(
            quantum_route.quantum_service,
            "harvest_nebula",
            lambda db_arg, player_id: dict(_HARVEST_RESULT),
        )

        response = await quantum_route.quantum_harvest(
            request=None, player=player, db=db
        )

        assert response == _HARVEST_RESULT
        assert db.committed is True
        broken_send.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_unexpected_service_exception_rolls_back_and_emits_nothing(
        self, monkeypatch: pytest.MonkeyPatch, mock_send: AsyncMock
    ) -> None:
        player = _fake_player()
        db = _FakeSession()

        def _boom(db_arg, player_id):
            raise RuntimeError("unexpected")

        monkeypatch.setattr(quantum_route.quantum_service, "harvest_nebula", _boom)

        with pytest.raises(RuntimeError):
            await quantum_route.quantum_harvest(request=None, player=player, db=db)

        assert db.rolled_back is True
        assert db.committed is False
        mock_send.assert_not_awaited()
