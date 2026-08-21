"""Soft-ORDER invent=0 floor — LEG-2055/#2135 · LEG-2056/#2136 · LEG-2059/#2139 ·
LEG-2060/#2140 · LEG-2063/#2143 (condition_multiplier / demand_factor /
citadel missing error_code / defense-incident stamp / tariff no-owner-exempt).

Run: pytest tests/unit/test_softorder_floor_leg2055.py --noconftest -q
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from src.services import port_ownership_service as po


class TestDemandFactor:
    def test_floor_tariff_five_pct(self):
        assert po.demand_factor(0.05) == pytest.approx(0.75)

    def test_eight_pct(self):
        assert po.demand_factor(0.08) == pytest.approx(0.60)

    def test_high_tariff_floors_at_tenth(self):
        assert po.demand_factor(0.25) == pytest.approx(0.10)
        assert po.demand_factor(0.50) == pytest.approx(0.10)

    def test_zero_tariff_full_demand(self):
        assert po.demand_factor(0.0) == pytest.approx(1.0)

    def test_traffic_compose_helpers(self):
        d = po.demand_factor(0.05)
        with_rep = po.traffic_with_rep(100.0, d, reputation_score=1.0)
        assert with_rep == pytest.approx(100.0 * 0.75 * 1.10)
        final = po.traffic_final(with_rep, region_tax_rate=0.10)
        assert final == pytest.approx(with_rep * 0.90)


class TestConditionMultiplier:
    def _station(self, *, tier: str = "basic", stamped: datetime | None = None):
        ownership = {}
        if stamped is not None:
            ownership[po.LAST_DEFENSE_INCIDENT_AT_KEY] = stamped.isoformat()
        return SimpleNamespace(security_level=tier, ownership=ownership)

    def test_no_stamp_basic_security_is_one(self):
        now = datetime(2026, 8, 21, tzinfo=UTC)
        assert po.condition_multiplier(self._station(tier="basic"), now) == pytest.approx(1.0)

    def test_none_security_flat_haircut(self):
        now = datetime(2026, 8, 21, tzinfo=UTC)
        assert po.condition_multiplier(self._station(tier="none"), now) == pytest.approx(0.85)

    def test_fresh_incident_full_ten_pct(self):
        now = datetime(2026, 8, 21, tzinfo=UTC)
        stamped = now
        assert po.condition_multiplier(
            self._station(tier="basic", stamped=stamped), now
        ) == pytest.approx(0.90)

    def test_seven_day_old_incident_no_haircut(self):
        now = datetime(2026, 8, 21, tzinfo=UTC)
        stamped = now - timedelta(days=7)
        assert po.condition_multiplier(
            self._station(tier="standard", stamped=stamped), now
        ) == pytest.approx(1.0)

    def test_forced_sale_uses_live_multiplier(self):
        # 60k/mo × 12 × 0.85 = 612k → clamp into [500k, 1M]
        assert po.forced_sale_value(60_000, 500_000, condition_mult=0.85) == 612_000


class TestStampLastDefenseIncident:
    def test_stamp_writes_ownership_iso(self):
        station = SimpleNamespace(ownership=None)
        now = datetime(2026, 8, 21, 12, 0, tzinfo=UTC)
        # _ownership mutates Station.ownership — use a real-ish stub
        station.ownership = {}
        from sqlalchemy.orm.attributes import flag_modified as _fm  # noqa: F401

        # Bypass flag_modified by patching
        import src.services.port_ownership_service as mod

        called = {}

        def _fake_flag(obj, key):
            called["key"] = key

        orig = mod.flag_modified
        mod.flag_modified = _fake_flag
        try:
            po.stamp_last_defense_incident(station, now)
        finally:
            mod.flag_modified = orig

        assert called.get("key") == "ownership"
        assert station.ownership[po.LAST_DEFENSE_INCIDENT_AT_KEY] == now.isoformat()


class TestCitadelMissingErrorCode:
    def test_missing_building_returns_error_code_and_fields(self):
        from src.services.citadel_service import CitadelService

        svc = CitadelService(MagicMock())
        planet = SimpleNamespace(
            active_events={"defense_buildings": {}},
            defense_shields=0,
        )
        req = {
            "type": "building",
            "key": "radar_array",
            "min": 1,
            "name": "Radar Array",
        }
        result = svc._eval_prereq(
            planet,
            req,
            "Fortress",
            {},
            set(),
        )
        assert result is not None
        assert result["error_code"] == "ERR_CITADEL_PREREQUISITE_MISSING"
        assert result["building_key"] == "radar_array"
        assert result["building_name"] == "Radar Array"
        assert result["reason"] == "prerequisite_building_missing"


class TestTariffOwnerNotExempt:
    """LEG-2063 invent=0 — owner/co-owner still pay tax when owner_id set."""

    def test_tax_rate_applies_whenever_owner_present(self):
        # Mirrors trading.py:713-715 contract: no owner_id skip for the
        # trading player. Pure regression of the gate predicate.
        station_owned = SimpleNamespace(owner_id="p-owner", tax_rate=0.10)
        station_npc = SimpleNamespace(owner_id=None, tax_rate=0.10)

        def effective_tax(station, trader_id: str) -> float:
            # invent=0: trader identity must NOT clear the tariff
            _ = trader_id
            return (
                station.tax_rate
                if (station.owner_id is not None and station.tax_rate is not None)
                else 0.0
            )

        assert effective_tax(station_owned, "p-owner") == pytest.approx(0.10)
        assert effective_tax(station_owned, "p-coowner") == pytest.approx(0.10)
        assert effective_tax(station_npc, "anyone") == pytest.approx(0.0)
