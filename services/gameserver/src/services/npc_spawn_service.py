"""NPC Spawn Service — static pirate-captain materialization (v1).

Materializes NPCCharacter + Ship rows from the BANG rosters stashed on
``Galaxy.bang_snapshot.regions[*].universe.npcRosters`` and surfaces them
in sector presence so the COMMS panel and the combat target list see them
with zero extra plumbing.

Canon anchors:
  - DATA_MODELS/npcs.md — NPCCharacter schema + "Patrol-squad linkage"
  - SYSTEMS/npc-scheduler.md — Loop B (spawn recipe; this is a one-shot,
    degenerate Loop B) and "KIA processing" steps 1-4
  - ADR-0047 — ``Sector.defenses.pirate_patrol_ships`` JSON shape

V1 scope (documented subset, not invention — the NPC scheduler/lifecycle
docs are explicitly Design-only):
  - Pirate CAPTAINS only. Enforcers are held back for a later slice;
    lords are held back because the BANG snapshot's 13 lords contradict
    canon ADR-0047's "Stronghold-tier only, 1-2 per region" (conflict
    flagged to Max).
  - Static NPCs: no movement, no schedules, no NPC-initiated combat,
    no respawn (Loop B), no NPCDeathLog, no reputation/bounty hooks.
"""

import logging
import uuid
from datetime import datetime, UTC
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import func
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from src.core.game_time import scaled_deadline
from src.models.galaxy import Galaxy
from src.models.npc_character import NPCCharacter, NPCArchetype, NPCStatus
from src.models.sector import Sector
from src.models.ship import Ship, ShipSpecification, ShipStatus, ShipType

logger = logging.getLogger(__name__)

# Deterministic iteration order over the snapshot regions. Offsets are NOT
# derived from this order — they come from the live sectors table (see
# _region_offset_map), because the actual galaxy layout has diverged from
# the import-time region ordering.
REGION_ORDER: Tuple[str, ...] = ("terran_space", "player_owned", "central_nexus")

PIRATE_CAPTAIN_KIND = "pirate_captain"
PIRATE_CAPTAIN_TITLE = "Pirate Captain"

# Canon: SYSTEMS/npc-scheduler.md "KIA processing" step 2 —
# respawn_eligible_at = now + 7 days (faction-tunable cooldown).
KIA_RESPAWN_COOLDOWN_HOURS = 7 * 24


def _region_offset_map(db: Session, regions: Dict[str, Any]) -> Dict[str, int]:
    """Derive per-region global sector-id offsets from the live database.

    Roster ``hostSectorId`` values are region-local starting at 1, so a
    region's offset is its smallest global ``sectors.sector_id`` minus 1:
    ``offset = min(sector_id WHERE region_id = <snapshot region id>) - 1``.

    This deliberately does NOT recompute offsets from the snapshot's
    region code-order — the live galaxy layout has diverged from that
    assumption (dev galaxy: player_owned at global 1-1000, terran_space
    at 1001-1300, central_nexus at 1301-6300), and order-derived offsets
    land every roster in the wrong region. The region_id cross-check in
    materialize_from_bang remains the corruption guard.

    Regions without a ``region_id`` in the snapshot or without sectors in
    the database get no entry (the caller skips their rosters with a
    warning). Regions added later via "Add Player-Owned Region" live
    under ``bang_snapshot.additional_regions`` WITHOUT their universe
    blob — they carry no rosters and are out of scope here.
    """
    offsets: Dict[str, int] = {}
    for region_type, region_snapshot in regions.items():
        if not isinstance(region_snapshot, dict):
            continue
        region_id = region_snapshot.get("region_id")
        if not region_id:
            continue
        min_sector_id = (
            db.query(func.min(Sector.sector_id))
            .filter(Sector.region_id == region_id)
            .scalar()
        )
        if min_sector_id is None:
            continue
        offsets[region_type] = int(min_sector_id) - 1
    return offsets


# Roman-numeral generations for round-robin name reuse (II, III, IV, ...)
_ROMAN_NUMERALS: Tuple[Tuple[int, str], ...] = (
    (1000, "M"), (900, "CM"), (500, "D"), (400, "CD"),
    (100, "C"), (90, "XC"), (50, "L"), (40, "XL"),
    (10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I"),
)


def _roman(n: int) -> str:
    parts: List[str] = []
    for value, symbol in _ROMAN_NUMERALS:
        while n >= value:
            parts.append(symbol)
            n -= value
    return "".join(parts)


def _build_npc_ship(
    spec: ShipSpecification,
    name: str,
    sector_id: int,
) -> Ship:
    """Construct an NPC-piloted Ship mirroring ShipService.create_ship defaults.

    # PLACEHOLDER: canon defines no pirate hull stats (see police-forces.md
    # for the fully-specified Interdictor pattern this should eventually
    # follow); pirate captains reuse LIGHT_FREIGHTER specs for now.
    # Revisit with pirate interdictor hulls when canon supplies numbers.
    """
    return Ship(
        name=name,
        type=spec.type,
        owner_id=None,
        is_npc=True,
        sector_id=sector_id,
        base_speed=spec.speed,
        current_speed=spec.speed,
        turn_cost=spec.turn_cost,
        warp_capable=spec.warp_compatible,
        is_active=True,
        status=ShipStatus.IN_SPACE,
        maintenance={
            "condition": 100.0,
            "last_maintenance": datetime.now(UTC).isoformat(),
            "next_maintenance": None,
            "repair_needed": False,
        },
        cargo={"capacity": spec.max_cargo, "used": 0, "contents": {}},
        combat={
            "shields": spec.max_shields,
            "max_shields": spec.max_shields,
            "shield_recharge_rate": spec.shield_recharge_rate,
            "hull": spec.hull_points,
            "max_hull": spec.hull_points,
            "evasion": spec.evasion,
            "attack_rating": spec.attack_rating,
            "defense_rating": spec.defense_rating,
        },
        attack_turn_cost=spec.attack_turn_cost,
        genesis_devices=0,
        max_genesis_devices=spec.max_genesis_devices,
        mines=0,
        max_mines=spec.max_drones,
        is_destroyed=False,
        is_flagship=False,
        # NPC hulls are not purchased — no market value to invent.
        purchase_value=0,
        current_value=0,
        upgrades={},
        equipment_slots={},
        insurance=None,
    )


def _presence_entry(npc: NPCCharacter, ship: Ship) -> Dict[str, Any]:
    """A Sector.players_present entry shaped like movement_service's
    _update_player_presence write, plus ``is_npc: true`` so consumers can
    distinguish NPCs (the player_id key carries the NPCCharacter id, which
    will NOT resolve against the players table)."""
    return {
        "player_id": str(npc.id),
        "username": f"{PIRATE_CAPTAIN_TITLE} {npc.name}",
        "ship_id": str(ship.id),
        "ship_name": ship.name,
        "ship_type": ship.type.name,
        "team_id": None,
        "arrived_at": datetime.now(UTC).isoformat(),
        "is_npc": True,
    }


def materialize_from_bang(db: Session, galaxy: Galaxy) -> Dict[str, Any]:
    """One-shot, degenerate Loop B (SYSTEMS/npc-scheduler.md): spawn the
    pirate-captain rosters from the galaxy's BANG snapshot.

    Per captain, two rows (Ship + NPCCharacter) and two JSONB presence
    writes on the host sector (players_present entry; one shared
    pirate_patrol_ships squad row per roster).

    Idempotent per roster: a roster is skipped when NPCCharacter rows with
    its ``bang_roster_ref`` already exist — including KIA rows, so a
    killed captain does not silently respawn on re-run (respawn is Loop B
    with cooldown, out of v1 scope).

    The caller owns the transaction (flush only, no commit) — mirrors the
    service pattern; spawn_npcs.py commits.
    """
    snapshot = galaxy.bang_snapshot or {}
    regions = snapshot.get("regions") or {}
    stats: Dict[str, Any] = {
        "rosters_seen": 0,
        "rosters_spawned": 0,
        "rosters_skipped_existing": 0,
        "rosters_skipped_bad_sector": 0,
        "captains_spawned": 0,
        "warnings": [],
    }

    if not regions:
        stats["warnings"].append(
            f"galaxy {galaxy.id} has no bang_snapshot.regions — nothing to spawn"
        )
        return stats

    # PLACEHOLDER: canon defines no pirate hull stats (see police-forces.md
    # for the pattern — Marshal/Sentinel Interdictors are fully numeric);
    # revisit with pirate interdictor hulls. Captains fly LIGHT_FREIGHTER
    # specs until canon supplies pirate numbers.
    spec = (
        db.query(ShipSpecification)
        .filter(ShipSpecification.type == ShipType.LIGHT_FREIGHTER)
        .first()
    )
    if spec is None:
        raise ValueError(
            "No ShipSpecification for LIGHT_FREIGHTER — seed ship specs "
            "before spawning NPCs"
        )

    offsets = _region_offset_map(db, regions)

    for region_type in REGION_ORDER:
        region_snapshot = regions.get(region_type)
        if not isinstance(region_snapshot, dict):
            continue
        universe = region_snapshot.get("universe") or {}
        rosters = universe.get("npcRosters") or []
        region_id = region_snapshot.get("region_id")
        offset = offsets.get(region_type)
        if offset is None:
            if any(str(r.get("kind", "")) == PIRATE_CAPTAIN_KIND for r in rosters):
                stats["warnings"].append(
                    f"region {region_type}: no region_id in snapshot or no "
                    f"sectors in DB — cannot derive offset; skipping its rosters"
                )
            continue

        for roster in rosters:
            if str(roster.get("kind", "")) != PIRATE_CAPTAIN_KIND:
                continue
            stats["rosters_seen"] += 1
            # Galaxy-scoped so two galaxies sharing region types/roster ids
            # never collide on the idempotency marker.
            roster_ref = f"{galaxy.id}:{region_type}:{roster.get('id')}"

            existing = (
                db.query(NPCCharacter)
                .filter(NPCCharacter.bang_roster_ref == roster_ref)
                .count()
            )
            if existing:
                stats["rosters_skipped_existing"] += 1
                logger.info(
                    "Roster %s already materialized (%d NPC rows); skipping",
                    roster_ref, existing,
                )
                continue

            name_pool: List[str] = [str(n) for n in (roster.get("namePool") or [])]
            target_count = int(roster.get("targetCount", 0))
            if not name_pool or target_count <= 0:
                stats["warnings"].append(
                    f"roster {roster_ref}: empty namePool or targetCount "
                    f"{target_count}; skipping"
                )
                continue

            # Rosters carry region-local hostSectorId starting at 1; the
            # offset is derived from the live sectors table (see
            # _region_offset_map), not from import-order assumptions.
            host_local = int(roster.get("hostSectorId", 0))
            global_sector_id = host_local + offset
            # Row lock: the presence writes below are JSONB
            # read-modify-write — serialize against concurrent writers.
            sector = (
                db.query(Sector)
                .filter(Sector.sector_id == global_sector_id)
                .with_for_update()
                .first()
            )
            if sector is None:
                stats["rosters_skipped_bad_sector"] += 1
                stats["warnings"].append(
                    f"roster {roster_ref}: global sector {global_sector_id} "
                    f"(local {host_local} + offset {offset}) not found"
                )
                continue
            # Offset-mistake guard: the resolved sector must belong to the
            # region the roster came from.
            if region_id and str(sector.region_id) != str(region_id):
                stats["rosters_skipped_bad_sector"] += 1
                stats["warnings"].append(
                    f"roster {roster_ref}: sector {global_sector_id} belongs "
                    f"to region {sector.region_id}, expected {region_id} — "
                    f"offset mismatch, refusing to spawn"
                )
                continue

            faction_code = str(roster.get("factionCode", "pirates"))
            now = datetime.now(UTC)
            squad_npc_ids: List[str] = []
            presence_entries: List[Dict[str, Any]] = []

            for i in range(target_count):
                base_name = name_pool[i % len(name_pool)]
                # Round-robin wrap reuses pool names — suffix a roman
                # numeral generation (II, III, ...) so usernames stay
                # unique per sector.
                generation = i // len(name_pool) + 1
                captain_name = (
                    base_name if generation == 1
                    else f"{base_name} {_roman(generation)}"
                )
                ship = _build_npc_ship(
                    spec,
                    name=f"Captain {captain_name}'s Marauder",
                    sector_id=global_sector_id,
                )
                db.add(ship)
                db.flush()  # surface ship.id for the FK + presence entry

                npc = NPCCharacter(
                    name=captain_name,
                    title=PIRATE_CAPTAIN_TITLE,
                    faction_code=faction_code,
                    archetype=NPCArchetype.HOSTILE_RAIDER,
                    status=NPCStatus.ON_DUTY,
                    current_sector_id=global_sector_id,
                    ship_id=ship.id,
                    bang_roster_ref=roster_ref,
                    spawned_at=now,
                    last_seen_at=now,
                )
                db.add(npc)
                db.flush()

                squad_npc_ids.append(str(npc.id))
                presence_entries.append(_presence_entry(npc, ship))
                stats["captains_spawned"] += 1

            # Presence write 1: players_present (movement_service shape) —
            # makes the captains visible in COMMS and the combat target list.
            players_present = list(sector.players_present or [])
            known_ids = {p.get("player_id") for p in players_present}
            players_present.extend(
                e for e in presence_entries if e["player_id"] not in known_ids
            )
            sector.players_present = players_present
            flag_modified(sector, "players_present")

            # Presence write 2: defenses.pirate_patrol_ships — ADR-0047
            # squad shape. One squad row per roster. holding_id omitted:
            # the PirateHolding table is Design-only (canon gap).
            defenses = dict(sector.defenses or {})
            pirate_patrols = list(defenses.get("pirate_patrol_ships") or [])
            pirate_patrols.append({
                "patrol_id": str(uuid.uuid4()),
                "faction_code": faction_code,
                "squad_kind": PIRATE_CAPTAIN_KIND,
                "npc_character_ids": squad_npc_ids,
                "ship_count": len(squad_npc_ids),
                "deployed_at": now.isoformat(),
            })
            defenses["pirate_patrol_ships"] = pirate_patrols
            sector.defenses = defenses
            flag_modified(sector, "defenses")

            stats["rosters_spawned"] += 1
            logger.info(
                "Spawned %d pirate captain(s) at sector %d (roster %s)",
                len(squad_npc_ids), global_sector_id, roster_ref,
            )

    db.flush()
    return stats


def handle_npc_ship_destroyed(db: Session, ship_id: uuid.UUID) -> Optional[NPCCharacter]:
    """KIA processing, steps 1-4 of SYSTEMS/npc-scheduler.md.

    Called by the combat worker (lazy import, frozen signature) when a
    ship is destroyed. No-op (returns None) when the ship has no NPC
    pilot, so the worker can call it unconditionally.

    Steps implemented:
      1. (this handler is the destruction hook)
      2. status=KIA, current_sector_id=NULL, destroyed_at=now,
         respawn_eligible_at=now + 7 canonical days (scaled_deadline so
         dev time acceleration applies)
      4. squad row updated to drop the dead NPC; deleted when empty —
         plus removal of the NPC's players_present entry

    Deferred (documented, not dropped silently): step 3 NPCDeathLog (no
    table in v1), step 5 ship_id detach (canonical destruction handler
    owns the ship row), steps 6-9 realtime events, reputation hooks, and
    Loop B replacement.

    Rows are never deleted — the named NPC is permanently gone but the
    NPCCharacter row persists per canon.
    """
    npc = (
        db.query(NPCCharacter)
        .filter(NPCCharacter.ship_id == ship_id)
        .first()
    )
    if npc is None:
        return None
    if npc.status == NPCStatus.KIA:
        return npc  # already processed

    sector_id = npc.current_sector_id
    now = datetime.now(UTC)

    npc.status = NPCStatus.KIA
    npc.current_sector_id = None
    npc.destroyed_at = now
    npc.last_seen_at = now
    npc.respawn_eligible_at = scaled_deadline(KIA_RESPAWN_COOLDOWN_HOURS, start=now)

    if sector_id is not None:
        # Row lock: the presence cleanup below is JSONB read-modify-write —
        # serialize against concurrent writers.
        sector = (
            db.query(Sector)
            .filter(Sector.sector_id == sector_id)
            .with_for_update()
            .first()
        )
        if sector is not None:
            npc_id_str = str(npc.id)

            players_present = [
                p for p in (sector.players_present or [])
                if p.get("player_id") != npc_id_str
            ]
            sector.players_present = players_present
            flag_modified(sector, "players_present")

            defenses = dict(sector.defenses or {})
            patrols: List[Dict[str, Any]] = []
            for patrol in (defenses.get("pirate_patrol_ships") or []):
                remaining = [
                    nid for nid in (patrol.get("npc_character_ids") or [])
                    if nid != npc_id_str
                ]
                if npc_id_str not in (patrol.get("npc_character_ids") or []):
                    patrols.append(patrol)
                elif remaining:
                    updated = dict(patrol)
                    updated["npc_character_ids"] = remaining
                    updated["ship_count"] = len(remaining)
                    patrols.append(updated)
                # canon: empty squad rows are deleted
            defenses["pirate_patrol_ships"] = patrols
            sector.defenses = defenses
            flag_modified(sector, "defenses")

    db.flush()
    logger.info(
        "NPC %s (%s) KIA — ship %s destroyed in sector %s",
        npc.display_name, npc.id, ship_id, sector_id,
    )
    return npc
