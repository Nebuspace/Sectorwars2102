"""LEG-157: anchor-repair lifecycle events reach the realtime bus.

``anchor_repair_service`` already builds region_anchor_missing /
region_anchor_repaired / region_anchor_repair_failed dicts; the governance
sweep commits then hands them to ``_broadcast_events``. This pins the
region+admin fan-out and the best-effort (never-raise) contract.
"""
from __future__ import annotations

import asyncio
import uuid
from unittest.mock import AsyncMock, patch

from src.services.npc_scheduler_service import _broadcast_events


def _anchor_event(event_type: str, *, region_id=None, sector_id=1001):
    return {
        "type": event_type,
        "region_id": str(region_id if region_id is not None else uuid.uuid4()),
        "region_name": "Rylan Reach",
        "anchor_type": "capital_terra",
        "sector_id": sector_id,
    }


class TestAnchorRepairRealtimeBroadcast:
    def test_region_anchor_missing_fans_out_region_and_admin(self):
        event = _anchor_event("region_anchor_missing")

        async def _run():
            with patch("src.services.websocket_service.connection_manager") as mock_cm:
                mock_cm.broadcast_to_region = AsyncMock()
                mock_cm.broadcast_to_admins = AsyncMock()
                mock_cm.broadcast_to_sector = AsyncMock()
                await _broadcast_events([event])
                mock_cm.broadcast_to_region.assert_awaited_once_with(
                    event["region_id"], event
                )
                mock_cm.broadcast_to_admins.assert_awaited_once_with(event)
                mock_cm.broadcast_to_sector.assert_not_awaited()

        asyncio.run(_run())

    def test_region_anchor_repaired_fans_out_region_and_admin(self):
        event = _anchor_event("region_anchor_repaired")

        async def _run():
            with patch("src.services.websocket_service.connection_manager") as mock_cm:
                mock_cm.broadcast_to_region = AsyncMock()
                mock_cm.broadcast_to_admins = AsyncMock()
                await _broadcast_events([event])
                mock_cm.broadcast_to_region.assert_awaited_once_with(
                    event["region_id"], event
                )
                mock_cm.broadcast_to_admins.assert_awaited_once_with(event)

        asyncio.run(_run())

    def test_region_anchor_repair_failed_fans_out_region_and_admin(self):
        event = _anchor_event("region_anchor_repair_failed", sector_id=None)

        async def _run():
            with patch("src.services.websocket_service.connection_manager") as mock_cm:
                mock_cm.broadcast_to_region = AsyncMock()
                mock_cm.broadcast_to_admins = AsyncMock()
                mock_cm.broadcast_to_sector = AsyncMock()
                await _broadcast_events([event])
                mock_cm.broadcast_to_region.assert_awaited_once()
                mock_cm.broadcast_to_admins.assert_awaited_once_with(event)
                # Must not fall through to sector path (sector_id is None).
                mock_cm.broadcast_to_sector.assert_not_awaited()

        asyncio.run(_run())

    def test_missing_region_id_still_broadcasts_admins(self):
        event = _anchor_event("region_anchor_missing")
        event["region_id"] = None

        async def _run():
            with patch("src.services.websocket_service.connection_manager") as mock_cm:
                mock_cm.broadcast_to_region = AsyncMock()
                mock_cm.broadcast_to_admins = AsyncMock()
                await _broadcast_events([event])
                mock_cm.broadcast_to_region.assert_not_awaited()
                mock_cm.broadcast_to_admins.assert_awaited_once_with(event)

        asyncio.run(_run())

    def test_bus_failure_does_not_raise(self):
        event = _anchor_event("region_anchor_repaired")

        async def _run():
            with patch("src.services.websocket_service.connection_manager") as mock_cm:
                mock_cm.broadcast_to_region = AsyncMock(
                    side_effect=RuntimeError("region bus down"),
                )
                mock_cm.broadcast_to_admins = AsyncMock(
                    side_effect=RuntimeError("admin bus down"),
                )
                await _broadcast_events([event])  # must not raise

        asyncio.run(_run())
