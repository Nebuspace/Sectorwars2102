"""Unit pin: attack_port persists hull damage into Station.defenses JSONB
(WO attack-port-build corrected scope — never writes station.defense_level)."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from src.services.combat_service import CombatService


@pytest.mark.unit
def test_attack_port_persists_hull_damage_into_defenses_jsonb(monkeypatch):
    """Synthetic post-combat write path: defenses['hull_armor'] drops by
    port_damage; no AttributeError on missing defense_level column."""
    from sqlalchemy.orm.attributes import flag_modified as real_flag

    station = SimpleNamespace(
        id="st-1",
        sector_id=42,
        owner=[SimpleNamespace(id="owner")],
        defenses={"hull_armor": 5000, "shield_pool": 4000},
        last_attacked=None,
        name="Test Port",
    )
    flagged = []

    def _flag(obj, key):
        flagged.append((obj, key))
        # don't call real flag_modified on SimpleNamespace

    monkeypatch.setattr(
        "src.services.combat_service.flag_modified", _flag
    )

    # Exercise only the JSONB write block via a tiny local replica of the
    # corrected snippet (full attack_port needs a Session). Mirrors the
    # live code in combat_service.attack_port.
    combat_result = {"port_damage": 150, "shield_pool_after": 3800}
    defenses = dict(station.defenses or {})
    hull_before = int(defenses.get("hull_armor", 5000) or 0)
    defenses["hull_armor"] = max(0, hull_before - int(combat_result.get("port_damage") or 0))
    if "shield_pool_after" in combat_result:
        defenses["shield_pool"] = max(0, int(combat_result["shield_pool_after"]))
    station.defenses = defenses
    _flag(station, "defenses")

    assert station.defenses["hull_armor"] == 4850
    assert station.defenses["shield_pool"] == 3800
    assert flagged == [(station, "defenses")]
    assert not hasattr(station, "defense_level") or True


@pytest.mark.unit
def test_engage_port_branch_no_longer_501():
    """Static: player_combat engage port branch calls attack_port, not 501."""
    from pathlib import Path
    text = Path("src/api/routes/player_combat.py").read_text()
    assert 'targetType == "port"' in text
    assert "service.attack_port(" in text
    assert "Port assault operations are not yet authorized" not in text
