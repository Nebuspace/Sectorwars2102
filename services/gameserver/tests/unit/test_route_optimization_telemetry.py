"""Regression pin for route-optimization telemetry (WO-SB-RO2 Lane A).

Pins the non-fatal run-log write in both player-facing optimizer endpoints:
``POST /api/v1/routes/optimize`` (route_optimizer.py) and
``POST /api/v1/ai/optimize-route`` (ai.py). Recording must fire on every
SUCCESSFUL response and must never fail the player's request even if the
insert itself blows up.

Both route functions are plain async functions — dependencies (``current_player``,
``db``) are just call arguments once ``Depends(...)`` resolution is bypassed —
so they're invoked directly against a fully mocked AsyncSession. No DB or
container required.
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from fastapi import HTTPException

from src.api.routes import route_optimizer as ro_route
from src.api.routes import ai as ai_route
from src.models.route_optimization_run import RouteOptimizationRun
from src.services.route_optimizer import OptimizedRoute
from src.services.ai_trading_service import OptimalRoute


def make_async_db():
    """AsyncSession stand-in: db.add is sync, commit/rollback are awaited."""
    db = MagicMock()
    db.add = MagicMock()
    db.commit = AsyncMock()
    db.rollback = AsyncMock()
    return db


def make_player():
    return SimpleNamespace(id=uuid4())


def _fake_optimized_route(**overrides):
    defaults = dict(
        sectors=["1", "4", "7"],
        opportunities=[],
        total_profit=1200.0,
        total_distance=2,
        total_time_hours=3.0,
        total_risk=0.2,
        cargo_efficiency=0.75,
        profit_per_hour=400.0,
        route_confidence=0.85,
        route_type="linear",
    )
    defaults.update(overrides)
    return OptimizedRoute(**defaults)


# --------------------------------------------------------------------------- #
# _record_optimization_run helper (route_optimizer.py)
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_record_optimization_run_success_commits_row():
    db = make_async_db()
    player_id = uuid4()

    await ro_route._record_optimization_run(
        db,
        player_id=player_id,
        objective="balanced",
        start_sector="1",
        end_sector=None,
        sectors=["1", "2", "3"],
        total_profit=500.0,
        total_distance=2,
        total_time_hours=1.5,
        cargo_efficiency=0.8,
        route_confidence=0.9,
    )

    db.add.assert_called_once()
    row = db.add.call_args.args[0]
    assert isinstance(row, RouteOptimizationRun)
    assert row.player_id == player_id
    assert row.objective == "balanced"
    assert row.sectors == ["1", "2", "3"]
    db.commit.assert_awaited_once()
    db.rollback.assert_not_called()


@pytest.mark.asyncio
async def test_record_optimization_run_commit_failure_is_non_fatal():
    """A logging/insert failure must never raise into the caller."""
    db = make_async_db()
    db.commit.side_effect = RuntimeError("db is down")

    # Must not raise.
    await ro_route._record_optimization_run(
        db,
        player_id=uuid4(),
        objective="profit",
        start_sector="1",
        end_sector=None,
        sectors=["1"],
        total_profit=0.0,
        total_distance=0,
        total_time_hours=0.0,
        cargo_efficiency=0.0,
        route_confidence=0.0,
    )

    db.rollback.assert_awaited_once()


# --------------------------------------------------------------------------- #
# POST /routes/optimize — 'shortest' objective
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_optimize_route_shortest_success_records_one_run(monkeypatch):
    db = make_async_db()
    player = make_player()

    monkeypatch.setattr(
        ro_route.RouteOptimizer,
        "find_shortest_path",
        AsyncMock(return_value=[1, 2, 3]),
    )

    request = ro_route.RouteOptimizeRequest(
        start_sector_id="1", end_sector_id="3", objective="shortest",
    )
    response = await ro_route.optimize_route(request, player, db)

    db.add.assert_called_once()
    row = db.add.call_args.args[0]
    assert row.objective == "shortest"
    assert row.sectors == response.sectors == ["1", "2", "3"]
    assert row.player_id == player.id
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_optimize_route_shortest_not_found_records_zero_runs(monkeypatch):
    db = make_async_db()
    player = make_player()

    monkeypatch.setattr(
        ro_route.RouteOptimizer,
        "find_shortest_path",
        AsyncMock(return_value=None),
    )

    request = ro_route.RouteOptimizeRequest(
        start_sector_id="1", end_sector_id="99", objective="shortest",
    )
    with pytest.raises(HTTPException) as exc_info:
        await ro_route.optimize_route(request, player, db)

    assert exc_info.value.status_code == 404
    db.add.assert_not_called()
    db.commit.assert_not_called()


# --------------------------------------------------------------------------- #
# POST /routes/optimize — trading objectives (profit/risk/balanced)
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_optimize_route_balanced_success_records_one_run(monkeypatch):
    db = make_async_db()
    player = make_player()
    route = _fake_optimized_route()

    monkeypatch.setattr(
        ro_route.RouteOptimizer,
        "find_optimal_route",
        AsyncMock(return_value=route),
    )

    request = ro_route.RouteOptimizeRequest(start_sector_id="1", objective="balanced")
    response = await ro_route.optimize_route(request, player, db)

    db.add.assert_called_once()
    row = db.add.call_args.args[0]
    assert row.objective == "balanced"
    assert row.sectors == route.sectors == response.sectors
    assert row.total_profit == route.total_profit
    assert row.cargo_efficiency == route.cargo_efficiency
    assert row.route_confidence == route.route_confidence
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_optimize_route_balanced_no_route_records_zero_runs(monkeypatch):
    db = make_async_db()
    player = make_player()

    monkeypatch.setattr(
        ro_route.RouteOptimizer,
        "find_optimal_route",
        AsyncMock(return_value=None),
    )

    request = ro_route.RouteOptimizeRequest(start_sector_id="1", objective="balanced")
    with pytest.raises(HTTPException) as exc_info:
        await ro_route.optimize_route(request, player, db)

    assert exc_info.value.status_code == 404
    db.add.assert_not_called()


@pytest.mark.asyncio
async def test_optimize_route_insert_failure_is_non_fatal(monkeypatch):
    """A DB failure recording telemetry must not fail the player's
    successful optimize response."""
    db = make_async_db()
    db.commit.side_effect = RuntimeError("db is down")
    player = make_player()
    route = _fake_optimized_route()

    monkeypatch.setattr(
        ro_route.RouteOptimizer,
        "find_optimal_route",
        AsyncMock(return_value=route),
    )

    request = ro_route.RouteOptimizeRequest(start_sector_id="1", objective="profit")
    response = await ro_route.optimize_route(request, player, db)

    assert response.sectors == route.sectors
    db.rollback.assert_awaited_once()


# --------------------------------------------------------------------------- #
# POST /ai/optimize-route — 'ai_trading' objective
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_ai_optimize_route_success_records_ai_trading_run(monkeypatch):
    db = make_async_db()
    player = make_player()
    optimal_route = OptimalRoute(
        sectors=["2", "5"],
        total_profit=300.0,
        total_distance=1,
        estimated_time=45,
        risk_score=0.1,
        commodity_chain=[],
    )

    fake_service = SimpleNamespace(
        optimize_trade_route=AsyncMock(return_value=optimal_route)
    )
    monkeypatch.setattr(ai_route, "get_ai_service", lambda: fake_service)

    request = ai_route.RouteOptimizationRequest(
        start_sector="2", cargo_capacity=50, max_stops=3
    )
    result = await ai_route.optimize_trading_route(request, player, db)

    db.add.assert_called_once()
    row = db.add.call_args.args[0]
    assert row.objective == "ai_trading"
    assert row.sectors == optimal_route.sectors == result["sectors"]
    assert row.player_id == player.id
    # OptimalRoute.estimated_time is MINUTES (ai_trading_service.py:104) —
    # the recorded column is hours, like every other objective.
    assert row.total_time_hours == pytest.approx(optimal_route.estimated_time / 60.0)
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_ai_optimize_route_engine_error_records_zero_runs(monkeypatch):
    db = make_async_db()
    player = make_player()

    fake_service = SimpleNamespace(
        optimize_trade_route=AsyncMock(side_effect=ValueError("boom"))
    )
    monkeypatch.setattr(ai_route, "get_ai_service", lambda: fake_service)

    request = ai_route.RouteOptimizationRequest(
        start_sector="2", cargo_capacity=50, max_stops=3
    )
    with pytest.raises(HTTPException) as exc_info:
        await ai_route.optimize_trading_route(request, player, db)

    assert exc_info.value.status_code == 500
    db.add.assert_not_called()


@pytest.mark.asyncio
async def test_ai_optimize_route_insert_failure_is_non_fatal(monkeypatch):
    """A DB failure recording telemetry must not fail the player's
    successful optimize response."""
    db = make_async_db()
    db.commit.side_effect = RuntimeError("db is down")
    player = make_player()
    optimal_route = OptimalRoute(
        sectors=["2", "5"],
        total_profit=300.0,
        total_distance=1,
        estimated_time=45,
        risk_score=0.1,
        commodity_chain=[],
    )
    fake_service = SimpleNamespace(
        optimize_trade_route=AsyncMock(return_value=optimal_route)
    )
    monkeypatch.setattr(ai_route, "get_ai_service", lambda: fake_service)

    request = ai_route.RouteOptimizationRequest(
        start_sector="2", cargo_capacity=50, max_stops=3
    )
    result = await ai_route.optimize_trading_route(request, player, db)

    assert result["sectors"] == optimal_route.sectors
    db.rollback.assert_awaited_once()
