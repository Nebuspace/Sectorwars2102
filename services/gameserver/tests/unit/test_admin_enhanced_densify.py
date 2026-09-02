"""LEG-3876 densify — admin_enhanced mutation/list routes must not echo Exception text."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from src.api.routes import admin_enhanced as ae_mod
from src.api.routes.admin_enhanced import (
    ERR_ADMIN_ENHANCED_CREATE_PLANET_FAILED,
    ERR_ADMIN_ENHANCED_CREATE_PORT_FAILED,
    ERR_ADMIN_ENHANCED_CREATE_WARP_FAILED,
    ERR_ADMIN_ENHANCED_FETCH_SECTORS_FAILED,
    ERR_ADMIN_ENHANCED_UPDATE_SECTOR_FAILED,
    PlanetCreateRequest,
    SectorUpdateRequest,
    StationCreateRequest,
    WarpTunnelEnhancedRequest,
    create_enhanced_warp_tunnel,
    create_planet,
    create_port,
    get_enhanced_sectors,
    update_sector,
)


class _BoomDB:
    def query(self, *args, **kwargs):
        raise RuntimeError("secret-enhanced-query-should-not-leak")


@pytest.mark.asyncio
async def test_update_sector_returns_structured_500():
    with pytest.raises(HTTPException) as excinfo:
        await update_sector(
            sector_id=1,
            request=SectorUpdateRequest(),
            current_admin=SimpleNamespace(id=1),
            db=_BoomDB(),
        )

    exc = excinfo.value
    assert exc.status_code == 500
    assert exc.detail == {
        "error_code": "ERR_ADMIN_ENHANCED_UPDATE_SECTOR_FAILED",
        "detail": "Failed to update sector",
    }
    assert "secret-enhanced-query-should-not-leak" not in str(exc.detail)


@pytest.mark.asyncio
async def test_create_port_returns_structured_500():
    request = StationCreateRequest(
        sector_id=1,
        name="Test Port",
        port_class=1,
        commodities={},
        services={},
        defense_drones=0,
        has_turrets=False,
        tax_rate=0.0,
    )
    with pytest.raises(HTTPException) as excinfo:
        await create_port(
            request=request,
            current_admin=SimpleNamespace(id=1),
            db=_BoomDB(),
        )

    exc = excinfo.value
    assert exc.status_code == 500
    assert exc.detail == {
        "error_code": "ERR_ADMIN_ENHANCED_CREATE_PORT_FAILED",
        "detail": "Failed to create port",
    }
    assert "secret-enhanced-query-should-not-leak" not in str(exc.detail)


@pytest.mark.asyncio
async def test_create_planet_returns_structured_500():
    request = PlanetCreateRequest(
        sector_id=1,
        name="Test Planet",
        planet_type="terran",
        colonists={},
        production_rates={},
        breeding_rate=1,
        citadel_level=0,
        shield_level=0,
        fighters=0,
    )
    with pytest.raises(HTTPException) as excinfo:
        await create_planet(
            request=request,
            current_admin=SimpleNamespace(id=1),
            db=_BoomDB(),
        )

    exc = excinfo.value
    assert exc.status_code == 500
    assert exc.detail == {
        "error_code": "ERR_ADMIN_ENHANCED_CREATE_PLANET_FAILED",
        "detail": "Failed to create planet",
    }
    assert "secret-enhanced-query-should-not-leak" not in str(exc.detail)


@pytest.mark.asyncio
async def test_create_enhanced_warp_tunnel_returns_structured_500():
    request = WarpTunnelEnhancedRequest(
        source_sector_id=1,
        target_sector_id=2,
        tunnel_type="natural",
        is_one_way=False,
        stability=80,
        turn_cost=1,
        access_control="public",
    )
    with pytest.raises(HTTPException) as excinfo:
        await create_enhanced_warp_tunnel(
            request=request,
            current_admin=SimpleNamespace(id=1),
            db=_BoomDB(),
        )

    exc = excinfo.value
    assert exc.status_code == 500
    assert exc.detail == {
        "error_code": "ERR_ADMIN_ENHANCED_CREATE_WARP_FAILED",
        "detail": "Failed to create warp tunnel",
    }
    assert "secret-enhanced-query-should-not-leak" not in str(exc.detail)


@pytest.mark.asyncio
async def test_get_enhanced_sectors_returns_structured_500():
    with pytest.raises(HTTPException) as excinfo:
        await get_enhanced_sectors(
            current_admin=SimpleNamespace(),
            db=_BoomDB(),
        )

    exc = excinfo.value
    assert exc.status_code == 500
    assert exc.detail == {
        "error_code": "ERR_ADMIN_ENHANCED_FETCH_SECTORS_FAILED",
        "detail": "Failed to fetch enhanced sectors",
    }
    assert "secret-enhanced-query-should-not-leak" not in str(exc.detail)


def test_admin_enhanced_http500_catches_are_structured():
    """LEG-3876 — static pin."""
    src = Path(ae_mod.__file__).read_text(encoding="utf-8")
    for code in (
        ERR_ADMIN_ENHANCED_UPDATE_SECTOR_FAILED,
        ERR_ADMIN_ENHANCED_CREATE_PORT_FAILED,
        ERR_ADMIN_ENHANCED_CREATE_PLANET_FAILED,
        ERR_ADMIN_ENHANCED_CREATE_WARP_FAILED,
        ERR_ADMIN_ENHANCED_FETCH_SECTORS_FAILED,
    ):
        assert code in src
    assert src.count("route_internal_error(") >= 5
    assert 'detail="Failed to update sector"' not in src
