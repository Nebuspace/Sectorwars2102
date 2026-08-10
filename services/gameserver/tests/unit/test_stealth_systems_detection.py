"""cycle-50 — Stealth Systems −25% on contraband P(detected)."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.services.contraband_service import (
    STEALTH_SYSTEMS_DETECTION_MULT,
    ContrabandService,
)


@pytest.mark.unit
class TestStealthSystemsDetectionMult:
    def test_equipment_slot_reduces_p(self):
        svc = ContrabandService(db=None)  # type: ignore[arg-type]
        ship = SimpleNamespace(equipment_slots={"stealth_module": {}}, modules={})
        base = 0.40
        assert svc._apply_stealth_systems_mult(base, ship) == pytest.approx(
            base * STEALTH_SYSTEMS_DETECTION_MULT
        )

    def test_no_stealth_unchanged(self):
        svc = ContrabandService(db=None)  # type: ignore[arg-type]
        ship = SimpleNamespace(equipment_slots={}, modules={})
        assert svc._apply_stealth_systems_mult(0.40, ship) == 0.40

    def test_lattice_module_counts(self):
        svc = ContrabandService(db=None)  # type: ignore[arg-type]
        ship = SimpleNamespace(
            equipment_slots={},
            modules={"installed": {"stealth": {"tier": 1}}},
        )
        assert svc._ship_has_stealth_systems(ship) is True
