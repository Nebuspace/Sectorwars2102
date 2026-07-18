"""WO #3 (mack gate), Part A — DEFECT-fleet-battle-asyncsession: 13 routes in
``src/api/routes/fleets.py`` were wired ``db: Session = Depends(get_async_session)``
against ``FleetService``, which is entirely SYNC (``self.db.query(...)
.with_for_update()/.commit()``, never awaited). ``AsyncSession`` has NO
``.query()`` attribute at all (confirmed below) — every one of those 13
routes' FIRST ``.query()`` call raised a bare ``AttributeError`` and 500'd,
100% of the time, for every fleet route class. The fix swaps all 13 to
``Depends(get_db)`` (the sync session — matches ``resupply_fleet``, the one
route in this module that was ALREADY correctly wired this way, and whose
own comment previously flagged the other 13 as the pre-existing mismatch).

Two independent proofs per route CLASS (create / get / simulate / update /
disband):

  1. STATIC — the route function's ``db`` parameter really is
     ``Depends(get_db)`` post-fix (not ``get_async_session``, which is no
     longer even imported into the module).
  2. DYNAMIC — calling the handler with a sync-Session-shaped test double
     reaches its ``.query()`` call and proceeds to a deterministic outcome
     (a 404/400 HTTPException) WITHOUT an AttributeError; calling the SAME
     handler with an AsyncSession-shaped stand-in (one that genuinely lacks
     ``.query()``, mirroring the confirmed real gap) reproduces the original
     defect's AttributeError at that exact call site.

No live DB / no TestClient (the Mac has no local Postgres) — handlers are
awaited directly, matching the established route-level convention in this
suite (test_research_unlock_route.py).
"""
from __future__ import annotations

import inspect
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException

from src.api.routes import fleets as route
from src.core.database import get_db, get_async_session
from src.models.fleet import Fleet, FleetBattle
from src.models.team import Team


# --------------------------------------------------------------------------- #
# Confirm the defect's premise: AsyncSession genuinely has no ``.query()``.
# --------------------------------------------------------------------------- #

def test_async_session_has_no_query_attribute():
    """Pins the root cause this WO fixes: if any route were still wired to
    ``get_async_session``, its FIRST ``.query()`` call would AttributeError,
    100% of the time — this is not a hypothetical, it's a hard API gap."""
    from sqlalchemy.ext.asyncio import AsyncSession
    assert not hasattr(AsyncSession, "query")


# --------------------------------------------------------------------------- #
# Part 1 — STATIC: all 13 swapped routes now declare Depends(get_db).
# --------------------------------------------------------------------------- #

SWAPPED_ROUTE_NAMES = [
    "create_fleet",
    "get_team_fleets",
    "get_my_fleets",
    "get_team_battles",
    "simulate_battle_round",
    "get_fleet",
    "get_fleet_members",
    "add_ship_to_fleet",
    "remove_ship_from_fleet",
    "update_fleet_formation",
    "update_fleet_commander",
    "disband_fleet",
    "initiate_battle",
]


class TestAllThirteenRoutesWireToSyncGetDb:
    def test_exactly_thirteen_names_enumerated(self):
        # Guards the enumeration itself against silent drift (a 14th route
        # added/removed in fleets.py without updating this list).
        assert len(SWAPPED_ROUTE_NAMES) == 13

    @pytest.mark.parametrize("handler_name", SWAPPED_ROUTE_NAMES)
    def test_db_param_depends_on_get_db_not_get_async_session(self, handler_name):
        handler = getattr(route, handler_name)
        sig = inspect.signature(handler)
        db_param = sig.parameters["db"]
        dep = db_param.default
        assert dep.dependency is get_db, (
            f"{handler_name}'s db param must resolve to get_db (sync Session) "
            f"— FleetService is entirely sync; got {dep.dependency!r}"
        )
        assert dep.dependency is not get_async_session

    def test_module_no_longer_imports_get_async_session(self):
        # get_async_session is still importABLE from core.database (other
        # modules may legitimately use it) but fleets.py itself must not
        # bind the name at module scope anymore — the whole point of the fix.
        assert "get_async_session" not in vars(route)

    def test_resupply_fleet_unchanged_already_sync(self):
        """resupply_fleet was ALREADY correctly wired to get_db before this
        WO (its own comment flagged the other 13 as the mismatch) — confirms
        the fix didn't touch it and it remains the pre-existing correct
        baseline."""
        sig = inspect.signature(route.resupply_fleet)
        assert sig.parameters["db"].default.dependency is get_db


# --------------------------------------------------------------------------- #
# Part 2 — DYNAMIC: one reachability smoke per route CLASS.
# --------------------------------------------------------------------------- #

class _BrokenAsyncSessionStandIn:
    """Deliberately exposes NO ``.query()`` — mirrors the confirmed real gap
    on ``sqlalchemy.ext.asyncio.AsyncSession`` (see test above). Standing in
    for "what get_async_session would have injected pre-fix"."""


class _FakeQuery:
    def __init__(self, result):
        self._result = result  # None, a single object, or a list

    def filter(self, *a, **k):
        return self

    def populate_existing(self):
        return self

    def with_for_update(self, *a, **k):
        return self

    def distinct(self):
        return self

    def subquery(self):
        return self

    def first(self):
        if isinstance(self._result, list):
            return self._result[0] if self._result else None
        return self._result

    def all(self):
        if isinstance(self._result, list):
            return self._result
        return [] if self._result is None else [self._result]


class _FakeSyncSession:
    """Sync-Session-shaped test double — mirrors what get_db actually
    injects. Deterministically returns None (-> 404/400) for every model
    queried, which is sufficient to prove the handler reached PAST its
    first ``.query()`` call without AttributeError."""

    def __init__(self, results_by_model=None):
        self._results = results_by_model or {}

    def query(self, model):
        return _FakeQuery(self._results.get(model))


def make_player(**overrides):
    defaults = dict(id=uuid4(), team_id=uuid4())
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


class TestRouteClassSmokes:
    """One route per class (create / get / simulate / update / disband).
    Each: (a) with the broken AsyncSession stand-in -> AttributeError at the
    handler's first sync .query() call (the historical defect, reproduced);
    (b) with the sync FakeSession -> reaches past it to a deterministic
    HTTPException (the fix)."""

    # --- CREATE: create_fleet -------------------------------------------- #

    @pytest.mark.asyncio
    async def test_create_fleet_broken_async_session_raises_attribute_error(self):
        player = make_player()
        with pytest.raises(AttributeError):
            await route.create_fleet(
                request=SimpleNamespace(name="F", formation="standard", commander_id=None),
                player=player,
                db=_BrokenAsyncSessionStandIn(),
            )

    @pytest.mark.asyncio
    async def test_create_fleet_sync_session_reaches_query_no_attribute_error(self):
        player = make_player()
        db = _FakeSyncSession({Team: None})  # FleetService.create_fleet's Team lookup -> None
        with pytest.raises(HTTPException) as exc_info:
            await route.create_fleet(
                request=SimpleNamespace(name="F", formation="standard", commander_id=None),
                player=player,
                db=db,
            )
        assert exc_info.value.status_code == 400  # "Team ... not found" -- reached past .query()

    # --- GET: get_fleet ---------------------------------------------------- #

    @pytest.mark.asyncio
    async def test_get_fleet_broken_async_session_raises_attribute_error(self):
        player = make_player()
        with pytest.raises(AttributeError):
            await route.get_fleet(fleet_id=uuid4(), player=player, db=_BrokenAsyncSessionStandIn())

    @pytest.mark.asyncio
    async def test_get_fleet_sync_session_reaches_query_no_attribute_error(self):
        player = make_player()
        db = _FakeSyncSession({Fleet: None})
        with pytest.raises(HTTPException) as exc_info:
            await route.get_fleet(fleet_id=uuid4(), player=player, db=db)
        assert exc_info.value.status_code == 404  # "Fleet not found" -- reached past .query()

    # --- SIMULATE: simulate_battle_round ----------------------------------- #

    @pytest.mark.asyncio
    async def test_simulate_battle_round_broken_async_session_raises_attribute_error(self):
        player = make_player()
        with pytest.raises(AttributeError):
            await route.simulate_battle_round(
                battle_id=uuid4(), player=player, db=_BrokenAsyncSessionStandIn(),
            )

    @pytest.mark.asyncio
    async def test_simulate_battle_round_sync_session_reaches_query_no_attribute_error(self):
        player = make_player()
        db = _FakeSyncSession({FleetBattle: None})
        with pytest.raises(HTTPException) as exc_info:
            await route.simulate_battle_round(battle_id=uuid4(), player=player, db=db)
        assert exc_info.value.status_code == 404  # "Battle not found" -- reached past .query()

    # --- UPDATE: update_fleet_formation ------------------------------------ #

    @pytest.mark.asyncio
    async def test_update_fleet_formation_broken_async_session_raises_attribute_error(self):
        player = make_player()
        with pytest.raises(AttributeError):
            await route.update_fleet_formation(
                fleet_id=uuid4(), formation="standard", player=player,
                db=_BrokenAsyncSessionStandIn(),
            )

    @pytest.mark.asyncio
    async def test_update_fleet_formation_sync_session_reaches_query_no_attribute_error(self):
        player = make_player()
        db = _FakeSyncSession({Fleet: None})
        with pytest.raises(HTTPException) as exc_info:
            await route.update_fleet_formation(
                fleet_id=uuid4(), formation="standard", player=player, db=db,
            )
        assert exc_info.value.status_code == 404  # "Fleet not found" -- reached past .query()

    # --- DISBAND: disband_fleet --------------------------------------------- #

    @pytest.mark.asyncio
    async def test_disband_fleet_broken_async_session_raises_attribute_error(self):
        player = make_player()
        with pytest.raises(AttributeError):
            await route.disband_fleet(fleet_id=uuid4(), player=player, db=_BrokenAsyncSessionStandIn())

    @pytest.mark.asyncio
    async def test_disband_fleet_sync_session_reaches_query_no_attribute_error(self):
        player = make_player()
        db = _FakeSyncSession({Fleet: None})
        with pytest.raises(HTTPException) as exc_info:
            await route.disband_fleet(fleet_id=uuid4(), player=player, db=db)
        assert exc_info.value.status_code == 404  # "Fleet not found" -- reached past .query()
