"""ADR-0050 SK24 — plain UUID region snapshots for audit rows.

Copied at write time WITHOUT an FK to ``regions.id``, so region
terminate/regenerate cannot cascade-delete or SET-NULL the audit identity
(``CombatLog.region_id_snapshot`` is the precedent).
"""
from __future__ import annotations

import uuid
from typing import Any, Optional


def region_id_snapshot_of(obj: Any) -> Optional[uuid.UUID]:
    """Best-effort ``region_id`` from a Sector / Station / Holding-like object."""
    if obj is None:
        return None
    rid = getattr(obj, "region_id", None)
    return rid if isinstance(rid, uuid.UUID) else rid


def coalesce_region_snapshot(*candidates: Any) -> Optional[uuid.UUID]:
    """First non-None UUID among candidates (objects or raw UUIDs)."""
    for c in candidates:
        if c is None:
            continue
        if isinstance(c, uuid.UUID):
            return c
        rid = region_id_snapshot_of(c)
        if rid is not None:
            return rid
    return None
