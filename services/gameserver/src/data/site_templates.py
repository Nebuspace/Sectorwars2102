"""Site-shape-template library seed data (ADR-0091 §6 "Floor guarantee via a
validated template library").

Each expedition roll (ADR-0091 §1/§3) draws from this pool at launch — there
is no per-planet pre-stored map; the planet's profile only *weights* which
templates are more likely to be drawn (M12). This is a minimal v1 placeholder
set (2-3 templates per shape class), not the full offline-validated library
the build-time harness (§6) will eventually produce.

Interface kept deliberately simple for the risk-roll service (lane2) to code
against immediately:

    SITE_TEMPLATES: list[dict] — each record:
        shape_class    str   — one of SHAPE_CLASSES
        template_id    str   — stable identifier, unique within shape_class
        slot_count     int   — usable_slots for this template (ADR-0091 §4:
                                clamp(round(mult * B), 6, 32); these are
                                representative placeholder values, not tied
                                to a specific size bucket)
        energy_baseline str  — one of the four v1 common energy sources
                                (ADR-0091 §7 Energy channel B)

All slot_count / energy values are [P] provisional pending the offline
validation harness and WO calibration (ADR-0091 §4/§6).
"""

# Shape-class multipliers on the size envelope B = 4 + 2*size (ADR-0091 §4
# table). [P] provisional, pending calibration.
SHAPE_CLASS_MULTIPLIERS = {
    "COMPACT": 0.85,
    "TERRACED": 1.00,
    "LINEAR": 1.10,
    "IRREGULAR": 1.15,
    "SPRAWLING": 1.30,
    "ENGINEERED": 1.00,
}

SHAPE_CLASSES = tuple(SHAPE_CLASS_MULTIPLIERS.keys())

# Four v1 common energy sources (ADR-0091 §7 Energy channel B).
ENERGY_BASELINES = ("SOLAR", "GEOTHERMAL", "TIDAL", "FUSION_SEED")

# --- Minimal placeholder template pool --------------------------------------
# 2-3 templates per shape class. template_id is unique within its shape_class.
SITE_TEMPLATES: list = [
    # COMPACT — dense near-square; lowest ceiling, most efficient.
    {"shape_class": "COMPACT", "template_id": "compact_01", "slot_count": 7, "energy_baseline": "SOLAR"},
    {"shape_class": "COMPACT", "template_id": "compact_02", "slot_count": 9, "energy_baseline": "GEOTHERMAL"},

    # TERRACED — the current-model baseline shape (anchor); reproduces the
    # existing size->level cap exactly at 1.0x.
    {"shape_class": "TERRACED", "template_id": "terraced_01", "slot_count": 8, "energy_baseline": "SOLAR"},
    {"shape_class": "TERRACED", "template_id": "terraced_02", "slot_count": 12, "energy_baseline": "TIDAL"},
    {"shape_class": "TERRACED", "template_id": "terraced_03", "slot_count": 16, "energy_baseline": "GEOTHERMAL"},

    # LINEAR — 1-2-wide strip; 2x1 max footprint, no 2x2 (structurally caps
    # citadel at L4 regardless of slot count — ADR-0091 §4 C_footprint).
    {"shape_class": "LINEAR", "template_id": "linear_01", "slot_count": 9, "energy_baseline": "TIDAL"},
    {"shape_class": "LINEAR", "template_id": "linear_02", "slot_count": 13, "energy_baseline": "FUSION_SEED"},

    # IRREGULAR — disjoint clusters; clearing cost.
    {"shape_class": "IRREGULAR", "template_id": "irregular_01", "slot_count": 9, "energy_baseline": "GEOTHERMAL"},
    {"shape_class": "IRREGULAR", "template_id": "irregular_02", "slot_count": 14, "energy_baseline": "FUSION_SEED"},

    # SPRAWLING — highest ceiling; carries a live sprawl tax (per-slot upkeep
    # + baseline power scale with footprint).
    {"shape_class": "SPRAWLING", "template_id": "sprawling_01", "slot_count": 15, "energy_baseline": "SOLAR"},
    {"shape_class": "SPRAWLING", "template_id": "sprawling_02", "slot_count": 20, "energy_baseline": "FUSION_SEED"},

    # ENGINEERED — ARTIFICIAL + the onboarding/floor-guarantee fallback shape.
    # A bad roll is bland, never bricked — this shape is always valid.
    {"shape_class": "ENGINEERED", "template_id": "engineered_01", "slot_count": 8, "energy_baseline": "FUSION_SEED"},
    {"shape_class": "ENGINEERED", "template_id": "engineered_02", "slot_count": 12, "energy_baseline": "SOLAR"},
]


def templates_for_shape(shape_class: str) -> list:
    """Return every template record for a given shape class."""
    return [t for t in SITE_TEMPLATES if t["shape_class"] == shape_class]
