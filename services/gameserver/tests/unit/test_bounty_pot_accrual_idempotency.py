"""LEG-935 — stored system-bounty pot daily accrual + idempotency pins.

Verify-first: accrual logic lives in BountyService.accrue_system_bounty_pot;
the scheduler shell is economy_sweeps._run_bounty_accrual_sweep_sync, driven from
core_loop on the canonical-day tick. These tests prove the service-layer contract
the sweep relies on: one period's worth per call, cap-bound growth, duplicate-period
no-op (restart / duplicate wake safe).
"""
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from src.services import bounty_service as bs


@pytest.fixture(autouse=True)
def _noop_flag_modified(monkeypatch):
    monkeypatch.setattr(bs, "flag_modified", lambda *a, **k: None)


def _criminal(rep: int = -600, pot: int = 0, pot_period=None):
    settings = {}
    if pot:
        settings[bs.SYSTEM_BOUNTY_POT_KEY] = pot
    if pot_period is not None:
        settings[bs.SYSTEM_BOUNTY_POT_PERIOD_KEY] = pot_period
    return SimpleNamespace(
        id=uuid4(),
        personal_reputation=rep,
        settings=settings,
    )


def test_accrue_once_per_canonical_day_adds_daily():
    player = _criminal(rep=-600)
    daily = bs.BountyService.system_bounty_daily_accrual(player)
    assert daily == bs.SYSTEM_BOUNTY_BASE_ACCRUAL_PER_DAY  # shallow criminal tier

    added = bs.BountyService.accrue_system_bounty_pot(player, period=10)
    assert added == daily
    assert bs.BountyService.get_system_bounty_pot(player) == daily
    assert player.settings[bs.SYSTEM_BOUNTY_POT_PERIOD_KEY] == 10


def test_accrue_respects_tier_cap():
    cap = bs.BountyService.system_bounty_pot_cap(_criminal(rep=-500))
    assert cap == 5000
    player = _criminal(rep=-500, pot=4900)
    added = bs.BountyService.accrue_system_bounty_pot(player, period=1)
    assert added == 100
    assert bs.BountyService.get_system_bounty_pot(player) == cap


def test_duplicate_period_wake_is_idempotent():
    player = _criminal(rep=-800)
    first = bs.BountyService.accrue_system_bounty_pot(player, period=42)
    assert first > 0
    pot_after_first = bs.BountyService.get_system_bounty_pot(player)

    second = bs.BountyService.accrue_system_bounty_pot(player, period=42)
    assert second == 0
    assert bs.BountyService.get_system_bounty_pot(player) == pot_after_first
    assert player.settings[bs.SYSTEM_BOUNTY_POT_PERIOD_KEY] == 42


def test_next_canonical_day_accrues_again_after_duplicate_skip():
    player = _criminal(rep=-600)
    bs.BountyService.accrue_system_bounty_pot(player, period=5)
    pot_day5 = bs.BountyService.get_system_bounty_pot(player)

    assert bs.BountyService.accrue_system_bounty_pot(player, period=5) == 0
    added_day6 = bs.BountyService.accrue_system_bounty_pot(player, period=6)
    assert added_day6 > 0
    assert bs.BountyService.get_system_bounty_pot(player) == pot_day5 + added_day6


def test_non_criminal_does_not_grow_pot_but_advances_anchor():
    player = _criminal(rep=0)
    added = bs.BountyService.accrue_system_bounty_pot(player, period=3)
    assert added == 0
    assert bs.BountyService.get_system_bounty_pot(player) == 0
    assert player.settings[bs.SYSTEM_BOUNTY_POT_PERIOD_KEY] == 3


def test_core_loop_wires_bounty_accrual_sweep_on_canonical_tick():
    core_loop = (
        Path(__file__).resolve().parents[2]
        / "src"
        / "services"
        / "scheduler"
        / "core_loop.py"
    )
    source = core_loop.read_text(encoding="utf-8")
    assert "_run_bounty_accrual_sweep_sync" in source
    assert "asyncio.to_thread(_run_bounty_accrual_sweep_sync)" in source
