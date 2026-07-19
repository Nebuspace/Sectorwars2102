"""WO-BANG-NEXUS-LATENT: pin Nexus attachment tunnels as non-latent.

Canon ruling (orchestrator, 2026-07-10): Terran + Nexus are PRE-DISCOVERED
for every player, so the Nexus attachment tunnels are the sanctioned
cross-region gateway, not a Warp Jumper scan-discoverable secret. Both
creation sites in ``bang_import_service.py`` must emit ``is_latent=False``.

ADR-0034 latency still governs ordinary *in-region* natural tunnels
(``sector_warps`` import + the raw-warp translation path) — this suite does
not touch those and asserts nothing about them.
"""
from __future__ import annotations

import inspect
import uuid
from typing import Any, List
from unittest.mock import MagicMock

from src.models.warp_tunnel import WarpTunnel, WarpTunnelType
from src.services.bang_import_service import BangImportService, RegionAttachment


class _AddCapturingSession:
    """Minimal stand-in for AsyncSession — only needs a sync ``add``.

    ``_add_nexus_warp`` is a plain (non-async) staticmethod that only calls
    ``session.add(...)``; no query/flush surface is exercised, so a real
    AsyncSession or a fuller fake is unnecessary.
    """

    def __init__(self) -> None:
        self.added: List[Any] = []

    def add(self, obj: Any) -> None:
        self.added.append(obj)


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

    def test_player_owned_spoke_warp_is_not_latent(self) -> None:
        session = _AddCapturingSession()
        spoke, nexus = self._spoke_and_nexus()

        BangImportService._add_nexus_warp(session, "player_owned", spoke, nexus)

        assert len(session.added) == 1
        tunnel = session.added[0]
        assert isinstance(tunnel, WarpTunnel)
        assert tunnel.is_latent is False
        assert tunnel.type == WarpTunnelType.NATURAL
        assert tunnel.is_bidirectional is True
        assert tunnel.origin_sector_id == spoke.nexus_landing_sector_id
        assert tunnel.destination_sector_id == nexus.gate_sector_id

    def test_terran_space_spoke_warp_is_not_latent(self) -> None:
        session = _AddCapturingSession()
        spoke, nexus = self._spoke_and_nexus()

        BangImportService._add_nexus_warp(session, "terran_space", spoke, nexus)

        assert len(session.added) == 1
        tunnel = session.added[0]
        assert tunnel.is_latent is False
        assert tunnel.type == WarpTunnelType.NATURAL
        assert tunnel.is_bidirectional is True

    def test_falls_back_to_gate_sector_when_no_landing_chosen(self) -> None:
        """Degraded-region fallback path (no Gateway Plaza landing) is still
        wired non-latent — the fix is not landing-selection-dependent."""
        session = _AddCapturingSession()
        spoke = RegionAttachment(gate_sector_id=uuid.uuid4())  # no landing sector
        nexus = RegionAttachment(gate_sector_id=uuid.uuid4())

        BangImportService._add_nexus_warp(session, "player_owned", spoke, nexus)

        tunnel = session.added[0]
        assert tunnel.is_latent is False
        assert tunnel.origin_sector_id == spoke.gate_sector_id

    def test_no_op_when_spoke_not_imported(self) -> None:
        session = _AddCapturingSession()
        _, nexus = self._spoke_and_nexus()

        BangImportService._add_nexus_warp(session, "player_owned", None, nexus)

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
        assert "WarpTunnel(" in source


class TestInRegionWarpLatencyUntouched:
    """Confirms the audit finding: the two in-region warp-import sites
    (sector_warps association-table insert, and raw-warp -> WarpSpec
    translation) still carry the bang-sourced per-warp latent flag through
    unchanged — those are ADR-0034 ordinary natural tunnels, not the Nexus
    attachment gateway, and are out of scope for this fix."""

    def test_apply_region_still_persists_bang_latent_flag(self) -> None:
        source = inspect.getsource(BangImportService._apply_region)
        assert "is_latent=w.is_latent" in source

    def test_translate_region_still_carries_bang_latent_flag(self) -> None:
        source = inspect.getsource(BangImportService._translate_region)
        assert 'is_latent=bool(w.get("is_latent", w.get("isLatent", False)))' in source
