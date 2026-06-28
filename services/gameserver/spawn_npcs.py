#!/usr/bin/env python3
"""
Idempotent Living NPC System bootstrap (wrapper around
npc_spawn_service.bootstrap_galaxy).

Per galaxy with a BANG snapshot: materialize NPCCharacter + Ship rows
from the stashed rosters, seed NPCRoster rows (Loop B maintenance
targets), and backfill patrol schedules onto pre-runtime NPC rows so
the scheduler (NPC_SCHEDULER_ENABLED) can drive them.

Run inside the gameserver container:
    docker compose exec gameserver python spawn_npcs.py

Safe to re-run: every step is idempotent by bang_roster_ref.
"""

import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import src.models  # register all mappers
from src.core.database import SessionLocal
from src.models.galaxy import Galaxy
from src.services.npc_spawn_service import bootstrap_galaxy

logger = logging.getLogger(__name__)


def main() -> None:
    session = SessionLocal()
    try:
        galaxies = session.query(Galaxy).all()
        if not galaxies:
            print("No galaxies found — nothing to spawn.")
            return

        print(
            f"{'GALAXY':<38} {'ROSTERS':>8} {'SPAWNED':>8} {'EXISTS':>7} "
            f"{'BADSEC':>7} {'NPCS':>6} {'R-NEW':>6} {'R-OLD':>6} {'SCHED':>6}"
        )
        print("-" * 100)
        for galaxy in galaxies:
            if not galaxy.bang_snapshot:
                print(f"{str(galaxy.id):<38} {'—':>8}  (no bang_snapshot; skipped)")
                continue
            stats = bootstrap_galaxy(session, galaxy)
            session.commit()
            print(
                f"{str(galaxy.id):<38} "
                f"{stats['rosters_seen']:>8} "
                f"{stats['rosters_spawned']:>8} "
                f"{stats['rosters_skipped_existing']:>7} "
                f"{stats['rosters_skipped_bad_sector']:>7} "
                f"{stats['captains_spawned']:>6} "
                f"{stats.get('rosters_created', 0):>6} "
                f"{stats.get('rosters_existing', 0):>6} "
                f"{stats.get('schedules_backfilled', 0):>6}"
            )
            for warning in stats["warnings"]:
                print(f"  WARNING: {warning}")
        print("-" * 100)
        print("NPC bootstrap complete.")
    finally:
        session.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    main()
