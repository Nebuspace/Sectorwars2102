"""Regression pin for the NH18 admin route-optimization feed (WO-SB-RO2 Lane B).

Pins GET /admin/ai/route-optimization (admin_comprehensive.py) against a real
read of route_optimization_runs: the exact payload keys
RouteOptimizationDisplay.tsx already consumes (id/playerId/playerName/
startSector/route/estimatedProfit/estimatedTime/efficiency/status, plus
optimization_stats), the efficiency clamp, the nickname-or-username
fallback, and the "zero rows -> [] + None, never fabricated zeros" rule.

DB-free: `db` is a MagicMock whose .query(...) chains are stubbed per call,
in the exact order the route issues them (count, runs, players, averages,
active-window count).
"""
import inspect
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from src.api.routes import admin_comprehensive as admin_route


def make_db(*query_results):
    """A MagicMock Session whose db.query(...) returns `query_results` in
    call order (the route never queries the same model shape twice with
    ambiguous routing, so a flat ordered list is sufficient)."""
    db = MagicMock()
    db.query.side_effect = list(query_results)
    return db


def _count_query(value):
    """A MagicMock .query(...) chain: .scalar() -> value."""
    q = MagicMock()
    q.scalar.return_value = value
    return q


def _runs_query(runs):
    """A MagicMock .query(...) chain: order_by().limit().all() -> runs."""
    q = MagicMock()
    q.order_by.return_value.limit.return_value.all.return_value = runs
    return q


def _players_query(players):
    """A MagicMock .query(...) chain: filter().all() -> players."""
    q = MagicMock()
    q.filter.return_value.all.return_value = players
    return q


def _avg_query(avg_efficiency, avg_profit):
    """A MagicMock .query(...) chain: .one() -> (avg_efficiency, avg_profit)."""
    q = MagicMock()
    q.one.return_value = (avg_efficiency, avg_profit)
    return q


def _active_count_query(value):
    """A MagicMock .query(...) chain: filter().scalar() -> value."""
    q = MagicMock()
    q.filter.return_value.scalar.return_value = value
    return q


def make_run(**overrides):
    defaults = dict(
        id=uuid4(),
        player_id=uuid4(),
        objective="balanced",
        start_sector="1",
        end_sector=None,
        sectors=["1", "2", "3"],
        total_profit=500.0,
        total_distance=2,
        total_time_hours=1.5,
        cargo_efficiency=0.8,
        route_confidence=0.9,
        status="completed",
        created_at=datetime.now(timezone.utc),
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def make_admin():
    return SimpleNamespace(username="admin")


# --------------------------------------------------------------------------- #
# Empty table -> honest empty, never fabricated zeros
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_zero_rows_returns_empty_list_and_null_stats():
    db = make_db(_count_query(0))

    result = await admin_route.get_ai_route_optimization_data(make_admin(), db)

    assert result == {"active_optimizations": [], "optimization_stats": None}
    # Only the count query fires — no averages computed against an empty table.
    assert db.query.call_count == 1


def test_stale_no_engine_comment_is_gone():
    source = inspect.getsource(admin_route.get_ai_route_optimization_data)
    assert "No route optimization engine exists" not in source


# --------------------------------------------------------------------------- #
# Non-empty table -> real payload, all display fields mapped
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_nonzero_rows_maps_all_display_fields():
    player_id = uuid4()
    run = make_run(player_id=player_id)
    player = SimpleNamespace(id=player_id, username="Trader Jane")

    db = make_db(
        _count_query(1),
        _runs_query([run]),
        _players_query([player]),
        _avg_query(0.8, 500.0),
        _active_count_query(1),
    )

    result = await admin_route.get_ai_route_optimization_data(make_admin(), db)

    row = result["active_optimizations"][0]
    assert row["id"] == str(run.id)
    assert row["playerId"] == str(player_id)
    assert row["playerName"] == "Trader Jane"
    assert row["startSector"] == "1"
    assert row["route"] == ["1", "2", "3"]
    assert row["estimatedProfit"] == 500.0
    assert row["estimatedTime"] == 1.5
    assert row["efficiency"] == 80
    assert row["status"] == "completed"

    stats = result["optimization_stats"]
    assert stats["total_routes_optimized"] == 1
    assert stats["avg_efficiency_improvement"] == 80.0
    assert stats["avg_profit_increase"] == 500.0
    assert stats["active_optimizations"] == 1


@pytest.mark.asyncio
async def test_efficiency_clamped_to_0_100():
    player_id = uuid4()
    run = make_run(player_id=player_id, cargo_efficiency=1.5)  # would be 150%
    player = SimpleNamespace(id=player_id, username="Trader Jane")

    db = make_db(
        _count_query(1),
        _runs_query([run]),
        _players_query([player]),
        _avg_query(1.5, 500.0),
        _active_count_query(0),
    )

    result = await admin_route.get_ai_route_optimization_data(make_admin(), db)

    assert result["active_optimizations"][0]["efficiency"] == 100


@pytest.mark.asyncio
async def test_unknown_player_falls_back_to_unknown_label():
    run = make_run()  # no matching Player row for this player_id

    db = make_db(
        _count_query(1),
        _runs_query([run]),
        _players_query([]),
        _avg_query(0.8, 500.0),
        _active_count_query(0),
    )

    result = await admin_route.get_ai_route_optimization_data(make_admin(), db)

    assert result["active_optimizations"][0]["playerName"] == "Unknown"
