"""LEG-3706 — admin_enhanced mutation/list routes must not echo Exception text."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from src.api.routes import admin_enhanced as ae_mod
from src.api.routes.admin_enhanced import (
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
async def test_update_sector_unexpected_is_opaque_500():
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
async def test_create_port_unexpected_is_opaque_500():
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
async def test_create_planet_unexpected_is_opaque_500():
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
async def test_create_enhanced_warp_tunnel_unexpected_is_opaque_500():
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
async def test_get_enhanced_sectors_unexpected_is_opaque_500():
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


def test_admin_enhanced_http500_catches_have_no_detail_str_e():
    """LEG-3706 — static pin: HTTP 500 catch paths stay opaque."""
    src = Path(ae_mod.__file__).read_text(encoding="utf-8")
    assert "route_internal_error" in src
    assert "ERR_ADMIN_ENHANCED_FETCH_SECTORS_FAILED" in src
    assert "ERR_ADMIN_ENHANCED_CREATE_WARP_FAILED" in src
    assert "ERR_ADMIN_ENHANCED_CREATE_PLANET_FAILED" in src
    assert "ERR_ADMIN_ENHANCED_CREATE_PORT_FAILED" in src
    assert "ERR_ADMIN_ENHANCED_UPDATE_SECTOR_FAILED" in src
    assert "Failed to update sector: {str(e)}" not in src
    assert "Failed to create port: {str(e)}" not in src
    assert "Failed to create planet: {str(e)}" not in src
    assert "Failed to create warp tunnel: {str(e)}" not in src
    assert "Failed to fetch enhanced sectors: {str(e)}" not in src
