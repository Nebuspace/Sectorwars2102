"""NPC Engagement Routing — police response per ADR-0042 + police-forces.md.

``route_engagement`` runs synchronously inside the offense transaction
(combat / warp-gate deploy): it picks the squad immediately — named
officers atomically flip to ENGAGED_PENDING_ARRIVAL — and inserts a
durable ``PendingEngagement`` watcher row. The squad ARRIVES (placed in
the offender's CURRENT sector, status ENGAGED) only when the offender
has spent 2 more turns on the cumulative ``lifetime_turns_spent`` clock;
the 1-minute ``sweep_pending_engagements`` discharges, cancels, and
expires watchers.

Canon mechanics implemented here:
  - Jurisdiction split (police-forces.md): Federation Police cover all
    of Terran Space and the Federation Zone (first 33% of sector
    numbers) of player regions; Nexus Sentinels cover the Central Nexus
    only. The forces never share jurisdiction or intelligence.
  - Routing (npc-scheduler.md + ADR-0063 N-I1): nearest eligible
    on-duty officer by warp-graph hop distance — Marshals capped at 5
    hops, Captains 8; no quantum-jump pursuit (ADR-0060 G-V3).
  - Squad tiers (police-forces.md): Federation 1 / 2 / 3 by rep tier,
    Captain joins at the most-negative tier and for any direct attack
    on a Marshal; Sentinels always 4 (6 after a Sentinel kill), Captain
    leads Capital-sector breaches.
  - 5-turn per-offense-type cooldown; jurisdiction-exit cancel (−25
    evade-arrest rep per police-forces.md § flee); 24h canonical
    expiry; 5–15 min no-officer grace (then immediate arrival, no extra
    2-turn layer — ADR-0042).

Documented v1 deferrals (flagged, not invented):
  - Anonymous Defender escorts for High/Public-Enemy Federation tiers
    (only NAMED officers dispatch — escort hulls are a later slice).
  - Post-arrival pursuit (chasing a player who moves within
    jurisdiction) — the squad releases when the offender leaves the
    encounter sector.
  - Surrender (police-forces.md "Engagement outcomes" #1 — a pre-combat
    choice to decline and pay a fine). WO-CMB-NPC-INITIATED-1 (Max
    ruling, 2026-07-10) supersedes this bullet's old text ("combat with
    the arrived squad is player-initiated PvE via the existing attack
    path"): npc_combat_initiation_service.initiate_npc_combat now has
    the squad attack FIRST once ARRIVED/ENGAGED (lane B calls it from
    the PendingEngagement ARRIVED path above). Surrender itself is
    still NOT built — no negotiation prompt — so the only escape once
    combat starts is the existing defender combat-escape roll,
    unchanged.
  - Contraband scans and stolen-ship reports (their source systems are
    Design-only).

Lock order: Player → Ship → NPCCharacter → Sector (ascending), matching
npc_movement_service and the combat path.
"""

import logging
import random
import uuid
from collections import deque
from datetime import datetime, UTC
from typing import Any, Dict, List, Optional, Set, Tuple

from sqlalchemy.orm import Session

from src.core.game_time import scaled_deadline
from src.models.npc_character import NPCCharacter, NPCArchetype, NPCStatus
from src.models.pending_engagement import PendingEngagement, EngagementStatus
from src.models.player import Player
from src.models.region import Region
from src.models.sector import Sector, sector_warps
from src.models.ship import Ship
from src.models.warp_tunnel import WarpTunnel, WarpTunnelStatus
from src.services import npc_movement_service

logger = logging.getLogger(__name__)

# ADR-0042 numbers.
ARRIVAL_TURN_DELAY = 2
OFFENSE_COOLDOWN_TURNS = 5
ENGAGEMENT_EXPIRY_HOURS = 24
GRACE_MIN_MINUTES = 5
GRACE_MAX_MINUTES = 15

# ADR-0063 N-I1 routing caps (warp-graph hops; no QJ pursuit).
MARSHAL_MAX_HOPS = 5
CAPTAIN_MAX_HOPS = 8

# police-forces.md: Federation Zone = first 33% of a region's sectors.
FEDERATION_ZONE_FRACTION = 0.33

FEDERATION = "federation"
SENTINEL = "sentinel"

_FORCE_FACTION = {FEDERATION: "terran_federation", SENTINEL: "galactic_concord"}


# ---------------------------------------------------------------------------
# Jurisdiction
# ---------------------------------------------------------------------------

def jurisdiction_of(db: Session, sector: Sector) -> Optional[str]:
    """Which force polices this sector: 'federation', 'sentinel', or
    None (Border/Frontier zones of player regions are unpoliced)."""
    if sector is None or sector.region_id is None:
        return None
    region = db.query(Region).filter(Region.id == sector.region_id).first()
    if region is None:
        return None
    if region.is_central_nexus:
        return SENTINEL
    if region.is_terran_space:
        return FEDERATION
    # Player-owned: Federation Zone = first 33% of region-local numbers.
    bounds = (
        db.query(Sector.sector_id)
        .filter(Sector.region_id == sector.region_id)
        .order_by(Sector.sector_id)
        .all()
    )
    if not bounds:
        return None
    min_id = bounds[0][0]
    total = len(bounds)
    local_position = sector.sector_id - min_id + 1
    if local_position <= int(FEDERATION_ZONE_FRACTION * total):
        return FEDERATION
    return None


# ---------------------------------------------------------------------------
# Hop distances (BFS on the player warp graph, capped)
# ---------------------------------------------------------------------------

def _hop_distances(db: Session, origin_sector_id: int, max_hops: int) -> Dict[int, int]:
    """{global sector_id: hops} for everything within ``max_hops`` of the
    origin. Direct warps both ways (bidirectional rows) + natural ACTIVE
    tunnels; player gates excluded (consistent with NPC movement)."""
    origin = db.query(Sector).filter(Sector.sector_id == origin_sector_id).first()
    if origin is None:
        return {}

    distances: Dict[uuid.UUID, int] = {origin.id: 0}
    sector_ids: Dict[uuid.UUID, int] = {origin.id: origin.sector_id}
    queue = deque([(origin.id, 0)])

    while queue:
        pk, dist = queue.popleft()
        if dist >= max_hops:
            continue
        neighbours: Set[uuid.UUID] = set()
        for row in db.execute(
            sector_warps.select().where(sector_warps.c.source_sector_id == pk)
        ).fetchall():
            neighbours.add(row.destination_sector_id)
        for row in db.execute(
            sector_warps.select().where(
                sector_warps.c.destination_sector_id == pk,
                sector_warps.c.is_bidirectional == True,  # noqa: E712
            )
        ).fetchall():
            neighbours.add(row.source_sector_id)
        for tunnel in (
            db.query(WarpTunnel)
            .filter(
                WarpTunnel.origin_sector_id == pk,
                WarpTunnel.status == WarpTunnelStatus.ACTIVE,
                WarpTunnel.created_by_player_id.is_(None),
            )
            .all()
        ):
            neighbours.add(tunnel.destination_sector_id)
        for tunnel in (
            db.query(WarpTunnel)
            .filter(
                WarpTunnel.destination_sector_id == pk,
                WarpTunnel.is_bidirectional == True,  # noqa: E712
                WarpTunnel.status == WarpTunnelStatus.ACTIVE,
                WarpTunnel.created_by_player_id.is_(None),
            )
            .all()
        ):
            neighbours.add(tunnel.origin_sector_id)

        for npk in neighbours:
            if npk in distances:
                continue
            distances[npk] = dist + 1
            queue.append((npk, dist + 1))

    # Map PKs to global sector ids in one query.
    rows = (
        db.query(Sector.id, Sector.sector_id)
        .filter(Sector.id.in_(distances.keys()))
        .all()
    )
    return {global_id: distances[pk] for pk, global_id in rows}


# ---------------------------------------------------------------------------
# Squad selection
# ---------------------------------------------------------------------------

def _is_captain(npc: NPCCharacter) -> bool:
    return "Captain" in (npc.title or "")


def _federation_squad_size(player: Player) -> Tuple[int, bool]:
    """(named officer count, captain joins) per police-forces.md threat
    tiers, mapped onto this codebase's REPUTATION_TIERS bands (code wins
    on numbers; canon's 7-name scale is flagged for the docs repo)."""
    rep = player.personal_reputation or 0
    if rep <= -750:   # Villain ≈ canon Public Enemy
        return 3, True
    if rep <= -500:   # Criminal ≈ canon Pirate/Criminal (High)
        return 3, False
    if rep <= -250:   # Outlaw ≈ canon Smuggler/Outlaw (Medium)
        return 2, False
    return 1, False   # Suspicious/Questionable (Low)


def _pick_squad(
    db: Session,
    jurisdiction: str,
    region_id,
    offense_sector_id: int,
    size: int,
    include_captain: bool,
) -> List[NPCCharacter]:
    """Nearest eligible on-duty officers by hop distance, respecting the
    per-role routing caps. Returns [] when nobody is in range."""
    candidates = (
        db.query(NPCCharacter)
        .filter(
            NPCCharacter.archetype == NPCArchetype.LAW_ENFORCEMENT,
            NPCCharacter.status == NPCStatus.ON_DUTY,
            NPCCharacter.faction_code == _FORCE_FACTION[jurisdiction],
            NPCCharacter.home_region_id == region_id,
            NPCCharacter.current_sector_id.isnot(None),
        )
        .all()
    )
    if not candidates:
        return []

    distances = _hop_distances(db, offense_sector_id, CAPTAIN_MAX_HOPS)

    def in_range(npc: NPCCharacter) -> Optional[int]:
        hops = distances.get(npc.current_sector_id)
        if hops is None:
            return None
        cap = CAPTAIN_MAX_HOPS if _is_captain(npc) else MARSHAL_MAX_HOPS
        return hops if hops <= cap else None

    ranked = sorted(
        ((hops, npc) for npc in candidates if (hops := in_range(npc)) is not None),
        key=lambda pair: (pair[0], str(pair[1].id)),
    )
    if not ranked:
        return []

    # Captain semantics (police-forces.md): Federation Captains join ON
    # TOP of the tier's Marshals ("personally respond to Public Enemy
    # tier"); the Sentinel Captain leads WITHIN the fixed squad of 4
    # ("the Captain plus the 3 nearest Sentinels").
    squad: List[NPCCharacter] = []
    if include_captain:
        captain = next((npc for _, npc in ranked if _is_captain(npc)), None)
        if captain is not None:
            squad.append(captain)
            if jurisdiction == FEDERATION:
                size += 1
    for _, npc in ranked:
        if len(squad) >= size:
            break
        if npc not in squad:
            squad.append(npc)
    return squad


# ---------------------------------------------------------------------------
# route_engagement — synchronous offense handler
# ---------------------------------------------------------------------------

def route_engagement(
    db: Session,
    player: Player,
    offense_type: str,
    offense_sector: Sector,
    *,
    squad_size_override: Optional[int] = None,
    include_captain: bool = False,
) -> Optional[PendingEngagement]:
    """Dispatch the police response to an offense. Returns the inserted
    PendingEngagement (or None when out of jurisdiction / on cooldown).
    Flush-only — rides the offense transaction; never raises into the
    caller (offense handling must not break combat resolution)."""
    try:
        return _route_engagement_inner(
            db, player, offense_type, offense_sector,
            squad_size_override=squad_size_override,
            include_captain=include_captain,
        )
    except Exception:
        logger.exception("route_engagement failed for %s/%s", player.id, offense_type)
        return None


def _route_engagement_inner(
    db: Session,
    player: Player,
    offense_type: str,
    offense_sector: Sector,
    *,
    squad_size_override: Optional[int],
    include_captain: bool,
) -> Optional[PendingEngagement]:
    jurisdiction = jurisdiction_of(db, offense_sector)
    if jurisdiction is None:
        return None

    turn_count = player.lifetime_turns_spent or 0

    # Per-type 5-turn cooldown (ADR-0042): offenses inside the window
    # still fire their reputation hooks but dispatch no new squad.
    recent = (
        db.query(PendingEngagement)
        .filter(
            PendingEngagement.player_id == player.id,
            PendingEngagement.offense_type == offense_type,
            PendingEngagement.offense_at_turn_count > turn_count - OFFENSE_COOLDOWN_TURNS,
        )
        .first()
    )
    if recent is not None:
        return None

    if jurisdiction == SENTINEL:
        size = squad_size_override or 4  # always 4; 6 after a Sentinel kill
    else:
        size, tier_captain = _federation_squad_size(player)
        include_captain = include_captain or tier_captain
        if squad_size_override:
            size = squad_size_override

    squad = _pick_squad(
        db, jurisdiction, offense_sector.region_id,
        offense_sector.sector_id, size, include_captain,
    )

    now = datetime.now(UTC)
    engagement = PendingEngagement(
        player_id=player.id,
        offense_type=offense_type,
        jurisdiction=jurisdiction,
        offense_sector_id=offense_sector.sector_id,
        region_id=offense_sector.region_id,
        npc_squad_ids=[str(npc.id) for npc in squad],
        offense_at_turn_count=turn_count,
        arrival_turn_threshold=(turn_count + ARRIVAL_TURN_DELAY) if squad else None,
        status=EngagementStatus.PENDING,
        grace_expires_at=None if squad else scaled_deadline(
            random.randint(GRACE_MIN_MINUTES, GRACE_MAX_MINUTES) / 60.0, start=now
        ),
        expires_at=scaled_deadline(ENGAGEMENT_EXPIRY_HOURS, start=now),
    )
    db.add(engagement)

    # Atomic commitment: a pending officer can't be picked twice.
    for npc in squad:
        npc.status = NPCStatus.ENGAGED_PENDING_ARRIVAL
        npc.last_seen_at = now

    db.flush()
    if squad:
        logger.info(
            "Engagement dispatched: %s -> player %s (%s, %d officers, arrival at turn %d)",
            offense_type, player.id, jurisdiction, len(squad),
            engagement.arrival_turn_threshold,
        )
    else:
        logger.info(
            "Engagement %s -> player %s: no eligible officer — %s grace window",
            offense_type, player.id, jurisdiction,
        )
    return engagement


def engagement_summary(engagement: Optional[PendingEngagement],
                       db: Optional[Session] = None) -> Optional[Dict[str, Any]]:
    """Small dict for combat-response payloads ('Marshal Vance is en
    route — 2 turns to arrival')."""
    if engagement is None:
        return None
    names: List[str] = []
    if db is not None and engagement.npc_squad_ids:
        ids = [uuid.UUID(s) for s in engagement.npc_squad_ids]
        names = [
            npc.display_name
            for npc in db.query(NPCCharacter).filter(NPCCharacter.id.in_(ids)).all()
        ]
    return {
        "jurisdiction": engagement.jurisdiction,
        "offense_type": engagement.offense_type,
        "squad": names,
        "turns_to_arrival": ARRIVAL_TURN_DELAY if engagement.npc_squad_ids else None,
        "grace_window": engagement.grace_expires_at.isoformat()
        if engagement.grace_expires_at else None,
    }


# ---------------------------------------------------------------------------
# Arrival / release primitives
# ---------------------------------------------------------------------------

def _place_squad(db: Session, engagement: PendingEngagement,
                 dest_sector_id: int) -> List[Dict[str, Any]]:
    """Place the committed squad in the offender's current sector
    (canonical 'the police chase you' arrival — ADR-0042 step 6)."""
    events: List[Dict[str, Any]] = []
    now = datetime.now(UTC)
    for npc_id_str in (engagement.npc_squad_ids or []):
        npc = (
            db.query(NPCCharacter)
            .filter(NPCCharacter.id == uuid.UUID(npc_id_str))
            .first()
        )
        if npc is None or npc.status != NPCStatus.ENGAGED_PENDING_ARRIVAL:
            continue
        ship = None
        if npc.ship_id is not None:
            # Lock order: Ship before Sector (module docstring).
            ship = (
                db.query(Ship)
                .filter(Ship.id == npc.ship_id)
                .with_for_update()
                .first()
            )
        if ship is None or ship.is_destroyed:
            npc.status = NPCStatus.ON_DUTY
            continue

        old_sector_id = npc.current_sector_id
        sector_ids = [s for s in {old_sector_id, dest_sector_id} if s is not None]
        locked = npc_movement_service._locked_sectors(db, sector_ids)
        old_sector = locked.get(old_sector_id) if old_sector_id is not None else None
        dest = locked.get(dest_sector_id)
        if dest is None:
            npc.status = NPCStatus.ON_DUTY
            continue

        if old_sector is not None:
            npc_movement_service.remove_npc_presence(old_sector, npc.id)
        npc_movement_service.add_npc_presence(dest, npc, ship)
        npc.current_sector_id = dest_sector_id
        npc.status = NPCStatus.ENGAGED
        npc.last_seen_at = now
        ship.sector_id = dest_sector_id

        events.append({
            "type": "npc_engaged",
            "sector_id": dest_sector_id,
            "npc_id": str(npc.id),
            "display_name": npc.display_name,
            "ship_id": str(ship.id),
            "ship_name": ship.name,
            "ship_type": ship.type.name,
            "is_npc": True,
            "timestamp": now.isoformat(),
        })
    return events


def _release_squad(db: Session, engagement: PendingEngagement) -> None:
    """Return committed officers to duty (cancel / expiry / resolution).
    Officers already KIA or re-tasked are left alone."""
    now = datetime.now(UTC)
    for npc_id_str in (engagement.npc_squad_ids or []):
        npc = (
            db.query(NPCCharacter)
            .filter(NPCCharacter.id == uuid.UUID(npc_id_str))
            .first()
        )
        if npc is None:
            continue
        if npc.status in (NPCStatus.ENGAGED_PENDING_ARRIVAL, NPCStatus.ENGAGED):
            npc.status = NPCStatus.ON_DUTY
            npc.last_seen_at = now


# ---------------------------------------------------------------------------
# 1-minute sweep
# ---------------------------------------------------------------------------

def sweep_pending_engagements(db: Session) -> List[Dict[str, Any]]:
    """ADR-0042 sweep: (a) discharge watchers whose threshold is
    reached, (b) cancel jurisdiction exits (−25 evade-arrest), (c)
    expire stale rows, (d) re-route no-officer grace windows, and (e)
    resolve arrived encounters the offender has left."""
    events: List[Dict[str, Any]] = []
    now = datetime.now(UTC)

    # PER-ROW ISOLATION (WO-B1/B2): mirror the bounty-accrual / planetary-
    # advance sweeps' discipline so one bad engagement cannot lose the rest
    # of the tick's transitions, and no batch-wide row lock blocks the
    # offense path's route_engagement insert.
    #
    # B2 — query the CANDIDATE ids (the open PENDING/ARRIVED subset) with NO
    # lock; the per-row loop re-fetches each by id with_for_update under its
    # own savepoint, so locks are held briefly per row rather than across the
    # whole sweep.
    candidate_ids = (
        db.query(PendingEngagement.id)
        .filter(PendingEngagement.status.in_(
            (EngagementStatus.PENDING, EngagementStatus.ARRIVED)
        ))
        .all()
    )

    for (engagement_id,) in candidate_ids:
        # B1 — each engagement runs inside its OWN SAVEPOINT. A Postgres-level
        # error inside _sweep_one aborts only this savepoint's subtransaction;
        # sp.rollback() restores the session to the pre-row state (releasing
        # this row's lock) and the loop CONTINUES, so earlier successful rows
        # survive to the caller's outer commit. sp.commit() releases the
        # savepoint while keeping the row's changes buffered for that commit.
        sp = db.begin_nested()
        try:
            engagement = (
                db.query(PendingEngagement)
                .filter(PendingEngagement.id == engagement_id)
                .with_for_update()
                .first()
            )
            # Re-confirm on the locked row: a concurrent sweep / resolution
            # could have moved it out of the open set since the candidate
            # query. Skip without touching it.
            if engagement is None or engagement.status not in (
                EngagementStatus.PENDING, EngagementStatus.ARRIVED
            ):
                sp.rollback()
                continue
            row_events = _sweep_one(db, engagement, now)
            sp.commit()
            events.extend(row_events)
        except Exception:
            logger.exception("Engagement sweep failed for %s", engagement_id)
            sp.rollback()

    db.flush()
    return events


def _sweep_one(db: Session, engagement: PendingEngagement,
               now: datetime) -> List[Dict[str, Any]]:
    player = (
        db.query(Player)
        .filter(Player.id == engagement.player_id)
        .first()
    )
    if player is None:
        _release_squad(db, engagement)
        engagement.status = EngagementStatus.EXPIRED
        engagement.resolved_at = now
        return []

    if engagement.status == EngagementStatus.ARRIVED:
        # Encounter over once the offender is no longer in the sector
        # (fled / destroyed / moved on). Post-arrival pursuit is a
        # documented deferral.
        if player.current_sector_id != engagement.arrival_sector_id:
            _release_squad(db, engagement)
            engagement.status = EngagementStatus.RESOLVED
            engagement.resolved_at = now
        return []

    # --- PENDING rows ---

    # (c) >24h expiry — release the held officers.
    if engagement.expires_at is not None and now >= engagement.expires_at:
        _release_squad(db, engagement)
        engagement.status = EngagementStatus.EXPIRED
        engagement.resolved_at = now
        return []

    # (b) Jurisdiction exit fires immediately on boundary cross:
    # squad reverts, −25 evade-arrest (police-forces.md § flee).
    current_sector = (
        db.query(Sector)
        .filter(Sector.sector_id == player.current_sector_id)
        .first()
    )
    current_jurisdiction = (
        jurisdiction_of(db, current_sector) if current_sector else None
    )
    in_jurisdiction = (
        current_jurisdiction == engagement.jurisdiction
        and (current_sector.region_id == engagement.region_id
             if engagement.region_id else True)
    )
    if not in_jurisdiction:
        _release_squad(db, engagement)
        engagement.status = EngagementStatus.CANCELLED
        engagement.resolved_at = now
        try:
            from src.services.personal_reputation_service import (
                PersonalReputationService,
            )
            PersonalReputationService(db).adjust_reputation(
                player.id, -25, "evade_arrest"
            )
        except Exception:
            logger.exception("evade_arrest rep hook failed (non-fatal)")
        return []

    # (d) No-officer grace: once the window closes, the next available
    # squad arrives IMMEDIATELY (no extra 2-turn layer — ADR-0042).
    if not engagement.npc_squad_ids:
        if engagement.grace_expires_at is None or now < engagement.grace_expires_at:
            return []
        if engagement.jurisdiction == SENTINEL:
            size, include_captain = 4, False
        else:
            size, include_captain = _federation_squad_size(player)
        squad = _pick_squad(
            db, engagement.jurisdiction, engagement.region_id,
            player.current_sector_id, size, include_captain,
        )
        if not squad:
            return []  # still short-handed; retry next sweep
        engagement.npc_squad_ids = [str(npc.id) for npc in squad]
        for npc in squad:
            npc.status = NPCStatus.ENGAGED_PENDING_ARRIVAL
        events = _place_squad(db, engagement, player.current_sector_id)
        engagement.status = EngagementStatus.ARRIVED
        engagement.arrival_sector_id = player.current_sector_id
        return events

    # (a) Turn-counter watcher.
    if (engagement.arrival_turn_threshold is not None
            and (player.lifetime_turns_spent or 0) >= engagement.arrival_turn_threshold):
        events = _place_squad(db, engagement, player.current_sector_id)
        engagement.status = EngagementStatus.ARRIVED
        engagement.arrival_sector_id = player.current_sector_id
        return events

    return []
