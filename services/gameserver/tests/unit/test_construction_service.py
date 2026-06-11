"""Unit tests for the ship construction service (pure functions, no DB).

Covers the canon cost/duration tables, milestone math, resource checkpoint
math, the phase-progression state machine driven by a fake clock (explicit
`now` values + monkeypatched game_time.GAME_TIME_SCALE), forfeit splits, and
the rent forfeiture gate.
"""
from datetime import datetime, timedelta, UTC
from types import SimpleNamespace

import pytest

from src.core import game_time
from src.services import construction_service as cs


FIXED_NOW = datetime(2102, 6, 1, 12, 0, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def real_time_scale(monkeypatch):
    """Default every test to canonical time; tests override where needed."""
    monkeypatch.setattr(game_time, "GAME_TIME_SCALE", 1.0)


def make_reservation(
    ship_type="SCOUT_SHIP",
    state="deposit_collected",
    milestones=None,
    delivered=None,
    phase_deadline=None,
    rent_paid_until=None,
):
    """Lightweight stand-in carrying the attributes the pure engine reads."""
    cost = cs.SHIP_BUILD_SPECS[ship_type]["total_cost"]
    return SimpleNamespace(
        ship_type=ship_type,
        state=state,
        total_cost=cost,
        milestones=milestones if milestones is not None else {
            "deposit": True, "keel_laid": True, "hull_complete": False, "final": False,
        },
        resources_required=cs.resource_bundle(cost),
        resources_delivered=delivered if delivered is not None else {},
        phase_deadline=phase_deadline,
        hold_expires_at=None,
        claim_expires_at=None,
        rent_paid_until=rent_paid_until,
        rent_owed_since=None,
        updated_at=None,
    )


class TestCanonTables:
    """Canon: total project costs and build days per ship type."""

    @pytest.mark.parametrize("ship_type,cost,days", [
        ("SCOUT_SHIP", 40_000, 3),
        ("FAST_COURIER", 65_000, 4),
        ("LIGHT_FREIGHTER", 100_000, 5),
        ("CARGO_HAULER", 320_000, 7),
        ("DEFENDER", 380_000, 8),
        ("COLONY_SHIP", 640_000, 10),
        ("CARRIER", 1_900_000, 14),
        ("WARP_JUMPER", 1_000_000, 14),
    ])
    def test_cost_and_duration(self, ship_type, cost, days):
        spec = cs.SHIP_BUILD_SPECS[ship_type]
        assert spec["total_cost"] == cost
        assert spec["build_days"] == days

    def test_only_canon_types_buildable(self):
        assert set(cs.SHIP_BUILD_SPECS) == {
            "SCOUT_SHIP", "FAST_COURIER", "LIGHT_FREIGHTER", "CARGO_HAULER",
            "DEFENDER", "COLONY_SHIP", "CARRIER", "WARP_JUMPER",
        }

    def test_tier_a_gating_and_specialized_slips(self):
        assert cs.TIER_A_ONLY_TYPES == {"CARRIER", "WARP_JUMPER"}
        assert cs.SPECIALIZED_SLIP_TYPES == {"WARP_JUMPER"}
        assert cs.SLIP_POOLS == {
            "B": {"standard": 12, "specialized": 0},
            "A": {"standard": 8, "specialized": 4},
        }

    def test_phase_splits_cover_build_days(self):
        assert sum(cs.PHASE_SPLITS.values()) == pytest.approx(1.0)
        for ship_type, spec in cs.SHIP_BUILD_SPECS.items():
            total = sum(cs.phase_hours(ship_type, p) for p in cs.PHASE_ORDER)
            assert total == pytest.approx(spec["build_days"] * 24.0)

    def test_scout_phase_hours(self):
        # 3 days x 24h split 20/30/30/20
        assert cs.phase_hours("SCOUT_SHIP", "frame_assembly") == pytest.approx(14.4)
        assert cs.phase_hours("SCOUT_SHIP", "systems_integration") == pytest.approx(21.6)
        assert cs.phase_hours("SCOUT_SHIP", "outfitting") == pytest.approx(21.6)
        assert cs.phase_hours("SCOUT_SHIP", "final_assembly") == pytest.approx(14.4)


class TestMilestoneMath:
    """Milestones: deposit 10% / keel-laid 25% / hull-complete 25% / final 40%."""

    def test_scout_amounts(self):
        assert cs.milestone_amounts(40_000) == {
            "deposit": 4_000, "keel_laid": 10_000, "hull_complete": 10_000, "final": 16_000,
        }

    @pytest.mark.parametrize("ship_type", sorted(cs.SHIP_BUILD_SPECS))
    def test_amounts_sum_to_total_cost(self, ship_type):
        cost = cs.SHIP_BUILD_SPECS[ship_type]["total_cost"]
        assert sum(cs.milestone_amounts(cost).values()) == cost

    def test_phase_milestone_gates(self):
        # A phase will not START until its milestone is paid.
        assert cs.PHASE_MILESTONE_GATE == {
            "frame_assembly": "keel_laid",
            "outfitting": "hull_complete",
        }


class TestResourceBundle:
    """Per 1,000 credits of total cost: 5 ore, 2 equipment, 1 organics."""

    def test_scout_bundle(self):
        assert cs.resource_bundle(40_000) == {"ore": 200, "equipment": 80, "organics": 40}

    def test_carrier_bundle(self):
        assert cs.resource_bundle(1_900_000) == {
            "ore": 9_500, "equipment": 3_800, "organics": 1_900,
        }


class TestCheckpointMath:
    """frame >= 25% of everything; systems 100% ore+equipment / 50% organics;
    outfitting 100% all; final_assembly has no gate."""

    REQUIRED = {"ore": 200, "equipment": 80, "organics": 40}

    def test_frame_checkpoint_shortfall_from_zero(self):
        assert cs.checkpoint_shortfall(self.REQUIRED, {}, "frame_assembly") == {
            "ore": 50, "equipment": 20, "organics": 10,
        }

    def test_frame_checkpoint_met_at_quarter(self):
        delivered = {"ore": 50, "equipment": 20, "organics": 10}
        assert cs.checkpoint_met(self.REQUIRED, delivered, "frame_assembly") is True

    def test_systems_checkpoint_organics_half(self):
        delivered = {"ore": 200, "equipment": 80, "organics": 20}
        assert cs.checkpoint_met(self.REQUIRED, delivered, "systems_integration") is True

    def test_systems_checkpoint_blocks_on_one_unit(self):
        delivered = {"ore": 200, "equipment": 80, "organics": 19}
        assert cs.checkpoint_shortfall(
            self.REQUIRED, delivered, "systems_integration"
        ) == {"organics": 1}

    def test_outfitting_requires_everything(self):
        delivered = {"ore": 200, "equipment": 79, "organics": 40}
        assert cs.checkpoint_shortfall(self.REQUIRED, delivered, "outfitting") == {
            "equipment": 1,
        }

    def test_final_assembly_has_no_resource_gate(self):
        assert cs.checkpoint_shortfall(self.REQUIRED, {}, "final_assembly") == {}

    def test_fractional_requirements_round_up(self):
        # 25% of 41 organics = 10.25 -> need 11 (ceil), not 10.
        required = {"ore": 0, "equipment": 0, "organics": 41}
        shortfall = cs.checkpoint_shortfall(required, {"organics": 10}, "frame_assembly")
        assert shortfall == {"organics": 1}


class TestPhaseStartBlockers:
    def test_unpaid_milestone_blocks_frame(self):
        res = make_reservation(
            milestones={"deposit": True, "keel_laid": False},
            delivered={"ore": 200, "equipment": 80, "organics": 40},
        )
        blockers = cs.phase_start_blockers(res, "frame_assembly")
        assert len(blockers) == 1 and "keel_laid" in blockers[0]

    def test_resource_shortfall_blocks_frame(self):
        res = make_reservation()  # keel paid, nothing delivered
        blockers = cs.phase_start_blockers(res, "frame_assembly")
        assert len(blockers) == 1 and "checkpoint" in blockers[0]

    def test_clear_when_gates_met(self):
        res = make_reservation(delivered={"ore": 50, "equipment": 20, "organics": 10})
        assert cs.phase_start_blockers(res, "frame_assembly") == []


class TestPhaseProgression:
    """State machine driven with a fake clock (explicit `now`, scale 1.0)."""

    def test_frame_starts_when_gates_met(self):
        res = make_reservation(delivered={"ore": 50, "equipment": 20, "organics": 10})
        assert cs._progress_phases(res, FIXED_NOW) is True
        assert res.state == "frame_assembly"
        assert res.phase_deadline == FIXED_NOW + timedelta(hours=14.4)

    def test_blocked_build_stays_paused(self):
        res = make_reservation()  # keel paid but no resources
        assert cs._progress_phases(res, FIXED_NOW) is False
        assert res.state == "deposit_collected"
        assert res.phase_deadline is None

    def test_phase_completes_and_chains_to_next(self):
        full = dict(cs.resource_bundle(40_000))
        res = make_reservation(
            state="frame_assembly",
            delivered=full,
            phase_deadline=FIXED_NOW - timedelta(minutes=1),
        )
        cs._progress_phases(res, FIXED_NOW)
        assert res.state == "systems_integration"
        # Next phase anchors at the PREVIOUS deadline — no build time lost.
        assert res.phase_deadline == (
            FIXED_NOW - timedelta(minutes=1) + timedelta(hours=21.6)
        )

    def test_pauses_at_outfitting_until_hull_milestone_paid(self):
        full = dict(cs.resource_bundle(40_000))
        res = make_reservation(
            state="frame_assembly",
            delivered=full,
            phase_deadline=FIXED_NOW - timedelta(days=10),  # long absence
        )
        cs._progress_phases(res, FIXED_NOW)
        # frame + systems elapsed; outfitting gated on hull_complete (unpaid).
        assert res.state == "outfitting"
        assert res.phase_deadline is None

    def test_unpauses_with_full_duration_from_now(self):
        full = dict(cs.resource_bundle(40_000))
        res = make_reservation(
            state="outfitting",
            milestones={"deposit": True, "keel_laid": True, "hull_complete": True, "final": False},
            delivered=full,
            phase_deadline=None,
        )
        cs._progress_phases(res, FIXED_NOW)
        assert res.state == "outfitting"
        assert res.phase_deadline == FIXED_NOW + timedelta(hours=21.6)

    def test_full_catchup_to_complete_with_claim_window(self):
        """Player away for weeks: phases chain off each deadline and the claim
        window anchors at the final_assembly end, not at `now`."""
        full = dict(cs.resource_bundle(40_000))
        start = FIXED_NOW - timedelta(days=30)
        res = make_reservation(
            state="frame_assembly",
            milestones={"deposit": True, "keel_laid": True, "hull_complete": True, "final": False},
            delivered=full,
            phase_deadline=start,
        )
        cs._progress_phases(res, FIXED_NOW)
        assert res.state == "complete"
        assert res.phase_deadline is None
        # systems 21.6h + outfitting 21.6h + final 14.4h after frame end,
        # then the 7-canonical-day claim window.
        final_end = start + timedelta(hours=21.6 + 21.6 + 14.4)
        assert res.claim_expires_at == final_end + timedelta(hours=7 * 24)

    def test_running_phase_unchanged_before_deadline(self):
        deadline = FIXED_NOW + timedelta(hours=2)
        res = make_reservation(
            state="frame_assembly",
            delivered={"ore": 50, "equipment": 20, "organics": 10},
            phase_deadline=deadline,
        )
        assert cs._progress_phases(res, FIXED_NOW) is False
        assert res.state == "frame_assembly"
        assert res.phase_deadline == deadline

    def test_game_time_scale_compresses_deadlines(self, monkeypatch):
        monkeypatch.setattr(game_time, "GAME_TIME_SCALE", 144.0)
        res = make_reservation(delivered={"ore": 50, "equipment": 20, "organics": 10})
        cs._progress_phases(res, FIXED_NOW)
        # 14.4 canonical hours at scale 144 -> 6 wall-clock minutes.
        assert res.phase_deadline == FIXED_NOW + timedelta(minutes=6)


class TestForfeitSplits:
    """Hold forfeit: deposit splits 50% to next-in-queue, 50% to treasury."""

    def test_even_split(self):
        assert cs.split_forfeited_deposit(4_000) == (2_000, 2_000)

    def test_odd_credit_goes_to_treasury(self):
        to_next, to_treasury = cs.split_forfeited_deposit(4_001)
        assert (to_next, to_treasury) == (2_000, 2_001)
        assert to_next + to_treasury == 4_001

    def test_claim_forfeit_refund_is_seventy_percent(self):
        # Canon sell-back minus 30%.
        assert cs.claim_forfeit_refund(40_000) == 28_000
        assert cs.claim_forfeit_refund(1_900_000) == 1_330_000


class TestCancelRefunds:
    def test_half_refund_before_hull_complete(self):
        assert cs.cancel_refund(10_000, hull_complete_paid=False) == 5_000

    def test_seventy_percent_sellback_after_hull_complete(self):
        assert cs.cancel_refund(10_000, hull_complete_paid=True) == 7_000

    def test_deposit_only_cancel(self):
        # Cancelling straight from the queue: half the deposit back.
        deposit = cs.milestone_amounts(40_000)["deposit"]
        assert cs.cancel_refund(deposit, hull_complete_paid=False) == 2_000


class TestRent:
    def test_daily_rent_is_half_percent(self):
        assert cs.daily_rent(40_000) == 200
        assert cs.daily_rent(1_900_000) == 9_500

    def test_no_rent_outside_rent_states(self):
        res = make_reservation(state="queued", rent_paid_until=FIXED_NOW - timedelta(days=10))
        assert cs.rent_overdue_canonical_days(res, FIXED_NOW) == 0.0
        assert cs.rent_owed_amount(res, FIXED_NOW) == 0

    def test_paid_up_owes_nothing(self):
        res = make_reservation(rent_paid_until=FIXED_NOW + timedelta(days=2))
        assert cs.rent_owed_amount(res, FIXED_NOW) == 0
        assert cs._rent_forfeit_due(res, FIXED_NOW) is False

    def test_owed_accrues_per_started_day(self):
        res = make_reservation(rent_paid_until=FIXED_NOW - timedelta(days=1, hours=1))
        assert cs.rent_overdue_canonical_days(res, FIXED_NOW) == pytest.approx(25 / 24)
        # Two started canonical days -> 2 x 200 for a Scout.
        assert cs.rent_owed_amount(res, FIXED_NOW) == 400

    def test_forfeit_at_three_canonical_days(self):
        res = make_reservation(rent_paid_until=FIXED_NOW - timedelta(days=2, hours=23))
        assert cs._rent_forfeit_due(res, FIXED_NOW) is False
        res.rent_paid_until = FIXED_NOW - timedelta(days=3)
        assert cs._rent_forfeit_due(res, FIXED_NOW) is True

    def test_scale_compresses_rent_forfeiture(self, monkeypatch):
        # At scale 144, 30 wall minutes = 72 canonical hours = 3 days.
        monkeypatch.setattr(game_time, "GAME_TIME_SCALE", 144.0)
        res = make_reservation(rent_paid_until=FIXED_NOW - timedelta(minutes=30))
        assert cs._rent_forfeit_due(res, FIXED_NOW) is True
        res.rent_paid_until = FIXED_NOW - timedelta(minutes=29)
        assert cs._rent_forfeit_due(res, FIXED_NOW) is False


class TestStateSets:
    """Slip accounting and lifecycle sets stay consistent with the canon machine."""

    def test_hold_reserves_and_builds_occupy(self):
        assert "hold_active" in cs.SLIP_HOLDING_STATES
        assert cs.RENT_STATES == cs.SLIP_HOLDING_STATES - {"hold_active"}

    def test_terminal_states(self):
        assert cs.TERMINAL_STATES == {"claimed", "cancelled", "forfeited"}

    def test_delivery_window(self):
        # Deliveries open once the slip is secured, close after outfitting.
        assert cs.DELIVERY_STATES == {
            "deposit_collected", "frame_assembly", "systems_integration", "outfitting",
        }
