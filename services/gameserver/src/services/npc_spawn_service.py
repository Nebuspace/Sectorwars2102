"""NPC Spawn Service — static NPC materialization from BANG rosters (v1).

Materializes NPCCharacter + Ship rows from the BANG rosters stashed on
``Galaxy.bang_snapshot.regions[*].universe.npcRosters`` and surfaces them
in sector presence so the COMMS panel and the combat target list see them
with zero extra plumbing.

Canon anchors:
  - DATA_MODELS/npcs.md — NPCCharacter schema + "Patrol-squad linkage"
  - SYSTEMS/npc-scheduler.md — Loop B (spawn recipe; this is a one-shot,
    degenerate Loop B) and "KIA processing" steps 1-4
  - ADR-0047 — ``Sector.defenses.pirate_patrol_ships`` JSON shape
  - FEATURES/gameplay/police-forces.md — Federation Marshals / Nexus
    Sentinels, Interdictor hulls, police squad JSONB shape

V1 scope (documented subset, not invention — the NPC scheduler/lifecycle
docs are explicitly Design-only):
  - Pirate CAPTAINS plus the police forces (Federation Marshals /
    Marshal-Captains on Marshal Interdictors; Nexus Sentinels /
    Sentinel-Captains on Sentinel Interdictors). Pirate enforcers are
    held back for a later slice; lords are held back because the BANG
    snapshot's 13 lords contradict canon ADR-0047's "Stronghold-tier
    only, 1-2 per region" (conflict flagged to Max).
  - Static NPCs: no movement, no schedules, no NPC-initiated combat,
    no respawn (Loop B), no NPCDeathLog, no bounty hooks. The one
    reputation hook in v1 lives in combat_service.attack_npc_ship
    (Marshal kill → −250 Terran Federation rep, police-forces.md:75).
"""

import logging
import uuid
from datetime import datetime, UTC
from typing import Any, Dict, List, NamedTuple, Optional, Tuple

from sqlalchemy import func
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from src.core.game_time import scaled_deadline
from src.models.faction import Faction, FactionType
from src.models.galaxy import Galaxy
from src.models.npc_character import (
    NPCCharacter,
    NPCArchetype,
    NPCDeathLog,
    NPCLifecycleStage,
    NPCStatus,
)
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
MERCHANT_CAPTAIN_KIND = "merchant_captain"

# TRADER roster tunables. Canon is silent on both (flagged for
# DECISIONS.md): trader counts are operator-tunable per region, and the
# wallet seed funds the first cargo load.
TRADERS_PER_REGION = 6
TRADER_STARTING_CREDITS = 25_000

# Gameserver-side trader name pool (BANG emits no trader rosters; names
# are flavor, operator-replaceable — not invented canon numbers).
TRADER_NAME_POOL: Tuple[str, ...] = (
    "Mira Voss", "Dex Okonkwo", "Sana Albrecht", "Joaquin Reyes",
    "Petra Lindqvist", "Tomas Ferreira", "Anneke De Vries", "Ravi Chandran",
    "Yuki Tanahashi", "Bram Kowalczyk", "Leila Haddad", "Oskar Jensen",
)

PIRATE_PATROL_DEFENSES_KEY = "pirate_patrol_ships"
# Police squads land under their own defenses key. Canon-divergence note:
# police-forces.md "Patrol-squad row coherence" says the existing
# ``defenses.patrol_ships`` shape "is extended", but patrol_ships is
# already shape-conflicted in code — admin.py reads it as an INT while
# sector.py defaults it to a list — so landing dict squad rows there
# would break admin pages. A dedicated key mirrors the ADR-0047
# pirate_patrol_ships precedent instead; divergence FLAGGED for the docs
# repo, not silently resolved.
POLICE_PATROL_DEFENSES_KEY = "police_patrol_ships"
PATROL_DEFENSES_KEYS: Tuple[str, ...] = (
    PIRATE_PATROL_DEFENSES_KEY,
    POLICE_PATROL_DEFENSES_KEY,
)

# Canon squad-row field (police-forces.md squad JSONB shape).
POLICE_WANTED_THRESHOLD = -500

# Canon: SYSTEMS/npc-scheduler.md "KIA processing" step 2 —
# respawn_eligible_at = now + 7 days (faction-tunable cooldown).
KIA_RESPAWN_COOLDOWN_HOURS = 7 * 24

# ADR-0063 N-D2: respawn-permitted archetypes return as the SAME
# identity after a 15-minute cooldown (career and reputation persist).
# Canon grants this to "most named pirates, some trader archetypes";
# v1 grants it to pirates — traders join in the trader slice.
RESPAWN_COOLDOWN_MINUTES = 15
RESPAWN_PERMITTED_ARCHETYPES = frozenset({NPCArchetype.HOSTILE_RAIDER})

# Patrol cycle pacing (canon FEATURES/gameplay/police-forces.md: Marshal
# squads cycle ~4h per sector, Sentinels ~3h; pirates canon-silent —
# mirroring the Marshal cadence, flagged).
PATROL_MINUTES_PER_SECTOR: Dict[str, int] = {
    PIRATE_CAPTAIN_KIND: 240,
    "federation_marshal": 240,
    "marshal_captain": 240,
    "nexus_sentinel": 180,
    "sentinel_captain": 180,
}


class KindConfig(NamedTuple):
    """Spawn recipe for one BANG roster ``kind`` value."""

    archetype: NPCArchetype
    # DATA_MODELS/npcs.md — title is rendered before the name in UI
    # ("Marshal", "Sentinel-Captain").
    title: str
    ship_type: ShipType
    # str.format(name=...) template for the piloted hull's Ship.name.
    ship_name_format: str
    # Canon squad_kind enumeration value (police-forces.md): captains
    # fold into their force's squad kind — only ``federation_marshal``
    # and ``nexus_sentinel`` exist for police.
    squad_kind: str
    defenses_key: str
    # Fallback when the roster carries no factionCode.
    default_faction_code: str
    # BANG roster ids restart per kind (dev: federation_marshal id=1
    # collides with pirate roster id=1), so the NEW police kinds embed
    # kind in bang_roster_ref. Pirate refs keep the original kind-blind
    # format — changing it would break idempotency with rows already
    # materialized under the old refs.
    kind_in_roster_ref: bool
    # Police squad rows carry the canon wanted_threshold /
    # scheduled_clear_at fields; pirate rows keep the ADR-0047 shape.
    is_police: bool
    # Patrol archetypes join a defenses squad row; independent actors
    # (TRADER merchant captains) never do.
    joins_squad: bool = True


KIND_CONFIG: Dict[str, KindConfig] = {
    PIRATE_CAPTAIN_KIND: KindConfig(
        archetype=NPCArchetype.HOSTILE_RAIDER,
        title=PIRATE_CAPTAIN_TITLE,
        # PLACEHOLDER: canon defines no pirate hull stats (police-forces.md
        # shows the fully-numeric Interdictor pattern this should follow) —
        # pirate captains reuse LIGHT_FREIGHTER specs until canon supplies
        # pirate numbers.
        ship_type=ShipType.LIGHT_FREIGHTER,
        ship_name_format="Captain {name}'s Marauder",
        squad_kind=PIRATE_CAPTAIN_KIND,
        defenses_key=PIRATE_PATROL_DEFENSES_KEY,
        default_faction_code="pirates",
        kind_in_roster_ref=False,
        is_police=False,
    ),
    "federation_marshal": KindConfig(
        archetype=NPCArchetype.LAW_ENFORCEMENT,
        title="Marshal",
        ship_type=ShipType.NPC_MARSHAL_INTERDICTOR,
        # PLACEHOLDER: canon gives no police ship-naming convention —
        # mirrors the pirate "Captain X's Marauder" pattern.
        ship_name_format="Marshal {name}'s Interdictor",
        squad_kind="federation_marshal",
        defenses_key=POLICE_PATROL_DEFENSES_KEY,
        default_faction_code="terran_federation",
        kind_in_roster_ref=True,
        is_police=True,
    ),
    # Captains fly the SAME Interdictor hull as regulars — canon confirms
    # via "28 docked Interdictors" for 24 Sentinels + 4 Captains and the
    # 1:1 permanent ship assignment (police-forces.md).
    "marshal_captain": KindConfig(
        archetype=NPCArchetype.LAW_ENFORCEMENT,
        title="Marshal-Captain",
        ship_type=ShipType.NPC_MARSHAL_INTERDICTOR,
        # PLACEHOLDER: canon gives no police ship-naming convention —
        # mirrors the pirate "Captain X's Marauder" pattern.
        ship_name_format="Marshal-Captain {name}'s Interdictor",
        squad_kind="federation_marshal",
        defenses_key=POLICE_PATROL_DEFENSES_KEY,
        default_faction_code="terran_federation",
        kind_in_roster_ref=True,
        is_police=True,
    ),
    "nexus_sentinel": KindConfig(
        archetype=NPCArchetype.LAW_ENFORCEMENT,
        title="Sentinel",
        ship_type=ShipType.NPC_SENTINEL_INTERDICTOR,
        # PLACEHOLDER: canon gives no police ship-naming convention —
        # mirrors the pirate "Captain X's Marauder" pattern.
        ship_name_format="Sentinel {name}'s Interdictor",
        squad_kind="nexus_sentinel",
        defenses_key=POLICE_PATROL_DEFENSES_KEY,
        default_faction_code="galactic_concord",
        kind_in_roster_ref=True,
        is_police=True,
    ),
    "sentinel_captain": KindConfig(
        archetype=NPCArchetype.LAW_ENFORCEMENT,
        title="Sentinel-Captain",
        ship_type=ShipType.NPC_SENTINEL_INTERDICTOR,
        # PLACEHOLDER: canon gives no police ship-naming convention —
        # mirrors the pirate "Captain X's Marauder" pattern.
        ship_name_format="Sentinel-Captain {name}'s Interdictor",
        squad_kind="nexus_sentinel",
        defenses_key=POLICE_PATROL_DEFENSES_KEY,
        default_faction_code="galactic_concord",
        kind_in_roster_ref=True,
        is_police=True,
    ),
    # TRADER archetype "NPC regular players" (SYSTEMS/npc-lifecycle.md:
    # "NPC merchant captain — travel route between 2–4 stations on a
    # schedule"). Standard merchant hull (no is_npc_only); rosters are
    # gameserver-seeded — BANG emits no trader kind today.
    MERCHANT_CAPTAIN_KIND: KindConfig(
        archetype=NPCArchetype.TRADER,
        title="Trader",
        ship_type=ShipType.CARGO_HAULER,
        # PLACEHOLDER naming convention, mirrors the pirate pattern.
        ship_name_format="Trader {name}'s Hauler",
        squad_kind=MERCHANT_CAPTAIN_KIND,
        defenses_key=PIRATE_PATROL_DEFENSES_KEY,  # unused: joins_squad=False
        default_faction_code="merchants",
        kind_in_roster_ref=True,
        is_police=False,
        joins_squad=False,
    ),
}


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
    """Construct an NPC-piloted Ship mirroring ShipService.create_ship
    defaults. Hull stats come entirely from the kind's ShipSpecification
    (see KIND_CONFIG for the per-kind hull mapping and the pirate-hull
    placeholder note)."""
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
        # Canon renders title before name (DATA_MODELS/npcs.md).
        "username": npc.display_name,
        "ship_id": str(ship.id),
        "ship_name": ship.name,
        "ship_type": ship.type.name,
        "team_id": None,
        "arrived_at": datetime.now(UTC).isoformat(),
        "is_npc": True,
    }


def _ensure_federation_faction(db: Session) -> Faction:
    """Get-or-create the Terran Federation faction row, idempotent by
    faction_type.

    Canon (police-forces.md "Faction registration"): the Federation Police
    are the enforcement arm of the existing **Terran Federation** — and the
    Marshal-kill reputation hook (−250, police-forces.md) needs this row to
    exist. The factions table ships empty: the only existing seeder
    (auth/admin.py create_default_factions) is dead code with zero call
    sites AND names this faction "United Space Federation" vs canon's
    "Terran Federation" — docs-vs-code discrepancy FLAGGED, not followed;
    the canon name is used here. An existing FEDERATION-typed row (however
    named) is left untouched.

    The Galactic Concord (Sentinel force) is NOT seeded: its CONCORD
    FactionType is Design-only (police-forces.md "Faction registration")
    and adding the enum value is out of scope for this slice.
    """
    faction = (
        db.query(Faction)
        .filter(Faction.faction_type == FactionType.FEDERATION)
        .first()
    )
    if faction is None:
        faction = Faction(
            name="Terran Federation",
            faction_type=FactionType.FEDERATION,
            description=(
                "The dominant galactic governing authority. Operates the "
                "Federation Police — the Marshal Service — across Terran "
                "Space and the Federation Zones of player regions."
            ),
        )
        db.add(faction)
        db.flush()
        logger.info("Seeded Terran Federation faction row (%s)", faction.id)
    return faction


def materialize_from_bang(db: Session, galaxy: Galaxy) -> Dict[str, Any]:
    """One-shot, degenerate Loop B (SYSTEMS/npc-scheduler.md): spawn the
    pirate-captain and police rosters from the galaxy's BANG snapshot.

    Per NPC, two rows (Ship + NPCCharacter) and two JSONB presence
    writes on the host sector (players_present entry; one shared squad
    row per roster under the kind's defenses key — pirate_patrol_ships
    or police_patrol_ships, see KIND_CONFIG).

    Idempotent per roster: a roster is skipped when NPCCharacter rows with
    its ``bang_roster_ref`` already exist — including KIA rows, so a
    killed NPC does not silently respawn on re-run (respawn is Loop B
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
        # Counts EVERY spawned NPC (pirate captains + police officers) —
        # key name frozen for spawn_npcs.py output compatibility.
        "captains_spawned": 0,
        "warnings": [],
    }

    if not regions:
        stats["warnings"].append(
            f"galaxy {galaxy.id} has no bang_snapshot.regions — nothing to spawn"
        )
        return stats

    # The Marshal-kill reputation hook (combat_service) targets the
    # Terran Federation faction row — guarantee it exists before any
    # LAW_ENFORCEMENT NPC can be spawned (and therefore killed).
    _ensure_federation_faction(db)

    # One spec fetch per distinct hull across all spawnable kinds.
    hull_types = {cfg.ship_type for cfg in KIND_CONFIG.values()}
    specs: Dict[ShipType, ShipSpecification] = {
        s.type: s
        for s in (
            db.query(ShipSpecification)
            .filter(ShipSpecification.type.in_(hull_types))
            .all()
        )
    }
    if not specs:
        raise ValueError(
            "No ShipSpecification rows for any NPC hull — seed ship specs "
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
            if any(str(r.get("kind", "")) in KIND_CONFIG for r in rosters):
                stats["warnings"].append(
                    f"region {region_type}: no region_id in snapshot or no "
                    f"sectors in DB — cannot derive offset; skipping its rosters"
                )
            continue

        for roster in rosters:
            kind = str(roster.get("kind", ""))
            cfg = KIND_CONFIG.get(kind)
            if cfg is None:
                continue
            stats["rosters_seen"] += 1
            # Galaxy-scoped so two galaxies sharing region types/roster ids
            # never collide on the idempotency marker. Police refs embed
            # the kind because BANG roster ids restart per kind (see
            # KindConfig.kind_in_roster_ref); pirate refs keep the
            # original kind-blind format for idempotency with rows
            # already materialized under it.
            if cfg.kind_in_roster_ref:
                roster_ref = f"{galaxy.id}:{region_type}:{kind}:{roster.get('id')}"
            else:
                roster_ref = f"{galaxy.id}:{region_type}:{roster.get('id')}"

            spec = specs.get(cfg.ship_type)
            if spec is None:
                stats["warnings"].append(
                    f"roster {roster_ref}: no ShipSpecification for "
                    f"{cfg.ship_type.name} — run migrations + boot seeder "
                    f"first; skipping"
                )
                continue

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

            faction_code = str(roster.get("factionCode", cfg.default_faction_code))
            now = datetime.now(UTC)
            squad_npc_ids: List[str] = []
            presence_entries: List[Dict[str, Any]] = []

            for i in range(target_count):
                base_name = name_pool[i % len(name_pool)]
                # Round-robin wrap reuses pool names — suffix a roman
                # numeral generation (II, III, ...) so usernames stay
                # unique per sector.
                generation = i // len(name_pool) + 1
                npc_name = (
                    base_name if generation == 1
                    else f"{base_name} {_roman(generation)}"
                )
                ship = _build_npc_ship(
                    spec,
                    name=cfg.ship_name_format.format(name=npc_name),
                    sector_id=global_sector_id,
                )
                db.add(ship)
                db.flush()  # surface ship.id for the FK + presence entry

                npc = NPCCharacter(
                    name=npc_name,
                    title=cfg.title,
                    faction_code=faction_code,
                    archetype=cfg.archetype,
                    status=NPCStatus.ON_DUTY,
                    current_sector_id=global_sector_id,
                    ship_id=ship.id,
                    bang_roster_ref=roster_ref,
                    # ADR-0063 N-F1: first officer is the designated
                    # primary authority; the rest are backups who can
                    # zero-gap promote when the primary falls.
                    duty_role=(
                        f"primary_{kind}" if i == 0 else f"backup_{kind}"
                    ),
                    spawned_at=now,
                    last_seen_at=now,
                )
                db.add(npc)
                db.flush()

                squad_npc_ids.append(str(npc.id))
                presence_entries.append(_presence_entry(npc, ship))
                stats["captains_spawned"] += 1

            # Presence write 1: players_present (movement_service shape) —
            # makes the NPCs visible in COMMS and the combat target list.
            players_present = list(sector.players_present or [])
            known_ids = {p.get("player_id") for p in players_present}
            players_present.extend(
                e for e in presence_entries if e["player_id"] not in known_ids
            )
            sector.players_present = players_present
            flag_modified(sector, "players_present")

            # Presence write 2: squad row under the kind's defenses key —
            # ADR-0047 shape for pirates (holding_id omitted: the
            # PirateHolding table is Design-only, canon gap); canon
            # police-forces.md squad shape for police (adds
            # wanted_threshold / scheduled_clear_at).
            squad_row: Dict[str, Any] = {
                "patrol_id": str(uuid.uuid4()),
                "faction_code": faction_code,
                "squad_kind": cfg.squad_kind,
                "npc_character_ids": squad_npc_ids,
                "ship_count": len(squad_npc_ids),
                "deployed_at": now.isoformat(),
            }
            if cfg.is_police:
                squad_row["wanted_threshold"] = POLICE_WANTED_THRESHOLD
                squad_row["scheduled_clear_at"] = None
            defenses = dict(sector.defenses or {})
            patrols = list(defenses.get(cfg.defenses_key) or [])
            patrols.append(squad_row)
            defenses[cfg.defenses_key] = patrols
            sector.defenses = defenses
            flag_modified(sector, "defenses")

            stats["rosters_spawned"] += 1
            logger.info(
                "Spawned %d %s NPC(s) at sector %d (roster %s)",
                len(squad_npc_ids), kind, global_sector_id, roster_ref,
            )

    db.flush()
    return stats


# ---------------------------------------------------------------------------
# Living NPC System bootstrap — roster seeding + schedule backfill
# ---------------------------------------------------------------------------

def _patrol_route(db: Session, host_sector_id: int) -> List[int]:
    """Host sector plus up to two adjacent same-region sectors — the v1
    patrol loop. Canon defines patrol CYCLE pacing but not route
    composition (worldgen patrol routes are Design-only); the adjacent
    ring is the smallest canon-compatible route, flagged for the docs
    repo."""
    from src.models.sector import sector_warps

    host = db.query(Sector).filter(Sector.sector_id == host_sector_id).first()
    if host is None:
        return [host_sector_id]

    neighbour_pks = set()
    for row in db.execute(
        sector_warps.select().where(sector_warps.c.source_sector_id == host.id)
    ).fetchall():
        neighbour_pks.add(row.destination_sector_id)
    for row in db.execute(
        sector_warps.select().where(
            sector_warps.c.destination_sector_id == host.id,
            sector_warps.c.is_bidirectional == True,  # noqa: E712
        )
    ).fetchall():
        neighbour_pks.add(row.source_sector_id)

    route = [host_sector_id]
    if neighbour_pks:
        neighbours = (
            db.query(Sector)
            .filter(
                Sector.id.in_(neighbour_pks),
                Sector.region_id == host.region_id,
            )
            .order_by(Sector.sector_id)
            .limit(2)
            .all()
        )
        route.extend(s.sector_id for s in neighbours)
    return route


def _schedule_template(db: Session, kind: str, host_sector_id: int) -> Dict[str, Any]:
    """V1 daily_schedule: one all-day patrol block over the host's
    adjacent ring. The full canon daily texture (sleep / dine / train
    blocks) needs the lodging slice for sleep locations — deferred,
    divergence documented in SYSTEMS/npc-lifecycle.md terms."""
    return {
        "timezone": "utc",
        "shift_offset_hours": 0,
        "blocks": [
            {
                "start_minute": 0,
                "end_minute": 1440,
                "activity": "patrol",
                "location_type": "patrol_route",
                "location_ref": {
                    "sectors": _patrol_route(db, host_sector_id),
                    "minutes_per_sector": PATROL_MINUTES_PER_SECTOR.get(kind, 240),
                },
            }
        ],
    }


def seed_rosters_from_bang(db: Session, galaxy: Galaxy) -> Dict[str, Any]:
    """Materialize NPCRoster rows from the BANG snapshot (idempotent by
    bang_roster_ref — the same ref format materialize_from_bang uses, so
    rosters adopt the NPCs already spawned under it)."""
    from src.models.npc_character import NPCRoster

    snapshot = galaxy.bang_snapshot or {}
    regions = snapshot.get("regions") or {}
    stats: Dict[str, Any] = {"rosters_created": 0, "rosters_existing": 0, "warnings": []}

    offsets = _region_offset_map(db, regions)

    for region_type in REGION_ORDER:
        region_snapshot = regions.get(region_type)
        if not isinstance(region_snapshot, dict):
            continue
        universe = region_snapshot.get("universe") or {}
        rosters = universe.get("npcRosters") or []
        region_id = region_snapshot.get("region_id")
        offset = offsets.get(region_type)
        if offset is None or not region_id:
            if any(str(r.get("kind", "")) in KIND_CONFIG for r in rosters):
                stats["warnings"].append(
                    f"region {region_type}: no offset/region_id — rosters not seeded"
                )
            continue

        for roster in rosters:
            kind = str(roster.get("kind", ""))
            cfg = KIND_CONFIG.get(kind)
            if cfg is None:
                continue
            if cfg.kind_in_roster_ref:
                roster_ref = f"{galaxy.id}:{region_type}:{kind}:{roster.get('id')}"
            else:
                roster_ref = f"{galaxy.id}:{region_type}:{roster.get('id')}"

            existing = (
                db.query(NPCRoster)
                .filter(NPCRoster.bang_roster_ref == roster_ref)
                .first()
            )
            if existing is not None:
                stats["rosters_existing"] += 1
                continue

            host_local = int(roster.get("hostSectorId", 0))
            global_sector_id = host_local + offset
            target_count = int(roster.get("targetCount", 0))
            name_pool = [str(n) for n in (roster.get("namePool") or [])]
            if target_count <= 0:
                stats["warnings"].append(
                    f"roster {roster_ref}: targetCount {target_count} — not seeded"
                )
                continue

            db.add(NPCRoster(
                region_id=region_id,
                faction_code=str(roster.get("factionCode", cfg.default_faction_code)),
                role=kind,
                default_archetype=cfg.archetype,
                schedule_template=_schedule_template(db, kind, global_sector_id),
                default_lodging_id=roster.get("defaultLodgingId"),
                default_lodging_type=roster.get("defaultLodgingType"),
                target_count=target_count,
                name_pool={"names": name_pool},
                host_sector_id=global_sector_id,
                bang_roster_ref=roster_ref,
            ))
            stats["rosters_created"] += 1

    db.flush()
    return stats


def seed_trader_rosters(db: Session, galaxy: Galaxy) -> Dict[str, Any]:
    """Seed merchant_captain NPCRoster rows — one per region that has at
    least two trading stations. Gameserver-side because BANG emits no
    trader kind today; counts default to TRADERS_PER_REGION
    (operator-tunable by editing the roster row's target_count).
    Idempotent by bang_roster_ref. Routes/schedules are generated
    PER NPC at spawn (Loop B), so the roster's schedule_template stays
    empty."""
    from src.models.npc_character import NPCRoster
    from src.models.region import Region
    from src.models.station import Station

    stats: Dict[str, Any] = {
        "trader_rosters_created": 0,
        "trader_rosters_existing": 0,
        "warnings": [],
    }

    for region in db.query(Region).all():
        roster_ref = f"{galaxy.id}:trader:{region.id}"
        existing = (
            db.query(NPCRoster)
            .filter(NPCRoster.bang_roster_ref == roster_ref)
            .first()
        )
        if existing is not None:
            stats["trader_rosters_existing"] += 1
            continue

        region_sector_ids = {
            row[0]
            for row in db.query(Sector.sector_id)
            .filter(Sector.region_id == region.id)
            .all()
        }
        stations = [
            s for s in db.query(Station).all()
            if s.sector_id in region_sector_ids and (s.commodities or {})
        ]
        if len(stations) < 2:
            continue  # nothing to trade between — no roster

        db.add(NPCRoster(
            region_id=region.id,
            faction_code="merchants",
            role=MERCHANT_CAPTAIN_KIND,
            default_archetype=NPCArchetype.TRADER,
            schedule_template={},
            target_count=TRADERS_PER_REGION,
            name_pool={"names": list(TRADER_NAME_POOL)},
            host_sector_id=stations[0].sector_id,
            bang_roster_ref=roster_ref,
        ))
        stats["trader_rosters_created"] += 1

    db.flush()
    return stats


def backfill_npc_schedules(db: Session) -> int:
    """Give pre-runtime NPC rows (empty daily_schedule) their roster's
    schedule template + home region, and assign duty roles (ADR-0063
    N-F1: one primary per roster, the rest backups) to rows that lack
    one. Idempotent — rows with a schedule are untouched by the
    schedule pass, rows with a duty_role by the role pass. Returns the
    number of rows touched."""
    from sqlalchemy import cast, String as SAString

    from src.models.npc_character import NPCRoster

    backfilled = 0
    for roster in db.query(NPCRoster).all():
        npcs = (
            db.query(NPCCharacter)
            .filter(
                NPCCharacter.bang_roster_ref == roster.bang_roster_ref,
                cast(NPCCharacter.daily_schedule, SAString) == '{}',
            )
            .all()
        )
        for npc in npcs:
            npc.daily_schedule = dict(roster.schedule_template or {})
            npc.home_region_id = npc.home_region_id or roster.region_id
            backfilled += 1

        # Duty roles, independent of the schedule pass. Traders are
        # independent actors — no primary/backup chain.
        if roster.role == MERCHANT_CAPTAIN_KIND:
            continue
        roster_npcs = (
            db.query(NPCCharacter)
            .filter(
                NPCCharacter.bang_roster_ref == roster.bang_roster_ref,
                NPCCharacter.status != NPCStatus.KIA,
            )
            .order_by(NPCCharacter.spawned_at)
            .all()
        )
        has_primary = any(
            (n.duty_role or "").startswith("primary") for n in roster_npcs
        )
        for npc in roster_npcs:
            if npc.duty_role:
                continue
            npc.duty_role = (
                f"backup_{roster.role}" if has_primary
                else f"primary_{roster.role}"
            )
            has_primary = True
            backfilled += 1
    if backfilled:
        db.flush()
    return backfilled


def backfill_orphan_npc_schedules(db: Session) -> int:
    """Repair NPCs that have an empty ``daily_schedule`` and no roster to
    inherit one from.

    ``backfill_npc_schedules`` only touches NPCs whose ``bang_roster_ref``
    matches an existing ``NPCRoster`` row. Any NPC present in the DB with an
    empty schedule and no matching roster (the drifted-galaxy case observed
    on dev: 80 spawned NPCs, zero rosters — provenance predates this code)
    is therefore never scheduled, so Loop A can never resolve a block for it
    and it freezes in PATROL forever, making the world read as dead.

    This gives each such NPC a patrol-route schedule derived from its
    CURRENT sector (the same ``_schedule_template`` shape rosters use), so
    the scheduler can drive it. Idempotent: only schedulable, non-KIA rows
    with an empty schedule and a known sector are touched. Returns the
    number repaired.
    """
    from sqlalchemy import cast, String as SAString

    # Kind only selects PATROL_MINUTES_PER_SECTOR pacing (default 240), so a
    # coarse archetype->kind map is sufficient; unknown kinds fall back.
    archetype_kind = {
        NPCArchetype.LAW_ENFORCEMENT: "federation_marshal",
        NPCArchetype.HOSTILE_RAIDER: PIRATE_CAPTAIN_KIND,
    }

    repaired = 0
    npcs = (
        db.query(NPCCharacter)
        .filter(
            NPCCharacter.status.in_((NPCStatus.ON_DUTY, NPCStatus.OFF_DUTY)),
            NPCCharacter.lifecycle_stage.notin_(
                (NPCLifecycleStage.KIA, NPCLifecycleStage.RETIRED)
            ),
            # TRADERS are excluded: they get a generated trade route at spawn
            # (Loop B). A trader should never be handed a patrol route — that
            # would freeze its economy behaviour.
            NPCCharacter.archetype != NPCArchetype.TRADER,
            cast(NPCCharacter.daily_schedule, SAString) == '{}',
            NPCCharacter.current_sector_id.isnot(None),
        )
        .all()
    )
    for npc in npcs:
        kind = archetype_kind.get(npc.archetype, "federation_marshal")
        npc.daily_schedule = _schedule_template(db, kind, npc.current_sector_id)
        repaired += 1
    if repaired:
        db.flush()
    return repaired


def bootstrap_galaxy(db: Session, galaxy: Galaxy) -> Dict[str, Any]:
    """One idempotent entry point for the Living NPC System bootstrap:
    materialize NPCs from the BANG snapshot, seed NPCRoster rows, and
    backfill schedules onto pre-runtime NPC rows. Flush-only; the caller
    (spawn_npcs.py / admin tooling) commits."""
    stats = materialize_from_bang(db, galaxy)
    roster_stats = seed_rosters_from_bang(db, galaxy)
    stats.update(roster_stats)
    trader_stats = seed_trader_rosters(db, galaxy)
    stats.update({k: v for k, v in trader_stats.items() if k != "warnings"})
    stats["warnings"].extend(trader_stats.get("warnings") or [])
    stats["schedules_backfilled"] = backfill_npc_schedules(db)
    # Catch NPCs with no roster to inherit from (drifted snapshot) so they
    # still get a patrol schedule and are not frozen.
    stats["orphan_schedules_backfilled"] = backfill_orphan_npc_schedules(db)
    return stats


def bootstrap_region(db: Session, region_id: uuid.UUID) -> Dict[str, Any]:
    """Region-scoped convenience wrapper: bootstrap the galaxy whose BANG
    snapshot contains ``region_id``. The underlying steps are all
    idempotent, so re-running for a sibling region is harmless."""
    for galaxy in db.query(Galaxy).all():
        regions = (galaxy.bang_snapshot or {}).get("regions") or {}
        for region_snapshot in regions.values():
            if (
                isinstance(region_snapshot, dict)
                and str(region_snapshot.get("region_id")) == str(region_id)
            ):
                return bootstrap_galaxy(db, galaxy)
    return {"warnings": [f"no galaxy snapshot contains region {region_id}"]}


def handle_npc_ship_destroyed(
    db: Session,
    ship_id: uuid.UUID,
    killed_by_player_id: Optional[uuid.UUID] = None,
    combat_log_id: Optional[uuid.UUID] = None,
    destruction_cause: str = "combat",
) -> Optional[NPCCharacter]:
    """KIA processing per SYSTEMS/npc-scheduler.md + ADR-0063.

    Called by the combat worker (lazy import; the extra kwargs default
    to None so legacy positional calls keep working) when a ship is
    destroyed. No-op (returns None) when the ship has no NPC pilot, so
    the worker can call it unconditionally.

    Steps implemented:
      2. ADR-0063 N-D2 split — respawn-permitted archetypes (pirates)
         go status=RESPAWNING with a 15-canonical-minute cooldown and
         keep their lifecycle stage (same identity returns, career
         persists; Loop B resurrects them). Everyone else is
         permanently KIA: lifecycle_stage=KIA, no respawn date —
         Loop B fills the slot with a fresh reduced-stat recruit.
      3. NPCDeathLog row (killer, sector, region, combat log, cause).
      4. squad row updated to drop the dead NPC; deleted when empty —
         plus removal of the NPC's players_present entry.
      N-F1 zero-gap promotion: when a primary-duty officer dies, an
         on-duty backup from the same roster promotes to primary
         immediately (role_history appended); the recruit fill then
         lands in the vacated backup slot.

    Deferred (documented, not dropped silently): step 5 ship_id detach
    (canonical destruction handler owns the ship row), and N-D3
    handoff-invalidation (shift handoffs land in Phase 4). Reputation
    hooks live in the caller: combat_service inspects the returned
    NPCCharacter and applies the Marshal-kill Federation rep delta
    (police-forces.md) — this handler stays rep-free.

    Rows are never deleted — a permanently-dead named NPC's
    NPCCharacter row persists per canon.
    """
    npc = (
        db.query(NPCCharacter)
        .filter(NPCCharacter.ship_id == ship_id)
        .first()
    )
    if npc is None:
        return None
    if npc.status in (NPCStatus.KIA, NPCStatus.RESPAWNING):
        return npc  # already processed

    sector_id = npc.current_sector_id
    now = datetime.now(UTC)

    if npc.archetype in RESPAWN_PERMITTED_ARCHETYPES:
        npc.status = NPCStatus.RESPAWNING
        npc.respawn_eligible_at = scaled_deadline(
            RESPAWN_COOLDOWN_MINUTES / 60.0, start=now
        )
    else:
        npc.status = NPCStatus.KIA
        npc.lifecycle_stage = NPCLifecycleStage.KIA
        npc.respawn_eligible_at = None
    npc.current_sector_id = None
    npc.destroyed_at = now
    npc.last_seen_at = now

    # Step 3 — death audit row (skipped only when the NPC had no sector,
    # which cannot happen for combat kills).
    if sector_id is not None:
        db.add(NPCDeathLog(
            npc_id=npc.id,
            killed_by_player_id=killed_by_player_id,
            sector_id=sector_id,
            home_region_id=npc.home_region_id,
            combat_log_id=combat_log_id,
            destruction_cause=destruction_cause,
            killed_at=now,
        ))

    # N-F1 — zero-gap promotion: the on-duty backup steps up the moment
    # the primary falls. The recruit fill (Loop B) then targets the
    # vacated backup slot.
    if (npc.duty_role or "").startswith("primary") and npc.bang_roster_ref:
        backup = (
            db.query(NPCCharacter)
            .filter(
                NPCCharacter.bang_roster_ref == npc.bang_roster_ref,
                NPCCharacter.status == NPCStatus.ON_DUTY,
                NPCCharacter.duty_role.like("backup%"),
            )
            .order_by(NPCCharacter.spawned_at)
            .first()
        )
        if backup is not None:
            old_role = backup.duty_role
            backup.duty_role = npc.duty_role
            backup.promotion_pending_at = None
            history = list(backup.role_history or [])
            history.append({
                "from": old_role,
                "to": npc.duty_role,
                "at": now.isoformat(),
                "reason": f"zero-gap promotion after {npc.display_name} KIA",
            })
            backup.role_history = history
            flag_modified(backup, "role_history")
            logger.info(
                "N-F1 zero-gap promotion: %s %s -> %s after %s KIA",
                backup.display_name, old_role, npc.duty_role, npc.display_name,
            )

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
            for defenses_key in PATROL_DEFENSES_KEYS:
                if defenses_key not in defenses:
                    continue
                patrols: List[Dict[str, Any]] = []
                for patrol in (defenses.get(defenses_key) or []):
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
                defenses[defenses_key] = patrols
            sector.defenses = defenses
            flag_modified(sector, "defenses")

    db.flush()
    logger.info(
        "NPC %s (%s) KIA — ship %s destroyed in sector %s",
        npc.display_name, npc.id, ship_id, sector_id,
    )

    # Best-effort npc_kia realtime event. Only possible when an event
    # loop is running (async route context); sync contexts (worker
    # threads, CLI) rely on polled players_present, which the cleanup
    # above already updated.
    if sector_id is not None:
        try:
            import asyncio

            from src.services.websocket_service import connection_manager

            asyncio.get_running_loop().create_task(
                connection_manager.broadcast_to_sector(sector_id, {
                    "type": "npc_kia",
                    "sector_id": sector_id,
                    "npc_id": str(npc.id),
                    "display_name": npc.display_name,
                    "is_npc": True,
                    "timestamp": now.isoformat(),
                })
            )
        except RuntimeError:
            pass  # no running loop — polled presence covers it
        except Exception:
            logger.exception("npc_kia broadcast failed (non-fatal)")

    return npc
