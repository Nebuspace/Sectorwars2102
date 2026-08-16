"""LEG-38: bang-import TradeDock placement rules (b)+(c)."""
from __future__ import annotations

from types import SimpleNamespace
from typing import List
from unittest.mock import MagicMock

from src.services.bang_import_service import BangImportService, WarpSpec


def _svc() -> BangImportService:
    return BangImportService(bang_image="test:0", docker_client=MagicMock())


def _warp(frm: int, to: int, bidirectional: bool = True) -> WarpSpec:
    return WarpSpec(
        from_sector_int=frm,
        to_sector_int=to,
        is_bidirectional=bidirectional,
        turn_cost=1,
        warp_stability=1.0,
    )


def test_inbound_warp_counts_bidirectional():
    plan = SimpleNamespace(
        warps=[_warp(10, 20), _warp(30, 20, bidirectional=False)],
    )
    counts = BangImportService._inbound_warp_counts(plan)  # type: ignore[arg-type]
    assert counts[20] == 2  # from 10↔20 and 30→20
    assert counts[10] == 1  # reverse of bidirectional
    assert 30 not in counts


def test_frontier_zone_sectors_terran_excludes_fed_and_border():
    frontier = BangImportService._frontier_zone_sectors("terran_space", 300)
    assert 1 not in frontier
    assert 99 not in frontier
    assert 150 not in frontier  # border-ish
    assert min(frontier) >= 200
    assert 300 in frontier
    assert BangImportService._frontier_zone_sectors("central_nexus", 5000) == set()


def test_candidate_pool_excludes_fedspace_frontier_and_low_inbound():
    # Federation geo band 2..99; fedspace 1..10; frontier starts ~202
    warps: List[WarpSpec] = []
    # Give sector 50 strong connectivity; sector 5 is fedspace; 250 is frontier
    for n in (48, 49, 51):
        warps.append(_warp(n, 50))
    warps.append(_warp(249, 250))
    warps.append(_warp(248, 250))

    plan = SimpleNamespace(
        total_sectors=300,
        fedspace_sector_ints=list(range(1, 11)),
        warps=warps,
        stations=[],
        clusters=[],
    )
    pool = _svc()._tradedock_candidate_pool(
        "terran_space", plan, occupied=set(), min_inbound=2  # type: ignore[arg-type]
    )
    assert 50 in pool
    assert 5 not in pool  # fedspace
    assert 250 not in pool  # frontier + outside geo band
    assert all(2 <= s <= 99 for s in pool)


def test_candidate_pool_respects_occupied():
    warps = [_warp(40, 50), _warp(41, 50)]
    plan = SimpleNamespace(
        total_sectors=300,
        fedspace_sector_ints=[1],
        warps=warps,
        stations=[],
        clusters=[],
    )
    pool = _svc()._tradedock_candidate_pool(
        "terran_space", plan, occupied={50}, min_inbound=2  # type: ignore[arg-type]
    )
    assert 50 not in pool
