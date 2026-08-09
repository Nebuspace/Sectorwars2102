"""Unit tests — terraforming_service.py (planetary terraforming lifecycle).

No test file existed for this service. DB-free: TerraformingService.db is a
hand-rolled _FakeDb (keyed-queue-per-model query chain + no-op commit/
refresh), matching this suite's established convention. The CRT-spine
collaborator structures.settle() (and the grid-preset seed()/
place_terraform_preset() calls inside start_terraforming, which are
wrapped in a defensive try/except in the source and out of this service's
lane) are monkeypatched to scripted stand-ins -- this service's own
lazy-tick/completion/cancellation math is what's under test here, not the
structures grid or the CRT spine, which are independently owned modules.
Real (unattached) Planet/Player model instances are used because several
methods reassign active_events (a JSONB column) directly (no flag_modified
call in THIS module's mutation paths other than the grid-preset one, which
is monkeypatched away) -- SQLAlchemy column assignment works fine on an
unattached instance either way, but using the real model keeps attribute
names honest against the schema.

Sections:
  TestGetTerraformingLevels — the static ladder-config exposure.
  TestCostScalingFactor — the habitability-banded credit multiplier
    (40/70 strictly-greater-than bands).
  TestCalculateIncrement / TestGetPopulationBonusDescription — the
    population-scaled per-tick speed and its human-readable description.
  TestGetTerraformingMeta / TestSetTerraformingMetaField — the
    active_events JSONB accessor/mutator for the terraforming entry.
  TestRecomputeMaxPopulation — the ADR-0035 demographic-ceiling trigger.
  TestCompleteTerraforming — habitability raised to (never below) target,
    max_population/population_growth updates, terraforming state cleared,
    active_events entry removed, status derivation.
  TestAdvanceTerraforming — the lazy advance-on-read tick math: no-op
    guards (inactive/no-target/no-metadata), sub-tick accrual leaving the
    anchor untouched, a normal multi-tick advance, and a tick that crosses
    the target completing the project.
  TestStartTerraforming — every precondition rejection (bad level,
    already-habitable, already-active, target below current, insufficient
    credits/organics/equipment), cost-scaling applied to the charged
    amount, the stale-event replacement, and the new project's recorded
    metadata.
  TestGetTerraformingStatus — inactive vs active shapes, and the
    reconcile-on-read path that finalizes a project whose habitability
    already reached/passed its target.
  TestCancelTerraforming — no-active-project rejection, the 50% refund,
    the arbitrage-fix habitability revert to start_habitability (never
    banking mid-project ticks), and status re-derivation.
  TestSettleTerraforming — the commit-on-change wrapper around settle().
"""
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from src.core import game_time
from src.models.planet import Planet, PlanetStatus
from src.models.player import Player
from src.services import structures as structures_module
from src.services.terraforming_service import (
    TERRAFORMING_COST_SCALE_BASE,
    TERRAFORMING_COST_SCALE_HIGH,
    TERRAFORMING_COST_SCALE_LOW,
    TERRAFORMING_LEVELS,
    TERRAFORMING_MAX_INCREMENT,
    TerraformingService,
)


class _FakeQuery:
    def __init__(self, result):
        self._result = result

    def join(self, *_a, **_k):
        return self

    def filter(self, *_a, **_k):
        return self

    def populate_existing(self):
        return self

    def with_for_update(self, *_a, **_k):
        return self

    def first(self):
        return self._result[0] if self._result else None


class _FakeDb:
    def __init__(self, results=None):
        self._results = {k: list(v) for k, v in (results or {}).items()}
        self.commit_count = 0
        self.refreshed = []

    def query(self, model):
        queue = self._results.get(model, [])
        result = queue.pop(0) if queue else []
        return _FakeQuery(result)

    def commit(self):
        self.commit_count += 1

    def refresh(self, obj):
        self.refreshed.append(obj)


def _planet(**kwargs):
    p = Planet()
    p.id = kwargs.pop("id", "planet-1")
    p.name = kwargs.pop("name", "New Eden")
    p.habitability_score = kwargs.pop("habitability_score", 20)
    p.terraforming_active = kwargs.pop("terraforming_active", False)
    p.terraforming_target = kwargs.pop("terraforming_target", None)
    p.terraforming_start_time = kwargs.pop("terraforming_start_time", None)
    p.terraforming_progress = kwargs.pop("terraforming_progress", 0.0)
    p.status = kwargs.pop("status", PlanetStatus.HABITABLE)
    p.organics = kwargs.pop("organics", 10_000)
    p.equipment = kwargs.pop("equipment", 10_000)
    p.colonists = kwargs.pop("colonists", 0)
    p.population = kwargs.pop("population", 0)
    p.population_growth = kwargs.pop("population_growth", 1.0)
    p.max_population = kwargs.pop("max_population", 0)
    p.active_events = kwargs.pop("active_events", [])
    p.structures = kwargs.pop("structures", {})
    for k, v in kwargs.items():
        setattr(p, k, v)
    return p


def _player(**kwargs):
    pl = Player()
    pl.id = kwargs.pop("id", "player-1")
    pl.credits = kwargs.pop("credits", 5_000_000)
    for k, v in kwargs.items():
        setattr(pl, k, v)
    return pl


def _terra_event(**kwargs):
    defaults = dict(
        type="terraforming",
        level=1,
        level_name="Basic Atmospheric",
        credit_cost=100_000,
        organics_cost=500,
        equipment_cost=200,
        habitability_boost=10,
        duration_hours=72,
        started_at=datetime(2026, 1, 1, tzinfo=UTC).isoformat(),
        start_habitability=20,
        last_tick_at=datetime(2026, 1, 1, tzinfo=UTC).isoformat(),
    )
    defaults.update(kwargs)
    return defaults


def _service(planet_results=None, player_results=None):
    results = {}
    if planet_results is not None:
        results[Planet] = planet_results
    if player_results is not None:
        results[Player] = player_results
    db = _FakeDb(results=results)
    return TerraformingService(db), db


# ---------------------------------------------------------------------------
# static / pure helpers
# ---------------------------------------------------------------------------


class TestGetTerraformingLevels:
    def test_returns_all_five_levels_with_expected_shape(self):
        levels = TerraformingService.get_terraforming_levels()
        assert set(levels.keys()) == {1, 2, 3, 4, 5}
        for level, cfg in levels.items():
            assert cfg["level"] == level
            assert cfg["creditCost"] == TERRAFORMING_LEVELS[level]["cost"]
            assert cfg["habitabilityBoost"] == TERRAFORMING_LEVELS[level]["habitability_boost"]


class TestCostScalingFactor:
    def test_at_or_below_40_is_base(self):
        assert TerraformingService._cost_scaling_factor(40) == TERRAFORMING_COST_SCALE_BASE
        assert TerraformingService._cost_scaling_factor(0) == TERRAFORMING_COST_SCALE_BASE

    def test_above_40_and_at_or_below_70_is_low(self):
        assert TerraformingService._cost_scaling_factor(41) == TERRAFORMING_COST_SCALE_LOW
        assert TerraformingService._cost_scaling_factor(70) == TERRAFORMING_COST_SCALE_LOW

    def test_above_70_is_high(self):
        assert TerraformingService._cost_scaling_factor(71) == TERRAFORMING_COST_SCALE_HIGH
        assert TerraformingService._cost_scaling_factor(89) == TERRAFORMING_COST_SCALE_HIGH


class TestCalculateIncrement:
    def test_zero_population_gives_base_increment(self):
        svc, _ = _service()
        assert svc._calculate_increment(_planet(colonists=0, population=0)) == 1

    def test_just_under_a_threshold_is_still_base(self):
        svc, _ = _service()
        assert svc._calculate_increment(_planet(population=999)) == 1

    def test_one_threshold_crossed_adds_one(self):
        svc, _ = _service()
        assert svc._calculate_increment(_planet(population=1000)) == 2

    def test_capped_at_the_max_increment(self):
        svc, _ = _service()
        assert svc._calculate_increment(_planet(population=50_000)) == TERRAFORMING_MAX_INCREMENT

    def test_uses_the_larger_of_colonists_or_population(self):
        svc, _ = _service()
        p = _planet(colonists=5000, population=0)
        assert svc._calculate_increment(p) == TERRAFORMING_MAX_INCREMENT


class TestGetPopulationBonusDescription:
    def test_max_speed_message(self):
        svc, _ = _service()
        p = _planet(population=50_000)
        assert "Maximum speed" in svc._get_population_bonus_description(p)

    def test_sub_max_speed_message_names_next_threshold(self):
        svc, _ = _service()
        p = _planet(population=0)
        desc = svc._get_population_bonus_description(p)
        assert "Current speed: 1 points/tick" in desc


class TestGetTerraformingMeta:
    def test_finds_the_terraforming_event(self):
        svc, _ = _service()
        event = _terra_event()
        p = _planet(active_events=[{"type": "other"}, event])
        assert svc._get_terraforming_meta(p) == event

    def test_returns_none_when_absent(self):
        svc, _ = _service()
        p = _planet(active_events=[{"type": "other"}])
        assert svc._get_terraforming_meta(p) is None

    def test_returns_none_for_empty_events(self):
        svc, _ = _service()
        assert svc._get_terraforming_meta(_planet(active_events=[])) is None


class TestSetTerraformingMetaField:
    def test_updates_only_the_terraforming_entry(self):
        svc, _ = _service()
        other = {"type": "other", "x": 1}
        event = _terra_event()
        p = _planet(active_events=[other, event])
        svc._set_terraforming_meta_field(p, "last_tick_at", "NEW")
        terra = [e for e in p.active_events if e.get("type") == "terraforming"][0]
        assert terra["last_tick_at"] == "NEW"
        assert p.active_events[0] == other

    def test_no_op_when_no_terraforming_entry_exists(self):
        svc, _ = _service()
        p = _planet(active_events=[{"type": "other"}])
        svc._set_terraforming_meta_field(p, "last_tick_at", "NEW")
        assert p.active_events == [{"type": "other"}]


class TestRecomputeMaxPopulation:
    def test_matches_the_canonical_formula(self):
        svc, _ = _service()
        p = _planet(habitability_score=45, max_population=0)
        svc._recompute_max_population(p)
        assert p.max_population == 45_000

    def test_never_negative_for_a_falsy_score(self):
        svc, _ = _service()
        p = _planet(habitability_score=0)
        svc._recompute_max_population(p)
        assert p.max_population == 0


# ---------------------------------------------------------------------------
# _complete_terraforming
# ---------------------------------------------------------------------------


class TestCompleteTerraforming:
    def test_raises_habitability_to_the_stored_target(self):
        svc, _ = _service()
        p = _planet(
            habitability_score=25,
            terraforming_active=True,
            terraforming_target=30,
            active_events=[_terra_event(habitability_boost=10)],
        )
        result = svc._complete_terraforming(p)
        assert p.habitability_score == 30
        assert result["finalHabitability"] == 30

    def test_never_regresses_if_tick_path_already_exceeded_target(self):
        svc, _ = _service()
        p = _planet(
            habitability_score=35,
            terraforming_active=True,
            terraforming_target=30,
            active_events=[_terra_event()],
        )
        svc._complete_terraforming(p)
        assert p.habitability_score == 35

    def test_falls_back_to_boost_when_no_target_recorded(self):
        svc, _ = _service()
        p = _planet(
            habitability_score=25,
            terraforming_active=True,
            terraforming_target=None,
            active_events=[_terra_event(habitability_boost=10)],
        )
        svc._complete_terraforming(p)
        assert p.habitability_score == 35

    def test_caps_at_100(self):
        svc, _ = _service()
        p = _planet(
            habitability_score=95,
            terraforming_active=True,
            terraforming_target=None,
            active_events=[_terra_event(habitability_boost=30)],
        )
        svc._complete_terraforming(p)
        assert p.habitability_score == 100

    def test_recomputes_max_population(self):
        svc, _ = _service()
        p = _planet(
            habitability_score=25,
            terraforming_active=True,
            terraforming_target=40,
            active_events=[_terra_event()],
        )
        svc._complete_terraforming(p)
        assert p.max_population == 40_000

    def test_boosts_population_growth_at_high_habitability(self):
        svc, _ = _service()
        p = _planet(
            habitability_score=75,
            terraforming_active=True,
            terraforming_target=85,
            population_growth=1.0,
            active_events=[_terra_event()],
        )
        svc._complete_terraforming(p)
        assert p.population_growth == 2.0

    def test_never_lowers_an_already_higher_growth_rate(self):
        svc, _ = _service()
        p = _planet(
            habitability_score=55,
            terraforming_active=True,
            terraforming_target=65,
            population_growth=3.0,
            active_events=[_terra_event()],
        )
        svc._complete_terraforming(p)
        assert p.population_growth == 3.0

    def test_clears_terraforming_state_and_removes_event(self):
        svc, _ = _service()
        p = _planet(
            habitability_score=25,
            terraforming_active=True,
            terraforming_target=30,
            terraforming_start_time=datetime.now(UTC),
            active_events=[_terra_event(), {"type": "other"}],
        )
        svc._complete_terraforming(p)
        assert p.terraforming_active is False
        assert p.terraforming_target is None
        assert p.terraforming_start_time is None
        assert p.terraforming_progress == 100.0
        assert p.active_events == [{"type": "other"}]

    def test_status_colonized_when_populated(self):
        svc, _ = _service()
        p = _planet(
            habitability_score=25,
            terraforming_active=True,
            terraforming_target=30,
            colonists=5,
            active_events=[_terra_event()],
        )
        svc._complete_terraforming(p)
        assert p.status == PlanetStatus.COLONIZED

    def test_status_habitable_when_unpopulated(self):
        svc, _ = _service()
        p = _planet(
            habitability_score=25,
            terraforming_active=True,
            terraforming_target=30,
            colonists=0,
            population=0,
            active_events=[_terra_event()],
        )
        svc._complete_terraforming(p)
        assert p.status == PlanetStatus.HABITABLE


# ---------------------------------------------------------------------------
# _advance_terraforming
# ---------------------------------------------------------------------------


class TestAdvanceTerraforming:
    def test_inactive_project_is_a_no_op(self):
        svc, _ = _service()
        p = _planet(terraforming_active=False)
        assert svc._advance_terraforming(p, _via_settle=True) is False

    def test_missing_start_time_is_a_no_op(self):
        svc, _ = _service()
        p = _planet(terraforming_active=True, terraforming_start_time=None, terraforming_target=30)
        assert svc._advance_terraforming(p, _via_settle=True) is False

    def test_missing_target_is_a_no_op(self):
        svc, _ = _service()
        p = _planet(
            terraforming_active=True,
            terraforming_start_time=datetime.now(UTC),
            terraforming_target=None,
        )
        assert svc._advance_terraforming(p, _via_settle=True) is False

    def test_missing_duration_metadata_is_a_no_op(self):
        svc, _ = _service()
        p = _planet(
            terraforming_active=True,
            terraforming_start_time=datetime.now(UTC),
            terraforming_target=30,
            active_events=[],
        )
        assert svc._advance_terraforming(p, _via_settle=True) is False

    def test_already_at_target_completes_immediately(self):
        svc, _ = _service()
        p = _planet(
            habitability_score=30,
            terraforming_active=True,
            terraforming_start_time=datetime.now(UTC),
            terraforming_target=30,
            active_events=[_terra_event()],
        )
        assert svc._advance_terraforming(p, _via_settle=True) is True
        assert p.terraforming_active is False

    def test_sub_tick_elapsed_time_leaves_state_unchanged(self):
        svc, _ = _service()
        now = datetime.now(UTC)
        event = _terra_event(
            duration_hours=72, start_habitability=20,
            last_tick_at=now.isoformat(),
        )
        p = _planet(
            habitability_score=20,
            terraforming_active=True,
            terraforming_start_time=now,
            terraforming_target=30,
            active_events=[event],
        )
        assert svc._advance_terraforming(p, _via_settle=True) is False
        assert p.habitability_score == 20

    def test_multi_tick_advance_raises_habitability_and_moves_anchor(self):
        svc, _ = _service()
        # duration=72h, total_points=10 (20->30) -> tick_period=7.2h.
        # 20 CANONICAL hours elapsed -> 2 full ticks. _advance_terraforming
        # has no `now` override -- it reads canonical_hours_since(anchor)
        # against the REAL wall clock -- so the anchor must be computed
        # relative to datetime.now(UTC), scaled by the real GAME_TIME_SCALE,
        # not a fixed historical timestamp (a fixed 2026-01-01 anchor would
        # measure however many real hours have elapsed since then, not 20).
        elapsed_wall_hours = 20 / game_time.GAME_TIME_SCALE
        anchor = datetime.now(UTC) - timedelta(hours=elapsed_wall_hours)
        event = _terra_event(
            duration_hours=72, start_habitability=20,
            last_tick_at=anchor.isoformat(),
        )
        p = _planet(
            habitability_score=20,
            terraforming_active=True,
            terraforming_start_time=anchor,
            terraforming_target=30,
            population=0,
            active_events=[event],
        )
        changed = svc._advance_terraforming(p, _via_settle=True)
        assert changed is True
        assert p.habitability_score == 22  # 2 ticks x 1 pt/tick
        assert p.terraforming_active is True  # not yet complete

    def test_a_tick_that_reaches_target_completes_the_project(self):
        # Anchor far enough in the past (relative to the real wall clock,
        # scaled) that the elapsed canonical hours blow past the 10-point
        # target regardless of GAME_TIME_SCALE.
        elapsed_wall_hours = 500 / game_time.GAME_TIME_SCALE
        anchor = datetime.now(UTC) - timedelta(hours=elapsed_wall_hours)
        event = _terra_event(
            duration_hours=72, start_habitability=20,
            last_tick_at=anchor.isoformat(),
        )
        p = _planet(
            habitability_score=20,
            terraforming_active=True,
            terraforming_start_time=anchor,
            terraforming_target=30,
            active_events=[event],
        )
        svc, _ = _service()
        changed = svc._advance_terraforming(p, _via_settle=True)
        assert changed is True
        assert p.terraforming_active is False
        assert p.habitability_score == 30


# ---------------------------------------------------------------------------
# start_terraforming
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _stub_structures_grid(monkeypatch):
    monkeypatch.setattr(structures_module, "seed", lambda *_a, **_k: None)
    monkeypatch.setattr(structures_module, "place_terraform_preset", lambda *_a, **_k: None)


class TestStartTerraforming:
    def test_invalid_level_raises(self):
        svc, _ = _service()
        with pytest.raises(ValueError, match="Invalid terraforming level"):
            svc.start_terraforming("planet-1", "player-1", level=6)

    def test_already_habitable_enough_raises(self):
        p = _planet(habitability_score=90)
        svc, _ = _service(planet_results=[[p]])
        with pytest.raises(ValueError, match="already"):
            svc.start_terraforming("planet-1", "player-1", level=1)

    def test_already_active_raises(self):
        p = _planet(habitability_score=20, terraforming_active=True)
        svc, _ = _service(planet_results=[[p]])
        with pytest.raises(ValueError, match="already active"):
            svc.start_terraforming("planet-1", "player-1", level=1)

    def test_explicit_target_below_current_raises(self):
        p = _planet(habitability_score=20)
        svc, _ = _service(planet_results=[[p]])
        with pytest.raises(ValueError, match="must be higher"):
            svc.start_terraforming("planet-1", "player-1", level=1, target_habitability=10)

    def test_insufficient_credits_raises(self):
        p = _planet(habitability_score=20)
        player = _player(credits=1)
        svc, _ = _service(planet_results=[[p]], player_results=[[player]])
        with pytest.raises(ValueError, match="Insufficient credits"):
            svc.start_terraforming("planet-1", "player-1", level=1)

    def test_insufficient_organics_raises(self):
        p = _planet(habitability_score=20, organics=0)
        player = _player()
        svc, _ = _service(planet_results=[[p]], player_results=[[player]])
        with pytest.raises(ValueError, match="Insufficient organics"):
            svc.start_terraforming("planet-1", "player-1", level=1)

    def test_insufficient_equipment_raises(self):
        p = _planet(habitability_score=20, equipment=0)
        player = _player()
        svc, _ = _service(planet_results=[[p]], player_results=[[player]])
        with pytest.raises(ValueError, match="Insufficient equipment"):
            svc.start_terraforming("planet-1", "player-1", level=1)

    def test_successful_start_charges_base_cost_below_scaling_threshold(self):
        p = _planet(habitability_score=20)
        player = _player(credits=1_000_000)
        svc, db = _service(planet_results=[[p]], player_results=[[player]])
        result = svc.start_terraforming("planet-1", "player-1", level=1)
        expected_cost = TERRAFORMING_LEVELS[1]["cost"]  # scale 1.0 at habitability 20
        assert result["creditCost"] == expected_cost
        assert player.credits == 1_000_000 - expected_cost
        assert p.organics == 10_000 - TERRAFORMING_LEVELS[1]["organics_cost"]
        assert p.equipment == 10_000 - TERRAFORMING_LEVELS[1]["equipment_cost"]
        assert p.terraforming_active is True
        assert p.status == PlanetStatus.TERRAFORMING
        assert db.commit_count == 1

    def test_cost_scaling_applied_above_the_low_threshold(self):
        p = _planet(habitability_score=50)  # >40 -> x1.25
        player = _player(credits=1_000_000)
        svc, _ = _service(planet_results=[[p]], player_results=[[player]])
        result = svc.start_terraforming("planet-1", "player-1", level=1)
        expected_cost = int(TERRAFORMING_LEVELS[1]["cost"] * TERRAFORMING_COST_SCALE_LOW)
        assert result["creditCost"] == expected_cost

    def test_target_defaults_to_level_boost_capped_at_100(self):
        p = _planet(habitability_score=95)
        # 95 is below MIN_TARGET(90)? No -- 95 >= 90 would raise "already" above.
        # Use a habitability just under 90 with a boost that would exceed 100.
        p = _planet(habitability_score=85)
        player = _player()
        svc, _ = _service(planet_results=[[p]], player_results=[[player]])
        result = svc.start_terraforming("planet-1", "player-1", level=5)  # boost=30
        assert result["targetHabitability"] == 100

    def test_explicit_target_overrides_level_boost(self):
        p = _planet(habitability_score=20)
        player = _player()
        svc, _ = _service(planet_results=[[p]], player_results=[[player]])
        result = svc.start_terraforming(
            "planet-1", "player-1", level=1, target_habitability=50
        )
        assert result["targetHabitability"] == 50

    def test_replaces_a_stale_terraforming_event(self):
        stale = _terra_event(level=3, credit_cost=999)
        p = _planet(habitability_score=20, active_events=[stale])
        player = _player()
        svc, _ = _service(planet_results=[[p]], player_results=[[player]])
        svc.start_terraforming("planet-1", "player-1", level=1)
        terra_events = [e for e in p.active_events if e.get("type") == "terraforming"]
        assert len(terra_events) == 1
        assert terra_events[0]["level"] == 1

    def test_records_start_habitability_and_last_tick_at_in_metadata(self):
        p = _planet(habitability_score=33)
        player = _player()
        svc, _ = _service(planet_results=[[p]], player_results=[[player]])
        svc.start_terraforming("planet-1", "player-1", level=1)
        terra = [e for e in p.active_events if e.get("type") == "terraforming"][0]
        assert terra["start_habitability"] == 33
        assert terra["last_tick_at"] == terra["started_at"]

    def test_planet_not_owned_raises(self):
        svc, _ = _service(planet_results=[[]])
        with pytest.raises(ValueError, match="not found or not owned"):
            svc.start_terraforming("planet-1", "player-1", level=1)


# ---------------------------------------------------------------------------
# get_terraforming_status
# ---------------------------------------------------------------------------


class TestGetTerraformingStatus:
    def test_inactive_project_returns_available_levels(self, monkeypatch):
        monkeypatch.setattr(
            structures_module, "settle", lambda *_a, **_k: SimpleNamespace(changed=False)
        )
        p = _planet(terraforming_active=False)
        svc, _ = _service(planet_results=[[p]])
        result = svc.get_terraforming_status("planet-1", "player-1")
        assert result["active"] is False
        assert set(result["availableLevels"].keys()) == {1, 2, 3, 4, 5}

    def test_active_project_reports_progress_fields(self, monkeypatch):
        monkeypatch.setattr(
            structures_module, "settle", lambda *_a, **_k: SimpleNamespace(changed=False)
        )
        now = datetime.now(UTC)
        event = _terra_event(
            duration_hours=72, start_habitability=20, last_tick_at=now.isoformat()
        )
        p = _planet(
            habitability_score=22,
            terraforming_active=True,
            terraforming_target=30,
            terraforming_start_time=now,
            terraforming_progress=20.0,
            active_events=[event],
        )
        svc, _ = _service(planet_results=[[p]])
        result = svc.get_terraforming_status("planet-1", "player-1")
        assert result["active"] is True
        assert result["currentHabitability"] == 22
        assert result["terraformingTarget"] == 30
        assert result["level"] == 1
        assert result["estimatedTicksRemaining"] is not None

    def test_settle_changed_triggers_commit_and_refresh(self, monkeypatch):
        monkeypatch.setattr(
            structures_module, "settle", lambda *_a, **_k: SimpleNamespace(changed=True)
        )
        p = _planet(terraforming_active=False)
        svc, db = _service(planet_results=[[p]])
        svc.get_terraforming_status("planet-1", "player-1")
        assert db.commit_count >= 1
        assert p in db.refreshed

    def test_reconcile_on_read_completes_a_stale_active_project(self, monkeypatch):
        monkeypatch.setattr(
            structures_module, "settle", lambda *_a, **_k: SimpleNamespace(changed=False)
        )
        # habitability already >= target while still marked active -- an
        # invalid state the reconcile-on-read path must finalize.
        p = _planet(
            habitability_score=35,
            terraforming_active=True,
            terraforming_target=30,
            active_events=[_terra_event()],
        )
        svc, db = _service(planet_results=[[p]])
        result = svc.get_terraforming_status("planet-1", "player-1")
        assert result["active"] is False
        assert p.terraforming_active is False
        assert db.commit_count >= 1


# ---------------------------------------------------------------------------
# cancel_terraforming
# ---------------------------------------------------------------------------


class TestCancelTerraforming:
    def test_no_active_project_raises(self):
        p = _planet(terraforming_active=False)
        svc, _ = _service(planet_results=[[p]])
        with pytest.raises(ValueError, match="No active terraforming"):
            svc.cancel_terraforming("planet-1", "player-1")

    def test_refunds_half_the_original_credit_cost(self):
        event = _terra_event(credit_cost=100_000)
        p = _planet(terraforming_active=True, active_events=[event])
        player = _player(credits=0)
        svc, _ = _service(planet_results=[[p]], player_results=[[player]])
        result = svc.cancel_terraforming("planet-1", "player-1")
        assert result["refundAmount"] == 50_000
        assert player.credits == 50_000

    def test_reverts_habitability_gained_mid_project_arbitrage_fix(self):
        event = _terra_event(start_habitability=20)
        # Grid/tick path already lifted habitability to 25 mid-project.
        p = _planet(habitability_score=25, terraforming_active=True, active_events=[event])
        player = _player()
        svc, _ = _service(planet_results=[[p]], player_results=[[player]])
        svc.cancel_terraforming("planet-1", "player-1")
        assert p.habitability_score == 20

    def test_does_not_raise_habitability_if_it_never_moved(self):
        event = _terra_event(start_habitability=20)
        p = _planet(habitability_score=20, terraforming_active=True, active_events=[event])
        player = _player()
        svc, _ = _service(planet_results=[[p]], player_results=[[player]])
        svc.cancel_terraforming("planet-1", "player-1")
        assert p.habitability_score == 20

    def test_clears_terraforming_state_and_removes_event(self):
        event = _terra_event()
        p = _planet(
            terraforming_active=True,
            terraforming_target=30,
            terraforming_start_time=datetime.now(UTC),
            active_events=[event],
        )
        player = _player()
        svc, _ = _service(planet_results=[[p]], player_results=[[player]])
        svc.cancel_terraforming("planet-1", "player-1")
        assert p.terraforming_active is False
        assert p.terraforming_target is None
        assert p.terraforming_start_time is None
        assert p.active_events == []

    def test_status_colonized_when_populated(self):
        event = _terra_event(start_habitability=20)
        p = _planet(
            habitability_score=20, terraforming_active=True, colonists=3, active_events=[event]
        )
        player = _player()
        svc, _ = _service(planet_results=[[p]], player_results=[[player]])
        svc.cancel_terraforming("planet-1", "player-1")
        assert p.status == PlanetStatus.COLONIZED

    def test_status_uninhabitable_when_zero_habitability_and_unpopulated(self):
        event = _terra_event(start_habitability=0)
        p = _planet(
            habitability_score=0, terraforming_active=True, colonists=0, population=0,
            active_events=[event],
        )
        player = _player()
        svc, _ = _service(planet_results=[[p]], player_results=[[player]])
        svc.cancel_terraforming("planet-1", "player-1")
        assert p.status == PlanetStatus.UNINHABITABLE

    def test_player_not_found_raises(self):
        event = _terra_event()
        p = _planet(terraforming_active=True, active_events=[event])
        svc, _ = _service(planet_results=[[p]], player_results=[[]])
        with pytest.raises(ValueError, match="Player not found"):
            svc.cancel_terraforming("planet-1", "player-1")


# ---------------------------------------------------------------------------
# settle_terraforming
# ---------------------------------------------------------------------------


class TestSettleTerraforming:
    def test_inactive_project_is_a_no_op_without_calling_settle(self, monkeypatch):
        calls = []
        monkeypatch.setattr(
            structures_module, "settle",
            lambda *_a, **_k: calls.append(1) or SimpleNamespace(changed=True),
        )
        svc, _ = _service()
        p = _planet(terraforming_active=False)
        assert svc.settle_terraforming(p) is False
        assert calls == []

    def test_changed_commits_and_refreshes(self, monkeypatch):
        monkeypatch.setattr(
            structures_module, "settle", lambda *_a, **_k: SimpleNamespace(changed=True)
        )
        svc, db = _service()
        p = _planet(terraforming_active=True)
        assert svc.settle_terraforming(p) is True
        assert db.commit_count == 1
        assert p in db.refreshed

    def test_unchanged_does_not_commit(self, monkeypatch):
        monkeypatch.setattr(
            structures_module, "settle", lambda *_a, **_k: SimpleNamespace(changed=False)
        )
        svc, db = _service()
        p = _planet(terraforming_active=True)
        assert svc.settle_terraforming(p) is False
        assert db.commit_count == 0
