"""WO-P2-econ-blackmarket-venue-spawn Leg C, Part 2 -- nexus-hub placement.

``NexusGenerationService._generate_port_for_sector`` gets a
BLACK_MARKET_FRONTIER_CHANCE roll, ONLY when the calling cluster is
FRONTIER_OUTPOST (``is_frontier=True``), to place venues in "Frontier zone,
Fringe Alliance territory" per black-market.md :22-29. DB-free -- the method
under test builds and returns a plain dict, no ORM session involved.

Statistical, not mocked-RNG: ``_generate_port_for_sector`` uses the module-
level ``random`` (unseeded, matching the rest of this generator's existing
convention), so these tests measure the observed DISTRIBUTION over many
calls rather than forcing a single roll via monkeypatch -- the same
"measure the distribution, don't force the edge case" discipline the WO's
own placement-rate math uses. The is_frontier=False assertion is exact
(zero tolerance) because that path is structurally unreachable, not just
improbable -- the roll is gated behind ``if is_frontier``.
"""
from __future__ import annotations

import pytest

from src.models.station import StationClass, StationType
from src.services.nexus_generation_service import (
    BLACK_MARKET_FRONTIER_CHANCE,
    NexusGenerationService,
)

N = 4000  # large enough to keep the expected-vs-observed band tight without flaking


@pytest.mark.unit
class TestFrontierBlackMarketPlacement:
    def test_non_frontier_never_produces_black_market(self) -> None:
        svc = NexusGenerationService()
        types = {
            svc._generate_port_for_sector(sector_num=i, region_id="r", is_frontier=False).get("type")
            for i in range(2, N)  # sector_num=1 is the special starter branch, skip it
        }
        assert StationType.BLACK_MARKET not in types

    def test_frontier_produces_black_market_near_the_documented_rate(self) -> None:
        svc = NexusGenerationService()
        results = [
            svc._generate_port_for_sector(sector_num=i, region_id="r", is_frontier=True).get("type")
            for i in range(2, N)
        ]
        observed_rate = results.count(StationType.BLACK_MARKET) / len(results)

        # Generous +/-50% relative band around BLACK_MARKET_FRONTIER_CHANCE
        # (0.04 -> [0.02, 0.06]) -- wide enough to never flake on a fair
        # coin at N=4000 (binomial std dev here is ~0.003, so 0.02/0.06 are
        # both >6 standard deviations out), tight enough to catch a real
        # regression (e.g. the roll silently dropped, or wired to the wrong
        # constant).
        assert BLACK_MARKET_FRONTIER_CHANCE * 0.5 <= observed_rate <= BLACK_MARKET_FRONTIER_CHANCE * 1.5, (
            f"observed {observed_rate:.4f}, expected ~{BLACK_MARKET_FRONTIER_CHANCE}"
        )

    def test_black_market_venue_type_and_class_stay_orthogonal(self) -> None:
        """Leg A's whole point: venue TYPE and station CLASS must never be
        coupled again. Every BLACK_MARKET result observed still draws its
        class from the SAME pool as any other frontier port."""
        svc = NexusGenerationService()
        allowed_classes = {
            StationClass.CLASS_4, StationClass.CLASS_5, StationClass.CLASS_6,
            StationClass.CLASS_7, StationClass.CLASS_8,
        }
        black_market_classes = {
            port["station_class"]
            for i in range(2, N)
            if (port := svc._generate_port_for_sector(sector_num=i, region_id="r", is_frontier=True))["type"]
            == StationType.BLACK_MARKET
        }
        assert black_market_classes  # sanity: the roll actually fired at least once at N=4000
        assert black_market_classes <= allowed_classes

    def test_default_is_frontier_false_preserves_the_old_call_shape(self) -> None:
        """The two-positional-arg call shape every OTHER caller (and any
        future one) uses still works unchanged -- is_frontier defaults to
        False, never BLACK_MARKET."""
        svc = NexusGenerationService()
        for i in range(2, 500):
            port = svc._generate_port_for_sector(i, "r")  # positional, no is_frontier
            assert port["type"] != StationType.BLACK_MARKET

    def test_sector_one_starter_branch_is_never_affected(self) -> None:
        """sector_num == 1 is the special Central Nexus Starport Prime
        branch -- it must stay TRADING/CLASS_0 even if is_frontier=True is
        (incorrectly) passed for it; the branch returns before the roll is
        ever reached."""
        svc = NexusGenerationService()
        port = svc._generate_port_for_sector(sector_num=1, region_id="r", is_frontier=True)
        assert port["type"] == StationType.TRADING
        assert port["station_class"] == StationClass.CLASS_0
