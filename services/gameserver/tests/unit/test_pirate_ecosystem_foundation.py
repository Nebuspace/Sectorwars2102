"""Unit tests for pirate_ecosystem_service (WO-PIRATE-ECO-1, lanes A+B).

DB-free: the pure-core functions (score_holdings, compute_target_population,
suppression_modifier, would_exceed_max_population, update_cleansed_state) are
tested directly against plain fixtures/dicts -- no session at all. The four
DB-backed wrappers (compute_population_score, sum_kill_weights,
find_eligible_sectors, refresh_pirate_ecosystem_snapshot) are tested against
a bespoke in-memory fake Session (mirrors the established fake-session
pattern in test_bounty_service_nh2.py / test_region_invite_service.py),
scoped to exactly the query shapes this service issues -- not a general SQL
evaluator.

Kill-log fixtures are holding-CLEAR rows (region_id always present, per the
lane-A canon correction: PirateKillLog is canon-exact NOT NULL region_id) --
never bare ship-kill rows.

All timestamps are injected (no wall-clock sleeps); PirateHolding/
PirateKillLog/Region rows are constructed directly (never added to a real
session), matching the established `_make_invite`-style fixture pattern.
"""

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from src.models.pirate_holding import PirateHolding, PirateHoldingTier
from src.models.pirate_kill_log import PirateKillLog, PirateKillDisposition
from src.models.region import Region
from src.models.station import Station
from src.services import pirate_ecosystem_service as pes


# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------

def _holding(*, region_id, sector_id=1, tier=PirateHoldingTier.CAMP, owner_player_id=None):
    return PirateHolding(
        id=uuid.uuid4(),
        region_id=region_id,
        sector_id=sector_id,
        tier=tier,
        owner_player_id=owner_player_id,
        current_strength=1.0,
    )


def _kill_log(
    *,
    region_id,
    holding_id=None,
    tier=PirateHoldingTier.CAMP,
    kill_weight=1,
    created_at=None,
    disposition=PirateKillDisposition.CLEARED,
):
    return PirateKillLog(
        id=uuid.uuid4(),
        region_id=region_id,
        holding_id=holding_id or uuid.uuid4(),
        tier=tier,
        kill_weight=kill_weight,
        disposition=disposition,
        created_at=created_at or datetime.now(timezone.utc),
    )


def _region(*, total_sectors=1000, pirate_ecosystem_state=None):
    return Region(
        id=uuid.uuid4(),
        name=f"r-{uuid.uuid4().hex[:8]}",
        display_name="R",
        total_sectors=total_sectors,
        pirate_ecosystem_state=pirate_ecosystem_state,
    )


# ---------------------------------------------------------------------------
# Bespoke fake Session -- scoped to this service's actual query surface.
# Not a general SQL evaluator; supports exactly eq / in_ / ge filters and the
# whole-model / single-column / kill-weight-sum entity shapes
# pirate_ecosystem_service issues.
# ---------------------------------------------------------------------------

def _table_name(entity):
    tbl = getattr(entity, "__table__", None)
    if tbl is not None:
        return tbl.name
    cls = getattr(entity, "class_", None)
    if cls is not None:
        return cls.__table__.name
    tbl2 = getattr(entity, "table", None)
    if tbl2 is not None and hasattr(tbl2, "name"):
        return tbl2.name
    for child in entity.get_children():
        found = _table_name(child)
        if found:
            return found
    return None


class _FakeQuery:
    def __init__(self, rows, entities):
        self._rows = rows
        self._entities = entities
        self._criteria = []

    def filter(self, *criteria):
        self._criteria.extend(criteria)
        return self

    def _matches(self, row):
        for cond in self._criteria:
            key = cond.left.key
            value = getattr(row, key, None)
            rhs = cond.right.value if hasattr(cond.right, "value") else cond.right
            opname = getattr(cond.operator, "__name__", None)
            if opname == "eq":
                if value != rhs:
                    return False
            elif opname == "in_op":
                if value not in rhs:
                    return False
            elif opname == "ge":
                if value is None or not (value >= rhs):
                    return False
            else:
                raise NotImplementedError(f"fake query: unsupported operator {cond.operator!r}")
        return True

    def all(self):
        matched = [r for r in self._rows if self._matches(r)]
        # A bare single-column entity (db.query(Model.col)) returns tuples,
        # mirroring the real API -- detected by InstrumentedAttribute shape
        # (has both .key and .class_; a whole mapped class has neither).
        if (
            len(self._entities) == 1
            and hasattr(self._entities[0], "key")
            and hasattr(self._entities[0], "class_")
        ):
            key = self._entities[0].key
            return [(getattr(r, key),) for r in matched]
        return matched

    def scalar(self):
        # This service's only .scalar() call is sum_kill_weights' coalesced
        # SUM(kill_weight) aggregate.
        matched = [r for r in self._rows if self._matches(r)]
        return sum(getattr(r, "kill_weight", 0) for r in matched)


class _FakeSession:
    def __init__(self, holdings=None, kill_logs=None, stations=None):
        self._by_table = {
            "pirate_holdings": list(holdings or []),
            "pirate_kill_log": list(kill_logs or []),
            "stations": list(stations or []),
        }
        self.flush_count = 0

    def query(self, *entities):
        name = _table_name(entities[0])
        return _FakeQuery(self._by_table[name], entities)

    def flush(self):
        self.flush_count += 1


# ---------------------------------------------------------------------------
# Population score (pure core)
# ---------------------------------------------------------------------------

class TestScoreHoldings:
    def test_score_matrix(self):
        region_id = uuid.uuid4()
        holdings = [
            _holding(region_id=region_id, tier=PirateHoldingTier.CAMP),
            _holding(region_id=region_id, tier=PirateHoldingTier.CAMP),
            _holding(region_id=region_id, tier=PirateHoldingTier.OUTPOST),
            _holding(region_id=region_id, tier=PirateHoldingTier.STRONGHOLD),
            _holding(region_id=region_id, tier=PirateHoldingTier.CAMP, owner_player_id=uuid.uuid4()),
        ]
        # 2 camps(1 each) + 1 outpost(3) + 1 stronghold(10) = 15; the
        # captured camp contributes 0 (pirate-ecosystem.md:59-64, excluded
        # by owner_player_id IS NOT NULL). NOTE: the WO brief for this test
        # stated the matrix should total 14 -- that does not match canon's
        # own weight table (Camp 1 / Outpost 3 / Stronghold 10,
        # pirate-ecosystem.md:49-53): 2*1 + 1*3 + 1*10 = 15, not 14. Pinning
        # the canon-correct value here; flagged in the report as an
        # arithmetic discrepancy in the WO text, not a code bug.
        assert pes.score_holdings(holdings) == 15

    def test_capturing_stronghold_drops_exactly_ten(self):
        region_id = uuid.uuid4()
        stronghold = _holding(region_id=region_id, tier=PirateHoldingTier.STRONGHOLD)
        camp = _holding(region_id=region_id, tier=PirateHoldingTier.CAMP)
        before = pes.score_holdings([stronghold, camp])
        stronghold.owner_player_id = uuid.uuid4()  # captured
        after = pes.score_holdings([stronghold, camp])
        assert before - after == 10

    def test_empty_region_scores_zero(self):
        assert pes.score_holdings([]) == 0


class TestComputePopulationScoreDbWrapper:
    def test_filters_to_region_and_excludes_captured(self):
        region_a = uuid.uuid4()
        region_b = uuid.uuid4()
        holdings = [
            _holding(region_id=region_a, tier=PirateHoldingTier.OUTPOST),
            _holding(region_id=region_a, tier=PirateHoldingTier.CAMP, owner_player_id=uuid.uuid4()),
            _holding(region_id=region_b, tier=PirateHoldingTier.STRONGHOLD),  # different region
        ]
        db = _FakeSession(holdings=holdings)
        assert pes.compute_population_score(db, region_a) == 3


# ---------------------------------------------------------------------------
# Target population (pure core)
# ---------------------------------------------------------------------------

class TestComputeTargetPopulation:
    def test_standard_unmodified_target_is_35(self):
        assert pes.compute_target_population(1000) == 35.0  # 801-1200 (Standard) bucket

    def test_kill_weight_modifier_pinned(self):
        # kill_weight=5 -> suppression = 1 - 0.05*5 = 0.75 -> 35*0.75 = 26.25
        assert pes.compute_target_population(1000, kill_weight=5) == pytest.approx(26.25)

    def test_kill_weight_floor_pinned(self):
        # kill_weight=20 -> raw modifier = 1-1.0 = 0.0, floored to 0.20 -> 35*0.20 = 7.0
        assert pes.compute_target_population(1000, kill_weight=20) == pytest.approx(7.0)

    def test_cleansed_bonus_modifier_pinned(self):
        now = datetime(2026, 7, 9, tzinfo=timezone.utc)
        cleansed_at = now - timedelta(days=10)  # inside the 30-day bonus window
        # no kills: modifier 1.0 * 0.5 (cleansed bonus) = 0.5 -> 35*0.5 = 17.5
        assert pes.compute_target_population(
            1000, cleansed_at=cleansed_at, now=now
        ) == pytest.approx(17.5)

    def test_cleansed_bonus_expired_after_30_days_not_applied(self):
        now = datetime(2026, 7, 9, tzinfo=timezone.utc)
        cleansed_at = now - timedelta(days=31)
        assert pes.compute_target_population(
            1000, cleansed_at=cleansed_at, now=now
        ) == pytest.approx(35.0)

    def test_bucket_boundaries(self):
        assert pes.base_target_for_total_sectors(300) == 12.0
        assert pes.base_target_for_total_sectors(301) == 22.0
        assert pes.base_target_for_total_sectors(600) == 22.0
        assert pes.base_target_for_total_sectors(601) == 30.0
        assert pes.base_target_for_total_sectors(800) == 30.0
        assert pes.base_target_for_total_sectors(801) == 35.0
        assert pes.base_target_for_total_sectors(1200) == 35.0

    def test_extrapolation_above_1200_scales_linearly(self):
        # 1500 (Region's player_owned CHECK-constraint max) -> 35 * 1500/1200 = 43.75
        assert pes.base_target_for_total_sectors(1500) == pytest.approx(43.75)

    def test_zero_kill_weight_suppression_is_full_1_0_no_divide_by_zero(self):
        assert pes.suppression_modifier(0, cleansed_at=None) == 1.0


class TestSumKillWeights:
    def test_sums_within_window_excludes_other_region_and_stale(self):
        region_id = uuid.uuid4()
        now = datetime(2026, 7, 9, tzinfo=timezone.utc)
        logs = [
            _kill_log(region_id=region_id, kill_weight=3, created_at=now - timedelta(days=5)),
            _kill_log(region_id=region_id, kill_weight=10, created_at=now - timedelta(days=29)),
            _kill_log(region_id=region_id, kill_weight=1, created_at=now - timedelta(days=31)),  # stale
            _kill_log(region_id=uuid.uuid4(), kill_weight=99, created_at=now),  # other region
        ]
        db = _FakeSession(kill_logs=logs)
        assert pes.sum_kill_weights(db, region_id, days=30, now=now) == 13

    def test_no_kills_returns_zero(self):
        db = _FakeSession(kill_logs=[])
        assert pes.sum_kill_weights(db, uuid.uuid4(), now=datetime.now(timezone.utc)) == 0


# ---------------------------------------------------------------------------
# Population cap (pure core)
# ---------------------------------------------------------------------------

class TestWouldExceedMaxPopulation:
    def test_cap_boundary_stronghold_true_camp_false(self):
        total_sectors = 1000  # base_target=35 (Standard bucket), cap=52.5
        assert pes.would_exceed_max_population(43, PirateHoldingTier.STRONGHOLD, total_sectors) is True  # 53>52.5
        assert pes.would_exceed_max_population(43, PirateHoldingTier.CAMP, total_sectors) is False  # 44<=52.5

    def test_exact_cap_is_not_exceeded(self):
        # base=12 (<=300 bucket), cap=18. score 8 + stronghold(10) = 18 == cap
        # -> NOT exceeded (canon uses a strict > comparison, :406-408).
        assert pes.would_exceed_max_population(8, PirateHoldingTier.STRONGHOLD, 300) is False


# ---------------------------------------------------------------------------
# Cleansed-region detection (pure core, injected clock)
# ---------------------------------------------------------------------------

class TestUpdateCleansedState:
    def test_first_zero_sets_zero_population_since(self):
        state = dict(pes.DEFAULT_ECOSYSTEM_STATE)
        t0 = datetime(2026, 7, 1, tzinfo=timezone.utc)
        pes.update_cleansed_state(state, 0, t0)
        assert state["zero_population_since"] == t0.isoformat()
        assert state["cleansed_at"] is None

    def test_day_6_no_stamp(self):
        state = dict(pes.DEFAULT_ECOSYSTEM_STATE)
        t0 = datetime(2026, 7, 1, tzinfo=timezone.utc)
        pes.update_cleansed_state(state, 0, t0)
        pes.update_cleansed_state(state, 0, t0 + timedelta(days=6))
        assert state["cleansed_at"] is None

    def test_day_7_stamps(self):
        state = dict(pes.DEFAULT_ECOSYSTEM_STATE)
        t0 = datetime(2026, 7, 1, tzinfo=timezone.utc)
        pes.update_cleansed_state(state, 0, t0)
        t7 = t0 + timedelta(days=7)
        pes.update_cleansed_state(state, 0, t7)
        assert state["cleansed_at"] == t7.isoformat()

    def test_idempotent_rerun_never_restamps(self):
        state = dict(pes.DEFAULT_ECOSYSTEM_STATE)
        t0 = datetime(2026, 7, 1, tzinfo=timezone.utc)
        pes.update_cleansed_state(state, 0, t0)
        t7 = t0 + timedelta(days=7)
        pes.update_cleansed_state(state, 0, t7)
        first_stamp = state["cleansed_at"]

        t10 = t0 + timedelta(days=10)
        pes.update_cleansed_state(state, 0, t10)  # still zero -- must NOT re-stamp
        assert state["cleansed_at"] == first_stamp

    def test_population_returning_resets_for_next_first_zero(self):
        state = dict(pes.DEFAULT_ECOSYSTEM_STATE)
        t0 = datetime(2026, 7, 1, tzinfo=timezone.utc)
        pes.update_cleansed_state(state, 0, t0)  # first zero at t0
        t3 = t0 + timedelta(days=3)
        pes.update_cleansed_state(state, 5, t3)  # pirates return -- resets eligibility
        assert state["zero_population_since"] is None

        t3b = t3 + timedelta(hours=1)
        pes.update_cleansed_state(state, 0, t3b)  # zero again -- a NEW first-zero
        assert state["zero_population_since"] == t3b.isoformat()
        assert state["zero_population_since"] != t0.isoformat()


# ---------------------------------------------------------------------------
# Eligible-sector finder (DB-backed wrapper)
# ---------------------------------------------------------------------------

class TestFindEligibleSectors:
    def test_excludes_holding_and_station_occupied_sectors(self):
        region_id = uuid.uuid4()
        holdings = [_holding(region_id=region_id, sector_id=101)]
        stations = [Station(id=uuid.uuid4(), name="Outpost Alpha", sector_id=102)]
        db = _FakeSession(holdings=holdings, stations=stations)

        candidates = [100, 101, 102, 103]
        result = pes.find_eligible_sectors(db, region_id, candidates)
        assert result == [100, 103]

    def test_holding_in_a_different_region_does_not_exclude(self):
        region_id = uuid.uuid4()
        other_region = uuid.uuid4()
        holdings = [_holding(region_id=other_region, sector_id=101)]
        db = _FakeSession(holdings=holdings)

        result = pes.find_eligible_sectors(db, region_id, [100, 101])
        assert result == [100, 101]

    def test_empty_candidates_returns_empty(self):
        db = _FakeSession()
        assert pes.find_eligible_sectors(db, uuid.uuid4(), []) == []


# ---------------------------------------------------------------------------
# Region.pirate_ecosystem_state snapshot (read + refresh)
# ---------------------------------------------------------------------------

class TestGetPirateEcosystemState:
    def test_null_state_returns_default_without_writing_back(self):
        region = _region(pirate_ecosystem_state=None)
        result = pes.get_pirate_ecosystem_state(region)
        assert result == pes.DEFAULT_ECOSYSTEM_STATE
        assert region.pirate_ecosystem_state is None  # read-only -- confirms no write-back


class TestRefreshPirateEcosystemSnapshot:
    def test_lazy_inits_null_state_and_flushes(self):
        region = _region(total_sectors=1000, pirate_ecosystem_state=None)
        db = _FakeSession()
        assert db.flush_count == 0

        state = pes.refresh_pirate_ecosystem_snapshot(db, region, now=datetime.now(timezone.utc))

        assert db.flush_count == 1
        assert region.pirate_ecosystem_state is not None
        assert region.pirate_ecosystem_state["base_target"] == 35.0
        assert state["current_population_score"] == 0

    def test_refresh_moves_snapshot_when_a_holding_changes(self):
        region = _region(total_sectors=1000)
        holding = _holding(region_id=region.id, tier=PirateHoldingTier.OUTPOST)
        db = _FakeSession(holdings=[holding])
        now = datetime(2026, 7, 9, tzinfo=timezone.utc)

        state1 = pes.refresh_pirate_ecosystem_snapshot(db, region, now=now)
        assert state1["current_population_score"] == 3
        # Persisted onto the mapped attribute (JSONB mutation via
        # reassignment + flag_modified), not just returned and discarded.
        assert region.pirate_ecosystem_state["current_population_score"] == 3

        holding.owner_player_id = uuid.uuid4()  # captured -- pirate score drops to 0
        state2 = pes.refresh_pirate_ecosystem_snapshot(db, region, now=now + timedelta(hours=1))

        assert state2["current_population_score"] == 0
        assert region.pirate_ecosystem_state["current_population_score"] == 0
        assert state1["current_population_score"] != state2["current_population_score"]
