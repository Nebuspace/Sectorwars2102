"""Capital Federation Marshal coverage — Sector 1 hard floor.

Canon (police-forces.md): starter cluster is densest Fed patrol coverage.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.services.npc_movement_service import (
    CAPITAL_WATCH_SQUAD_SIZE,
    FEDERATION_FACTION,
    _region_capital_global_id,
)


@pytest.mark.unit
def test_region_capital_global_id_maps_local_onto_offset_regions():
    region = SimpleNamespace(capital_sector_number=1)
    assert _region_capital_global_id(region, [1, 2, 3]) == 1
    assert _region_capital_global_id(region, [301, 302, 303]) == 301
    region.capital_sector_number = 5
    assert _region_capital_global_id(region, [301, 302, 303, 304, 305]) == 305


@pytest.mark.unit
def test_capital_watch_constants():
    # Squad size must cover a ≤3-waypoint capital route under even phase.
    assert CAPITAL_WATCH_SQUAD_SIZE >= 3
    assert FEDERATION_FACTION == "terran_federation"
