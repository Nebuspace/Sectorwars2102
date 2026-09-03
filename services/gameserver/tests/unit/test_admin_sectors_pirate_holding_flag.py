"""LEG-4182: pin has_pirate_holding on GET /api/v1/admin/sectors list.

DB-free source pins — live admin auth/DB coverage remains the integration
suite. Does not invent outlaw_base_id / interior_sector_ids / composition.
"""
from __future__ import annotations

from pathlib import Path

import pytest

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
