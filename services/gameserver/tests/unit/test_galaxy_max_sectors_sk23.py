"""WO-FIX-GALAXY-MAX-SECTORS-HARDCODED-500 — SK23 soft target.

DB-free: assert the ORM column is nullable (no hard cap).
"""
from __future__ import annotations

from src.models.galaxy import Galaxy


def test_galaxy_max_sectors_is_nullable_soft_target():
    col = Galaxy.__table__.c.max_sectors
    assert col.nullable is True
    assert col.default.arg == 500
