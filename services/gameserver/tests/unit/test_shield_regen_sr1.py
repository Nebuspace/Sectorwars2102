"""Regression pin for WO-SR1 between-battle shield regen
(WO-SECA-PIN-TESTS Lane A).

Pins CombatService._apply_shield_regen (combat_service.py:2070-2136) — the
out-of-combat shield credit advanced on read at the start of each battle
(:2067, _ensure_combat_state). DB-free: CombatService(db=None) is safe here
because the method under test only reads/writes the ``combat`` dict passed to
it; it never touches self.db.

Time is frozen (monkeypatching the module's ``datetime`` symbol) so every
expected credit is computed exactly, with no wall-clock-jitter tolerance.
Anchors are placed at WALL-clock hours-ago derived from the desired
CANONICAL elapsed hours divided by the live GAME_TIME_SCALE, so the pin holds
whether the suite runs at scale 1.0 (Mac, fake env) or scale 144 (deployed
dev, per combat_service.py:459-461) — expectations are always computed via
the same canonical_hours_since helper combat_service imports, never a raw
wall-clock delta.
"""
from datetime import datetime, timedelta, timezone

import pytest

from src.core.game_time import GAME_TIME_SCALE, canonical_hours_since
from src.services import combat_service as cs
from src.services.combat_service import CombatService

FIXED_NOW = datetime(2026, 7, 1, 12, 0, 0, tzinfo=timezone.utc)


class _FrozenDateTime(datetime):
    """datetime.now() pinned to FIXED_NOW; fromisoformat/replace/arithmetic
    all behave exactly like the real class."""

    @classmethod
    def now(cls, tz=None):
        return FIXED_NOW.astimezone(tz) if tz else FIXED_NOW


@pytest.fixture(autouse=True)
def _freeze_time(monkeypatch):
    monkeypatch.setattr(cs, "datetime", _FrozenDateTime)


def _service():
    return CombatService(db=None)


def _anchor_for_canonical_hours(canonical_hours: float) -> datetime:
    """A UTC anchor whose canonical-hours gap to FIXED_NOW is exactly
    ``canonical_hours``, regardless of the live GAME_TIME_SCALE."""
    wall_hours_ago = canonical_hours / GAME_TIME_SCALE
    return FIXED_NOW - timedelta(hours=wall_hours_ago)


# --------------------------------------------------------------------------- #
# (1) First touch — no anchor key yet
# --------------------------------------------------------------------------- #

def test_first_touch_seeds_anchor_and_credits_nothing():
    """No SHIELD_REGEN_ANCHOR_KEY in the combat dict: baseline set to now,
    0.0 credited, shields untouched (:2095-2099)."""
    svc = _service()
    combat = {"shields": 10.0, "max_shields": 100.0, "shield_recharge_rate": 5.0, "hull": 80}
    credited = svc._apply_shield_regen(combat)
    assert credited == 0.0
    assert combat["shields"] == 10.0
    assert combat[svc.SHIELD_REGEN_ANCHOR_KEY] == FIXED_NOW.isoformat()
    assert combat["hull"] == 80  # hull is repair-only; regen never touches it


# --------------------------------------------------------------------------- #
# (2) Corrupt anchor
# --------------------------------------------------------------------------- #

def test_corrupt_anchor_resets_baseline_and_credits_nothing():
    """A malformed anchor string resets the baseline to now and credits
    0.0 (:2101-2108) — same shape as first-touch."""
    svc = _service()
    combat = {
        "shields": 10.0, "max_shields": 100.0, "shield_recharge_rate": 5.0, "hull": 42,
        svc.SHIELD_REGEN_ANCHOR_KEY: "not-a-timestamp",
    }
    credited = svc._apply_shield_regen(combat)
    assert credited == 0.0
    assert combat["shields"] == 10.0
    assert combat[svc.SHIELD_REGEN_ANCHOR_KEY] == FIXED_NOW.isoformat()
    assert combat["hull"] == 42


# --------------------------------------------------------------------------- #
# (3) Valid anchor — sub-cap credit, and the >24h-idle clamp
# --------------------------------------------------------------------------- #

def test_valid_anchor_credits_rate_times_elapsed_hours_subcap():
    """A 3-canonical-hour-old anchor (well under SHIELD_REGEN_MAX_CREDIT_HOURS)
    credits exactly rate * elapsed_hours (:2121-2129)."""
    svc = _service()
    anchor = _anchor_for_canonical_hours(3.0)
    combat = {
        "shields": 10.0, "max_shields": 1000.0, "shield_recharge_rate": 5.0, "hull": 80,
        svc.SHIELD_REGEN_ANCHOR_KEY: anchor.isoformat(),
    }
    elapsed_hours = canonical_hours_since(anchor, FIXED_NOW)
    assert elapsed_hours < svc.SHIELD_REGEN_MAX_CREDIT_HOURS  # genuinely sub-cap

    expected_new = min(1000.0, 10.0 + 5.0 * elapsed_hours)
    expected_credit = round(expected_new - 10.0, 1)

    credited = svc._apply_shield_regen(combat)
    assert credited == expected_credit
    assert combat["shields"] == round(expected_new, 1)
    assert combat[svc.SHIELD_REGEN_ANCHOR_KEY] == FIXED_NOW.isoformat()
    assert combat["hull"] == 80


def test_valid_anchor_idle_gap_clamped_at_max_credit_hours():
    """A 100-canonical-hour-old anchor (well past the 24h window) credits as
    if only SHIELD_REGEN_MAX_CREDIT_HOURS had elapsed — a long-dormant ship
    doesn't snap to full in one jump (:2125-2127)."""
    svc = _service()
    anchor = _anchor_for_canonical_hours(100.0)
    combat = {
        "shields": 10.0, "max_shields": 1000.0, "shield_recharge_rate": 5.0, "hull": 80,
        svc.SHIELD_REGEN_ANCHOR_KEY: anchor.isoformat(),
    }
    elapsed_hours = canonical_hours_since(anchor, FIXED_NOW)
    assert elapsed_hours > svc.SHIELD_REGEN_MAX_CREDIT_HOURS  # genuinely over-cap

    capped_hours = svc.SHIELD_REGEN_MAX_CREDIT_HOURS
    expected_new = min(1000.0, 10.0 + 5.0 * capped_hours)
    expected_credit = round(expected_new - 10.0, 1)

    credited = svc._apply_shield_regen(combat)
    assert credited == expected_credit
    assert combat["shields"] == round(expected_new, 1)
    assert combat["hull"] == 80


# --------------------------------------------------------------------------- #
# (4) Shields never exceed max_shields
# --------------------------------------------------------------------------- #

def test_shields_never_exceed_max_shields():
    """A large rate over a real elapsed gap would overshoot max_shields
    without the clamp at :2130 — assert it lands exactly at the cap."""
    svc = _service()
    anchor = _anchor_for_canonical_hours(3.0)
    combat = {
        "shields": 95.0, "max_shields": 100.0, "shield_recharge_rate": 50.0, "hull": 80,
        svc.SHIELD_REGEN_ANCHOR_KEY: anchor.isoformat(),
    }
    credited = svc._apply_shield_regen(combat)
    assert combat["shields"] == 100.0
    assert credited == 5.0
    assert combat["hull"] == 80


# --------------------------------------------------------------------------- #
# (5) Anchor always advances, even on no-op paths
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize(
    "shields,max_shields,rate",
    [
        (10.0, 100.0, 0.0),      # rate <= 0
        (0.0, 0.0, 5.0),         # max_shields <= 0
        (100.0, 100.0, 5.0),     # already full
    ],
    ids=["zero-rate", "zero-max-shields", "already-full"],
)
def test_anchor_advances_on_every_no_op_path(shields, max_shields, rate):
    """Even when nothing is credited, the anchor advances to now so a no-op
    read doesn't bank elapsed time for a later window (:2114-2119)."""
    svc = _service()
    anchor = _anchor_for_canonical_hours(5.0)
    combat = {
        "shields": shields, "max_shields": max_shields, "shield_recharge_rate": rate,
        "hull": 33, svc.SHIELD_REGEN_ANCHOR_KEY: anchor.isoformat(),
    }
    credited = svc._apply_shield_regen(combat)
    assert credited == 0.0
    assert combat["shields"] == shields  # untouched
    assert combat[svc.SHIELD_REGEN_ANCHOR_KEY] == FIXED_NOW.isoformat()
    assert combat["hull"] == 33
