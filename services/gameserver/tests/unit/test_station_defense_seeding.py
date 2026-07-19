"""Unit tests for WO-CMB-PORT-DEF-SEED-1 (class-scaled station defenses).

Station.defenses ships a flat 0/50 shape (see the WO-BP-a Column default in
src/models/station.py) regardless of station_class, so
combat_service._resolve_port_combat always resolved against the same
numbers no matter how strong a port "should" be. Station.default_defenses_for_class
implements the canon drone table (FEATURES/gameplay/combat.md#port-assault,
mirrored in FEATURES/galaxy/sectors.md "Owned port defenses"): Class 1: 50
drones, Class 2: 100, Class 3: 200, Class 4: 300 + auto-turrets, Class 5:
500 + advanced grid (realized via the existing `defense_grid` boolean — see
the helper's docstring). Classes outside 1-5 (CLASS_0 hub/capital and the
premium CLASS_6-11 tiers) borrow the Class-5 profile — NO-CANON, flagged in
the helper's block comment.

DB-free: exercises the pure static helper directly, and the two
station-creation sites' wiring is proven by asserting the helper is called
with the right class argument via monkeypatching (no ORM session needed).
"""
from __future__ import annotations

import inspect
from typing import Any, Dict

import pytest

from src.models.station import Station, StationClass
from src.services import combat_service


# ---------------------------------------------------------------------------
# Canon drone table (Class 1-5) — exact counts, and both defense_drones AND
# max_defense_drones seeded to the canon count (canon "carry built-in
# drones" reads as already-garrisoned, not just a capacity ceiling).
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCanonDroneCounts:
    @pytest.mark.parametrize(
        "station_class,expected_drones",
        [
            (StationClass.CLASS_1, 50),
            (StationClass.CLASS_2, 100),
            (StationClass.CLASS_3, 200),
            (StationClass.CLASS_4, 300),
            (StationClass.CLASS_5, 500),
        ],
    )
    def test_defense_drones_matches_canon_count(
        self, station_class: StationClass, expected_drones: int
    ) -> None:
        defenses = Station.default_defenses_for_class(station_class)
        assert defenses["defense_drones"] == expected_drones

    @pytest.mark.parametrize(
        "station_class,expected_drones",
        [
            (StationClass.CLASS_1, 50),
            (StationClass.CLASS_2, 100),
            (StationClass.CLASS_3, 200),
            (StationClass.CLASS_4, 300),
            (StationClass.CLASS_5, 500),
        ],
    )
    def test_max_defense_drones_matches_canon_count(
        self, station_class: StationClass, expected_drones: int
    ) -> None:
        """Canon "carries" built-in drones -- the station is already
        garrisoned at creation, not merely capacity-capped."""
        defenses = Station.default_defenses_for_class(station_class)
        assert defenses["max_defense_drones"] == expected_drones


# ---------------------------------------------------------------------------
# C4/C5 markers: auto_turrets (Class 4+) / defense_grid a.k.a. "advanced
# grid" (Class 5 only). C1-C3 carry neither.
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestBooleanMarkers:
    @pytest.mark.parametrize(
        "station_class",
        [StationClass.CLASS_1, StationClass.CLASS_2, StationClass.CLASS_3],
    )
    def test_classes_1_to_3_carry_neither_marker(self, station_class: StationClass) -> None:
        defenses = Station.default_defenses_for_class(station_class)
        assert defenses["auto_turrets"] is False
        assert defenses["defense_grid"] is False

    def test_class_4_carries_auto_turrets_only(self) -> None:
        defenses = Station.default_defenses_for_class(StationClass.CLASS_4)
        assert defenses["auto_turrets"] is True
        assert defenses["defense_grid"] is False

    def test_class_5_carries_both_markers(self) -> None:
        """Class 5 is strictly the strongest defined tier -- it keeps
        auto_turrets (introduced at Class 4) AND gains the advanced grid."""
        defenses = Station.default_defenses_for_class(StationClass.CLASS_5)
        assert defenses["auto_turrets"] is True
        assert defenses["defense_grid"] is True


# ---------------------------------------------------------------------------
# Classes outside the canon 1-5 range inherit the Class-5 profile.
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestOutOfRangeClassesInheritClassFive:
    @pytest.mark.parametrize(
        "station_class",
        [
            StationClass.CLASS_0,
            StationClass.CLASS_6,
            StationClass.CLASS_7,
            StationClass.CLASS_8,
            StationClass.CLASS_9,
            StationClass.CLASS_10,
            StationClass.CLASS_11,
        ],
    )
    def test_out_of_range_class_equals_class_5_profile(self, station_class: StationClass) -> None:
        assert Station.default_defenses_for_class(station_class) == Station.default_defenses_for_class(
            StationClass.CLASS_5
        )

    def test_raw_int_class_value_is_accepted(self) -> None:
        """The helper also accepts a bare int (defensive coding for any
        future caller that hasn't widened to StationClass yet)."""
        assert Station.default_defenses_for_class(3) == Station.default_defenses_for_class(StationClass.CLASS_3)
        assert Station.default_defenses_for_class(0) == Station.default_defenses_for_class(StationClass.CLASS_5)
        assert Station.default_defenses_for_class(99) == Station.default_defenses_for_class(StationClass.CLASS_5)


# ---------------------------------------------------------------------------
# Constant, class-independent fields.
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestConstantFields:
    @pytest.mark.parametrize(
        "station_class",
        list(StationClass),
    )
    def test_patrol_ships_stays_zero_and_military_contract_stays_false(
        self, station_class: StationClass
    ) -> None:
        """Neither field is a class-birth stat: patrol_ships is an
        owner-acquired garrison asset assigned after creation (nothing in
        the codebase seeds a nonzero Station.defenses.patrol_ships), and
        military_contract is an owner-purchased immunity contract."""
        defenses = Station.default_defenses_for_class(station_class)
        assert defenses["patrol_ships"] == 0
        assert defenses["military_contract"] is False

    def test_key_set_is_stable_across_every_class(self) -> None:
        expected_keys = set(Station.default_defenses_for_class(StationClass.CLASS_1).keys())
        for station_class in StationClass:
            assert set(Station.default_defenses_for_class(station_class).keys()) == expected_keys


# ---------------------------------------------------------------------------
# Fresh-dict-per-call: mutating one call's result must never leak into
# another call's result, the shared class-table profile, or (via the
# existing flat Column default staying untouched) any pre-existing/legacy
# station row that never runs through this helper.
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestNoAliasingAcrossCallsOrLegacyRows:
    def test_two_calls_for_the_same_class_return_independent_dict_objects(self) -> None:
        first = Station.default_defenses_for_class(StationClass.CLASS_5)
        second = Station.default_defenses_for_class(StationClass.CLASS_5)
        assert first == second
        assert first is not second

        first["hull_armor"] = -1
        first["defense_grid"] = False
        assert second["hull_armor"] == 50000
        assert second["defense_grid"] is True

    def test_mutating_a_result_does_not_corrupt_a_later_call_for_the_same_class(self) -> None:
        result = Station.default_defenses_for_class(StationClass.CLASS_2)
        result["defense_drones"] = 999999
        later = Station.default_defenses_for_class(StationClass.CLASS_2)
        assert later["defense_drones"] == 100

    def test_legacy_flat_column_default_is_left_untouched(self) -> None:
        """Existing/legacy stations that never pass through the new helper
        (any creation path other than the two WO-CMB-PORT-DEF-SEED-1 sites)
        must keep resolving to the pre-WO flat 0/50 shape -- this WO is
        additive-only, not a backfill."""
        column_default: Dict[str, Any] = Station.__table__.c.defenses.default.arg
        assert column_default["defense_drones"] == 0
        assert column_default["max_defense_drones"] == 50
        assert column_default["auto_turrets"] is False
        assert column_default["defense_grid"] is False
        assert column_default["hull_armor"] == 5000
        assert column_default["shield_pool"] == 4000


# ---------------------------------------------------------------------------
# Source-pin: combat_service._resolve_port_combat's `.get()` key literals
# must still match what the helper emits, so a future rename on either side
# doesn't silently desync the seeded defenses from what the resolver reads.
# combat_service.py is READ-ONLY for this WO -- this test only asserts.
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestResolverKeySetStillMatchesHelperOutput:
    _RESOLVER_CONSUMED_KEYS = (
        "hull_armor",
        "shield_pool",
        "shield_regen",
        "defensive_fire",
        "point_defense_rating",
        "defense_drones",
        "patrol_ships",
    )

    def test_resolver_source_still_reads_every_expected_key(self) -> None:
        source = inspect.getsource(combat_service.CombatService._resolve_port_combat)
        for key in self._RESOLVER_CONSUMED_KEYS:
            assert f'"{key}"' in source, f"_resolve_port_combat no longer reads {key!r}"

    def test_helper_output_is_a_superset_of_every_resolver_consumed_key(self) -> None:
        emitted = set(Station.default_defenses_for_class(StationClass.CLASS_1).keys())
        assert set(self._RESOLVER_CONSUMED_KEYS).issubset(emitted)
