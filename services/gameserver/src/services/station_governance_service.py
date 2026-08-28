"""Syndicate governance votes — port-ownership.md:138-152 (LEG-301).

Pure resolution is unit-testable without a Session. The DB wrapper opens one
motion per (station, vote_type) while OPEN, freezes share weights at open,
and lazy-resolves after the canonical window.
"""
from __future__ import annotations

import logging
import random
import uuid
from datetime import datetime, timedelta, UTC
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from src.core import game_time
from src.models.player import Player
from src.models.port_ownership import StationGovernanceVote
from src.models.station import Station
from src.services.port_ownership_service import (
    PortOwnershipError,
    SYNDICATE_MODE_KEY,
    _ensure_primary_share,
    _lock_station,
)

logger = logging.getLogger(__name__)

# Canon table (port-ownership.md:138-144). Magnitudes are not invented here.
VOTE_SPECS: Dict[str, Dict[str, Any]] = {
    "tariff": {
        "threshold": 0.50,
        "veto": False,
        "window_hours": 72.0,
        "major_upgrade": False,
    },
    "upgrade": {
        "threshold": 0.50,
        "veto": True,
        "window_hours": 72.0,
        "major_upgrade": True,
        "capex_min": 500_000,
    },
    "sale": {
        "threshold": 0.66,
        "veto": True,
        "window_hours": 96.0,
        "major_upgrade": False,
    },
    "withdrawal": {
        "threshold": 0.50,
        "veto": False,
        "window_hours": 72.0,
        "major_upgrade": False,
    },
}

_VOTE_ALIASES = {
    "tariff": "tariff",
    "tariff_change": "tariff",
    "upgrade": "upgrade",
    "major_upgrade": "upgrade",
    "sale": "sale",
    "withdrawal": "withdrawal",
    "withdrawal_schedule": "withdrawal",
    "withdrawal-schedule": "withdrawal",
}

POSITIONS = frozenset({"for", "against", "absent", "veto", "against_veto"})
INACTIVE_DAYS = 30
INACTIVE_FORFEIT_DAYS = 90
QUORUM_FRAC = 0.50
VETO_HOLDER_FRAC = 0.25
VETO_OVERRIDE_FRAC = 0.75
TOTAL_STAKE = 100.0


def normalize_vote_type(raw: str) -> str:
    key = str(raw or "").strip().lower()
    if key not in _VOTE_ALIASES:
        raise PortOwnershipError(
            400,
            "vote_type must be tariff, upgrade, sale, or withdrawal "
            "(canon port-ownership.md vote-threshold table)",
        )
    return _VOTE_ALIASES[key]


def _capex_from_proposed(proposed_value: Any) -> Optional[int]:
    if isinstance(proposed_value, (int, float)) and not isinstance(proposed_value, bool):
        return int(proposed_value)
    if isinstance(proposed_value, dict):
        raw = proposed_value.get("capex", proposed_value.get("amount"))
        if isinstance(raw, (int, float)) and not isinstance(raw, bool):
            return int(raw)
    return None


def counted_stake(pct: float, inactive: bool) -> float:
    """Inactive owners (≥30 canonical days without login) count at 50% stake."""
    return float(pct) * (0.5 if inactive else 1.0)


def resolve_governance_ballots(
    *,
    vote_type: str,
    snapshot: List[Dict[str, Any]],
    ballots: List[Dict[str, Any]],
    rng_seed: int,
    window_closed: bool,
) -> Dict[str, Any]:
    """Stake-weighted quorum / threshold / veto / tiebreak. No DB."""
    spec = VOTE_SPECS[vote_type]
    by_id = {str(s["player_id"]): s for s in snapshot}
    latest: Dict[str, str] = {}
    for b in ballots:
        pid = str(b.get("player_id"))
        pos = str(b.get("position") or "")
        if pid in by_id and pos in POSITIONS:
            latest[pid] = pos

    represented = 0.0
    yes = 0.0
    voting = 0.0
    against_veto = 0.0
    veto_holders: List[str] = []
    for pid, share in by_id.items():
        pct = float(share.get("pct") or 0)
        inactive = bool(share.get("inactive"))
        weight = counted_stake(pct, inactive)
        pos = latest.get(pid)
        if pos is None:
            continue
        represented += pct  # quorum uses total stake represented
        if pos == "absent":
            continue
        voting += weight
        if pos == "for":
            yes += weight
        if pos == "against_veto":
            against_veto += weight
        if pos == "veto" and pct > VETO_HOLDER_FRAC * TOTAL_STAKE:
            veto_holders.append(pid)

    quorum_ok = represented >= QUORUM_FRAC * TOTAL_STAKE
    threshold = float(spec["threshold"])
    threshold_ok = yes >= threshold * TOTAL_STAKE

    veto_blocked = False
    veto_overridden = False
    if spec["veto"] and veto_holders:
        veto_blocked = True
        if voting > 0 and (against_veto / voting) >= VETO_OVERRIDE_FRAC:
            veto_overridden = True
            veto_blocked = False

    result: Dict[str, Any] = {
        "quorum_ok": quorum_ok,
        "represented_pct": represented,
        "yes_weight": yes,
        "threshold": threshold,
        "threshold_ok": threshold_ok,
        "veto_holders": veto_holders,
        "veto_overridden": veto_overridden,
        "window_closed": window_closed,
    }

    if veto_blocked:
        result["status"] = "vetoed"
        result["passed"] = False
        return result

    if quorum_ok and threshold_ok:
        result["status"] = "passed"
        result["passed"] = True
        return result

    if not window_closed:
        result["status"] = "open"
        result["passed"] = False
        return result

    # Tiebreak: highest single stakeholder's position; ties at the top random.
    ranked = sorted(
        snapshot,
        key=lambda s: (-float(s.get("pct") or 0), str(s.get("player_id"))),
    )
    if not ranked:
        result["status"] = "failed"
        result["passed"] = False
        return result
    top_pct = float(ranked[0].get("pct") or 0)
    tied = [s for s in ranked if float(s.get("pct") or 0) == top_pct]
    rng = random.Random(int(rng_seed))
    winner = rng.choice(tied)
    winner_id = str(winner["player_id"])
    winner_pos = latest.get(winner_id, "against")
    result["tiebreak_player_id"] = winner_id
    result["tiebreak_position"] = winner_pos
    result["status"] = "tiebreak"
    result["passed"] = winner_pos == "for"
    return result


def _player_last_login(player: Player) -> Optional[datetime]:
    return getattr(player, "last_game_login", None) or getattr(
        player, "last_activity_at", None
    )


def _player_inactive(player: Player, now: datetime) -> bool:
    login = _player_last_login(player)
    if login is None:
        return True
    return game_time.scaled_elapsed(login, now) >= timedelta(days=INACTIVE_DAYS)


def player_forfeit_eligible(player: Player, now: datetime) -> bool:
    """True when owner has been inactive long enough to forfeit syndicate stake."""
    login = _player_last_login(player)
    if login is None:
        return True
    return game_time.scaled_elapsed(login, now) >= timedelta(days=INACTIVE_FORFEIT_DAYS)


def _require_syndicate_share(station: Station, player: Player) -> Tuple[str, List[Dict[str, Any]]]:
    mode = (station.ownership or {}).get(SYNDICATE_MODE_KEY) or "solo"
    if mode != "syndicate":
        raise PortOwnershipError(
            403, "Governance votes are syndicate co-owners only"
        )
    shares = _ensure_primary_share(station)
    pid = str(player.id)
    if not any(str(s.get("player_id")) == pid for s in shares):
        raise PortOwnershipError(403, "Only syndicate co-owners may vote")
    return pid, shares


def _snapshot_for_open(
    db: Session, shares: List[Dict[str, Any]], now: datetime
) -> List[Dict[str, Any]]:
    ids = [uuid.UUID(str(s["player_id"])) for s in shares]
    players = {
        str(p.id): p
        for p in db.query(Player).filter(Player.id.in_(ids)).all()
    }
    snap = []
    for s in shares:
        pid = str(s["player_id"])
        pl = players.get(pid)
        snap.append(
            {
                "player_id": pid,
                "pct": int(s["pct"]),
                "inactive": True if pl is None else _player_inactive(pl, now),
            }
        )
    return snap


def _ballot_map(ballots: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for b in ballots:
        out[str(b.get("player_id"))] = dict(b)
    return out


def _maybe_resolve_row(row: StationGovernanceVote, now: datetime) -> None:
    if row.status != "open":
        return
    closed = now >= row.window_ends_at
    outcome = resolve_governance_ballots(
        vote_type=row.vote_type,
        snapshot=list(row.share_snapshot or []),
        ballots=list(row.ballots or []),
        rng_seed=int(row.rng_seed or 0),
        window_closed=closed,
    )
    if outcome["status"] == "open":
        return
    row.status = outcome["status"]
    row.outcome = outcome
    flag_modified(row, "outcome")


def cast_governance_vote(
    db: Session,
    station: Station,
    player: Player,
    *,
    vote_type: str,
    proposed_value: Any,
    voter_stake_pct: Any,
    position: str,
    now: Optional[datetime] = None,
    rng_seed: Optional[int] = None,
) -> Dict[str, Any]:
    """Open or ballot on a syndicate motion. Caller owns commit."""
    now = now or datetime.now(UTC)
    vote_type = normalize_vote_type(vote_type)
    spec = VOTE_SPECS[vote_type]
    pos = str(position or "").strip().lower()
    if pos not in POSITIONS:
        raise PortOwnershipError(
            400,
            "position must be for, against, absent, veto, or against_veto",
        )
    if pos == "veto" and not spec["veto"]:
        raise PortOwnershipError(400, "This vote type has no veto right")

    if spec["major_upgrade"]:
        capex = _capex_from_proposed(proposed_value)
        if capex is None or capex <= int(spec["capex_min"]):
            raise PortOwnershipError(
                400,
                "Major-upgrade votes require capex > 500000 credits",
            )

    station = _lock_station(db, station.id)
    pid, shares = _require_syndicate_share(station, player)

    open_rows = (
        db.query(StationGovernanceVote)
        .filter(
            StationGovernanceVote.station_id == station.id,
            StationGovernanceVote.vote_type == vote_type,
            StationGovernanceVote.status == "open",
        )
        .all()
    )
    for row in open_rows:
        _maybe_resolve_row(row, now)

    row = (
        db.query(StationGovernanceVote)
        .filter(
            StationGovernanceVote.station_id == station.id,
            StationGovernanceVote.vote_type == vote_type,
            StationGovernanceVote.status == "open",
        )
        .first()
    )

    if row is None:
        snapshot = _snapshot_for_open(db, shares, now)
        try:
            claimed = int(voter_stake_pct)
        except (TypeError, ValueError):
            raise PortOwnershipError(400, "voter_stake_pct must be an integer")
        mine = next(s for s in snapshot if s["player_id"] == pid)
        if claimed != int(mine["pct"]):
            raise PortOwnershipError(
                400,
                "voter_stake_pct must match the share locked at vote-open",
            )
        seed = int(rng_seed) if rng_seed is not None else random.SystemRandom().randint(1, 2**31 - 1)
        row = StationGovernanceVote(
            station_id=station.id,
            vote_type=vote_type,
            proposed_value=proposed_value if isinstance(proposed_value, dict) else {"value": proposed_value},
            status="open",
            opened_at=now,
            window_ends_at=game_time.scaled_deadline(float(spec["window_hours"]), start=now),
            share_snapshot=snapshot,
            ballots=[{"player_id": pid, "position": pos, "at": now.isoformat()}],
            rng_seed=seed,
        )
        db.add(row)
        db.flush()
        _maybe_resolve_row(row, now)
        logger.info(
            "Governance vote opened station=%s type=%s by %s",
            station.id, vote_type, pid,
        )
        return _vote_payload(row)

    # Existing open motion: lock weights from snapshot (no stake-buying).
    snap = list(row.share_snapshot or [])
    mine = next((s for s in snap if str(s.get("player_id")) == pid), None)
    if mine is None:
        raise PortOwnershipError(403, "Voter was not a co-owner at vote-open")
    try:
        claimed = int(voter_stake_pct)
    except (TypeError, ValueError):
        raise PortOwnershipError(400, "voter_stake_pct must be an integer")
    if claimed != int(mine["pct"]):
        raise PortOwnershipError(
            400,
            "voter_stake_pct must match the share locked at vote-open",
        )
    ballots = list(row.ballots or [])
    by_p = _ballot_map(ballots)
    by_p[pid] = {"player_id": pid, "position": pos, "at": now.isoformat()}
    row.ballots = list(by_p.values())
    flag_modified(row, "ballots")
    db.flush()
    _maybe_resolve_row(row, now)
    return _vote_payload(row)


def _vote_payload(row: StationGovernanceVote) -> Dict[str, Any]:
    live = resolve_governance_ballots(
        vote_type=row.vote_type,
        snapshot=list(row.share_snapshot or []),
        ballots=list(row.ballots or []),
        rng_seed=int(row.rng_seed or 0),
        window_closed=row.status != "open",
    )
    return {
        "id": str(row.id),
        "station_id": str(row.station_id),
        "vote_type": row.vote_type,
        "proposed_value": row.proposed_value,
        "status": row.status,
        "window_ends_at": row.window_ends_at.isoformat() if row.window_ends_at else None,
        "share_snapshot": row.share_snapshot,
        "ballots": row.ballots,
        "resolution": row.outcome or live,
    }
