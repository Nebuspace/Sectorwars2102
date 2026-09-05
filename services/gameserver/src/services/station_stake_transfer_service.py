"""Syndicate stake-transfer propose / approve / reject (LEG-4236).

Canon: FEATURES/economy/port-ownership.md § Syndicate — stake transfers require
approval of stake holders representing >50% of remaining stake
(remaining = 100 − transfer_pct). Strictly greater than half.

Proposals live in Station.ownership.co_ownership_stake_transfers (JSONB,
invite-pattern — no migration). Governance /vote is untouched.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, UTC
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from src.models.player import Player
from src.models.station import Station
from src.services.port_ownership_service import (
    PortOwnershipError,
    SYNDICATE_MAX_MEMBERS,
    SYNDICATE_MODE_KEY,
    _ensure_primary_share,
    _lock_station,
    _set_syndicate_state,
)

logger = logging.getLogger(__name__)

STAKE_TRANSFERS_KEY = "co_ownership_stake_transfers"
TOTAL_STAKE = 100
# Canon: >50% of remaining stake (strictly greater than half).
APPROVAL_FRAC = 0.50


def remaining_stake_pct(transfer_pct: int) -> int:
    """Stake not being moved (canon 'remaining stake')."""
    return TOTAL_STAKE - int(transfer_pct)


def approval_weight_for(
    player_id: str,
    snapshot: List[Dict[str, Any]],
    from_player_id: str,
    transfer_pct: int,
) -> int:
    """How much of the remaining-stake pool this voter represents.

    Transferor's weight is their holding after the transfer (pct − transfer_pct);
    other co-owners contribute their full snapshotted pct.
    """
    mine = next(
        (s for s in snapshot if str(s.get("player_id")) == str(player_id)),
        None,
    )
    if mine is None:
        return 0
    pct = int(mine["pct"])
    if str(player_id) == str(from_player_id):
        return max(0, pct - int(transfer_pct))
    return pct


def approving_weight_total(
    approvals: List[Dict[str, Any]],
    snapshot: List[Dict[str, Any]],
    from_player_id: str,
    transfer_pct: int,
) -> int:
    seen = set()
    total = 0
    for a in approvals:
        pid = str(a.get("player_id"))
        if pid in seen:
            continue
        seen.add(pid)
        total += approval_weight_for(pid, snapshot, from_player_id, transfer_pct)
    return total


def threshold_met(approving_weight: int, remaining: int) -> bool:
    """True when approving stake exceeds 50% of remaining stake."""
    if remaining <= 0:
        return False
    return float(approving_weight) > float(remaining) * APPROVAL_FRAC


def _require_syndicate_co_owner(
    station: Station, player: Player
) -> Tuple[str, List[Dict[str, Any]]]:
    mode = (station.ownership or {}).get(SYNDICATE_MODE_KEY) or "solo"
    if mode != "syndicate":
        raise PortOwnershipError(
            403, "Stake transfers are syndicate co-owners only"
        )
    shares = _ensure_primary_share(station)
    pid = str(player.id)
    if not any(str(s.get("player_id")) == pid for s in shares):
        raise PortOwnershipError(403, "Only syndicate co-owners may act on stake transfers")
    return pid, shares


def _transfers(station: Station) -> List[Dict[str, Any]]:
    raw = (station.ownership or {}).get(STAKE_TRANSFERS_KEY) or []
    return list(raw) if isinstance(raw, list) else []


def _set_transfers(station: Station, transfers: List[Dict[str, Any]]) -> None:
    ownership = dict(station.ownership or {})
    ownership[STAKE_TRANSFERS_KEY] = list(transfers)
    station.ownership = ownership
    # Unit tests may pass SimpleNamespace stubs without SA instrumentation.
    if hasattr(station, "_sa_instance_state"):
        flag_modified(station, "ownership")


def _snapshot_shares(shares: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [
        {"player_id": str(s["player_id"]), "pct": int(s["pct"])}
        for s in shares
    ]


def _payload(proposal: Dict[str, Any]) -> Dict[str, Any]:
    remaining = int(proposal["remaining_stake_pct"])
    weight = int(proposal.get("approving_weight") or 0)
    return {
        "proposal_id": str(proposal["proposal_id"]),
        "from_player_id": str(proposal["from_player_id"]),
        "to_player_id": str(proposal["to_player_id"]),
        "pct": int(proposal["pct"]),
        "status": proposal["status"],
        "remaining_stake_pct": remaining,
        "approving_weight": weight,
        "threshold_met": threshold_met(weight, remaining),
        "approvals": list(proposal.get("approvals") or []),
        "share_snapshot": list(proposal.get("share_snapshot") or []),
        "created_at": proposal.get("created_at"),
        "resolved_at": proposal.get("resolved_at"),
    }


def pending_stake_transfers_for(station: Station) -> List[Dict[str, Any]]:
    """Public payloads for pending proposals on this station (LEG-4238).

    Caller decides visibility (co-owners only on syndicate GET).
    """
    return [
        _payload(t)
        for t in _transfers(station)
        if t.get("status") == "pending"
    ]


def _apply_transfer(
    station: Station,
    shares: List[Dict[str, Any]],
    from_player_id: str,
    to_player_id: str,
    transfer_pct: int,
) -> List[Dict[str, Any]]:
    """Atomically rewrite co_ownership_shares for an applied transfer."""
    by_id: Dict[str, int] = {
        str(s["player_id"]): int(s["pct"]) for s in shares
    }
    if from_player_id not in by_id:
        raise PortOwnershipError(400, "Transferor no longer holds stake")
    if by_id[from_player_id] < transfer_pct:
        raise PortOwnershipError(400, "Transferor no longer holds enough stake")

    by_id[from_player_id] = by_id[from_player_id] - transfer_pct
    by_id[to_player_id] = by_id.get(to_player_id, 0) + transfer_pct

    new_shares = [
        {"player_id": pid, "pct": pct}
        for pid, pct in by_id.items()
        if pct > 0
    ]
    if sum(int(s["pct"]) for s in new_shares) != TOTAL_STAKE:
        raise PortOwnershipError(500, "Stake transfer would break 100% invariant")
    if len(new_shares) > SYNDICATE_MAX_MEMBERS:
        raise PortOwnershipError(
            400, f"Syndicate cannot exceed {SYNDICATE_MAX_MEMBERS} members"
        )

    # Primary owner_id stays unless they no longer hold stake — then promote
    # the largest remaining shareholder (stable tie-break by player_id).
    owner_sid = str(station.owner_id) if station.owner_id else None
    holder_ids = {str(s["player_id"]) for s in new_shares}
    if owner_sid and owner_sid not in holder_ids:
        top = max(
            new_shares,
            key=lambda s: (int(s["pct"]), str(s["player_id"])),
        )
        station.owner_id = uuid.UUID(str(top["player_id"]))

    new_mode = "syndicate" if len(new_shares) > 1 else "solo"
    _set_syndicate_state(station, mode=new_mode, shares=new_shares)
    return new_shares


def propose_stake_transfer(
    db: Session,
    station: Station,
    player: Player,
    to_player_id,
    pct: int,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Co-owner proposes transferring ``pct`` of their stake to ``to_player_id``.

    Caller owns commit. Proposer auto-approves; applies immediately when their
    remaining weight alone clears the >50% remaining-stake gate.
    """
    now = now or datetime.now(UTC)
    station = _lock_station(db, station.id)
    pid, shares = _require_syndicate_co_owner(station, player)

    try:
        pct_i = int(pct)
    except (TypeError, ValueError):
        raise PortOwnershipError(400, "pct must be an integer")
    if pct_i < 1 or pct_i > 99:
        raise PortOwnershipError(400, "pct must be between 1 and 99")

    try:
        to_uuid = to_player_id if isinstance(to_player_id, uuid.UUID) else uuid.UUID(str(to_player_id))
    except (ValueError, AttributeError, TypeError):
        raise PortOwnershipError(400, "to_player_id must be a UUID")
    to_sid = str(to_uuid)
    if to_sid == pid:
        raise PortOwnershipError(400, "Cannot transfer stake to yourself")

    target = db.query(Player).filter(Player.id == to_uuid).first()
    if target is None:
        raise PortOwnershipError(404, "Target player not found")

    from_share = next(s for s in shares if str(s["player_id"]) == pid)
    from_pct = int(from_share["pct"])
    if pct_i > from_pct:
        raise PortOwnershipError(400, "Cannot transfer more stake than you hold")

    member_ids = {str(s["player_id"]) for s in shares}
    if to_sid not in member_ids and len(member_ids) + 1 > SYNDICATE_MAX_MEMBERS:
        raise PortOwnershipError(
            400, f"Syndicate cannot exceed {SYNDICATE_MAX_MEMBERS} members"
        )

    pending = [
        t for t in _transfers(station)
        if t.get("status") == "pending" and str(t.get("from_player_id")) == pid
    ]
    if pending:
        raise PortOwnershipError(
            400, "You already have a pending stake-transfer proposal"
        )

    snapshot = _snapshot_shares(shares)
    remaining = remaining_stake_pct(pct_i)
    proposal_id = str(uuid.uuid4())
    approval = {"player_id": pid, "at": now.isoformat()}
    weight = approving_weight_total(
        [approval], snapshot, pid, pct_i
    )
    proposal: Dict[str, Any] = {
        "proposal_id": proposal_id,
        "from_player_id": pid,
        "to_player_id": to_sid,
        "pct": pct_i,
        "status": "pending",
        "share_snapshot": snapshot,
        "approvals": [approval],
        "approving_weight": weight,
        "remaining_stake_pct": remaining,
        "created_at": now.isoformat(),
        "resolved_at": None,
    }

    transfers = _transfers(station)
    if threshold_met(weight, remaining):
        new_shares = _apply_transfer(station, shares, pid, to_sid, pct_i)
        proposal["status"] = "applied"
        proposal["resolved_at"] = now.isoformat()
        proposal["shares"] = new_shares
        transfers.append(proposal)
        _set_transfers(station, transfers)
        logger.info(
            "Stake transfer applied immediately station=%s proposal=%s %s%% %s→%s",
            station.id, proposal_id, pct_i, pid, to_sid,
        )
        return {"proposal": _payload(proposal), "shares": new_shares}

    transfers.append(proposal)
    _set_transfers(station, transfers)
    logger.info(
        "Stake transfer proposed station=%s proposal=%s %s%% %s→%s (pending)",
        station.id, proposal_id, pct_i, pid, to_sid,
    )
    return {"proposal": _payload(proposal)}


def approve_stake_transfer(
    db: Session,
    station: Station,
    player: Player,
    proposal_id: str,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Co-owner approves a pending proposal; applies when threshold clears."""
    now = now or datetime.now(UTC)
    station = _lock_station(db, station.id)
    pid, shares = _require_syndicate_co_owner(station, player)

    transfers = _transfers(station)
    proposal = next(
        (t for t in transfers if str(t.get("proposal_id")) == str(proposal_id)),
        None,
    )
    if proposal is None:
        raise PortOwnershipError(404, "Stake-transfer proposal not found")
    if proposal.get("status") != "pending":
        raise PortOwnershipError(400, "Proposal is not pending")

    snapshot = list(proposal.get("share_snapshot") or [])
    if not any(str(s.get("player_id")) == pid for s in snapshot):
        raise PortOwnershipError(403, "Only co-owners at propose-time may approve")

    approvals = list(proposal.get("approvals") or [])
    if any(str(a.get("player_id")) == pid for a in approvals):
        raise PortOwnershipError(400, "Already approved this proposal")

    from_pid = str(proposal["from_player_id"])
    pct_i = int(proposal["pct"])
    to_sid = str(proposal["to_player_id"])
    remaining = int(proposal["remaining_stake_pct"])

    approvals.append({"player_id": pid, "at": now.isoformat()})
    weight = approving_weight_total(approvals, snapshot, from_pid, pct_i)
    proposal["approvals"] = approvals
    proposal["approving_weight"] = weight

    if threshold_met(weight, remaining):
        # Re-read live shares under lock; refuse if transferor can no longer fund.
        live = _ensure_primary_share(station)
        new_shares = _apply_transfer(station, live, from_pid, to_sid, pct_i)
        proposal["status"] = "applied"
        proposal["resolved_at"] = now.isoformat()
        proposal["shares"] = new_shares
        _set_transfers(station, transfers)
        logger.info(
            "Stake transfer applied station=%s proposal=%s by approvals weight=%s",
            station.id, proposal_id, weight,
        )
        return {"proposal": _payload(proposal), "shares": new_shares}

    _set_transfers(station, transfers)
    return {"proposal": _payload(proposal)}


def reject_stake_transfer(
    db: Session,
    station: Station,
    player: Player,
    proposal_id: str,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Co-owner rejects a pending proposal (closes immediately)."""
    now = now or datetime.now(UTC)
    station = _lock_station(db, station.id)
    pid, _shares = _require_syndicate_co_owner(station, player)

    transfers = _transfers(station)
    proposal = next(
        (t for t in transfers if str(t.get("proposal_id")) == str(proposal_id)),
        None,
    )
    if proposal is None:
        raise PortOwnershipError(404, "Stake-transfer proposal not found")
    if proposal.get("status") != "pending":
        raise PortOwnershipError(400, "Proposal is not pending")

    snapshot = list(proposal.get("share_snapshot") or [])
    if not any(str(s.get("player_id")) == pid for s in snapshot):
        raise PortOwnershipError(403, "Only co-owners at propose-time may reject")

    proposal["status"] = "rejected"
    proposal["resolved_at"] = now.isoformat()
    proposal["rejected_by"] = pid
    _set_transfers(station, transfers)
    logger.info(
        "Stake transfer rejected station=%s proposal=%s by %s",
        station.id, proposal_id, pid,
    )
    return {"proposal": _payload(proposal)}
