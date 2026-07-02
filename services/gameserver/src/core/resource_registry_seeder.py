"""
Resource Registry Seeder (WO-ARCH-RES-1-KERNEL)

Seeds the canonical resource catalog into the `resources` table from the
canon list at sw2102-docs/FEATURES/definitions.md#resource-types. Mirrors the
ship_specifications_seeder.py pattern: a module-level dict keyed by the
model's enum, an idempotent query-then-upsert seed function, called once at
startup (src/main.py).

Vocabulary mapping — ResourceType (models/resource.py) predates this WO and
already used UPPER_CASE names for exactly these 13 canon resources; its own
docstring documents 3 of the mappings (BASIC_FOOD->organics,
TECHNOLOGY->equipment, POPULATION->colonists). The two not already called out
there: PRISMATIC_ORE->prismatic_ore (identity) and PHOTONIC_CRYSTALS-
>lumen_crystals (canon's rare-material name for that enum member). RESOURCE_
REGISTRY below is the single place all 13 mappings are explicit.

Field provenance (do not invent — every value below traces to a source):
  * base_price / price_range (min, max) for the 7 core commodities +
    colonists come from src.core.commodity_economy.COMMODITY_BASE_PRICES,
    the existing single source of truth for commodity economics (WO-Y /
    ADR-0082); those ranges are asserted at import time to match canon's
    published price-range table (definitions.md:193-201).
  * combat_drones has no single canon price — definitions.md:208 gives two
    flat prices (Attack 1,000cr / Defense 1,200cr, at military outposts, not
    the dynamic station-commodity mechanic). Encoded as base_price=1000
    (the lower/baseline of the two) with range (1000, 1200) spanning both —
    not an invented number, the exact two canon figures.
  * quantum_shards, quantum_crystals, prismatic_ore, lumen_crystals: canon
    gives no credit price for any of the four (they're harvested/assembled/
    found, not station-traded) — base_price and range are left None rather
    than fabricated.
  * is_producible=True marks the 8 resources present in the station
    production_rate regen mechanic (bang_import_service._COMMODITY_DEFAULTS /
    nexus_generation_service.base_commodities — the 7 core commodities plus
    colonists). The remaining 5 (combat_drones, quantum_shards,
    quantum_crystals, prismatic_ore, lumen_crystals) are acquired via
    distinct mechanics (fixed-price purchase, nebula harvest, shard assembly,
    rare find) with no production_rate regen, so is_producible=False.
  * is_storable=True marks exactly commodity_economy.SAFE_STORABLE_
    COMMODITIES (ore, organics, equipment) — the only commodities the
    citadel safe accepts (ADR-0082). Everything else is False.
  * icon defaults to the canonical slug (same as `name`) — no glyph/asset
    key has been designed yet; a future UI pass can repoint icon without a
    schema change. Not a design decision, a placeholder.

NOTE: commodity_economy.COMMODITY_BASE_PRICES also carries a "precious_metals"
entry (code-only, no canon docs backing) that is deliberately EXCLUDED here —
definitions.md's Rare Materials section lists prismatic_ore and lumen_crystals,
not precious_metals. That's a pre-existing code/docs divergence outside this
WO's scope; flagged for the orchestrator, not resolved here.
"""

import logging
from typing import Any, Dict

from sqlalchemy.orm import Session

from src.core.commodity_economy import COMMODITY_BASE_PRICES
from src.models.resource import Resource, ResourceType

logger = logging.getLogger(__name__)

CATEGORY_CORE = "core_commodity"
CATEGORY_STRATEGIC = "strategic_resource"
CATEGORY_RARE = "rare_material"


def _core(commodity_key: str) -> Dict[str, Any]:
    """Pull base_price/range for a commodity_economy-backed resource."""
    entry = COMMODITY_BASE_PRICES[commodity_key]
    lo, hi = entry["range"]
    return {"base_price": entry["base"], "price_range_min": lo, "price_range_max": hi}


# Canon resource registry (definitions.md:187-219) keyed by ResourceType.
# Declaration order mirrors the canon doc's three subsections.
RESOURCE_REGISTRY: Dict[ResourceType, Dict[str, Any]] = {
    # --- Core trading commodities (7) ---------------------------------
    ResourceType.ORE: {
        "name": "ore", "label": "Ore", "category": CATEGORY_CORE,
        **_core("ore"), "is_storable": True, "is_producible": True,
    },
    ResourceType.BASIC_FOOD: {
        "name": "organics", "label": "Organics", "category": CATEGORY_CORE,
        **_core("organics"), "is_storable": True, "is_producible": True,
    },
    ResourceType.GOURMET_FOOD: {
        "name": "gourmet_food", "label": "Gourmet Food", "category": CATEGORY_CORE,
        **_core("gourmet_food"), "is_storable": False, "is_producible": True,
    },
    ResourceType.FUEL: {
        "name": "fuel", "label": "Fuel", "category": CATEGORY_CORE,
        **_core("fuel"), "is_storable": False, "is_producible": True,
    },
    ResourceType.TECHNOLOGY: {
        "name": "equipment", "label": "Equipment", "category": CATEGORY_CORE,
        **_core("equipment"), "is_storable": True, "is_producible": True,
    },
    ResourceType.EXOTIC_TECHNOLOGY: {
        "name": "exotic_technology", "label": "Exotic Technology", "category": CATEGORY_CORE,
        **_core("exotic_technology"), "is_storable": False, "is_producible": True,
    },
    ResourceType.LUXURY_GOODS: {
        "name": "luxury_goods", "label": "Luxury Goods", "category": CATEGORY_CORE,
        **_core("luxury_goods"), "is_storable": False, "is_producible": True,
    },
    # --- Strategic resources (4) ---------------------------------------
    ResourceType.POPULATION: {
        "name": "colonists", "label": "Colonists", "category": CATEGORY_STRATEGIC,
        **_core("colonists"), "is_storable": False, "is_producible": True,
    },
    ResourceType.COMBAT_DRONES: {
        "name": "combat_drones", "label": "Combat Drones", "category": CATEGORY_STRATEGIC,
        # definitions.md:208 — Attack drones 1,000cr / Defense drones 1,200cr
        # at military outposts (flat catalog price, not the dynamic
        # station-commodity mechanic).
        "base_price": 1000, "price_range_min": 1000, "price_range_max": 1200,
        "is_storable": False, "is_producible": False,
    },
    ResourceType.QUANTUM_SHARDS: {
        "name": "quantum_shards", "label": "Quantum Shards", "category": CATEGORY_STRATEGIC,
        # No canon credit price — harvested from nebulae, not station-traded.
        "base_price": None, "price_range_min": None, "price_range_max": None,
        "is_storable": False, "is_producible": False,
    },
    ResourceType.QUANTUM_CRYSTALS: {
        "name": "quantum_crystals", "label": "Quantum Crystals", "category": CATEGORY_STRATEGIC,
        # No canon credit price — assembled from 5 shards, not station-traded.
        "base_price": None, "price_range_min": None, "price_range_max": None,
        "is_storable": False, "is_producible": False,
    },
    # --- Rare materials (2) ---------------------------------------------
    ResourceType.PRISMATIC_ORE: {
        "name": "prismatic_ore", "label": "Prismatic Ore", "category": CATEGORY_RARE,
        # No canon credit price — ~1 in 10,000 asteroid find, not station-traded.
        "base_price": None, "price_range_min": None, "price_range_max": None,
        "is_storable": False, "is_producible": False,
    },
    ResourceType.PHOTONIC_CRYSTALS: {
        # PHOTONIC_CRYSTALS is this enum's pre-existing name for canon's
        # "lumen_crystals" rare material (see module docstring).
        "name": "lumen_crystals", "label": "Lumen Crystals", "category": CATEGORY_RARE,
        # No canon credit price — nebula-specific find, not station-traded.
        "base_price": None, "price_range_min": None, "price_range_max": None,
        "is_storable": False, "is_producible": False,
    },
}


def seed_resource_registry(db: Session) -> int:
    """Idempotently upsert :data:`RESOURCE_REGISTRY` into the `resources` table.

    Query-then-upsert keyed on the canon `name` slug (WO-ARCH-RES-2I-E /
    ARCH-res-8 ungated kernel) — NOT the `type` enum. Re-keying on `name`
    decouples idempotency from ResourceType, which is a forward-compatible
    step ahead of the (separate, PROPOSE-AND-HOLD) enum->varchar retire
    migration: this seeder behaves identically whether or not that migration
    ever lands. `type` is still written on every row (the column is
    untouched, models/resource.py:92) so nothing downstream that still reads
    `type` regresses.
    Mirrors ship_specifications_seeder.seed_ship_specifications (single-
    threaded startup seed — no DB-level uniqueness needed, see
    models/resource.py). Icon defaults to the canonical slug (`name`) — see
    module docstring. Returns the number of catalog entries processed
    (created + updated).
    """
    processed = 0
    for resource_type, entry in RESOURCE_REGISTRY.items():
        existing = db.query(Resource).filter(Resource.name == entry["name"]).first()
        icon = entry.get("icon", entry["name"])

        if existing is None:
            db.add(
                Resource(
                    type=resource_type,
                    name=entry["name"],
                    label=entry["label"],
                    icon=icon,
                    category=entry["category"],
                    base_value=entry["base_price"] or 0,  # legacy NOT NULL column; 0 where no canon price
                    base_price=entry["base_price"],
                    price_range_min=entry["price_range_min"],
                    price_range_max=entry["price_range_max"],
                    is_storable=entry["is_storable"],
                    is_producible=entry["is_producible"],
                )
            )
            logger.info("Created resource registry entry for %s", entry["name"])
        else:
            existing.type = resource_type
            existing.label = entry["label"]
            existing.icon = icon
            existing.category = entry["category"]
            existing.base_value = entry["base_price"] or 0
            existing.base_price = entry["base_price"]
            existing.price_range_min = entry["price_range_min"]
            existing.price_range_max = entry["price_range_max"]
            existing.is_storable = entry["is_storable"]
            existing.is_producible = entry["is_producible"]
            logger.info("Updated resource registry entry for %s", entry["name"])
        processed += 1

    db.commit()
    logger.info("Resource registry seeding complete: %d processed", processed)
    return processed
