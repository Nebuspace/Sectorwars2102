"""Unit tests for the port ownership service (pure functions, no DB).

Mirrors test_construction_service.py: SimpleNamespace stand-ins exercise the
pure cores directly — listability exclusions, the price clamp, sealed-bid
resolution, the reputation gate, takeover month evaluation (consecutive
reset + eligible transition), the forced-sale clamp, and the bot-farming
dispute heuristic. The DB wrappers are thin layers over these cores.
"""
from datetime import datetime, timedelta, UTC
from types import SimpleNamespace

import pytest

from src.core import game_time
from src.services import port_ownership_service as po


FIXED_NOW = datetime(2102, 6, 1, 12, 0, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def real_time_scale(monkeypatch):
    """Default every test to canonical time; tests override where needed."""
    monkeypatch.setattr(game_time, "GAME_TIME_SCALE", 1.0)


def make_station(
    station_class=1,
    is_player_ownable=True,
    is_destroyed=False,
    is_spacedock=False,
    tradedock_tier=None,
    is_quest_hub=False,
    is_faction_headquarters=False,
):
    """Lightweight stand-in carrying the attributes is_listable reads."""
    return SimpleNamespace(
        station_class=SimpleNamespace(value=station_class),
        is_player_ownable=is_player_ownable,
        is_destroyed=is_destroyed,
        is_spacedock=is_spacedock,
        tradedock_tier=tradedock_tier,
        is_quest_hub=is_quest_hub,
        is_faction_headquarters=is_faction_headquarters,
    )


def make_offer(bid, created_at=FIXED_NOW, player_id="p"):
    return SimpleNamespace(bid=bid, created_at=created_at, player_id=player_id)


def make_campaign(months_satisfied=0, last_evaluated_month=0, history=None):
    return SimpleNamespace(
        months_satisfied=months_satisfied,
        last_evaluated_month=last_evaluated_month,
        monthly_history=history if history is not None else [],
        status="building",
    )


class TestIsListable:
    """Canon: classes 0/4/5 are the ONLY class exclusions (1-3 / 6-11 are
    purchasable); never spacedocks, TradeDocks, population hubs (quest
    hubs / faction HQs station-side), destroyed or non-ownable stations."""

    @pytest.mark.parametrize("cls", [1, 2, 3, 6, 7, 8, 9, 10, 11])
    def test_purchasable_classes(self, cls):
        assert po.is_listable(make_station(station_class=cls)) is True

    @pytest.mark.parametrize("cls", [0, 4, 5])
    def test_excluded_classes(self, cls):
        assert po.is_listable(make_station(station_class=cls)) is False

    def test_purchasable_class_set_matches_canon(self):
        assert po.PURCHASABLE_CLASSES == {1, 2, 3, 6, 7, 8, 9, 10, 11}

    def test_spacedock_never_listable(self):
        assert po.is_listable(make_station(is_spacedock=True)) is False

    def test_tradedock_never_listable(self):
        assert po.is_listable(make_station(tradedock_tier="A")) is False
        assert po.is_listable(make_station(tradedock_tier="B")) is False

    def test_quest_hub_and_faction_hq_excluded(self):
        assert po.is_listable(make_station(is_quest_hub=True)) is False
        assert po.is_listable(make_station(is_faction_headquarters=True)) is False

    def test_non_ownable_and_destroyed_excluded(self):
        assert po.is_listable(make_station(is_player_ownable=False)) is False
        assert po.is_listable(make_station(is_destroyed=True)) is False

    def test_raw_int_class_value_accepted(self):
        # Defensive: enum .value or a raw int both work.
        station = make_station()
        station.station_class = 3
        assert po.is_listable(station) is True


class TestPriceClamp:
    """Canon clamp: [250,000, 2,000,000]; class bases inside canon's bands."""

    def test_floor(self):
        assert po.clamp_price(0) == 250_000
        assert po.clamp_price(249_999) == 250_000

    def test_ceiling(self):
        assert po.clamp_price(50_000_000) == 2_000_000
        assert po.clamp_price(2_000_001) == 2_000_000

    def test_passthrough_inside_band(self):
        assert po.clamp_price(700_000) == 700_000

    def test_class_bases_low_band(self):
        # Classes 1-3 low (~250k-400k).
        for cls in (1, 2, 3):
            assert 250_000 <= po.CLASS_BASE_PRICE[cls] <= 400_000

    def test_class_bases_mid_band(self):
        # Classes 6-7 mid (~450k-550k).
        for cls in (6, 7):
            assert 450_000 <= po.CLASS_BASE_PRICE[cls] <= 550_000

    def test_class_bases_high_band(self):
        # Classes 8-11 high (~600k-1.2M).
        for cls in (8, 9, 10, 11):
            assert 600_000 <= po.CLASS_BASE_PRICE[cls] <= 1_200_000

    def test_only_purchasable_classes_priced(self):
        assert set(po.CLASS_BASE_PRICE) == po.PURCHASABLE_CLASSES


class TestBusinessPriceWithTreasury:
    """Treasury-arbitrage guard: the clamp applies ONLY to the business
    component; the treasury is added ON TOP (it conveys 1:1 with the sale,
    so an owner can never launder >2M of treasury through the 2M ceiling)."""

    def test_treasury_added_on_top_of_ceiling(self):
        # 3M business clamps to 2M; 500k treasury rides on top.
        assert po.business_price_with_treasury(3_000_000, 500_000) == 2_500_000

    def test_treasury_added_on_top_of_floor(self):
        assert po.business_price_with_treasury(100_000, 50_000) == 300_000

    def test_treasury_never_lost_inside_band(self):
        assert po.business_price_with_treasury(700_000, 300_000) == 1_000_000

    def test_zero_treasury_is_plain_clamp(self):
        assert po.business_price_with_treasury(700_000, 0) == po.clamp_price(700_000)
        assert po.business_price_with_treasury(5_000_000, 0) == 2_000_000

    def test_huge_treasury_conveys_in_full(self):
        # A 10M treasury means a 10M+ price — the buyer receives it back
        # with the station, so this is value-neutral, not a clamp bypass.
        assert po.business_price_with_treasury(2_500_000, 10_000_000) == 12_000_000


class TestSealedBidResolution:
    """Highest bid wins; ties go to the earliest offer; single offer wins."""

    def test_single_offer_wins(self):
        offer = make_offer(500_000)
        winner, losers = po.pick_winner([offer])
        assert winner is offer
        assert losers == []

    def test_highest_bid_wins_multi(self):
        low = make_offer(500_000, player_id="low")
        high = make_offer(750_000, player_id="high")
        mid = make_offer(600_000, player_id="mid")
        winner, losers = po.pick_winner([low, high, mid])
        assert winner is high
        assert set(id(o) for o in losers) == {id(low), id(mid)}

    def test_tie_goes_to_earliest_offer(self):
        early = make_offer(600_000, created_at=FIXED_NOW, player_id="early")
        late = make_offer(600_000, created_at=FIXED_NOW + timedelta(minutes=5), player_id="late")
        winner, losers = po.pick_winner([late, early])
        assert winner is early
        assert losers == [late]

    def test_no_offers_raises(self):
        with pytest.raises(ValueError):
            po.pick_winner([])


class TestReputationGate:
    """Canon 'Trusted'+ mapped onto Player.reputation_tier's 8-tier personal
    scale as 'Heroic'+ (see service docstring for the mapping rationale)."""

    @pytest.mark.parametrize("tier", ["Heroic", "Legendary"])
    def test_allowed_tiers(self, tier):
        assert po.tier_allows_purchase(tier) is True

    @pytest.mark.parametrize("tier", [
        "Villain", "Criminal", "Outlaw", "Suspicious", "Neutral", "Lawful",
    ])
    def test_blocked_tiers(self, tier):
        assert po.tier_allows_purchase(tier) is False

    def test_unknown_tier_fails_closed(self):
        assert po.tier_allows_purchase("Trusted") is False  # not a personal tier
        assert po.tier_allows_purchase(None) is False
        assert po.tier_allows_purchase("") is False

    def test_tier_order_matches_personal_reputation_service(self):
        from src.services.personal_reputation_service import REPUTATION_TIERS
        assert po.TIER_ORDER == [t[2] for t in REPUTATION_TIERS]


class TestMonthSatisfied:
    """A month counts only with >50% share AND hostile pricing."""

    def test_share_must_exceed_half(self):
        assert po.month_satisfied(0.51, hostile=True) is True
        assert po.month_satisfied(0.50, hostile=True) is False  # strictly >
        assert po.month_satisfied(0.49, hostile=True) is False

    def test_hostility_required(self):
        assert po.month_satisfied(0.95, hostile=False) is False


class TestApplyMonth:
    """Consecutive-month accounting: satisfied months accumulate, a failed
    month resets to 0; history records every month; the evaluation cursor
    advances."""

    @staticmethod
    def record(month, satisfied):
        return {
            "month": month, "station_volume": 1000, "challenger_volume": 600,
            "share": 0.6, "hostile": satisfied, "satisfied": satisfied,
        }

    def test_satisfied_months_accumulate(self):
        campaign = make_campaign()
        po.apply_month(campaign, self.record(0, True))
        po.apply_month(campaign, self.record(1, True))
        assert campaign.months_satisfied == 2
        assert campaign.last_evaluated_month == 2
        assert [r["month"] for r in campaign.monthly_history] == [0, 1]

    def test_failed_month_resets_to_zero(self):
        campaign = make_campaign(months_satisfied=2, last_evaluated_month=2)
        po.apply_month(campaign, self.record(2, False))
        assert campaign.months_satisfied == 0
        assert campaign.last_evaluated_month == 3

    def test_reset_then_rebuild_requires_three_more(self):
        campaign = make_campaign()
        for month, ok in enumerate([True, True, False, True, True]):
            po.apply_month(campaign, self.record(month, ok))
        # Two satisfied since the reset — still short of the threshold.
        assert campaign.months_satisfied == 2
        assert campaign.months_satisfied < po.TAKEOVER_MONTHS_REQUIRED

    def test_eligible_transition_at_three_consecutive(self):
        campaign = make_campaign()
        for month in range(3):
            po.apply_month(campaign, self.record(month, True))
        assert campaign.months_satisfied == po.TAKEOVER_MONTHS_REQUIRED == 3

    def test_history_is_replaced_not_mutated(self):
        # JSONB change detection needs a NEW list object each fold.
        original = []
        campaign = make_campaign(history=original)
        po.apply_month(campaign, self.record(0, True))
        assert campaign.monthly_history is not original
        assert original == []


class TestForcedSalePrice:
    """clamp(avg-monthly-revenue x 12 x condition, acquisition_cost,
    2 x acquisition_cost); condition_multiplier is 1.0 in v1."""

    def test_condition_multiplier_is_v1_default(self):
        assert po.CONDITION_MULTIPLIER == 1.0

    def test_low_revenue_floors_at_acquisition_cost(self):
        # 10k/month x 12 = 120k < 500k floor.
        assert po.forced_sale_value(10_000, 500_000) == 500_000

    def test_high_revenue_caps_at_double_acquisition_cost(self):
        # 200k/month x 12 = 2.4M > 2 x 500k.
        assert po.forced_sale_value(200_000, 500_000) == 1_000_000

    def test_mid_revenue_passes_through(self):
        # 60k/month x 12 = 720k, inside [500k, 1M].
        assert po.forced_sale_value(60_000, 500_000) == 720_000

    def test_zero_revenue_still_pays_acquisition_cost(self):
        assert po.forced_sale_value(0, 300_000) == 300_000


class TestDisputeHeuristic:
    """Bot-farming auto-arbitration: matched volume = 2 x min(buy, sell)
    per commodity; campaign fails when the fraction exceeds 80%."""

    def test_pure_wash_trading_is_fully_matched(self):
        buys = {"ore": 50_000}
        sells = {"ore": 50_000}
        assert po.self_cancelling_fraction(buys, sells) == pytest.approx(1.0)

    def test_one_sided_volume_is_unmatched(self):
        assert po.self_cancelling_fraction({"ore": 100_000}, {}) == 0.0
        assert po.self_cancelling_fraction({}, {"ore": 100_000}) == 0.0

    def test_cross_commodity_volume_does_not_match(self):
        # Buying ore and selling fuel is legitimate trade, not a wash.
        assert po.self_cancelling_fraction({"ore": 50_000}, {"fuel": 50_000}) == 0.0

    def test_partial_wash_fraction(self):
        # 30k matched both ways out of 100k total -> 60%.
        buys = {"ore": 30_000}
        sells = {"ore": 70_000}
        assert po.self_cancelling_fraction(buys, sells) == pytest.approx(0.6)

    def test_no_volume_is_zero_not_division_error(self):
        assert po.self_cancelling_fraction({}, {}) == 0.0

    def test_threshold_boundary(self):
        # Exactly 80% does NOT fail the campaign (strictly > threshold).
        buys = {"ore": 40_000, "fuel": 10_000}
        sells = {"ore": 40_000, "organics": 10_000}
        fraction = po.self_cancelling_fraction(buys, sells)
        assert fraction == pytest.approx(0.8)
        assert not (fraction > po.BOT_FARM_FRACTION)


class TestCanonConstants:
    """Window and bound constants stay canon-true."""

    def test_grace_window_is_24_canonical_hours(self):
        assert po.GRACE_HOURS == 24.0

    def test_counter_window_is_7_canonical_days(self):
        assert po.COUNTER_WINDOW_HOURS == 7 * 24.0

    def test_scaled_month_is_30_canonical_days(self):
        assert po.MONTH_HOURS == 30 * 24.0

    def test_tax_bounds(self):
        assert (po.MIN_TAX_RATE, po.MAX_TAX_RATE) == (0.0, 0.25)

    def test_price_bounds(self):
        assert (po.PRICE_FLOOR, po.PRICE_CEILING) == (250_000, 2_000_000)

    def test_grace_window_scales_with_game_time(self, monkeypatch):
        monkeypatch.setattr(game_time, "GAME_TIME_SCALE", 144.0)
        deadline = game_time.scaled_deadline(po.GRACE_HOURS, start=FIXED_NOW)
        # 24 canonical hours at scale 144 -> 10 wall-clock minutes.
        assert deadline == FIXED_NOW + timedelta(minutes=10)

    def test_wall_cutoff_walks_backwards(self):
        cutoff = po._wall_cutoff(90, now=FIXED_NOW)
        assert cutoff == FIXED_NOW - timedelta(days=90)

    def test_month_bounds_anchor_and_scale(self, monkeypatch):
        monkeypatch.setattr(game_time, "GAME_TIME_SCALE", 144.0)
        start, end = po._month_bounds(FIXED_NOW, 1)
        # 30 canonical days at scale 144 -> 5 wall-clock hours per month.
        assert start == FIXED_NOW + timedelta(hours=5)
        assert end == FIXED_NOW + timedelta(hours=10)


class TestHostilityVerdict:
    """Hostile pricing = challenger's avg SELL unit price undercuts the
    prevailing station-pays price (avg snapshotted station_buy_price) by
    >=3%. Documented v1 fallback: months with NO snapshots are hostile=True
    (volume share is the primary gate; pricing is corroborative only —
    legacy months must not silently block canon takeovers)."""

    def test_undercut_is_hostile(self):
        # Station pays 100; challenger sells at 90 (< 97) -> hostile.
        assert po.hostility_verdict(90.0, 100.0) is True

    def test_at_market_is_not_hostile(self):
        assert po.hostility_verdict(100.0, 100.0) is False

    def test_three_percent_boundary_is_strict(self):
        # Exactly 97.0 against a 100.0 station-pays price is NOT hostile
        # (strictly below the 0.97 threshold).
        assert po.HOSTILE_UNDERCUT_FACTOR == 0.97
        assert po.hostility_verdict(97.0, 100.0) is False
        assert po.hostility_verdict(96.99, 100.0) is True

    def test_no_snapshots_falls_back_to_hostile(self):
        # avg_station_buy None = no station_buy_price snapshots that month.
        assert po.hostility_verdict(90.0, None) is True

    def test_no_challenger_sells_is_never_hostile(self):
        # No sell-side pricing to be hostile with — even without snapshots.
        assert po.hostility_verdict(None, 100.0) is False
        assert po.hostility_verdict(None, None) is False


class TestCatchUpPlan:
    """Lazy month catch-up is bounded: more than CATCHUP_EVAL_LIMIT pending
    months batch-skip the older ones in one step (counter reset, single
    history record) and only the trailing 3 are evaluated individually."""

    def test_limit_is_three(self):
        assert po.CATCHUP_EVAL_LIMIT == 3

    def test_under_limit_evaluates_everything(self):
        skipped, months = po.catch_up_plan(0, 3)
        assert skipped is None
        assert list(months) == [0, 1, 2]

    def test_no_pending_months(self):
        skipped, months = po.catch_up_plan(2, 2)
        assert skipped is None
        assert list(months) == []

    def test_over_limit_skips_older_months(self):
        # 10 pending months: skip 0-6 in one step, evaluate 7, 8, 9.
        skipped, months = po.catch_up_plan(0, 10)
        assert skipped == (0, 6)
        assert list(months) == [7, 8, 9]

    def test_skip_resumes_from_cursor(self):
        skipped, months = po.catch_up_plan(5, 12)
        assert skipped == (5, 8)
        assert list(months) == [9, 10, 11]

    def test_exactly_at_limit_skips_nothing(self):
        skipped, months = po.catch_up_plan(4, 7)
        assert skipped is None
        assert list(months) == [4, 5, 6]


class TestCounterMatchMonthSemantics:
    """counter_match compares owner vs challenger volume in the CURRENT
    IN-PROGRESS month (recomputed live) — comparing against a completed
    month the challenger already won made the match unwinnable."""

    def test_current_month_index_zero_at_start(self):
        assert po.current_month_index(FIXED_NOW, FIXED_NOW) == 0

    def test_current_month_index_mid_month(self):
        assert po.current_month_index(FIXED_NOW, FIXED_NOW + timedelta(days=29)) == 0

    def test_current_month_index_rolls_at_30_canonical_days(self):
        assert po.current_month_index(FIXED_NOW, FIXED_NOW + timedelta(days=30)) == 1
        assert po.current_month_index(FIXED_NOW, FIXED_NOW + timedelta(days=95)) == 3

    def test_current_month_index_scales_with_game_time(self, monkeypatch):
        monkeypatch.setattr(game_time, "GAME_TIME_SCALE", 144.0)
        # 30 canonical days at scale 144 -> 5 wall-clock hours per month.
        assert po.current_month_index(FIXED_NOW, FIXED_NOW + timedelta(hours=4)) == 0
        assert po.current_month_index(FIXED_NOW, FIXED_NOW + timedelta(hours=5)) == 1


class TestSettlementOwnerGuard:
    """Forced-sale settlement re-checks the owner: a station with NO owner
    fails the campaign; an owner CHANGE since eligibility resets the
    campaign to 'building' (the new owner gets a fresh run)."""

    def test_no_owner_fails(self):
        assert po.settlement_owner_guard(None, "abc") == "failed"
        assert po.settlement_owner_guard(None, None) == "failed"

    def test_owner_changed_resets(self):
        assert po.settlement_owner_guard("new-owner", "old-owner") == "reset"

    def test_same_owner_proceeds(self):
        assert po.settlement_owner_guard("abc", "abc") == "proceed"

    def test_uuid_vs_str_comparison_normalizes(self):
        import uuid as _uuid
        owner = _uuid.uuid4()
        assert po.settlement_owner_guard(owner, str(owner)) == "proceed"
        assert po.settlement_owner_guard(owner, str(_uuid.uuid4())) == "reset"

    def test_legacy_campaign_without_record_proceeds(self):
        # Campaigns that became eligible before the guard shipped have no
        # owner_at_eligibility record — settle normally.
        assert po.settlement_owner_guard("abc", None) == "proceed"

    def test_owner_at_eligibility_reads_latest_record(self):
        campaign = SimpleNamespace(monthly_history=[
            {"month": 0, "satisfied": True},
            {"owner_at_eligibility": "first-owner"},
            {"month": 1, "satisfied": False},
            {"owner_at_eligibility": "second-owner"},
        ])
        assert po._owner_at_eligibility(campaign) == "second-owner"

    def test_owner_at_eligibility_none_when_absent(self):
        campaign = SimpleNamespace(monthly_history=[{"month": 0, "satisfied": True}])
        assert po._owner_at_eligibility(campaign) is None
        assert po._owner_at_eligibility(SimpleNamespace(monthly_history=None)) is None
