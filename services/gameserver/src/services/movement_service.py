import logging
import random
import uuid
from typing import List, Dict, Any, Optional, Tuple
from datetime import datetime, timedelta, UTC
from sqlalchemy.orm import Session
from sqlalchemy import and_, or_, select
from sqlalchemy.exc import IntegrityError

from src.models.player import Player
from src.models.ship import Ship, ShipStatus
from src.models.sector import Sector, SectorType, sector_warps
from src.models.planet import Planet
from src.models.warp_tunnel import WarpTunnel, WarpTunnelStatus, WarpTunnelType
from src.models.player_warp_knowledge import (
    PlayerWarpKnowledge,
    WarpLayer,
    WarpVisibilityState,
    WarpRevealedVia,
)
from src.models.team_member import TeamMember
from src.models.combat import CombatResult
from src.models.combat_log import CombatLog
from src.models.drone import Drone, DroneStatus
from sqlalchemy.orm.attributes import flag_modified

from src.services import warp_gate_service
from src.services.turn_service import spend_turns, regenerate_turns

logger = logging.getLogger(__name__)


def _is_player_gate(tunnel: WarpTunnel) -> bool:
    """Player-built warp gate: ARTIFICIAL tunnel with created_by_player_id set
    (the created_by_player_id IS NOT NULL predicate distinguishes player gates
    from generator-placed ARTIFICIAL connections — warp-gates.md). Traversal
    is 0 turns and strictly one-way."""
    return (
        tunnel.type == WarpTunnelType.ARTIFICIAL
        and tunnel.created_by_player_id is not None
    )


def _aware(dt: Optional[datetime]) -> Optional[datetime]:
    """Coerce a possibly-naive datetime to UTC-aware so comparisons against
    ``datetime.now(UTC)`` never raise. Stored timestamps are timezone-aware
    columns, but a value parsed from JSONB may be naive."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt


def _tunnel_collapse_due(tunnel: WarpTunnel) -> bool:
    """WO-WC / async-workers.md § Warp-tunnel collapse: lazy-on-read lifetime
    check. An artificial tunnel carries an ``expected_lifetime`` — an absolute
    ISO-8601 collapse time (jsonb-schema.md: WarpTunnel.properties.expected_lifetime
    = "iso8601 | null"; natural tunnels are null). If that collapse time is in
    the past, the tunnel is due to collapse. The legacy ``expires_at`` column is
    honored too (same semantics, an absolute collapse instant).

    Returns True iff the tunnel has a defined collapse time that is now past.
    Natural tunnels (no lifetime, no expires_at) never collapse on lifetime.
    """
    now = datetime.now(UTC)

    # Legacy absolute column (set by some generators / artificial flows).
    expires_at = _aware(getattr(tunnel, "expires_at", None))
    if expires_at is not None and expires_at <= now:
        return True

    # Canonical lifetime in properties JSONB.
    props = tunnel.properties or {}
    raw = props.get("expected_lifetime")
    if raw is None:
        return False

    collapse_at: Optional[datetime] = None
    if isinstance(raw, datetime):
        collapse_at = _aware(raw)
    elif isinstance(raw, str):
        try:
            # Accept a trailing 'Z' (Zulu) as +00:00.
            collapse_at = _aware(datetime.fromisoformat(raw.replace("Z", "+00:00")))
        except ValueError:
            logger.warning(
                "Unparseable expected_lifetime %r on tunnel %s — treating as "
                "no-lifetime", raw, tunnel.id,
            )
            return False
    elif isinstance(raw, (int, float)):
        # Defensive fallback: a numeric lifetime is interpreted as DAYS from
        # creation (WO additive phrasing "created_at + expected_lifetime").
        created = _aware(getattr(tunnel, "created_at", None))
        if created is not None:
            collapse_at = created + timedelta(days=float(raw))

    return collapse_at is not None and collapse_at <= now


def _player_knows_warp(db: Session, player_id: uuid.UUID, tunnel: WarpTunnel) -> bool:
    """True if ``player_id`` holds a revealed/traversed PlayerWarpKnowledge row
    for this warp tunnel (ADR-0045). Only meaningful for latent tunnels — a
    non-latent tunnel is visible to everyone regardless of this table."""
    row = db.query(PlayerWarpKnowledge).filter(
        PlayerWarpKnowledge.player_id == player_id,
        PlayerWarpKnowledge.warp_layer == WarpLayer.WARP_TUNNELS,
        PlayerWarpKnowledge.warp_id == tunnel.id,
    ).first()
    return row is not None and row.is_known


def _reveal_warp_to_player(
    db: Session,
    player_id: uuid.UUID,
    tunnel: WarpTunnel,
    revealed_via: WarpRevealedVia,
) -> PlayerWarpKnowledge:
    """Idempotent upsert of a per-player warp-knowledge row at ``revealed``
    state (ADR-0045 / aria-companion.md § Warp discovery). Re-revealing an
    already-known warp is a no-op that never downgrades a ``traversed`` row.
    Does NOT commit — the caller owns the transaction.

    Also fans the reveal out to the discoverer's CURRENT team/corp (WO-GWQ-
    WARPSHARE): every teammate who doesn't already hold a knowledge row for
    this tunnel gets one at ``revealed_via=CORP_SHARE``, plus a realtime WS
    push. See ``_propagate_warp_reveal_to_team``.
    """
    row = db.query(PlayerWarpKnowledge).filter(
        PlayerWarpKnowledge.player_id == player_id,
        PlayerWarpKnowledge.warp_layer == WarpLayer.WARP_TUNNELS,
        PlayerWarpKnowledge.warp_id == tunnel.id,
    ).first()
    if row is None:
        row = PlayerWarpKnowledge(
            player_id=player_id,
            warp_layer=WarpLayer.WARP_TUNNELS,
            warp_id=tunnel.id,
            visibility_state=WarpVisibilityState.REVEALED,
            revealed_via=revealed_via,
        )
        db.add(row)
    elif row.visibility_state == WarpVisibilityState.HIDDEN:
        # Promote hidden -> revealed; never downgrade traversed.
        row.visibility_state = WarpVisibilityState.REVEALED
        row.revealed_via = revealed_via
    elif (
        row.revealed_via == WarpRevealedVia.CORP_SHARE
        and revealed_via != WarpRevealedVia.CORP_SHARE
    ):
        # ADR-0064 R-V3: the Nexus-warp marker requires PERSONAL discovery
        # "regardless of corp share" — a row whose only provenance was a
        # teammate's share must upgrade to personal the moment this player
        # genuinely discovers the same tunnel themselves (scan/traversal),
        # so a personal-discovery check never stays permanently masked by
        # an earlier share. Visibility state is already revealed; only the
        # provenance changes.
        row.revealed_via = revealed_via

    _propagate_warp_reveal_to_team(db, player_id, tunnel)
    return row


def _propagate_warp_reveal_to_team(
    db: Session, discoverer_id: uuid.UUID, tunnel: WarpTunnel
) -> None:
    """Corp-share fan-out (WO-GWQ-WARPSHARE / ADR-0045): every CURRENT
    teammate of the discoverer who doesn't already hold a PlayerWarpKnowledge
    row for ``tunnel`` gets one at ``revealed_via=CORP_SHARE``, so their own
    available-moves / nexus-marker views reflect a teammate's discovery
    without a scan of their own. A solo player (no team) propagates to no
    one. Does NOT commit — caller owns the transaction.

    Idempotency mirrors special_formation_service.flip_formation_discovery /
    medal_service.award_medal: a pre-check SELECT, then an INSERT wrapped in
    its own SAVEPOINT (``db.begin_nested()``) so a concurrent teammate-reveal
    racing on the same UNIQUE(player_id, warp_layer, warp_id) constraint
    rolls back only the losing INSERT, never the caller's open transaction.
    """
    team_id = db.query(Player.team_id).filter(Player.id == discoverer_id).scalar()
    if team_id is None:
        return

    teammates = (
        db.query(Player.id, Player.user_id)
        .join(TeamMember, TeamMember.player_id == Player.id)
        .filter(TeamMember.team_id == team_id, Player.id != discoverer_id)
        .all()
    )
    if not teammates:
        return

    newly_notified: List[Tuple[uuid.UUID, uuid.UUID]] = []
    for teammate_player_id, teammate_user_id in teammates:
        existing = db.query(PlayerWarpKnowledge).filter(
            PlayerWarpKnowledge.player_id == teammate_player_id,
            PlayerWarpKnowledge.warp_layer == WarpLayer.WARP_TUNNELS,
            PlayerWarpKnowledge.warp_id == tunnel.id,
        ).first()
        if existing is not None:
            continue

        knowledge = PlayerWarpKnowledge(
            player_id=teammate_player_id,
            warp_layer=WarpLayer.WARP_TUNNELS,
            warp_id=tunnel.id,
            visibility_state=WarpVisibilityState.REVEALED,
            revealed_via=WarpRevealedVia.CORP_SHARE,
        )
        try:
            with db.begin_nested():
                db.add(knowledge)
                db.flush()
        except IntegrityError:
            # Lost the race to a concurrent share/scan for the same
            # (player, tunnel) — already known now, not a new share for
            # this call. begin_nested already rolled back to the savepoint;
            # nothing else lost.
            logger.info(
                "corp-share warp reveal: teammate %s already knows tunnel %s "
                "(race resolved by UNIQUE)", teammate_player_id, tunnel.id,
            )
            continue

        newly_notified.append((teammate_player_id, teammate_user_id))

    if newly_notified:
        _dispatch_warp_corp_share(db, discoverer_id, newly_notified, tunnel)


def _dispatch_warp_corp_share(
    db: Session,
    discoverer_id: uuid.UUID,
    newly_notified: List[Tuple[uuid.UUID, uuid.UUID]],
    tunnel: WarpTunnel,
) -> None:
    """Push the corp-share warp-discovery event to each newly-notified
    teammate's socket (WO-GWQ-WARPSHARE; special-formations.md § Discovery:
    "Corp-mates can share discoveries via the existing realtime-bus event").

    Mirrors ``_dispatch_hostile_detected`` / ``_broadcast_sector_presence``:
    import inside the function, grab the running loop, schedule with
    ``loop.create_task`` (so it runs after the caller's commit and never
    blocks the sync reveal), and swallow any failure so a quiet socket can
    never break a scan. Delivered via ``send_personal_message`` (keyed on
    ``str(user_id)``, movement_service.py:484-505 precedent) to exactly the
    newly-notified subset — a teammate who already knew gets no redundant
    push."""
    try:
        import asyncio
        from src.services.websocket_service import connection_manager

        loop = asyncio.get_running_loop()
        ts = datetime.now(UTC).isoformat()
        origin_sector = db.query(Sector).filter(Sector.id == tunnel.origin_sector_id).first()
        destination_sector = db.query(Sector).filter(Sector.id == tunnel.destination_sector_id).first()
        payload = {
            "type": "warp_corp_share",
            "discoverer_id": str(discoverer_id),
            "origin_sector_id": origin_sector.sector_id if origin_sector else None,
            "destination_sector_id": destination_sector.sector_id if destination_sector else None,
            "revealed_via": WarpRevealedVia.CORP_SHARE.value,
            "timestamp": ts,
        }
        for teammate_player_id, teammate_user_id in newly_notified:
            loop.create_task(connection_manager.send_personal_message(
                str(teammate_user_id),
                {**payload, "player_id": str(teammate_player_id)},
            ))
    except Exception:
        logger.debug(
            "Skipped warp corp-share WS push (no loop or socket)", exc_info=True,
        )


def _dispatch_exploration_medals(db: Session, player: Player, context: Dict[str, Any]) -> None:
    """Fire the medals-lane frozen hook
    ``medal_service.check_and_award_exploration_medals(db, player, context)``
    after an exploration event (ADR-0028 medal storage). Mirrors the wired
    combat dispatcher (``combat_service._dispatch_combat_medals``).

    ``context`` carries the player's current exploration statistics (here
    ``{sectors_visited}``). The hook is idempotent on the medals-lane side
    (UNIQUE(player_id, medal_id) + threshold gating); this dispatcher is
    defensive: resolved by ``getattr`` (the hook may be absent in a deployment
    where the medals lane hasn't landed), and any failure is logged and
    swallowed — a medal hiccup must NEVER break movement. py_compile-safe: no
    parse-time reference to a not-yet-existing symbol."""
    try:
        import src.services.medal_service as _medal_module
        module_hook = getattr(_medal_module, "check_and_award_exploration_medals", None)
        if callable(module_hook):
            module_hook(db, player, context)
    except Exception as e:  # never let a medal hiccup break movement
        logger.error("Exploration medal dispatch hook failed: %s", e)


class MovementService:
    """Service for managing player movement through the galaxy."""

    # Player warp gate tow surcharge: +2 turns FLAT regardless of towed size
    # (FEATURES/gameplay/ships.md:357; WO-AF). Distinct from the size-based
    # surcharge_per_move cached on Ship.tow_state, which applies to natural warps
    # and tunnels.
    GATE_TOW_SURCHARGE_FLAT = 2

    def __init__(self, db: Session):
        self.db = db

    def _count_unique_sectors_visited(self, player_id: uuid.UUID) -> int:
        """Count the distinct sectors this player has ever visited.

        The ARIAExplorationMap table holds exactly one row per (player, sector)
        — it is the canonical unique-sector visit ledger written by
        ``_execute_movement`` — so the row count for a player IS the player's
        ``sectors_visited`` statistic (the Explorer's Badge threshold). This is
        called after the current move's visit row has been added/flushed, so a
        first visit to sector #500 is counted at the moment it crosses the
        threshold. Defensive (returns 0 on any error) so it can never break a
        move: the exploration model is imported lazily to mirror the
        best-effort, deployment-tolerant pattern of the ARIA hooks."""
        try:
            from src.models.aria_personal_intelligence import ARIAExplorationMap
            # autoflush is off — flush the just-added visit row for THIS move so it
            # is included in the count; without this the 500th unique visit would be
            # missed and the Explorer's Badge would award one move late (review fix).
            self.db.flush()
            return (
                self.db.query(ARIAExplorationMap)
                .filter(ARIAExplorationMap.player_id == player_id)
                .count()
            )
        except Exception as e:
            logger.error("Failed to count unique sectors visited for %s: %s", player_id, e)
            return 0

    # Mechanical-failure model (WO-AB; blessed base_rate 2%/jump — DECISIONS
    # Pending). Each successful jump rolls for a mechanical failure; the
    # MAINTENANCE_SYSTEM upgrade's banked failure_rate_reduction (stored in
    # ship.maintenance) lowers the effective rate. On a failure one installed
    # upgrade drops a level (see ship_upgrade_service.degrade_random_system).
    MECHANICAL_FAILURE_BASE_RATE = 0.02

    def _roll_mechanical_failure(self, player: Player, result: Dict[str, Any]) -> None:
        """Roll for a mechanical failure after a SUCCESSFUL jump (WO-AB).

        effective = base_rate * (1 - failure_rate_reduction), clamped to
        [0, base_rate] (failure_rate_reduction read from ship.maintenance,
        default 0). On ``random() < effective`` a random installed upgrade drops
        one level via ship_upgrade_service.degrade_random_system, which reverses
        that level's stat bonus so ship stats stay consistent. A failure on a
        ship with no installed upgrades is a harmless no-op.

        Surfaces a ``mechanical_failure`` note into ``result`` only when a real
        degrade occurred — the existing move return contract is otherwise
        untouched. Best-effort: a roll/degrade hiccup must NEVER strand the move
        (mirrors the ARIA / medal hooks), and the degrade is folded into the
        move's already-committed transaction by an explicit commit here so a
        post-commit failure on a freshly-committed move still persists.
        """
        try:
            ship = player.current_ship
            if not ship:
                return

            maintenance = ship.maintenance if isinstance(ship.maintenance, dict) else {}
            try:
                reduction = float(maintenance.get("failure_rate_reduction", 0) or 0)
            except (TypeError, ValueError):
                reduction = 0.0

            effective = self.MECHANICAL_FAILURE_BASE_RATE * (1.0 - reduction)
            # Clamp to [0, base_rate]: reduction>1 (defensive) can't make it
            # negative, and it can never exceed the base rate.
            effective = max(0.0, min(effective, self.MECHANICAL_FAILURE_BASE_RATE))

            if random.random() >= effective:
                return  # no failure this jump

            from src.services.ship_upgrade_service import ShipUpgradeService
            outcome = ShipUpgradeService(self.db).degrade_random_system(ship)

            if outcome.get("degraded"):
                # The move was already committed in _execute_movement; persist the
                # degrade's ship mutations in their own commit.
                self.db.commit()
                result["mechanical_failure"] = {
                    "upgrade_type": outcome.get("upgrade_type"),
                    "old_level": outcome.get("old_level"),
                    "new_level": outcome.get("new_level"),
                    "message": (
                        f"Mechanical failure: {outcome.get('upgrade_type')} dropped to "
                        f"level {outcome.get('new_level')} — re-purchase to restore it"
                    ),
                }
        except Exception as e:
            logger.error("Mechanical-failure roll failed during movement: %s", e)
            # Don't let a failed roll poison the (already-committed) move.
            try:
                self.db.rollback()
            except Exception:
                pass

    # Scanner-array detection model (WO-AY). After a SUCCESSFUL move, a
    # best-effort sweep finds planets within the scanner_array's
    # detection_range_sectors (JUMPS) of the destination that have an
    # OPERATIONAL scanner_array, and warns each detecting planet's owner if the
    # moving ship is hostile to them. Range is read from citadel_service's
    # building def (READ-ONLY) — never hardcoded — and the BFS is bounded by it
    # so the sweep stays cheap (~2 hops). Combat-adjacent "hostile" definition
    # (owner mismatch AND not same team/corp) is NO-CANON and DECISIONS-filed.

    def _scanner_detection_range(self) -> int:
        """Read the scanner_array's detection_range_sectors from citadel_service's
        building definitions (READ-ONLY). Falls back to 0 (sweep no-ops) if the
        def or effect is missing — never raises, never hardcodes the range."""
        try:
            from src.services.citadel_service import DEFENSE_BUILDINGS
            spec = DEFENSE_BUILDINGS.get("scanner_array", {}) or {}
            effects = spec.get("effects", {}) or {}
            return int(effects.get("detection_range_sectors", 0) or 0)
        except Exception as e:
            logger.error("Failed to read scanner detection range: %s", e)
            return 0

    def _bounded_sector_distances(self, start_uuid: uuid.UUID, max_jumps: int) -> Dict[uuid.UUID, int]:
        """Map each sector UUID within ``max_jumps`` warp-jumps of ``start_uuid``
        to its jump distance, over the bidirectional ``sector_warps`` graph.

        Level-batched BFS (one pair of queries per depth) mirroring
        genesis_service._bfs_distances and bounded by ``max_jumps`` so a sweep
        never traverses the galaxy. Edge model matches the rest of this service:
        forward source->dest edges plus reverse dest->source edges for
        bidirectional warps. ``start_uuid`` is at distance 0 (the destination
        sector itself — a planet sitting in the arrival sector counts)."""
        distances: Dict[uuid.UUID, int] = {start_uuid: 0}
        frontier: List[uuid.UUID] = [start_uuid]
        for depth in range(1, max_jumps + 1):
            if not frontier:
                break
            fwd = self.db.execute(
                select(sector_warps.c.destination_sector_id)
                .where(sector_warps.c.source_sector_id.in_(frontier))
            ).scalars().all()
            rev = self.db.execute(
                select(sector_warps.c.source_sector_id)
                .where(and_(
                    sector_warps.c.destination_sector_id.in_(frontier),
                    sector_warps.c.is_bidirectional.is_(True),
                ))
            ).scalars().all()
            nxt: List[uuid.UUID] = []
            for nbr in list(fwd) + list(rev):
                if nbr not in distances:
                    distances[nbr] = depth
                    nxt.append(nbr)
            frontier = nxt
        return distances

    def _is_hostile_to_planet(self, player: Player, owner: Optional[Player]) -> bool:
        """The moving ``player`` is hostile to a planet whose owner is ``owner``
        (NO-CANON, DECISIONS-filed conservative "someone's approaching" rule):
        hostile iff the mover is NOT the owner AND NOT a member of the owner's
        team/corp. An unowned planet (owner is None) has no one to warn and is
        never hostile-detecting here."""
        if owner is None:
            return False
        if owner.id == player.id:
            return False  # the owner's own ship is never hostile
        # Same team/corp is friendly. team_id is nullable; two None team_ids do
        # NOT make strangers teammates, so require a real shared team_id.
        if (
            owner.team_id is not None
            and player.team_id is not None
            and owner.team_id == player.team_id
        ):
            return False
        return True

    def _sweep_scanner_detection(self, player: Player, destination_sector_id: int) -> None:
        """Best-effort scanner-array detection sweep after a SUCCESSFUL move (WO-AY).

        Finds planets within the scanner_array's detection_range_sectors JUMPS of
        the destination that have an OPERATIONAL scanner_array (count >= 1), and
        pushes a player-scoped ``hostile_detected`` WS frame to each detecting
        planet's owner when the moving ship is hostile to them. The detection
        range is read from citadel_service's building def (never hardcoded) and
        the BFS is bounded by it.

        Best-effort, exactly like _roll_mechanical_failure / _notify_bumped: the
        whole sweep is wrapped so it can NEVER block or crash the move. The WS
        send is async and this runs in a sync path, so it is scheduled on the
        running loop via get_running_loop + create_task (it fires after the
        caller's transaction commits and yields). All DB reads here are
        READ-ONLY (planets/sectors/owners) — no mutation, no commit.
        """
        try:
            max_jumps = self._scanner_detection_range()
            if max_jumps <= 0:
                return

            dest_sector = (
                self.db.query(Sector)
                .filter(Sector.sector_id == destination_sector_id)
                .first()
            )
            if not dest_sector:
                return

            # Sector UUIDs within range (distance 0..max_jumps from destination).
            distances = self._bounded_sector_distances(dest_sector.id, max_jumps)
            in_range_uuids = list(distances.keys())
            if not in_range_uuids:
                return

            # Planets sitting in any in-range sector (READ-ONLY).
            planets = (
                self.db.query(Planet)
                .filter(Planet.sector_uuid.in_(in_range_uuids))
                .all()
            )
            if not planets:
                return

            ship_id = player.current_ship_id
            for planet in planets:
                # Only OPERATIONAL scanner arrays detect. defense_buildings lives
                # under active_events['defense_buildings'] as building->count;
                # active_events may be a legacy list, in which case it's empty
                # (mirrors citadel_service._get_defense_buildings tolerance).
                events = planet.active_events
                if not isinstance(events, dict):
                    continue
                buildings = events.get("defense_buildings", {}) or {}
                try:
                    scanner_count = int(buildings.get("scanner_array", 0) or 0)
                except (TypeError, ValueError):
                    scanner_count = 0
                if scanner_count < 1:
                    continue

                if planet.owner_id is None:
                    continue
                owner = (
                    self.db.query(Player)
                    .filter(Player.id == planet.owner_id)
                    .first()
                )
                if owner is None or owner.user_id is None:
                    continue

                if not self._is_hostile_to_planet(player, owner):
                    continue

                self._dispatch_hostile_detected(
                    owner_user_id=owner.user_id,
                    sector_id=destination_sector_id,
                    detection_range=distances.get(planet.sector_uuid, 0),
                    ship_id=ship_id,
                    detected_player_id=player.id,
                )
        except Exception as e:
            logger.error("Scanner detection sweep failed during movement: %s", e)

    def _dispatch_hostile_detected(self, owner_user_id, sector_id: int,
                                   detection_range: int, ship_id,
                                   detected_player_id) -> None:
        """Schedule the async ``hostile_detected`` WS push to a planet owner.

        Mirrors docking_service._notify_bumped / turn_service._emit_turn_pool_update:
        import inside the function, grab the running loop, schedule the coroutine
        with loop.create_task (so it runs after the move's transaction commits and
        yields, never blocking the sync move), and swallow any failure (no loop,
        no socket) so a quiet socket can never break the move."""
        try:
            import asyncio
            from src.services.websocket_service import connection_manager

            loop = asyncio.get_running_loop()
            loop.create_task(connection_manager.send_hostile_detected(
                str(owner_user_id),
                {
                    "sector_id": sector_id,
                    "detection_range": detection_range,
                    "ship_id": str(ship_id) if ship_id else None,
                    "detected_player_id": str(detected_player_id),
                },
            ))
        except Exception:
            logger.debug(
                "Skipped hostile-detected WS notice (no loop or socket)",
                exc_info=True,
            )

    def _broadcast_sector_presence(self, old_sector_id: int, new_sector_id: int,
                                   mover_ws_user_id: str,
                                   old_region_id: Optional[uuid.UUID] = None,
                                   new_region_id: Optional[uuid.UUID] = None) -> None:
        """Schedule the async WS room-hop for the moving player (WO-RT-ROOM-HOP).

        Delegates to ``connection_manager.update_user_location``, which both
        corrects the WS sector-room registry (previously frozen at connect
        time — a moved player kept receiving broadcasts for their OLD sector
        forever, per realtime-bus.md:92) AND emits the ``player_left_sector`` /
        ``player_entered_sector`` frames itself, in the same shape the
        player-client's PlayerMovementMessage contract expects
        (websocket.ts:19-25) — this method no longer emits raw
        ``broadcast_to_sector`` frames directly, so each move emits exactly one
        leave + one enter instead of two independent sources racing to do it.
        When the destination sector's region differs from the origin's, also
        schedules ``update_user_region`` so region-scoped broadcasts
        (governance / election / treaty) follow the player across the
        boundary.

        Mirrors ``_dispatch_hostile_detected`` / docking_service._notify_bumped /
        turn_service._emit_turn_pool_update: import inside the function, grab the
        running loop, schedule the coroutines with ``loop.create_task`` (so they
        run after the move's transaction commits and yields, never blocking the
        sync move), and swallow any failure (no loop, no socket) so a quiet
        socket can never break the move."""
        try:
            import asyncio
            from src.services.websocket_service import connection_manager

            loop = asyncio.get_running_loop()
            loop.create_task(connection_manager.update_user_location(mover_ws_user_id, new_sector_id))

            if old_region_id != new_region_id:
                loop.create_task(connection_manager.update_user_region(
                    mover_ws_user_id,
                    str(new_region_id) if new_region_id is not None else None,
                ))
        except Exception:
            logger.debug(
                "Skipped sector/region WS room-hop (no loop or socket)",
                exc_info=True,
            )

    def move_player_to_sector(self, player_id: uuid.UUID, destination_sector_id: int) -> Dict[str, Any]:
        """
        Move a player to a destination sector.
        Returns a dict with success status, message, and turn cost.
        """
        # Lazily complete any HARMONIZING warp gates touching the player's
        # current sector BEFORE locking the player row (gate row is locked
        # first — same lock order as warp_gate_service) so a freshly
        # harmonized gate is traversable without a separate listing call.
        # Failures here must not block normal movement (ARIA-hook pattern).
        try:
            pre_player = self.db.query(Player).filter(Player.id == player_id).first()
            if pre_player:
                warp_gate_service.advance_gates_touching_sector(
                    self.db, pre_player.current_sector_id
                )
        except Exception as e:
            # Roll back so a failed advance can't poison the movement
            # transaction that follows (nothing else is pending yet).
            logger.error("Failed lazy warp-gate advance during movement: %s", e)
            self.db.rollback()

        # Lock player row to prevent concurrent movement race conditions
        player = self.db.query(Player).filter(Player.id == player_id).with_for_update().first()
        if not player:
            return {"success": False, "message": "Player not found", "turn_cost": 0}

        # ADR-0004 continuous regen: lazily refill the turn pool for real time
        # elapsed BEFORE any affordability check, inside the row lock so a
        # concurrent spend cannot double-credit.
        regenerate_turns(self.db, player)

        # Block movement if player is docked at a port or landed on a planet
        if player.is_docked:
            return {"success": False, "message": "You must undock before moving to another sector", "turn_cost": 0}
        if player.is_landed:
            return {"success": False, "message": "You must leave the planet before moving to another sector", "turn_cost": 0}

        # Ensure player has an active ship
        if not player.current_ship:
            return {"success": False, "message": "No active ship selected", "turn_cost": 0}

        # A Warp Jumper harmonizing into a gate focus is frozen in place
        # (ADR-0029 / ADR-0036). Reject movement before any turn charge so a
        # mid-build hull can't fly off mid-harmonization. 0 turn cost.
        if player.current_ship.status == ShipStatus.HARMONIZING:
            return {
                "success": False,
                "message": "Your ship is harmonizing into a warp gate focus "
                           "and cannot move — cancel the anchor first",
                "turn_cost": 0,
            }

        # Carrier ship-hangar (WO-AE; ships.md:338-340): a ship docked inside a
        # Carrier is an inert passenger — it cannot move independently. The
        # pilot rides along when the Carrier moves, or pays 1 turn to undock and
        # resume control. Reject independent moves before any turn charge.
        try:
            from src.services.hangar_service import HangarService
            if HangarService(self.db).is_ship_hangared(player.current_ship_id):
                return {
                    "success": False,
                    "message": "Your ship is docked inside a Carrier — undock to move "
                               "under your own power",
                    "turn_cost": 0,
                }
        except Exception as e:
            logger.error("Hangar passenger move-guard failed: %s", e)

        # Tractor tow (WO-AF; ships.md:359): a ship currently BEING TOWED by some
        # hauler cannot move independently — it rides along when the hauler moves,
        # or must detach first. Reject before any turn charge. The HAULER's own
        # move is allowed (and pays the surcharge in _execute_movement below).
        try:
            from src.services.tow_service import TowService
            if TowService(self.db).is_being_towed(player.current_ship_id):
                return {
                    "success": False,
                    "message": "Your ship is being towed — detach the tractor lock to "
                               "move under your own power",
                    "turn_cost": 0,
                }
        except Exception as e:
            logger.error("Tow passenger move-guard failed: %s", e)

        current_sector_id = player.current_sector_id

        # Return early if already in the destination sector
        if current_sector_id == destination_sector_id:
            return {"success": True, "message": "Already in this sector", "turn_cost": 0}

        # Tractor tow (WO-AF; ships.md:354-357): if THIS ship is actively towing
        # another, the hauler pays its full move cost PLUS a tow surcharge. The
        # surcharge is size-based on warps/tunnels (the cached surcharge_per_move:
        # tiny+1/small+2/medium+3/large+5), but a PLAYER WARP GATE costs +2 turns
        # FLAT regardless of towed size (ships.md:357). The towed ship rides along
        # (its sector follows the hauler, towed pilot pays 0) — applied in
        # _execute_movement. Best-effort read: a tow-state hiccup must not break
        # the player's own move; absent tow => surcharge 0 (unchanged behavior).
        tow_size_surcharge = 0
        try:
            from src.services.tow_service import TowService
            if TowService(self.db).is_actively_towing(player.current_ship):
                tow_size_surcharge = int(
                    (player.current_ship.tow_state or {}).get("surcharge_per_move", 0)
                )
        except Exception as e:
            logger.error("Tow surcharge read failed (continuing at base cost): %s", e)
            tow_size_surcharge = 0

        # Prefer a 0-turn ACTIVE player-built warp gate over a parallel direct
        # warp (FIX 7). The available-moves listing advertises the gate at 0
        # turns; if a direct warp ALSO connects origin -> destination, charging
        # the direct-warp cost here would contradict the advertised 0. Take the
        # gate first so the charged cost matches what the player was shown.
        player_gate_tunnel = self._has_player_gate(current_sector_id, destination_sector_id)
        if player_gate_tunnel is not None:
            # Gate is 0 turns normally; while towing it is +2 turns FLAT
            # (ships.md:357 — flat, not the size surcharge).
            gate_cost = 0 + (self.GATE_TOW_SURCHARGE_FLAT if tow_size_surcharge else 0)
            if player.turns < gate_cost:
                return {"success": False, "message": "Not enough turns for this gate transit while towing", "turn_cost": gate_cost}

            # WG1 access-mode enforcement + WO-GWQ-GATE-TOLL layered gates
            # (faction-rep min/max) — this is the REAL move-validation path a
            # player-built gate traversal takes (this branch matches BEFORE
            # _check_warp_tunnel is ever called below, so its own dead
            # player-gate branch never fires from here — see its comment).
            # Must run, and reject if it's going to, BEFORE any toll credit
            # moves and AFTER the turns check above already passed (so a
            # blocked player is never charged turns for a move that never
            # happens, and a turns-short player is never billed a toll for a
            # move that was already going to fail). warp_gate_service is
            # already imported module-wide above (see advance_gates_touching_
            # sector's use elsewhere in this file) -- no fresh import needed.
            try:
                warp_gate_service.check_traversal_access(self.db, player, player_gate_tunnel)
                warp_gate_service.collect_toll(self.db, player, player_gate_tunnel)
            except warp_gate_service.WarpGateError as e:
                return {"success": False, "message": e.detail, "turn_cost": 0}

            result = self._execute_movement(player, destination_sector_id, gate_cost)
            tunnel_events = self._check_for_tunnel_events(
                player, current_sector_id, destination_sector_id
            )
            encounters = self._check_for_encounters(player, destination_sector_id)
            result.update({"tunnel_events": tunnel_events, "encounters": encounters})
            # WO-AY: a successful gate jump is a real arrival — sweep for
            # scanner-array detections (best-effort; only on real success).
            if result.get("success"):
                self._sweep_scanner_detection(player, destination_sector_id)
            return result

        # Check if direct warp exists
        can_warp, warp_cost, warp_message = self._check_direct_warp(
            current_sector_id, destination_sector_id, player.current_ship
        )

        if can_warp:
            # Add the size-based tow surcharge to the base warp cost.
            warp_cost = warp_cost + tow_size_surcharge
            # Check if player has enough turns
            if player.turns < warp_cost:
                return {"success": False, "message": "Not enough turns for this movement", "turn_cost": warp_cost}

            # Execute the move
            result = self._execute_movement(player, destination_sector_id, warp_cost)

            # Check for encounters
            encounters = self._check_for_encounters(player, destination_sector_id)

            # Combine results
            result.update({"encounters": encounters})

            # WO-AB: a successful direct warp counts as a jump — roll for a
            # mechanical failure (best-effort; only fires on a real move success).
            if result.get("success"):
                self._roll_mechanical_failure(player, result)
                # WO-AY: same success path — sweep for scanner-array detections.
                self._sweep_scanner_detection(player, destination_sector_id)
            return result

        # Check if warp tunnel exists
        can_tunnel, tunnel_cost, tunnel_message = self._check_warp_tunnel(
            current_sector_id, destination_sector_id, player.current_ship
        )

        if can_tunnel:
            # Add the size-based tow surcharge to the base tunnel cost. (A
            # NATURAL warp tunnel is the size-surcharge path; player gates are
            # the +2-flat path handled above. _check_warp_tunnel never returns a
            # player gate — those are matched by _has_player_gate first.)
            tunnel_cost = tunnel_cost + tow_size_surcharge
            # Check if player has enough turns
            if player.turns < tunnel_cost:
                return {"success": False, "message": "Not enough turns for this warp tunnel jump", "turn_cost": tunnel_cost}

            # Execute the move
            result = self._execute_movement(player, destination_sector_id, tunnel_cost)
            
            # Check for tunnel-specific events
            tunnel_events = self._check_for_tunnel_events(player, current_sector_id, destination_sector_id)
            
            # Check for encounters
            encounters = self._check_for_encounters(player, destination_sector_id)

            # Combine results
            result.update({"tunnel_events": tunnel_events, "encounters": encounters})

            # WO-AB: a successful warp-tunnel jump counts as a jump — roll for a
            # mechanical failure (best-effort; only fires on a real move success).
            if result.get("success"):
                self._roll_mechanical_failure(player, result)
                # WO-AY: same success path — sweep for scanner-array detections.
                self._sweep_scanner_detection(player, destination_sector_id)
            return result

        # If we get here, no valid path was found
        return {"success": False, "message": "No valid path to destination sector", "turn_cost": 0}
    
    def get_available_moves(self, player_id: uuid.UUID) -> Dict[str, Any]:
        """
        Get all sectors a player can move to from their current position.
        Returns a dict with direct warps and warp tunnels available.
        """
        player = self.db.query(Player).filter(Player.id == player_id).first()
        if not player:
            return {"warps": [], "tunnels": []}

        # Lazily complete HARMONIZING warp gates touching this sector so a
        # freshly-harmonized gate appears in the listing without a separate
        # poll (warp_gate_service owns the completion semantics). Listing
        # must keep working even if the advance fails.
        try:
            if warp_gate_service.advance_gates_touching_sector(
                self.db, player.current_sector_id
            ):
                self.db.commit()
        except Exception as e:
            logger.error("Failed lazy warp-gate advance during move listing: %s", e)
            self.db.rollback()

        # Get current sector
        current_sector = self.db.query(Sector).filter(Sector.sector_id == player.current_sector_id).first()
        if not current_sector:
            return {"warps": [], "tunnels": []}
        
        # Get ship for capabilities
        ship = player.current_ship
        
        # Get direct warps. sector_warps stores bidirectional connections
        # as ONE row (source, dest, is_bidirectional=true) per the
        # bang-integration schema map — so a sector reaches its
        # bidirectional neighbours via *incoming* rows too, not just
        # outgoing. Walk both sides.
        direct_warps = []
        seen_sector_ids: set = set()

        # Outgoing edges (this sector is the source).
        for connected_sector in current_sector.outgoing_warps:
            warp_cost = self._calculate_warp_cost(current_sector, connected_sector, ship)
            direct_warps.append({
                "sector_id": connected_sector.sector_id,
                "name": connected_sector.name,
                "type": connected_sector.type.name,
                "turn_cost": warp_cost,
                "can_afford": player.turns >= warp_cost
            })
            seen_sector_ids.add(connected_sector.sector_id)

        # Incoming bidirectional edges — the bang translator stores a
        # two-way warp A↔B as one row (source=A, dest=B, bidir=true),
        # so from B's POV the reverse traversal is this incoming row.
        # Without this branch a player can leave a sector but cannot
        # warp back along a bidirectional connection.
        incoming_bidir_rows = self.db.execute(
            sector_warps.select().where(
                sector_warps.c.destination_sector_id == current_sector.id,
                sector_warps.c.is_bidirectional == True,  # noqa: E712 — SQLA boolean column compare
            )
        ).fetchall()
        for row in incoming_bidir_rows:
            origin = self.db.query(Sector).filter(Sector.id == row.source_sector_id).first()
            if origin is None or origin.sector_id in seen_sector_ids:
                continue
            warp_cost = self._calculate_warp_cost(current_sector, origin, ship)
            direct_warps.append({
                "sector_id": origin.sector_id,
                "name": origin.name,
                "type": origin.type.name,
                "turn_cost": warp_cost,
                "can_afford": player.turns >= warp_cost
            })
            seen_sector_ids.add(origin.sector_id)

        # Get warp tunnels - both outgoing and incoming (for bidirectional)
        warp_tunnels = []

        # Outgoing tunnels (origin is current sector)
        outgoing_tunnels = self.db.query(WarpTunnel).filter(
            WarpTunnel.origin_sector_id == current_sector.id,
            WarpTunnel.status == WarpTunnelStatus.ACTIVE
        ).all()

        for tunnel in outgoing_tunnels:
            # WO-WC: a lifetime-expired tunnel is collapsed and must not list as
            # available (the traversal gate fails it anyway; hide it here too).
            if _tunnel_collapse_due(tunnel):
                continue
            # WO-LW view-filter: hide a latent tunnel the player hasn't
            # personally discovered (ADR-0045). Non-latent tunnels and player
            # gates are unaffected — they list exactly as before.
            if (
                getattr(tunnel, "is_latent", False)
                and not _is_player_gate(tunnel)
                and not _player_knows_warp(self.db, player.id, tunnel)
            ):
                continue
            dest_sector = self.db.query(Sector).filter(Sector.id == tunnel.destination_sector_id).first()
            if dest_sector:
                tunnel_cost = tunnel.turn_cost
                player_gate = _is_player_gate(tunnel)

                if player_gate:
                    # Player warp gate: 0 turns flat — no ship-type
                    # multipliers and no max(1, ...) clamp (warp-gates.md).
                    tunnel_cost = 0
                elif ship and ship.warp_capable:
                    tunnel_cost = max(1, int(tunnel_cost * 0.8))  # 20% reduction for warp-capable ships

                warp_tunnels.append({
                    "sector_id": dest_sector.sector_id,
                    "name": dest_sector.name,
                    "type": dest_sector.type.name,
                    "turn_cost": tunnel_cost,
                    # Contract: gate entries surface as tunnel_type
                    # "warp_gate" so the client renders them unchanged.
                    "tunnel_type": "warp_gate" if player_gate else tunnel.type.name,
                    "stability": tunnel.stability,
                    "one_way": not tunnel.is_bidirectional,
                    "can_afford": player.turns >= tunnel_cost
                })

        # Incoming bidirectional tunnels (destination is current sector, but tunnel is bidirectional)
        incoming_bidirectional = self.db.query(WarpTunnel).filter(
            WarpTunnel.destination_sector_id == current_sector.id,
            WarpTunnel.is_bidirectional == True,
            WarpTunnel.status == WarpTunnelStatus.ACTIVE
        ).all()

        for tunnel in incoming_bidirectional:
            # WO-WC: hide a lifetime-expired (collapsed) tunnel from the reverse view too.
            if _tunnel_collapse_due(tunnel):
                continue
            # WO-LW view-filter: a latent tunnel the player hasn't discovered
            # stays hidden on the reverse (incoming-bidirectional) view too.
            if (
                getattr(tunnel, "is_latent", False)
                and not _is_player_gate(tunnel)
                and not _player_knows_warp(self.db, player.id, tunnel)
            ):
                continue
            # The "destination" for travel is the tunnel's origin sector
            dest_sector = self.db.query(Sector).filter(Sector.id == tunnel.origin_sector_id).first()
            if dest_sector:
                # Don't add duplicates (in case there's already a tunnel in the other direction)
                if any(t["sector_id"] == dest_sector.sector_id for t in warp_tunnels):
                    continue

                tunnel_cost = tunnel.turn_cost

                # Apply ship-specific adjustments
                if ship and ship.warp_capable:
                    tunnel_cost = max(1, int(tunnel_cost * 0.8))  # 20% reduction for warp-capable ships

                warp_tunnels.append({
                    "sector_id": dest_sector.sector_id,
                    "name": dest_sector.name,
                    "type": dest_sector.type.name,
                    "turn_cost": tunnel_cost,
                    "tunnel_type": tunnel.type.name,
                    "stability": tunnel.stability,
                    # This branch only matches is_bidirectional rows, so the
                    # reverse traversal is by definition not one-way. Player
                    # gates (always one-way) can never appear here.
                    "one_way": False,
                    "can_afford": player.turns >= tunnel_cost
                })

        return {
            "warps": direct_warps,
            "tunnels": warp_tunnels
        }

    def scan_for_latent_tunnels(self, player_id: uuid.UUID) -> Dict[str, Any]:
        """WO-LW — reveal the latent warp tunnels touching the player's current
        sector, writing a per-player PlayerWarpKnowledge row for each newly
        discovered one (ADR-0045 / aria-companion.md § Warp discovery,
        ``revealed_via = scan``).

        This is the sector-local reveal primitive: a scan resolves the latent
        tunnels at the player's position (both directions — a latent tunnel
        looks bidirectional until revealed). Already-known and non-latent
        tunnels are left untouched. Returns the count + the sectors revealed.
        Idempotent: re-scanning a sector with no new latent tunnels reveals 0.
        """
        player = self.db.query(Player).filter(Player.id == player_id).first()
        if not player:
            return {"success": False, "message": "Player not found", "revealed": 0, "sectors": []}

        current_sector = self.db.query(Sector).filter(
            Sector.sector_id == player.current_sector_id
        ).first()
        if not current_sector:
            return {"success": False, "message": "Current sector not found", "revealed": 0, "sectors": []}

        # Latent tunnels touching this sector in either direction (a latent
        # tunnel looks bidirectional from the player's chair until revealed).
        latent_tunnels = self.db.query(WarpTunnel).filter(
            WarpTunnel.is_latent == True,  # noqa: E712 — SQLA boolean column compare
            WarpTunnel.status == WarpTunnelStatus.ACTIVE,
            or_(
                WarpTunnel.origin_sector_id == current_sector.id,
                WarpTunnel.destination_sector_id == current_sector.id,
            ),
        ).all()

        revealed_sector_numbers: List[int] = []
        revealed_count = 0
        for tunnel in latent_tunnels:
            # Player gates are never latent; skip defensively.
            if _is_player_gate(tunnel):
                continue
            if _player_knows_warp(self.db, player_id, tunnel):
                continue
            _reveal_warp_to_player(self.db, player_id, tunnel, WarpRevealedVia.SCAN)
            revealed_count += 1
            # Report the OTHER endpoint relative to the player's sector.
            other_id = (
                tunnel.destination_sector_id
                if tunnel.origin_sector_id == current_sector.id
                else tunnel.origin_sector_id
            )
            other = self.db.query(Sector).filter(Sector.id == other_id).first()
            if other is not None:
                revealed_sector_numbers.append(other.sector_id)

        if revealed_count:
            self.db.commit()

        return {
            "success": True,
            "message": (
                f"Scan revealed {revealed_count} latent warp tunnel(s)"
                if revealed_count
                else "Scan complete — no undiscovered latent tunnels here"
            ),
            "revealed": revealed_count,
            "sectors": revealed_sector_numbers,
        }
    
    def get_path_between_sectors(self, start_sector_id: int, end_sector_id: int) -> List[Dict[str, Any]]:
        """
        Find the shortest path between two sectors.
        Returns a list of sectors in the path with turn costs.
        """
        # Get sectors
        start_sector = self.db.query(Sector).filter(Sector.sector_id == start_sector_id).first()
        end_sector = self.db.query(Sector).filter(Sector.sector_id == end_sector_id).first()
        
        if not start_sector or not end_sector:
            return []
        
        # Simple BFS for path finding
        visited = {start_sector.id: None}  # Maps sector ID to previous sector ID
        queue = [(start_sector, 0)]  # (sector, distance)
        
        while queue:
            current, distance = queue.pop(0)
            
            # If we've reached the destination
            if current.id == end_sector.id:
                break
            
            # Add all neighbors to the queue. Walk both outgoing edges and
            # incoming bidirectional edges so BFS pathfinding can traverse
            # bang's bidirectional sector_warps in reverse.
            for neighbor in current.outgoing_warps:
                if neighbor.id not in visited:
                    visited[neighbor.id] = current.id
                    queue.append((neighbor, distance + 1))
            incoming_bidir = self.db.execute(
                sector_warps.select().where(
                    sector_warps.c.destination_sector_id == current.id,
                    sector_warps.c.is_bidirectional == True,  # noqa: E712
                )
            ).fetchall()
            for row in incoming_bidir:
                if row.source_sector_id in visited:
                    continue
                origin = self.db.query(Sector).filter(Sector.id == row.source_sector_id).first()
                if origin is None:
                    continue
                visited[origin.id] = current.id
                queue.append((origin, distance + 1))
            
            # Check warp tunnels
            tunnels = self.db.query(WarpTunnel).filter(
                WarpTunnel.origin_sector_id == current.id,
                WarpTunnel.status == WarpTunnelStatus.ACTIVE
            ).all()
            
            for tunnel in tunnels:
                dest = self.db.query(Sector).filter(Sector.id == tunnel.destination_sector_id).first()
                if dest and dest.id not in visited:
                    visited[dest.id] = current.id
                    queue.append((dest, distance + 1))
        
        # If we didn't reach the end sector
        if end_sector.id not in visited:
            return []
        
        # Reconstruct the path
        path = []
        current_id = end_sector.id
        
        while current_id is not None:
            current_sector = self.db.query(Sector).filter(Sector.id == current_id).first()
            if current_sector:
                path.insert(0, {
                    "sector_id": current_sector.sector_id,
                    "name": current_sector.name,
                    "type": current_sector.type.name
                })
            
            current_id = visited[current_id]
        
        # Calculate turn costs between each step
        for i in range(len(path) - 1):
            from_sector_id = path[i]["sector_id"]
            to_sector_id = path[i + 1]["sector_id"]
            
            from_sector = self.db.query(Sector).filter(Sector.sector_id == from_sector_id).first()
            to_sector = self.db.query(Sector).filter(Sector.sector_id == to_sector_id).first()
            
            # Check if direct warp (either direction for bidirectional
            # sector_warps rows) or tunnel.
            if self._is_directly_connected(from_sector, to_sector):
                path[i + 1]["turn_cost"] = self._calculate_warp_cost(from_sector, to_sector, None)
                path[i + 1]["connection_type"] = "warp"
            else:
                # Must be a tunnel
                tunnel = self.db.query(WarpTunnel).filter(
                    WarpTunnel.origin_sector_id == from_sector.id,
                    WarpTunnel.destination_sector_id == to_sector.id,
                    WarpTunnel.status == WarpTunnelStatus.ACTIVE
                ).first()
                
                if tunnel:
                    path[i + 1]["turn_cost"] = tunnel.turn_cost
                    path[i + 1]["connection_type"] = "tunnel"
                else:
                    path[i + 1]["turn_cost"] = 999  # Should not happen
                    path[i + 1]["connection_type"] = "unknown"
        
        # Set turn cost for first sector to 0
        if path:
            path[0]["turn_cost"] = 0
            path[0]["connection_type"] = "start"
        
        return path
    
    def _is_directly_connected(self, from_sector: Sector, to_sector: Sector) -> bool:
        """True if there's a usable direct warp from ``from_sector`` to ``to_sector``.

        bang's translator stores bidirectional warps as a single row
        (source=A, dest=B, is_bidirectional=True). The reverse traversal
        is therefore NOT in ``from_sector.outgoing_warps`` — we have to
        also accept ``to_sector`` as the source of a row that points back
        to ``from_sector`` when that row is bidirectional. See
        DOCS/PLANS/bang-integration-schema-map.md.
        """
        if to_sector in from_sector.outgoing_warps:
            return True
        reverse_row = self.db.execute(
            sector_warps.select().where(
                sector_warps.c.source_sector_id == to_sector.id,
                sector_warps.c.destination_sector_id == from_sector.id,
                sector_warps.c.is_bidirectional == True,  # noqa: E712
            )
        ).first()
        return reverse_row is not None

    def _check_direct_warp(self, current_sector_id: int, destination_sector_id: int, ship: Ship) -> Tuple[bool, int, str]:
        """Check if a direct warp is possible and calculate turn cost."""
        # Get sector objects
        current_sector = self.db.query(Sector).filter(Sector.sector_id == current_sector_id).first()
        destination_sector = self.db.query(Sector).filter(Sector.sector_id == destination_sector_id).first()

        if not current_sector or not destination_sector:
            return False, 0, "Invalid sector IDs"

        # Check if destination is directly connected (either direction
        # for bidirectional sector_warps rows).
        if not self._is_directly_connected(current_sector, destination_sector):
            return False, 0, "Sectors are not directly connected"

        # Calculate turn cost
        turn_cost = self._calculate_warp_cost(current_sector, destination_sector, ship)

        return True, turn_cost, "Direct warp available"
    
    def _has_player_gate(self, current_sector_id: int, destination_sector_id: int) -> Optional[WarpTunnel]:
        """The ACTIVE player-built warp gate connecting origin ->
        destination (FIX 7), or None. Player gates are one-way ARTIFICIAL
        tunnels with created_by_player_id set and a flat 0-turn cost; they
        outrank a parallel direct warp so the charged cost matches the
        advertised 0. Returns the tunnel row itself (not just a bool) so the
        caller can run access-control + toll collection (WO-GWQ-GATE-TOLL)
        against the SAME row without a second query."""
        current_sector = self.db.query(Sector).filter(
            Sector.sector_id == current_sector_id
        ).first()
        destination_sector = self.db.query(Sector).filter(
            Sector.sector_id == destination_sector_id
        ).first()
        if not current_sector or not destination_sector:
            return None

        tunnel = self.db.query(WarpTunnel).filter(
            WarpTunnel.origin_sector_id == current_sector.id,
            WarpTunnel.destination_sector_id == destination_sector.id,
            WarpTunnel.status == WarpTunnelStatus.ACTIVE,
        ).first()
        return tunnel if tunnel is not None and _is_player_gate(tunnel) else None

    def _check_warp_tunnel(self, current_sector_id: int, destination_sector_id: int, ship: Ship) -> Tuple[bool, int, str]:
        """Check if a warp tunnel is available and calculate turn cost."""
        # Get sector objects
        current_sector = self.db.query(Sector).filter(Sector.sector_id == current_sector_id).first()
        destination_sector = self.db.query(Sector).filter(Sector.sector_id == destination_sector_id).first()

        if not current_sector or not destination_sector:
            return False, 0, "Invalid sector IDs"

        # Check for active warp tunnel (outgoing direction)
        tunnel = self.db.query(WarpTunnel).filter(
            WarpTunnel.origin_sector_id == current_sector.id,
            WarpTunnel.destination_sector_id == destination_sector.id,
            WarpTunnel.status == WarpTunnelStatus.ACTIVE
        ).first()

        # If no outgoing tunnel, check for bidirectional tunnel in reverse direction
        if not tunnel:
            tunnel = self.db.query(WarpTunnel).filter(
                WarpTunnel.origin_sector_id == destination_sector.id,
                WarpTunnel.destination_sector_id == current_sector.id,
                WarpTunnel.is_bidirectional == True,
                WarpTunnel.status == WarpTunnelStatus.ACTIVE
            ).first()

        if not tunnel:
            return False, 0, "No active warp tunnel found"

        # WO-WC — lazy-on-read lifetime collapse (async-workers.md § Warp-tunnel
        # collapse: "expected_lifetime is checked when a ship attempts traversal.
        # If expected_lifetime is past, the tunnel transitions to COLLAPSED and
        # traversal fails."). This is the single move-validation gate and runs
        # BEFORE any turn is charged, so an expired tunnel never moves the ship.
        # Mirrors the existing max_uses collapse semantics — both are canon.
        # (Player gates are handled separately below and carry no lifetime.)
        if _tunnel_collapse_due(tunnel):
            # Lock the tunnel row before flipping status so a concurrent
            # traversal can't double-flip (single-writer; same lock discipline
            # as the player-row lock in move_player_to_sector).
            locked = self.db.query(WarpTunnel).filter(
                WarpTunnel.id == tunnel.id
            ).with_for_update().first()
            if locked is not None and locked.status == WarpTunnelStatus.ACTIVE:
                locked.status = WarpTunnelStatus.COLLAPSED
                # The traversal fails, so the caller (move_player_to_sector)
                # returns BEFORE _execute_movement's commit. Persist the
                # COLLAPSED transition on its own — canon requires the tunnel to
                # actually transition, not just refuse this one traversal. This
                # is an isolated, idempotent state flip safe to commit here.
                try:
                    self.db.commit()
                except Exception as e:
                    logger.error("Failed to persist tunnel COLLAPSED flip: %s", e)
                    self.db.rollback()
            return False, 0, "This warp tunnel has collapsed and can no longer be traversed"

        # WO-LW — latent-tunnel per-player reveal gate (ADR-0045 /
        # aria-companion.md § Warp discovery). A latent tunnel stays invisible —
        # and untraversable — to a player until they personally hold a
        # revealed/traversed knowledge row for it (discovered via a scan). A
        # non-latent tunnel is unaffected: visible and usable as today. Player
        # gates (handled below) are intrinsically known to anyone who can see
        # the gate and are never latent.
        if getattr(tunnel, "is_latent", False) and not _is_player_gate(tunnel):
            traverser_id = getattr(ship, "owner_id", None) if ship else None
            if traverser_id is None or not _player_knows_warp(self.db, traverser_id, tunnel):
                return False, 0, "No active warp tunnel found"

        # Player-built warp gate: 0 turns flat, no ship-type multipliers and
        # no max(1, ...) clamp (warp-gates.md). Strictly one-way: the reverse
        # branch above only matches is_bidirectional rows and gates are
        # created is_bidirectional=False, so a gate matched here is
        # guaranteed origin -> destination.
        if _is_player_gate(tunnel):
            # WG1 access-mode enforcement (warp-gates.md "Access control"): a player-built gate
            # honors its access_requirements mode (PUBLIC/TEAM_ONLY/PRIVATE/WHITELIST/ALLIANCE).
            # NOTE (WO-GWQ-GATE-TOLL audit finding): this branch is UNREACHABLE from
            # move_player_to_sector for a real player-gate move — MovementService._has_player_gate
            # already matches any ACTIVE player-built gate connecting these two sectors and takes
            # move_player_to_sector's OWN player-gate branch first (FIX 7's "prefer the 0-turn
            # gate" precedence), returning before _check_warp_tunnel is ever called. The real
            # enforcement point (access mode + faction-rep layers + toll) now lives in that
            # branch. This copy is kept as a harmless defensive backstop (and this function is
            # still reachable from other callers that don't go through _has_player_gate first),
            # but do not rely on it as THE enforcement point.
            traverser_id = getattr(ship, "owner_id", None) if ship else None
            if traverser_id is not None:
                from src.services.warp_gate_service import check_traversal_access, WarpGateError
                traverser = self.db.query(Player).filter(Player.id == traverser_id).first()
                if traverser is not None:
                    try:
                        check_traversal_access(self.db, traverser, tunnel)
                    except WarpGateError as e:
                        return False, 0, e.detail
            return True, tunnel.turn_cost, "Warp gate available"

        # Get base turn cost
        turn_cost = tunnel.turn_cost

        # WO-GWQ-TUNNELTYPE: the QUANTUM/UNSTABLE 50% non-warp-capable
        # surcharge above this comment minted a type-based cost split that
        # sectors.md:47 does not document (NATURAL/ARTIFICIAL are
        # "indistinguishable in routing cost and stability") and has been
        # removed.
        # NO-CANON: warp-capable ships still get a 20% turn-cost reduction on
        # any tunnel type. This is ship-based, not tunnel-type-based, so it
        # survives the type-vocab convergence, but it is undocumented in
        # sectors.md — flagged for a canon decision, not removed.
        if ship and getattr(ship, 'warp_capable', False):
            turn_cost = max(1, int(turn_cost * 0.8))  # 20% reduction for warp-capable ships

        # Maintenance performance-band SPEED modifier (ships.md:68-75), applied
        # to natural-tunnel traversal exactly like the warp path. The neutral
        # "Good" band leaves this unchanged. NOT applied to the player-gate
        # branch above — gates are 0-turn flat with no multipliers
        # (warp-gates.md), the same reason they skip the warp-capable reduction.
        turn_cost = max(1, int(turn_cost * self._maintenance_speed_multiplier(ship)))

        return True, turn_cost, "Warp tunnel available"
    
    def _maintenance_speed_multiplier(self, ship: Optional[Ship]) -> float:
        """Turn-cost multiplier from the ship's maintenance performance band's
        SPEED modifier (ships.md:68-75 "Performance bands" — the Speed column).

        Canon reads the band's ``speed`` as a fractional SPEED change: a faster
        ship (positive speed, e.g. Pristine +0.05) reaches a sector for fewer
        turns; a slower ship (negative speed, e.g. Worn -0.05 / Critical -0.50)
        costs more. Turn cost is therefore scaled by ``1 / (1 + speed)`` so the
        relationship is reciprocal to speed:

            Pristine (+0.05) -> 1/1.05 ≈ 0.952  (cheaper)
            Good     ( 0.00) -> 1/1.00  = 1.000  (UNCHANGED — reproduce-exactly)
            Worn     (-0.05) -> 1/0.95 ≈ 1.053  (pricier)
            Critical (-0.50) -> 1/0.50  = 2.000  (double cost)

        The neutral "Good" band (75-89%, speed 0.0) returns exactly 1.0 so a
        ship in good condition costs exactly as it did before this wiring. The
        denominator is floored defensively (a hypothetical speed <= -1 would
        otherwise divide by zero); canon's worst band is -0.50 so the floor
        never bites in practice. Best-effort: a maintenance-read hiccup leaves
        the cost unchanged (multiplier 1.0) and never strands a move.

        Imports ``maintenance_band`` + ``effective_condition`` lazily, mirroring
        combat_service's ``from src.services.maintenance_service import
        combat_multiplier`` in-function import pattern.
        """
        if ship is None:
            return 1.0
        try:
            from src.services.maintenance_service import (
                maintenance_band,
                effective_condition,
            )
            band = maintenance_band(effective_condition(ship))
            speed = float(band.get("speed", 0.0) or 0.0)
            if speed == 0.0:
                return 1.0  # neutral band — exactly unchanged
            denom = 1.0 + speed
            if denom <= 0.0:
                denom = 0.01  # defensive: never divide by zero (canon worst is -0.50)
            return 1.0 / denom
        except Exception as e:
            logger.error("Maintenance speed-band read failed (cost unchanged): %s", e)
            return 1.0

    def _calculate_warp_cost(self, from_sector: Sector, to_sector: Sector, ship: Optional[Ship]) -> int:
        """Calculate turn cost for a direct warp between sectors.

        Uniform per-warp cost (movement.md:13, movement.md:24): 'Ship.current_speed
        does not change the turn cost of any traversal' and 'A Scout (speed 2.5)
        and a Cargo Hauler (speed 0.5) both pay the same'. Every hull type pays
        warp.turn_cost, adjusted only by the ship's maintenance-band speed
        factor (ships.md:89) — never by ship type or current_speed.
        """
        # Find the warp connection details. Try the forward direction first;
        # if missing, try the reverse direction with is_bidirectional=true
        # (the bang translator stores A↔B as ONE row per the schema map,
        # so reverse traversal reads the same row).
        warp = self.db.query(sector_warps).filter(
            sector_warps.c.source_sector_id == from_sector.id,
            sector_warps.c.destination_sector_id == to_sector.id
        ).first()

        if not warp:
            warp = self.db.query(sector_warps).filter(
                sector_warps.c.source_sector_id == to_sector.id,
                sector_warps.c.destination_sector_id == from_sector.id,
                sector_warps.c.is_bidirectional == True,  # noqa: E712
            ).first()

        if not warp:
            return 999  # Very high cost if no direct connection (should not happen)
        
        # Get base turn cost from the warp
        base_cost = warp.turn_cost if warp.turn_cost else 1

        # Maintenance performance-band SPEED modifier (ships.md:68-75). A worn
        # ship moves slower (costs more turns); a pristine ship moves faster
        # (costs fewer). The neutral "Good" band leaves this exactly 1.0, so a
        # ship in good condition is unchanged. This is the ONLY per-ship
        # adjustment to the uniform warp.turn_cost (movement.md:13/:24) —
        # never ship type or current_speed.
        base_cost = int(base_cost * self._maintenance_speed_multiplier(ship))

        # No turn cost can be less than 1
        return max(1, base_cost)
    
    # Hull damage one armored mine deals to a hostile ship entering the sector.
    # Proposed in ADR-0083 (pending Max bless); deterrent-scale, non-lethal
    # (hull is floored at 1.0 so a minefield cripples but does not destroy —
    # lethal mines / destruction-on-zero is a documented future refinement).
    MINE_DETONATION_DAMAGE = 200.0

    def _detonate_sector_mines(self, player: Player, sector: Sector) -> None:
        """Detonate one hostile armored mine in `sector` against the entering ship.

        Mines live in sector.defenses {mines, mine_owner_id, mine_team_id}. Same
        non-null team is friendly (no detonation); the owner is never mined by
        their own field. One mine is consumed per hostile entry.
        """
        defenses = sector.defenses or {}
        mine_count = int(defenses.get("mines", 0) or 0)
        owner_id = defenses.get("mine_owner_id")
        if mine_count <= 0 or not owner_id or str(owner_id) == str(player.id):
            return

        owner_team = defenses.get("mine_team_id")
        entrant_team = str(player.team_id) if player.team_id else None
        if owner_team and entrant_team and owner_team == entrant_team:
            return  # friendly minefield — same team

        if not player.current_ship:
            return

        from src.services.combat_service import CombatService
        combat = CombatService(self.db)._ensure_combat_state(player.current_ship)
        hull = float(combat.get("hull", 0) or 0)
        # Floor at 1.0: a mine cripples, it does not destroy (v1; see ADR-0083).
        combat["hull"] = max(1.0, round(hull - self.MINE_DETONATION_DAMAGE, 1))
        flag_modified(player.current_ship, "combat")

        new_def = dict(defenses)
        remaining = mine_count - 1
        new_def["mines"] = remaining
        if remaining <= 0:
            new_def["mines"] = 0
            new_def["mine_owner_id"] = None
            new_def["mine_team_id"] = None
        sector.defenses = new_def
        flag_modified(sector, "defenses")

        logger.info(
            f"Mine detonated on player {player.id} entering sector {sector.sector_id}: "
            f"-{self.MINE_DETONATION_DAMAGE} hull (now {combat['hull']}), {remaining} mine(s) remain"
        )

    def _execute_movement(self, player: Player, destination_sector_id: int, turn_cost: int) -> Dict[str, Any]:
        """Execute a player's movement to a destination sector."""
        old_sector_id = player.current_sector_id
        # Snapshot BEFORE the region mutation below, so the WS room-hop can
        # tell whether this move crossed a region boundary (WO-RT-ROOM-HOP).
        old_region_id = player.current_region_id

        # Fetch the destination up front: the move needs its region for the
        # player sync below, and the response reuses it for sector_info.
        # Fail fast BEFORE any player mutation — moving a player into a
        # sector that doesn't exist would strand them with a stale region.
        destination_sector = self.db.query(Sector).filter(Sector.sector_id == destination_sector_id).first()
        if not destination_sector:
            return {
                "success": False,
                "message": "Destination sector not found",
                "turn_cost": 0
            }

        # Update player position and clear all location state
        player.current_sector_id = destination_sector_id
        # Keep current_region_id in sync with the destination — warp tunnels
        # cross regions, and a stale region makes the region-filtered routes
        # (e.g. /player/current-sector) 404 until the next correction
        player.current_region_id = destination_sector.region_id
        player.is_docked = False  # Player is no longer docked at a port
        player.is_landed = False  # Player is no longer landed on a planet
        player.current_port_id = None  # Clear dangling port reference
        player.current_planet_id = None  # Clear dangling planet reference
        # WO-DOCK-500 Leg 1: warping away from a port is an implicit undock, so
        # release the docking-slip occupancy too — otherwise the row orphans and
        # the next dock 500s on the UNIQUE player_id (the trading /undock path
        # already releases; warp/quantum/hangar/tow did not).
        from src.services.docking_service import release as _release_docking_slip
        _release_docking_slip(self.db, None, player)
        
        # Update ship position
        if player.current_ship:
            player.current_ship.sector_id = destination_sector_id

            # Carrier ship-hangar ride-along (WO-AE; ships.md:340). When a
            # Carrier moves, every docked passenger's ship + pilot follows to
            # the destination at 0 turns for the passenger. Best-effort — a
            # hangar hiccup must never strand the Carrier's own move; it rides
            # this method's single commit below.
            if player.current_ship.hangar and player.current_ship.hangar.get("docked"):
                try:
                    from src.services.hangar_service import HangarService
                    HangarService(self.db).carry_hangared_ships(
                        player.current_ship, destination_sector_id
                    )
                except Exception as e:
                    logger.error("Carrier hangar ride-along hook failed: %s", e)

            # Tractor tow ride-along (WO-AF; ships.md:354). When a HAULER moves,
            # the towed ship's sector follows and the towed pilot pays 0 turns.
            # The surcharge the hauler pays for this is already folded into
            # turn_cost by the caller. Best-effort — a tow hiccup must not strand
            # the hauler's own move; it rides this method's single commit below.
            if player.current_ship.tow_state and player.current_ship.tow_state.get("towed_ship_id"):
                try:
                    from src.services.tow_service import TowService
                    TowService(self.db).carry_towed_ship(
                        player.current_ship, destination_sector_id
                    )
                except Exception as e:
                    logger.error("Tractor tow ride-along hook failed: %s", e)

        # Mine detonation: hostile armored mines in the destination detonate
        # against the entering ship (combat.md "mines damage hostile entrants";
        # ADR-0083 damage model). Best-effort — a hook failure must never strand
        # the move; it rides the move's own commit below.
        try:
            self._detonate_sector_mines(player, destination_sector)
        except Exception as e:
            logger.error("Mine detonation hook failed: %s", e)

        # Consume turns
        spend_turns(player, turn_cost)

        # ARIA consciousness hook — movement counts as interaction
        try:
            player.aria_total_interactions += 1
            thresholds = {50: (2, 1.1), 150: (3, 1.2), 400: (4, 1.35), 1000: (5, 1.5)}
            for threshold, (level, multiplier) in thresholds.items():
                if player.aria_total_interactions >= threshold and player.aria_consciousness_level < level:
                    player.aria_consciousness_level = level
                    player.aria_bonus_multiplier = multiplier
        except Exception as e:
            logger.error("Failed ARIA hook during movement: %s", e)

        # ARIA exploration map — the per-sector visit record aria-companion.md
        # documents. This table is the known-graph source for course plotting
        # (ADR-0072): without the row ARIA has no memory of ever being here
        # and refuses to plot back. Best-effort like the consciousness hook
        # above; rides the move's own commit.
        try:
            from src.models.aria_personal_intelligence import ARIAExplorationMap
            visit = (
                self.db.query(ARIAExplorationMap)
                .filter(
                    ARIAExplorationMap.player_id == player.id,
                    ARIAExplorationMap.sector_id == destination_sector.id,
                )
                .first()
            )
            if visit:
                visit.visit_count = (visit.visit_count or 0) + 1
                visit.last_visit = datetime.utcnow()
            else:
                self.db.add(
                    ARIAExplorationMap(
                        player_id=player.id,
                        sector_id=destination_sector.id,
                    )
                )
                # Emergent faction-rep (ADR-0032): "First-scan a NEBULA /
                # BLACK_HOLE / ANOMALY / WARP_STORM sector | +15 Nova Scientific
                # Institute" (factions-and-teams.md NS table). IDEMPOTENT BY
                # CONSTRUCTION: this is the new-row branch — it runs only the
                # FIRST time this (player, sector) pair is recorded, i.e. the
                # player's first scan/arrival in the sector. Subsequent visits
                # take the `if visit:` branch above and never re-award.
                #
                # Routed through the ADR-0032 dispatcher (the single canon entry
                # point), flush-only, riding this method's single commit below —
                # exactly like the KILL_PIRATE_NPC combat hook. Gated on the two
                # canon research-sector types that have a populated Sector.type
                # value (NEBULA, BLACK_HOLE); ANOMALY/WARP_STORM are un-columned
                # and so unrepresentable here (flagged, not invented). DOUBLE-
                # FIRE SAFE: no prior faction-rep hook exists at this site (the
                # ARIA/medal/formation hooks are disjoint signals).
                if destination_sector.type in (
                    SectorType.NEBULA,
                    SectorType.BLACK_HOLE,
                ):
                    try:
                        from src.services.emergent_reputation_service import (
                            apply_emergent_action,
                        )
                        apply_emergent_action(
                            self.db,
                            player,
                            "NOVA_FIRST_SCAN_RESEARCH_SECTOR",
                            {"sector_id": destination_sector.sector_id},
                        )
                    except Exception as e:
                        logger.error(
                            "Failed NOVA_FIRST_SCAN_RESEARCH_SECTOR emergent-rep "
                            "hook: %s", e
                        )
                # Award rank points for discovering a new sector (first visit only).
                # Idempotent by construction: this is the new-row branch, which
                # fires exactly once per (player, sector) pair — the `if visit:`
                # branch above handles all subsequent arrivals and never re-awards.
                #
                # Flush the ARIAExplorationMap insert into the outer txn FIRST so
                # it is validated there (fails loudly if bad — same as before this
                # change). Then wrap the rank award in a SAVEPOINT (begin_nested):
                # if award_rank_points' internal flush hits a DB error, only the
                # savepoint rolls back; the outer move txn is preserved and the
                # subsequent db.commit() still succeeds.
                self.db.flush()
                try:
                    from src.services.ranking_service import RankingService as _RS
                    _expl_pts = _RS.calculate_exploration_points()
                    with self.db.begin_nested():
                        _RS(self.db).award_rank_points(player.id, _expl_pts, "exploration")
                except Exception as e:
                    logger.error("Failed to award exploration rank points: %s", e)
        except Exception as e:
            logger.error("Failed ARIA exploration-map hook during movement: %s", e)

        # Special-formation discovery hook (WO-CA). Arriving in a sector that is
        # a formation's anchor — or one of its interior sectors — first-observes
        # that formation: flips is_discovered False→True and back-fills its public
        # name. Mirrors the planet/feature discovery pattern (discovery_service),
        # and like the ARIA/medal hooks above it is best-effort and flush-only, so
        # a formation hiccup never strands the move and the flip rides this
        # method's single commit below.
        try:
            from src.services.special_formation_service import flip_formation_discovery
            flip_formation_discovery(self.db, player, destination_sector)
        except Exception as e:
            logger.error("Special-formation discovery hook failed during movement: %s", e)

        # Exploration medal dispatch hook (ADR-0028 / medals lane). The
        # ARIAExplorationMap table above is the canonical unique-sector visit
        # record — one row per (player, sector) — so its DISTINCT-sector count
        # for this player IS the player's sectors_visited statistic. We dispatch
        # here, AFTER the visit row is added/incremented but BEFORE this method's
        # single commit, so the medal-award SAVEPOINT folds into the same commit
        # exactly like the combat medal hook. Best-effort: a medal hiccup must
        # never strand the move (mirrors the ARIA hooks above). The medals-lane
        # hook is idempotent (UNIQUE(player_id, medal_id) + threshold gating), so
        # it no-ops on every move except the one that first crosses 500 sectors —
        # never re-awards.
        try:
            _dispatch_exploration_medals(
                self.db,
                player,
                {"sectors_visited": self._count_unique_sectors_visited(player.id)},
            )
        except Exception as e:
            logger.error("Exploration medal dispatch hook failed during movement: %s", e)

        # Updates player's presence in sector records
        self._update_player_presence(player, old_sector_id, destination_sector_id)

        # Snapshot the destination region + WS connection key BEFORE the
        # commit, while destination_sector's ORM attributes are still loaded
        # in this session. The WS connection key is the User id (str(user.id)
        # at connect time); Player.user_id IS that user id.
        new_region_id = destination_sector.region_id
        mover_ws_user_id = str(player.user_id)

        # Commit changes
        self.db.commit()

        # Room-hop the WS registry to the OLD/NEW sector (and region, if this
        # move crossed one) so subscribers' who's-here / region-scoped views
        # stay live instead of frozen at connect-time (WO-RT-ROOM-HOP). Done
        # AFTER the commit so subscribers never observe pre-commit presence,
        # and strictly best-effort — a WS hiccup must NOT break the move
        # (mirrors _dispatch_hostile_detected / the ARIA/medal hooks above).
        self._broadcast_sector_presence(
            old_sector_id, destination_sector_id, mover_ws_user_id,
            old_region_id=old_region_id, new_region_id=new_region_id,
        )

        # Get sector information for response (sector fetched and
        # existence-checked pre-mutation above)
        sector_info = {
            "id": destination_sector.sector_id,
            "name": destination_sector.name,
            "type": destination_sector.type.name,
            "hazard_level": destination_sector.hazard_level,
            "radiation_level": destination_sector.radiation_level
        }
        
        return {
            "success": True,
            "message": f"Moved to Sector {destination_sector_id}",
            "turn_cost": turn_cost,
            "sector": sector_info,
            "turns_remaining": player.turns
        }
    
    def _update_player_presence(self, player: Player, old_sector_id: int, new_sector_id: int) -> None:
        """Update player presence records in sectors.

        Locks both sector rows (ascending sector_id) before the JSONB
        read-modify-write: with NPC movers also rewriting players_present
        (npc_movement_service), an unlocked stale-array write here would
        silently erase or resurrect NPC presence entries. Lock order is
        Player → Sector — the caller already holds the player row, and
        ascending sector order matches every other NPC-system sector
        writer, so the paths cannot deadlock AB-BA.
        """
        old_sector = None
        new_sector = None
        for sid in sorted({old_sector_id, new_sector_id}):
            row = (
                self.db.query(Sector)
                .filter(Sector.sector_id == sid)
                .with_for_update()
                .first()
            )
            if row is None:
                continue
            if sid == old_sector_id:
                old_sector = row
            if sid == new_sector_id:
                new_sector = row

        if old_sector:
            # Remove player from old sector's players_present
            players_present = list(old_sector.players_present or [])
            player_entry = next((p for p in players_present if p.get("player_id") == str(player.id)), None)
            if player_entry:
                players_present.remove(player_entry)
            old_sector.players_present = players_present
            flag_modified(old_sector, 'players_present')

        if new_sector:
            # Add player to new sector's players_present
            players_present = list(new_sector.players_present or [])
            player_entry = {
                "player_id": str(player.id),
                "username": player.username,
                "ship_id": str(player.current_ship_id) if player.current_ship_id else None,
                "ship_name": player.current_ship.name if player.current_ship else "None",
                "ship_type": player.current_ship.type.name if player.current_ship else "None",
                "team_id": str(player.team_id) if player.team_id else None,
                "arrived_at": datetime.now().isoformat()
            }

            # Check if player is already in the list (shouldn't be, but safety check)
            existing = next((p for p in players_present if p.get("player_id") == str(player.id)), None)
            if existing:
                players_present.remove(existing)

            players_present.append(player_entry)
            new_sector.players_present = players_present
            flag_modified(new_sector, 'players_present')
    
    def _check_for_encounters(self, player: Player, sector_id: int) -> List[Dict[str, Any]]:
        """Check for encounters upon entering a sector."""
        encounters = []
        
        # Get the destination sector
        sector = self.db.query(Sector).filter(Sector.sector_id == sector_id).first()
        if not sector:
            return encounters
        
        # Check for other players (PvP opportunity)
        other_players = [p for p in sector.players_present if p.get("player_id") != str(player.id)]
        if other_players:
            encounters.append({
                "type": "players",
                "players": other_players,
                "threat_level": "varies"
            })
        
        # Check for special sector events
        if sector.type.name in ["BLACK_HOLE", "NEBULA", "ASTEROID_FIELD", "WORMHOLE"]:
            encounters.append({
                "type": "sector_hazard",
                "hazard": sector.type.name,
                "threat_level": "medium" if sector.hazard_level < 7 else "high"
            })
        
        # Check for sector drones — live hostile deployed Drone rows, mirroring
        # the attackable set defined in attack_sector_drones
        # (combat_service.py:1426-1431): same sector, not the moving player's
        # own drones, still standing (DEPLOYED/DAMAGED, health > 0).
        hostile_drone_count = self.db.query(Drone).filter(
            Drone.sector_id == sector.id,
            Drone.player_id != player.id,
            Drone.status.in_([DroneStatus.DEPLOYED.value, DroneStatus.DAMAGED.value]),
            Drone.health > 0,
        ).count()
        if hostile_drone_count > 0:
            encounters.append({
                "type": "drones",
                "count": hostile_drone_count,
                "threat_level": "low" if hostile_drone_count < 10 else "medium"
            })

        # Check for faction patrols (Wanted-status detection --
        # WO-RT-PATROL-ENCOUNTER, sector-presence.md "NPC faction
        # patrols"). Canon's pseudocode reads sector.defenses['patrol_ships']
        # as a list of squad dicts, but that key is ALREADY a live SCALAR
        # INT elsewhere in this codebase -- station siege-defense fire
        # power (combat_service.py _resolve_port_combat), admin's
        # security_level rollup, and MILITARY_ZONE seeding
        # (nexus_generation_service.py / bang_import_service.py) all
        # read/write it as an int; nexus_generation_service.py even
        # comments "patrol_ships MUST be a SCALAR INT, never a [list]".
        # The already-shipped police/pirate squad writer
        # (npc_spawn_service.py) hit this exact conflict already and
        # deliberately lands squad rows under a SEPARATE dedicated key --
        # POLICE_PATROL_DEFENSES_KEY = "police_patrol_ships" -- flagging
        # the divergence there rather than silently overloading
        # patrol_ships. This leg follows that precedent.
        #
        # NO-CANON / PARKED (flagged to the orchestrator, not silently
        # invented): the doc's pseudocode also has this leg call
        # "combat_resolver.attack_player(patrol, player) directly" as
        # non-optional, NPC-initiated combat. combat_service.py has no
        # entry point shaped for that -- attack_player requires two real
        # Player rows with a current_ship; a squad row is a plain dict,
        # not a Player. The squads npc_spawn_service seeds are already
        # placed as live NPCCharacter+Ship presence entries (added to
        # this same sector's players_present precisely "to make the NPCs
        # visible in COMMS and the combat target list"), and the
        # already-shipped v1 scope for those same squads explicitly
        # defers automatic engagement: npc_spawn_service.py's module
        # docstring says v1 has "no NPC-initiated combat", and
        # npc_engagement_service.py's docstring says "combat with the
        # arrived squad is player-initiated PvE via the existing attack
        # path". Auto-firing combat here would silently override that
        # documented decision without sign-off, so this leg stops at
        # detection -- an informational encounter entry, matching the
        # players/hazard/drones legs above, which are all pings the
        # client surfaces rather than forced combat calls.
        patrols = (sector.defenses or {}).get("police_patrol_ships")
        if isinstance(patrols, list):
            for patrol in patrols:
                if not isinstance(patrol, dict):
                    continue
                threshold = patrol.get("wanted_threshold")
                if threshold is None:
                    continue
                faction_code = patrol.get("faction_code")
                try:
                    matched = player.is_wanted_at(faction_code, threshold)
                except Exception:
                    continue
                if matched:
                    encounters.append({
                        "type": "faction_patrol",
                        "faction": faction_code,
                        "squad": patrol.get("squad_kind"),
                        "ship_count": patrol.get("ship_count"),
                        "threat_level": "high",
                        "engagement": "pursuit"
                    })

        return encounters
    
    def _check_for_tunnel_events(self, player: Player, from_sector_id: int, to_sector_id: int) -> List[Dict[str, Any]]:
        """Check for events during warp tunnel travel."""
        events = []
        
        # Get sectors
        from_sector = self.db.query(Sector).filter(Sector.sector_id == from_sector_id).first()
        to_sector = self.db.query(Sector).filter(Sector.sector_id == to_sector_id).first()
        
        if not from_sector or not to_sector:
            return events
        
        # Get the tunnel
        tunnel = self.db.query(WarpTunnel).filter(
            WarpTunnel.origin_sector_id == from_sector.id,
            WarpTunnel.destination_sector_id == to_sector.id
        ).first()
        
        if not tunnel:
            return events
        
        # Check for tunnel stability issues
        if tunnel.stability < 0.7:
            # Chance of tunnel instability causing issues
            if tunnel.stability < 0.5:
                # High instability = radiation exposure
                events.append({
                    "type": "radiation_exposure",
                    "severity": "high" if tunnel.stability < 0.3 else "medium",
                    "effect": "ship_damage"
                })

                # WO-GWQ-TUNNELTYPE: re-keyed off stability alone. The prior
                # gate was `tunnel.type.name in ["QUANTUM", "UNSTABLE"]`, a
                # non-canon type dependency. sector-presence.md:122-123
                # documents spacetime_anomaly as nested under this same
                # stability<0.5 branch alongside radiation_exposure, with no
                # type condition, so any sufficiently unstable tunnel now
                # qualifies regardless of type.
                events.append({
                    "type": "spacetime_anomaly",
                    "severity": "medium",
                    "effect": "random"
                })
        
        # Update tunnel usage counter
        if tunnel.max_uses is not None:
            tunnel.current_uses += 1
            
            # Check if tunnel is about to collapse
            if tunnel.max_uses - tunnel.current_uses <= 3:
                events.append({
                    "type": "tunnel_degradation",
                    "stability": tunnel.stability,
                    "remaining_uses": tunnel.max_uses - tunnel.current_uses,
                    "effect": "warning"
                })
                
                # If this was the last use, collapse the tunnel
                if tunnel.current_uses >= tunnel.max_uses:
                    tunnel.status = WarpTunnelStatus.COLLAPSED
                    events.append({
                        "type": "tunnel_collapse",
                        "severity": "high",
                        "effect": "permanent"
                    })
        
        return events