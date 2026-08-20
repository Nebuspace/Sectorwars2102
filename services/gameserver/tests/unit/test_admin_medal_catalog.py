"""LEG-355: GET /medals/admin/catalog — DB-free pins for MedalAdmin."""
from __future__ import annotations

import inspect
from unittest.mock import MagicMock

import pytest
from fastapi.params import Depends

from src.api.routes import medals as medals_routes
from src.api.routes.medals import admin_list_medal_catalog
from src.auth.admin_scopes import PLAYERS_VIEW
from src.services.medal_catalog import MEDAL_CATALOG


@pytest.mark.asyncio
async def test_admin_catalog_returns_seeded_medals():
    admin = MagicMock()
    result = await admin_list_medal_catalog(admin=admin)
    assert result["total"] == len(MEDAL_CATALOG)
    assert result["total"] > 0
    assert len(result["items"]) == result["total"]
    sample = result["items"][0]
    for key in ("id", "name", "category", "tier", "description", "criteria"):
        assert key in sample
    assert {item["id"] for item in result["items"]} == set(MEDAL_CATALOG.keys())


def test_admin_catalog_route_gated_players_view():
    """Route must use require_scope(PLAYERS_VIEW) — same gate MedalAdmin documents."""
    sig = inspect.signature(admin_list_medal_catalog)
    depends = sig.parameters["admin"].default
    assert isinstance(depends, Depends)
    # require_scope(scope) closes over scope in cells
    closure = depends.dependency.__closure__
    assert closure is not None
    closed = {cell.cell_contents for cell in closure}
    assert PLAYERS_VIEW in closed
    # Router registers the path MedalAdmin calls
    paths = {getattr(r, "path", None) for r in medals_routes.router.routes}
    assert "/admin/catalog" in paths or any(
        (getattr(r, "path", "") or "").endswith("/admin/catalog")
        for r in medals_routes.router.routes
    )
