"""Audit-cycle-27 #2 — Get-home ritual verify-first pins.

Canon (warp-gates.md § Get-home ritual, ADR-0029) lists five mechanical steps.
Tip sweep found each already wired — this file pins the load-bearing constants
and call sites so a future regression cannot silently drop them while the
warp-gates.md banner still reads Design-only.

Steps → code (verified 2026-08-09):
  1. WJ QJ tow companion, medium-or-smaller, +5 flat
     → quantum_service._compute_jump_cost / jump + QJ_TOW_SURCHARGE_FLAT
  2. Phase 3 sacrifice auto-detaches tow; companion survives at dest
     → tow_service.detach_on_destruction (warp_gate_anchor path)
  3. Companion tows escape pod (+1 tiny per-move)
     → Ship.TOW_SURCHARGE[TINY] == 1
  4–5. Tow through player gate +2 flat (any tow, not ritual-only)
     → movement_service.GATE_TOW_SURCHARGE_FLAT + player-gate branch
"""
from __future__ import annotations

import inspect

import pytest

from src.models.ship import ShipSize, TOW_SURCHARGE, size_units_for, tow_surcharge_for
from src.services import movement_service as ms_mod
from src.services import quantum_service as qs_mod
from src.services.tow_service import (
    GATE_TOW_SURCHARGE_FLAT,
    QJ_MAX_TOWED_SIZE_UNITS,
    QJ_TOW_SURCHARGE_FLAT,
    TowService,
)


@pytest.mark.unit
class TestGetHomeRitualConstants:
    def test_qj_tow_surcharge_is_plus_five_flat(self):
        assert QJ_TOW_SURCHARGE_FLAT == 5

    def test_gate_tow_surcharge_is_plus_two_flat(self):
        assert GATE_TOW_SURCHARGE_FLAT == 2
        assert ms_mod.MovementService.GATE_TOW_SURCHARGE_FLAT == 2

    def test_escape_pod_tiny_tow_is_plus_one(self):
        assert TOW_SURCHARGE[ShipSize.TINY] == 1
        assert tow_surcharge_for(ShipSize.TINY) == 1

    def test_qj_size_cap_includes_medium_excludes_large(self):
        assert size_units_for(ShipSize.MEDIUM) <= QJ_MAX_TOWED_SIZE_UNITS
        assert size_units_for(ShipSize.LARGE) > QJ_MAX_TOWED_SIZE_UNITS


@pytest.mark.unit
class TestGetHomeRitualWiringPins:
    def test_quantum_jump_cost_folds_qj_tow_surcharge(self):
        src = inspect.getsource(qs_mod._compute_jump_cost)
        assert "QJ_TOW_SURCHARGE_FLAT" in src
        assert "is_actively_towing" in src

    def test_quantum_jump_carry_towed_ship_on_arrival(self):
        src = inspect.getsource(qs_mod.jump)
        assert "carry_towed_ship" in src

    def test_movement_player_gate_applies_flat_tow_surcharge(self):
        src = inspect.getsource(ms_mod.MovementService.move_player_to_sector)
        assert "GATE_TOW_SURCHARGE_FLAT" in src
        assert "_has_player_gate" in src

    def test_phase3_sacrifice_auto_detaches_tow(self):
        src = inspect.getsource(TowService.detach_on_destruction)
        assert "warp_gate_anchor" in src or "HAULER" in src
        assert "tow_state" in src
