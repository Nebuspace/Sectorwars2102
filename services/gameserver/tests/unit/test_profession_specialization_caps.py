"""LEG-3969 — specialization caps by citadel phase (professions.md L134-142)."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest

from src.models.colonist_profession import ProfessionType
from src.models.profession_training_queue import ProfessionTrainingStatus
from src.services import profession_service as ps
from src.services.profession_service import (
    MIN_CITADEL_FOR_TRAINING,
    ProfessionService,
    max_specialized_for_planet,
    specialization_cap_fraction,
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


class _PlayerQueryStub:
    def __init__(self, player):
        self._player = player

    def filter(self, *args, **kwargs):
        return self

    def with_for_update(self):
        return self

    def first(self):
        return self._player


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


def _structures_with():
    return {"v": 1, "buildings": [], "plots": [], "instability": 0}


def _planet(owner_id, *, citadel_level=3, colonists=1000, equipment=10_000, organics=10_000):
    return SimpleNamespace(
        id=uuid4(),
        owner_id=owner_id,
        citadel_level=citadel_level,
        research_level=3,
        colonists=colonists,
        equipment=equipment,
        organics=organics,
        structures=_structures_with(),
    )


def _player(owner_id, *, credits=1_000_000):
    return SimpleNamespace(id=owner_id, credits=credits)


def _profession_row(planet_id, profession: ProfessionType, count: int):
    return SimpleNamespace(
        planet_id=planet_id,
        profession=profession.value,
        count=count,
        active_count=None,
    )


def _queue_row(planet_id, trainee_count: int, *, status=ProfessionTrainingStatus.QUEUED.value):
    return SimpleNamespace(
        id=uuid4(),
        planet_id=planet_id,
        profession=ProfessionType.MINING_ENGINEERS.value,
        trainee_count=trainee_count,
        status=status,
        queued_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
        completes_at=datetime(2099, 1, 1, tzinfo=timezone.utc),
    )


@pytest.mark.parametrize(
    "level,expected",
    [
        (0, 0.0),
        (1, 0.0),
        (2, 0.10),
        (3, 0.25),
        (4, 0.50),
        (5, 0.75),
        (6, 0.75),
    ],
)
def test_specialization_cap_fraction_by_citadel_level(level, expected):
    assert specialization_cap_fraction(level) == pytest.approx(expected)


@pytest.mark.parametrize(
    "citadel_level,population,specialized,expected_max",
    [
        (1, 1000, 0, 0),
        (2, 1000, 0, 100),
        (3, 1000, 0, 250),
        (4, 1000, 0, 500),
        (5, 1000, 0, 750),
        (3, 1200, 200, 300),  # 1000 generic + 200 specialized → cap 25% of 1200
    ],
)
def test_max_specialized_for_planet(citadel_level, population, specialized, expected_max):
    owner = uuid4()
    planet = _planet(owner, citadel_level=citadel_level, colonists=population - specialized)
    professions = []
    if specialized:
        professions.append(
            _profession_row(planet.id, ProfessionType.MINING_ENGINEERS, specialized)
        )
    db = _DBStub(professions=professions)
    assert max_specialized_for_planet(planet, db) == expected_max


def test_settlement_rejects_local_training_via_citadel_gate():
    owner = uuid4()
    planet = _planet(owner, citadel_level=2, colonists=900)
    professions = [_profession_row(planet.id, ProfessionType.MINING_ENGINEERS, 100)]
    db = _DBStub(professions=professions, player=_player(owner))
    svc = ProfessionService(db)
    with pytest.raises(ValueError, match="citadel_level_too_low"):
        svc.queue_training(planet, owner, ProfessionType.MINING_ENGINEERS.value, 1)


def test_settlement_allows_held_specialists_in_state():
    owner = uuid4()
    planet = _planet(owner, citadel_level=2, colonists=900)
    professions = [_profession_row(planet.id, ProfessionType.MINING_ENGINEERS, 100)]
    db = _DBStub(professions=professions)
    svc = ProfessionService(db)
    state = svc.get_state(planet, owner)
    assert state["professions"][ProfessionType.MINING_ENGINEERS.value] == 100


@pytest.mark.parametrize("citadel_level,cap_count", [(3, 250), (4, 500), (5, 750)])
def test_queue_training_accepts_at_cap_boundary(citadel_level, cap_count, monkeypatch):
    owner = uuid4()
    planet = _planet(owner, citadel_level=citadel_level, colonists=1000)
    db = _DBStub(player=_player(owner))
    svc = ProfessionService(db)
    fixed_now = datetime(2026, 9, 2, tzinfo=timezone.utc)
    monkeypatch.setattr(ps, "scaled_deadline", lambda hours, start=None: fixed_now + timedelta(days=1))

    result = svc.queue_training(
        planet, owner, ProfessionType.MINING_ENGINEERS.value, cap_count, now=fixed_now
    )
    assert result["success"] is True
    assert len(db.added) == 1


@pytest.mark.parametrize("citadel_level,cap_count", [(3, 250), (4, 500), (5, 750)])
def test_queue_training_rejects_one_over_cap(citadel_level, cap_count):
    owner = uuid4()
    planet = _planet(owner, citadel_level=citadel_level, colonists=1000)
    db = _DBStub(player=_player(owner))
    svc = ProfessionService(db)
    with pytest.raises(ValueError, match="specialization_cap_exceeded"):
        svc.queue_training(
            planet, owner, ProfessionType.MINING_ENGINEERS.value, cap_count + 1
        )


def test_queue_training_rejects_when_existing_specialists_at_cap():
    owner = uuid4()
    planet = _planet(owner, citadel_level=3, colonists=750)
    professions = [_profession_row(planet.id, ProfessionType.MINING_ENGINEERS, 250)]
    db = _DBStub(professions=professions, player=_player(owner))
    svc = ProfessionService(db)
    with pytest.raises(ValueError, match="specialization_cap_exceeded"):
        svc.queue_training(planet, owner, ProfessionType.MINING_ENGINEERS.value, 1)


def test_queue_training_counts_queued_trainees_toward_cap():
    owner = uuid4()
    planet = _planet(owner, citadel_level=3, colonists=750)
    queue = [_queue_row(planet.id, 200)]
    professions = [_profession_row(planet.id, ProfessionType.MINING_ENGINEERS, 50)]
    db = _DBStub(professions=professions, queue=queue, player=_player(owner))
    svc = ProfessionService(db)
    with pytest.raises(ValueError, match="specialization_cap_exceeded"):
        svc.queue_training(planet, owner, ProfessionType.MINING_ENGINEERS.value, 1)


def test_outpost_zero_cap_rejects_training_at_l3_gate_first():
    """L1 training is blocked by citadel gate before cap; L3+ uses cap code."""
    owner = uuid4()
    planet = _planet(owner, citadel_level=MIN_CITADEL_FOR_TRAINING - 1, colonists=1000)
    db = _DBStub(player=_player(owner))
    svc = ProfessionService(db)
    with pytest.raises(ValueError, match="citadel_level_too_low"):
        svc.queue_training(planet, owner, ProfessionType.MINING_ENGINEERS.value, 1)


def test_get_state_exposes_specialization_cap_fields(monkeypatch):
    owner = uuid4()
    planet = _planet(owner, citadel_level=3, colonists=800)
    professions = [_profession_row(planet.id, ProfessionType.MINING_ENGINEERS, 200)]
    queue = [_queue_row(planet.id, 50)]
    db = _DBStub(professions=professions, queue=queue)
    svc = ProfessionService(db)
    monkeypatch.setattr(svc, "advance_queue", lambda *_a, **_k: False)
    state = svc.get_state(planet, owner)
    assert state["specialization_cap_max"] == 250  # 25% of 800 generic + 200 specialized
    assert state["specialized_total"] == 250  # 200 trained + 50 queued
    assert state["specialization_cap_fraction"] == pytest.approx(0.25)
    assert "specialization_cap_max" in state
    assert "specialized_total" in state
    assert "specialization_cap_fraction" in state


def test_l3_partial_specialists_allows_remaining_headroom(monkeypatch):
    owner = uuid4()
    planet = _planet(owner, citadel_level=3, colonists=800)
    professions = [_profession_row(planet.id, ProfessionType.MINING_ENGINEERS, 200)]
    db = _DBStub(professions=professions, player=_player(owner))
    svc = ProfessionService(db)
    fixed_now = datetime(2026, 9, 2, tzinfo=timezone.utc)
    monkeypatch.setattr(ps, "scaled_deadline", lambda hours, start=None: fixed_now + timedelta(days=1))

    result = svc.queue_training(
        planet, owner, ProfessionType.MINING_ENGINEERS.value, 50, now=fixed_now
    )
    assert result["success"] is True
