"""NPC Movement Service — single-hop NPC moves on the player warp graph.

Canon anchors:
  - SYSTEMS/npc-scheduler.md "NPC movement": NPCs move on the same warp
    graph players use and pay the same ``turn_cost``; they have no player
    turn pools — the scheduler is the authoritative actor — but the TIME
    a movement takes is honored (~86 wall-clock seconds per turn at
    L1-equivalent regen pacing, scaled through GAME_TIME_SCALE).
  - ADR-0034: NPCs respect one-way warps (the directional helpers below
    only traverse edges a player could traverse in the same direction).
  - ADR-0060 G-V3: no quantum-jump pursuit — NPCs never use QJ here.

Scope decisions (documented, not invented):
  - NPCs traverse a player-built warp gate (ARTIFICIAL tunnel with
    created_by_player_id set) ONLY when the gate owner has granted the
    NPC's faction access (FEATURES/economy/npc-traders.md "Cross-region
    routing and warp gates": per-faction permission surface on
    WarpTunnel, default-DENY — "player infrastructure stays a player
    advantage unless the owner deliberately opens it"). The grant is a
    list of faction_codes under the access_requirements JSONB key
    ``_NPC_FACTION_GRANT_KEY`` (default absent/empty = no NPC faction may
    traverse). Natural / generator / un-owned tunnels are unrestricted.

CONCURRENCY — lock order (global convention for NPC writers):
    Player → Station → Ship → NPCCharacter → Sector (ascending sector_id)

``move_npc`` locks the NPC's SHIP row first, then both sector rows in
ascending ``sector_id`` order. This matches the combat path
(combat_service.attack_npc_ship locks Player then the NPC Ship, and
handle_npc_ship_destroyed then locks the Sector) so the two paths can
never deadlock AB-BA. Liveness is re-validated AFTER the ship lock is
held: a move that raced a kill must not resurrect presence the KIA
handler just cleaned.
"""

import logging
import uuid
from datetime import datetime, UTC
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from src.core.game_time import canonical_hours_since
from src.models.npc_character import NPCCharacter, NPCStatus, NPCArchetype
from src.models.sector import Sector
from src.models.ship import Ship
from src.models.warp_tunnel import WarpTunnel, WarpTunnelStatus
from src.services.movement_service import MovementService, _is_player_gate
from src.services.npc_spawn_service import _presence_entry, _patrol_route

logger = logging.getLogger(__name__)

# Canon (SYSTEMS/npc-scheduler.md commute feasibility): wall-clock pacing
# of NPC movement is ~86 seconds per turn at L1-equivalent regen pacing.
SECONDS_PER_TURN_L1 = 86

# FEATURES/economy/npc-traders.md "Cross-region routing and warp gates":
# NPC movement may traverse a player-built gate ONLY when the gate owner has
# granted the NPC's faction access — a per-faction permission surface on
# WarpTunnel, default-DENY. Canon describes the model but not the literal JSONB
# key name; this is the per-faction grant key the owner-side grant UI writes and
# the only place NPC traversal reads. The value is a list of granted
# faction_codes (absent / empty = no NPC faction may traverse).
_NPC_FACTION_GRANT_KEY = "npc_factions"  # NO-CANON: grant key name (default-deny)


def _npc_gate_access_granted(tunnel: WarpTunnel, faction_code: Optional[str]) -> bool:
    """True iff the player-built ``tunnel``'s owner has granted ``faction_code``
    access through its access_requirements grant key (default-DENY).

    Mirrors warp_gate_service.check_traversal_access's access_requirements read
    pattern (reqs = tunnel.access_requirements or {}, dict-guarded). Default-deny:
    a tunnel with no grant key, a non-list value, or a faction not in the granted
    list is denied; a faction with no code is always denied (it can hold no
    grant). Natural/un-owned tunnels never reach here (callers gate on
    _is_player_gate first)."""
    if not faction_code:
        return False
    reqs = tunnel.access_requirements or {}
    if not isinstance(reqs, dict):
        return False
    granted = reqs.get(_NPC_FACTION_GRANT_KEY)
    if not isinstance(granted, list):
        return False
    return faction_code in {str(g) for g in granted}

# Statuses that may never be moved by the scheduler.
_UNMOVABLE_STATUSES = (
    NPCStatus.KIA,
    NPCStatus.RESPAWNING,
    NPCStatus.RETIRED,
    NPCStatus.REASSIGNED,
)


def hop_cost(db: Session, origin_sector_id: int, dest_sector_id: int,
             ship: Optional[Ship],
             faction_code: Optional[str] = None) -> Optional[int]:
    """Turn cost of a single legal hop origin → dest, or None when no
    NPC-traversable connection exists.

    Direct warps (including reverse traversal of bidirectional rows) and
    natural warp tunnels always qualify. A player-built warp gate qualifies
    ONLY when ``faction_code`` has been granted access by the gate owner
    (default-DENY; see module docstring and ``_npc_gate_access_granted``).
    When ``faction_code`` is None (no NPC faction context) player gates are
    denied — preserving the prior "NPCs never use player infrastructure"
    behaviour for any non-NPC caller.
    """
    ms = MovementService(db)
    can_warp, warp_cost, _ = ms._check_direct_warp(
        origin_sector_id, dest_sector_id, ship
    )
    if can_warp:
        return warp_cost

    origin = db.query(Sector).filter(Sector.sector_id == origin_sector_id).first()
    dest = db.query(Sector).filter(Sector.sector_id == dest_sector_id).first()
    if origin is None or dest is None:
        return None

    tunnel = (
        db.query(WarpTunnel)
        .filter(
            WarpTunnel.origin_sector_id == origin.id,
            WarpTunnel.destination_sector_id == dest.id,
            WarpTunnel.status == WarpTunnelStatus.ACTIVE,
        )
        .first()
    )
    if tunnel is None:
        tunnel = (
            db.query(WarpTunnel)
            .filter(
                WarpTunnel.origin_sector_id == dest.id,
                WarpTunnel.destination_sector_id == origin.id,
                WarpTunnel.is_bidirectional == True,  # noqa: E712
                WarpTunnel.status == WarpTunnelStatus.ACTIVE,
            )
            .first()
        )
    if tunnel is None:
        return None

    if _is_player_gate(tunnel):
        # Player infrastructure: default-deny per-faction access gate.
        if not _npc_gate_access_granted(tunnel, faction_code):
            return None
        # Granted: player gates are a flat turn_cost (canon 0-turn gates;
        # warp_gate_service charges turn_cost as-is, no warp-capable multiplier
        # and no max(1,…) clamp — a 0-turn gate must stay 0).
        return max(0, tunnel.turn_cost or 0)

    cost = tunnel.turn_cost or 1
    if ship is not None and getattr(ship, "warp_capable", False):
        cost = max(1, int(cost * 0.8))
    return max(1, cost)


def add_npc_presence(sector: Sector, npc: NPCCharacter, ship: Ship) -> None:
    """Append the NPC's players_present entry (idempotent). Caller must
    hold the sector row lock."""
    npc_id = str(npc.id)
    players_present = [
        p for p in (sector.players_present or [])
        if p.get("player_id") != npc_id
    ]
    players_present.append(_presence_entry(npc, ship))
    sector.players_present = players_present
    flag_modified(sector, "players_present")


def remove_npc_presence(sector: Sector, npc_id: uuid.UUID) -> None:
    """Drop the NPC's players_present entry. Caller must hold the sector
    row lock."""
    npc_id_str = str(npc_id)
    sector.players_present = [
        p for p in (sector.players_present or [])
        if p.get("player_id") != npc_id_str
    ]
    flag_modified(sector, "players_present")


def _locked_sectors(db: Session, sector_ids: List[int]) -> Dict[int, Sector]:
    """Lock sector rows in ascending sector_id order (deadlock-safe both
    against other NPC movers and against the KIA path).

    WO-NPC-PRESENCE-TWIN — the NPC-side twin of movement_service.
    _update_player_presence's identity-map fix: ``move_npc`` already loads
    origin/dest UNLOCKED earlier (``hop_cost``'s ``_check_direct_warp``
    Sector reads), so a bare ``.with_for_update()`` re-read here returns the
    SAME stale cached object instead of picking up a concurrent player move's
    committed ``players_present`` write. ``.populate_existing()`` below
    closes that.

    FLUSH-FIRST, not naive: this helper has a THIRD caller besides
    ``move_npc``/``_relocate_npc`` — npc_engagement_service._place_squad
    calls this ONCE PER SQUAD OFFICER inside a for-loop, all sharing the
    SAME ``dest_sector_id`` (the offender's sector) and often the same
    ``old_sector_id`` too, with no flush between officers. On a session
    opened autoflush=False (core/database.py:19), officer N's
    ``add_npc_presence``/``remove_npc_presence`` write is still pending when
    officer N+1's call re-locks the SAME Sector row — a bare
    ``.populate_existing()`` would DISCARD officer N's presence write
    (reverting the squad to whichever officer landed last). Flushing here,
    immediately before the lock, persists any such pending pre-lock Sector
    mutation first (this call's own or an earlier same-session caller's) so
    the populate_existing() re-read observes it instead of reverting it.
    Mirrors movement_service.py's WO-MONEY-STRAGGLER-NAIVE precedent for the
    identical class of bug.
    """
    db.flush()
    locked: Dict[int, Sector] = {}
    for sid in sorted(set(sector_ids)):
        row = (
            db.query(Sector)
            .filter(Sector.sector_id == sid)
            .populate_existing()
            .with_for_update()
            .first()
        )
        if row is not None:
            locked[sid] = row
    return locked


def move_npc(
    db: Session,
    npc: NPCCharacter,
    dest_sector_id: int,
    *,
    enforce_pacing: bool = True,
) -> List[Dict[str, Any]]:
    """Move an NPC one hop to ``dest_sector_id``.

    Returns realtime event dicts for the caller to broadcast AFTER its
    commit (npc_left_sector / npc_entered_sector), or [] when the move
    did not happen (no connection, pacing window not yet elapsed, NPC no
    longer movable). Flush-only — the caller owns the transaction.
    """
    if npc.ship_id is None or npc.current_sector_id is None:
        return []
    origin_sector_id = npc.current_sector_id
    if origin_sector_id == dest_sector_id:
        return []

    # LOCK 1 — the NPC's ship row (before any sector rows; see module
    # docstring lock order).
    ship = (
        db.query(Ship)
        .filter(Ship.id == npc.ship_id)
        .with_for_update()
        .first()
    )
    if ship is None or ship.is_destroyed:
        return []

    # Liveness re-validation under the ship lock — a kill that raced this
    # move has already flipped status / cleaned presence; do not undo it.
    db.refresh(npc)
    if npc.status in _UNMOVABLE_STATUSES or npc.current_sector_id != origin_sector_id:
        return []

    # Pass the NPC's faction so a player-built gate is traversable iff the gate
    # owner granted this faction access (FEATURES/economy/npc-traders.md).
    cost = hop_cost(db, origin_sector_id, dest_sector_id, ship,
                    faction_code=npc.faction_code)
    if cost is None:
        logger.warning(
            "NPC %s: no traversable connection %s -> %s",
            npc.id, origin_sector_id, dest_sector_id,
        )
        return []

    # Canon pacing: a movement takes cost × ~86s of canonical time;
    # last_seen_at marks the previous movement/spawn.
    if enforce_pacing and npc.last_seen_at is not None:
        required_hours = cost * SECONDS_PER_TURN_L1 / 3600.0
        if canonical_hours_since(npc.last_seen_at) < required_hours:
            return []

    # LOCK 2 — both sector rows, ascending sector_id.
    sectors = _locked_sectors(db, [origin_sector_id, dest_sector_id])
    dest = sectors.get(dest_sector_id)
    if dest is None:
        return []
    origin = sectors.get(origin_sector_id)

    now = datetime.now(UTC)
    if origin is not None:
        remove_npc_presence(origin, npc.id)
    add_npc_presence(dest, npc, ship)

    npc.current_sector_id = dest_sector_id
    npc.home_region_id = npc.home_region_id or dest.region_id
    npc.last_seen_at = now
    ship.sector_id = dest_sector_id

    db.flush()

    base = {
        "npc_id": str(npc.id),
        "display_name": npc.display_name,
        "ship_id": str(ship.id),
        "ship_name": ship.name,
        "ship_type": ship.type.name,
        "is_npc": True,
        "timestamp": now.isoformat(),
    }
    return [
        {**base, "type": "npc_left_sector", "sector_id": origin_sector_id,
         "destination_sector_id": dest_sector_id},
        {**base, "type": "npc_entered_sector", "sector_id": dest_sector_id,
         "origin_sector_id": origin_sector_id},
    ]


def next_hop_toward(db: Session, origin_sector_id: int,
                    target_sector_id: int) -> Optional[int]:
    """First hop of the shortest path origin → target on the warp graph
    (BFS via MovementService), or None when unreachable."""
    if origin_sector_id == target_sector_id:
        return None
    path = MovementService(db).get_path_between_sectors(
        origin_sector_id, target_sector_id
    )
    if len(path) < 2:
        return None
    return int(path[1]["sector_id"])


# ---------------------------------------------------------------------------
# Stranded-NPC repair
# ---------------------------------------------------------------------------

def _collect_sector_ids(obj: Any, out: set) -> None:
    """Recursively gather every ``sector_id`` int and ``sectors`` list entry
    referenced anywhere in a daily_schedule JSON blob."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == "sector_id" and isinstance(v, int):
                out.add(v)
            elif k == "sectors" and isinstance(v, list):
                out.update(x for x in v if isinstance(x, int))
            else:
                _collect_sector_ids(v, out)
    elif isinstance(obj, list):
        for x in obj:
            _collect_sector_ids(x, out)


def _schedule_target_sectors(npc: NPCCharacter) -> set:
    out: set = set()
    _collect_sector_ids(npc.daily_schedule or {}, out)
    return out


def _relocate_npc(db: Session, npc: NPCCharacter, dest_sector_id: int) -> bool:
    """Teleport-repair a stranded NPC to ``dest_sector_id`` (no warp-link,
    cost or pacing — this fixes a frozen NPC, it is not a normal move).
    Same lock order as ``move_npc`` (ship row, then both sectors ascending)
    so it can't deadlock against movers or the KIA path. Returns True when
    the NPC was relocated. Flush-only; caller owns the commit."""
    if npc.ship_id is None or npc.current_sector_id is None:
        return False
    origin_id = npc.current_sector_id
    if origin_id == dest_sector_id:
        return False
    ship = db.query(Ship).filter(Ship.id == npc.ship_id).with_for_update().first()
    if ship is None or ship.is_destroyed:
        return False
    db.refresh(npc)
    if npc.status in _UNMOVABLE_STATUSES or npc.current_sector_id != origin_id:
        return False
    sectors = _locked_sectors(db, [origin_id, dest_sector_id])
    dest = sectors.get(dest_sector_id)
    if dest is None:  # destination sector must exist
        return False
    origin = sectors.get(origin_id)
    if origin is not None:
        remove_npc_presence(origin, npc.id)
    add_npc_presence(dest, npc, ship)
    npc.current_sector_id = dest_sector_id
    npc.home_region_id = npc.home_region_id or dest.region_id
    npc.last_seen_at = datetime.now(UTC)
    ship.sector_id = dest_sector_id
    db.flush()
    return True


def relocate_stranded_npcs(db: Session) -> int:
    """Find NPCs frozen because their current sector can't reach their
    schedule's target sectors (the silent ``next_hop_toward``→None no-op that
    leaves a trader stuck in the wrong region forever) and teleport-repair
    each onto one of its own route sectors so it can resume moving.

    An NPC is considered fine (skipped) when its current sector IS a target,
    or when ANY target is reachable from it (it's simply en route). Idempotent:
    a healthy NPC is never touched. Flush-only; caller owns the commit."""
    movable = (
        db.query(NPCCharacter)
        .filter(
            NPCCharacter.status.notin_(_UNMOVABLE_STATUSES),
            NPCCharacter.ship_id.isnot(None),
            NPCCharacter.current_sector_id.isnot(None),
        )
        .all()
    )
    msvc = MovementService(db)
    relocated = 0
    for npc in movable:
        targets = _schedule_target_sectors(npc)
        if not targets or npc.current_sector_id in targets:
            continue  # no schedule, or already on its route
        cur = npc.current_sector_id
        reachable = any(
            len(msvc.get_path_between_sectors(cur, t)) >= 2
            for t in sorted(targets)
        )
        if reachable:
            continue  # en route — it will arrive on its own
        dest = min(targets)  # deterministic anchor on its own route
        if _relocate_npc(db, npc, dest):
            relocated += 1
            logger.info("Relocated stranded NPC %s: sector %s -> %s",
                        npc.id, cur, dest)
    return relocated


def disperse_law_patrols(db: Session) -> int:
    """Spread LAW_ENFORCEMENT NPCs across their region instead of swarming the
    roster's single host sector (24 Sentinels anchored to one sector + its two
    neighbours made ~6-8 pile into every sector around the host). Each LAW NPC
    gets a DETERMINISTIC scattered patrol anchor (seeded by its id) somewhere in
    its region, a small local patrol loop around that anchor, and is relocated
    there. Deterministic + idempotent: an NPC already on its scattered anchor is
    left untouched, so this self-heals re-clustered respawns each startup without
    churn. Pirates are intentionally NOT dispersed (their clustering is canon —
    holdings/strongholds). Flush-only; caller owns the commit."""
    law = (
        db.query(NPCCharacter)
        .filter(
            NPCCharacter.archetype == NPCArchetype.LAW_ENFORCEMENT,
            NPCCharacter.status.notin_(_UNMOVABLE_STATUSES),
            NPCCharacter.ship_id.isnot(None),
        )
        .all()
    )
    if not law:
        return 0
    region_sectors: Dict[Any, List[int]] = {}
    dispersed = 0
    for npc in law:
        region_id = npc.home_region_id
        if region_id is None:
            cur = db.query(Sector).filter(Sector.sector_id == npc.current_sector_id).first()
            region_id = cur.region_id if cur else None
        if region_id is None:
            continue
        if region_id not in region_sectors:
            region_sectors[region_id] = sorted(
                sid for (sid,) in db.query(Sector.sector_id)
                .filter(Sector.region_id == region_id).all()
            )
        sids = region_sectors[region_id]
        if not sids:
            continue
        # Deterministic per-NPC anchor (stable across restarts → idempotent).
        h = int(str(npc.id).replace("-", "")[:12], 16)
        anchor = sids[h % len(sids)]
        route = _patrol_route(db, anchor)
        cur_route = (
            ((npc.daily_schedule or {}).get("blocks") or [{}])[0]
            .get("location_ref") or {}
        ).get("sectors")
        if cur_route == route and npc.current_sector_id == anchor:
            continue  # already dispersed to its anchor
        # Relocate FIRST — _relocate_npc does db.refresh(npc), which would
        # discard an unflushed daily_schedule change; set the scattered route
        # AFTER so it survives (the caller commits).
        if npc.current_sector_id != anchor:
            _relocate_npc(db, npc, anchor)
        npc.daily_schedule = {
            "timezone": "utc",
            "shift_offset_hours": 0,
            "blocks": [{
                "start_minute": 0, "end_minute": 1440,
                "activity": "patrol", "location_type": "patrol_route",
                "location_ref": {"sectors": route, "minutes_per_sector": 240},
            }],
        }
        npc.home_region_id = npc.home_region_id or region_id
        dispersed += 1
    return dispersed
