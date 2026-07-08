"""Shared canon nebula-color derivation (WO-SB-QH2 / WO-GWQ-NEXUS-NEBULA-FIELDS).

Single home for the six-color canon taxonomy that
``quantum_service._HARVEST_YIELD_BANDS`` keys harvest yield on
(sw2102-docs quantum-resources.md § "Nebula types and field strengths").
Every writer of ``Cluster.nebula_type`` / ``Cluster.quantum_field_strength``
/ ``Cluster.color_hex`` derives its color from a 0-100 field-strength value
through :func:`derive_nebula_color` and looks up the matching hex in
:data:`NEBULA_COLOR_HEX`, so a cluster is never left carrying a value the
harvest band table doesn't recognise:

* ``bang_import_service._finalize_cluster_nebula_fields`` — mean of bang's
  per-sector nebula density samples (bang emits ``{type, density}`` per
  sector; there is no cluster-level density in the payload).
* ``nexus_generation_service._synthesize_cluster_nebula_fields`` — nexus's
  synthetic sectors carry only a bare ``SectorType.NEBULA`` flag, no
  density sample to average, so this rolls a NO-CANON uniform value in the
  same domain instead.

Both callers get the identical color/hex mapping from ONE definition site.
"""
from __future__ import annotations

from typing import Dict

#: Canonical nebula-color -> galaxy-map render hex, from sw2102-docs
#: quantum-resources.md § "Nebula types and field strengths". These hexes ARE
#: canon (quantum-resources.md:44); only the density boundary cutpoints below
#: are a builder proposal.
NEBULA_COLOR_HEX: Dict[str, str] = {
    "crimson": "#DC143C",
    "azure": "#1E90FF",
    "emerald": "#00FF7F",
    "violet": "#9370DB",
    "amber": "#FF8C00",
    "obsidian": "#2F4F4F",
}

#: WO-SB-QH2 [NO-CANON, flagged to DECISIONS]: canon's per-color field-strength
#: ranges OVERLAP (e.g. azure 60-80 vs emerald 50-70 — quantum-resources.md
#: nebula table), so no single mean-density value maps unambiguously under
#: canon as written. These are disjoint cutpoints a builder proposes so
#: harvest can key on exactly one color per cluster: >=80 crimson, 60-79
#: azure, 50-59 emerald, 40-49 violet, 20-39 amber, <20 obsidian.
NEBULA_COLOR_BOUNDARY_CRIMSON: int = 80
NEBULA_COLOR_BOUNDARY_AZURE: int = 60
NEBULA_COLOR_BOUNDARY_EMERALD: int = 50
NEBULA_COLOR_BOUNDARY_VIOLET: int = 40
NEBULA_COLOR_BOUNDARY_AMBER: int = 20


def derive_nebula_color(mean_density: float) -> str:
    """Map a 0-100 field-strength/density value to a canon color key.

    bang emits per-sector nebula as ``{type: 'normal'|'magnetic', density:
    1-100}`` (content.ts:404-408) with no color concept of its own; nexus
    generation has no per-sector density at all. Both derive the six-color
    canon taxonomy through this one function so a cluster can actually be
    harvested instead of rejected as 'uncharted'. See the [NO-CANON]
    boundary comment above.
    """
    if mean_density >= NEBULA_COLOR_BOUNDARY_CRIMSON:
        return "crimson"
    if mean_density >= NEBULA_COLOR_BOUNDARY_AZURE:
        return "azure"
    if mean_density >= NEBULA_COLOR_BOUNDARY_EMERALD:
        return "emerald"
    if mean_density >= NEBULA_COLOR_BOUNDARY_VIOLET:
        return "violet"
    if mean_density >= NEBULA_COLOR_BOUNDARY_AMBER:
        return "amber"
    return "obsidian"
