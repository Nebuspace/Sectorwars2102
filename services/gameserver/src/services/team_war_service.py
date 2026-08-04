"""Team-war score + victory (WO-BUILD-TEAM-WAR-SCORE-HOOK-VICTORY).

Declares / list / ceasefire already live on ``Team.member_roles['active_wars']``
(teams.py). This module wires the missing resolution loop:

* On a resolved PvP ship-kill, if attacker and defender are on different teams
  that hold a mutual ``status=active`` war entry, increment the kill score on
  both sides' JSONB entries.
* When either side's ``score.us`` reaches ``VICTORY_KILL_THRESHOLD``, flip both
  entries to ``status=ceased`` with ``cease_reason=victory`` (reward payout and
  UI deferred).

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

# NO-CANON provisional — factions-and-teams.md names victory rewards but gives
# no kill threshold. Banked for a DECISIONS.md ratification; thin v1 needs a
# concrete gate so wars can actually end without a manual ceasefire.
VICTORY_KILL_THRESHOLD = 10


def _active_war_vs(wars: list, target_team_id: str) -> Optional[dict]:
    for war in wars or []:
        if (
            war.get("target_team_id") == target_team_id
            and war.get("status") == "active"
        ):
            return war
    return None


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
    if a_score["us"] >= VICTORY_KILL_THRESHOLD:
        victory = True
        now = datetime.now(UTC).isoformat()
        for entry, winner, loser in (
            (a_entry, str(atid), str(dtid)),
            (d_entry, str(atid), str(dtid)),
        ):
            entry["status"] = "ceased"
            entry["ceased_at"] = now
            entry["cease_reason"] = "victory"
            entry["winner_team_id"] = winner
            entry["loser_team_id"] = loser

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
        logger.info(
            "team_war: victory team=%s over team=%s at %d kills",
            atid, dtid, a_score["us"],
        )
    return result
