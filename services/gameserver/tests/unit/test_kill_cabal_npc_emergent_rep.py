"""WO-BUILD-CABAL-NPC-KILL-NO-REPUTATION — table + wiring pins.

Cabal NPC kills must award the same +5 Federation emergent-rep as pirate
kills (factions-and-teams.md TF table / ADR-0032). Cabal spawn remains
design-only; this pins the forward-compatible combat hook.
"""

from pathlib import Path

from src.models.faction import FactionType
from src.services.emergent_reputation_service import EMERGENT_ACTIONS


def test_kill_cabal_npc_registered_parallel_to_pirate():
    pirate = EMERGENT_ACTIONS["KILL_PIRATE_NPC"]
    cabal = EMERGENT_ACTIONS["KILL_CABAL_NPC"]
    assert [(d.faction, d.delta) for d in cabal.deltas] == [
        (FactionType.FEDERATION, 5)
    ]
    assert [(d.faction, d.delta) for d in cabal.deltas] == [
        (d.faction, d.delta) for d in pirate.deltas
    ]


def test_combat_kill_resolver_wires_cabal_branch():
    src = Path(__file__).resolve().parents[2] / "src/services/combat_service.py"
    text = src.read_text()
    assert 'faction_code == "cabal"' in text
    assert '"KILL_CABAL_NPC"' in text
