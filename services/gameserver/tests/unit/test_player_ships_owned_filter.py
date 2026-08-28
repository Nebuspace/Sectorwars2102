"""WO-DIAGNOSE-SHIP-OWNER-ID-VS-REGISTERED-OWNER-ID-BACKFILL-GAP

GET /player/ships must key off registered_owner_id (canon legal owner),
falling back to owner_id only when registered_owner_id is NULL.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

from src.services.ship_ownership_query import ship_listed_for_owner


def _ship(**kwargs):
    defaults = dict(
        id=uuid.uuid4(),
        owner_id=uuid.uuid4(),
        registered_owner_id=None,
    )
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def test_lists_when_registered_owner_matches():
    player = uuid.uuid4()
    ship = _ship(owner_id=uuid.uuid4(), registered_owner_id=player)
    assert ship_listed_for_owner(ship, player) is True


def test_excludes_when_only_stale_owner_id_matches():
    """Divergent fields: registered owner is someone else — do not list."""
    player = uuid.uuid4()
    ship = _ship(owner_id=player, registered_owner_id=uuid.uuid4())
    assert ship_listed_for_owner(ship, player) is False


def test_fallback_when_registered_owner_null():
    """Pre-registry row: registered_owner_id never backfilled."""
    player = uuid.uuid4()
    ship = _ship(owner_id=player, registered_owner_id=None)
    assert ship_listed_for_owner(ship, player) is True


def test_excludes_unrelated_null_registered():
    player = uuid.uuid4()
    ship = _ship(owner_id=uuid.uuid4(), registered_owner_id=None)
    assert ship_listed_for_owner(ship, player) is False
