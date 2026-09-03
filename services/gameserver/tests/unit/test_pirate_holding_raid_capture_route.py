"""Route-level tests for POST /pirate-holdings/{holding_id}/raid/capture (LEG-4153).

Follows the pattern established in test_pirate_holdings_discovery.py:
FastAPI TestClient via httpx.AsyncClient + dependency_overrides.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from src.api.routes import pirate_holdings as ph_mod
from src.auth.dependencies import get_current_player, get_current_user
from src.core.database import get_db
from src.models.pirate_holding import PirateHolding, PirateHoldingTier


# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------

_PLAYER_ID = uuid.uuid4()
_TEAM_ID = uuid.uuid4()
_HOLDING_ID = uuid.uuid4()
_REGION_ID = uuid.uuid4()
_CAPTURED_AT = datetime(2026, 9, 3, 15, 0, 0, tzinfo=timezone.utc)


def _holding(*, holding_id=None, tier=PirateHoldingTier.OUTPOST, **overrides):
    defaults = dict(
        id=holding_id or _HOLDING_ID,
        region_id=_REGION_ID,
        sector_id=42,
        tier=tier,
        owner_player_id=None,
        owner_team_id=None,
        captured_at=None,
        combat_lock_held_by=_PLAYER_ID,
        combat_lock_team_snapshot=[_PLAYER_ID],
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _team():
    return SimpleNamespace(id=_TEAM_ID, members=[SimpleNamespace(id=_PLAYER_ID)])


def _player(*, player_id=None, team=None):
    pid = player_id or _PLAYER_ID
    t = team if team is not None else _team()
    return SimpleNamespace(id=pid, team=t, team_id=t.id if t else None)


# ---------------------------------------------------------------------------
# Fake DB session — supports query().filter().with_for_update().first() chain
# ---------------------------------------------------------------------------

class _FakeQuery:
    def __init__(self, holding):
        self._holding = holding

    def filter(self, *args, **kwargs):
        return self

    def with_for_update(self, *args, **kwargs):
        return self

    def options(self, *args, **kwargs):
        return self

    def first(self):
        return self._holding

    def all(self):
        return [self._holding] if self._holding else []


class _FakeSession:
    def __init__(self, holding):
        self._holding = holding
        self.committed = False
        self.rolled_back = False

    def query(self, model):
        return _FakeQuery(self._holding)

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------

def _make_app(db, player):
    app = FastAPI()
    app.include_router(ph_mod.router)
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=uuid.uuid4())
    app.dependency_overrides[get_current_player] = lambda: SimpleNamespace(id=player.id)
    app.dependency_overrides[get_db] = lambda: db
    return app


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestCapturePirateHoldingRaid:
    @pytest.mark.asyncio
    async def test_capture_happy_path(self):
        holding = _holding()
        player = _player()
        db = _FakeSession(holding)

        def _fake_capture(session, h, p, *, kill_log_entry_kwargs):
            h.owner_team_id = p.team.id
            h.captured_at = _CAPTURED_AT
            h.combat_lock_held_by = None
            h.combat_lock_team_snapshot = None

        app = _make_app(db, player)
        with patch.object(ph_mod.pes, "capture_holding", side_effect=_fake_capture):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                resp = await client.post(f"/pirate-holdings/{holding.id}/raid/capture")

        assert resp.status_code == 200
        body = resp.json()
        assert body["holding_id"] == str(holding.id)
        assert body["owner_team_id"] == str(_TEAM_ID)
        assert body["captured_at"] is not None
        assert db.committed is True

    @pytest.mark.asyncio
    async def test_holding_not_found_404(self):
        player = _player()
        db = _FakeSession(None)  # query returns None

        app = _make_app(db, player)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(f"/pirate-holdings/{uuid.uuid4()}/raid/capture")

        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_bad_uuid_404(self):
        player = _player()
        db = _FakeSession(None)

        app = _make_app(db, player)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post("/pirate-holdings/not-a-uuid/raid/capture")

        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_already_captured_by_team_409(self):
        holding = _holding(owner_team_id=uuid.uuid4())
        player = _player()
        db = _FakeSession(holding)

        app = _make_app(db, player)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(f"/pirate-holdings/{holding.id}/raid/capture")

        assert resp.status_code == 409
        assert "already captured" in resp.json()["detail"]

    @pytest.mark.asyncio
    async def test_already_captured_by_player_409(self):
        holding = _holding(owner_player_id=uuid.uuid4())
        player = _player()
        db = _FakeSession(holding)

        app = _make_app(db, player)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(f"/pirate-holdings/{holding.id}/raid/capture")

        assert resp.status_code == 409

    @pytest.mark.asyncio
    async def test_no_active_lock_409(self):
        holding = _holding(combat_lock_held_by=None, combat_lock_team_snapshot=None)
        player = _player()
        db = _FakeSession(holding)

        app = _make_app(db, player)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(f"/pirate-holdings/{holding.id}/raid/capture")

        assert resp.status_code == 409
        assert "No active combat lock" in resp.json()["detail"]

    @pytest.mark.asyncio
    async def test_lock_held_by_other_player_409(self):
        other_player_id = uuid.uuid4()
        holding = _holding(
            combat_lock_held_by=other_player_id,
            combat_lock_team_snapshot=[other_player_id],
        )
        player = _player()  # _PLAYER_ID, not in snapshot
        db = _FakeSession(holding)

        app = _make_app(db, player)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(f"/pirate-holdings/{holding.id}/raid/capture")

        assert resp.status_code == 409
        assert "another player" in resp.json()["detail"]

    @pytest.mark.asyncio
    async def test_teammate_in_snapshot_can_capture(self):
        """Player is not the lock holder but IS in the team snapshot — allowed."""
        holder_id = uuid.uuid4()
        teammate_id = uuid.uuid4()
        holding = _holding(
            combat_lock_held_by=holder_id,
            combat_lock_team_snapshot=[holder_id, teammate_id],
        )
        player = _player(player_id=teammate_id)
        db = _FakeSession(holding)

        def _fake_capture(session, h, p, *, kill_log_entry_kwargs):
            h.owner_team_id = p.team.id
            h.captured_at = _CAPTURED_AT
            h.combat_lock_held_by = None
            h.combat_lock_team_snapshot = None

        app = _make_app(db, player)
        with patch.object(ph_mod.pes, "capture_holding", side_effect=_fake_capture):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                resp = await client.post(f"/pirate-holdings/{holding.id}/raid/capture")

        assert resp.status_code == 200
        assert resp.json()["holding_id"] == str(holding.id)

    @pytest.mark.asyncio
    async def test_capture_passes_correct_kill_log_kwargs(self):
        """Verify TIER_WEIGHT and attacker identity reach capture_holding."""
        from src.services.pirate_ecosystem_service import TIER_WEIGHT

        holding = _holding(tier=PirateHoldingTier.STRONGHOLD)
        player = _player()
        db = _FakeSession(holding)
        captured_kwargs = {}

        def _fake_capture(session, h, p, *, kill_log_entry_kwargs):
            captured_kwargs.update(kill_log_entry_kwargs)
            h.owner_team_id = p.team.id
            h.captured_at = _CAPTURED_AT

        app = _make_app(db, player)
        with patch.object(ph_mod.pes, "capture_holding", side_effect=_fake_capture):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                await client.post(f"/pirate-holdings/{holding.id}/raid/capture")

        assert captured_kwargs["kill_weight"] == TIER_WEIGHT[PirateHoldingTier.STRONGHOLD]
        assert captured_kwargs["attacker_player_id"] == player.id
        assert captured_kwargs["attacker_team_id"] == _TEAM_ID
        assert captured_kwargs["holding_id"] == holding.id
        assert captured_kwargs["tier"] == PirateHoldingTier.STRONGHOLD
