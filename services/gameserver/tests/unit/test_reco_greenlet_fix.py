"""Unit tests for WO-SWEEP-RECO-GREENLET.

On a fresh DB (no PlayerTradingProfile row yet), AITradingService's
PlayerTradingProfile CREATE branch (_create_initial_trading_profile)
commits mid get_trading_recommendations(). core/database.py's
AsyncSessionLocal leaves expire_on_commit unset (the async_sessionmaker
default: True), so that commit expires EVERY object attached to the
session -- not just the profile it just created, but the caller's
already-loaded `player` too. The three _generate_* helpers
(_generate_market_opportunities / _generate_route_recommendations /
_generate_risk_warnings) all read player.current_sector_id right after,
which is a sync lazy-reload attempt on an async session outside a
greenlet-spawned context -- sqlalchemy.exc.MissingGreenlet
("greenlet_spawn has not been called; can't call await_only() here").
Exactly the defect class already documented (and fixed, for a different
object) at enhanced_ai_service.py:476-481. Legacy dev DBs always had a
profile row already, so this CREATE branch -- and the bug -- never ran
until now.

DB-free, async fake session. Unlike this codebase's usual
column_descriptions-keyed fake (test_aria_cascade_path.py), this one also
models the ONE piece of real SQLAlchemy session behavior the bug hinges
on: expire_on_commit expiring every tracked object on commit(), with a
read of an expired attribute raising until refresh() -- see
_FakeExpiring/_ExpiredAttributeError below.
"""
from __future__ import annotations

import logging
import uuid

import pytest

from src.services.ai_trading_service import AITradingService


class _ExpiredAttributeError(Exception):
    """Same Exception-subclass shape as sqlalchemy.exc.MissingGreenlet, so
    `except Exception` sites in production code behave identically against
    this fake as they would against the real error."""


class _FakeExpiring:
    """Duck-typed stand-in for an ORM instance under expire_on_commit=True:
    attributes given at construction read fine until the owning fake
    session's commit() marks the instance expired; a read while expired
    raises _ExpiredAttributeError, and refresh() un-expires it again --
    the real AsyncSession contract this bug depends on."""

    def __init__(self, **attrs):
        object.__setattr__(self, "_attrs", dict(attrs))
        object.__setattr__(self, "_expired", False)

    def __getattr__(self, name):
        attrs = object.__getattribute__(self, "_attrs")
        if name not in attrs:
            raise AttributeError(name)
        if object.__getattribute__(self, "_expired"):
            raise _ExpiredAttributeError(
                "greenlet_spawn has not been called; can't call "
                "await_only() here. Was IO attempted in an unexpected "
                "place?"
            )
        return attrs[name]


class _FakeResult:
    def __init__(self, rows):
        self._rows = list(rows)

    def scalars(self):
        return self

    def all(self):
        return list(self._rows)

    def scalar_one_or_none(self):
        return self._rows[0] if self._rows else None

    def scalar(self):
        return self._rows[0] if self._rows else None


class FakeRecoSession:
    """Async db double for get_trading_recommendations' fresh-player path:
    one Player row with no PlayerTradingProfile yet, and an otherwise-empty
    galaxy (no sectors, no NPCs) so the three _generate_* helpers take
    their real, honest "nothing found" early-return paths -- exactly what
    a genuinely fresh DB presents, not a shortcut around them."""

    def __init__(self, player):
        self.player = player
        self._session_objects = [player]
        self.added = []
        self.commits = 0

    async def execute(self, stmt):
        from src.models.player import Player
        from src.models.sector import Sector

        descs = getattr(stmt, "column_descriptions", None)
        entity = descs[0].get("entity") if descs else None

        if entity is Player:
            return _FakeResult([self.player])
        if entity is Sector:
            return _FakeResult([])  # no sectors seeded -- honest "not found"

        # NPCCharacter hostile-count query (select(func.count())) has no
        # column entity to key off of -- fall back to the FROM table name.
        table_name = stmt.get_final_froms()[0].name
        if table_name == "npc_characters":
            return _FakeResult([0])

        raise AssertionError(f"unexpected query in fresh-player path: {stmt!r}")

    def add(self, obj):
        self.added.append(obj)
        self._session_objects.append(obj)

    async def commit(self):
        self.commits += 1
        for obj in self._session_objects:
            if isinstance(obj, _FakeExpiring):
                object.__setattr__(obj, "_expired", True)

    async def refresh(self, obj):
        if isinstance(obj, _FakeExpiring):
            object.__setattr__(obj, "_expired", False)
        if obj not in self._session_objects:
            self._session_objects.append(obj)

    async def rollback(self):
        pass


def _make_player(current_sector_id=7):
    return _FakeExpiring(
        id=uuid.uuid4(),
        current_sector_id=current_sector_id,
        trading_profile=None,
    )


@pytest.mark.asyncio
async def test_create_initial_trading_profile_refreshes_player():
    """Direct pin on the fix site: _create_initial_trading_profile's own
    commit expires every object on the session, including the `player` it
    was handed (not just the `profile` it just created and already
    refreshes). Before the fix, the final assertion raises
    _ExpiredAttributeError (real code: MissingGreenlet)."""
    player = _make_player(current_sector_id=7)
    db = FakeRecoSession(player)
    service = AITradingService()

    profile = await service._create_initial_trading_profile(db, player)

    assert profile.player_id == player.id
    assert db.commits == 1
    # The regression: without refreshing `player` after the commit above,
    # this read hits an expired attribute on an async session.
    assert player.current_sector_id == 7


@pytest.mark.asyncio
async def test_get_trading_recommendations_fresh_player_no_profile_row(caplog):
    """End-to-end through the real service entry point (ai.py:130 calls
    this directly): a fresh player with no PlayerTradingProfile row must
    not trip the greenlet defect anywhere in the three _generate_* calls
    that read player.current_sector_id right after profile creation.

    Asserting only the return value isn't enough to pin this: every
    _generate_* helper has its own broad except Exception that would
    swallow a MissingGreenlet and still return [], making the bug
    invisible to a caller that checks output alone. caplog is what makes
    this a real regression test -- it fails loudly pre-fix even though
    the return value looks identical either way.
    """
    player = _make_player(current_sector_id=7)
    db = FakeRecoSession(player)
    service = AITradingService()

    with caplog.at_level(logging.ERROR):
        recommendations = await service.get_trading_recommendations(
            db, str(player.id), limit=5
        )

    assert recommendations == []
    greenlet_errors = [
        r for r in caplog.records
        if "greenlet_spawn" in r.getMessage() or "_ExpiredAttributeError" in r.getMessage()
    ]
    assert not greenlet_errors, (
        "greenlet/expired-attribute error was raised and silently "
        f"swallowed: {[r.getMessage() for r in greenlet_errors]}"
    )
    # None of the three generators' own broad except-blocks should have
    # fired at all on this honest-empty-galaxy fresh path.
    generator_errors = [
        r for r in caplog.records
        if r.getMessage().startswith((
            "Error generating market opportunities",
            "Error generating route recommendations",
            "Error generating risk warnings",
        ))
    ]
    assert not generator_errors, generator_errors
