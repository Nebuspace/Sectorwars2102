"""Central Nexus Bank service (ADR-0050 / DATA_MODELS/player.md).

Deposit-only from the cascade orchestrator (planet-safe transfer, station
credit-compensation fallback, offline warp-gate refund). Withdrawals at
Starport Prime docks are a follow-on surface — this module owns the account
row + deposit/ledger helpers the three GAP call sites need.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, UTC
from typing import Any, Dict, Optional

from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from src.models.player import Player
from src.models.player_central_bank import PlayerCentralBankAccount

logger = logging.getLogger(__name__)

# Ledger entry types (DATA_MODELS/player.md)
ENTRY_CASCADE_SAFE_TRANSFER = "cascade_safe_transfer"
ENTRY_CASCADE_STATION_COMPENSATION = "cascade_station_compensation"
ENTRY_WARP_GATE_CASCADE_REFUND = "warp_gate_cascade_refund"
ENTRY_WITHDRAW_CREDITS = "withdraw_credits"
ENTRY_WITHDRAW_COMMODITY = "withdraw_commodity"


class CentralBankError(Exception):
    """Recoverable Bank withdrawal/deposit failure for HTTP mapping."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int = 400,
        payload: Optional[Dict[str, Any]] = None,
    ):
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.payload = payload or {}


def get_or_create_account(
    db: Session, player_id: uuid.UUID,
) -> PlayerCentralBankAccount:
    """Return the player's Bank row, creating an empty one if absent."""
    account = (
        db.query(PlayerCentralBankAccount)
        .filter(PlayerCentralBankAccount.player_id == player_id)
        .populate_existing()
        .with_for_update()
        .first()
    )
    if account is None:
        account = PlayerCentralBankAccount(
            player_id=player_id,
            credits=0,
            commodities={},
            ledger=[],
        )
        db.add(account)
        db.flush()
    return account


def _append_ledger(
    account: PlayerCentralBankAccount,
    *,
    entry_type: str,
    amount: int,
    source: str,
    notes: Optional[str] = None,
    access_override: bool = False,
    extra: Optional[Dict[str, Any]] = None,
) -> None:
    entry: Dict[str, Any] = {
        "timestamp": datetime.now(UTC).isoformat(),
        "type": entry_type,
        "amount": int(amount),
        "source": source,
        "notes": notes,
        "access_override": bool(access_override),
    }
    if extra:
        entry.update(extra)
    ledger = list(account.ledger or [])
    ledger.append(entry)
    account.ledger = ledger
    flag_modified(account, "ledger")


def deposit_credits(
    db: Session,
    player_id: uuid.UUID,
    amount: int,
    *,
    entry_type: str,
    source: str,
    notes: Optional[str] = None,
    access_override: bool = True,
) -> PlayerCentralBankAccount:
    """Credit liquid credits into the Bank. Cascade deposits set
    access_override=True so GC-lapse cannot trap the balance (ADR-0054 X-I3).
    """
    if amount <= 0:
        return get_or_create_account(db, player_id)
    account = get_or_create_account(db, player_id)
    account.credits = int(account.credits or 0) + int(amount)
    _append_ledger(
        account,
        entry_type=entry_type,
        amount=int(amount),
        source=source,
        notes=notes,
        access_override=access_override,
    )
    db.flush()
    return account


def deposit_commodities(
    db: Session,
    player_id: uuid.UUID,
    commodities: Dict[str, int],
    *,
    entry_type: str,
    source: str,
    notes: Optional[str] = None,
    access_override: bool = True,
) -> PlayerCentralBankAccount:
    """Merge commodity stacks into the Bank account."""
    cleaned = {
        str(k): int(v) for k, v in (commodities or {}).items() if int(v) > 0
    }
    if not cleaned:
        return get_or_create_account(db, player_id)
    account = get_or_create_account(db, player_id)
    holdings = dict(account.commodities or {})
    for commodity, qty in cleaned.items():
        holdings[commodity] = int(holdings.get(commodity, 0)) + qty
    account.commodities = holdings
    flag_modified(account, "commodities")
    total_units = sum(cleaned.values())
    _append_ledger(
        account,
        entry_type=entry_type,
        amount=total_units,
        source=source,
        notes=notes,
        access_override=access_override,
        extra={"commodities": cleaned},
    )
    db.flush()
    return account


def _override_credit_balance(account: PlayerCentralBankAccount) -> int:
    """Credits withdrawable at a non-Starport-Prime dock (ADR-0054 X-I3)."""
    total = 0
    for entry in account.ledger or []:
        if not entry.get("access_override") or entry.get("consumed"):
            continue
        remaining = entry.get("remaining_amount", entry.get("amount", 0))
        try:
            remaining = int(remaining)
        except (TypeError, ValueError):
            continue
        if remaining > 0:
            total += remaining
    return total


def available_withdraw_credits(
    account: PlayerCentralBankAccount, *, at_starport_prime: bool
) -> int:
    balance = int(account.credits or 0)
    if at_starport_prime:
        return balance
    return min(balance, _override_credit_balance(account))


def _consume_override_entries(
    account: PlayerCentralBankAccount, amount: int
) -> None:
    remaining = int(amount)
    ledger = list(account.ledger or [])
    for entry in ledger:
        if remaining <= 0:
            break
        if not entry.get("access_override") or entry.get("consumed"):
            continue
        avail = entry.get("remaining_amount", entry.get("amount", 0))
        try:
            avail = int(avail)
        except (TypeError, ValueError):
            continue
        if avail <= 0:
            continue
        if avail <= remaining:
            remaining -= avail
            entry["consumed"] = True
            entry["remaining_amount"] = 0
        else:
            entry["remaining_amount"] = avail - remaining
            remaining = 0
    account.ledger = ledger
    flag_modified(account, "ledger")


def _require_docked_at(player: Player, station) -> None:
    if not player.is_docked or player.current_port_id != station.id:
        raise CentralBankError(
            "You must be docked at a station to withdraw from the Bank"
        )


def withdraw_credits(
    db: Session,
    player: Player,
    station,
    amount: int,
) -> PlayerCentralBankAccount:
    """Move liquid credits from the Bank to the player's wallet."""
    if amount <= 0:
        raise CentralBankError("Withdrawal amount must be positive")
    _require_docked_at(player, station)
    at_starport_prime = bool(getattr(station, "is_starport_prime", False))
    account = get_or_create_account(db, player.id)
    available = available_withdraw_credits(
        account, at_starport_prime=at_starport_prime
    )
    if amount > available:
        if not at_starport_prime:
            raise CentralBankError(
                "Withdrawal exceeds access-override balance; dock at "
                "Starport Prime to access full account",
                payload={"available_at_this_port": available},
            )
        raise CentralBankError(
            "INSUFFICIENT_BANK_BALANCE",
            payload={"available": available, "requested": amount},
        )
    if not at_starport_prime:
        _consume_override_entries(account, amount)
    account.credits = int(account.credits or 0) - int(amount)
    player.credits = int(player.credits or 0) + int(amount)
    _append_ledger(
        account,
        entry_type=ENTRY_WITHDRAW_CREDITS,
        amount=int(amount),
        source=f"station:{station.id}",
        notes="bank_withdraw_credits",
        access_override=False,
    )
    db.flush()
    return account


def withdraw_commodity(
    db: Session,
    player: Player,
    ship,
    station,
    commodity: str,
    quantity: int,
) -> Dict[str, Any]:
    """Move commodity stacks from the Bank into the player's ship cargo."""
    from src.models.ship import effective_cargo_capacity
    from src.services.turn_service import spend_turns

    if quantity <= 0:
        raise CentralBankError("Withdrawal quantity must be positive")
    _require_docked_at(player, station)
    if not getattr(station, "is_starport_prime", False):
        raise CentralBankError(
            "Commodity withdrawals require docking at Starport Prime"
        )

    account = get_or_create_account(db, player.id)
    holdings = dict(account.commodities or {})
    key = str(commodity)
    available = int(holdings.get(key, 0))
    if quantity > available:
        raise CentralBankError(
            "INSUFFICIENT_BANK_BALANCE",
            payload={
                "commodity": key,
                "available": available,
                "requested": quantity,
            },
        )

    cargo = dict(ship.cargo or {})
    contents: Dict[str, int] = dict(cargo.get("contents") or {})
    capacity = effective_cargo_capacity(ship)
    used = sum(int(q) for q in contents.values() if isinstance(q, (int, float)))
    free_cargo = max(0, int(capacity) - int(used))
    if quantity > free_cargo:
        raise CentralBankError(
            "Insufficient cargo space",
            payload={"free_cargo": free_cargo, "requested": quantity},
        )

    turn_cost = (int(quantity) + 99) // 100
    if int(player.turns or 0) < turn_cost:
        raise CentralBankError(
            "Not enough turns for commodity withdrawal",
            payload={"turns_required": turn_cost, "turns_available": int(player.turns or 0)},
        )

    spend_turns(player, turn_cost)
    holdings[key] = available - int(quantity)
    if holdings[key] <= 0:
        holdings.pop(key, None)
    account.commodities = holdings
    flag_modified(account, "commodities")

    contents[key] = int(contents.get(key, 0)) + int(quantity)
    cargo["contents"] = contents
    cargo["used"] = sum(int(q) for q in contents.values())
    ship.cargo = cargo
    flag_modified(ship, "cargo")

    _append_ledger(
        account,
        entry_type=ENTRY_WITHDRAW_COMMODITY,
        amount=int(quantity),
        source=f"station:{station.id}",
        notes="bank_withdraw_commodity",
        access_override=False,
        extra={"commodity": key, "turn_cost": turn_cost},
    )
    db.flush()
    return {
        "commodity": key,
        "quantity": int(quantity),
        "turn_cost": turn_cost,
        "bank_commodities_remaining": dict(account.commodities or {}),
    }


def is_player_online_sync(player_id: uuid.UUID) -> Optional[bool]:
    """Best-effort sync read of Redis ``player_online:{id}``.

    Returns True/False when Redis answers, or None when Redis is unavailable
    / unset (caller treats None as offline → Bank, per ADR-0050 offline
    routing — never invent an online state we cannot prove).
    """
    try:
        from src.services.redis_service import redis_service

        client = getattr(redis_service, "sync_redis", None)
        if client is None:
            return None
        key = f"player_online:{player_id}"
        value = client.get(key)
        if value is None:
            return False
        return True
    except Exception as exc:  # noqa: BLE001 — Redis outage must not abort cascade
        logger.warning(
            "central_bank: online-status probe failed for %s (%s) — "
            "treating as offline",
            player_id, exc,
        )
        return None


def credit_wallet_or_bank(
    db: Session,
    player: Player,
    amount: int,
    *,
    entry_type: str,
    source: str,
    notes: Optional[str] = None,
) -> str:
    """Route a credit payout: online → Player.credits, else → Bank.

    Returns ``\"wallet\"`` or ``\"bank\"`` naming where the amount landed.
    """
    if amount <= 0:
        return "wallet"
    online = is_player_online_sync(player.id)
    if online is True:
        player.credits = int(player.credits or 0) + int(amount)
        return "wallet"
    deposit_credits(
        db,
        player.id,
        amount,
        entry_type=entry_type,
        source=source,
        notes=notes,
        access_override=True,
    )
    return "bank"


def pay_station_loss_compensation(
    db: Session,
    player_id: uuid.UUID,
    amount: int,
    *,
    station_id: Optional[uuid.UUID] = None,
    notes: Optional[str] = None,
) -> PlayerCentralBankAccount:
    """Path-A station-loss credit-compensation fallback → Bank (ADR-0050).

    Called when a terminating station cannot cover its relocation fee even
    after stripping upgrades. The acquisition_cost / capital-ledger WO
    computes ``amount``; this helper is the canonical deposit target so that
    path never falls back to Player.credits.
    """
    source = f"station:{station_id}" if station_id else "station_loss"
    return deposit_credits(
        db,
        player_id,
        amount,
        entry_type=ENTRY_CASCADE_STATION_COMPENSATION,
        source=source,
        notes=notes or "cascade_station_compensation_50pct_acquisition",
        access_override=True,
    )
