"""building_catalog.py — the static CRT grid building catalog (WO-K1b-1).

The citadel analogue of ``tech_tree.py`` / the shipped ``DEFENSE_BUILDINGS`` & ``CITADEL_LEVELS``
dict-of-specs pattern: ONE static, code-not-DB catalog of every placeable building KIND, keyed by
kind. It grows by appending rows. Nothing here writes state — ``structures.place()`` reads this
catalog to validate + stamp a placement; ``derive_citadel_level()`` reads it for footprint/area.

ROW SHAPE (per CRT-MASTER §3 / 03-spec-citadel §2):
    kind          str   — the catalog key (also stored on the placed building)
    domain        str   — "economy" | "defense" | "civic" | "monument"
    name          str   — display name
    footprint     [w,h] — plot cells on the shared grid (multi-cell must be contiguous+cleared)
    max_level     int   — upgrade ceiling (defense kinds are count-based: max_level 1)
    build_hours   {lvl:hours}      — per-level timer → complete_at
    cost          {lvl:{credits, <material>:n}}  — credits (treasury) + per-planet materials
    power_draw    {lvl:int}        — + draws / − supplies (POWER_PLANT supplies)
    crew          {lvl:int}        — head-count (profession-typed in T2; flat here)
    upkeep        {credits, materials:{...}}   — /day drain (the loop-maker; citadel disrepair K1b-5)
    tech_gate     str|None         — research node required to BUILD (point-of-use read at enqueue)
    terrain_bonus {TERRAIN:mult}   — multiplier applied at effect-read time (never baked)
    effect        {kind, ...}      — what the building does (read at point-of-use)
    signature     bool             — one landmark per domain re-skins the viewscreen (T2 cosmetic)
    prereqs       [KIND,...]       — other kinds that must already exist on the planet

⚠️ ALL NUMBERS ARE [NO-CANON] — conservative kernel placeholders, anchored to the shipped
``DEFENSE_BUILDINGS`` (defense kinds carry their shipped cost/build_hours/min-citadel verbatim) and
the genesis/citadel scale elsewhere. The full magnitude table is proposed on STATUS for the
Orchestrator's bless (K0-style); tune via these constants, no structural change.

Kernel set (this WO): economy MINE/FARM/FABRICATOR/POWER_PLANT/STORAGE_SILO/SPACEPORT · civic
HAB_DOME/LAB/LOGISTICS_OFFICE · defense TURRET_NETWORK/ORBITAL_PLATFORM/SCANNER_ARRAY (the 3 shipped)
+ RAIL_GUN/DEFENSE_GRID (the K0-cashed design-only pair). REFINERY/BIO_PROCESSOR/Districts/fortress
tiers/monument Wonders are T2+.
"""

from typing import Any, Dict, List, Optional

DOMAINS = ("economy", "defense", "civic", "monument", "terraform")


# --- The catalog. kind → spec. All numbers NO-CANON (conservative kernel). -----------------------
BUILDING_CATALOG: Dict[str, Dict[str, Any]] = {
    # ===== ECONOMY — the production floor; seeded from factory/farm/mine_level =====
    "MINE": {
        "kind": "MINE", "domain": "economy", "name": "Mine",
        "footprint": [1, 1], "max_level": 5,
        "build_hours": {1: 24, 2: 36, 3: 54, 4: 81, 5: 120},
        "cost": {1: {"credits": 30000, "fuel_ore": 100}, 2: {"credits": 60000, "fuel_ore": 200},
                 3: {"credits": 120000, "fuel_ore": 400}, 4: {"credits": 240000, "fuel_ore": 800},
                 5: {"credits": 480000, "fuel_ore": 1600}},
        "power_draw": {1: 20, 2: 28, 3: 38, 4: 50, 5: 64}, "crew": {1: 4, 2: 6, 3: 9, 4: 13, 5: 18},
        "upkeep": {"credits": 200, "materials": {}}, "tech_gate": None,
        "terrain_bonus": {"ORE_SEAM": 0.25, "VOLCANIC_VENT": 0.10},
        "effect": {"kind": "production_mult", "resource": "fuel_ore"},
        "signature": False, "prereqs": [],
    },
    "FARM": {
        "kind": "FARM", "domain": "economy", "name": "Farm",
        "footprint": [1, 1], "max_level": 5,
        "build_hours": {1: 24, 2: 36, 3: 54, 4: 81, 5: 120},
        "cost": {1: {"credits": 30000}, 2: {"credits": 60000}, 3: {"credits": 120000},
                 4: {"credits": 240000}, 5: {"credits": 480000}},
        "power_draw": {1: 15, 2: 22, 3: 30, 4: 40, 5: 52}, "crew": {1: 4, 2: 6, 3: 9, 4: 13, 5: 18},
        "upkeep": {"credits": 200, "materials": {}}, "tech_gate": None,
        "terrain_bonus": {"FERTILE": 0.25, "COASTAL": 0.25},
        "effect": {"kind": "production_mult", "resource": "organics"},
        "signature": False, "prereqs": [],
    },
    "FABRICATOR": {
        "kind": "FABRICATOR", "domain": "economy", "name": "Fabricator",
        "footprint": [1, 1], "max_level": 5,
        "build_hours": {1: 24, 2: 36, 3: 54, 4: 81, 5: 120},
        "cost": {1: {"credits": 40000}, 2: {"credits": 80000}, 3: {"credits": 160000},
                 4: {"credits": 320000}, 5: {"credits": 640000}},
        "power_draw": {1: 30, 2: 42, 3: 56, 4: 74, 5: 96}, "crew": {1: 5, 2: 7, 3: 10, 4: 14, 5: 19},
        "upkeep": {"credits": 300, "materials": {}}, "tech_gate": None,
        "terrain_bonus": {}, "effect": {"kind": "production_mult", "resource": "equipment"},
        "signature": False, "prereqs": [],
    },
    "POWER_PLANT": {
        "kind": "POWER_PLANT", "domain": "economy", "name": "Power Plant",
        "footprint": [1, 1], "max_level": 5,
        "build_hours": {1: 36, 2: 54, 3: 81, 4: 120, 5: 168},
        "cost": {1: {"credits": 60000}, 2: {"credits": 120000}, 3: {"credits": 240000},
                 4: {"credits": 480000}, 5: {"credits": 960000}},
        # negative power_draw == net SUPPLY to the grid.
        "power_draw": {1: -80, 2: -130, 3: -200, 4: -300, 5: -440}, "crew": {1: 3, 2: 5, 3: 7, 4: 10, 5: 14},
        "upkeep": {"credits": 400, "materials": {}}, "tech_gate": None,
        "terrain_bonus": {"HIGHLAND": 0.30, "VOLCANIC_VENT": 0.30},
        "effect": {"kind": "power_supply"}, "signature": False, "prereqs": [],
    },
    "STORAGE_SILO": {
        "kind": "STORAGE_SILO", "domain": "economy", "name": "Storage Silo",
        "footprint": [1, 1], "max_level": 3,
        "build_hours": {1: 24, 2: 36, 3: 54},
        "cost": {1: {"credits": 25000}, 2: {"credits": 50000}, 3: {"credits": 100000}},
        "power_draw": {1: 5, 2: 8, 3: 12}, "crew": {1: 2, 2: 3, 3: 4},
        "upkeep": {"credits": 100, "materials": {}}, "tech_gate": None,
        "terrain_bonus": {}, "effect": {"kind": "storage_cap"}, "signature": False, "prereqs": [],
    },
    "SPACEPORT": {
        "kind": "SPACEPORT", "domain": "economy", "name": "Spaceport",
        "footprint": [2, 1], "max_level": 3,
        "build_hours": {1: 96, 2: 144, 3: 216},
        "cost": {1: {"credits": 200000, "equipment": 100}, 2: {"credits": 400000, "equipment": 200},
                 3: {"credits": 800000, "equipment": 400}},
        "power_draw": {1: 40, 2: 56, 3: 76}, "crew": {1: 8, 2: 12, 3: 18},
        "upkeep": {"credits": 600, "materials": {}}, "tech_gate": "t.prod.2",
        "terrain_bonus": {"FLAT": 0.10, "COASTAL": 0.10},
        "effect": {"kind": "export_throughput"}, "signature": True, "prereqs": [],
    },

    # ===== CIVIC — housing / research / habitability; the L-gate buildings =====
    "HAB_DOME": {
        "kind": "HAB_DOME", "domain": "civic", "name": "Habitation Dome",
        "footprint": [1, 1], "max_level": 5,
        "build_hours": {1: 36, 2: 54, 3: 81, 4: 120, 5: 168},
        "cost": {1: {"credits": 50000}, 2: {"credits": 100000}, 3: {"credits": 200000},
                 4: {"credits": 400000}, 5: {"credits": 800000}},
        "power_draw": {1: 25, 2: 35, 3: 48, 4: 64, 5: 84}, "crew": {1: 2, 2: 3, 3: 4, 4: 6, 5: 8},
        "upkeep": {"credits": 300, "materials": {}}, "tech_gate": None,
        "terrain_bonus": {},
        # population_housed input to derive_citadel_level; also lifts its plot's axis (T2).
        "effect": {"kind": "housing", "capacity_per_level": 50000},
        "signature": False, "prereqs": [],
    },
    "LAB": {
        "kind": "LAB", "domain": "civic", "name": "Research Lab",
        "footprint": [1, 1], "max_level": 5,
        "build_hours": {1: 48, 2: 72, 3: 108, 4: 162, 5: 240},
        "cost": {1: {"credits": 60000}, 2: {"credits": 120000}, 3: {"credits": 240000},
                 4: {"credits": 480000}, 5: {"credits": 960000}},
        "power_draw": {1: 30, 2: 42, 3: 56, 4: 74, 5: 96}, "crew": {1: 6, 2: 9, 3: 13, 4: 18, 5: 24},
        "upkeep": {"credits": 400, "materials": {}}, "tech_gate": None,
        "terrain_bonus": {},
        # mints research_points (= shipped research_level faucet, ADR-0087).
        "effect": {"kind": "research_faucet"}, "signature": False, "prereqs": [],
    },
    "LOGISTICS_OFFICE": {
        "kind": "LOGISTICS_OFFICE", "domain": "civic", "name": "Logistics Office",
        "footprint": [1, 1], "max_level": 2,
        "build_hours": {1: 48, 2: 72},
        "cost": {1: {"credits": 40000}, 2: {"credits": 80000}},
        "power_draw": {1: 10, 2: 16}, "crew": {1: 3, 2: 5},
        "upkeep": {"credits": 150, "materials": {}}, "tech_gate": "t.prod.2",
        "terrain_bonus": {}, "effect": {"kind": "upkeep_reduction"}, "signature": False, "prereqs": [],
    },
    "ADMIN_SPIRE": {
        # The Seat landmark (§6 / Player.house) — the L5 Planetary-Capital key building that
        # derive_citadel_level() gates the top tier on. One per planet (max_level 1, count-style).
        "kind": "ADMIN_SPIRE", "domain": "civic", "name": "Administration Spire",
        "footprint": [2, 2], "max_level": 1,
        "build_hours": {1: 240},
        "cost": {1: {"credits": 2000000, "equipment": 500}},
        "power_draw": {1: 120}, "crew": {1: 20},
        "upkeep": {"credits": 1500, "materials": {}}, "tech_gate": None,
        "terrain_bonus": {}, "effect": {"kind": "seat_landmark"},
        "signature": True, "prereqs": ["SPACEPORT"],
    },

    # ===== DEFENSE — the 3 shipped (cost/build/min-citadel verbatim from DEFENSE_BUILDINGS) +
    # RAIL_GUN/DEFENSE_GRID (cashed via K0). count-based: max_level 1, count tracked by placements.
    "TURRET_NETWORK": {
        "kind": "TURRET_NETWORK", "domain": "defense", "name": "Turret Network",
        "footprint": [1, 1], "max_level": 1,
        "build_hours": {1: 72}, "cost": {1: {"credits": 150000}},
        "power_draw": {1: 15}, "crew": {1: 3},
        "upkeep": {"credits": 300, "materials": {}}, "tech_gate": None,
        "min_citadel_level": 3, "terrain_bonus": {},
        "effect": {"kind": "ct1_defense", "ct1_kind": "turret_network"},
        "signature": False, "prereqs": [],
    },
    "ORBITAL_PLATFORM": {
        "kind": "ORBITAL_PLATFORM", "domain": "defense", "name": "Orbital Platform",
        "footprint": [1, 1], "max_level": 1,
        "build_hours": {1: 168}, "cost": {1: {"credits": 500000}},
        "power_draw": {1: 40}, "crew": {1: 6},
        "upkeep": {"credits": 800, "materials": {}}, "tech_gate": None,
        "min_citadel_level": 4, "terrain_bonus": {},
        "effect": {"kind": "ct1_defense", "ct1_kind": "orbital_platform"},
        "signature": True, "prereqs": [],
    },
    "SCANNER_ARRAY": {
        "kind": "SCANNER_ARRAY", "domain": "defense", "name": "Scanner Array",
        "footprint": [1, 1], "max_level": 1,
        "build_hours": {1: 48}, "cost": {1: {"credits": 75000}},
        "power_draw": {1: 10}, "crew": {1: 2},
        "upkeep": {"credits": 150, "materials": {}}, "tech_gate": None,
        "min_citadel_level": 2, "terrain_bonus": {},
        "effect": {"kind": "ct1_defense", "ct1_kind": "scanner_array"},
        "signature": False, "prereqs": [],
    },
    # The two K0-cashed design-only buildings (magnitudes already Max-blessed in K0).
    "RAIL_GUN": {
        "kind": "RAIL_GUN", "domain": "defense", "name": "Rail Gun Battery",
        "footprint": [1, 1], "max_level": 1,
        "build_hours": {1: 72}, "cost": {1: {"credits": 150000}},
        "power_draw": {1: 40}, "crew": {1: 4},
        "upkeep": {"credits": 400, "materials": {}}, "tech_gate": "t.def.railgun.1",
        "min_citadel_level": 4, "terrain_bonus": {},
        "effect": {"kind": "ct1_defense", "ct1_kind": "rail_gun"},
        "signature": False, "prereqs": [],
    },
    "DEFENSE_GRID": {
        "kind": "DEFENSE_GRID", "domain": "defense", "name": "Planetary Defense Grid",
        "footprint": [2, 1], "max_level": 1,
        "build_hours": {1: 96}, "cost": {1: {"credits": 200000}},
        "power_draw": {1: 60}, "crew": {1: 5},
        "upkeep": {"credits": 500, "materials": {}}, "tech_gate": "t.def.grid.1",
        "min_citadel_level": 3, "terrain_bonus": {},
        "effect": {"kind": "ct1_defense", "ct1_kind": "planetary_defense_grid"},
        "signature": False, "prereqs": [],
    },

    # ===== TERRAFORM (domain:"terraform") — rigs that RE-SHAPE per-plot axes (K1b-2). Placed by the
    # legacy start_terraforming presets; pushed by structures.terraform_grid_tick. NOT counted by
    # derive_citadel_level (it sums only economy/civic floor-area). push_axis/push_base are stamped
    # onto the placed rig by the preset so the field tick reads them.
    "THERMAL_RIG": {
        "kind": "THERMAL_RIG", "domain": "terraform", "name": "Thermal Rig",
        "footprint": [1, 1], "max_level": 5,
        "build_hours": {1: 24, 2: 36, 3: 54, 4: 81, 5: 120},
        "cost": {1: {"credits": 40000, "organics": 200}, 2: {"credits": 80000, "organics": 400},
                 3: {"credits": 160000, "organics": 800}, 4: {"credits": 320000, "organics": 1600},
                 5: {"credits": 640000, "organics": 3200}},
        "power_draw": {1: 30, 2: 42, 3: 56, 4: 74, 5: 96}, "crew": {1: 4, 2: 6, 3: 9, 4: 13, 5: 18},
        "upkeep": {"credits": 300, "materials": {"equipment": 5}}, "tech_gate": None,
        "terrain_bonus": {}, "effect": {"kind": "terra_push", "axis": "thermal"},
        "push_axis": "thermal", "push_base": 2.0, "signature": False, "prereqs": [],
    },
    "HYDRO_PLANT": {
        "kind": "HYDRO_PLANT", "domain": "terraform", "name": "Hydro Plant",
        "footprint": [1, 1], "max_level": 5,
        "build_hours": {1: 24, 2: 36, 3: 54, 4: 81, 5: 120},
        "cost": {1: {"credits": 40000, "organics": 200}, 2: {"credits": 80000, "organics": 400},
                 3: {"credits": 160000, "organics": 800}, 4: {"credits": 320000, "organics": 1600},
                 5: {"credits": 640000, "organics": 3200}},
        "power_draw": {1: 30, 2: 42, 3: 56, 4: 74, 5: 96}, "crew": {1: 4, 2: 6, 3: 9, 4: 13, 5: 18},
        "upkeep": {"credits": 300, "materials": {"equipment": 5}}, "tech_gate": None,
        "terrain_bonus": {}, "effect": {"kind": "terra_push", "axis": "hydro"},
        "push_axis": "hydro", "push_base": 2.0, "signature": False, "prereqs": [],
    },
}


def get(kind: str) -> Optional[Dict[str, Any]]:
    """Return the catalog row for a kind, or None if unknown."""
    return BUILDING_CATALOG.get(kind)


def kinds_in_domain(domain: str) -> List[str]:
    return [k for k, spec in BUILDING_CATALOG.items() if spec["domain"] == domain]


def footprint_cells(kind: str) -> int:
    spec = BUILDING_CATALOG.get(kind)
    if not spec:
        return 0
    w, h = spec["footprint"]
    return int(w) * int(h)


def assert_catalog_valid() -> None:
    """CI/boot guard: every row is well-formed and self-consistent (catalog invariants).

    Mirrors tech_tree.assert_dag_reachable's role — a catalog edit that breaks an invariant fails
    loudly rather than producing a silently un-placeable building."""
    for kind, spec in BUILDING_CATALOG.items():
        assert spec.get("kind") == kind, f"{kind}: row 'kind' must equal its key"
        assert spec.get("domain") in DOMAINS, f"{kind}: domain must be one of {DOMAINS}"
        fp = spec.get("footprint")
        assert isinstance(fp, list) and len(fp) == 2 and fp[0] >= 1 and fp[1] >= 1, \
            f"{kind}: footprint must be [w>=1, h>=1]"
        max_level = spec.get("max_level")
        assert isinstance(max_level, int) and max_level >= 1, f"{kind}: max_level must be >=1"
        for table in ("build_hours", "cost", "power_draw", "crew"):
            t = spec.get(table)
            assert isinstance(t, dict) and all(1 <= lvl <= max_level for lvl in t), \
                f"{kind}: '{table}' must be keyed by levels 1..max_level"
            assert all(lvl in t for lvl in range(1, max_level + 1)), \
                f"{kind}: '{table}' missing a level in 1..{max_level}"
        # cost rows must carry credits
        for lvl, c in spec["cost"].items():
            assert "credits" in c, f"{kind}: cost[{lvl}] must include 'credits'"
        # prereqs must reference real kinds
        for pre in spec.get("prereqs", []):
            assert pre in BUILDING_CATALOG, f"{kind}: prereq '{pre}' is not a catalog kind"


# Fail fast at import if the catalog is malformed (cheap; runs once at module load).
assert_catalog_valid()
