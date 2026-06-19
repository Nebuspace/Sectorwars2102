"""
Canonical medal catalog (ADR-0028).

This module is the single source of truth for the *definitions* of every medal
in the game. Per ADR-0028 the catalog lives in code here and is seeded
idempotently into the relational ``medals`` table (see :func:`seed_medals`);
per-player awards live in the ``player_medals`` association table.

Schema mapping — each entry maps onto :class:`src.models.medal.Medal`:

    id          -> Medal.id          (stable string PK, namespaced, e.g. "combat.bronze_star")
    name        -> Medal.name
    description -> Medal.description
    category    -> Medal.category    (Combat / Economic / Exploration / Diplomatic / Special)
    tier        -> Medal.tier        (bronze / silver / gold / unique ...)
    criteria    -> Medal.criteria    (JSONB; the structured award-trigger condition)

The ``criteria`` JSONB preserves the original trigger shape plus the legacy
``icon`` key so the existing JSONB-era readers keep functioning during the
relational migration:

    {"type": <trigger_type>, "threshold": <int>, "icon": <legacy_icon_key>}

These ids are STABLE. Renaming an id orphans every existing award row, so the
legacy short keys (e.g. ``bronze_star``) are preserved in ``criteria.legacy_key``
to keep the JSONB-era data reconcilable by the backfill CLI.
"""

from typing import Dict, Any, List

# ---------------------------------------------------------------------------
# Tier vocabulary (free-form String in the model; we keep it consistent here).
# ---------------------------------------------------------------------------
TIER_BRONZE = "bronze"
TIER_SILVER = "silver"
TIER_GOLD = "gold"
TIER_UNIQUE = "unique"

# Category vocabulary (matches the legacy MEDAL_DEFINITIONS categories).
CAT_COMBAT = "Combat"
CAT_ECONOMIC = "Economic"
CAT_EXPLORATION = "Exploration"
CAT_DIPLOMATIC = "Diplomatic"
CAT_SPECIAL = "Special"


def _medal(
    medal_id: str,
    name: str,
    category: str,
    tier: str,
    description: str,
    trigger_type: str,
    threshold: int,
    icon: str,
    legacy_key: str,
) -> Dict[str, Any]:
    """Construct a catalog entry whose shape mirrors the ``medals`` columns."""
    return {
        "id": medal_id,
        "name": name,
        "category": category,
        "tier": tier,
        "description": description,
        "criteria": {
            "type": trigger_type,
            "threshold": threshold,
            "icon": icon,
            "legacy_key": legacy_key,
        },
    }


# ---------------------------------------------------------------------------
# THE CATALOG — keyed by the stable string medal_id.
# Ported 1:1 from the legacy MEDAL_DEFINITIONS (13 medals).
# ---------------------------------------------------------------------------
MEDAL_CATALOG: Dict[str, Dict[str, Any]] = {
    # ── Combat ──────────────────────────────────────────────────────
    "combat.first_blood": _medal(
        "combat.first_blood", "First Blood", CAT_COMBAT, TIER_BRONZE,
        "Awarded for your first combat victory",
        "combat_victories", 1, "blood_first", "first_blood",
    ),
    "combat.bronze_star": _medal(
        "combat.bronze_star", "Bronze Star", CAT_COMBAT, TIER_BRONZE,
        "Awarded for 100 combat victories",
        "combat_victories", 100, "star_bronze", "bronze_star",
    ),
    "combat.silver_star": _medal(
        "combat.silver_star", "Silver Star", CAT_COMBAT, TIER_SILVER,
        "Awarded for 1000 combat victories",
        "combat_victories", 1000, "star_silver", "silver_star",
    ),
    "combat.quantum_cross": _medal(
        "combat.quantum_cross", "Quantum Cross", CAT_COMBAT, TIER_GOLD,
        "Awarded for defeating a player 5+ ranks above you",
        "rank_upset", 5, "cross_quantum", "quantum_cross",
    ),
    "combat.fleet_commander": _medal(
        "combat.fleet_commander", "Fleet Commander", CAT_COMBAT, TIER_SILVER,
        "Awarded for commanding a fleet of 5+ ships",
        "ships_owned", 5, "commander_fleet", "fleet_commander",
    ),
    # ── Economic ────────────────────────────────────────────────────
    "economic.traders_merit": _medal(
        "economic.traders_merit", "Trader's Merit", CAT_ECONOMIC, TIER_BRONZE,
        "Awarded for completing 500 trades",
        "total_trades", 500, "medal_trade", "traders_merit",
    ),
    "economic.merchant_prince": _medal(
        "economic.merchant_prince", "Merchant Prince", CAT_ECONOMIC, TIER_GOLD,
        "Awarded for accumulating 10,000,000 credits lifetime",
        "lifetime_credits", 10000000, "crown_merchant", "merchant_prince",
    ),
    # ── Exploration ─────────────────────────────────────────────────
    "exploration.explorers_badge": _medal(
        "exploration.explorers_badge", "Explorer's Badge", CAT_EXPLORATION, TIER_SILVER,
        "Awarded for visiting 500 unique sectors",
        "sectors_visited", 500, "badge_explorer", "explorers_badge",
    ),
    "exploration.colonizer": _medal(
        "exploration.colonizer", "Colonizer", CAT_EXPLORATION, TIER_BRONZE,
        "Awarded for colonizing your first planet",
        "planets_colonized", 1, "flag_colony", "colonizer",
    ),
    "exploration.genesis_award": _medal(
        "exploration.genesis_award", "Genesis Award", CAT_EXPLORATION, TIER_GOLD,
        "Awarded for creating 25 planets with genesis devices",
        "planets_created", 25, "award_genesis", "genesis_award",
    ),
    # ── Diplomatic ──────────────────────────────────────────────────
    "diplomatic.ambassadors_star": _medal(
        "diplomatic.ambassadors_star", "Ambassador's Star", CAT_DIPLOMATIC, TIER_GOLD,
        "Awarded for reaching HONORED reputation with 10 factions",
        "faction_honored", 10, "star_ambassador", "ambassadors_star",
    ),
    # ── Special ─────────────────────────────────────────────────────
    "special.arias_favor": _medal(
        "special.arias_favor", "ARIA's Favor", CAT_SPECIAL, TIER_UNIQUE,
        "Awarded for reaching maximum consciousness level with ARIA",
        "aria_consciousness", 5, "favor_aria", "arias_favor",
    ),
    "special.orange_cat_society": _medal(
        "special.orange_cat_society", "Orange Cat Society", CAT_SPECIAL, TIER_UNIQUE,
        "Awarded for discovering the hidden Orange Cat sector",
        "special_discovery", 1, "cat_orange", "orange_cat_society",
    ),
}


# Reverse map: legacy short key -> stable namespaced id. Used by the
# read-compat path so callers (and old JSONB rows) referencing e.g.
# ``bronze_star`` resolve to ``combat.bronze_star``.
LEGACY_KEY_TO_ID: Dict[str, str] = {
    entry["criteria"]["legacy_key"]: medal_id
    for medal_id, entry in MEDAL_CATALOG.items()
}


def get_catalog_entry(medal_id_or_legacy: str) -> Dict[str, Any]:
    """Resolve a catalog entry by stable id OR legacy short key. Returns {} if unknown."""
    if medal_id_or_legacy in MEDAL_CATALOG:
        return MEDAL_CATALOG[medal_id_or_legacy]
    resolved = LEGACY_KEY_TO_ID.get(medal_id_or_legacy)
    if resolved:
        return MEDAL_CATALOG[resolved]
    return {}


def medals_for_trigger(trigger_type: str) -> List[Dict[str, Any]]:
    """All catalog entries whose criteria trigger on the given trigger_type."""
    return [
        entry
        for entry in MEDAL_CATALOG.values()
        if entry["criteria"].get("type") == trigger_type
    ]


# ---------------------------------------------------------------------------
# Idempotent seed — upsert the catalog into the ``medals`` table.
# Mirrors src/core/ship_specifications_seeder.py:seed_ship_specifications:
# update existing rows in place, insert missing ones, safe on every re-run.
# ---------------------------------------------------------------------------
def seed_medals(db) -> int:
    """Idempotently upsert :data:`MEDAL_CATALOG` into the ``medals`` table.

    Returns the number of catalog entries processed (created + updated).
    Safe to call on every startup; never deletes rows (ON DELETE RESTRICT on
    PlayerMedal.medal_id would block deletion of held medals anyway).
    """
    import logging
    from src.models.medal import Medal

    logger = logging.getLogger(__name__)

    processed = 0
    for medal_id, entry in MEDAL_CATALOG.items():
        existing = db.query(Medal).filter(Medal.id == medal_id).first()
        if existing is None:
            db.add(
                Medal(
                    id=entry["id"],
                    name=entry["name"],
                    description=entry["description"],
                    category=entry["category"],
                    tier=entry["tier"],
                    criteria=entry["criteria"],
                )
            )
        else:
            # Update mutable metadata in place (text rewrites, balance tweaks).
            existing.name = entry["name"]
            existing.description = entry["description"]
            existing.category = entry["category"]
            existing.tier = entry["tier"]
            existing.criteria = entry["criteria"]
        processed += 1

    db.commit()
    logger.info("Seeded %d medals into the catalog", processed)
    return processed
