"""Unit tests for GET /routes/history (WO-ECON-ROUTE-HISTORY).

Exercises the player-facing route-history endpoint (route_optimizer.py) by
calling the route coroutine directly against a fake AsyncSession -- no real
DB required. ``current_player``/``db`` are just call arguments once
``Depends(...)`` resolution is bypassed, mirroring the direct-call house
pattern already used in test_route_optimization_telemetry.py.

Confirms the query is scoped to the caller only (never another player's
rows), orders newest-first, honors + caps ``limit`` at 50, and returns []
cleanly when the player has no recorded runs.
"""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from src.api.routes import route_optimizer as ro_route
from src.models.route_optimization_run import RouteOptimizationRun


def make_run(player_id, *, created_at, objective="balanced", **overrides):
    defaults = dict(
        id=uuid4(),
        player_id=player_id,
        objective=objective,
        start_sector="1",
        end_sector="3",
        sectors=["1", "2", "3"],
        total_profit=500.0,
        total_distance=2,
        total_time_hours=1.5,
        cargo_efficiency=0.8,
        route_confidence=0.9,
        status="completed",
        created_at=created_at,
    )
    defaults.update(overrides)
    return RouteOptimizationRun(**defaults)


def make_async_db(rows):
    """AsyncSession stand-in: db.execute is awaited, but the Result it
    resolves to is a plain (synchronous) SQLAlchemy Result on the real
    session -- ``.scalars().all()`` must NOT be auto-mocked as async too
    (the AsyncMock-nested-attr trap), so the Result stand-in is a MagicMock,
    not an AsyncMock."""
    db = MagicMock()
    fake_result = MagicMock()
    fake_result.scalars.return_value.all.return_value = rows
    db.execute = AsyncMock(return_value=fake_result)
    return db


def make_player(player_id=None):
    return SimpleNamespace(id=player_id or uuid4())


# --------------------------------------------------------------------------- #
# Response mapping
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_route_history_maps_rows_newest_first():
    player_id = uuid4()
    now = datetime.now(timezone.utc)
    newer = make_run(player_id, created_at=now, objective="shortest")
    older = make_run(player_id, created_at=now - timedelta(hours=1), objective="profit")
    # The fake DB just returns whatever rows it's handed, in the order
    # given -- the ORDER BY itself is proven by the query-shape assertion
    # below, not by this test re-sorting for the fake.
    db = make_async_db([newer, older])
    player = make_player(player_id)

    response = await ro_route.get_route_history(10, player, db)

    assert [r.id for r in response] == [str(newer.id), str(older.id)]
    assert response[0].objective == "shortest"
    assert response[0].sectors == ["1", "2", "3"]
    assert response[0].total_profit == 500.0
    assert response[0].status == "completed"
    assert response[1].objective == "profit"


@pytest.mark.asyncio
async def test_route_history_empty_returns_empty_list():
    db = make_async_db([])
    player = make_player()

    response = await ro_route.get_route_history(10, player, db)

    assert response == []


# --------------------------------------------------------------------------- #
# Query shape: scoping + ordering + limit cap
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_route_history_query_filters_on_caller_player_id():
    player_id = uuid4()
    db = make_async_db([])
    player = make_player(player_id)

    await ro_route.get_route_history(10, player, db)

    sent_query = db.execute.call_args.args[0]
    where = sent_query.whereclause
    assert where.left.key == "player_id"
    assert where.right.value == player_id


@pytest.mark.asyncio
async def test_route_history_query_orders_newest_first():
    db = make_async_db([])
    player = make_player()

    await ro_route.get_route_history(10, player, db)

    sent_query = db.execute.call_args.args[0]
    order_by = sent_query._order_by_clauses
    assert len(order_by) == 1
    assert order_by[0].element.key == "created_at"
    assert order_by[0].modifier.__name__ == "desc_op" or "DESC" in str(order_by[0]).upper()


@pytest.mark.asyncio
async def test_route_history_limit_is_honored_below_cap():
    db = make_async_db([])
    player = make_player()

    await ro_route.get_route_history(5, player, db)

    sent_query = db.execute.call_args.args[0]
    assert sent_query._limit_clause.value == 5


@pytest.mark.asyncio
async def test_route_history_limit_is_clamped_at_50():
    """A limit above the 50-row cap must never reach the DB as-is -- the
    route clamps internally even when called directly (bypassing FastAPI's
    own Query(le=50) HTTP-layer validation, as this direct-call test does)."""
    db = make_async_db([])
    player = make_player()

    await ro_route.get_route_history(500, player, db)

    sent_query = db.execute.call_args.args[0]
    assert sent_query._limit_clause.value == 50


# --------------------------------------------------------------------------- #
# Isolation: never another player's rows
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_route_history_never_returns_another_players_rows():
    player_a = uuid4()
    player_b = uuid4()
    now = datetime.now(timezone.utc)

    # The real query's WHERE clause is what actually excludes player B's
    # rows in production (proven above); here the fake DB is wired to
    # behave like a real one WOULD for player A's request -- only player
    # A's row comes back, even though player B has a more recent one.
    row_a = make_run(player_a, created_at=now - timedelta(hours=1))
    db = make_async_db([row_a])
    player = make_player(player_a)

    response = await ro_route.get_route_history(10, player, db)

    assert len(response) == 1
    assert response[0].id == str(row_a.id)
    sent_query = db.execute.call_args.args[0]
    assert sent_query.whereclause.right.value == player_a
    assert sent_query.whereclause.right.value != player_b
