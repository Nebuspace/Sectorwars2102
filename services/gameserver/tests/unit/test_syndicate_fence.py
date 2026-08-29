"""LEG-300 Shadow Syndicate fence — eligibility, 8% roll, 70% payout, gate.

DB-free: dummy DATABASE_URL so conftest/settings import does not require Neon.
"""
from __future__ import annotations

import os
import random
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

os.environ.setdefault("ENVIRONMENT", "testing")
os.environ.setdefault("DATABASE_URL", "postgresql://ci:ci@127.0.0.1:5432/ci")
os.environ.setdefault("JWT_SECRET", "ci-test-jwt-secret-not-used-32chars!!")
os.environ.setdefault("ADMIN_USERNAME", "ci-admin-user")
os.environ.setdefault("ADMIN_PASSWORD", "ci-admin-pass-12")
os.environ.setdefault(
    "ARIA_ENCRYPTION_KEY", "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
)

from src.core.commodity_economy import base_price
from src.models.reputation import ReputationLevel
from src.services.syndicate_fence_service import (
    FENCE_ELIGIBLE_RATE,
    FENCE_PAYOUT_PERCENT,
    SyndicateFenceService,
    assign_has_syndicate_fence,
    cargo_fence_payout,
    consume_flagged_origin,
    host_station_fence_eligible,
    roll_has_syndicate_fence,
    syndicate_fence_tab_visible,
)


class _Rng:
    def __init__(self, value: float):
        self.value = value

    def random(self) -> float:
        return self.value


def test_payout_is_flat_70_percent_of_market():
    assert FENCE_PAYOUT_PERCENT == 70
    assert cargo_fence_payout(1000) == 700
    assert cargo_fence_payout(1) == 0  # floor
    ore = base_price("ore")
    assert cargo_fence_payout(ore * 10) == (ore * 10 * 70) // 100


def test_eligibility_excludes_federation_nova_tradedock():
    assert host_station_fence_eligible(tradedock_tier=None, faction_affiliation=None)
    assert host_station_fence_eligible(
        tradedock_tier=None, faction_affiliation="Mercantile Guild"
    )
    assert host_station_fence_eligible(
        tradedock_tier=None, faction_affiliation="Frontier Coalition"
    )
    assert host_station_fence_eligible(
        tradedock_tier=None, faction_affiliation="Astral Mining Consortium"
    )
    assert not host_station_fence_eligible(
        tradedock_tier="A", faction_affiliation=None
    )
    assert not host_station_fence_eligible(
        tradedock_tier="B", faction_affiliation="Independent"
    )
    assert not host_station_fence_eligible(
        tradedock_tier=None, faction_affiliation="Terran Federation"
    )
    assert not host_station_fence_eligible(
        tradedock_tier=None, faction_affiliation="Federation"
    )
    assert not host_station_fence_eligible(
        tradedock_tier=None, faction_affiliation="Nova Scientific Institute"
    )


def test_roll_rate_and_ineligible_never_true():
    assert FENCE_ELIGIBLE_RATE == 0.08
    assert roll_has_syndicate_fence(False, _Rng(0.0)) is False
    assert roll_has_syndicate_fence(True, _Rng(0.079)) is True
    assert roll_has_syndicate_fence(True, _Rng(0.08)) is False


def test_assign_tradedock_never_rolls_true():
    assert (
        assign_has_syndicate_fence(
            universe_seed=1,
            sector_int_id=99,
            name="TradeDock Prime",
            tradedock_tier="A",
            rng=_Rng(0.0),
        )
        is False
    )


def test_visibility_gate_neutral_and_personal_rep():
    assert syndicate_fence_tab_visible(
        syndicate_level=ReputationLevel.NEUTRAL, personal_reputation=0
    )
    assert syndicate_fence_tab_visible(
        syndicate_level=ReputationLevel.RECOGNIZED, personal_reputation=-1
    )
    assert not syndicate_fence_tab_visible(
        syndicate_level=None, personal_reputation=-500
    )
    assert not syndicate_fence_tab_visible(
        syndicate_level=ReputationLevel.QUESTIONABLE, personal_reputation=-10
    )
    assert not syndicate_fence_tab_visible(
        syndicate_level=ReputationLevel.NEUTRAL, personal_reputation=1
    )


def test_consume_flagged_origin_only():
    cargo = {
        "capacity": 50,
        "used": 10,
        "contents": {"ore": 10},
        "flagged_origin": {"ore": 4},
    }
    ok, reason, market = consume_flagged_origin(cargo, "ore", 4)
    assert ok and reason == "ok"
    assert market == base_price("ore") * 4
    assert cargo["contents"]["ore"] == 6
    assert "ore" not in cargo["flagged_origin"]
    assert cargo["used"] == 6
    ok2, reason2, _ = consume_flagged_origin(cargo, "ore", 1)
    assert not ok2 and reason2 == "insufficient_flagged_origin"


def test_fence_cargo_pays_70_and_consumes_flagged():
    station_id = uuid4()
    player = SimpleNamespace(
        id=uuid4(),
        credits=100,
        personal_reputation=0,
        is_docked=True,
        current_port_id=station_id,
        current_sector_id=12,
    )
    ship = SimpleNamespace(
        cargo={
            "capacity": 50,
            "used": 5,
            "contents": {"ore": 5},
            "flagged_origin": {"ore": 5},
        }
    )
    station = SimpleNamespace(
        id=station_id,
        has_syndicate_fence=True,
        sector_id=12,
    )
    svc = SyndicateFenceService(db=MagicMock())
    svc._syndicate_level = lambda _pid: ReputationLevel.NEUTRAL  # noqa: E731
    result = svc.fence_cargo(player, ship, station, "ore", 5)
    assert result["success"] is True
    expected = cargo_fence_payout(base_price("ore") * 5)
    assert result["payout"] == expected
    assert player.credits == 100 + expected
    assert ship.cargo["flagged_origin"] == {}


def test_api_py_mounts_syndicate_fence_router():
    root = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "..", "src", "api", "api.py")
    )
    text = open(root, encoding="utf-8").read()
    assert "syndicate_fence_router" in text
    assert "include_router(syndicate_fence_router" in text


def test_thousand_eligible_hosts_land_near_eight_percent():
    rng = random.Random(20260819)
    hits = sum(roll_has_syndicate_fence(True, rng) for _ in range(1000))
    # binomial 1000×0.08 → mean 80; keep a wide but non-vacuous band
    assert 50 <= hits <= 110
