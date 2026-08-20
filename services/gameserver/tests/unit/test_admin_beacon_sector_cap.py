"""LEG-1043 — admin GET/PATCH /regions/{id}/beacon-sector-cap (DB-free)."""
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from src.services import message_beacon_service as mbs
from src.services.message_beacon_service import BeaconError


def _region(**kwargs):
    defaults = dict(id=uuid4(), trade_bonuses={})
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


class TestSectorCapHelpers:
    def test_default_when_absent(self):
        region = _region(trade_bonuses={})
        payload = mbs.get_region_beacon_sector_cap(region)
        assert payload["beacon_sector_cap"] == mbs.DEFAULT_SECTOR_CAP
        assert payload["default_cap"] == 10
        assert payload["max_cap"] == 50
        assert payload["configured_raw"] is None
        assert payload["region_id"] == str(region.id)

    def test_reads_configured_value(self):
        region = _region(trade_bonuses={mbs.REGION_BEACON_CAP_KEY: 15})
        payload = mbs.get_region_beacon_sector_cap(region)
        assert payload["beacon_sector_cap"] == 15
        assert payload["configured_raw"] == 15

    def test_clamps_malformed_raw_to_default_band(self):
        region = _region(trade_bonuses={mbs.REGION_BEACON_CAP_KEY: "nope"})
        assert mbs._sector_cap(region) == mbs.DEFAULT_SECTOR_CAP
        region.trade_bonuses = {mbs.REGION_BEACON_CAP_KEY: 999}
        assert mbs._sector_cap(region) == mbs.MAX_SECTOR_CAP


class TestSetBeaconSectorCap:
    def test_persists_clamped_value(self):
        region = _region(trade_bonuses={"tariff_rate": 0.05})
        db = MagicMock()
        with patch.object(mbs, "flag_modified") as flag:
            result = mbs.set_region_beacon_sector_cap(db, region, 20)
        assert result["beacon_sector_cap"] == 20
        assert result["configured_raw"] == 20
        assert region.trade_bonuses[mbs.REGION_BEACON_CAP_KEY] == 20
        assert region.trade_bonuses["tariff_rate"] == 0.05
        flag.assert_called_once_with(region, "trade_bonuses")
        db.flush.assert_called_once()

    def test_rejects_below_one(self):
        region = _region()
        db = MagicMock()
        with pytest.raises(BeaconError, match="between 1 and 50"):
            mbs.set_region_beacon_sector_cap(db, region, 0)

    def test_rejects_above_max(self):
        region = _region()
        db = MagicMock()
        with pytest.raises(BeaconError, match="between 1 and 50"):
            mbs.set_region_beacon_sector_cap(db, region, 51)

    def test_rejects_bool(self):
        region = _region()
        db = MagicMock()
        with pytest.raises(BeaconError, match="integer"):
            mbs.set_region_beacon_sector_cap(db, region, True)  # type: ignore[arg-type]


class TestAdminRouteRegistration:
    def test_routes_registered_on_admin_router(self):
        from src.api.routes import admin as admin_routes

        paths = {
            getattr(r, "path", None)
            for r in admin_routes.router.routes
            if getattr(r, "path", None)
        }
        assert "/regions/{region_id}/beacon-sector-cap" in paths
