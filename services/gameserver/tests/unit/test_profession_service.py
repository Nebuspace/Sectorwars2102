"""LEG-2253 — colonist professions kernel unit tests."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest

from src.models.colonist_profession import ProfessionType, PROFESSION_TRAINING_DAYS
from src.models.profession_training_queue import ProfessionTrainingStatus
from src.services import profession_service as ps
from src.services.profession_service import (
    MIN_CITADEL_FOR_TRAINING,
    MIN_MILITARY_ACADEMY_FOR_COMBAT_PILOTS,
    MIN_ORBITAL_SHIPYARD_FOR_SPACE_ENGINEERS,
    MIN_RESEARCH_LAB_FOR_RESEARCH_SCIENTISTS,
    MIN_TERRAFORMING_LAB_FOR_TERRAFORM_ENGINEERS,
    ProfessionService,
)
from src.services.structures import max_kind_level


class _QueryStub:
    def __init__(self, rows):
        self._rows = list(rows)

    def filter(self, *args, **kwargs):
        return self

    def order_by(self, *args, **kwargs):
        return self

    def first(self):
        return self._rows[0] if self._rows else None

    def all(self):
        return list(self._rows)


class _DBStub:
    def __init__(self, professions=None, queue=None, player=None):
        self.professions = professions or []
        self.queue = queue or []
        self.player = player
        self.added = []

    def query(self, model):
        name = getattr(model, "__name__", str(model))
        if name == "ColonistProfession":
            return _QueryStub(self.professions)
        if name == "ProfessionTrainingQueue":
            return _QueryStub(self.queue)
        if name == "Player":
            return _PlayerQueryStub(self.player)
        return _QueryStub([])

    def add(self, obj):
        self.added.append(obj)
        if hasattr(obj, "profession") and hasattr(obj, "trainee_count"):
            self.queue.append(obj)

    def flush(self):
        return None


class _PlayerQueryStub:
    def __init__(self, player):
        self._player = player

    def filter(self, *args, **kwargs):
        return self

    def with_for_update(self):
        return self

    def first(self):
        return self._player


def _structures_with(*buildings):
    return {"v": 1, "buildings": list(buildings), "plots": [], "instability": 0}


def _op_building(kind: str, level: int):
    return {
        "id": f"b_{kind}_{level}",
        "kind": kind,
        "level": level,
        "complete_at": None,
        "browned_out": False,
    }


def _planet(owner_id, *, citadel_level=3, research_level=3, colonists=500, structures=None, equipment=10_000, organics=10_000):
    return SimpleNamespace(
        id=uuid4(),
        owner_id=owner_id,
        citadel_level=citadel_level,
        research_level=research_level,
        colonists=colonists,
        equipment=equipment,
        organics=organics,
        structures=structures if structures is not None else _structures_with(),
    )


def _player(owner_id, *, credits=1_000_000):
    return SimpleNamespace(id=owner_id, credits=credits)


def _db_with_player(owner_id, *, credits=1_000_000, professions=None, queue=None):
    return _DBStub(
        professions=professions,
        queue=queue,
        player=_player(owner_id, credits=credits),
    )


def _svc_with_player(owner_id, *, credits=1_000_000):
    return ProfessionService(_DBStub(player=_player(owner_id, credits=credits))), _player(
        owner_id, credits=credits
    )


def test_training_days_cover_all_twelve_professions():
    assert len(PROFESSION_TRAINING_DAYS) == 12
    for prof in ProfessionType:
        assert prof in PROFESSION_TRAINING_DAYS


def test_production_multipliers_stacking():
    counts = {
        ProfessionType.MINING_ENGINEERS: 100,
        ProfessionType.AGRICULTURAL_SCIENTISTS: 50,
    }
    mult = ps.production_multipliers(counts)
    assert mult["fuel"] == pytest.approx(1.30)
    assert mult["organics"] == pytest.approx(1.35)
    assert mult["colonists"] == pytest.approx(1.15)


def test_terraform_engineer_monthly_cap():
    assert ps.terraform_engineer_monthly_habitability(0) == 0.0
    assert ps.terraform_engineer_monthly_habitability(1000) == pytest.approx(0.5)
    assert ps.terraform_engineer_monthly_habitability(10000) == pytest.approx(5.0)
    assert ps.terraform_engineer_monthly_habitability(20000) == pytest.approx(5.0)


def test_non_owner_rejected():
    owner = uuid4()
    stranger = uuid4()
    planet = _planet(owner)
    svc = ProfessionService(_DBStub())
    with pytest.raises(ValueError, match="not_owner"):
        svc.queue_training(planet, stranger, ProfessionType.MINING_ENGINEERS.value, 10)


def test_citadel_gate():
    owner = uuid4()
    planet = _planet(owner, citadel_level=MIN_CITADEL_FOR_TRAINING - 1)
    svc = ProfessionService(_DBStub())
    with pytest.raises(ValueError, match="citadel_level_too_low"):
        svc.queue_training(planet, owner, ProfessionType.MINING_ENGINEERS.value, 10)


def test_research_scientists_rejected_when_research_lab_below_l3():
    owner = uuid4()
    planet = _planet(
        owner,
        research_level=MIN_RESEARCH_LAB_FOR_RESEARCH_SCIENTISTS - 1,
    )
    svc = ProfessionService(_DBStub())
    with pytest.raises(ValueError, match="research_lab_level_too_low"):
        svc.queue_training(
            planet, owner, ProfessionType.RESEARCH_SCIENTISTS.value, 10
        )


def test_research_scientists_allowed_at_research_lab_l3(monkeypatch):
    owner = uuid4()
    planet = _planet(owner, research_level=MIN_RESEARCH_LAB_FOR_RESEARCH_SCIENTISTS)
    db = _db_with_player(owner)
    svc = ProfessionService(db)
    fixed_now = datetime(2026, 8, 28, tzinfo=timezone.utc)
    fixed_deadline = fixed_now + timedelta(days=40)
    monkeypatch.setattr(ps, "scaled_deadline", lambda hours, start=None: fixed_deadline)

    result = svc.queue_training(
        planet,
        owner,
        ProfessionType.RESEARCH_SCIENTISTS.value,
        10,
        now=fixed_now,
    )
    assert result["success"] is True
    assert result["training_days"] == 40
    assert len(db.added) == 1


def test_training_eligibility_research_scientists_requires_research_lab_l3():
    owner = uuid4()
    svc = ProfessionService(_DBStub())
    low_lab = _planet(owner, research_level=2)
    eligibility = svc.training_eligibility(low_lab)
    assert eligibility[ProfessionType.RESEARCH_SCIENTISTS.value] is False
    assert eligibility[ProfessionType.MINING_ENGINEERS.value] is True

    high_lab = _planet(owner, research_level=3)
    eligibility = svc.training_eligibility(high_lab)
    assert eligibility[ProfessionType.RESEARCH_SCIENTISTS.value] is True


def test_get_state_includes_training_eligibility():
    owner = uuid4()
    planet = _planet(owner, research_level=2)
    svc = ProfessionService(_DBStub())
    state = svc.get_state(planet, owner)
    assert state["training_eligibility"][ProfessionType.RESEARCH_SCIENTISTS.value] is False
    assert state["training_eligibility"][ProfessionType.MINING_ENGINEERS.value] is True


def test_queue_training_charges_provisional_costs(monkeypatch):
    owner = uuid4()
    planet = _planet(owner, colonists=200, equipment=5000)
    player = _player(owner, credits=1_000_000)
    db = _DBStub(player=player)
    svc = ProfessionService(db)
    fixed_now = datetime(2026, 8, 28, tzinfo=timezone.utc)
    fixed_deadline = fixed_now + timedelta(days=20)

    monkeypatch.setattr(ps, "scaled_deadline", lambda hours, start=None: fixed_deadline)

    result = svc.queue_training(
        planet,
        owner,
        ProfessionType.MINING_ENGINEERS.value,
        25,
        now=fixed_now,
    )
    assert result["success"] is True
    assert result["cost_blocked"] is False
    assert result["cost_charged"] is True
    assert result["cost"] == {"credits": 12_500, "equipment": 250}
    assert player.credits == 1_000_000 - 12_500
    assert planet.equipment == 5000 - 250
    assert result["training_days"] == 20
    assert len(db.added) == 1
    row = db.added[0]
    assert row.trainee_count == 25
    assert row.status == ProfessionTrainingStatus.QUEUED.value
    assert planet.colonists == 200


def test_queue_training_rejects_insufficient_credits(monkeypatch):
    owner = uuid4()
    planet = _planet(owner, colonists=200)
    player = _player(owner, credits=100)
    db = _DBStub(player=player)
    svc = ProfessionService(db)
    monkeypatch.setattr(ps, "scaled_deadline", lambda hours, start=None: datetime.now(timezone.utc))
    with pytest.raises(ValueError, match="insufficient_credits"):
        svc.queue_training(planet, owner, ProfessionType.MINING_ENGINEERS.value, 25)


def test_queue_training_rejects_insufficient_equipment(monkeypatch):
    owner = uuid4()
    planet = _planet(owner, colonists=200, equipment=10)
    player = _player(owner, credits=1_000_000)
    db = _DBStub(player=player)
    svc = ProfessionService(db)
    monkeypatch.setattr(ps, "scaled_deadline", lambda hours, start=None: datetime.now(timezone.utc))
    with pytest.raises(ValueError, match="insufficient_equipment"):
        svc.queue_training(planet, owner, ProfessionType.MINING_ENGINEERS.value, 25)


def test_advance_queue_converts_colonists():
    owner = uuid4()
    planet = _planet(owner, colonists=100)
    prof_row = SimpleNamespace(
        planet_id=planet.id,
        profession=ProfessionType.INDUSTRIAL_MANAGERS.value,
        count=0,
    )
    queue_row = SimpleNamespace(
        id=uuid4(),
        planet_id=planet.id,
        profession=ProfessionType.INDUSTRIAL_MANAGERS.value,
        trainee_count=40,
        status=ProfessionTrainingStatus.QUEUED.value,
        completes_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    db = _DBStub(professions=[prof_row], queue=[queue_row])
    svc = ProfessionService(db)
    now = datetime(2026, 8, 28, tzinfo=timezone.utc)
    changed = svc.advance_queue(planet, now=now)
    assert changed is True
    assert planet.colonists == 60
    assert prof_row.count == 40
    assert queue_row.status == ProfessionTrainingStatus.COMPLETED.value


def test_get_state_includes_training_costs_unblocked():
    owner = uuid4()
    planet = _planet(owner)
    db = _DBStub(player=_player(owner))
    svc = ProfessionService(db)
    state = svc.get_state(planet, owner)
    assert state["cost_blocked"] is False
    assert state["cost_basis"] == "provisional_per_100"
    assert len(state["training_costs_per_100"]) == 12
    assert len(state["professions"]) == 12


def test_space_engineer_repair_multiplier_without_specialists():
    planet_id = uuid4()
    db = _DBStub()
    assert ps.space_engineer_repair_multiplier(db, planet_id) == 1.0


def test_space_engineer_repair_multiplier_with_specialists():
    planet_id = uuid4()
    prof_row = SimpleNamespace(
        planet_id=planet_id,
        profession=ProfessionType.SPACE_ENGINEERS.value,
        count=50,
    )
    db = _DBStub(professions=[prof_row])
    assert ps.space_engineer_repair_multiplier(db, planet_id) == pytest.approx(1.25)


def test_trade_specialist_credit_multiplier_without_specialists():
    planet_id = uuid4()
    db = _DBStub()
    assert ps.trade_specialist_credit_multiplier(db, planet_id) == 1.0


def test_trade_specialist_credit_multiplier_with_specialists():
    planet_id = uuid4()
    prof_row = SimpleNamespace(
        planet_id=planet_id,
        profession=ProfessionType.TRADE_SPECIALISTS.value,
        count=10,
    )
    db = _DBStub(professions=[prof_row])
    assert ps.trade_specialist_credit_multiplier(db, planet_id) == pytest.approx(1.25)


class _PlanetQueryStub:
    def __init__(self, planets):
        self._planets = list(planets)

    def join(self, *args, **kwargs):
        return self

    def filter(self, *args, **kwargs):
        return self

    def all(self):
        return list(self._planets)


class _StationProfessionDBStub:
    def __init__(self, *, planets=None, professions=None):
        self.planets = planets or []
        self.professions = professions or []

    def query(self, model):
        name = getattr(model, "__name__", str(model))
        if name == "Planet":
            return _PlanetQueryStub(self.planets)
        if name == "ColonistProfession":
            return _QueryStub(self.professions)
        return _QueryStub([])


def test_space_engineer_repair_multiplier_for_station_sector_match():
    owner = uuid4()
    planet_id = uuid4()
    planet = SimpleNamespace(id=planet_id, sector_id=42)
    prof_row = SimpleNamespace(
        planet_id=planet_id,
        profession=ProfessionType.SPACE_ENGINEERS.value,
        count=5,
    )
    station = SimpleNamespace(sector_id=42)
    db = _StationProfessionDBStub(planets=[planet], professions=[prof_row])
    assert ps.space_engineer_repair_multiplier_for_station(db, owner, station) == pytest.approx(1.25)


def test_trade_specialist_credit_multiplier_for_station_no_owned_planet():
    owner = uuid4()
    station = SimpleNamespace(sector_id=99)
    db = _StationProfessionDBStub(planets=[])
    assert ps.trade_specialist_credit_multiplier_for_station(db, owner, station) == 1.0


def test_mining_engineer_ore_multiplier_without_engineers():
    planet_id = uuid4()
    db = _DBStub()
    assert ps.mining_engineer_ore_multiplier(db, planet_id) == 1.0


def test_mining_engineer_ore_multiplier_with_engineers():
    planet_id = uuid4()
    prof_row = SimpleNamespace(
        planet_id=planet_id,
        profession=ProfessionType.MINING_ENGINEERS.value,
        count=10,
    )
    db = _DBStub(professions=[prof_row])
    assert ps.mining_engineer_ore_multiplier(db, planet_id) == pytest.approx(1.30)


def test_mining_engineer_ore_multiplier_for_region_none():
    owner = uuid4()
    db = _StationProfessionDBStub(planets=[])
    assert ps.mining_engineer_ore_multiplier_for_region(db, owner, None) == 1.0


def test_mining_engineer_ore_multiplier_for_region_match():
    owner = uuid4()
    region_id = uuid4()
    planet_id = uuid4()
    planet = SimpleNamespace(id=planet_id, region_id=region_id)
    prof_row = SimpleNamespace(
        planet_id=planet_id,
        profession=ProfessionType.MINING_ENGINEERS.value,
        count=5,
    )
    db = _StationProfessionDBStub(planets=[planet], professions=[prof_row])
    assert ps.mining_engineer_ore_multiplier_for_region(db, owner, region_id) == pytest.approx(1.30)


def test_mining_engineer_ore_multiplier_for_region_no_owned_planet():
    owner = uuid4()
    db = _StationProfessionDBStub(planets=[])
    assert ps.mining_engineer_ore_multiplier_for_region(db, owner, uuid4()) == 1.0


def test_max_kind_level_ignores_incomplete_and_missing():
    assert max_kind_level({}, "ORBITAL_SHIPYARD") == 0
    grid = _structures_with(
        _op_building("ORBITAL_SHIPYARD", 1),
        {
            "id": "b_pending",
            "kind": "ORBITAL_SHIPYARD",
            "level": 5,
            "complete_at": "2099-01-01T00:00:00+00:00",
        },
        _op_building("MILITARY_ACADEMY", 2),
    )
    assert max_kind_level(grid, "ORBITAL_SHIPYARD") == 1
    assert max_kind_level(grid, "MILITARY_ACADEMY") == 2
    assert max_kind_level(grid, "TERRAFORMING_LAB") == 0


def test_space_engineers_gate_blocks_below_orbital_shipyard_l2():
    owner = uuid4()
    planet = _planet(
        owner,
        structures=_structures_with(
            _op_building("ORBITAL_SHIPYARD", MIN_ORBITAL_SHIPYARD_FOR_SPACE_ENGINEERS - 1)
        ),
    )
    svc = ProfessionService(_DBStub())
    with pytest.raises(ValueError, match="orbital_shipyard_level_too_low"):
        svc.queue_training(planet, owner, ProfessionType.SPACE_ENGINEERS.value, 10)
    eligibility = svc.training_eligibility(planet)
    assert eligibility[ProfessionType.SPACE_ENGINEERS.value] is False


def test_space_engineers_gate_passes_at_orbital_shipyard_l2(monkeypatch):
    owner = uuid4()
    planet = _planet(
        owner,
        structures=_structures_with(
            _op_building("ORBITAL_SHIPYARD", MIN_ORBITAL_SHIPYARD_FOR_SPACE_ENGINEERS)
        ),
    )
    db = _db_with_player(owner)
    svc = ProfessionService(db)
    fixed_now = datetime(2026, 8, 28, tzinfo=timezone.utc)
    fixed_deadline = fixed_now + timedelta(days=30)
    monkeypatch.setattr(ps, "scaled_deadline", lambda hours, start=None: fixed_deadline)
    result = svc.queue_training(
        planet, owner, ProfessionType.SPACE_ENGINEERS.value, 10, now=fixed_now
    )
    assert result["success"] is True
    assert svc.training_eligibility(planet)[ProfessionType.SPACE_ENGINEERS.value] is True


def test_combat_pilots_gate_blocks_below_military_academy_l2():
    owner = uuid4()
    planet = _planet(
        owner,
        structures=_structures_with(
            _op_building("MILITARY_ACADEMY", MIN_MILITARY_ACADEMY_FOR_COMBAT_PILOTS - 1)
        ),
    )
    svc = ProfessionService(_DBStub())
    with pytest.raises(ValueError, match="military_academy_level_too_low"):
        svc.queue_training(planet, owner, ProfessionType.COMBAT_PILOTS.value, 10)
    assert svc.training_eligibility(planet)[ProfessionType.COMBAT_PILOTS.value] is False


def test_combat_pilots_gate_passes_at_military_academy_l2(monkeypatch):
    owner = uuid4()
    planet = _planet(
        owner,
        structures=_structures_with(
            _op_building("MILITARY_ACADEMY", MIN_MILITARY_ACADEMY_FOR_COMBAT_PILOTS)
        ),
    )
    db = _db_with_player(owner)
    svc = ProfessionService(db)
    fixed_now = datetime(2026, 8, 28, tzinfo=timezone.utc)
    fixed_deadline = fixed_now + timedelta(days=25)
    monkeypatch.setattr(ps, "scaled_deadline", lambda hours, start=None: fixed_deadline)
    result = svc.queue_training(
        planet, owner, ProfessionType.COMBAT_PILOTS.value, 10, now=fixed_now
    )
    assert result["success"] is True
    assert svc.training_eligibility(planet)[ProfessionType.COMBAT_PILOTS.value] is True


def test_terraform_engineers_gate_blocks_below_terraforming_lab_l3():
    owner = uuid4()
    planet = _planet(
        owner,
        structures=_structures_with(
            _op_building(
                "TERRAFORMING_LAB", MIN_TERRAFORMING_LAB_FOR_TERRAFORM_ENGINEERS - 1
            )
        ),
    )
    svc = ProfessionService(_DBStub())
    with pytest.raises(ValueError, match="terraforming_lab_level_too_low"):
        svc.queue_training(planet, owner, ProfessionType.TERRAFORM_ENGINEERS.value, 10)
    assert svc.training_eligibility(planet)[ProfessionType.TERRAFORM_ENGINEERS.value] is False


def test_terraform_engineers_gate_passes_at_terraforming_lab_l3(monkeypatch):
    owner = uuid4()
    planet = _planet(
        owner,
        structures=_structures_with(
            _op_building(
                "TERRAFORMING_LAB", MIN_TERRAFORMING_LAB_FOR_TERRAFORM_ENGINEERS
            )
        ),
    )
    db = _db_with_player(owner)
    svc = ProfessionService(db)
    fixed_now = datetime(2026, 8, 28, tzinfo=timezone.utc)
    fixed_deadline = fixed_now + timedelta(days=35)
    monkeypatch.setattr(ps, "scaled_deadline", lambda hours, start=None: fixed_deadline)
    result = svc.queue_training(
        planet, owner, ProfessionType.TERRAFORM_ENGINEERS.value, 10, now=fixed_now
    )
    assert result["success"] is True
    assert svc.training_eligibility(planet)[ProfessionType.TERRAFORM_ENGINEERS.value] is True
