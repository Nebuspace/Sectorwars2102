"""Unit pins for ADR-0063 N-I1 hop caps + Pirate Lord routing helper."""
from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from src.services import npc_engagement_service as eng


@pytest.mark.unit
class TestNpcClassRoutingDistance:
    def test_dict_matches_canon_defaults(self) -> None:
        assert eng.NPC_CLASS_ROUTING_DISTANCE == {
            "sector_marshal": 5,
            "faction_patrol_captain": 8,
            "pirate_lord": 3,
        }
        assert eng.PIRATE_LORD_MAX_HOPS == 3

    def test_hop_cap_for_npc_by_title(self) -> None:
        marshal = SimpleNamespace(title="Marshal", archetype=None)
        captain = SimpleNamespace(title="Marshal-Captain", archetype=None)
        lord = SimpleNamespace(title="Pirate Lord", archetype=None)
        pirate_captain = SimpleNamespace(title="Pirate Captain", archetype=None)

        assert eng.hop_cap_for_npc(marshal) == eng.MARSHAL_MAX_HOPS
        assert eng.hop_cap_for_npc(captain) == eng.CAPTAIN_MAX_HOPS
        assert eng.hop_cap_for_npc(lord) == eng.PIRATE_LORD_MAX_HOPS
        # Pirate Captain contains "Captain" — LE hop helper still classifies
        # by title substring; pirate offense uses _pick_pirate_lord_responders.
        assert eng.hop_cap_for_npc(pirate_captain) == eng.CAPTAIN_MAX_HOPS

    def test_is_pirate_lord_exact_title(self) -> None:
        assert eng._is_pirate_lord(SimpleNamespace(title="Pirate Lord")) is True
        assert eng._is_pirate_lord(SimpleNamespace(title="Pirate Captain")) is False
        assert eng._is_pirate_lord(SimpleNamespace(title=None)) is False
