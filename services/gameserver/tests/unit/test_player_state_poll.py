"""Regression pin for the GET /player/state poll's conditional commit
(WO-QTI-STATE-POLL).

Before this WO the hot state poll called the deprecated
``ranking_service.refresh_daily_turns`` shim and then issued an
unconditional ``db.commit()`` on EVERY read. It now delegates straight to
``turn_service.regenerate_turns`` (the same ADR-0004 FROZEN HOOK every
turn-spend site calls) and commits only when that hook actually advanced
the balance — a bare read never needs a write-commit.

DB-free: the route function is called directly (bypassing FastAPI's
``Depends`` resolution) against a plain in-memory player object and a
``MagicMock`` session. No database or container required.
"""
import inspect
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from src.api.routes import player as player_route
from src.services import ranking_service, turn_service


def make_player(**overrides):
    """A SimpleNamespace carrying every attribute get_player_state (and the
    real turn_service.effective_regen_per_hour / BountyService reads it also
    exercises) touches."""
    defaults = dict(
        id=uuid4(),
        user_id=None,  # None short-circuits regenerate_turns' WS push
        username="tester",
        credits=1000,
        turns=500,
        current_sector_id=1,
        is_docked=False,
        is_landed=False,
        current_port_id=None,
        current_planet_id=None,
        defense_drones=0,
        attack_drones=0,
        mines=0,
        current_ship_id=None,
        team_id=None,
        personal_reputation=0,
        reputation_tier="Neutral",
        name_color="#FFFFFF",
        military_rank="Recruit",
        aria_bonus_multiplier=1.0,
        settings={},
        max_turns=1000,
        last_turn_regeneration=None,
        created_at=None,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


@pytest.mark.asyncio
async def test_no_regen_due_skips_commit(monkeypatch):
    """A poll landing mid-turn (not a full turn's worth of elapsed time yet)
    must not commit — this is the steady-state case for a hot poll."""
    player = make_player(turns=500)
    db = MagicMock()

    def fake_regenerate_turns(_db, _player):
        # Mirrors the real no-op branch: turns unchanged.
        return {
            "regenerated": False,
            "turns_added": 0,
            "old_turns": 500,
            "new_turns": 500,
            "max_turns": player.max_turns,
        }

    monkeypatch.setattr(turn_service, "regenerate_turns", fake_regenerate_turns)

    response = await player_route.get_player_state(player=player, db=db)

    db.commit.assert_not_called()
    assert response.turns == 500


@pytest.mark.asyncio
async def test_regen_advances_commits_exactly_once(monkeypatch):
    """A poll that crosses a full-turn boundary must commit exactly once,
    and the response must reflect the freshly-advanced balance."""
    player = make_player(turns=500)
    db = MagicMock()

    def fake_regenerate_turns(_db, _player):
        # regenerate_turns always mutates player.turns directly on the ORM
        # object, independent of whether the caller later commits.
        player.turns = 507
        return {
            "regenerated": True,
            "turns_added": 7,
            "old_turns": 500,
            "new_turns": 507,
            "max_turns": player.max_turns,
        }

    monkeypatch.setattr(turn_service, "regenerate_turns", fake_regenerate_turns)

    response = await player_route.get_player_state(player=player, db=db)

    db.commit.assert_called_once()
    assert response.turns == 507


@pytest.mark.asyncio
async def test_no_regen_due_still_returns_fresh_turns_value(monkeypatch):
    """Even when nothing advances (so nothing commits), the response must
    read the in-memory player state, not a stale cached value — the
    freshness of the READ is decoupled from whether a WRITE was needed."""
    player = make_player(turns=250)
    db = MagicMock()

    def fake_regenerate_turns(_db, _player):
        return {
            "regenerated": False,
            "turns_added": 0,
            "old_turns": 250,
            "new_turns": 250,
            "max_turns": player.max_turns,
        }

    monkeypatch.setattr(turn_service, "regenerate_turns", fake_regenerate_turns)

    response = await player_route.get_player_state(player=player, db=db)

    assert response.turns == 250
    db.commit.assert_not_called()


def test_no_unconditional_commit_remains_in_source():
    """Source-pin: get_player_state's only db.commit() call must sit strictly
    deeper (inside the regenerated-gate) than the regenerate_turns() call
    itself, so a future edit can't silently hoist it back to unconditional."""
    source = inspect.getsource(player_route.get_player_state)
    lines = source.splitlines()

    regen_call_lines = [l for l in lines if "regenerate_turns(db, player)" in l]
    assert regen_call_lines, "expected a regenerate_turns(db, player) call"
    regen_call_indent = len(regen_call_lines[0]) - len(regen_call_lines[0].lstrip())

    commit_lines = [l for l in lines if "db.commit()" in l]
    assert commit_lines, "expected a conditional db.commit() in get_player_state"
    for l in commit_lines:
        indent = len(l) - len(l.lstrip())
        assert indent > regen_call_indent, (
            "db.commit() must be indented inside the regenerated-gate, "
            f"not unconditional at the top level (line: {l!r})"
        )


def test_refresh_daily_turns_shim_removed():
    """The deprecated shim's own docstring said it was retained ONLY for the
    /player/state read endpoint; now that this route no longer calls it (and
    no other caller exists repo-wide), it must be gone rather than dead code."""
    assert not hasattr(ranking_service.RankingService, "refresh_daily_turns")
