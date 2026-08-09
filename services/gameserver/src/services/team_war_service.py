"""Team-war score + victory (WO-BUILD-TEAM-WAR-SCORE-HOOK-VICTORY).

Declares / list / ceasefire already live on ``Team.member_roles['active_wars']``
(teams.py). This module wires the missing resolution loop:

* On a resolved PvP ship-kill, if attacker and defender are on different teams
  that hold a mutual ``status=active`` war entry, increment the kill score on
  both sides' JSONB entries.
* When either side's ``score.us`` reaches ``VICTORY_KILL_THRESHOLD``, flip both
  entries to ``status=ceased`` with ``cease_reason=victory``, persist
  ``victory_at`` / ``winner_team_id`` / ``loser_team_id``, credit each winning
  team member ``VICTORY_PAYOUT_PER_MEMBER``, and emit a structured
  ``team_war_victory`` realtime event to both team rooms (UI still deferred).

Flush-only; caller owns commit. Lock both Team rows ascending-id (same
discipline as ``declare_war`` / fleet_service).
"""

from __future__ import annotations

import logging
from datetime import datetime, UTC
from typing import Any, Dict, Optional
from uuid import UUID

from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from src.models.player import Player
from src.models.team import Team

logger = logging.getLogger(__name__)

# Ratified 2026-08-09 (DECISIONS.md no-canon-magnitudes-batch-remainder) —
# factions-and-teams.md names victory rewards but gave no kill threshold or
# payout; both ratified as-is here.
VICTORY_KILL_THRESHOLD = 10
VICTORY_PAYOUT_PER_MEMBER = 5000


def _active_war_vs(wars: list, target_team_id: str) -> Optional[dict]:
    for war in wars or []:
        if (
            war.get("target_team_id") == target_team_id
            and war.get("status") == "active"
        ):
            return war
    return None


def _broadcast_team_event(team_id: UUID, payload: Dict[str, Any]) -> None:
    """Best-effort team-room WS push. Same idiom as team_reputation /
    combat_service: lazy-import connection_manager singleton, create_task,
    never block, never break the caller.
    """
    try:
        import asyncio

        from src.services.websocket_service import connection_manager

        asyncio.get_running_loop().create_task(
            connection_manager.broadcast_to_team(str(team_id), payload)
        )
    except RuntimeError:
        pass  # no running loop — sync/unit context
    except Exception:
        logger.exception(
            "team_war telemetry broadcast failed for type=%s (non-fatal)",
            payload.get("type"),
        )


def _emit_team_war_victory_event(
    *,
    winner_team_id: UUID,
    loser_team_id: UUID,
    victory_at: str,
    winner_score_us: int,
    loser_score_us: int,
) -> Dict[str, Any]:
    """Structured victory frame (event-only; no credits). Broadcast to both
    team rooms so either side's clients can react when a war board lands.
    """
    payload = {
        "type": "team_war_victory",
        "winner_team_id": str(winner_team_id),
        "loser_team_id": str(loser_team_id),
        "victory_at": victory_at,
        "cease_reason": "victory",
        "threshold": VICTORY_KILL_THRESHOLD,
        "score": {
            "winner_us": winner_score_us,
            "loser_us": loser_score_us,
        },
        "timestamp": victory_at,
    }
    _broadcast_team_event(winner_team_id, payload)
    _broadcast_team_event(loser_team_id, payload)
    return payload


def _apply_victory_payout(winner_team: Team) -> int:
    """Credit each winning-team member ``VICTORY_PAYOUT_PER_MEMBER`` on war
    victory. Flush-only, mirrors the module's caller-owns-commit convention.
    Returns the total amount credited (for logging/telemetry).
    """
    total = 0
    for member in winner_team.members or []:
        member.credits = int(getattr(member, "credits", 0) or 0) + VICTORY_PAYOUT_PER_MEMBER
        total += VICTORY_PAYOUT_PER_MEMBER
    return total


def record_pvp_kill(
    db: Session,
    attacker: Player,
    defender: Player,
) -> Optional[Dict[str, Any]]:
    """Credit a PvP ship-kill toward an active team war, if any.

    Returns a small result dict when a war was touched, else None.
    Never raises into combat — defensive wrapper recommended at call site.
    """
    atid = getattr(attacker, "team_id", None)
    dtid = getattr(defender, "team_id", None)
    if atid is None or dtid is None or atid == dtid:
        return None

    # Lock both teams ascending-id (declare_war / fleet deadlock discipline).
    locked: Dict[UUID, Team] = {}
    for tid in sorted((atid, dtid), key=lambda x: str(x)):
        row = (
            db.query(Team)
            .filter(Team.id == tid)
            .populate_existing()
            .with_for_update()
            .first()
        )
        if row is not None:
            locked[tid] = row

    attacker_team = locked.get(atid)
    defender_team = locked.get(dtid)
    if attacker_team is None or defender_team is None:
        return None

    if not attacker_team.member_roles:
        attacker_team.member_roles = {}
    if not defender_team.member_roles:
        defender_team.member_roles = {}

    a_wars = list(attacker_team.member_roles.get("active_wars") or [])
    d_wars = list(defender_team.member_roles.get("active_wars") or [])
    a_entry = _active_war_vs(a_wars, str(dtid))
    d_entry = _active_war_vs(d_wars, str(atid))
    if a_entry is None or d_entry is None:
        return None

    a_score = dict(a_entry.get("score") or {"us": 0, "them": 0})
    d_score = dict(d_entry.get("score") or {"us": 0, "them": 0})
    # Attacker's view: us = our kills of them. Defender's view: them = kills
    # scored against us (i.e. attacker's kills). Keep the pair mirrored.
    a_score["us"] = int(a_score.get("us") or 0) + 1
    d_score["them"] = a_score["us"]
    a_score["them"] = int(d_score.get("us") or 0)
    a_entry["score"] = a_score
    d_entry["score"] = d_score

    victory = False
    victory_event: Optional[Dict[str, Any]] = None
    victory_payout_total = 0
    if a_score["us"] >= VICTORY_KILL_THRESHOLD:
        victory = True
        victory_payout_total = _apply_victory_payout(attacker_team)
        now = datetime.now(UTC).isoformat()
        for entry, winner, loser in (
            (a_entry, str(atid), str(dtid)),
            (d_entry, str(atid), str(dtid)),
        ):
            entry["status"] = "ceased"
            entry["ceased_at"] = now
            entry["victory_at"] = now
            entry["cease_reason"] = "victory"
            entry["winner_team_id"] = winner
            entry["loser_team_id"] = loser

        victory_event = _emit_team_war_victory_event(
            winner_team_id=atid,
            loser_team_id=dtid,
            victory_at=now,
            winner_score_us=a_score["us"],
            loser_score_us=int(d_score.get("us") or 0),
        )

    # Write lists back (entries are mutable dicts already in the lists).
    attacker_team.member_roles["active_wars"] = a_wars
    defender_team.member_roles["active_wars"] = d_wars
    flag_modified(attacker_team, "member_roles")
    flag_modified(defender_team, "member_roles")

    result = {
        "attacker_team_id": str(atid),
        "defender_team_id": str(dtid),
        "attacker_score_us": a_score["us"],
        "victory": victory,
        "threshold": VICTORY_KILL_THRESHOLD,
    }
    if victory:
        result["winner_team_id"] = str(atid)
        result["loser_team_id"] = str(dtid)
        result["victory_at"] = a_entry.get("victory_at")
        result["event"] = victory_event
        result["victory_payout_total"] = victory_payout_total
        logger.info(
            "team_war: victory team=%s over team=%s at %d kills, payout=%dcr",
            atid, dtid, a_score["us"], victory_payout_total,
        )
    return result
