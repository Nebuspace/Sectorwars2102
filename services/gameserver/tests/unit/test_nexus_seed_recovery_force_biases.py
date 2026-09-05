"""LEG-2577 — is_populated recovery, force_regenerate, cluster-type biases."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.models.cluster import ClusterType
from src.models.planet import PlanetType
from src.models.region import Region, RegionType
from src.models.station import StationClass
from src.services.nexus_generation_service import (
    NexusGenerationService,
    _PORT_CLASS_WEIGHTS_TRADE_HUB,
    _pick_weighted_choice,
    _pick_weighted_int,
)


class TestNexusWeightedPickers:
    def test_trade_hub_port_weights_majority_ge_class_4(self):
        samples = [_pick_weighted_int(_PORT_CLASS_WEIGHTS_TRADE_HUB) for _ in range(500)]
        ge4 = sum(1 for k in samples if k >= 4)
        assert ge4 > len(samples) * 0.6

    def test_population_center_planet_bias_oceanic_heavy(self):
        weights = [
            (PlanetType.OCEANIC, 8),
            (PlanetType.MOUNTAINOUS, 3),
            (PlanetType.ICE, 1),
            (PlanetType.VOLCANIC, 1),
            (PlanetType.BARREN, 1),
        ]
        samples = [_pick_weighted_choice(weights) for _ in range(500)]
        oceanic = sum(1 for p in samples if p == PlanetType.OCEANIC)
        assert oceanic > len(samples) * 0.5


class TestNexusPortPlanetBiases:
    def test_trade_hub_port_class_skew(self):
        svc = NexusGenerationService()
        classes = []
        for _ in range(200):
            row = svc._generate_port_for_sector(
                450,
                "region-id",
                cluster_type=ClusterType.TRADE_HUB,
            )
            classes.append(row["station_class"])
        ge4 = sum(1 for c in classes if c.value >= 4)
        assert ge4 > len(classes) * 0.6

    def test_population_center_planet_bias_and_habitability(self):
        svc = NexusGenerationService()
        oceanic = 0
        habit_scores = []
        for _ in range(200):
            row = svc._generate_planet_for_sector(
                600,
                "region-id",
                cluster_type=ClusterType.POPULATION_CENTER,
            )
            if row["type"] == PlanetType.OCEANIC:
                oceanic += 1
            habit_scores.append(row["habitability_score"])
        assert oceanic > 100
        assert sum(habit_scores) / len(habit_scores) > 55


@pytest.mark.asyncio
async def test_generate_skips_when_populated_without_force():
    svc = NexusGenerationService()
    session = AsyncMock()
    existing = Region(
        name="central-nexus",
        display_name="Central Nexus",
        region_type=RegionType.CENTRAL_NEXUS,
        is_populated=True,
    )
    with patch.object(
        svc,
        "_check_existing_nexus",
        new=AsyncMock(return_value=existing),
    ):
        result = await svc.generate_central_nexus(session, force_regenerate=False)
    assert result["status"] == "exists"
    session.commit.assert_not_called()


@pytest.mark.asyncio
async def test_generate_force_deletes_populated_nexus():
    svc = NexusGenerationService()
    session = AsyncMock()
    existing = Region(
        name="central-nexus",
        display_name="Central Nexus",
        region_type=RegionType.CENTRAL_NEXUS,
        is_populated=True,
    )
    with patch.object(
        svc,
        "_check_existing_nexus",
        new=AsyncMock(return_value=existing),
    ), patch.object(
        svc,
        "_delete_central_nexus_region",
        new=AsyncMock(),
    ) as delete_mock, patch.object(
        svc,
        "_create_nexus_region",
        new=AsyncMock(side_effect=RuntimeError("stop after delete")),
    ):
        with pytest.raises(RuntimeError, match="stop after delete"):
            await svc.generate_central_nexus(session, force_regenerate=True)
    delete_mock.assert_awaited_once_with(session, existing)
    session.commit.assert_awaited()


@pytest.mark.asyncio
async def test_partial_nexus_retries_without_force():
    svc = NexusGenerationService()
    session = AsyncMock()
    partial = Region(
        name="central-nexus",
        display_name="Central Nexus",
        region_type=RegionType.CENTRAL_NEXUS,
        is_populated=False,
    )
    with patch.object(
        svc,
        "_check_existing_nexus",
        new=AsyncMock(return_value=partial),
    ), patch.object(
        svc,
        "_delete_central_nexus_region",
        new=AsyncMock(),
    ) as delete_mock, patch.object(
        svc,
        "_create_nexus_region",
        new=AsyncMock(side_effect=RuntimeError("stop after delete")),
    ):
        with pytest.raises(RuntimeError, match="stop after delete"):
            await svc.generate_central_nexus(session, force_regenerate=False)
    delete_mock.assert_awaited_once_with(session, partial)


@pytest.mark.asyncio
async def test_generation_failure_marks_is_populated_false():
    svc = NexusGenerationService()
    session = AsyncMock()
    partial = MagicMock()
    partial.is_populated = True
    with patch.object(
        svc,
        "_check_existing_nexus",
        new=AsyncMock(side_effect=[None, partial]),
    ), patch.object(
        svc,
        "_create_nexus_region",
        new=AsyncMock(side_effect=RuntimeError("boom")),
    ):
        with pytest.raises(RuntimeError, match="boom"):
            await svc.generate_central_nexus(session)
    assert partial.is_populated is False
    session.commit.assert_awaited()
