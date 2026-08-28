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
    MIN_RESEARCH_LAB_FOR_RESEARCH_SCIENTISTS,
    ProfessionService,
)


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
    def __init__(self, professions=None, queue=None):
        self.professions = professions or []
        self.queue = queue or []
        self.added = []

    def query(self, model):
        name = getattr(model, "__name__", str(model))
        if name == "ColonistProfession":
            return _QueryStub(self.professions)
        if name == "ProfessionTrainingQueue":
            return _QueryStub(self.queue)
        return _QueryStub([])

    def add(self, obj):
        self.added.append(obj)
        if hasattr(obj, "profession") and hasattr(obj, "trainee_count"):
            self.queue.append(obj)

    def flush(self):
        return None


def _planet(owner_id, *, citadel_level=3, research_level=3, colonists=500):
    return SimpleNamespace(
        id=uuid4(),
        owner_id=owner_id,
        citadel_level=citadel_level,
        research_level=research_level,
        colonists=colonists,
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
    db = _DBStub()
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


def test_queue_training_no_charge(monkeypatch):
    owner = uuid4()
    planet = _planet(owner, colonists=200)
    db = _DBStub()
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
    assert result["cost_blocked"] is True
    assert result["cost_charged"] is False
    assert result["training_days"] == 20
    assert len(db.added) == 1
    row = db.added[0]
    assert row.trainee_count == 25
    assert row.status == ProfessionTrainingStatus.QUEUED.value
    assert planet.colonists == 200


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


def test_get_state_includes_cost_blocked():
    owner = uuid4()
    planet = _planet(owner)
    db = _DBStub()
    svc = ProfessionService(db)
    state = svc.get_state(planet, owner)
    assert state["cost_blocked"] is True
    assert "DECISION-NEEDED" in state["cost_block_reason"]
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
