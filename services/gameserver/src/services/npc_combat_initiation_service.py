"""NPC-Initiated Combat — the generic "NPC attacks first" entry point
(WO-CMB-NPC-INITIATED-1, Max ruling 2026-07-10, superseding the two v1
player-initiated-PvE deferral docstrings in npc_engagement_service.py and
combat_service.py).

``initiate_npc_combat`` is the shared attack-initiation primitive both
police and pirates call into: police auto-engage a Wanted player in
patrolled space (lane B calls this from npc_engagement_service's
PendingEngagement ARRIVED path, once a squad member is ENGAGED and
co-located with the offender — the caller picks WHICH squad member attacks,
e.g. the Captain if present else the senior Marshal); pirates auto-engage
per canon aggression (lane C calls this from the pirate/patrol encounter
surface). Neither trigger caller is built here.

Lives in its own module rather than npc_engagement_service.py: that file's
own docstring scopes it to police/jurisdiction (ADR-0042 + police-forces.md
— PendingEngagement, squad routing, jurisdiction). Pirates have no
jurisdiction/PendingEngagement concept per faction-lore.md's spawn/encounter
model, so this module gives both lanes a shared, faction-agnostic home.

Resolution reuses combat_service.CombatService's existing engine via
composition (no new resolution mechanics — WO-CMB-NPC-INITIATED-1
constraint 1): ``_resolve_ship_combat`` now takes a symmetric
``attacker_ship: Optional[Ship] = None`` parameter (the NPC-attacker mirror
of the pre-existing ``defender_ship`` NPC-defender branch), so escape/flee
is inherited for free from the unchanged defender-side escape roll —
constraint 2 ("no zero-agency insta-death") is satisfied structurally, not
via a new mechanic.

Layered-consequence design, mirroring how attack_player/attack_npc_ship
already separate the shared resolver from per-context orchestration: this
module applies only the GENERIC mechanical consequences common to ANY
NPC-initiated fight (CombatLog, ship destruction/wreck, NPC KIA/respawn
processing). Faction-specific consequences — Marshal-kill Federation rep
deltas, pirate loot/drop tables, evade-arrest rep, cargo disposal (pirates
loot into their own hold; police confiscate to a depot per police-forces.md
outcome #3) — are the CALLER's responsibility, applied on the returned dict
exactly like attack_player/attack_npc_ship layer their own consequences
around the shared resolver today. ``cargo_stolen`` is returned raw (what the
resolver decided to take) but NOT transferred here for this reason.

Documented deferrals (flagged, not invented):
  - Surrender (police-forces.md "Engagement outcomes" #1 — a pre-combat
    choice to decline and pay a fine) is NOT built. No negotiation prompt.
    Flee agency is the existing defender combat-escape roll only.
  - The pirate "tribute branch" (faction-lore.md — demand tribute instead
    of opening fire; player picks fight/pay/flee) is also NOT built here.
    Both Surrender and tribute are lane B/C's concern: they call
    ``initiate_npc_combat`` only on the fight branch (declined/no offer).

Flush-only, never commits, never raises — mirrors
npc_engagement_service.route_engagement's established idiom, so this is
safe to call from a per-row SAVEPOINT (lane B's PendingEngagement sweep) or
a synchronous request-context caller (lane C). Always returns a dict (a
"success": bool rich result, matching combat_service.py's own
attack_player/attack_npc_ship convention) — never None, never a raised
exception.

The npc_combat_initiated realtime event is intentionally NOT emitted from
inside this module — ``emit_npc_combat_initiated`` is a separate,
directly-callable function the CALLER invokes POST-ITS-OWN-COMMIT, matching
the documented discipline of combat_service._emit_combat_phase_events
("POST-COMMIT... never able to touch the already-landed transaction").
"""

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from src.models.combat import CombatType
from src.models.combat_log import CombatLog
from src.models.npc_character import NPCArchetype, NPCCharacter
from src.models.player import Player
from src.models.region import RegionType
from src.models.sector import Sector
from src.models.ship import Ship, ShipStatus
from src.models.station import SECURITY_TIER_PROTECTED_MIN_RANK, Station

logger = logging.getLogger(__name__)


def initiate_npc_combat(
    db: Session,
    npc: NPCCharacter,
    defender: Player,
    sector: Sector,
    *,
    trigger: str,
    trigger_context: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """The NPC-attacks-first entry point (WO-CMB-NPC-INITIATED-1).

    ``npc`` is the single attacking NPC the CALLER has already selected
    (e.g. a police squad's Captain/senior Marshal, or a lone pirate) — this
    function resolves exactly ONE NPC-ship-vs-ONE-player-ship fight,
    matching the existing engine's fundamentally 1v1 shape (attack_npc_ship
    never fights multiple NPCs either). ``trigger`` is a free-form,
    caller-owned label (e.g. "wanted_status_engagement",
    "pirate_aggression") folded into the returned dict; ``trigger_context``
    is an opaque caller-owned passthrough. Neither is interpreted here.

    Always returns a dict with a "success" key — never None, never raises.
    On any guard failure, "success" is False with a "message" explaining
    why (mirrors attack_player/attack_npc_ship's own convention). On
    success, "success" is True regardless of who WON the fight — it means
    the attack was initiated and mechanically resolved.
    """
    try:
        return _initiate_npc_combat_inner(
            db, npc, defender, sector, trigger=trigger, trigger_context=trigger_context
        )
    except Exception:
        logger.exception(
            "initiate_npc_combat failed: npc=%s defender=%s trigger=%s",
            getattr(npc, "id", None), getattr(defender, "id", None), trigger,
        )
        return {"success": False, "message": "NPC-initiated combat failed unexpectedly"}


def _guard_failure(
    db: Session, npc: NPCCharacter, defender: Player, sector: Sector,
) -> Tuple[Optional[Ship], Optional[Dict[str, Any]]]:
    """All pre-resolution guards, isolated from the resolution/consequence
    logic below so neither function trips the complexity linter and each
    stays independently readable. Returns ``(npc_ship, None)`` when every
    guard passes (the caller reuses this fetched row rather than
    re-querying), or ``(None, failure_dict)`` on the first guard that
    fails."""
    # --- NPC-side guards -----------------------------------------------
    npc_ship = db.query(Ship).filter(Ship.id == npc.ship_id).first() if npc.ship_id else None
    if npc_ship is None or npc_ship.is_destroyed:
        return None, {"success": False, "message": "NPC has no active ship"}
    if npc.current_sector_id != sector.sector_id:
        return None, {"success": False, "message": "NPC is not in the target sector"}

    # --- Defender-side guards --------------------------------------------
    if defender.current_ship is None or defender.current_ship.is_destroyed:
        return None, {"success": False, "message": "Defender has no active ship"}
    if defender.current_sector_id != sector.sector_id:
        return None, {"success": False, "message": "Target is not in your sector"}

    # Docked-safety sanctuary: extends combat_service.py's
    # ERR_DOCKED_SHIP_PROTECTED gate (attack_player, ~:706-721) to
    # NPC-initiated attacks. [FLAG] canon's Guarantee #1 says "hostile
    # PLAYER" specifically, but the stated purpose ("ships docked at a
    # protected station are safe") reads attacker-agnostic — extending it
    # is the defensible default, approved with this flag on WO-CMB-NPC-
    # INITIATED-1's proposal.
    if defender.is_docked and defender.current_port_id:
        defender_station = (
            db.query(Station).filter(Station.id == defender.current_port_id).first()
        )
        if (
            defender_station is not None
            and defender_station.security_rank >= SECURITY_TIER_PROTECTED_MIN_RANK
        ):
            return None, {
                "success": False,
                "message": "ERR_DOCKED_SHIP_PROTECTED",
                "error": "ERR_DOCKED_SHIP_PROTECTED",
            }
    # Landed sanctuary: no security-tier gradient exists for planets in
    # this codebase, so a landed defender is unconditionally safe. Carried
    # over from an earlier superseded design for this WO — flagging since
    # it wasn't explicitly re-confirmed in this contract's ruling.
    if defender.is_landed:
        return None, {"success": False, "message": "Defender is landed and cannot be attacked"}

    # Terran-Space gate — [NO-CANON] AMENDMENT (Samantha ruling,
    # 2026-07-10, rides her DECISIONS row for Max's veto): do NOT reuse
    # combat_service._is_combat_allowed blindly. It blocks ALL combat in
    # TERRAN_SPACE, which would make police unable to engage a Wanted
    # player in the Federation's OWN jurisdiction — backwards per Max's
    # "police attack a Wanted player entering patrolled space" ruling.
    # LAW_ENFORCEMENT-archetype attackers bypass the block entirely;
    # every other archetype (pirates, etc.) keeps the identical rule
    # player-initiated combat already has.
    if npc.archetype != NPCArchetype.LAW_ENFORCEMENT:
        region_type = None
        if sector is not None and sector.cluster is not None and sector.cluster.region is not None:
            region_type = sector.cluster.region.region_type
        if region_type == RegionType.TERRAN_SPACE:
            return None, {"success": False, "message": "Combat is not allowed in this sector"}

    return npc_ship, None


def _initiate_npc_combat_inner(
    db: Session,
    npc: NPCCharacter,
    defender: Player,
    sector: Sector,
    *,
    trigger: str,
    trigger_context: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    trigger_context = trigger_context or {}

    npc_ship, failure = _guard_failure(db, npc, defender, sector)
    if failure is not None:
        return failure

    # --- Resolution (WO-CMB-NPC-INITIATED-1's one approved combat_service.py
    # touch: _resolve_ship_combat's symmetric attacker_ship parameter) -----
    from src.services.combat_service import CombatService, COMBAT_RESULT_TO_OUTCOME, \
        _combat_log_region_snapshot

    combat_service = CombatService(db)
    combat_result = combat_service._resolve_ship_combat(
        attacker=None, defender=defender, sector=sector, attacker_ship=npc_ship,
    )

    # --- CombatLog — no schema change: attacker_id stays NULL (no Player
    # behind the NPC), name/type snapshots preserve who attacked — the
    # literal mirror of attack_npc_ship's existing NPC-defender idiom,
    # just flipped to the attacker side. -----------------------------------
    defender_ship = defender.current_ship
    combat_log = CombatLog(
        combat_type=CombatType.SHIP_VS_SHIP.value,
        outcome=COMBAT_RESULT_TO_OUTCOME[combat_result["result"]],
        sector_id=sector.sector_id,
        sector_uuid=sector.id,
        region_id_snapshot=_combat_log_region_snapshot(sector),
        attacker_id=None,
        attacker_ship_id=npc_ship.id,
        attacker_ship_name=npc_ship.name,
        attacker_ship_type=npc_ship.type.value,
        defender_id=defender.id,
        defender_ship_id=defender.current_ship_id,
        defender_ship_name=defender_ship.name if defender_ship else None,
        defender_ship_type=defender_ship.type.value if defender_ship else None,
        rounds=combat_result["rounds"],
        attacker_drones=0,
        defender_drones=defender.defense_drones,
        attacker_drones_lost=combat_result["attacker_drones_lost"],
        defender_drones_lost=combat_result["defender_drones_lost"],
        attacker_damage_dealt=combat_result["attacker_damage_dealt"],
        defender_damage_dealt=combat_result["defender_damage_dealt"],
        cargo_looted=None,  # disposal is the caller's call — see module docstring
        combat_log=json.dumps(combat_result["combat_details"]),
        ended_at=datetime.now(timezone.utc),
    )
    db.add(combat_log)
    db.flush()  # populate combat_log timestamps / surface DB errors early

    dead_npc: Optional[NPCCharacter] = None

    # Defender's ship destroyed (the NPC attacker won): mirrors
    # _handle_ship_destruction's EXISTING "player's ship destroyed by a
    # non-player cause" call (attack_npc_ship's own
    # `self._handle_ship_destruction(attacker, None, "combat")` for the
    # symmetric case) — it already handles escape-pod ejection AND wreck
    # spawning internally, so no separate _spawn_cargo_wreck call is
    # needed here.
    if combat_result["defender_ship_destroyed"]:
        combat_service._handle_ship_destruction(defender, None, "npc_combat")

    # NPC's ship destroyed (the defender won): mirrors attack_npc_ship's
    # own NPC-destruction pattern exactly, just with the roles swapped —
    # defender is the killing-blow pilot.
    if combat_result["attacker_ship_destroyed"]:
        npc_ship.is_destroyed = True
        npc_ship.is_active = False
        npc_ship.status = ShipStatus.DESTROYED
        combat_service._spawn_cargo_wreck(
            destroyed_ship=npc_ship, cause="combat",
            original_owner=None, killing_blow_pilot=defender,
        )
        from src.services.npc_spawn_service import handle_npc_ship_destroyed
        dead_npc = handle_npc_ship_destroyed(
            db, npc_ship.id,
            killed_by_player_id=defender.id,
            combat_log_id=combat_log.id,
        )

    flag_modified(npc_ship, "combat")
    if defender_ship is not None:
        flag_modified(defender_ship, "combat")
    db.flush()

    return {
        "success": True,
        "message": combat_result["message"],
        "combat_result": combat_result["result"].name,
        "combat_log_id": str(combat_log.id),
        "npc_ship_destroyed": combat_result["attacker_ship_destroyed"],
        "defender_ship_destroyed": combat_result["defender_ship_destroyed"],
        "dead_npc": dead_npc,
        "npc_id": str(npc.id),
        "npc_display_name": npc.display_name,
        "npc_ship_id": str(npc_ship.id),
        "npc_ship_name": npc_ship.name,
        "defender_id": str(defender.id),
        "defender_ship_id": str(defender.current_ship_id) if defender.current_ship_id else None,
        "sector_id": sector.sector_id,
        "trigger": trigger,
        "trigger_context": trigger_context,
        "cargo_stolen": combat_result.get("cargo_stolen") or {},
    }


def emit_npc_combat_initiated(
    combat_log_id: uuid.UUID,
    npc: NPCCharacter,
    npc_ship: Ship,
    defender: Player,
    sector: Sector,
    *,
    trigger: str,
) -> None:
    """Best-effort ``npc_combat_initiated`` WS push — call this AFTER your
    own commit (see module docstring). Reuses the SAME transport
    ``combat_service._emit_combat_phase_events`` already uses:
    ``connection_manager.send_combat_update`` (personal copy to the
    defender, keyed by ``combat_id`` for correlation with the
    combat_started/round/resolved frames you should fire right after via
    the existing, unmodified ``combat_service._emit_combat_phase_events``)
    plus ``connection_manager.broadcast_to_sector`` for spectators. Mirrors
    ``_emit_teammate_under_attack``'s idiom: lazy import, grab the running
    loop, ``loop.create_task`` so the send never blocks, swallow every
    failure (no loop, no socket, a quiet client)."""
    try:
        import asyncio
        from src.services.websocket_service import connection_manager

        loop = asyncio.get_running_loop()
        combat_id = str(combat_log_id)
        event = {
            "type": "npc_combat_initiated",
            "combat_id": combat_id,
            "npc_id": str(npc.id),
            "npc_display_name": npc.display_name,
            "npc_archetype": npc.archetype.value if npc.archetype else None,
            "npc_ship_name": npc_ship.name,
            "npc_ship_type": npc_ship.type.name,
            "defender_id": str(defender.id),
            "defender_name": defender.username,
            "sector_id": sector.sector_id,
            "trigger": trigger,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        participants = [str(defender.user_id)] if defender.user_id else []
        if participants:
            loop.create_task(connection_manager.send_combat_update(
                combat_id, dict(event), participants
            ))
        loop.create_task(connection_manager.broadcast_to_sector(
            sector.sector_id, dict(event)
        ))
    except Exception:
        logger.debug(
            "Skipped npc_combat_initiated WS event (no loop or socket)",
            exc_info=True,
        )
