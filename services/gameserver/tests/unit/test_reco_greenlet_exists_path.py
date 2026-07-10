"""Unit tests for WO-SWEEP-RECO-GREENLET-2.

Second mechanism, same defect class as WO-SWEEP-RECO-GREENLET (c29b31c):
core/database.py's AsyncSessionLocal leaves expire_on_commit unset (the
async_sessionmaker default, True), so ANY commit mid-request expires EVERY
object attached to that session, not just the row the commit was about.

c29b31c fixed the profile-CREATE branch (a fresh player with no
PlayerTradingProfile row: _create_initial_trading_profile commits and
expired the caller's `player`). This suite pins the profile-EXISTS branch,
one layer up the call stack in enhanced_ai_service.py:

  EnhancedAIService.get_comprehensive_recommendations
    -> _get_trading_recommendations(assistant, ...)
         -> AITradingService.get_trading_recommendations(db, ...)   [ai_trading_service.py]
              -> _save_recommendations_to_db(...)                    ALWAYS commits,
                 regardless of which branch (create vs exists) added the
                 profile -- it is not gated on _create_initial_trading_profile
                 having run. On the exists-path this is the ONLY commit in
                 the whole call graph, and it is unconditional.
    -> back in get_comprehensive_recommendations, still uses `assistant`:
         - assistant.has_permission("combat"/"colony"/"station") for any
           OTHER requested system type
         - assistant_id=assistant.id in the trailing _log_security_event call
           (this one fires unconditionally, no matter which system types
           were requested, as long as TRADING was one of them)

Pre-fix, either of those is a sync lazy-reload attempt on an async session
-> sqlalchemy.exc.MissingGreenlet ("greenlet_spawn has not been called").
Unlike ai_trading_service.py's per-helper broad-except-return-[] shape,
get_comprehensive_recommendations' own try/except re-raises as
RuntimeError("Recommendation service temporarily unavailable") -- which
both enhanced_ai.py routes (:311-322 and :370-375) then turn into an HTTP
500. That is the "temporarily unavailable" shape the live sweep hit.

DB-free async fake session, extending the same _FakeExpiring/_FakeResult
idiom as test_reco_greenlet_fix.py (redefined here, self-contained, per
this codebase's existing per-file convention rather than cross-test
imports) with one addition: AIComprehensiveAssistant is now also a
tracked, expirable session object, and the fake's AIComprehensiveAssistant
query returns an ALREADY-EXISTING assistant (mirroring the ALREADY-EXISTING
PlayerTradingProfile that defines the exists-path itself).
"""
from __future__ import annotations

import logging
import uuid

import pytest

from src.services.ai_trading_service import AITradingService
from src.services.enhanced_ai_service import AISystemType, EnhancedAIService


class _ExpiredAttributeError(Exception):
    """Same Exception-subclass shape as sqlalchemy.exc.MissingGreenlet, so
    `except Exception` sites in production code behave identically against
    this fake as they would against the real error."""


class _FakeExpiring:
    """Duck-typed stand-in for an ORM instance under expire_on_commit=True:
    attributes given at construction read fine until the owning fake
    session's commit() marks the instance expired; a read while expired
    raises _ExpiredAttributeError, and refresh() un-expires it again."""

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


class _FakeAssistant(_FakeExpiring):
    """Adds the two real AIComprehensiveAssistant methods this call graph
    invokes as genuine methods (found via normal class-based lookup, exactly
    like the real mapped class), so their OWN body's attribute reads go
    through _FakeExpiring.__getattr__ -- the same "method resolves fine,
    the attribute read inside it is what breaks" shape as the real ORM.
    check_rate_limit is stubbed to a constant: quota logic isn't under
    test here, and every read it would otherwise do happens BEFORE the
    trading branch runs, well before any expiry risk."""

    def has_permission(self, system: str) -> bool:
        return self.access_permissions.get(system, False)

    def check_rate_limit(self) -> bool:
        return True


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


class FakeExistsPathSession:
    """Async db double for the profile-EXISTS path: one Player row whose
    trading_profile is already set (so AITradingService's CREATE branch
    never runs -- only its own unconditional _save_recommendations_to_db
    commit does), one existing AIComprehensiveAssistant row, and an
    otherwise-empty galaxy so the three _generate_* helpers take their
    real, honest "nothing found" paths -- same shape as
    test_reco_greenlet_fix.py's FakeRecoSession, extended with the
    assistant lookup this call graph adds one layer up."""

    def __init__(self, player, assistant):
        self.player = player
        self.assistant = assistant
        self._session_objects = [player, assistant]
        self.added = []
        self.commits = 0

    async def execute(self, stmt):
        from src.models.enhanced_ai_models import AIComprehensiveAssistant
        from src.models.player import Player
        from src.models.sector import Sector

        descs = getattr(stmt, "column_descriptions", None)
        entity = descs[0].get("entity") if descs else None

        if entity is Player:
            return _FakeResult([self.player])
        if entity is AIComprehensiveAssistant:
            return _FakeResult([self.assistant])
        if entity is Sector:
            return _FakeResult([])  # no sectors seeded -- honest "not found"

        # NPCCharacter hostile-count query (select(func.count())) has no
        # column entity to key off of -- fall back to the FROM table name.
        table_name = stmt.get_final_froms()[0].name
        if table_name == "npc_characters":
            return _FakeResult([0])

        raise AssertionError(f"unexpected query in exists-path: {stmt!r}")

    def add(self, obj):
        self.added.append(obj)
        if obj not in self._session_objects:
            self._session_objects.append(obj)

    async def flush(self):
        pass

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


def _make_player(current_sector_id=7, trading_profile=None):
    return _FakeExpiring(
        id=uuid.uuid4(),
        current_sector_id=current_sector_id,
        # Non-None -> the profile-EXISTS branch: AITradingService skips
        # _create_initial_trading_profile entirely. The empty-galaxy fake
        # never reads a field off this object (no predictions/routes/risk
        # data to associate it with), so a bare marker object is enough to
        # be truthy without needing to model PlayerTradingProfile's shape.
        trading_profile=trading_profile if trading_profile is not None else object(),
    )


def _make_assistant(player_id, access_permissions=None):
    return _FakeAssistant(
        id=uuid.uuid4(),
        player_id=player_id,
        security_level="standard",
        access_permissions=access_permissions or {"trading": True, "combat": False},
    )


@pytest.mark.asyncio
async def test_get_trading_recommendations_refreshes_assistant():
    """Direct pin on the fix site: _get_trading_recommendations must
    refresh `assistant` after the trading-service call, because that call's
    own _save_recommendations_to_db commits UNCONDITIONALLY -- on the
    exists-path there is no profile-create commit to have already covered
    it. Before the fix, the final assertion raises _ExpiredAttributeError
    (real code: MissingGreenlet)."""
    player = _make_player(current_sector_id=7)
    assistant = _make_assistant(player.id)
    db = FakeExistsPathSession(player, assistant)
    service = EnhancedAIService(db)

    await service._get_trading_recommendations(assistant, max_count=5)

    assert db.commits == 1
    # The regression: without refreshing `assistant` after that commit,
    # this read hits an expired attribute on an async session.
    assert assistant.id is not None


@pytest.mark.asyncio
async def test_get_comprehensive_recommendations_exists_path_survives(caplog):
    """End-to-end through the real public entry point both enhanced_ai.py
    routes call (:344 and :273): a player who ALREADY has a
    PlayerTradingProfile row (the exists-path AITradingService never
    exercised a commit-then-reuse bug for before) must not trip the
    greenlet defect on ANY of `assistant`'s post-commit reads --
    has_permission("combat") (requested but denied, so _get_combat_
    recommendations' own body never has to run) and the trailing
    _log_security_event's assistant_id=assistant.id.

    Unlike ai_trading_service.py's per-helper swallow-into-[] shape, THIS
    method's own except re-raises as RuntimeError -- so pre-fix this call
    doesn't return an empty list, it raises. Asserting only "did not raise"
    already pins the bug; caplog additionally confirms no internal
    generator swallowed a related error along the way.
    """
    player = _make_player(current_sector_id=7)
    assistant = _make_assistant(
        player.id, access_permissions={"trading": True, "combat": False}
    )
    db = FakeExistsPathSession(player, assistant)
    service = EnhancedAIService(db)

    # Monkeypatch the real _get_player_with_profile's player_id resolution
    # path: AITradingService.get_trading_recommendations parses player_id
    # back into a UUID via uuid.UUID(player_id) and never actually filters
    # the fake's canned Player result by it, so no patching is needed there
    # -- kept as a comment, not code, so a future reader isn't left
    # wondering why nothing is patched here.
    assert isinstance(service.trading_service, AITradingService)

    with caplog.at_level(logging.ERROR):
        recommendations = await service.get_comprehensive_recommendations(
            player_id=player.id,
            system_types=[AISystemType.TRADING, AISystemType.COMBAT],
            max_recommendations=5,
        )

    assert recommendations == []
    assert db.commits == 1

    greenlet_errors = [
        r
        for r in caplog.records
        if "greenlet_spawn" in r.getMessage() or "_ExpiredAttributeError" in r.getMessage()
    ]
    assert not greenlet_errors, (
        "greenlet/expired-attribute error was raised and silently "
        f"swallowed or re-raised: {[r.getMessage() for r in greenlet_errors]}"
    )
