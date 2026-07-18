"""
Unit tests for Central Nexus functionality
Tests the core business logic for Central Nexus generation and management
"""

import pytest
import uuid
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

from collections import Counter

from src.services.nexus_generation_service import NexusGenerationService
from src.models.cluster import ClusterType
from src.models.region import Region
from src.models.sector import Sector
from src.models.planet import Planet
from src.models.station import Station


# ---------------------------------------------------------------------------
# LOCKED — the ratified GX1 20-cluster Central Nexus table (4/2/6/5/3 remix).
#
# Canonical spec: sw2102-docs/SYSTEMS/central-nexus-clusters.md §"Cluster table".
# Transcribed EXACTLY in index order 1..20 — (name, ClusterType, grid (x, y)).
# These are LOCKED so a future drift in nexus_generation_service fails here.
# ---------------------------------------------------------------------------
NEXUS_CLUSTER_TABLE = [
    ("Commerce Central Hub", ClusterType.TRADE_HUB, (0, 0)),          # 1  ANCHOR
    ("Diplomatic Quarter", ClusterType.POPULATION_CENTER, (1, 0)),    # 2
    ("Industrial Complex", ClusterType.TRADE_HUB, (2, 0)),            # 3
    ("Prospect Belt", ClusterType.RESOURCE_RICH, (3, 0)),             # 4
    ("Drift Reaches", ClusterType.FRONTIER_OUTPOST, (4, 0)),          # 5
    ("Outer Survey Station", ClusterType.FRONTIER_OUTPOST, (0, 1)),   # 6
    ("Free Trade Zone", ClusterType.TRADE_HUB, (1, 1)),              # 7
    ("Lodestar Reach", ClusterType.RESOURCE_RICH, (2, 1)),           # 8
    ("Quiet Quarter", ClusterType.STANDARD, (3, 1)),                # 9
    ("Gateway Plaza", ClusterType.STANDARD, (4, 1)),                # 10 ANCHOR
    ("Settlers' Rest", ClusterType.POPULATION_CENTER, (0, 2)),       # 11
    ("Transit Junction", ClusterType.STANDARD, (1, 2)),             # 12
    ("Slag Fields", ClusterType.RESOURCE_RICH, (2, 2)),             # 13
    ("Starport Complex", ClusterType.TRADE_HUB, (3, 2)),            # 14
    ("Marker's Edge", ClusterType.FRONTIER_OUTPOST, (4, 2)),        # 15
    ("The Bazaar", ClusterType.STANDARD, (0, 3)),                  # 16
    ("Lonesome Span", ClusterType.FRONTIER_OUTPOST, (1, 3)),       # 17
    ("Wayfarer Hollow", ClusterType.STANDARD, (2, 3)),            # 18
    ("Merchant's Row", ClusterType.STANDARD, (3, 3)),             # 19
    ("Frontier Gateway", ClusterType.FRONTIER_OUTPOST, (4, 3)),    # 20
]


class TestNexusClusterTable:
    """LOCKED assertions of the ratified GX1 20-cluster Central Nexus table.

    These call the real ``_create_nexus_clusters`` and lock its output 1:1 to the
    FROZEN ratified remix (4 TRADE_HUB / 2 POPULATION_CENTER / 6 STANDARD /
    5 FRONTIER_OUTPOST / 3 RESOURCE_RICH). They replace the prior 8/4/8 mix as the
    canonical Nexus cluster expectation; a future drift fails here so it cannot
    ship silently.
    """

    @pytest.fixture
    def nexus_service(self):
        return NexusGenerationService()

    async def _clusters(self, nexus_service):
        # _create_nexus_clusters does session.add(...) (sync) then await flush().
        session = AsyncMock()
        return await nexus_service._create_nexus_clusters(session, "region-uuid")

    @pytest.mark.asyncio
    async def test_cluster_table_matches_ratified_remix_1to1(self, nexus_service):
        """1:1 lock: name + type + grid (x, y) in index order 1..20."""
        clusters = await self._clusters(nexus_service)
        assert len(clusters) == 20
        for idx, (name, ctype, (gx, gy)) in enumerate(NEXUS_CLUSTER_TABLE):
            c = clusters[idx]
            assert c.name == name, f"#{idx + 1} name {c.name!r} != {name!r}"
            assert c.type == ctype, f"#{idx + 1} type {c.type!r} != {ctype!r}"
            assert c.x_coord == gx, f"#{idx + 1} x_coord {c.x_coord} != {gx}"
            assert c.y_coord == gy, f"#{idx + 1} y_coord {c.y_coord} != {gy}"
            assert c.z_coord == 0

    @pytest.mark.asyncio
    async def test_cluster_type_counts_are_4_2_6_5_3(self, nexus_service):
        """4 TRADE_HUB · 2 POPULATION_CENTER · 6 STANDARD · 5 FRONTIER_OUTPOST ·
        3 RESOURCE_RICH · 0 MILITARY/CONTESTED/SPECIAL (= 20)."""
        clusters = await self._clusters(nexus_service)
        counts = Counter(c.type for c in clusters)
        assert counts[ClusterType.TRADE_HUB] == 4
        assert counts[ClusterType.POPULATION_CENTER] == 2
        assert counts[ClusterType.STANDARD] == 6
        assert counts[ClusterType.FRONTIER_OUTPOST] == 5
        assert counts[ClusterType.RESOURCE_RICH] == 3
        assert counts[ClusterType.MILITARY_ZONE] == 0
        assert counts[ClusterType.CONTESTED] == 0
        assert counts[ClusterType.SPECIAL_INTEREST] == 0
        assert sum(counts.values()) == 20

    @pytest.mark.asyncio
    async def test_civic_safe_anchors(self, nexus_service):
        """Slot 1 (Commerce Central Hub) = TRADE_HUB starter; slot 10 (Gateway
        Plaza) = STANDARD Capital, never FRONTIER_OUTPOST/RESOURCE_RICH."""
        clusters = await self._clusters(nexus_service)
        assert clusters[0].name == "Commerce Central Hub"
        assert clusters[0].type == ClusterType.TRADE_HUB
        assert clusters[9].name == "Gateway Plaza"
        assert clusters[9].type == ClusterType.STANDARD
        assert clusters[9].type not in (
            ClusterType.FRONTIER_OUTPOST,
            ClusterType.RESOURCE_RICH,
        )


class TestGenerateCentralNexusExistingShortCircuit:
    """Regression test for WO-LIVE-SUITE-TRIAGE (2026-07-02).

    generate_central_nexus's early-exit path (_check_existing_nexus) does
    `result = await session.execute(...); return result.scalar_one_or_none()`
    -- scalar_one_or_none() is SYNCHRONOUS on a real SQLAlchemy Result
    (same pattern at nexus_generation_service.py:588/684). A bare
    `AsyncMock()` session auto-mocks EVERY nested attribute as async, so
    calling `.scalar_one_or_none()` on the auto-mocked execute() result
    returns an unawaited coroutine instead of the configured value --
    surfaced as "AttributeError: 'coroutine' object has no attribute
    'id'" at nexus_generation_service.py:57. That is a test-mock-wiring
    defect, not a production one: the production `await session.execute()`
    already yields a real, synchronous Result; adding `await` in front of
    `scalar_one_or_none()` would break at runtime against a real session.
    This test wires the mock the way the earlier (now-deleted, see
    nexus.py:442-444 "district regeneration no longer applicable")
    TestNexusGenerationService suite failed to, so the pitfall can't
    silently ship a broken test again.
    """

    @pytest.mark.asyncio
    async def test_existing_nexus_short_circuits_without_double_await(self):
        nexus_service = NexusGenerationService()
        existing_region = Region(id=uuid.uuid4(), name="central-nexus")

        session = AsyncMock()
        session.execute = AsyncMock(
            return_value=MagicMock(
                scalar_one_or_none=MagicMock(return_value=existing_region)
            )
        )

        result = await nexus_service.generate_central_nexus(session)

        assert result["status"] == "exists"
        assert result["nexus_id"] == str(existing_region.id)


class TestNexusGenerationService:
    """Test the NexusGenerationService class"""

    @pytest.fixture
    def nexus_service(self):
        """Create NexusGenerationService instance"""
        return NexusGenerationService()

    def test_district_name_formatting(self, nexus_service):
        """Test district name formatting utility"""
        test_cases = [
            ('commerce_central', 'Commerce Central'),
            ('high_security_zone', 'High Security Zone'),
            ('free_trade_zone', 'Free Trade Zone'),
            ('gateway_plaza', 'Gateway Plaza')
        ]
        
        for district_type, expected_name in test_cases:
            formatted_name = district_type.replace('_', ' ').title()
            assert formatted_name == expected_name