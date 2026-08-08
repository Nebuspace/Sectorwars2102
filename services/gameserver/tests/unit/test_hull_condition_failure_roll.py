"""WO-BUILD-HULL-FAILURE-TIER-DICE-ROLL — performance-band per-jump failure roll.

DB-free: SimpleNamespace ships + patched flag_modified. Covers band gating,
MINOR/MAJOR/CATASTROPHIC effects, immobilized helper, and Escape Pod skip.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import patch

from src.models.ship import FailureType, ShipType
from src.services import maintenance_service as ms


class _SeqRng:
    """Deterministic rng.random() sequence."""

    def __init__(self, values):
        self._values = list(values)

    def random(self):
        return self._values.pop(0)


def _ship(*, condition: float, ship_type=ShipType.LIGHT_FREIGHTER, **extra):
    maintenance = {
        "condition": condition,
        "last_maintenance": "2099-01-01T00:00:00+00:00",  # no decay
        "failure_status": "NONE",
    }
    maintenance.update(extra.pop("maintenance_extra", {}))
    return SimpleNamespace(
        id=uuid.uuid4(),
        type=ship_type,
        maintenance=maintenance,
        **extra,
    )


def test_pristine_band_never_rolls():
    ship = _ship(condition=95.0)
    with patch.object(ms, "flag_modified"):
        assert ms.apply_hull_condition_failure_roll(ship, rng=_SeqRng([0.0])) is None
        assert ship.maintenance["failure_status"] == "NONE"


def test_worn_band_zero_failure_chance():
    ship = _ship(condition=60.0)
    with patch.object(ms, "flag_modified"):
        assert ms.apply_hull_condition_failure_roll(ship, rng=_SeqRng([0.0])) is None


def test_minor_failure_sensors_offline():
    ship = _ship(condition=40.0)  # Degraded → 5% MINOR
    with patch.object(ms, "flag_modified"):
        outcome = ms.apply_hull_condition_failure_roll(ship, rng=_SeqRng([0.0]))
    assert outcome is not None
    assert outcome["failure_type"] == FailureType.MINOR.value
    assert outcome["effect"] == "sensors_offline"
    assert outcome["needs_destroy"] is False
    assert ship.maintenance["failure_status"] == FailureType.MINOR.value
    assert ship.maintenance["sensors_offline"] is True


def test_minor_miss_when_rng_above_chance():
    ship = _ship(condition=40.0)
    with patch.object(ms, "flag_modified"):
        # Degraded failure=0.05 — 0.05 is NOT < chance (uses >= miss)
        assert ms.apply_hull_condition_failure_roll(ship, rng=_SeqRng([0.05])) is None


def test_major_failure_immobilizes():
    ship = _ship(condition=20.0)  # Failing → 15% MAJOR
    with patch.object(ms, "flag_modified"):
        outcome = ms.apply_hull_condition_failure_roll(ship, rng=_SeqRng([0.0]))
    assert outcome["failure_type"] == FailureType.MAJOR.value
    assert outcome["effect"] == "immobilized"
    assert ms.ship_is_immobilized(ship) is True


def test_catastrophic_destroy_branch():
    ship = _ship(condition=5.0)  # Critical → 30% CATASTROPHIC
    with patch.object(ms, "flag_modified"):
        # first fires the band roll; second < 0.20 → destroy
        outcome = ms.apply_hull_condition_failure_roll(
            ship, rng=_SeqRng([0.0, 0.0])
        )
    assert outcome["needs_destroy"] is True
    assert outcome["effect"] == "destroyed"
    # maintenance left untouched for destroy path
    assert ship.maintenance["failure_status"] == "NONE"


def test_catastrophic_survive_drops_hull_to_one_percent():
    ship = _ship(condition=5.0)
    with patch.object(ms, "flag_modified"):
        # second >= 0.20 → survive at 1%
        outcome = ms.apply_hull_condition_failure_roll(
            ship, rng=_SeqRng([0.0, 0.20])
        )
    assert outcome["needs_destroy"] is False
    assert outcome["effect"] == "hull_critical"
    assert ship.maintenance["condition"] == ms.CATASTROPHIC_SURVIVE_CONDITION
    assert ship.maintenance["failure_status"] == FailureType.CATASTROPHIC.value
    assert ship.maintenance["repair_needed"] is True


def test_escape_pod_skipped():
    ship = _ship(condition=5.0, ship_type=ShipType.ESCAPE_POD)
    with patch.object(ms, "flag_modified"):
        assert ms.apply_hull_condition_failure_roll(ship, rng=_SeqRng([0.0, 0.0])) is None


def test_ship_is_immobilized_false_for_minor():
    ship = _ship(condition=40.0, maintenance_extra={"failure_status": "MINOR"})
    assert ms.ship_is_immobilized(ship) is False


def test_ship_is_immobilized_none_ship():
    assert ms.ship_is_immobilized(None) is False
