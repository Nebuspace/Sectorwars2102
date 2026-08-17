"""WO-BANG-NEXUS-LATENT: pin Nexus attachment tunnels as non-latent.

Canon ruling (orchestrator, 2026-07-10): Terran + Nexus are PRE-DISCOVERED
for every player, so the Nexus attachment tunnels are the sanctioned
cross-region gateway, not a Warp Jumper scan-discoverable secret. Both
creation sites in ``bang_import_service.py`` must emit ``is_latent=False``.

ADR-0034 latency still governs ordinary *in-region* natural tunnels
(``sector_warps`` import + the raw-warp translation path) — this suite does
not touch those and asserts nothing about them.

LEG-88 / LEG-195: ``_add_nexus_warp`` is async and loads endpoint Sector
coords for hop-unit length + banded turn_cost — tests must await and stub
``session.get``.
"""
from __future__ import annotations

import inspect
import uuid
from types import SimpleNamespace
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock

import pytest

from src.models.warp_tunnel import WarpTunnel, WarpTunnelType
from src.services.bang_import_service import BangImportService, RegionAttachment


class _AddCapturingAsyncSession:
    """AsyncSession stand-in for ``_add_nexus_warp``.

    Captures ``add(...)`` and serves Sector-like rows from ``get`` so LEG-88
    length/turn-cost computation can run without a real DB.
    """

    def __init__(self, sectors: Optional[Dict[uuid.UUID, Any]] = None) -> None:
        self.added: List[Any] = []
        self._sectors = sectors or {}
        self.get = AsyncMock(side_effect=self._get)

    async def _get(self, _model: Any, key: Any) -> Any:
        return self._sectors.get(key)

    def add(self, obj: Any) -> None:
        self.added.append(obj)


def _sector(sector_id: uuid.UUID, *, x: float = 0.0, y: float = 0.0, z: float = 0.0) -> Any:
    return SimpleNamespace(id=sector_id, x_coord=x, y_coord=y, z_coord=z)


class TestAddNexusWarpIsNotLatent:
    """Pins the generation-time spoke <-> Nexus wiring (apply()'s two call
    sites, player_owned + terran_space) — the path that created the two
    latent rows found live."""

    def _spoke_and_nexus(self) -> tuple[RegionAttachment, RegionAttachment]:
        spoke = RegionAttachment(
            gate_sector_id=uuid.uuid4(),
            nexus_landing_sector_id=uuid.uuid4(),
            nexus_landing_sector_number=42,
        )
        nexus = RegionAttachment(gate_sector_id=uuid.uuid4())
        return spoke, nexus

    def _session_for(
        self, spoke: RegionAttachment, nexus: RegionAttachment
    ) -> _AddCapturingAsyncSession:
        spoke_endpoint = spoke.nexus_landing_sector_id or spoke.gate_sector_id
        assert spoke_endpoint is not None
        assert nexus.gate_sector_id is not None
        return _AddCapturingAsyncSession(
            {
                spoke_endpoint: _sector(spoke_endpoint, x=0.0, y=0.0, z=0.0),
                # distance 3 hop-units → turn_cost band 1
                nexus.gate_sector_id: _sector(nexus.gate_sector_id, x=3.0, y=0.0, z=0.0),
            }
        )

    @pytest.mark.asyncio
    async def test_player_owned_spoke_warp_is_not_latent(self) -> None:
        spoke, nexus = self._spoke_and_nexus()
        session = self._session_for(spoke, nexus)

        await BangImportService._add_nexus_warp(session, "player_owned", spoke, nexus)

        assert len(session.added) == 1
        tunnel = session.added[0]
        assert isinstance(tunnel, WarpTunnel)
        assert tunnel.is_latent is False
        assert tunnel.type == WarpTunnelType.NATURAL
        assert tunnel.is_bidirectional is True
        assert tunnel.origin_sector_id == spoke.nexus_landing_sector_id
        assert tunnel.destination_sector_id == nexus.gate_sector_id
        assert tunnel.turn_cost == 1
        assert tunnel.properties["length"] == 3.0
        assert tunnel.properties["traversal_cost"] == 1

    @pytest.mark.asyncio
    async def test_terran_space_spoke_warp_is_not_latent(self) -> None:
        spoke, nexus = self._spoke_and_nexus()
        session = self._session_for(spoke, nexus)

        await BangImportService._add_nexus_warp(session, "terran_space", spoke, nexus)

        assert len(session.added) == 1
        tunnel = session.added[0]
        assert tunnel.is_latent is False
        assert tunnel.type == WarpTunnelType.NATURAL
        assert tunnel.is_bidirectional is True
        assert tunnel.turn_cost == 1

    @pytest.mark.asyncio
    async def test_falls_back_to_gate_sector_when_no_landing_chosen(self) -> None:
        """Degraded-region fallback path (no Gateway Plaza landing) is still
        wired non-latent — the fix is not landing-selection-dependent."""
        spoke = RegionAttachment(gate_sector_id=uuid.uuid4())  # no landing sector
        nexus = RegionAttachment(gate_sector_id=uuid.uuid4())
        session = self._session_for(spoke, nexus)

        await BangImportService._add_nexus_warp(session, "player_owned", spoke, nexus)

        tunnel = session.added[0]
        assert tunnel.is_latent is False
        assert tunnel.origin_sector_id == spoke.gate_sector_id

    @pytest.mark.asyncio
    async def test_no_op_when_spoke_not_imported(self) -> None:
        session = _AddCapturingAsyncSession()
        _, nexus = self._spoke_and_nexus()

        await BangImportService._add_nexus_warp(session, "player_owned", None, nexus)

        assert session.added == []

    @pytest.mark.asyncio
    async def test_skips_when_endpoint_sector_missing(self) -> None:
        """LEG-88: missing Sector rows must not invent coords — skip insert."""
        spoke, nexus = self._spoke_and_nexus()
        session = _AddCapturingAsyncSession()  # empty get → None

        await BangImportService._add_nexus_warp(session, "player_owned", spoke, nexus)

        assert session.added == []


class TestApplyAdditionalRegionSourceIsNotLatent:
    """The "Add Player-Owned Region" admin flow (apply_additional_region)
    builds its WarpTunnel inline, entangled with async session.get/execute
    calls that aren't worth mocking end-to-end for one boolean. Per the WO's
    allowance, this pins the live source of the method instead: no
    ``is_latent=True`` remains in the attachment-tunnel block, and the
    replacement ``is_latent=False`` is present."""

    def test_source_has_no_latent_true(self) -> None:
        source = inspect.getsource(BangImportService.apply_additional_region)
        assert "is_latent=True" not in source
        assert "is_latent=False" in source

    def test_source_still_builds_a_natural_bidirectional_warp_tunnel(self) -> None:
        source = inspect.getsource(BangImportService.apply_additional_region)
        assert "type=WarpTunnelType.NATURAL" in source
        assert "is_bidirectional=True" in source
        # ADR-0050 SK22: the tunnel insert is now an idempotent
        # INSERT...ON CONFLICT DO NOTHING (pg_insert(WarpTunnel).values(...))
        # rather than a plain ORM session.add(WarpTunnel(...)) construction
        assert "pg_insert(WarpTunnel)" in source or "_pg_insert(WarpTunnel)" in source
        assert "on_conflict_do_nothing" in source
        # LEG-88 length/turn-cost persisted on the same insert
        assert "natural_tunnel_cost_fields" in source
        assert "turn_cost=" in source
        assert "properties=" in source
