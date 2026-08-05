"""WO-FIRSTLOGIN-NIGHTSHIFTER-THRESHOLD-BUG: personality_threshold_modifier."""

import pytest

from src.utils.guard_personalities import personality_threshold_modifier


def test_night_shifter_gets_easier_threshold():
    assert personality_threshold_modifier("Tired Night-Shifter", 0.40) == pytest.approx(-0.10)


def test_friendly_veteran_easier():
    assert personality_threshold_modifier("Friendly Veteran", 0.30) == pytest.approx(-0.10)


def test_strict_and_paranoid_harder():
    assert personality_threshold_modifier("Strict Rule-Follower", 0.60) == pytest.approx(+0.10)
    assert personality_threshold_modifier("Paranoid Newbie", 0.70) == pytest.approx(+0.10)


def test_neutral_mid_personalities():
    assert personality_threshold_modifier("Shrewd Investigator", 0.50) == pytest.approx(0.0)
    assert personality_threshold_modifier("Cynical Bureaucrat", 0.55) == pytest.approx(0.0)


def test_legacy_suspicion_fallback_covers_night_shifter_band():
    assert personality_threshold_modifier("Unknown Guard", 0.40) == pytest.approx(-0.10)
