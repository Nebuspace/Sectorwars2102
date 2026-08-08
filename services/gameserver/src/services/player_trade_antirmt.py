"""P2P trade anti-RMT helpers (ADR-0089 § Ratified parameters — PINNED).

Launch starting values ratified human 2026-06-28. Do not invent alternate numbers.
"""

from __future__ import annotations

import math
import uuid
from datetime import datetime, timedelta, timezone
from typing import Iterable, Optional, Tuple

# --- Flat sink (already live in settle) ---
FLAT_TAX_RATE = 0.05
MIN_TAX_CR = 1

# --- Progressive surcharge: marginal bands on net value sent / 7d ---
# (lo_inclusive, hi_exclusive_or_None, rate)
SURCHARGE_BANDS: Tuple[Tuple[int, Optional[int], float], ...] = (
    (0, 50_000, 0.0),
    (50_000, 250_000, 0.10),
    (250_000, 1_000_000, 0.30),
    (1_000_000, None, 0.60),
)

NEW_LOW_REP_RECEIVE_SURCHARGE = 0.25
NEW_LOW_REP_RECEIVE_SURCHARGE_FLOOR = 10_000

# --- Value-window caps ---
WINDOW_DAYS = 7
COUNTERPARTY_WINDOW_DAYS = 30
SEND_CAP_7D = 2_000_000
RECEIVE_CAP_ESTABLISHED_7D = 1_000_000
RECEIVE_CAP_NEW_LOW_REP_7D = 50_000
COUNTERPARTY_CAP_30D = 250_000
CLUSTER_OUTFLOW_CAP_7D = 500_000

NEW_ACCOUNT_AGE_DAYS = 14

# --- Initiate throttles (grief control, ADR-0089 § Ratified parameters) ---
FREE_INITIATES_PER_DAY = 20
POST_FREE_INITIATE_COOLDOWN_SECONDS = 30
POST_CANCEL_EXPIRE_COOLDOWN_SECONDS = 60


def _now() -> datetime:
    return datetime.now(timezone.utc)


def is_new_or_low_rep(
    *,
    created_at: Optional[datetime],
    personal_reputation: int,
    now: Optional[datetime] = None,
) -> bool:
    """ADR-0089: account age under 14 days OR personal-reputation below Neutral."""
    now = now or _now()
    if personal_reputation < 0:
        return True
    if created_at is None:
        return False
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    return (now - created_at) < timedelta(days=NEW_ACCOUNT_AGE_DAYS)


def progressive_surcharge_on_increase(prev_net_sent: int, add_net_sent: int) -> int:
    """Marginal surcharge on the interval [prev, prev+add] of net value sent.

    Balanced / net-receive trades (add_net_sent <= 0) pay 0 from send bands.
    """
    if add_net_sent <= 0:
        return 0
    prev = max(0, int(prev_net_sent))
    end = prev + int(add_net_sent)
    total = 0.0
    for lo, hi, rate in SURCHARGE_BANDS:
        if rate <= 0:
            continue
        band_lo = max(lo, prev)
        band_hi = end if hi is None else min(hi, end)
        if band_hi > band_lo:
            total += (band_hi - band_lo) * rate
    return int(math.ceil(total)) if total > 0 else 0


def new_low_rep_receive_surcharge(receive_this_trade: int, *, is_new_low_rep: bool) -> int:
    """+25% on net value received above 10,000 cr into a new/low-rep account."""
    if not is_new_low_rep:
        return 0
    excess = int(receive_this_trade) - NEW_LOW_REP_RECEIVE_SURCHARGE_FLOOR
    if excess <= 0:
        return 0
    return int(math.ceil(excess * NEW_LOW_REP_RECEIVE_SURCHARGE))


def receive_cap_for(*, is_new_low_rep: bool) -> int:
    return RECEIVE_CAP_NEW_LOW_REP_7D if is_new_low_rep else RECEIVE_CAP_ESTABLISHED_7D


class WindowTotals:
    __slots__ = ("sent", "received")

    def __init__(self, sent: int = 0, received: int = 0):
        self.sent = int(sent)
        self.received = int(received)

    @property
    def net_sent(self) -> int:
        return self.sent - self.received


def party_legs_from_log(
    *,
    player_id: uuid.UUID,
    initiator_id: Optional[uuid.UUID],
    target_id: Optional[uuid.UUID],
    initiator_appraised: int,
    target_appraised: int,
) -> Tuple[int, int]:
    """Return (sent, received) for player_id from one settled log row."""
    init_a = int(initiator_appraised or 0)
    tgt_a = int(target_appraised or 0)
    if player_id == initiator_id:
        return init_a, tgt_a
    if player_id == target_id:
        return tgt_a, init_a
    return 0, 0


def accumulate_window(
    player_id: uuid.UUID,
    rows: Iterable,
) -> WindowTotals:
    """rows: objects with initiator_id, target_id, initiator_appraised, target_appraised."""
    totals = WindowTotals()
    for row in rows:
        sent, recv = party_legs_from_log(
            player_id=player_id,
            initiator_id=getattr(row, "initiator_id", None),
            target_id=getattr(row, "target_id", None),
            initiator_appraised=getattr(row, "initiator_appraised", 0) or 0,
            target_appraised=getattr(row, "target_appraised", 0) or 0,
        )
        totals.sent += sent
        totals.received += recv
    return totals


def counterparty_flow(
    player_id: uuid.UUID,
    counterparty_id: uuid.UUID,
    rows: Iterable,
) -> int:
    """Absolute value exchanged with one counterparty (sent+received) over the rows."""
    total = 0
    for row in rows:
        init_id = getattr(row, "initiator_id", None)
        tgt_id = getattr(row, "target_id", None)
        pair = {init_id, tgt_id}
        if player_id not in pair or counterparty_id not in pair:
            continue
        sent, recv = party_legs_from_log(
            player_id=player_id,
            initiator_id=init_id,
            target_id=tgt_id,
            initiator_appraised=getattr(row, "initiator_appraised", 0) or 0,
            target_appraised=getattr(row, "target_appraised", 0) or 0,
        )
        total += sent + recv
    return total


def check_caps(
    *,
    prior_7d: WindowTotals,
    send_this: int,
    receive_this: int,
    is_new_low_rep: bool,
    prior_cp_flow_30d: int,
    cp_flow_this: int,
) -> Optional[str]:
    """Return a reason code if a hard cap would be exceeded, else None."""
    if prior_7d.sent + send_this > SEND_CAP_7D:
        return "send_cap_exceeded"
    rcap = receive_cap_for(is_new_low_rep=is_new_low_rep)
    if prior_7d.received + receive_this > rcap:
        return "receive_cap_exceeded"
    if prior_cp_flow_30d + cp_flow_this > COUNTERPARTY_CAP_30D:
        return "counterparty_cap_exceeded"
    return None
