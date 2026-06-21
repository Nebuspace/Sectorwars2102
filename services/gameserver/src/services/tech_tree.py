"""Tech tree — the static research node catalog (CRT WO-K0-1).

The flat, RP-only **kernel** of the unified Citadel ⋈ Research ⋈ Terraform tree
(CRT-MASTER §1.2 / §K0). Mirrors the shape of ``CITADEL_LEVELS`` /
``DEFENSE_BUILDINGS`` in ``citadel_service.py``: a module-level static dict that
the rest of the game *queries* and never mutates.

Design discipline (the keystone, CRT-MASTER §1.2):
    Effects are read **at point-of-use** — ``research_service.player_has_tech()``
    / ``tech_modifier()`` / ``gate_value()`` / ``has_tool()`` query this catalog
    plus a player's ledger; they are **never written onto** the entities they
    buff. Research is a *leaf* in the call graph: it is read, never invoked. The
    tree therefore mutates nothing → zero migration risk to every buffed system,
    and the tree grows by **appending catalog rows** (bump ``CATALOG_VERSION``),
    never by touching planet/ship/combat columns.

K0 kernel scope (this file):
    * ONE free root node ``t.root.0`` (cost 0 RP) — every node is reachable from
      it; a null ledger lazy-seeds ``unlocked=[t.root.0]`` (handled in
      ``research_service``, not here, to keep this module pure/static).
    * Two **content-unlock** nodes that cash the two Design-only DEFENSE_BUILDINGS
      (RAIL_GUN, PLANETARY_DEFENSE_GRID) into reality —
      ``effect.kind == "content_unlock"``, read by the K0-3 placement gate.
    * A few **inert placeholder** nodes (grid / hazard / intensity tools) —
      defined-but-unwired, reserved for K1b's grid gating. They carry their
      eventual ``effect`` shape so K1b wires consumers without a catalog reshape.

Node schema (CRT-MASTER §1.2 — kernel subset; richer keys appended later):
    {
      "id":       str,          # stable dotted id, unique
      "branch":   str,          # one of BRANCHES
      "tier":     int,          # 0 = root
      "name":     str,          # display
      "cost":     {"rp": int},  # kernel = flat RP only (insight/doctrine later)
      "prereqs":  [node_id],    # ids that must be unlocked first (DAG edges)
      "effect":   {"kind": str, ...},  # see EffectKind; read at point-of-use
    }

Nothing here imports a model or a session — it is a pure static catalog so it can
be imported by tests, CI, and the service layer without DB side effects.
"""

from __future__ import annotations

from typing import Any, Dict, List

# Bump when the catalog shape/contents change. The ledger is forward-compatible:
# it stores unlocked node *ids*, so appending rows never invalidates a ledger.
CATALOG_VERSION = 1

# The free Tier-0 root every node descends from (CRT-MASTER §1.2). A null ledger
# lazy-seeds to ``unlocked=[FREE_ROOT_ID]`` in research_service.
FREE_ROOT_ID = "t.root.0"

# Five branches share the Tier-0 "Applied Science" root (CRT-MASTER §1.2). The
# kernel only populates a subset; the constant pins the canonical vocabulary so
# later rows validate against it.
BRANCHES = ("production", "defense", "ships", "exploration", "terraforming")

# Effect kinds the kernel ships (point-of-use readers in research_service):
#   content_unlock — makes a catalog entry (e.g. a DEFENSE_BUILDING) placeable.
#   tool           — unlocks a capability flag (has_tool); inert in K0.
#   gate           — raises a stage/intensity ceiling (gate_value); inert in K0.
#   modifier       — bends a numeric curve at point-of-use (tech_modifier); inert in K0.
#   root           — the free origin; no effect.
EFFECT_KINDS = ("root", "content_unlock", "tool", "gate", "modifier")


# The flat kernel catalog. Order is irrelevant (lookups are by id); kept grouped
# by branch for readability. ~8–12 nodes (CRT-MASTER §K0).
TECH_NODES: Dict[str, Dict[str, Any]] = {
    # --- Tier-0 free root: the origin of every edge ---------------------------
    FREE_ROOT_ID: {
        "id": FREE_ROOT_ID,
        "branch": "production",          # root lives in production by convention
        "tier": 0,
        "name": "Applied Science",
        "cost": {"rp": 0},               # FREE
        "prereqs": [],                   # the only node with no prereq
        "effect": {"kind": "root"},
    },

    # --- DEFENSE branch: the two content-unlocks K0 cashes into reality --------
    # These two are the live payload of K0: unlocking one makes the matching
    # DEFENSE_BUILDINGS entry placeable through the EXISTING build flow (K0-3).
    "t.defense.railgun.1": {
        "id": "t.defense.railgun.1",
        "branch": "defense",
        "tier": 1,
        "name": "Rail Gun Emplacements",
        "cost": {"rp": 50},
        "prereqs": [FREE_ROOT_ID],
        # The placement gate (K0-3) reads effect.key against the building_type.
        "effect": {"kind": "content_unlock", "key": "rail_gun"},
    },
    "t.defense.grid.1": {
        "id": "t.defense.grid.1",
        "branch": "defense",
        "tier": 2,
        "name": "Planetary Defense Grid",
        "cost": {"rp": 120},
        # Chains off the rail-gun node — a real Tier-2 edge, still reachable.
        "prereqs": ["t.defense.railgun.1"],
        # content key renamed defense_grid -> planetary_defense_grid (blessed
        # rename) to avoid collision with the unrelated Station.defense_grid bool.
        "effect": {"kind": "content_unlock", "key": "planetary_defense_grid"},
    },

    # --- Inert PLACEHOLDER nodes (defined-but-unwired; reserved for K1b) -------
    # They carry their eventual effect shape so K1b wires consumers (grid clear,
    # hazard tools, terraform intensity) without reshaping the catalog. In K0 the
    # readers (has_tool / gate_value / tech_modifier) recognise them but no game
    # system consults those readers yet — genuinely inert.
    "t.exploration.survey.1": {
        "id": "t.exploration.survey.1",
        "branch": "exploration",
        "tier": 1,
        "name": "Orbital Survey Suite",
        "cost": {"rp": 30},
        "prereqs": [FREE_ROOT_ID],
        # Reserved for K1b grid fog/reveal — a capability flag (has_tool).
        "effect": {"kind": "tool", "key": "grid_survey"},
    },
    "t.terraforming.hazard_clear.1": {
        "id": "t.terraforming.hazard_clear.1",
        "branch": "terraforming",
        "tier": 1,
        "name": "Hazard Remediation",
        "cost": {"rp": 60},
        "prereqs": [FREE_ROOT_ID],
        # Reserved for K1b: tool-gates clearing radiation/hazard plots.
        "effect": {"kind": "tool", "key": "hazard_clear"},
    },
    "t.terraforming.plot_clear.1": {
        "id": "t.terraforming.plot_clear.1",
        "branch": "terraforming",
        "tier": 1,
        "name": "Land Clearance",
        "cost": {"rp": 40},
        "prereqs": [FREE_ROOT_ID],
        # Reserved for K1b: tool-gates clearing uncleared land plots.
        "effect": {"kind": "tool", "key": "plot_clear"},
    },
    "t.terraforming.intensity.1": {
        "id": "t.terraforming.intensity.1",
        "branch": "terraforming",
        "tier": 2,
        "name": "Aggressive Terraforming",
        "cost": {"rp": 90},
        "prereqs": ["t.terraforming.plot_clear.1"],
        # Reserved for K1b: gates terraform intensity ABOVE Conservative. The
        # gate_value reader returns this magnitude when unlocked, else the floor.
        "effect": {"kind": "gate", "key": "terraform_intensity", "gate": 2},
    },
    "t.production.yield.1": {
        "id": "t.production.yield.1",
        "branch": "production",
        "tier": 1,
        "name": "Process Optimization",
        "cost": {"rp": 45},
        "prereqs": [FREE_ROOT_ID],
        # Reserved for K1b/T1: bends a production curve at point-of-use. Inert in
        # K0 (no consumer calls tech_modifier yet).
        "effect": {"kind": "modifier", "key": "production_rate", "magnitude": 0.05},
    },
    "t.ships.efficiency.1": {
        "id": "t.ships.efficiency.1",
        "branch": "ships",
        "tier": 1,
        "name": "Drive Efficiency",
        "cost": {"rp": 45},
        "prereqs": [FREE_ROOT_ID],
        # Reserved: bends a movement/turn curve at point-of-use. Inert in K0.
        "effect": {"kind": "modifier", "key": "turn_cost", "magnitude": -0.05},
    },
}

# Convenience constants mirroring CRT-MASTER §1.2's named groups.
KERNEL_NODES: List[str] = list(TECH_NODES.keys())
FREE_ROOT_NODES: List[str] = [FREE_ROOT_ID]

# Node ids whose effect.kind == "content_unlock", indexed by the content key they
# unlock (e.g. "rail_gun" -> "t.defense.railgun.1"). The K0-3 placement gate uses
# this to map a building_type back to the node that must be unlocked.
CONTENT_UNLOCK_BY_KEY: Dict[str, str] = {
    node["effect"]["key"]: node_id
    for node_id, node in TECH_NODES.items()
    if node["effect"].get("kind") == "content_unlock"
}


def get_node(node_id: str) -> Dict[str, Any] | None:
    """Return the catalog node for ``node_id``, or None if unknown."""
    return TECH_NODES.get(node_id)


def node_id_for_content(content_key: str) -> str | None:
    """Return the node id that unlocks ``content_key`` content, or None.

    Used by the K0-3 placement gate to find which tech (if any) gates a given
    building_type. Returns None for content not gated by any node.
    """
    return CONTENT_UNLOCK_BY_KEY.get(content_key)


def assert_dag_reachable() -> None:
    """Validate the catalog is a well-formed DAG reachable from the free root.

    Three invariants (CRT-MASTER §K0 acceptance — "CI DAG-reachability passes"):
      1. Every node's ``prereqs`` reference EXISTING node ids.
      2. There are NO cycles (the graph is a DAG).
      3. EVERY node is reachable from ``FREE_ROOT_ID`` by following prereq edges.

    Raises ``AssertionError`` with a precise message on any violation. Safe to
    call from CI / unit tests / startup; pure (no DB, no side effects).
    """
    # Exactly one free root, and it is FREE_ROOT_ID with no prereqs.
    roots = [nid for nid, n in TECH_NODES.items() if not n["prereqs"]]
    assert roots == [FREE_ROOT_ID], (
        f"tech_tree: expected exactly one prereq-less root {FREE_ROOT_ID!r}, "
        f"found roots={roots!r}"
    )
    assert TECH_NODES[FREE_ROOT_ID]["cost"]["rp"] == 0, (
        "tech_tree: free root must cost 0 RP"
    )

    # (1) prereq references must exist; collect edges child<-prereq.
    for nid, node in TECH_NODES.items():
        assert node.get("branch") in BRANCHES, (
            f"tech_tree: node {nid!r} has unknown branch {node.get('branch')!r}"
        )
        assert node.get("effect", {}).get("kind") in EFFECT_KINDS, (
            f"tech_tree: node {nid!r} has unknown effect.kind "
            f"{node.get('effect', {}).get('kind')!r}"
        )
        for pre in node["prereqs"]:
            assert pre in TECH_NODES, (
                f"tech_tree: node {nid!r} lists missing prereq {pre!r}"
            )
            assert pre != nid, f"tech_tree: node {nid!r} lists itself as a prereq"

    # (2) cycle detection via DFS colouring over the prereq edges.
    WHITE, GREY, BLACK = 0, 1, 2
    color: Dict[str, int] = {nid: WHITE for nid in TECH_NODES}

    def _visit(nid: str, stack: List[str]) -> None:
        color[nid] = GREY
        for pre in TECH_NODES[nid]["prereqs"]:
            if color[pre] == GREY:
                cycle = " -> ".join(stack + [nid, pre])
                raise AssertionError(f"tech_tree: cycle detected: {cycle}")
            if color[pre] == WHITE:
                _visit(pre, stack + [nid])
        color[nid] = BLACK

    for nid in TECH_NODES:
        if color[nid] == WHITE:
            _visit(nid, [])

    # (3) reachability from the free root: BFS over the REVERSE edges
    # (root -> dependents). A node is reachable iff all its prereqs are reachable
    # AND the chain bottoms out at the free root. Since (1)+(2) hold, walking each
    # node's prereq chain and asserting it terminates at FREE_ROOT_ID is enough.
    def _reaches_root(nid: str, seen: set) -> bool:
        if nid == FREE_ROOT_ID:
            return True
        if nid in seen:
            return False
        seen.add(nid)
        prereqs = TECH_NODES[nid]["prereqs"]
        if not prereqs:
            return nid == FREE_ROOT_ID
        return all(_reaches_root(p, seen) for p in prereqs)

    for nid in TECH_NODES:
        assert _reaches_root(nid, set()), (
            f"tech_tree: node {nid!r} is not reachable from free root "
            f"{FREE_ROOT_ID!r}"
        )
