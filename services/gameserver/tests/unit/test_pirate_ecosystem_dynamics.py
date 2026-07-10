"""Unit tests for pirate_ecosystem_service's WO-PIRATE-ECO-2 growth/evolution
engines: run_weekly_tick, spawn_daughter_holding, seed_spawn_camp,
evolution_tick, promote_holding_tier, top_attackers_by_kill_weight, and
update_cleansed_state_for_region.

Follows test_pirate_ecosystem_foundation.py's established pattern (a bespoke
in-memory fake Session, scoped to exactly the query shapes this service
issues -- not a general SQL evaluator), extended for this module's additional
query surface: Sector, SpecialFormation, and the grouped kill-weight
aggregate (top_attackers_by_kill_weight).

Randomness is injected via a deterministic _ScriptedRng (NOT random.Random)
so every spawn/evolution-chance outcome is pinned by construction rather than
seed-fragile.

The SpecialFormation anchor/interior-sector containment predicate
(`.contains()` on an ARRAY column) has no simple eq/in_/ge shape, so the fake
does not introspect it -- `_FakeQuery` matches SpecialFormation rows on
region_id only and treats the OR'd containment branch as always-satisfiable.
The real assertion for the Stronghold-promotion-gate tests below comes from
evolution_tick's own Python-side type filtering
(`f.type.name in _STRONGHOLD_FORMATION_TYPES`), not from the fake's query
matching -- see _eval_clause's docstring.
"""

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict

import pytest

from src.models.pirate_holding import PirateHolding, PirateHoldingTier
from src.models.pirate_kill_log import PirateKillLog, PirateKillDisposition
from src.models.region import Region, RegionStatus
from src.models.sector import Sector
from src.models.special_formation import SpecialFormation, SpecialFormationType
from src.services import pirate_ecosystem_service as pes


# ---------------------------------------------------------------------------
# Deterministic scripted RNG
# ---------------------------------------------------------------------------

class _ScriptedRng:
    """Stand-in for random.Random -- deliberately NOT seeded randomness.
    .choices()/.choice() always return the FIRST candidate (test fixtures are
    ordered so "first" is the behaviorally-interesting choice); .random()
    drains a pre-set queue of floats (default: [0.0], i.e. every probability
    roll succeeds -- EVOLUTION_CHANCE is always < 1.0, so 0.0 always beats
    it)."""

    def __init__(self, random_values=None):
        self._queue = list(random_values) if random_values is not None else [0.0]

    def choices(self, population, weights=None, k=1):
        return [population[0]] * k

    def choice(self, seq):
        return seq[0]

    def random(self):
        if len(self._queue) > 1:
            return self._queue.pop(0)
        return self._queue[0]


_NEVER_ROLLS = _ScriptedRng(random_values=[0.999])  # beats no EVOLUTION_CHANCE bucket


# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------

def _holding(
    *,
    region_id,
    sector_id=1,
    tier=PirateHoldingTier.CAMP,
    owner_player_id=None,
    current_strength=1.0,
    last_damage_at=None,
    created_at=None,
):
    return PirateHolding(
        id=uuid.uuid4(),
        region_id=region_id,
        sector_id=sector_id,
        tier=tier,
        owner_player_id=owner_player_id,
        current_strength=current_strength,
        last_damage_at=last_damage_at,
        created_at=created_at or datetime.now(timezone.utc),
    )


def _region(*, total_sectors=300, status=RegionStatus.ACTIVE.value, pirate_ecosystem_state=None):
    return Region(
        id=uuid.uuid4(),
        name=f"r-{uuid.uuid4().hex[:8]}",
        display_name="R",
        total_sectors=total_sectors,
        status=status,
        pirate_ecosystem_state=pirate_ecosystem_state,
    )


def _sectors(region_id, count, start=100):
    return [
        Sector(
            id=uuid.uuid4(),
            sector_id=start + i,
            sector_number=i,
            name=f"S{start + i}",
            region_id=region_id,
            cluster_id=uuid.uuid4(),
        )
        for i in range(count)
    ]


def _formation(*, region_id, anchor_sector_id, formation_type, interior_sector_ids=None):
    return SpecialFormation(
        id=uuid.uuid4(),
        region_id=region_id,
        type=formation_type,
        anchor_sector_id=anchor_sector_id,
        interior_sector_ids=interior_sector_ids or [],
    )


def _capture_broadcasts(monkeypatch):
    """Bypasses asyncio entirely -- monkeypatches the ONE shared
    ``_broadcast_pirate_event`` helper (every WO-PIRATE-ECO-2 lane C emitter
    routes through it) to a plain recorder, returning the ``(region_id,
    payload)`` call list. This is how "exactly once" / "emits nothing" get
    asserted deterministically: a plain sync test has no running event loop,
    so the REAL transport path (asyncio.get_running_loop().create_task(...))
    always silently no-ops there anyway -- this fixture observes the call
    that would have been scheduled, not the scheduling itself."""
    calls: list = []
    monkeypatch.setattr(
        pes, "_broadcast_pirate_event",
        lambda region_id, payload: calls.append((region_id, payload)),
    )
    return calls


def _kill_log(*, region_id, attacker_player_id=None, kill_weight=1, created_at=None):
    return PirateKillLog(
        id=uuid.uuid4(),
        region_id=region_id,
        holding_id=uuid.uuid4(),
        tier=PirateHoldingTier.CAMP,
        kill_weight=kill_weight,
        attacker_player_id=attacker_player_id,
        disposition=PirateKillDisposition.CLEARED,
        created_at=created_at or datetime.now(timezone.utc),
    )


# ---------------------------------------------------------------------------
# Bespoke fake Session -- extends the foundation suite's pattern with
# Sector / SpecialFormation / Region-scalar / grouped-aggregate support.
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


def _eval_clause(cond, row):
    """Evaluate one filter criterion against a fixture row. Recognizes plain
    eq / in_ / ge / is_ / is_not comparisons, plus OR'd clause lists (treats
    a branch this fake can't introspect, e.g. ARRAY .contains(), as
    always-satisfiable rather than raising -- see module docstring)."""
    if hasattr(cond, "clauses") and hasattr(cond, "operator"):
        results = []
        for sub in cond.clauses:
            try:
                results.append(_eval_clause(sub, row))
            except NotImplementedError:
                results.append(True)
        return any(results)

    key = cond.left.key
    value = getattr(row, key, None)
    opname = getattr(cond.operator, "__name__", None)
    rhs = cond.right.value if hasattr(cond.right, "value") else cond.right

    if opname == "eq":
        return value == rhs
    if opname == "in_op":
        return value in rhs
    if opname == "ge":
        return value is not None and value >= rhs
    if opname == "is_":
        return value is None
    if opname == "is_not":
        return value is not None
    raise NotImplementedError(f"fake query: unsupported operator {cond.operator!r}")


class _FakeQuery:
    def __init__(self, rows, entities):
        self._rows = rows
        self._entities = entities
        self._criteria = []
        self._group_by = None
        self._limit = None

    def filter(self, *criteria):
        self._criteria.extend(criteria)
        return self

    def group_by(self, *cols):
        self._group_by = cols
        return self

    def order_by(self, *cols):
        return self  # ordering is computed explicitly in .all()'s group-by path

    def limit(self, n):
        self._limit = n
        return self

    def _matches(self, row):
        return all(_eval_clause(c, row) for c in self._criteria)

    def all(self):
        matched = [r for r in self._rows if self._matches(r)]
        if self._group_by:
            # Bespoke to this module's ONE grouped-aggregate shape:
            # (attacker_player_id, sum(kill_weight)) grouped by attacker,
            # ordered desc, limited -- top_attackers_by_kill_weight.
            key_attr = self._group_by[0].key
            totals: Dict[Any, int] = {}
            for r in matched:
                k = getattr(r, key_attr)
                totals[k] = totals.get(k, 0) + getattr(r, "kill_weight", 0)
            ordered = sorted(totals.items(), key=lambda kv: kv[1], reverse=True)
            if self._limit is not None:
                ordered = ordered[: self._limit]
            return [(k, v) for k, v in ordered]
        if (
            len(self._entities) == 1
            and hasattr(self._entities[0], "key")
            and hasattr(self._entities[0], "class_")
        ):
            key = self._entities[0].key
            return [(getattr(r, key),) for r in matched]
        return matched

    def first(self):
        matched = [r for r in self._rows if self._matches(r)]
        return matched[0] if matched else None

    def scalar(self):
        matched = [r for r in self._rows if self._matches(r)]
        tbl = _table_name(self._entities[0])
        if tbl == "pirate_kill_log":
            return sum(getattr(r, "kill_weight", 0) for r in matched)
        key = getattr(self._entities[0], "key", None)
        if key and matched:
            return getattr(matched[0], key)
        return None


class _FakeSession:
    def __init__(self, *, holdings=None, sectors=None, stations=None, regions=None,
                 kill_logs=None, formations=None):
        self._by_table = {
            "pirate_holdings": list(holdings or []),
            "sectors": list(sectors or []),
            "stations": list(stations or []),
            "regions": list(regions or []),
            "pirate_kill_log": list(kill_logs or []),
            "special_formations": list(formations or []),
        }
        self.flush_count = 0

    def query(self, *entities):
        name = _table_name(entities[0])
        return _FakeQuery(self._by_table[name], entities)

    def add(self, obj):
        if getattr(obj, "id", None) is None:
            obj.id = uuid.uuid4()
        self._by_table[obj.__table__.name].append(obj)

    def flush(self):
        self.flush_count += 1


# ---------------------------------------------------------------------------
# _week_start (pure)
# ---------------------------------------------------------------------------

class TestWeekStart:
    def test_sunday_midnight_is_its_own_week_start(self):
        # 2026-07-05 is a Sunday.
        sunday = datetime(2026, 7, 5, 0, 0, 0, tzinfo=timezone.utc)
        assert pes._week_start(sunday) == sunday

    def test_midweek_rolls_back_to_preceding_sunday(self):
        sunday = datetime(2026, 7, 5, 0, 0, 0, tzinfo=timezone.utc)
        wednesday = sunday + timedelta(days=3, hours=14)
        assert pes._week_start(wednesday) == sunday

    def test_next_sunday_is_a_different_week(self):
        sunday = datetime(2026, 7, 5, 0, 0, 0, tzinfo=timezone.utc)
        next_sunday = sunday + timedelta(days=7)
        assert pes._week_start(next_sunday) != pes._week_start(sunday)


# ---------------------------------------------------------------------------
# spawn_daughter_holding / seed_spawn_camp -- cap boundary + cleansed fallback
# ---------------------------------------------------------------------------

class TestSpawnDaughterHoldingCapBoundary:
    def test_spawns_up_to_the_cap_exactly_then_stops(self):
        # total_sectors=300 -> base_target=12 (<=300 bucket), cap = 12*1.5 = 18.
        region = _region(total_sectors=300)
        sectors = _sectors(region.id, count=25)
        db = _FakeSession(sectors=sectors)
        rng = _ScriptedRng()  # choices()/choice() always pick index 0

        created = []
        # 1st call: no parents yet -> falls back to seed_spawn_camp (score 0->1).
        # Every call thereafter: the ALWAYS-first-of-population Camp parent
        # (weight-picked) x the ALWAYS-first-of-distribution outcome (Camp,
        # since SPAWN_DISTRIBUTION[CAMP]'s first key is CAMP, not "skip") ->
        # deterministically spawns one more Camp (weight 1) per call, until
        # the cap check (18) blocks the 19th.
        for _ in range(19):
            holding = pes.spawn_daughter_holding(db, region, rng=rng)
            created.append(holding)

        scored = pes.compute_population_score(db, region.id)
        assert scored == 18, "score must land EXACTLY on the cap, never over"
        assert created[-1] is None, "the 19th attempt must be refused by the cap"
        assert all(h is not None for h in created[:18])

    def test_at_cap_further_spawns_return_none_without_creating_rows(self):
        region = _region(total_sectors=300)  # cap = 18
        # Pre-seed the region already sitting exactly at the cap (18 Camps).
        holdings = [_holding(region_id=region.id, sector_id=100 + i) for i in range(18)]
        sectors = _sectors(region.id, count=25)
        db = _FakeSession(holdings=holdings, sectors=sectors)
        rng = _ScriptedRng()

        before_count = len(db._by_table["pirate_holdings"])
        result = pes.spawn_daughter_holding(db, region, rng=rng)
        after_count = len(db._by_table["pirate_holdings"])

        assert result is None
        assert after_count == before_count, "a capped attempt must not add a row"

    def test_no_eligible_sectors_returns_none(self):
        region = _region(total_sectors=300)
        db = _FakeSession(sectors=[])  # zero sectors in the region at all
        rng = _ScriptedRng()
        assert pes.seed_spawn_camp(db, region, rng=rng) is None


class TestCleansedRegionSeedFallback:
    def test_empty_region_falls_back_to_seed_spawn_camp(self, monkeypatch):
        region = _region(total_sectors=300)
        sectors = _sectors(region.id, count=5)
        db = _FakeSession(sectors=sectors)
        rng = _ScriptedRng()

        calls = []
        original = pes.seed_spawn_camp

        def _spy(db_, region_, **kwargs):
            calls.append(True)
            return original(db_, region_, **kwargs)

        monkeypatch.setattr(pes, "seed_spawn_camp", _spy)

        holding = pes.spawn_daughter_holding(db, region, rng=rng)

        assert calls, "a region with zero parent holdings must route through seed_spawn_camp"
        assert holding is not None
        assert holding.tier == PirateHoldingTier.CAMP


# ---------------------------------------------------------------------------
# run_weekly_tick
# ---------------------------------------------------------------------------

class TestRunWeeklyTick:
    def test_non_active_region_is_skipped_and_state_untouched(self):
        region = _region(total_sectors=300, status=RegionStatus.SUSPENDED.value)
        db = _FakeSession()
        result = pes.run_weekly_tick(db, region, now=datetime.now(timezone.utc))
        assert result["action"] == "skipped"
        assert region.pirate_ecosystem_state is None  # never touched

    def test_delta_below_tolerance_band_is_no_growth(self):
        # total_sectors=300 -> target=12. Score already at 10 -> delta=2 < 3.
        region = _region(total_sectors=300)
        holdings = [_holding(region_id=region.id, sector_id=100 + i) for i in range(10)]
        db = _FakeSession(holdings=holdings, sectors=_sectors(region.id, 20))
        now = datetime(2026, 7, 9, tzinfo=timezone.utc)

        result = pes.run_weekly_tick(db, region, now=now, rng=_ScriptedRng())

        assert result["action"] == "no_growth"
        assert len(db._by_table["pirate_holdings"]) == 10, "no_growth must not spawn"

    def test_growth_spawns_and_persists_last_tick_state(self):
        region = _region(total_sectors=300)  # target=12
        db = _FakeSession(sectors=_sectors(region.id, 20))
        now = datetime(2026, 7, 9, tzinfo=timezone.utc)  # a Thursday

        result = pes.run_weekly_tick(db, region, now=now, rng=_ScriptedRng())

        assert result["action"] == "growth"
        # delta=12-0=12 -> sites_to_spawn = min(5, ceil(12/3)) = 4.
        assert len(result["spawned"]) == 4
        assert region.pirate_ecosystem_state["last_growth_tick_at"] == now.isoformat()
        assert region.pirate_ecosystem_state["last_growth_action"] == "growth"

    def test_same_week_rerun_is_idempotent_no_op(self):
        region = _region(total_sectors=300)
        db = _FakeSession(sectors=_sectors(region.id, 20))
        first_now = datetime(2026, 7, 9, tzinfo=timezone.utc)  # Thursday
        second_now = first_now + timedelta(days=2)  # still the same UTC week

        first = pes.run_weekly_tick(db, region, now=first_now, rng=_ScriptedRng())
        count_after_first = len(db._by_table["pirate_holdings"])

        second = pes.run_weekly_tick(db, region, now=second_now, rng=_ScriptedRng())
        count_after_second = len(db._by_table["pirate_holdings"])

        assert first["action"] == "growth"
        assert second["action"] == "already_ticked_this_window"
        assert count_after_second == count_after_first, "a same-window rerun must not spawn again"

    def test_next_week_ticks_again(self):
        region = _region(total_sectors=300)
        db = _FakeSession(sectors=_sectors(region.id, 20))
        first_now = datetime(2026, 7, 9, tzinfo=timezone.utc)
        next_week_now = first_now + timedelta(days=8)  # crosses a Sunday boundary

        pes.run_weekly_tick(db, region, now=first_now, rng=_ScriptedRng())
        second = pes.run_weekly_tick(db, region, now=next_week_now, rng=_ScriptedRng())

        assert second["action"] != "already_ticked_this_window"


# ---------------------------------------------------------------------------
# evolution_tick / promote_holding_tier
# ---------------------------------------------------------------------------

class TestEvolutionTickSchedule:
    def test_promotes_camp_to_outpost_at_the_30_day_threshold(self):
        region = _region(total_sectors=300)
        now = datetime(2026, 7, 9, tzinfo=timezone.utc)
        holding = _holding(
            region_id=region.id, tier=PirateHoldingTier.CAMP,
            current_strength=1.0, created_at=now - timedelta(days=30),
        )
        db = _FakeSession(holdings=[holding], regions=[region])

        result = pes.evolution_tick(db, holding, now=now, rng=_ScriptedRng([0.0]))

        assert result["action"] == "evolved"
        assert holding.tier == PirateHoldingTier.OUTPOST
        assert holding.last_damage_at == now, "promotion resets the evolution clock"

    def test_below_threshold_days_no_promotion(self):
        region = _region(total_sectors=300)
        now = datetime(2026, 7, 9, tzinfo=timezone.utc)
        holding = _holding(
            region_id=region.id, tier=PirateHoldingTier.CAMP,
            current_strength=1.0, created_at=now - timedelta(days=29),
        )
        db = _FakeSession(holdings=[holding], regions=[region])

        result = pes.evolution_tick(db, holding, now=now, rng=_ScriptedRng([0.0]))

        assert result["action"] == "none"
        assert result["reason"] == "clock_not_met"
        assert holding.tier == PirateHoldingTier.CAMP

    def test_damage_past_threshold_resets_clock_and_delays_promotion(self):
        # Created 40 days ago (comfortably past the 30-day Camp threshold),
        # but damaged only 5 days ago -- the damage-reset clock, not the
        # creation clock, governs (:288-296).
        region = _region(total_sectors=300)
        now = datetime(2026, 7, 9, tzinfo=timezone.utc)
        holding = _holding(
            region_id=region.id, tier=PirateHoldingTier.CAMP, current_strength=1.0,
            created_at=now - timedelta(days=40),
            last_damage_at=now - timedelta(days=5),
        )
        db = _FakeSession(holdings=[holding], regions=[region])

        result = pes.evolution_tick(db, holding, now=now, rng=_ScriptedRng([0.0]))

        assert result["action"] == "none"
        assert result["reason"] == "clock_not_met"
        assert holding.tier == PirateHoldingTier.CAMP

    def test_not_full_strength_blocks_promotion_regardless_of_clock(self):
        region = _region(total_sectors=300)
        now = datetime(2026, 7, 9, tzinfo=timezone.utc)
        holding = _holding(
            region_id=region.id, tier=PirateHoldingTier.CAMP, current_strength=0.80,
            created_at=now - timedelta(days=90),
        )
        db = _FakeSession(holdings=[holding], regions=[region])

        result = pes.evolution_tick(db, holding, now=now, rng=_ScriptedRng([0.0]))

        assert result["action"] == "none"
        assert result["reason"] == "not_full_strength"

    def test_stronghold_is_already_max_tier(self):
        region = _region(total_sectors=300)
        holding = _holding(region_id=region.id, tier=PirateHoldingTier.STRONGHOLD)
        db = _FakeSession(holdings=[holding], regions=[region])
        result = pes.evolution_tick(db, holding, rng=_ScriptedRng([0.0]))
        assert result == {"action": "none", "reason": "max_tier"}

    def test_player_captured_holding_is_never_promoted(self):
        region = _region(total_sectors=300)
        now = datetime(2026, 7, 9, tzinfo=timezone.utc)
        holding = _holding(
            region_id=region.id, tier=PirateHoldingTier.CAMP, current_strength=1.0,
            owner_player_id=uuid.uuid4(), created_at=now - timedelta(days=90),
        )
        db = _FakeSession(holdings=[holding], regions=[region])
        result = pes.evolution_tick(db, holding, now=now, rng=_ScriptedRng([0.0]))
        assert result == {"action": "none", "reason": "player_captured"}

    def test_roll_failure_no_promotion(self):
        region = _region(total_sectors=300)
        now = datetime(2026, 7, 9, tzinfo=timezone.utc)
        holding = _holding(
            region_id=region.id, tier=PirateHoldingTier.CAMP, current_strength=1.0,
            created_at=now - timedelta(days=30),
        )
        db = _FakeSession(holdings=[holding], regions=[region])
        # EVOLUTION_CHANCE[CAMP] == 0.20 -- a roll of 0.99 always fails.
        result = pes.evolution_tick(db, holding, now=now, rng=_ScriptedRng([0.99]))
        assert result["action"] == "none"
        assert result["reason"] == "roll_failed"
        assert holding.tier == PirateHoldingTier.CAMP


class TestEvolutionTickCap:
    def test_promotion_suppressed_when_it_would_exceed_the_cap(self):
        # total_sectors=300 -> cap=18. One Stronghold(10) + this ready Outpost
        # holding(3, about to promote to Stronghold=10): projected = 10 - 3 +
        # 10 = 17... need a value that DOES exceed. Use TWO Strongholds (20)
        # already over base but let's construct precisely:
        region = _region(total_sectors=300)  # cap = 18
        now = datetime(2026, 7, 9, tzinfo=timezone.utc)
        promoting = _holding(
            region_id=region.id, tier=PirateHoldingTier.OUTPOST, current_strength=1.0,
            created_at=now - timedelta(days=60), sector_id=500,
        )
        # Score without `promoting` contributing its post-promotion delta:
        # a Stronghold(10) + an Outpost(3, this one) = 13 currently.
        # Promoting OUTPOST(3) -> STRONGHOLD(10): projected = 13 - 3 + 10 = 20 > 18.
        other = _holding(region_id=region.id, tier=PirateHoldingTier.STRONGHOLD, sector_id=501)
        db = _FakeSession(holdings=[promoting, other], regions=[region])

        result = pes.evolution_tick(db, promoting, now=now, rng=_ScriptedRng([0.0]))

        assert result["action"] == "none"
        assert result["reason"] == "capped"
        assert promoting.tier == PirateHoldingTier.OUTPOST


class TestEvolutionTickStrongholdFormationGate:
    def test_outpost_without_qualifying_formation_is_suppressed_and_clock_resets(self):
        region = _region(total_sectors=1200)  # cap=52.5, plenty of headroom
        now = datetime(2026, 7, 9, tzinfo=timezone.utc)
        [sector] = _sectors(region.id, count=1, start=700)
        holding = _holding(
            region_id=region.id, tier=PirateHoldingTier.OUTPOST, current_strength=1.0,
            created_at=now - timedelta(days=60), sector_id=sector.sector_id,
        )
        db = _FakeSession(holdings=[holding], regions=[region], sectors=[sector], formations=[])

        result = pes.evolution_tick(db, holding, now=now, rng=_ScriptedRng([0.0]))

        assert result == {"action": "suppressed", "reason": "no_formation"}
        assert holding.tier == PirateHoldingTier.OUTPOST
        assert holding.last_damage_at == now, "the suppression must reset the evolution clock"

    def test_outpost_with_qualifying_bubble_formation_promotes(self):
        region = _region(total_sectors=1200)
        now = datetime(2026, 7, 9, tzinfo=timezone.utc)
        [sector] = _sectors(region.id, count=1, start=700)
        holding = _holding(
            region_id=region.id, tier=PirateHoldingTier.OUTPOST, current_strength=1.0,
            created_at=now - timedelta(days=60), sector_id=sector.sector_id,
        )
        formation = _formation(
            region_id=region.id, anchor_sector_id=sector.id,
            formation_type=SpecialFormationType.BUBBLE,
        )
        db = _FakeSession(
            holdings=[holding], regions=[region], sectors=[sector], formations=[formation],
        )

        result = pes.evolution_tick(db, holding, now=now, rng=_ScriptedRng([0.0]))

        assert result["action"] == "evolved"
        assert holding.tier == PirateHoldingTier.STRONGHOLD

    def test_outpost_with_non_qualifying_formation_type_is_still_suppressed(self):
        # A TUNNEL formation resident in the region does NOT satisfy the
        # Bubble/Dead-End-Bubble prereq -- confirms the Python-side type
        # filter (not just "any formation exists") is what gates this.
        region = _region(total_sectors=1200)
        now = datetime(2026, 7, 9, tzinfo=timezone.utc)
        [sector] = _sectors(region.id, count=1, start=700)
        holding = _holding(
            region_id=region.id, tier=PirateHoldingTier.OUTPOST, current_strength=1.0,
            created_at=now - timedelta(days=60), sector_id=sector.sector_id,
        )
        formation = _formation(
            region_id=region.id, anchor_sector_id=sector.id,
            formation_type=SpecialFormationType.TUNNEL,
        )
        db = _FakeSession(
            holdings=[holding], regions=[region], sectors=[sector], formations=[formation],
        )

        result = pes.evolution_tick(db, holding, now=now, rng=_ScriptedRng([0.0]))

        assert result == {"action": "suppressed", "reason": "no_formation"}


class TestPromoteHoldingTier:
    def test_mutates_tier_and_resets_damage_clock(self):
        region_id = uuid.uuid4()
        now = datetime(2026, 7, 9, tzinfo=timezone.utc)
        holding = _holding(region_id=region_id, tier=PirateHoldingTier.CAMP)

        result = pes.promote_holding_tier(holding, now=now)

        assert holding.tier == PirateHoldingTier.OUTPOST
        assert holding.last_damage_at == now
        assert result["action"] == "evolved"
        assert result["old_tier"] == "CAMP"
        assert result["new_tier"] == "OUTPOST"


# ---------------------------------------------------------------------------
# top_attackers_by_kill_weight / update_cleansed_state_for_region
# ---------------------------------------------------------------------------

class TestTopAttackersByKillWeight:
    def test_ranks_by_summed_weight_excludes_null_attacker(self):
        region_id = uuid.uuid4()
        now = datetime(2026, 7, 9, tzinfo=timezone.utc)
        p1, p2, p3 = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        logs = [
            _kill_log(region_id=region_id, attacker_player_id=p1, kill_weight=10, created_at=now),
            _kill_log(region_id=region_id, attacker_player_id=p2, kill_weight=25, created_at=now),
            _kill_log(region_id=region_id, attacker_player_id=p2, kill_weight=5, created_at=now),
            _kill_log(region_id=region_id, attacker_player_id=p3, kill_weight=1, created_at=now),
            _kill_log(region_id=region_id, attacker_player_id=None, kill_weight=999, created_at=now),
        ]
        db = _FakeSession(kill_logs=logs)

        result = pes.top_attackers_by_kill_weight(db, region_id, days=30, limit=2, now=now)

        assert result == [p2, p1]  # p2: 30, p1: 10, p3: 1 -- top 2, no None


class TestUpdateCleansedStateForRegion:
    def test_newly_cleansed_dispatches_medal_and_event_seam(self, monkeypatch):
        region = _region(total_sectors=300)
        now = datetime(2026, 7, 9, tzinfo=timezone.utc)
        # Already zero-population for >= CLEANSED_DAYS -- this call is the
        # transition that stamps cleansed_at.
        pre_state = dict(pes.DEFAULT_ECOSYSTEM_STATE)
        pre_state["zero_population_since"] = (now - timedelta(days=8)).isoformat()
        region.pirate_ecosystem_state = pre_state

        attacker = uuid.uuid4()
        logs = [_kill_log(region_id=region.id, attacker_player_id=attacker, kill_weight=5, created_at=now)]
        db = _FakeSession(kill_logs=logs)

        award_calls = []
        monkeypatch.setattr(
            pes, "_dispatch_pirate_hunter_medals",
            lambda db_, region_, attackers: award_calls.append(list(attackers)),
        )
        event_calls = []
        monkeypatch.setattr(
            pes, "_emit_region_cleansed_event",
            lambda region_, attackers_, **kwargs: event_calls.append((region_.id, list(attackers_))),
        )

        state = pes.update_cleansed_state_for_region(db, region, now=now)

        assert state["cleansed_at"] == now.isoformat()
        assert award_calls == [[attacker]]
        assert event_calls == [(region.id, [attacker])]

    def test_already_cleansed_rerun_does_not_redispatch(self, monkeypatch):
        region = _region(total_sectors=300)
        now = datetime(2026, 7, 9, tzinfo=timezone.utc)
        pre_state = dict(pes.DEFAULT_ECOSYSTEM_STATE)
        pre_state["cleansed_at"] = (now - timedelta(days=1)).isoformat()
        pre_state["zero_population_since"] = None
        region.pirate_ecosystem_state = pre_state
        db = _FakeSession()

        award_calls = []
        monkeypatch.setattr(
            pes, "_dispatch_pirate_hunter_medals",
            lambda db_, region_, attackers: award_calls.append(attackers),
        )

        pes.update_cleansed_state_for_region(db, region, now=now)

        assert award_calls == [], "an already-Cleansed region must not re-dispatch on every call"

    def test_not_yet_cleansed_no_side_effects(self):
        region = _region(total_sectors=300)
        now = datetime(2026, 7, 9, tzinfo=timezone.utc)
        holding = _holding(region_id=region.id, sector_id=999)  # score=1, not zero
        db = _FakeSession(holdings=[holding])

        state = pes.update_cleansed_state_for_region(db, region, now=now)

        assert state["cleansed_at"] is None


# ---------------------------------------------------------------------------
# Real award_medal integration smoke -- the "unknown medal_id" guard must
# make the dispatch a genuine no-op (not a crash) against a fake session with
# no PlayerMedal table registered at all, confirming _dispatch_pirate_hunter_
# medals' defensive contract without monkeypatching it away.
# ---------------------------------------------------------------------------

class TestDispatchPirateHunterMedalsIsHarmlessNoOp:
    def test_unknown_medal_id_never_touches_the_db(self):
        region = _region(total_sectors=300)
        db = _FakeSession()  # no "player_medals" table registered at all
        # Must not raise, even though the fake session has zero support for
        # whatever table award_medal would query.
        pes._dispatch_pirate_hunter_medals(db, region, [uuid.uuid4()])


# ---------------------------------------------------------------------------
# Realtime telemetry -- WO-PIRATE-ECO-2 lane C (pirate-ecosystem.md:413-423).
# region_pirate_growth / region_pirate_seed_spawn [NO-CANON] / holding_evolved
# / holding_evolution_suppressed [NO-CANON] / region_cleansed.
# ---------------------------------------------------------------------------

class TestRegionPirateGrowthTelemetry:
    def test_growth_action_emits_exactly_once(self, monkeypatch):
        calls = _capture_broadcasts(monkeypatch)
        region = _region(total_sectors=300)  # target=12
        # Pre-seed ONE parent so every spawn this tick takes the regular
        # daughter path -- isolates this test from seed_spawn_camp's OWN
        # telemetry (covered separately below).
        existing_parent = _holding(region_id=region.id, tier=PirateHoldingTier.CAMP, sector_id=50)
        db = _FakeSession(holdings=[existing_parent], sectors=_sectors(region.id, 20, start=100))
        now = datetime(2026, 7, 9, tzinfo=timezone.utc)

        result = pes.run_weekly_tick(db, region, now=now, rng=_ScriptedRng())

        assert result["action"] == "growth"
        assert len(calls) == 1
        region_id, payload = calls[0]
        assert region_id == region.id
        assert payload["type"] == "region_pirate_growth"
        assert payload["region_id"] == str(region.id)
        assert payload["action"] == "growth"
        assert payload["spawn_count"] == len(result["spawned"])
        assert payload["sites_spawned"] == result["spawned"]
        assert payload["target_population"] == result["target"]
        assert payload["current_population"] == result["current"]

    def test_no_growth_action_emits_exactly_once(self, monkeypatch):
        calls = _capture_broadcasts(monkeypatch)
        region = _region(total_sectors=300)  # target=12
        holdings = [_holding(region_id=region.id, sector_id=100 + i) for i in range(10)]
        db = _FakeSession(holdings=holdings, sectors=_sectors(region.id, 20))
        now = datetime(2026, 7, 9, tzinfo=timezone.utc)

        result = pes.run_weekly_tick(db, region, now=now, rng=_ScriptedRng())

        assert result["action"] == "no_growth"
        assert len(calls) == 1
        _region_id, payload = calls[0]
        assert payload["type"] == "region_pirate_growth"
        assert payload["action"] == "no_growth"
        assert payload["spawn_count"] == 0
        assert payload["sites_spawned"] == []

    def test_skipped_non_active_region_emits_nothing(self, monkeypatch):
        calls = _capture_broadcasts(monkeypatch)
        region = _region(total_sectors=300, status=RegionStatus.SUSPENDED.value)
        db = _FakeSession()

        result = pes.run_weekly_tick(db, region, now=datetime.now(timezone.utc))

        assert result["action"] == "skipped"
        assert calls == []

    def test_already_ticked_this_window_emits_nothing(self, monkeypatch):
        region = _region(total_sectors=300)
        db = _FakeSession(sectors=_sectors(region.id, 20))
        first_now = datetime(2026, 7, 9, tzinfo=timezone.utc)
        second_now = first_now + timedelta(days=2)  # same UTC week

        pes.run_weekly_tick(db, region, now=first_now, rng=_ScriptedRng())

        calls = _capture_broadcasts(monkeypatch)  # only observe the SECOND call
        result = pes.run_weekly_tick(db, region, now=second_now, rng=_ScriptedRng())

        assert result["action"] == "already_ticked_this_window"
        assert calls == []


class TestSeedSpawnTelemetry:
    def test_seed_spawn_emits_exactly_once(self, monkeypatch):
        calls = _capture_broadcasts(monkeypatch)
        region = _region(total_sectors=300)
        db = _FakeSession(sectors=_sectors(region.id, 5))
        now = datetime(2026, 7, 9, tzinfo=timezone.utc)

        holding = pes.seed_spawn_camp(db, region, now=now, rng=_ScriptedRng())

        assert holding is not None
        assert len(calls) == 1
        region_id, payload = calls[0]
        assert region_id == region.id
        assert payload["type"] == "region_pirate_seed_spawn"
        assert payload["region_id"] == str(region.id)
        assert payload["holding_id"] == str(holding.id)
        assert payload["sector_id"] == holding.sector_id

    def test_seed_spawn_emits_nothing_when_capped(self, monkeypatch):
        calls = _capture_broadcasts(monkeypatch)
        region = _region(total_sectors=300)  # cap=18
        holdings = [_holding(region_id=region.id, sector_id=100 + i) for i in range(18)]
        db = _FakeSession(holdings=holdings, sectors=_sectors(region.id, 5))

        result = pes.seed_spawn_camp(db, region, now=datetime.now(timezone.utc), rng=_ScriptedRng())

        assert result is None
        assert calls == []

    def test_seed_spawn_emits_nothing_when_no_eligible_sectors(self, monkeypatch):
        calls = _capture_broadcasts(monkeypatch)
        region = _region(total_sectors=300)
        db = _FakeSession(sectors=[])

        result = pes.seed_spawn_camp(db, region, now=datetime.now(timezone.utc), rng=_ScriptedRng())

        assert result is None
        assert calls == []

    def test_growth_tick_seed_fallback_fires_both_events(self, monkeypatch):
        """A tick whose first spawn hits an empty region (seed fallback)
        fires BOTH the specific region_pirate_seed_spawn event AND the
        aggregate region_pirate_growth event -- they are additive, not
        mutually exclusive (see _emit_seed_spawn_event's own docstring)."""
        calls = _capture_broadcasts(monkeypatch)
        region = _region(total_sectors=300)  # target=12, no holdings at all
        db = _FakeSession(sectors=_sectors(region.id, 20))
        now = datetime(2026, 7, 9, tzinfo=timezone.utc)

        result = pes.run_weekly_tick(db, region, now=now, rng=_ScriptedRng())

        assert result["action"] == "growth"
        types = [payload["type"] for _rid, payload in calls]
        assert types.count("region_pirate_seed_spawn") == 1
        assert types.count("region_pirate_growth") == 1


class TestEvolutionTelemetry:
    def test_promotion_emits_holding_evolved_exactly_once(self, monkeypatch):
        calls = _capture_broadcasts(monkeypatch)
        region = _region(total_sectors=300)
        now = datetime(2026, 7, 9, tzinfo=timezone.utc)
        holding = _holding(
            region_id=region.id, tier=PirateHoldingTier.CAMP,
            current_strength=1.0, created_at=now - timedelta(days=30),
        )
        db = _FakeSession(holdings=[holding], regions=[region])

        result = pes.evolution_tick(db, holding, now=now, rng=_ScriptedRng([0.0]))

        assert result["action"] == "evolved"
        assert len(calls) == 1
        region_id, payload = calls[0]
        assert region_id == holding.region_id
        assert payload["type"] == "holding_evolved"
        assert payload["holding_id"] == str(holding.id)
        assert payload["sector_id"] == holding.sector_id
        assert payload["old_tier"] == "CAMP"
        assert payload["new_tier"] == "OUTPOST"

    def test_formation_suppression_emits_holding_evolution_suppressed_exactly_once(self, monkeypatch):
        calls = _capture_broadcasts(monkeypatch)
        region = _region(total_sectors=1200)
        now = datetime(2026, 7, 9, tzinfo=timezone.utc)
        [sector] = _sectors(region.id, count=1, start=700)
        holding = _holding(
            region_id=region.id, tier=PirateHoldingTier.OUTPOST, current_strength=1.0,
            created_at=now - timedelta(days=60), sector_id=sector.sector_id,
        )
        db = _FakeSession(holdings=[holding], regions=[region], sectors=[sector], formations=[])

        result = pes.evolution_tick(db, holding, now=now, rng=_ScriptedRng([0.0]))

        assert result == {"action": "suppressed", "reason": "no_formation"}
        assert len(calls) == 1
        region_id, payload = calls[0]
        assert region_id == holding.region_id
        assert payload["type"] == "holding_evolution_suppressed"
        assert payload["holding_id"] == str(holding.id)
        assert payload["tier"] == "OUTPOST"
        assert payload["reason"] == "no_formation"

    def test_noop_outcomes_emit_nothing(self, monkeypatch):
        calls = _capture_broadcasts(monkeypatch)
        region = _region(total_sectors=300)
        now = datetime(2026, 7, 9, tzinfo=timezone.utc)
        # not_full_strength -- an early no-op guard well before any
        # cap/formation/roll logic runs.
        holding = _holding(
            region_id=region.id, tier=PirateHoldingTier.CAMP, current_strength=0.50,
            created_at=now - timedelta(days=90),
        )
        db = _FakeSession(holdings=[holding], regions=[region])

        result = pes.evolution_tick(db, holding, now=now, rng=_ScriptedRng([0.0]))

        assert result["action"] == "none"
        assert calls == []

    def test_roll_failure_emits_nothing(self, monkeypatch):
        calls = _capture_broadcasts(monkeypatch)
        region = _region(total_sectors=300)
        now = datetime(2026, 7, 9, tzinfo=timezone.utc)
        holding = _holding(
            region_id=region.id, tier=PirateHoldingTier.CAMP, current_strength=1.0,
            created_at=now - timedelta(days=30),
        )
        db = _FakeSession(holdings=[holding], regions=[region])

        result = pes.evolution_tick(db, holding, now=now, rng=_ScriptedRng([0.99]))

        assert result["action"] == "none"
        assert result["reason"] == "roll_failed"
        assert calls == []


class TestRegionCleansedTelemetry:
    def test_newly_cleansed_emits_region_cleansed_exactly_once(self, monkeypatch):
        calls = _capture_broadcasts(monkeypatch)
        region = _region(total_sectors=300)
        now = datetime(2026, 7, 9, tzinfo=timezone.utc)
        pre_state = dict(pes.DEFAULT_ECOSYSTEM_STATE)
        pre_state["zero_population_since"] = (now - timedelta(days=8)).isoformat()
        region.pirate_ecosystem_state = pre_state
        attacker = uuid.uuid4()
        logs = [_kill_log(region_id=region.id, attacker_player_id=attacker, kill_weight=5, created_at=now)]
        db = _FakeSession(kill_logs=logs)

        state = pes.update_cleansed_state_for_region(db, region, now=now)

        assert state["cleansed_at"] == now.isoformat()
        assert len(calls) == 1
        region_id, payload = calls[0]
        assert region_id == region.id
        assert payload["type"] == "region_cleansed"
        assert payload["region_id"] == str(region.id)
        assert payload["cleansed_at"] == now.isoformat()
        assert payload["attacker_leaderboard"] == [str(attacker)]

    def test_not_yet_cleansed_emits_nothing(self, monkeypatch):
        calls = _capture_broadcasts(monkeypatch)
        region = _region(total_sectors=300)
        holding = _holding(region_id=region.id, sector_id=999)  # score=1, not zero
        db = _FakeSession(holdings=[holding])

        pes.update_cleansed_state_for_region(db, region, now=datetime.now(timezone.utc))

        assert calls == []

    def test_already_cleansed_rerun_emits_nothing(self, monkeypatch):
        region = _region(total_sectors=300)
        now = datetime(2026, 7, 9, tzinfo=timezone.utc)
        pre_state = dict(pes.DEFAULT_ECOSYSTEM_STATE)
        pre_state["cleansed_at"] = (now - timedelta(days=1)).isoformat()
        pre_state["zero_population_since"] = None
        region.pirate_ecosystem_state = pre_state
        db = _FakeSession()

        calls = _capture_broadcasts(monkeypatch)
        pes.update_cleansed_state_for_region(db, region, now=now)

        assert calls == []


class TestTelemetryTransportNeverPropagates:
    @pytest.mark.asyncio
    async def test_dead_connection_manager_never_breaks_a_growth_tick(self, monkeypatch):
        """Real (unmocked) _broadcast_pirate_event path, WITH a genuinely
        running event loop (pytest-asyncio) so asyncio.get_running_loop()
        actually succeeds -- proving the general `except Exception` branch,
        not just the "no loop" RuntimeError branch every other sync test in
        this file exercises implicitly. connection_manager is knocked out
        entirely (simulates a dead/unavailable telemetry layer); the tick's
        own result must be completely unaffected."""
        import src.services.websocket_service as ws_module
        monkeypatch.setattr(ws_module, "connection_manager", None)

        region = _region(total_sectors=300)
        existing_parent = _holding(region_id=region.id, tier=PirateHoldingTier.CAMP, sector_id=50)
        db = _FakeSession(holdings=[existing_parent], sectors=_sectors(region.id, 20, start=100))
        now = datetime(2026, 7, 9, tzinfo=timezone.utc)

        result = pes.run_weekly_tick(db, region, now=now, rng=_ScriptedRng())

        assert result["action"] == "growth"
        assert len(result["spawned"]) == 4

    def test_no_running_loop_never_breaks_evolution(self):
        """Every OTHER test in this class runs as a plain sync test (no
        running event loop) -- this one exists purely to document that the
        "no loop" path is the REALISTIC baseline case (sync/worker
        contexts), not an edge case, and it is exercised by literally every
        telemetry-adjacent test above that doesn't monkeypatch
        _broadcast_pirate_event."""
        region = _region(total_sectors=300)
        now = datetime(2026, 7, 9, tzinfo=timezone.utc)
        holding = _holding(
            region_id=region.id, tier=PirateHoldingTier.CAMP,
            current_strength=1.0, created_at=now - timedelta(days=30),
        )
        db = _FakeSession(holdings=[holding], regions=[region])

        result = pes.evolution_tick(db, holding, now=now, rng=_ScriptedRng([0.0]))

        assert result["action"] == "evolved"
        assert holding.tier == PirateHoldingTier.OUTPOST
