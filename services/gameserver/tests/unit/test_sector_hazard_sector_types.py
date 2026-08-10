"""cycle-50 — RADIATION_ZONE / WARP_STORM on live SectorType enum."""
from __future__ import annotations

import pytest

from src.models.sector import SectorType


@pytest.mark.unit
class TestSectorHazardTypesOnLiveEnum:
    def test_radiation_zone_on_sector_type(self):
        assert SectorType.RADIATION_ZONE.value == "RADIATION_ZONE"
        assert "RADIATION_ZONE" in {m.name for m in SectorType}

    def test_warp_storm_on_sector_type(self):
        assert SectorType.WARP_STORM.value == "WARP_STORM"
        assert "WARP_STORM" in {m.name for m in SectorType}
