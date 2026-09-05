"""LEG-4182 / LEG-4198: has_pirate_holding payload + list filter.

DB-free source pins — live admin auth/DB coverage remains the integration
suite. Does not invent outlaw_base_id / interior_sector_ids / composition.
"""
from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from src.api.routes.admin import get_all_sectors

_ADMIN_ROUTE = (
    Path(__file__).resolve().parents[2] / "src" / "api" / "routes" / "admin.py"
)


@pytest.mark.unit
def test_admin_sectors_list_includes_has_pirate_holding_boolean() -> None:
    text = _ADMIN_ROUTE.read_text()
    # Narrow to the list-payload builder (has_port / has_planet siblings).
    assert "has_port = db.query(Station)" in text
    assert "has_planet = db.query(Planet)" in text
    assert "has_pirate_holding" in text
    assert "PirateHolding.sector_id == sector.sector_id" in text
    assert '"has_pirate_holding": has_pirate_holding' in text
    # Out of scope: lodging conversion fields must not appear on this list row.
    payload = text.split("sector_list.append({")[1].split("})")[0]
    assert "outlaw_base_id" not in payload
    assert "interior_sector_ids" not in payload
    assert "composition_profile" not in payload


@pytest.mark.unit
def test_admin_imports_pirate_holding_model() -> None:
    text = _ADMIN_ROUTE.read_text()
    assert "from src.models.pirate_holding import PirateHolding" in text


@pytest.mark.unit
def test_admin_sectors_accepts_filter_has_pirate_holding_param() -> None:
    sig = inspect.signature(get_all_sectors)
    assert "filter_has_pirate_holding" in sig.parameters
    param = sig.parameters["filter_has_pirate_holding"]
    assert param.default is None


@pytest.mark.unit
def test_admin_sectors_filter_has_pirate_holding_mirrors_port_planet() -> None:
    text = _ADMIN_ROUTE.read_text()
    # Signature accepts the query param.
    assert "filter_has_pirate_holding: Optional[bool] = None" in text
    # Subquery style matches port/planet (include when true, exclude when false).
    assert "db.query(PirateHolding.sector_id).distinct().subquery()" in text
    assert "if filter_has_pirate_holding:" in text
    assert "query.filter(Sector.sector_id.in_(holding_sectors))" in text
    assert "query.filter(~Sector.sector_id.in_(holding_sectors))" in text
    # Port/planet siblings still present — filter is additive, not a rewrite.
    assert "filter_has_port: Optional[bool] = None" in text
    assert "filter_has_planet: Optional[bool] = None" in text
