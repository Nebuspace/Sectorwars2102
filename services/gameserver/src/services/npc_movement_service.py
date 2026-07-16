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
from datetime import UTC, datetime
from typing import Any, Dict, List, Optional, Set

from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from src.core.game_time import canonical_hours_since
from src.models.npc_character import NPCArchetype, NPCCharacter, NPCStatus
from src.models.region import Region
from src.models.sector import Sector
from src.models.ship import Ship
from src.models.warp_tunnel import WarpTunnel, WarpTunnelStatus
from src.services.movement_service import MovementService, _is_player_gate
from src.services.npc_spawn_service import _patrol_route, _presence_entry

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

# Federation Marshals on the capital-watch route share a ≤3-waypoint loop
# (capital + up to 2 neighbours). Even phase assignment in Loop A keeps
# ~1/N at each waypoint; size 3 is the floor that still covers all three
# slots so Sector 1 is not left empty by stagger alone. police-forces.md:
# starter cluster (sectors 1..fedspace) is densest Fed coverage.
CAPITAL_WATCH_SQUAD_SIZE = 3
FEDERATION_FACTION = "terran_federation"


def _region_capital_global_id(region: Region, sids: List[int]) -> int:
    """Map region-local ``capital_sector_number`` onto a global sector_id.

    ``sids`` must be the region's sector_ids sorted ascending. Local 1 is the
    first sector in that list (Terran Space → global 1; offset regions →
    min(sector_id) + local - 1).
    """
    if not sids:
        raise ValueError("region has no sectors")
    local = int(region.capital_sector_number or 1)
    candidate = sids[0] + local - 1
    if candidate in sids:
        return candidate
    # Degenerate / legacy: fall back to the region's lowest sector.
    return sids[0]


def disperse_law_patrols(db: Session) -> int:
    """Spread LAW_ENFORCEMENT NPCs across their region instead of swarming the
    roster's single host sector (24 Sentinels anchored to one sector + its two
    neighbours made ~6-8 pile into every sector around the host). Each LAW NPC
    gets a DETERMINISTIC scattered patrol anchor (seeded by its id) somewhere in
    its region, a small local patrol loop around that anchor, and is relocated
    there. Deterministic + idempotent: an NPC already on its scattered anchor is
    left untouched, so this self-heals re-clustered respawns each startup without
    churn. Pirates are intentionally NOT dispersed (their clustering is canon —
    holdings/strongholds).

    Terran Space Federation Marshals: the first ``CAPITAL_WATCH_SQUAD_SIZE``
    (stable id sort) are anchored on the capital (Sector 1) so the starter
    sector always has rotating named Fed coverage; the rest disperse across
    the Federation-Zone third of the region. Flush-only; caller owns the commit.
    """
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

    # Group by home/current region so we can reserve a capital-watch squad
    # per Terran Space region before scattering the rest.
    by_region: Dict[Any, List[NPCCharacter]] = {}
    region_sectors: Dict[Any, List[int]] = {}
    regions: Dict[Any, Region] = {}

    for npc in law:
        region_id = npc.home_region_id
        if region_id is None:
            cur = db.query(Sector).filter(Sector.sector_id == npc.current_sector_id).first()
            region_id = cur.region_id if cur else None
        if region_id is None:
            continue
        by_region.setdefault(region_id, []).append(npc)
        if region_id not in region_sectors:
            region_sectors[region_id] = sorted(
                sid for (sid,) in db.query(Sector.sector_id)
                .filter(Sector.region_id == region_id).all()
            )
            region = db.query(Region).filter(Region.id == region_id).first()
            if region is not None:
                regions[region_id] = region

    dispersed = 0
    for region_id, members in by_region.items():
        sids = region_sectors.get(region_id) or []
        if not sids:
            continue
        region = regions.get(region_id)
        is_terran = bool(region is not None and region.is_terran_space)

        capital_sid: Optional[int] = None
        capital_watch_ids: Set[str] = set()
        if is_terran and region is not None:
            capital_sid = _region_capital_global_id(region, sids)
            # Prefer Federation Marshals for the capital watch (not Sentinels
            # if any ever share a region). Stable id sort → idempotent.
            fed = sorted(
                (
                    n for n in members
                    if (n.faction_code or "") == FEDERATION_FACTION
                ),
                key=lambda n: str(n.id),
            )
            capital_watch_ids = {str(n.id) for n in fed[:CAPITAL_WATCH_SQUAD_SIZE]}

        # Core Fed Zone pool for non-watch Terran LE (same 0.33 fraction
        # player-owned Fed Zone uses). Nexus / other regions: full list.
        if is_terran:
            # Lazy import: npc_engagement_service.py:69 imports this whole
            # module at its own top level, so a module-level import of this
            # single constant back here would deadlock whichever of the two
            # modules the process happens to import first (circular import
            # -- confirmed via VERIFY-FIRST for DRIFT-combat-patrol-entry-
            # dispatch: `import npc_engagement_service` first raises
            # ImportError on FEDERATION_ZONE_FRACTION, and does NOT
            # self-heal on retry). Deferring to call time breaks the cycle
            # cleanly -- both modules are always fully initialized by the
            # time this function actually runs.
            from src.services.npc_engagement_service import FEDERATION_ZONE_FRACTION
            core_n = max(1, int(len(sids) * FEDERATION_ZONE_FRACTION))
            scatter_pool = sids[:core_n] or sids
        else:
            scatter_pool = sids

        for npc in members:
            if str(npc.id) in capital_watch_ids and capital_sid is not None:
                anchor = capital_sid
                route = _patrol_route(db, capital_sid)
                capital_watch = True
            else:
                h = int(str(npc.id).replace("-", "")[:12], 16)
                anchor = scatter_pool[h % len(scatter_pool)]
                route = _patrol_route(db, anchor)
                capital_watch = False

            loc_ref: Dict[str, Any] = {
                "sectors": route,
                "minutes_per_sector": 240,
            }
            if capital_watch:
                loc_ref["capital_watch"] = True

            cur_ref = (
                ((npc.daily_schedule or {}).get("blocks") or [{}])[0]
                .get("location_ref") or {}
            )
            cur_route = cur_ref.get("sectors") if isinstance(cur_ref, dict) else None
            already = (
                cur_route == route
                and npc.current_sector_id == anchor
                and bool(cur_ref.get("capital_watch")) == capital_watch
            )
            if already:
                continue

            if npc.current_sector_id != anchor:
                _relocate_npc(db, npc, anchor)
            npc.daily_schedule = {
                "timezone": "utc",
                "shift_offset_hours": 0,
                "blocks": [{
                    "start_minute": 0, "end_minute": 1440,
                    "activity": "patrol", "location_type": "patrol_route",
                    "location_ref": loc_ref,
                }],
            }
            npc.home_region_id = npc.home_region_id or region_id
            dispersed += 1
    return dispersed


def ensure_capital_fed_presence(db: Session) -> int:
    """Hard floor: every Terran Space capital has ≥1 ON_DUTY Federation Marshal.

    Capital-watch squad members rotate through the capital + neighbours under
    Loop A phase stagger, but hops can briefly empty Sector 1. When that
    happens, teleport-repair the nearest capital-watch Marshal onto the
    capital so the cockpit always sees named Fed coverage. Returns the number
    of NPCs relocated. Flush-only; caller owns the commit.
    """
    filled = 0
    terran_regions = (
        db.query(Region)
        .all()
    )
    for region in terran_regions:
        if not region.is_terran_space:
            continue
        sids = sorted(
            sid for (sid,) in db.query(Sector.sector_id)
            .filter(Sector.region_id == region.id).all()
        )
        if not sids:
            continue
        capital_sid = _region_capital_global_id(region, sids)

        on_duty_here = (
            db.query(NPCCharacter)
            .filter(
                NPCCharacter.archetype == NPCArchetype.LAW_ENFORCEMENT,
                NPCCharacter.faction_code == FEDERATION_FACTION,
                NPCCharacter.status == NPCStatus.ON_DUTY,
                NPCCharacter.current_sector_id == capital_sid,
                NPCCharacter.ship_id.isnot(None),
            )
            .count()
        )
        if on_duty_here >= 1:
            continue

        # Prefer capital-watch Marshals already assigned to this capital's route.
        candidates = (
            db.query(NPCCharacter)
            .filter(
                NPCCharacter.archetype == NPCArchetype.LAW_ENFORCEMENT,
                NPCCharacter.faction_code == FEDERATION_FACTION,
                NPCCharacter.status == NPCStatus.ON_DUTY,
                NPCCharacter.ship_id.isnot(None),
                NPCCharacter.current_sector_id.isnot(None),
                NPCCharacter.current_sector_id != capital_sid,
            )
            .all()
        )
        # Stay inside this Terran region (home_region or current sector).
        region_candidates: List[NPCCharacter] = []
        for npc in candidates:
            if npc.home_region_id == region.id:
                region_candidates.append(npc)
                continue
            cur = db.query(Sector).filter(Sector.sector_id == npc.current_sector_id).first()
            if cur is not None and cur.region_id == region.id:
                region_candidates.append(npc)

        def _is_capital_watch(npc: NPCCharacter) -> bool:
            blocks = (npc.daily_schedule or {}).get("blocks") or []
            if not blocks:
                return False
            ref = blocks[0].get("location_ref") or {}
            return bool(isinstance(ref, dict) and ref.get("capital_watch"))

        region_candidates.sort(
            key=lambda n: (0 if _is_capital_watch(n) else 1, str(n.id))
        )
        if not region_candidates:
            continue
        pick = region_candidates[0]
        try:
            with db.begin_nested():
                if not _relocate_npc(db, pick, capital_sid):
                    continue
                # Keep them on the capital route so the next Loop A tick resumes
                # the rotating watch instead of scattering them again.
                route = _patrol_route(db, capital_sid)
                pick.daily_schedule = {
                    "timezone": "utc",
                    "shift_offset_hours": 0,
                    "blocks": [{
                        "start_minute": 0, "end_minute": 1440,
                        "activity": "patrol", "location_type": "patrol_route",
                        "location_ref": {
                            "sectors": route,
                            "minutes_per_sector": 240,
                            "capital_watch": True,
                        },
                    }],
                }
                pick.home_region_id = pick.home_region_id or region.id
                filled += 1
                logger.info(
                    "Capital Fed presence: relocated %s (%s) → sector %s",
                    pick.title or pick.display_name or pick.id,
                    pick.id,
                    capital_sid,
                )
        except Exception:
            logger.exception(
                "Capital Fed presence: relocate failed for %s", pick.id
            )
    return filled


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
    from sqlalchemy import text

    if npc.ship_id is None or npc.current_sector_id is None:
        return []
    origin_sector_id = npc.current_sector_id
    if origin_sector_id == dest_sector_id:
        return []

    # Fail fast under row-lock contention (same discipline as player move /
    # colonist transfer). Without this, a stuck idle-in-transaction peer
    # freezes the whole Loop A tick for minutes and NPCs look motionless.
    # Loop A wraps each drive in a SAVEPOINT so a lock timeout only aborts
    # this NPC's hop — not every earlier hop in the same tick.
    try:
        db.execute(text("SET LOCAL lock_timeout = '3s'"))
    except Exception:
        logger.debug("move_npc: could not set lock_timeout", exc_info=True)

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
    from sqlalchemy import text

    if npc.ship_id is None or npc.current_sector_id is None:
        return False
    origin_id = npc.current_sector_id
    if origin_id == dest_sector_id:
        return False
    try:
        db.execute(text("SET LOCAL lock_timeout = '3s'"))
    except Exception:
        logger.debug("relocate_npc: could not set lock_timeout", exc_info=True)
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
    a healthy NPC is never touched. Flush-only; caller owns the commit.

    Destination pick: NEVER ``min(targets)`` — colonist couriers all list the
    population-hub capital (Sector 1) as a load stop, so min() dumped every
    stranded trader onto Sector 1 (31-NPC capital pile-up). Prefer non-capital
    home-region stops and hash-spread across the pool.
    """
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
    # Cache sector_id → region_id and per-region capital for dest picking.
    sector_region: Dict[int, Any] = {}
    region_capital: Dict[Any, int] = {}
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
        dest = _pick_stranded_dest(
            db, npc, targets, sector_region, region_capital,
        )
        if dest is None or dest == cur:
            continue
        if _relocate_npc(db, npc, dest):
            relocated += 1
            logger.info("Relocated stranded NPC %s: sector %s -> %s",
                        npc.id, cur, dest)
    return relocated


def _pick_stranded_dest(
    db: Session,
    npc: NPCCharacter,
    targets: set,
    sector_region: Dict[int, Any],
    region_capital: Dict[Any, int],
) -> Optional[int]:
    """Choose a repair teleport target that does not collapse onto the capital.

    Prefer stops in the NPC's home region; among those, prefer non-capital
    stops (colonist routes always include the hub). Hash the NPC id across
    the remaining pool so many stranded NPCs don't all land on the same
    sector.
    """
    def _region_of(sid: int) -> Any:
        if sid in sector_region:
            return sector_region[sid]
        row = db.query(Sector.region_id).filter(Sector.sector_id == sid).first()
        rid = row[0] if row else None
        sector_region[sid] = rid
        return rid

    def _capital_of(rid: Any) -> Optional[int]:
        if rid is None:
            return None
        if rid in region_capital:
            return region_capital[rid]
        region = db.query(Region).filter(Region.id == rid).first()
        if region is None:
            region_capital[rid] = None  # type: ignore[assignment]
            return None
        sids = sorted(
            s for (s,) in db.query(Sector.sector_id)
            .filter(Sector.region_id == rid).all()
        )
        if not sids:
            region_capital[rid] = None  # type: ignore[assignment]
            return None
        cap = _region_capital_global_id(region, sids)
        region_capital[rid] = cap
        return cap

    home = npc.home_region_id
    home_targets = [t for t in targets if _region_of(t) == home] if home else []
    pool = home_targets if home_targets else list(targets)
    capital = _capital_of(home) if home else None
    # LAW capital-watch Marshals may legitimately repair onto the capital;
    # everyone else spreads onto non-hub stops when available.
    is_capital_watch = False
    blocks = (npc.daily_schedule or {}).get("blocks") or []
    if blocks:
        ref = blocks[0].get("location_ref") or {}
        is_capital_watch = bool(isinstance(ref, dict) and ref.get("capital_watch"))
    if capital is not None and not is_capital_watch:
        non_hub = [t for t in pool if t != capital]
        if non_hub:
            pool = non_hub
    if not pool:
        return None
    ordered = sorted(pool)
    h = int(str(npc.id).replace("-", "")[:12], 16)
    return ordered[h % len(ordered)]


def clear_capital_trader_pileup(db: Session, *, soft_cap: int = 4) -> int:
    """One-shot / boot hygiene: if the Terran capital has more than ``soft_cap``
    traders (stranded-relocator historically dumped them all on Sector 1),
    teleport the excess onto other stops on their own routes. Leaves LAW
    capital-watch Marshals alone. Flush-only; caller owns the commit.
    """
    moved = 0
    for region in db.query(Region).all():
        if not region.is_terran_space:
            continue
        sids = sorted(
            s for (s,) in db.query(Sector.sector_id)
            .filter(Sector.region_id == region.id).all()
        )
        if not sids:
            continue
        capital = _region_capital_global_id(region, sids)
        traders = (
            db.query(NPCCharacter)
            .filter(
                NPCCharacter.archetype == NPCArchetype.TRADER,
                NPCCharacter.status.notin_(_UNMOVABLE_STATUSES),
                NPCCharacter.current_sector_id == capital,
                NPCCharacter.ship_id.isnot(None),
            )
            .order_by(NPCCharacter.id)
            .all()
        )
        if len(traders) <= soft_cap:
            continue
        excess = traders[soft_cap:]
        sector_region: Dict[int, Any] = {}
        region_capital: Dict[Any, int] = {region.id: capital}
        for npc in excess:
            targets = _schedule_target_sectors(npc)
            # Force non-capital dest even if currently sitting on capital.
            targets = {t for t in targets if t != capital} or targets
            dest = _pick_stranded_dest(
                db, npc, targets, sector_region, region_capital,
            )
            if dest is None or dest == capital:
                continue
            if _relocate_npc(db, npc, dest):
                moved += 1
                logger.info(
                    "Capital pileup: moved trader %s off sector %s -> %s",
                    npc.id, capital, dest,
                )
    return moved
