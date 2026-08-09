"""Unit tests — expedition_service.py (ADR-0091 ground-expedition
risk-roll engine).

No test file existed for this service. DB-free: a purpose-built `_FakeDb`
dispatches on the SINGLE distinguishing query shape this module issues —
`Expedition.status` (identity-comparable, a stable InstrumentedAttribute)
for the pity-streak query, anything else for the `func.count(...)` active-
count query. The module-level `_RNG` (a `secrets.SystemRandom()` instance)
is monkeypatched to a `_FakeRNG` exposing controllable `.random()` /
`.choice()` so outcome-tier draws and template picks are deterministic and
independently verifiable against the real cumulative-weight math.

Sections:
  TestActiveExpeditionCount — the None-coalescing scalar wrapper.
  TestConsecutiveFailureStreak — leading-FAILURE-run counting, broken by
    any non-FAILURE row or run-out of history.
  TestWeightedOutcome — hard-pity override, the base cumulative-weight
    boundaries, and the high-hazard-radiation nudge shifting them.
  TestShapeClassForPlanet — planet_type -> candidate-pool selection.
  TestDrawTemplate — guaranteed_good's >=12-slot floor + its empty-pool
    fallback, and the normal shape-weighted draw + its own fallback.
  TestBuildResultPayload — the three payload shapes (partial / normal /
    guaranteed_good).
  TestRollExpedition — cap/cost gates, demo's bypass-everything, waive_
    cost's cap-still-applies, forced_success, FAILURE's null result, and
    ship_id wiring.
  TestRerollExpedition — the thin no-discount alias.
"""

from uuid import uuid4

import pytest

from src.models.expedition import Expedition, ExpeditionStatus
from src.models.planet import Planet
from src.models.player import Player
from src.models.ship import Ship
from src.services import expedition_service
from src.services.expedition_service import (
    EXPEDITION_COST_CREDITS,
    EXPEDITION_COST_TURNS,
    HARD_PITY_K,
    MAX_ACTIVE_EXPEDITIONS,
    ExpeditionError,
    _active_expedition_count,
    _build_result_payload,
    _consecutive_failure_streak,
    _draw_template,
    _shape_class_for_planet,
    _weighted_outcome,
    reroll_expedition,
    roll_expedition,
)


class _FakeCountQuery:
    def __init__(self, value):
        self._value = value

    def filter(self, *_args, **_kwargs):
        return self

    def scalar(self):
        return self._value


class _FakeStatusQuery:
    def __init__(self, rows):
        self._rows = rows

    def filter(self, *_args, **_kwargs):
        return self

    def order_by(self, *_args, **_kwargs):
        return self

    def limit(self, *_args, **_kwargs):
        return self

    def all(self):
        return self._rows


class _FakeDb:
    def __init__(self, count_result=0, status_rows=None):
        self._count_result = count_result
        self._status_rows = status_rows if status_rows is not None else []
        self.added = []
        self.flush_calls = 0

    def query(self, *args):
        if len(args) == 1 and args[0] is Expedition.status:
            return _FakeStatusQuery(self._status_rows)
        return _FakeCountQuery(self._count_result)

    def add(self, obj):
        self.added.append(obj)

    def flush(self):
        self.flush_calls += 1


class _FakeRNG:
    def __init__(self, random_value=0.0, choice_index=0):
        self._random_value = random_value
        self._choice_index = choice_index
        self.choice_calls = []

    def random(self):
        return self._random_value

    def choice(self, seq):
        seq = list(seq)
        self.choice_calls.append(seq)
        return seq[self._choice_index]


def _player(**kwargs):
    p = Player()
    p.id = kwargs.pop("id", uuid4())
    p.turns = kwargs.pop("turns", 1000)
    p.credits = kwargs.pop("credits", 10000)
    for k, v in kwargs.items():
        setattr(p, k, v)
    return p


def _planet(**kwargs):
    pl = Planet()
    pl.id = kwargs.pop("id", uuid4())
    pl.planet_type = kwargs.pop("planet_type", None)
    pl.radiation_level = kwargs.pop("radiation_level", 0.0)
    for k, v in kwargs.items():
        setattr(pl, k, v)
    return pl


def _ship(**kwargs):
    s = Ship()
    s.id = kwargs.pop("id", uuid4())
    for k, v in kwargs.items():
        setattr(s, k, v)
    return s


# ---------------------------------------------------------------------------
# _active_expedition_count
# ---------------------------------------------------------------------------


class TestActiveExpeditionCount:
    def test_returns_the_scalar_count(self):
        db = _FakeDb(count_result=2)
        assert _active_expedition_count(db, uuid4()) == 2

    def test_none_scalar_coalesces_to_zero(self):
        db = _FakeDb(count_result=None)
        assert _active_expedition_count(db, uuid4()) == 0


# ---------------------------------------------------------------------------
# _consecutive_failure_streak
# ---------------------------------------------------------------------------


class TestConsecutiveFailureStreak:
    def test_no_history_is_zero(self):
        db = _FakeDb(status_rows=[])
        assert _consecutive_failure_streak(db, uuid4()) == 0

    def test_leading_failure_run_is_counted(self):
        rows = [
            (ExpeditionStatus.FAILURE,),
            (ExpeditionStatus.FAILURE,),
            (ExpeditionStatus.SUCCESS,),
        ]
        db = _FakeDb(status_rows=rows)
        assert _consecutive_failure_streak(db, uuid4()) == 2

    def test_run_broken_immediately_by_a_success(self):
        rows = [(ExpeditionStatus.SUCCESS,), (ExpeditionStatus.FAILURE,)]
        db = _FakeDb(status_rows=rows)
        assert _consecutive_failure_streak(db, uuid4()) == 0

    def test_all_failures_up_to_the_query_limit(self):
        rows = [(ExpeditionStatus.FAILURE,)] * HARD_PITY_K
        db = _FakeDb(status_rows=rows)
        assert _consecutive_failure_streak(db, uuid4()) == HARD_PITY_K

    def test_partial_counts_as_a_break(self):
        rows = [(ExpeditionStatus.FAILURE,), (ExpeditionStatus.PARTIAL,), (ExpeditionStatus.FAILURE,)]
        db = _FakeDb(status_rows=rows)
        assert _consecutive_failure_streak(db, uuid4()) == 1


# ---------------------------------------------------------------------------
# _weighted_outcome
# ---------------------------------------------------------------------------


class TestWeightedOutcome:
    def test_hard_pity_forces_success_regardless_of_roll(self, monkeypatch):
        rows = [(ExpeditionStatus.FAILURE,)] * HARD_PITY_K
        db = _FakeDb(status_rows=rows)
        monkeypatch.setattr(expedition_service, "_RNG", _FakeRNG(random_value=0.99))
        player = _player()
        planet = _planet(radiation_level=0.0)
        assert _weighted_outcome(db, player, planet) == ExpeditionStatus.SUCCESS

    def test_low_roll_is_success(self, monkeypatch):
        db = _FakeDb(status_rows=[])
        monkeypatch.setattr(expedition_service, "_RNG", _FakeRNG(random_value=0.1))
        assert _weighted_outcome(db, _player(), _planet()) == ExpeditionStatus.SUCCESS

    def test_mid_roll_is_partial(self, monkeypatch):
        db = _FakeDb(status_rows=[])
        monkeypatch.setattr(expedition_service, "_RNG", _FakeRNG(random_value=0.7))
        assert _weighted_outcome(db, _player(), _planet()) == ExpeditionStatus.PARTIAL

    def test_high_roll_is_failure(self, monkeypatch):
        db = _FakeDb(status_rows=[])
        monkeypatch.setattr(expedition_service, "_RNG", _FakeRNG(random_value=0.95))
        assert _weighted_outcome(db, _player(), _planet()) == ExpeditionStatus.FAILURE

    def test_high_hazard_radiation_shifts_a_borderline_roll_from_success_to_partial(self, monkeypatch):
        db = _FakeDb(status_rows=[])
        monkeypatch.setattr(expedition_service, "_RNG", _FakeRNG(random_value=0.5))
        # base weights: SUCCESS cumulative 0.55 -> roll 0.5 is SUCCESS.
        assert _weighted_outcome(db, _player(), _planet(radiation_level=0.0)) == ExpeditionStatus.SUCCESS
        # high-hazard: SUCCESS cumulative drops to 0.47 -> the SAME roll is now PARTIAL.
        db2 = _FakeDb(status_rows=[])
        assert _weighted_outcome(db2, _player(), _planet(radiation_level=0.6)) == ExpeditionStatus.PARTIAL


# ---------------------------------------------------------------------------
# _shape_class_for_planet
# ---------------------------------------------------------------------------


class TestShapeClassForPlanet:
    def test_volcanic_draws_from_the_volcanic_pool(self, monkeypatch):
        rng = _FakeRNG(choice_index=0)
        monkeypatch.setattr(expedition_service, "_RNG", rng)
        result = _shape_class_for_planet(_planet(planet_type="VOLCANIC"))
        assert rng.choice_calls[0] == ["COMPACT", "IRREGULAR", "IRREGULAR", "COMPACT"]
        assert result == "COMPACT"

    def test_terran_draws_from_the_terran_pool(self, monkeypatch):
        rng = _FakeRNG(choice_index=1)
        monkeypatch.setattr(expedition_service, "_RNG", rng)
        result = _shape_class_for_planet(_planet(planet_type="TERRAN"))
        assert rng.choice_calls[0] == ["SPRAWLING", "TERRACED", "TERRACED"]
        assert result == "TERRACED"

    def test_ocean_also_draws_from_the_terran_pool(self, monkeypatch):
        rng = _FakeRNG(choice_index=0)
        monkeypatch.setattr(expedition_service, "_RNG", rng)
        _shape_class_for_planet(_planet(planet_type="OCEAN_WORLD"))
        assert rng.choice_calls[0] == ["SPRAWLING", "TERRACED", "TERRACED"]

    def test_unrecognized_type_falls_back_to_a_uniform_draw(self, monkeypatch):
        rng = _FakeRNG(choice_index=0)
        monkeypatch.setattr(expedition_service, "_RNG", rng)
        _shape_class_for_planet(_planet(planet_type="ICE"))
        assert set(rng.choice_calls[0]) == set(expedition_service.SHAPE_CLASSES)

    def test_missing_type_falls_back_to_a_uniform_draw(self, monkeypatch):
        rng = _FakeRNG(choice_index=0)
        monkeypatch.setattr(expedition_service, "_RNG", rng)
        _shape_class_for_planet(_planet(planet_type=None))
        assert set(rng.choice_calls[0]) == set(expedition_service.SHAPE_CLASSES)


# ---------------------------------------------------------------------------
# _draw_template
# ---------------------------------------------------------------------------


_SMALL_TEMPLATES = [
    {"shape_class": "COMPACT", "template_id": "small_a", "slot_count": 5, "energy_baseline": "SOLAR"},
    {"shape_class": "COMPACT", "template_id": "big_a", "slot_count": 14, "energy_baseline": "TIDAL"},
    {"shape_class": "TERRACED", "template_id": "small_b", "slot_count": 8, "energy_baseline": "SOLAR"},
]


class TestDrawTemplate:
    def test_guaranteed_good_restricts_to_the_12_slot_floor(self, monkeypatch):
        monkeypatch.setattr(expedition_service, "_all_templates", lambda: _SMALL_TEMPLATES)
        rng = _FakeRNG(choice_index=0)
        monkeypatch.setattr(expedition_service, "_RNG", rng)
        result = _draw_template(_planet(), guaranteed_good=True)
        assert rng.choice_calls[0] == [_SMALL_TEMPLATES[1]]  # only "big_a" clears >=12
        assert result["template_id"] == "big_a"

    def test_guaranteed_good_falls_back_to_all_templates_when_none_clear_the_floor(self, monkeypatch):
        no_big_templates = [t for t in _SMALL_TEMPLATES if t["slot_count"] < 12]
        monkeypatch.setattr(expedition_service, "_all_templates", lambda: no_big_templates)
        rng = _FakeRNG(choice_index=0)
        monkeypatch.setattr(expedition_service, "_RNG", rng)
        _draw_template(_planet(), guaranteed_good=True)
        assert rng.choice_calls[0] == no_big_templates

    def test_normal_draw_uses_the_planets_shape_weighted_pool(self, monkeypatch):
        monkeypatch.setattr(expedition_service, "_shape_class_for_planet", lambda planet: "COMPACT")
        rng = _FakeRNG(choice_index=0)
        monkeypatch.setattr(expedition_service, "_RNG", rng)
        result = _draw_template(_planet(), guaranteed_good=False)
        assert all(t["shape_class"] == "COMPACT" for t in rng.choice_calls[0])
        assert result["shape_class"] == "COMPACT"

    def test_normal_draw_falls_back_to_all_templates_on_an_empty_shape_pool(self, monkeypatch):
        monkeypatch.setattr(expedition_service, "_shape_class_for_planet", lambda planet: "COMPACT")
        monkeypatch.setattr(expedition_service, "templates_for_shape", lambda shape: [])
        monkeypatch.setattr(expedition_service, "_all_templates", lambda: _SMALL_TEMPLATES)
        rng = _FakeRNG(choice_index=0)
        monkeypatch.setattr(expedition_service, "_RNG", rng)
        _draw_template(_planet(), guaranteed_good=False)
        assert rng.choice_calls[0] == _SMALL_TEMPLATES


# ---------------------------------------------------------------------------
# _build_result_payload
# ---------------------------------------------------------------------------


_TEMPLATE = {"shape_class": "COMPACT", "template_id": "compact_01", "slot_count": 7, "energy_baseline": "SOLAR"}


class TestBuildResultPayload:
    def test_partial_returns_a_banded_minimal_payload(self):
        payload = _build_result_payload(_TEMPLATE, guaranteed_good=False, partial=True)
        assert payload == {
            "shape_class": "COMPACT",
            "template_id": "compact_01",
            "usable_slots": 7,
            "banded": True,
        }

    def test_normal_success_payload_has_no_signal(self):
        payload = _build_result_payload(_TEMPLATE, guaranteed_good=False, partial=False)
        assert payload["energy_baseline"] == "SOLAR"
        assert payload["hazards"] == []
        assert payload["resources"] == []
        assert payload["native_life"] is None
        assert "native_energy_present" not in payload

    def test_guaranteed_good_payload_meets_the_structural_floor(self):
        payload = _build_result_payload(_TEMPLATE, guaranteed_good=True, partial=False)
        assert payload["hazards"] == []
        assert payload["native_energy_present"] is True
        assert payload["resources"] == [{"tier": "T1", "deposit": "starter_ore"}]
        assert payload["native_life"] is False


# ---------------------------------------------------------------------------
# roll_expedition
# ---------------------------------------------------------------------------


class TestRollExpedition:
    def test_cap_exceeded_raises_and_mutates_nothing(self):
        db = _FakeDb(count_result=MAX_ACTIVE_EXPEDITIONS, status_rows=[])
        player = _player(turns=1000, credits=10000)
        with pytest.raises(ExpeditionError, match="active expeditions"):
            roll_expedition(db, player, _planet(), None)
        assert player.turns == 1000
        assert player.credits == 10000
        assert db.added == []

    def test_insufficient_turns_raises(self):
        db = _FakeDb(count_result=0, status_rows=[])
        player = _player(turns=1, credits=10000)
        with pytest.raises(ExpeditionError, match="Not enough turns"):
            roll_expedition(db, player, _planet(), None)

    def test_insufficient_credits_raises(self):
        db = _FakeDb(count_result=0, status_rows=[])
        player = _player(turns=1000, credits=1)
        with pytest.raises(ExpeditionError, match="Not enough credits"):
            roll_expedition(db, player, _planet(), None)

    def test_successful_roll_debits_turns_and_credits(self, monkeypatch):
        db = _FakeDb(count_result=0, status_rows=[])
        monkeypatch.setattr(expedition_service, "_RNG", _FakeRNG(random_value=0.1, choice_index=0))
        player = _player(turns=1000, credits=10000)
        expedition = roll_expedition(db, player, _planet(), None)
        assert player.turns == 1000 - EXPEDITION_COST_TURNS
        assert player.credits == 10000 - EXPEDITION_COST_CREDITS
        assert expedition in db.added
        assert db.flush_calls == 1

    def test_forced_success_ignores_the_weighted_draw(self, monkeypatch):
        db = _FakeDb(count_result=0, status_rows=[])
        # a high roll would normally be FAILURE -- forced_success overrides it.
        monkeypatch.setattr(expedition_service, "_RNG", _FakeRNG(random_value=0.99, choice_index=0))
        player = _player()
        expedition = roll_expedition(db, player, _planet(), None, forced_success=True)
        assert expedition.status == ExpeditionStatus.SUCCESS
        assert expedition.result is not None

    def test_failure_outcome_stores_no_result(self, monkeypatch):
        db = _FakeDb(count_result=0, status_rows=[])
        monkeypatch.setattr(expedition_service, "_RNG", _FakeRNG(random_value=0.99))
        player = _player()
        expedition = roll_expedition(db, player, _planet(), None)
        assert expedition.status == ExpeditionStatus.FAILURE
        assert expedition.result is None

    def test_demo_bypasses_cap_and_cost_entirely(self, monkeypatch):
        # cap already exceeded, and the player can't afford it -- demo skips both checks.
        db = _FakeDb(count_result=MAX_ACTIVE_EXPEDITIONS, status_rows=[])
        monkeypatch.setattr(expedition_service, "_RNG", _FakeRNG(random_value=0.1, choice_index=0))
        player = _player(turns=0, credits=0)
        expedition = roll_expedition(db, player, _planet(), None, demo=True)
        assert expedition.demo is True
        assert player.turns == 0
        assert player.credits == 0

    def test_waive_cost_skips_debit_but_still_enforces_the_cap(self):
        db = _FakeDb(count_result=MAX_ACTIVE_EXPEDITIONS, status_rows=[])
        player = _player(turns=0, credits=0)
        with pytest.raises(ExpeditionError, match="active expeditions"):
            roll_expedition(db, player, _planet(), None, waive_cost=True)

    def test_waive_cost_below_cap_skips_debit_only(self, monkeypatch):
        db = _FakeDb(count_result=0, status_rows=[])
        monkeypatch.setattr(expedition_service, "_RNG", _FakeRNG(random_value=0.1, choice_index=0))
        player = _player(turns=0, credits=0)
        expedition = roll_expedition(db, player, _planet(), None, waive_cost=True)
        assert player.turns == 0
        assert player.credits == 0
        assert expedition.status == ExpeditionStatus.SUCCESS

    def test_ship_id_is_wired_when_a_ship_is_provided(self, monkeypatch):
        db = _FakeDb(count_result=0, status_rows=[])
        monkeypatch.setattr(expedition_service, "_RNG", _FakeRNG(random_value=0.1, choice_index=0))
        ship = _ship()
        expedition = roll_expedition(db, _player(), _planet(), ship)
        assert expedition.ship_id == ship.id

    def test_ship_id_is_none_when_no_ship_is_provided(self, monkeypatch):
        db = _FakeDb(count_result=0, status_rows=[])
        monkeypatch.setattr(expedition_service, "_RNG", _FakeRNG(random_value=0.1, choice_index=0))
        expedition = roll_expedition(db, _player(), _planet(), None)
        assert expedition.ship_id is None


# ---------------------------------------------------------------------------
# reroll_expedition
# ---------------------------------------------------------------------------


class TestRerollExpedition:
    def test_is_a_thin_alias_over_roll_expedition(self, monkeypatch):
        db = _FakeDb(count_result=0, status_rows=[])
        monkeypatch.setattr(expedition_service, "_RNG", _FakeRNG(random_value=0.1, choice_index=0))
        player = _player(turns=1000, credits=10000)
        expedition = reroll_expedition(db, player, _planet(), None)
        # carries no discount -- full cost debited, same as a fresh roll.
        assert player.turns == 1000 - EXPEDITION_COST_TURNS
        assert player.credits == 10000 - EXPEDITION_COST_CREDITS
        assert expedition.demo is False
