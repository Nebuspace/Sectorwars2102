"""LEG-472: bang-import step-10 long-warp WarpTunnel rows.

Canon: SYSTEMS/bang-import-pipeline.md:216-223 — intra-region warps whose
endpoint span exceeds max(50, totalSectors/20) also persist a NATURAL
WarpTunnel (sector_warps remains the lightweight routing association).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.services.bang_import_service import (
    BangImportService,
    WarpSpec,
    _step10_endpoint_json,
    _step10_is_long_warp,
    _step10_long_warp_specs,
    _step10_long_warp_threshold,
    _step10_warp_tunnel_properties,
)

FIXTURE_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "bang"


def _warp(frm: int, to: int, *, bidirectional: bool = True, latent: bool = False) -> WarpSpec:
    return WarpSpec(
        from_sector_int=frm,
        to_sector_int=to,
        is_bidirectional=bidirectional,
        turn_cost=1,
        warp_stability=1.0,
        is_latent=latent,
    )


@pytest.mark.unit
class TestStep10Threshold:
    def test_1k_fixture_threshold_is_50(self) -> None:
        assert _step10_long_warp_threshold(1000) == 50

    def test_small_region_uses_floor_50(self) -> None:
        assert _step10_long_warp_threshold(300) == 50

    def test_large_region_scales_with_total_sectors(self) -> None:
        assert _step10_long_warp_threshold(2000) == 100


@pytest.mark.unit
class TestStep10LongWarpSelection:
    def test_span_must_exceed_threshold_not_equal(self) -> None:
        total = 1000
        assert not _step10_is_long_warp(_warp(1, 51), total)
        assert _step10_is_long_warp(_warp(1, 52), total)

    def test_latent_flag_does_not_skip_tunnel(self) -> None:
        total = 1000
        assert _step10_is_long_warp(_warp(1, 200, latent=True), total)

    def test_one_way_long_warp_selected(self) -> None:
        total = 1000
        w = _warp(10, 100, bidirectional=False)
        assert _step10_is_long_warp(w, total)


@pytest.mark.unit
class TestStep10WarpTunnelShape:
    def test_properties_match_canon_pins(self) -> None:
        props = _step10_warp_tunnel_properties()
        assert props == {
            "traversal_cost": 1,
            "discovered": True,
            "affected_by_storms": False,
        }

    def test_endpoint_json_populates_ids_and_coords(self) -> None:
        import uuid

        sid = uuid.uuid4()
        cid = uuid.uuid4()
        rid = uuid.uuid4()
        ep = _step10_endpoint_json(
            sector_uuid=sid,
            cluster_uuid=cid,
            region_id=rid,
            x_coord=1,
            y_coord=2,
            z_coord=3,
            controlling_faction="FC",
        )
        assert ep["sector_id"] == str(sid)
        assert ep["cluster_id"] == str(cid)
        assert ep["region_id"] == str(rid)
        assert ep["coordinates"] == {"x": 1, "y": 2, "z": 3}
        assert ep["controlling_faction"] == "FC"


@pytest.mark.unit
class TestStep10ApplyRegionWiring:
    def test_apply_region_source_creates_long_warp_tunnels(self) -> None:
        import inspect

        source = inspect.getsource(BangImportService._apply_region)
        assert "_step10_is_long_warp" in source
        assert "WarpTunnelType.NATURAL" in source
        assert "WarpTunnelStatus.ACTIVE" in source
        assert "WarpTunnelStability.STABLE" in source
        assert 'long_warp_tunnels' in source


@pytest.mark.unit
class TestStep10PlayerOwnedFixture:
    def test_long_warp_count_on_1k_fixture(self) -> None:
        raw = json.loads(
            (FIXTURE_DIR / "v1_3_0_player_owned_small.json").read_text(encoding="utf-8")
        )
        total = int(raw["totalSectors"])
        warps = [
            _warp(int(w["from"]), int(w["to"]), bidirectional=not bool(w.get("oneWay")))
            for w in raw.get("warps") or []
        ]
        long_specs = _step10_long_warp_specs(warps, total)
        assert len(long_specs) > 0
        share = len(long_specs) / len(warps)
        # Fixture topology yields >5% long spans; we do not synthesize/filter.
        assert share >= 0.05
