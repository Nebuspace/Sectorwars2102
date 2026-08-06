"""Unit tests for the canon faction roster seeder (WO-E, auth/admin.py
create_default_factions), wired into the gameserver startup lifespan
(src/main.py). Mirrors the seeder-test conventions in
test_resource_registry.py: real `db` fixture, create + idempotent re-seed.
"""

import pytest
from sqlalchemy.orm import Session

from src.auth.admin import create_default_factions
from src.models.faction import Faction, FactionType
from src.services.npc_spawn_service import (
    _ensure_concord_faction,
    _ensure_federation_faction,
)

CANON_FACTION_TYPES = {
    FactionType.FEDERATION,
    FactionType.MERCHANTS,
    FactionType.INDEPENDENTS,
    FactionType.MINING,
    FactionType.EXPLORERS,
    FactionType.OUTLAWS,
    FactionType.SYNDICATE,
    FactionType.PIRATES,
    FactionType.CONCORD,
}


def test_seed_creates_all_nine_canon_factions(db: Session):
    create_default_factions(db)
    types = {row[0] for row in db.query(Faction.faction_type).all()}
    assert types == CANON_FACTION_TYPES
    assert db.query(Faction).count() == 9
    syndicate = db.query(Faction).filter(Faction.faction_type == FactionType.SYNDICATE).one()
    assert syndicate.name == "Shadow Syndicate"
    concord = db.query(Faction).filter(Faction.faction_type == FactionType.CONCORD).one()
    assert concord.name == "Galactic Concord"


def test_seed_is_idempotent_across_two_calls(db: Session):
    """Calling the wired startup path twice in a row must not duplicate rows."""
    create_default_factions(db)
    create_default_factions(db)
    assert db.query(Faction).count() == 9
    types = {row[0] for row in db.query(Faction.faction_type).all()}
    assert types == CANON_FACTION_TYPES


def test_seed_coexists_with_federation_safety_net(db: Session):
    """npc_spawn_service._ensure_federation_faction may create the Federation
    row first at runtime; create_default_factions must fill in the rest
    without duplicating the pre-existing Federation row."""
    # Clear within the fixture transaction only (no commit — outer rollback
    # restores the live roster). Live DB may already hold a full seed.
    db.query(Faction).delete()
    db.flush()

    federation = _ensure_federation_faction(db)
    db.flush()
    assert db.query(Faction).count() == 1

    create_default_factions(db)

    assert db.query(Faction).count() == 9
    types = {row[0] for row in db.query(Faction.faction_type).all()}
    assert types == CANON_FACTION_TYPES
    # the pre-existing Federation row was left untouched, not duplicated
    still_there = db.query(Faction).filter(Faction.id == federation.id).one()
    assert still_there.faction_type == FactionType.FEDERATION


def test_ensure_concord_faction_idempotent(db: Session):
    a = _ensure_concord_faction(db)
    b = _ensure_concord_faction(db)
    assert a.id == b.id
    assert a.name == "Galactic Concord"
    assert a.faction_type == FactionType.CONCORD
    assert db.query(Faction).filter(Faction.faction_type == FactionType.CONCORD).count() == 1
