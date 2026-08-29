"""LEG-2252 Path B: bang.* reconstruction + CLI fail-closed / idempotent helpers."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from src.services.bang_import_service import BangImportService, ParsedUniverse
from src.services.bang_schema import (
    config_fingerprint,
    region_already_imported,
    stamp_region_snapshot,
    universe_from_bang_rows,
)

FIXTURE_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "bang"


def _tiny_rows():
    u1 = uuid4()
    s1 = uuid4()
    s2 = uuid4()
    universe = {
        "id": u1,
        "name": "tiny",
        "version": "1.3.11",
        "seed": 7,
        "total_sectors": 2,
        "config": {"sectors": 2, "seed": 7, "fedspace": 1, "maxWarps": 6},
    }
    sectors = [
        {
            "id": s1,
            "universe_id": u1,
            "sector_number": 1,
            "pos_x": 0,
            "pos_y": 0,
            "pos_z": 0,
            "beacon": None,
            "explored": False,
            "resources": None,
        },
        {
            "id": s2,
            "universe_id": u1,
            "sector_number": 2,
            "pos_x": 1,
            "pos_y": 0,
            "pos_z": 0,
            "beacon": "nav",
            "explored": True,
            "resources": None,
        },
    ]
    warps = [
        {
            "universe_id": u1,
            "from_sector": 1,
            "to_sector": 2,
            "one_way": False,
            "is_latent": False,
        }
    ]
    ports = [
        {
            "sector_id": s1,
            "name": "Port One",
            "class": 1,
            "is_spacedock": False,
            "commodities": {"ore": {"action": "B", "quantity": 10}},
            "black_market": False,
        }
    ]
    planets = [
        {
            "sector_id": s2,
            "name": "Rock",
            "type": "barren",
            "owner": None,
            "habitability_score": 10,
            "max_population": 10000,
            "max_colonists": 1000,
            "ore": 1,
            "organics": 2,
            "equipment": 3,
            "colonists": 0,
            "citadel_level": 1,
            "citadel_drone_capacity": 10,
            "citadel_safe_contents": 0,
            "citadel_drone_inventory": 0,
        }
    ]
    clusters = [
        {
            "cluster_number": 1,
            "name": "Alpha",
            "type": "STANDARD",
            "sector_range_start": 1,
            "sector_range_end": 2,
            "coords_x": 0,
            "coords_y": 0,
            "coords_z": 0,
            "warp_stability": 1.0,
            "economic_value": 10,
            "is_discovered": True,
            "is_hidden": False,
        }
    ]
    special_locations = [{"type": "terra", "sector_number": 1}]
    return dict(
        universe=universe,
        sectors=sectors,
        warps=warps,
        ports=ports,
        planets=planets,
        clusters=clusters,
        special_locations=special_locations,
        nebulae=[],
        special_formations=[],
        npc_rosters=[],
    )


def test_reconstruct_tiny_universe_has_translator_required_keys():
    rows = _tiny_rows()
    raw = universe_from_bang_rows(
        rows["universe"],
        sectors=rows["sectors"],
        warps=rows["warps"],
        special_locations=rows["special_locations"],
        ports=rows["ports"],
        planets=rows["planets"],
        nebulae=rows["nebulae"],
        clusters=rows["clusters"],
        special_formations=rows["special_formations"],
        npc_rosters=rows["npc_rosters"],
    )
    assert raw["version"] == "1.3.11"
    assert raw["seed"] == 7
    assert raw["totalSectors"] == 2
    assert set(raw["sectors"]) == {"1", "2"}
    assert raw["sectors"]["1"]["port"]["name"] == "Port One"
    assert raw["sectors"]["2"]["planets"][0]["citadel"]["level"] == 1
    assert raw["warps"][0]["from"] == 1
    assert raw["warps"][0]["isLatent"] is False
    assert raw["clusters"][0]["sectorCount"] == 2
    assert raw["fedspaceSectors"] == [1]
    assert raw["specialLocations"][0] == {"type": "terra", "sectorId": 1}


def test_reconstruct_omits_absent_optional_port_flags():
    rows = _tiny_rows()
    rows["ports"][0].pop("black_market")
    rows["warps"][0].pop("is_latent")
    raw = universe_from_bang_rows(
        rows["universe"],
        sectors=rows["sectors"],
        warps=rows["warps"],
        ports=rows["ports"],
        planets=rows["planets"],
        clusters=rows["clusters"],
    )
    assert "black_market" not in raw["sectors"]["1"]["port"]
    assert "isLatent" not in raw["warps"][0]


def test_reconstruct_terran_fixture_via_synthetic_bang_rows():
    fixture = json.loads((FIXTURE_DIR / "v1_3_0_terran_space.json").read_text())
    u_id = uuid4()
    sector_ids = {}
    sectors = []
    ports = []
    planets = []
    nebulae = []
    for _key, sec in fixture["sectors"].items():
        sid = uuid4()
        sector_ids[int(sec["id"])] = sid
        sectors.append(
            {
                "id": sid,
                "universe_id": u_id,
                "sector_number": int(sec["id"]),
                "pos_x": sec["position"]["x"],
                "pos_y": sec["position"]["y"],
                "pos_z": sec["position"]["z"],
                "beacon": sec.get("beacon"),
                "explored": sec.get("explored", False),
                "resources": sec.get("resources"),
            }
        )
        if sec.get("port"):
            ports.append(
                {
                    "sector_id": sid,
                    "name": sec["port"]["name"],
                    "class": sec["port"]["class"],
                    "is_spacedock": sec["port"].get("isSpaceDock", False),
                    "commodities": sec["port"]["commodities"],
                    "black_market": sec["port"].get("black_market", False),
                }
            )
        for planet in sec.get("planets") or []:
            citadel = planet.get("citadel") or {}
            planets.append(
                {
                    "sector_id": sid,
                    "name": planet["name"],
                    "type": planet["type"],
                    "owner": planet.get("owner"),
                    "habitability_score": planet["habitabilityScore"],
                    "max_population": planet["maxPopulation"],
                    "max_colonists": planet["maxColonists"],
                    "ore": planet.get("ore", 0),
                    "organics": planet.get("organics", 0),
                    "equipment": planet.get("equipment", 0),
                    "colonists": planet.get("colonists", 0),
                    "citadel_level": citadel.get("level"),
                    "citadel_drone_capacity": citadel.get("droneCapacity"),
                    "citadel_safe_contents": citadel.get("safeContents"),
                    "citadel_drone_inventory": citadel.get("droneInventory"),
                }
            )
        if sec.get("nebula"):
            nebulae.append(
                {
                    "sector_id": sid,
                    "type": sec["nebula"]["type"],
                    "density": sec["nebula"]["density"],
                }
            )
    warps = [
        {
            "from_sector": w["from"],
            "to_sector": w["to"],
            "one_way": w.get("oneWay", False),
        }
        for w in fixture["warps"]
    ]
    clusters = [
        {
            "cluster_number": c["id"],
            "name": c["name"],
            "type": c["type"],
            "sector_range_start": c["sectorRangeStart"],
            "sector_range_end": c["sectorRangeEnd"],
            "coords_x": c["coords"]["x"],
            "coords_y": c["coords"]["y"],
            "coords_z": c["coords"]["z"],
            "warp_stability": c["warpStability"],
            "economic_value": c["economicValue"],
            "is_discovered": c.get("isDiscovered", True),
            "is_hidden": c.get("isHidden", False),
        }
        for c in fixture["clusters"]
    ]
    raw = universe_from_bang_rows(
        {
            "version": fixture["version"],
            "seed": fixture["seed"],
            "total_sectors": fixture["totalSectors"],
            "config": fixture["config"],
        },
        sectors=sectors,
        warps=warps,
        ports=ports,
        planets=planets,
        nebulae=nebulae,
        clusters=clusters,
        special_locations=[
            {"type": sl["type"], "sector_number": sl["sectorId"]}
            for sl in fixture.get("specialLocations") or []
        ],
        special_formations=[
            {
                "formation_number": f["id"],
                "type": f["type"],
                "name": f["name"],
                "anchor_sector_number": f["anchorSectorId"],
                "interior_sector_numbers": f.get("interiorSectorIds") or [],
                "properties": f.get("properties") or {},
                "cluster_number": f["clusterId"],
                "endpoint_cluster_number": f.get("endpointClusterId"),
                "is_discovered": f.get("isDiscovered", False),
                "is_hidden": f.get("isHidden", False),
            }
            for f in fixture.get("specialFormations") or []
        ],
        npc_rosters=[
            {
                "roster_number": r["id"],
                "kind": r["kind"],
                "faction_code": r["factionCode"],
                "target_count": r["targetCount"],
                "host_sector_number": r["hostSectorId"],
                "name_pool": r["namePool"],
                "default_lodging_id": r.get("defaultLodgingId"),
            }
            for r in fixture.get("npcRosters") or []
        ],
    )
    assert raw["totalSectors"] == fixture["totalSectors"]
    assert len(raw["sectors"]) == len(fixture["sectors"])
    assert len(raw["warps"]) == len(fixture["warps"])
    assert len(raw["clusters"]) == len(fixture["clusters"])
    parsed = ParsedUniverse(region_type="terran_space", raw=raw)
    plan = BangImportService(
        bang_image="test",
        docker_client=MagicMock(name="path_b_noop_docker"),
    ).translate(
        {"terran_space": parsed},
        {"galaxy_name": "Path B Test", "master_seed": parsed.seed},
    )
    assert "terran_space" in plan.regions
    assert len(plan.regions["terran_space"].sectors) == fixture["totalSectors"]


def test_idempotent_same_seed_config():
    config = {"sectors": 300, "seed": 42}
    snap = {
        "regions": {
            "terran_space": {"universe": {"seed": 42, "config": config}},
        }
    }
    assert region_already_imported(snap, "terran_space", seed=42, config=config) is True
    assert region_already_imported(snap, "terran_space", seed=42, config={"sectors": 1}) is False
    assert region_already_imported(snap, "central_nexus", seed=42, config=config) is False


def test_cli_missing_args_exits_nonzero():
    from scripts.bootstrap_from_bang import parse_args

    with pytest.raises(SystemExit):
        parse_args([])


def test_cli_force_reimport_hint_constant():
    from scripts.bootstrap_from_bang import FORCE_REIMPORT_HINT

    assert "--force-reimport" in FORCE_REIMPORT_HINT


def test_config_fingerprint_stable():
    assert config_fingerprint({"b": 1, "a": 2}) == config_fingerprint({"a": 2, "b": 1})


def test_stamp_region_snapshot_merges_universe_and_keeps_additional():
    snap = stamp_region_snapshot(
        {"additional_regions": [{"region_type": "player_owned"}]},
        "central_nexus",
        region_id="rid-1",
        universe={"seed": 9, "config": {"sectors": 5000}},
    )
    assert snap["additional_regions"][0]["region_type"] == "player_owned"
    assert snap["regions"]["central_nexus"]["region_id"] == "rid-1"
    assert snap["regions"]["central_nexus"]["universe"]["seed"] == 9
    assert region_already_imported(
        snap, "central_nexus", seed=9, config={"sectors": 5000}
    )


def test_cli_terran_first_for_new_galaxy():
    from scripts.bootstrap_from_bang import require_terran_first_for_new_galaxy

    require_terran_first_for_new_galaxy(galaxy_exists=False, region_type="terran_space")
    require_terran_first_for_new_galaxy(galaxy_exists=True, region_type="central_nexus")
    with pytest.raises(SystemExit) as exc:
        require_terran_first_for_new_galaxy(
            galaxy_exists=False, region_type="central_nexus"
        )
    assert "terran_space first" in str(exc.value)


def test_should_attach_spokes_only_when_adding_nexus():
    from scripts.bootstrap_from_bang import should_attach_spokes_after_additional

    assert should_attach_spokes_after_additional("central_nexus") is True
    assert should_attach_spokes_after_additional("terran_space") is False
    assert should_attach_spokes_after_additional("player_owned") is False
