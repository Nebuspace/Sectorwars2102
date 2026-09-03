"""
LEG-4147: Research Scientists bonus wired into RP faucet rate.

Verifies that research_multiplier returns RESEARCH_SCIENTIST_MULTIPLIER (1.40)
when the planet has ≥1 Research Scientist, and 1.0 otherwise.
"""
from src.models.colonist_profession import ProfessionType
from src.services.profession_service import (
    RESEARCH_SCIENTIST_MULTIPLIER,
    research_multiplier,
)


def test_research_multiplier_with_scientists():
    counts = {ProfessionType.RESEARCH_SCIENTISTS: 5}
    assert research_multiplier(counts) == RESEARCH_SCIENTIST_MULTIPLIER


def test_research_multiplier_without_scientists():
    counts = {ProfessionType.RESEARCH_SCIENTISTS: 0}
    assert research_multiplier(counts) == 1.0


def test_research_multiplier_empty_counts():
    assert research_multiplier({}) == 1.0


def test_research_multiplier_value_is_140_percent():
    """Magnitude is 1.40 = +40% — tagged [OPEN] provisional."""
    assert RESEARCH_SCIENTIST_MULTIPLIER == 1.40
