"""WO-PROG-TURN-VISIBILITY Lane C -- welcome_back()'s turn_pool_updated emit.

Before this WO, turn_service.welcome_back()'s grant was invisible on the
client until the NEXT lazy-regen tick -- the only turn_pool_updated emitter
was regenerate_turns' _emit_turn_pool_update. This pins the new emit
welcome_back schedules on its own nonzero-grant path: it reuses
_emit_turn_pool_update -- the exact same guard idiom regenerate_turns uses
(asyncio.get_running_loop() + loop.create_task, best-effort, never raises) --
with reason='welcome_back' so a client can tell a lump-sum top-up apart from
ordinary regen.

DB-free: welcome_back() takes a transient (unpersisted) Player() and mutates
it in memory -- no engine, no session (see [[fake-orm-flush-defaults-gap]]
for why every field is set explicitly on `_make_player` below).
connection_manager.send_turn_pool_update is monkeypatched to an AsyncMock,
mirroring test_quantum_harvest_emit.py's mock_send fixture -- same singleton
_emit_turn_pool_update lazily imports at call time.

welcome_back() is itself SYNC and only SCHEDULES the send via
loop.create_task -- it is called un-awaited (it is not a coroutine), and each
test does one `await asyncio.sleep(0)` afterward to yield to the loop so the
scheduled task actually runs before assertions.
"""
import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest

from src.models.player import Player
from src.services import websocket_service
from src.services.turn_service import welcome_back


def _make_player(*, last_game_login, turns=100, max_turns=1000):
    """Transient (unpersisted) Player -- mirrors test_welcome_back_response.py's
    _make_player. Fields set explicitly since Column(default=...) never fires
    without a real flush."""
    player = Player()
    player.id = uuid.uuid4()
    player.user_id = uuid.uuid4()
    player.turns = turns
    player.max_turns = max_turns
    player.military_rank = "Recruit"  # RankingService bonus = 0 -> max_turns stays as set
    player.last_game_login = last_game_login
    return player


@pytest.fixture
def mock_send(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    """Patches the SAME connection_manager singleton _emit_turn_pool_update
    lazily imports at call time."""
    mock = AsyncMock(return_value=True)
    monkeypatch.setattr(websocket_service.connection_manager, "send_turn_pool_update", mock)
    return mock


@pytest.mark.unit
class TestWelcomeBackEmit:
    @pytest.mark.asyncio
    async def test_nonzero_grant_emits_exactly_one_frame_with_welcome_back_reason(
        self, mock_send: AsyncMock
    ) -> None:
        # Byte-for-byte the shipped scenario in test_welcome_back_response.py's
        # test_track_player_login_returns_granted_outcome_for_qualifying_gap --
        # grant math regression pin: 8 days inactive, 100 -> 500 turns.
        now = datetime.now(timezone.utc)
        prior_login = now - timedelta(days=8, hours=1)
        player = _make_player(last_game_login=prior_login, turns=100)

        outcome = welcome_back(player, prior_login)
        await asyncio.sleep(0)

        assert outcome["granted"] is True
        assert outcome["bonus"] == 400  # min(500, 8 * 50)
        assert player.turns == 500

        mock_send.assert_awaited_once()
        (sent_user_id, payload), _ = mock_send.await_args
        assert sent_user_id == str(player.user_id)
        assert payload["player_id"] == str(player.id)
        assert payload["turns"] == 500
        assert payload["max_turns"] == 1000
        assert payload["turns_added"] == 400
        assert payload["reason"] == "welcome_back"
        assert payload["bonus_multiplier"] == 1.0

    @pytest.mark.asyncio
    async def test_subthreshold_gap_grants_nothing_and_emits_nothing(
        self, mock_send: AsyncMock
    ) -> None:
        now = datetime.now(timezone.utc)
        prior_login = now - timedelta(hours=1)  # well under the 7-day threshold
        player = _make_player(last_game_login=prior_login, turns=100)

        outcome = welcome_back(player, prior_login)
        await asyncio.sleep(0)

        assert outcome["granted"] is False
        assert outcome["bonus"] == 0
        assert player.turns == 100
        mock_send.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_already_at_cap_grants_nothing_and_emits_nothing(
        self, mock_send: AsyncMock
    ) -> None:
        now = datetime.now(timezone.utc)
        prior_login = now - timedelta(days=30)  # well past threshold
        player = _make_player(last_game_login=prior_login, turns=1000, max_turns=1000)

        outcome = welcome_back(player, prior_login)
        await asyncio.sleep(0)

        assert outcome["granted"] is False
        assert player.turns == 1000
        mock_send.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_never_logged_in_grants_nothing_and_emits_nothing(
        self, mock_send: AsyncMock
    ) -> None:
        player = _make_player(last_game_login=None, turns=100)

        outcome = welcome_back(player, None)
        await asyncio.sleep(0)

        assert outcome["granted"] is False
        assert player.turns == 100
        mock_send.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_max_cap_grant_emits_the_capped_bonus_as_turns_added(
        self, mock_send: AsyncMock
    ) -> None:
        # 40 days * 50/day = 2000, capped to WELCOME_BACK_MAX=500 -- mirrors
        # AuthContext.welcomeBack.test.tsx's bonus=500/days_inactive=40 fixture.
        now = datetime.now(timezone.utc)
        prior_login = now - timedelta(days=40)
        player = _make_player(last_game_login=prior_login, turns=50)

        outcome = welcome_back(player, prior_login)
        await asyncio.sleep(0)

        assert outcome["bonus"] == 500
        assert player.turns == 550

        mock_send.assert_awaited_once()
        (_, payload), _ = mock_send.await_args
        assert payload["turns_added"] == 500
        assert payload["reason"] == "welcome_back"

    @pytest.mark.asyncio
    async def test_grant_clamped_by_max_turns_emits_actual_added_not_raw_bonus(
        self, mock_send: AsyncMock
    ) -> None:
        # Raw bonus is min(500, 8*50)=400, but the player only has 50 turns of
        # headroom below the cap -- the emit must carry the ACTUAL clamped
        # top-up (50), not the uncapped bonus (400).
        now = datetime.now(timezone.utc)
        prior_login = now - timedelta(days=8, hours=1)
        player = _make_player(last_game_login=prior_login, turns=950, max_turns=1000)

        outcome = welcome_back(player, prior_login)
        await asyncio.sleep(0)

        assert outcome["bonus"] == 50
        assert player.turns == 1000

        mock_send.assert_awaited_once()
        (_, payload), _ = mock_send.await_args
        assert payload["turns_added"] == 50
        assert payload["turns"] == 1000
        assert payload["max_turns"] == 1000
