"""WO-QUALITY-factiontype-missing-guard regression pin.

``FactionType._missing_`` (models/faction.py) used to call ``value.upper()``
unguarded -- a non-str (or None) lookup value raised AttributeError instead
of the clean ValueError an unrecognized enum member should raise. Pure,
DB-free: FactionType is a plain str/Enum, no fixtures needed.
"""
from __future__ import annotations

import pytest

from src.models.faction import FactionType


@pytest.mark.unit
class TestFactionTypeMissingGuard:
    def test_none_raises_value_error_not_attribute_error(self) -> None:
        with pytest.raises(ValueError):
            FactionType(None)

    def test_non_str_raises_value_error_not_attribute_error(self) -> None:
        with pytest.raises(ValueError):
            FactionType(123)

    def test_case_insensitive_lookup_still_resolves(self) -> None:
        assert FactionType("federation") == FactionType.FEDERATION
        assert FactionType("FEDERATION") == FactionType.FEDERATION
        assert FactionType("Federation") == FactionType.FEDERATION
        assert FactionType("oUtLaWs") == FactionType.OUTLAWS

    def test_unrecognized_string_still_raises_value_error(self) -> None:
        with pytest.raises(ValueError):
            FactionType("NotAReal")
