"""Pins Central Nexus Expanse zone sector range (WO-FIX-NEXUS-REGION-START-END-SECTOR-HARDCODE).

The Zone row must cover the live generator numbering (301 … 300+total), not
the stale 1–5000 hardcode that overlapped Terran Space and missed the last
300 Nexus sectors. Pure helper — no DB / no asyncio.
"""
from src.services.nexus_generation_service import (
    NEXUS_FIRST_SECTOR_NUM,
    nexus_expanse_sector_range,
)


def test_nexus_expanse_default_5000_is_301_to_5300():
    start, end = nexus_expanse_sector_range(5000)
    assert start == NEXUS_FIRST_SECTOR_NUM == 301
    assert end == 5300


def test_nexus_expanse_scales_with_total_sectors():
    assert nexus_expanse_sector_range(2000) == (301, 2300)
    assert nexus_expanse_sector_range(1) == (301, 301)
