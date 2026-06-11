#!/usr/bin/env python3
"""
Idempotent spawn: materialize v1 pirate-captain NPCs from BANG rosters.

The BANG snapshot on each Galaxy row carries per-region NPC rosters
(Galaxy.bang_snapshot.regions[*].universe.npcRosters) that the import
pipeline stashes but never materializes — the galaxy launches with zero
NPC ships. This runs npc_spawn_service.materialize_from_bang() per galaxy:
pirate CAPTAINS only (static v1 — no movement/schedules/initiation per
SYSTEMS/npc-scheduler.md, which is Design-only; enforcers and lords are
held back, see the service docstring for the lord-count canon conflict).

Run inside the gameserver container:
    docker compose exec gameserver python spawn_npcs.py

Safe to re-run: rosters whose NPCCharacter rows already exist (including
KIA rows) are skipped.
"""

import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import src.models  # register all mappers
from src.core.database import SessionLocal
from src.models.galaxy import Galaxy
from src.services.npc_spawn_service import materialize_from_bang

logger = logging.getLogger(__name__)


def main() -> None:
    session = SessionLocal()
    try:
        galaxies = session.query(Galaxy).all()
        if not galaxies:
            print("No galaxies found — nothing to spawn.")
            return

        print(f"{'GALAXY':<38} {'ROSTERS':>8} {'SPAWNED':>8} {'EXISTS':>7} {'BADSEC':>7} {'CAPTAINS':>9}")
        print("-" * 80)
        for galaxy in galaxies:
            if not galaxy.bang_snapshot:
                print(f"{str(galaxy.id):<38} {'—':>8}  (no bang_snapshot; skipped)")
                continue
            stats = materialize_from_bang(session, galaxy)
            session.commit()
            print(
                f"{str(galaxy.id):<38} "
                f"{stats['rosters_seen']:>8} "
                f"{stats['rosters_spawned']:>8} "
                f"{stats['rosters_skipped_existing']:>7} "
                f"{stats['rosters_skipped_bad_sector']:>7} "
                f"{stats['captains_spawned']:>9}"
            )
            for warning in stats["warnings"]:
                print(f"  WARNING: {warning}")
        print("-" * 80)
        print("NPC spawn complete.")
    finally:
        session.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    main()
