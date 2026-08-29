"""LEG-2734: apply_population_growth honors Medical/Agricultural colonist multipliers."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from src.models.colonist_profession import ProfessionType
from src.services.planetary_service import PlanetaryService


def _growth_planet(*, colonists: int = 500, habitability: int = 80):
    return SimpleNamespace(
        id=uuid4(),
        colonists=colonists,
        population=colonists,
        habitability_score=habitability,
        citadel_level=1,
        max_colonists=5000,
        max_population=80000,
        last_growth_at=datetime.now(UTC) - timedelta(days=1),
        under_siege=False,
        active_events={},
    )


def _db_with_professions(planet_id, professions: dict[ProfessionType, int]):
    rows = [
        SimpleNamespace(
            planet_id=planet_id,
            profession=prof.value,
            count=count,
        )
        for prof, count in professions.items()
    ]
    db = MagicMock()
    q = MagicMock()
    q.filter.return_value.all.return_value = rows
    db.query.return_value = q
    return db


def _baseline_gain(planet) -> int:
    before = planet.colonists
    PlanetaryService(db=_db_with_professions(planet.id, {})).apply_population_growth(planet)
    return planet.colonists - before


@pytest.mark.parametrize(
    ("profession", "multiplier"),
    [
        (ProfessionType.MEDICAL_PROFESSIONALS, 1.20),
        (ProfessionType.AGRICULTURAL_SCIENTISTS, 1.15),
    ],
)
def test_apply_population_growth_honors_profession_colonist_multiplier(
    profession, multiplier
):
    baseline_planet = _growth_planet()
    boosted_planet = _growth_planet()

    baseline_gain = _baseline_gain(baseline_planet)
    assert baseline_gain > 0

    boosted_db = _db_with_professions(boosted_planet.id, {profession: 10})
    PlanetaryService(db=boosted_db).apply_population_growth(boosted_planet)
    boosted_gain = boosted_planet.colonists - 500

    assert boosted_gain == int(baseline_gain * multiplier)
