"""LEG-473: bang-import step-9 isolated-cluster stamp.

Canon: SYSTEMS/bang-import-pipeline.md:198-204. Stamp Cluster.special_features
with ``isolated`` when none of the cluster's sectors is reachable from a
fedspace sector via bang natural warps. Do not synthesize isolation.
"""
from __future__ import annotations

import logging

import pytest

from src.models.cluster import ClusterType
from src.models.sector import SectorType
from src.services.bang_import_service import (
    ClusterSpec,
    SectorSpec,
    WarpSpec,
    _ISOLATED_FEATURE,
    _step9_isolated_cluster_int_ids,
    _step9_stamp_isolated_clusters,
)


def _cluster(cid: int, start: int, end: int) -> ClusterSpec:
    return ClusterSpec(
        cluster_int_id=cid,
        name=f"c{cid}",
        type=ClusterType.STANDARD,
        sector_range_start=start,
        sector_range_end=end,
        sector_count=end - start + 1,
        x_coord=0,
        y_coord=0,
        z_coord=0,
        warp_stability=1.0,
        economic_value=50,
        recommended_ship_class="light_freighter",
        max_warps=6,
        island_group_id=None,
        is_discovered=True,
        is_hidden=False,
        special_features=[],
    )


def _sector(sid: int, cluster_int_id: int, *, fedspace: bool = False) -> SectorSpec:
    return SectorSpec(
        sector_id=sid,
        sector_number=sid,
        is_capital=sid == 1,
        name=f"s{sid}",
        region_int_id=0,
        cluster_int_id=cluster_int_id,
        x_coord=0,
        y_coord=0,
        z_coord=0,
        type=SectorType.STANDARD,
        security_level=10 if fedspace else 5,
        hazard_level=0,
        nav_hazards={},
        nav_beacons=[],
        special_features=["fedspace"] if fedspace else [],
        is_discovered=True,
    )


def _warp(frm: int, to: int, *, bidirectional: bool = True) -> WarpSpec:
    return WarpSpec(
        from_sector_int=frm,
        to_sector_int=to,
        is_bidirectional=bidirectional,
        turn_cost=1,
        warp_stability=1.0,
    )


@pytest.mark.unit
class TestStep9IsolatedHelper:
    def test_disconnected_cluster_is_isolated(self) -> None:
        clusters = [_cluster(1, 1, 2), _cluster(2, 10, 11)]
        sectors = [
            _sector(1, 1, fedspace=True),
            _sector(2, 1),
            _sector(10, 2),
            _sector(11, 2),
        ]
        warps = [_warp(1, 2)]
        isolated = _step9_isolated_cluster_int_ids(clusters, sectors, warps)
        assert isolated == {2}

    def test_warp_from_fedspace_connects_cluster(self) -> None:
        clusters = [_cluster(1, 1, 1), _cluster(2, 10, 10)]
        sectors = [_sector(1, 1, fedspace=True), _sector(10, 2)]
        warps = [_warp(1, 10)]
        isolated = _step9_isolated_cluster_int_ids(clusters, sectors, warps)
        assert isolated == set()

    def test_fully_connected_does_not_synthesize(self) -> None:
        clusters = [_cluster(1, 1, 3)]
        sectors = [
            _sector(1, 1, fedspace=True),
            _sector(2, 1),
            _sector(3, 1),
        ]
        warps = [_warp(1, 2), _warp(2, 3)]
        assert _step9_isolated_cluster_int_ids(clusters, sectors, warps) == set()

    def test_one_way_away_does_not_connect(self) -> None:
        clusters = [_cluster(1, 1, 1), _cluster(2, 10, 10)]
        sectors = [_sector(1, 1, fedspace=True), _sector(10, 2)]
        # one-way from isolated cluster toward fedspace — cannot reach 10 from 1
        warps = [_warp(10, 1, bidirectional=False)]
        isolated = _step9_isolated_cluster_int_ids(clusters, sectors, warps)
        assert isolated == {2}

    def test_one_way_from_fedspace_connects(self) -> None:
        clusters = [_cluster(1, 1, 1), _cluster(2, 10, 10)]
        sectors = [_sector(1, 1, fedspace=True), _sector(10, 2)]
        warps = [_warp(1, 10, bidirectional=False)]
        isolated = _step9_isolated_cluster_int_ids(clusters, sectors, warps)
        assert isolated == set()

    def test_empty_fedspace_isolates_populated_clusters(self) -> None:
        clusters = [_cluster(1, 1, 1), _cluster(2, 2, 2)]
        sectors = [_sector(1, 1), _sector(2, 2)]
        warps = [_warp(1, 2)]
        isolated = _step9_isolated_cluster_int_ids(clusters, sectors, warps)
        assert isolated == {1, 2}

    def test_latent_warp_still_counts_as_natural(self) -> None:
        clusters = [_cluster(1, 1, 1), _cluster(2, 10, 10)]
        sectors = [_sector(1, 1, fedspace=True), _sector(10, 2)]
        warps = [
            WarpSpec(
                from_sector_int=1,
                to_sector_int=10,
                is_bidirectional=True,
                turn_cost=1,
                warp_stability=1.0,
                is_latent=True,
            )
        ]
        assert _step9_isolated_cluster_int_ids(clusters, sectors, warps) == set()

    def test_empty_cluster_skipped(self) -> None:
        clusters = [_cluster(1, 1, 1), _cluster(99, 99, 99)]
        sectors = [_sector(1, 1, fedspace=True)]
        isolated = _step9_isolated_cluster_int_ids(clusters, sectors, [])
        assert isolated == set()


@pytest.mark.unit
class TestStep9Stamp:
    def test_stamp_appends_isolated_once(self) -> None:
        clusters = [_cluster(1, 1, 1), _cluster(2, 10, 10)]
        clusters[1].special_features = ["isolated"]
        sectors = [_sector(1, 1, fedspace=True), _sector(10, 2)]
        isolated = _step9_stamp_isolated_clusters(clusters, sectors, [])
        assert isolated == {2}
        assert clusters[0].special_features == []
        assert clusters[1].special_features == ["isolated"]

    def test_warns_when_share_exceeds_quarter(self, caplog: pytest.LogCaptureFixture) -> None:
        clusters = [_cluster(1, 1, 1), _cluster(2, 10, 10)]
        sectors = [_sector(1, 1, fedspace=True), _sector(10, 2)]
        with caplog.at_level(logging.WARNING, logger="src.services.bang_import_service"):
            _step9_stamp_isolated_clusters(clusters, sectors, [])
        assert any("step-9" in rec.getMessage() for rec in caplog.records)
        assert _ISOLATED_FEATURE in clusters[1].special_features
        assert _ISOLATED_FEATURE not in clusters[0].special_features
