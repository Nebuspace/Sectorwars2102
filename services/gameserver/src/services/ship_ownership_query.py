"""Ownership predicates for player-facing ship lists.

Canon legal owner is ``Ship.registered_owner_id`` (DATA_MODELS/ships.md —
"formerly owner_id"). ``owner_id`` remains as a legacy possession column that
transfer/claim writers still keep in sync, but list endpoints must not treat
a matching ``owner_id`` as ownership when ``registered_owner_id`` points
elsewhere.
"""

from __future__ import annotations

from typing import Any, TYPE_CHECKING
from uuid import UUID

if TYPE_CHECKING:
    from sqlalchemy.sql.elements import ColumnElement


def ship_listed_for_owner(ship: Any, player_id: UUID) -> bool:
    """Whether GET /player/ships should include ``ship`` for ``player_id``."""
    if getattr(ship, "registered_owner_id", None) is not None:
        return ship.registered_owner_id == player_id
    return ship.owner_id == player_id


def owned_ships_filter(player_id: UUID) -> "ColumnElement[bool]":
    """SQLAlchemy filter matching :func:`ship_listed_for_owner`."""
    from sqlalchemy import and_, or_

    from src.models.ship import Ship

    return or_(
        Ship.registered_owner_id == player_id,
        and_(Ship.registered_owner_id.is_(None), Ship.owner_id == player_id),
    )
