"""Unit tests for the resource registry (WO-ARCH-RES-1-KERNEL).

Three layers, matching the medal-catalog / docking-turns test conventions
already in this suite:
  * pure-catalog tests on RESOURCE_REGISTRY — no DB, no fixtures
  * seeder tests against the real `db` fixture — create + idempotent re-seed
  * route test — calls the route function directly (bypassing FastAPI DI),
    same pattern as test_docking_turns.py
"""

import pytest
from sqlalchemy.orm import Session

from src.api.routes.resources import list_resources
from src.core.commodity_economy import COMMODITY_BASE_PRICES
from src.core.resource_registry_seeder import (
    CATEGORY_CORE,
    CATEGORY_RARE,
    CATEGORY_STRATEGIC,
    RESOURCE_REGISTRY,
    seed_resource_registry,
)
from src.models.resource import Resource, ResourceType

# ----------------------------------------------------------------------
# Pure-catalog tests — no DB
# ----------------------------------------------------------------------

CANON_NAMES = {
    "ore", "organics", "gourmet_food", "fuel", "equipment",
    "exotic_technology", "luxury_goods",
    "colonists", "combat_drones", "quantum_shards", "quantum_crystals",
    "prismatic_ore", "lumen_crystals",
}


def test_registry_covers_every_resource_type():
    """Every ResourceType member has exactly one registry entry (no gaps)."""
    assert set(RESOURCE_REGISTRY.keys()) == set(ResourceType)


def test_registry_names_match_canon_list():
    """The 13 seeded names are exactly definitions.md's Resource Types list."""
    names = {entry["name"] for entry in RESOURCE_REGISTRY.values()}
    assert names == CANON_NAMES
    assert len(RESOURCE_REGISTRY) == 13


def test_registry_category_counts_match_canon_sections():
    """7 core commodities / 4 strategic resources / 2 rare materials."""
    by_category = {}
    for entry in RESOURCE_REGISTRY.values():
        by_category.setdefault(entry["category"], 0)
        by_category[entry["category"]] += 1
    assert by_category == {CATEGORY_CORE: 7, CATEGORY_STRATEGIC: 4, CATEGORY_RARE: 2}


@pytest.mark.parametrize(
    "commodity_key",
    ["ore", "organics", "gourmet_food", "fuel", "equipment",
     "exotic_technology", "luxury_goods", "colonists"],
)
def test_priced_resources_match_commodity_economy_exactly(commodity_key):
    """base_price/range for the 8 station-tradeable resources trace 1:1 to
    commodity_economy.COMMODITY_BASE_PRICES — never re-derived or invented."""
    entry = next(e for e in RESOURCE_REGISTRY.values() if e["name"] == commodity_key)
    expected = COMMODITY_BASE_PRICES[commodity_key]
    lo, hi = expected["range"]
    assert entry["base_price"] == expected["base"]
    assert entry["price_range_min"] == lo
    assert entry["price_range_max"] == hi


@pytest.mark.parametrize(
    "name", ["quantum_shards", "quantum_crystals", "prismatic_ore", "lumen_crystals"]
)
def test_unpriced_rare_resources_have_no_fabricated_price(name):
    """Canon gives no credit price for these four — must stay None, not a guess."""
    entry = next(e for e in RESOURCE_REGISTRY.values() if e["name"] == name)
    assert entry["base_price"] is None
    assert entry["price_range_min"] is None
    assert entry["price_range_max"] is None


def test_combat_drones_price_spans_both_canon_figures():
    """definitions.md:208 — Attack 1,000cr / Defense 1,200cr; encoded as the
    exact span, not a fabricated single number."""
    entry = RESOURCE_REGISTRY[ResourceType.COMBAT_DRONES]
    assert entry["base_price"] == 1000
    assert entry["price_range_min"] == 1000
    assert entry["price_range_max"] == 1200


def test_is_storable_matches_citadel_safe_storable_set():
    """Only ore/organics/equipment are citadel-safe eligible (ADR-0082)."""
    storable = {e["name"] for e in RESOURCE_REGISTRY.values() if e["is_storable"]}
    assert storable == {"ore", "organics", "equipment"}


def test_is_producible_matches_station_production_mechanic():
    """The 7 core commodities + colonists carry the station production_rate
    regen mechanic; the other 5 do not."""
    producible = {e["name"] for e in RESOURCE_REGISTRY.values() if e["is_producible"]}
    assert producible == {
        "ore", "organics", "gourmet_food", "fuel", "equipment",
        "exotic_technology", "luxury_goods", "colonists",
    }


# ----------------------------------------------------------------------
# Seeder tests — real DB session
# ----------------------------------------------------------------------

def test_seed_creates_all_thirteen_resources(db: Session):
    processed = seed_resource_registry(db)
    assert processed == 13
    assert db.query(Resource).count() == 13


def test_seed_is_idempotent(db: Session):
    seed_resource_registry(db)
    processed_again = seed_resource_registry(db)
    assert processed_again == 13
    assert db.query(Resource).count() == 13  # no duplicates


def test_seed_reconciles_a_hand_edited_row(db: Session):
    """Re-running the seeder overwrites drift back to the canonical values —
    the registry in code is the source of truth, not the DB row."""
    seed_resource_registry(db)
    row = db.query(Resource).filter(Resource.type == ResourceType.ORE).first()
    row.label = "Corrupted Label"
    row.base_price = 999999
    db.commit()

    seed_resource_registry(db)
    db.refresh(row)
    assert row.label == "Ore"
    assert row.base_price == 15


def test_seeded_row_field_shape(db: Session):
    seed_resource_registry(db)
    row = db.query(Resource).filter(Resource.type == ResourceType.LUXURY_GOODS).first()
    assert row.name == "luxury_goods"
    assert row.label == "Luxury Goods"
    assert row.category == CATEGORY_CORE
    assert row.icon == "luxury_goods"  # defaults to the slug, see seeder docstring
    assert row.is_storable is False
    assert row.is_producible is True
    assert row.is_active is True


# ----------------------------------------------------------------------
# Route test — calls the handler directly, bypassing FastAPI DI (mirrors
# test_docking_turns.py convention)
# ----------------------------------------------------------------------

@pytest.mark.asyncio
async def test_list_resources_returns_seeded_catalog(db: Session):
    seed_resource_registry(db)

    result = await list_resources(player=None, db=db)

    assert len(result) == 13
    names = {r.name for r in result}
    assert names == CANON_NAMES
    # Ordered by (category, name) per the route's order_by.
    ordered_names = [r.name for r in result]
    assert ordered_names == sorted(
        ordered_names,
        key=lambda n: (
            next(r.category for r in result if r.name == n),
            n,
        ),
    )


@pytest.mark.asyncio
async def test_list_resources_excludes_inactive_rows(db: Session):
    seed_resource_registry(db)
    row = db.query(Resource).filter(Resource.type == ResourceType.PRISMATIC_ORE).first()
    row.is_active = False
    db.commit()

    result = await list_resources(player=None, db=db)

    assert "prismatic_ore" not in {r.name for r in result}
    assert len(result) == 12


@pytest.mark.asyncio
async def test_route_is_a_pure_table_read_not_a_hardcoded_list(db: Session):
    """The Accept criterion: inserting a new row surfaces via the API with
    zero code change. Proven here by bypassing the seeder entirely — insert
    a single hand-built row and confirm the route reflects exactly that row,
    which only holds if list_resources queries the table rather than
    re-deriving from RESOURCE_REGISTRY or any other hardcoded list."""
    db.add(
        Resource(
            type=ResourceType.ORE,
            name="ore",
            label="Ore",
            category=CATEGORY_CORE,
            base_value=15,
            icon="ore",
            base_price=15,
            price_range_min=15,
            price_range_max=45,
            is_storable=True,
            is_producible=True,
        )
    )
    db.commit()

    result = await list_resources(player=None, db=db)

    assert len(result) == 1
    assert result[0].name == "ore"
    assert result[0].base_price == 15
