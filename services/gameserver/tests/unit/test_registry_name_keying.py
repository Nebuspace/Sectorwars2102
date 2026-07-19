"""Unit tests for WO-ARCH-RES-2I-E (ARCH-res-8 ungated kernel — seeder rekey).

Real-DB tests against the `db` fixture (mirrors test_resource_registry.py's
seeder-test layer) — proves seed_resource_registry's upsert idempotency is
now keyed on the canon `name` slug rather than the ResourceType enum, ahead
of the (separate, PROPOSE-AND-HOLD) enum->varchar retire migration.
"""

from sqlalchemy.orm import Session

from src.core.resource_registry_seeder import RESOURCE_REGISTRY, seed_resource_registry
from src.models.resource import Resource, ResourceType


def test_double_seed_yields_thirteen_rows_thirteen_distinct_names(db: Session):
    seed_resource_registry(db)
    seed_resource_registry(db)

    rows = db.query(Resource).all()
    assert len(rows) == 13
    assert len({r.name for r in rows}) == 13


def test_lumen_crystals_row_exists_by_name(db: Session):
    """PHOTONIC_CRYSTALS -> 'lumen_crystals' mapping row (module docstring) —
    reachable by its canon name, the seeder's new idempotency key."""
    seed_resource_registry(db)
    row = db.query(Resource).filter(Resource.name == "lumen_crystals").first()
    assert row is not None
    assert row.type == ResourceType.PHOTONIC_CRYSTALS


def test_reseed_updates_by_name_not_duplicate(db: Session):
    """A row whose name pre-exists (matching a RESOURCE_REGISTRY entry) is
    updated in place on re-seed, never duplicated — the actual ARCH-res-8
    regression this WO exists to prevent."""
    seed_resource_registry(db)
    row = db.query(Resource).filter(Resource.name == "ore").first()
    row.label = "Drifted Label"
    db.commit()

    processed = seed_resource_registry(db)

    assert processed == 13
    matching_rows = db.query(Resource).filter(Resource.name == "ore").all()
    assert len(matching_rows) == 1
    assert matching_rows[0].label == "Ore"


def test_reseed_reconciles_type_when_name_is_the_lookup_key(db: Session):
    """Forward-compat: even if a row's `type` drifted from RESOURCE_REGISTRY
    (e.g. mid-transition to the enum->varchar retire), re-seeding by `name`
    still finds it and reconciles `type` back to canon — the seeder no
    longer depends on `type` staying in sync to find its own rows."""
    seed_resource_registry(db)
    row = db.query(Resource).filter(Resource.name == "ore").first()
    original_type = row.type
    # Simulate type drift while name (the new lookup key) stays canonical.
    row.type = ResourceType.PRISMATIC_ORE
    db.commit()

    seed_resource_registry(db)

    db.refresh(row)
    assert row.name == "ore"
    assert row.type == original_type == ResourceType.ORE
    # No duplicate row was created for prismatic_ore's own entry either.
    prismatic_rows = db.query(Resource).filter(Resource.name == "prismatic_ore").all()
    assert len(prismatic_rows) == 1


def test_registry_entry_count_matches_row_count_after_seed(db: Session):
    seed_resource_registry(db)
    assert db.query(Resource).count() == len(RESOURCE_REGISTRY) == 13
