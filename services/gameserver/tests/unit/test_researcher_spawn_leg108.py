"""LEG-108 — RESEARCHER spawn path is wired (DB-free structural pins).

Full spawn/fill + nebula-host preference live in
``tests/integration/test_npc_living_system.py::TestResearcherSpawnLEG108``
(needs Postgres). This file pins the substrate that makes that path real:
KIND_CONFIG, bootstrap call sites, and the Rogue Scientist drop mapping.
"""
from src.models.npc_character import NPCArchetype
from src.models.ship import ShipType
from src.services import combat_service
from src.services.npc_spawn_service import (
    KIND_CONFIG,
    NEBULA_SURVEYOR_KIND,
    NOVA_SCIENTIFIC_FACTION_CODE,
    RESEARCHERS_PER_REGION,
    RESEARCHER_TITLES,
    bootstrap_galaxy,
    seed_researcher_rosters,
)


def test_nebula_surveyor_kind_maps_to_researcher():
    cfg = KIND_CONFIG[NEBULA_SURVEYOR_KIND]
    assert cfg.archetype == NPCArchetype.RESEARCHER
    assert cfg.ship_type == ShipType.SCOUT_SHIP
    assert cfg.joins_squad is False
    assert cfg.default_faction_code == NOVA_SCIENTIFIC_FACTION_CODE
    assert RESEARCHERS_PER_REGION > 0
    assert "Rogue Scientist" in RESEARCHER_TITLES


def test_seed_researcher_rosters_is_bootstrap_entry():
    # bootstrap_galaxy body must call seed_researcher_rosters (LEG-108).
    import inspect

    src = inspect.getsource(bootstrap_galaxy)
    assert "seed_researcher_rosters" in src
    assert callable(seed_researcher_rosters)


def test_researcher_quantum_drop_constants_match_canon_table():
    # quantum-resources.md §3 Rogue Scientist: 15% / 1–3 shards.
    assert combat_service.NPC_QUANTUM_DROP_RESEARCHER_CHANCE == 0.15
    assert combat_service.NPC_QUANTUM_DROP_RESEARCHER_MIN == 1
    assert combat_service.NPC_QUANTUM_DROP_RESEARCHER_MAX == 3
