"""Regression tests for the WO-FIX-DEFENSE-SHIELDS-CITADEL-PREREQ-BYPASS /
WO-FIX-CARGO-WRECK-LOOT-INFLATION / WO-FIX-DEFENSE-TURRETS-FIGHTERS-NO-COMBAT-
EFFECT trio (all touching combat_service.py + planetary_service.py).

DB-free throughout: PlanetaryService(MagicMock()) / CombatService(MagicMock())
stand in for the Session, mirroring test_siege_stockpile_skim.py's
``_locked_query`` pattern for the two-query update_defenses lock chain.
"""
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

from src.models.planet import PlanetType
from src.services.combat_service import CombatService
from src.services.planetary_service import PlanetaryService, defense_unit_price


# --------------------------------------------------------------------------- #
# (1) WO-FIX-DEFENSE-SHIELDS-CITADEL-PREREQ-BYPASS
# --------------------------------------------------------------------------- #


def _make_planet(**kw):
    defaults = dict(
        id=uuid4(),
        citadel_level=1,
        type=PlanetType.TERRAN,
        defense_turrets=0,
        defense_shields=0,
        defense_fighters=0,
    )
    defaults.update(kw)
    return SimpleNamespace(**defaults)


def _ownership_query(planet):
    q = MagicMock()
    q.join.return_value = q
    q.filter.return_value = q
    q.first.return_value = planet
    return q


def _locked_planet_query(planet):
    q = MagicMock()
    q.filter.return_value.populate_existing.return_value.with_for_update.return_value.first.return_value = planet
    return q


def _service_for_update_defenses(planet, player=None):
    svc = PlanetaryService(db=MagicMock())
    queries = [_ownership_query(planet), _locked_planet_query(planet)]
    if player is not None:
        pq = MagicMock()
        pq.filter.return_value.populate_existing.return_value.with_for_update.return_value.first.return_value = player
        queries.append(pq)
    svc.db.query.side_effect = queries
    return svc


def test_update_defenses_shields_purchase_does_not_write_defense_shields():
    """The cheap per-unit 'shields' purchase must no longer touch
    planet.defense_shields -- that column is the real shield-GENERATOR level
    (upgrade_shield_generator), gated into citadel L4/L5 prerequisites."""
    planet = _make_planet(defense_shields=0)
    player = SimpleNamespace(id=uuid4(), credits=1_000_000)
    svc = _service_for_update_defenses(planet, player)

    result = svc.update_defenses(
        planet_id=planet.id, player_id=player.id, shields=8,
    )

    assert planet.defense_shields == 0  # untouched by the cheap purchase
    assert result["defenses"]["shields"] == 0


def test_update_defenses_shields_purchase_is_not_charged():
    """No credits are deducted for the (now-ignored) shields field -- only
    turrets/fighters are still priced."""
    planet = _make_planet(defense_turrets=0, defense_fighters=0)
    player = SimpleNamespace(id=uuid4(), credits=1_000_000)
    svc = _service_for_update_defenses(planet, player)

    result = svc.update_defenses(
        planet_id=planet.id, player_id=player.id, turrets=10, shields=500,
    )

    expected_cost = defense_unit_price("turrets", planet.citadel_level, planet.type) * 10
    assert result["creditsSpent"] == expected_cost
    assert planet.defense_turrets == 10


def test_citadel_shield_gate_reads_only_the_real_generator_ladder():
    """citadel_service's L4/L5 shield-generator prerequisite check reads
    planet.defense_shields -- confirm the cheap per-unit purchase path (now
    fixed to never touch it) can no longer satisfy it. A planet with the
    cheap-purchase field maxed but the real generator level at 0 must still
    fail the L4 (>=4) prerequisite."""
    from src.services.citadel_service import CitadelService

    planet = _make_planet(defense_shields=0)  # real generator level: none
    planet.active_events = None
    svc = CitadelService(db=MagicMock())
    req = {"type": "shield", "min": 4, "name": "Shield Generator L4"}
    result = svc._eval_prereq(planet, req, "L4 Major Colony", operational={}, queued_types=set())
    assert result is not None
    assert result["success"] is False
    assert result["error_code"] == "ERR_CITADEL_PREREQUISITE_MISSING"
    assert result["building_key"] == "shield_generator"
    assert result["building_name"] == "Shield Generator L4"


# --------------------------------------------------------------------------- #
# (2) WO-FIX-CARGO-WRECK-LOOT-INFLATION
# --------------------------------------------------------------------------- #


def _destroyed_ship(contents):
    return SimpleNamespace(
        id=uuid4(),
        cargo={"contents": dict(contents)},
        sector=SimpleNamespace(id=uuid4(), sector_id=1),
        sector_id=1,
        type=SimpleNamespace(value="cargo_hauler"),
    )


def test_wreck_recovery_band_applies_per_damage_type(monkeypatch):
    """An EMP kill recovers far more cargo than a missile kill -- proves the
    band is actually threaded through and damage-type-sensitive, not a flat
    drop-everything (the pre-fix behavior)."""
    cs = CombatService(db=MagicMock())
    monkeypatch.setattr(cs.db, "begin_nested", lambda: _nullcontext())

    emp_ship = _destroyed_ship({"ore": 1000})
    missile_ship = _destroyed_ship({"ore": 1000})

    monkeypatch.setattr("src.services.combat_service.random.uniform", lambda a, b: b)  # band ceiling
    emp_wreck = cs._spawn_cargo_wreck(
        destroyed_ship=emp_ship, cause="combat", original_owner=None,
        killing_blow_pilot=None, killing_damage_type="emp",
    )
    missile_wreck = cs._spawn_cargo_wreck(
        destroyed_ship=missile_ship, cause="combat", original_owner=None,
        killing_blow_pilot=None, killing_damage_type="missile",
    )

    assert emp_wreck.cargo["ore"] == 1000  # emp band ceiling = 100%
    assert missile_wreck.cargo["ore"] == 400  # missile band ceiling = 40%
    assert missile_wreck.cargo["ore"] < emp_wreck.cargo["ore"]


def test_wreck_no_longer_drops_full_cargo_unconditionally(monkeypatch):
    """The bug this fixes: _spawn_cargo_wreck previously ignored the killing
    damage type entirely and dropped 100% of surviving cargo. A mid-band roll
    must now recover LESS than the full hold."""
    cs = CombatService(db=MagicMock())
    monkeypatch.setattr(cs.db, "begin_nested", lambda: _nullcontext())
    ship = _destroyed_ship({"ore": 1000, "organics": 500})

    monkeypatch.setattr("src.services.combat_service.random.uniform", lambda a, b: (a + b) / 2)
    wreck = cs._spawn_cargo_wreck(
        destroyed_ship=ship, cause="combat", original_owner=None,
        killing_blow_pilot=None, killing_damage_type="missile",
    )

    assert wreck.cargo["ore"] < 1000
    assert wreck.cargo["organics"] < 500


def test_wreck_unknown_damage_type_falls_back_to_default_band(monkeypatch):
    """No killing_damage_type (drone/planet/port-defense/fleet kills) still
    spawns a wreck via the documented default band, not a crash or a 100%
    drop."""
    cs = CombatService(db=MagicMock())
    monkeypatch.setattr(cs.db, "begin_nested", lambda: _nullcontext())
    ship = _destroyed_ship({"ore": 1000})

    monkeypatch.setattr("src.services.combat_service.random.uniform", lambda a, b: b)
    wreck = cs._spawn_cargo_wreck(
        destroyed_ship=ship, cause="combat", original_owner=None,
        killing_blow_pilot=None, killing_damage_type=None,
    )

    assert wreck.cargo["ore"] == 800  # default band ceiling = 80% (laser-equivalent)


class _nullcontext:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


# --------------------------------------------------------------------------- #
# (3) WO-FIX-DEFENSE-TURRETS-FIGHTERS-NO-COMBAT-EFFECT
# --------------------------------------------------------------------------- #


def test_defense_turrets_and_fighters_now_contribute_to_reduction_and_kills():
    """Before this fix, defense_turrets/defense_fighters were read nowhere in
    the reduction calc -- a planet with a large purchased garrison and zero
    citadel buildings got IDENTICAL damage_reduction/anti_drone_kills_per_round
    to one with none."""
    cs = CombatService(db=MagicMock())

    # LEG-172: citadel_level must be 0 here. After LEG-164, citadel_passive_defense_rating
    # folds into effective_fighters; a default L1 citadel alone yields anti_drone_kills>0
    # and breaks the "bare garrison = zero contribution" pin this test exists for.
    bare_planet = _make_planet(
        citadel_level=0, defense_level=0, defense_shields=0, shields=0,
    )
    bare_planet.active_events = None
    bare_planet.specialization = None

    garrisoned_planet = _make_planet(
        citadel_level=0, defense_level=0, defense_shields=0, shields=0,
        defense_turrets=100, defense_fighters=100,
    )
    garrisoned_planet.active_events = None
    garrisoned_planet.specialization = None

    bare = cs._calculate_planetary_defense_reduction(bare_planet)
    garrisoned = cs._calculate_planetary_defense_reduction(garrisoned_planet)

    assert garrisoned["damage_reduction"] > bare["damage_reduction"]
    assert garrisoned["anti_drone_kills_per_round"] > bare["anti_drone_kills_per_round"]
    assert bare["damage_reduction"] == 0.0
    assert bare["anti_drone_kills_per_round"] == 0


def test_defense_turrets_fighters_contribution_is_capped():
    """The NO-CANON unit coefficients are capped well under the citadel-
    building terms -- an absurd garrison count must not blow past the caps."""
    cs = CombatService(db=MagicMock())
    planet = _make_planet(
        citadel_level=0, defense_level=0, defense_shields=0, shields=0,
        defense_turrets=10_000, defense_fighters=10_000,
    )
    planet.active_events = None
    planet.specialization = None

    result = cs._calculate_planetary_defense_reduction(planet)

    assert result["damage_reduction"] <= 0.90
    assert result["anti_drone_kills_per_round"] <= 50  # 20 (turrets cap) + 30 (fighters cap)
