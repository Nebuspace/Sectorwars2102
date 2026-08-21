"""LEG-131 — EquipmentRequest OpenAPI Field must list live EQUIPMENT_DEFINITIONS keys."""
from __future__ import annotations

from pathlib import Path

import pytest

from src.services.ship_upgrade_service import ShipUpgradeService


@pytest.mark.unit
def test_equipment_key_openapi_description_lists_live_keys() -> None:
    description = ShipUpgradeService.equipment_key_openapi_description()
    assert "tractor_beam" in description
    assert "ecm_suite" in description
    for key in ShipUpgradeService.EQUIPMENT_DEFINITIONS:
        assert key in description, f"OpenAPI description missing live key: {key}"


@pytest.mark.unit
def test_equipment_request_field_uses_derived_openapi_description() -> None:
    """Pin the route Field to the derived helper (avoids reintroducing a 3-key hardcode)."""
    route_src = (
        Path(__file__).resolve().parents[2]
        / "src"
        / "api"
        / "routes"
        / "ship_upgrades.py"
    ).read_text(encoding="utf-8")
    assert "equipment_key_openapi_description()" in route_src
    assert (
        'description="One of: quantum_harvester, mining_laser, planetary_lander"'
        not in route_src
    )
