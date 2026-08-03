"""Player-to-player trade window service (ADR-0089 v1 kernel).

Credits + commodities only. Ship-bundle transfer and progressive anti-RMT
surcharge are deferred. Flat 5% appraisal sink applies on settle.
FLUSH-ONLY — route owns commit.
"""

from __future__ import annotations

import logging
import math
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional, Tuple

from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from src.models.player import Player
from src.models.ship import Ship, effective_cargo_capacity
from src.models.player_trade import (
    PlayerTradeLog,
    PlayerTradeSession,
    PlayerTradeSessionStatus,
    PlayerTradeablePrice,
)

logger = logging.getLogger(__name__)

SESSION_TTL_SECONDS = 5 * 60
FLAT_TAX_RATE = 0.05
MIN_TAX_CR = 1

_EMPTY_OFFER = {"credits": 0, "commodities": {}, "ship_id": None}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _normalize_offer(raw: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    raw = raw if isinstance(raw, dict) else {}
    credits = int(raw.get("credits") or 0)
    if credits < 0:
        credits = 0
    commodities: Dict[str, int] = {}
    for key, qty in (raw.get("commodities") or {}).items():
        q = int(qty or 0)
        if q > 0 and isinstance(key, str):
            commodities[key] = q
    ship_id = raw.get("ship_id")
    if ship_id is not None:
        ship_id = str(ship_id)
    return {"credits": credits, "commodities": commodities, "ship_id": ship_id}


class PlayerTradeService:
    """Bilateral trade session lifecycle (initiate → accept → stage → confirm → settle)."""

    def __init__(self, db: Session):
        self.db = db

    # ------------------------------------------------------------------
    # Locks
    # ------------------------------------------------------------------
    def _lock_players(
        self, a_id: uuid.UUID, b_id: uuid.UUID
    ) -> Tuple[Optional[Player], Optional[Player]]:
        first, second = sorted([a_id, b_id], key=lambda i: str(i))
        p1 = (
            self.db.query(Player)
            .filter(Player.id == first)
            .populate_existing()
            .with_for_update()
            .first()
        )
        p2 = (
            self.db.query(Player)
            .filter(Player.id == second)
            .populate_existing()
            .with_for_update()
            .first()
        )
        by_id = {p.id: p for p in (p1, p2) if p is not None}
        return by_id.get(a_id), by_id.get(b_id)

    def _lock_ships(self, *ship_ids: Optional[uuid.UUID]) -> Dict[uuid.UUID, Ship]:
        ids = sorted({sid for sid in ship_ids if sid is not None}, key=lambda i: str(i))
        if not ids:
            return {}
        rows = (
            self.db.query(Ship)
            .filter(Ship.id.in_(ids))
            .order_by(Ship.id)
            .populate_existing()
            .with_for_update()
            .all()
        )
        return {s.id: s for s in rows}

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    def initiate(
        self, initiator_id: uuid.UUID, target_id: uuid.UUID
    ) -> Dict[str, Any]:
        if initiator_id == target_id:
            return {"success": False, "reason": "cannot_trade_self"}

        initiator, target = self._lock_players(initiator_id, target_id)
        if not initiator or not target:
            return {"success": False, "reason": "player_not_found"}
        if initiator.open_trade_session_id or target.open_trade_session_id:
            return {"success": False, "reason": "already_in_trade"}
        if initiator.current_sector_id != target.current_sector_id:
            return {"success": False, "reason": "not_co_located"}

        session = PlayerTradeSession(
            id=uuid.uuid4(),
            initiator_id=initiator_id,
            target_id=target_id,
            status=PlayerTradeSessionStatus.PENDING_ACCEPT,
            version=1,
            sector_id=initiator.current_sector_id,
            port_id=None,
            initiator_offer=dict(_EMPTY_OFFER),
            target_offer=dict(_EMPTY_OFFER),
            expires_at=_now() + timedelta(seconds=SESSION_TTL_SECONDS),
        )
        self.db.add(session)
        self.db.flush()
        initiator.open_trade_session_id = session.id
        target.open_trade_session_id = session.id
        self.db.flush()
        return {"success": True, "session": self._public(session)}

    def accept(self, session_id: uuid.UUID, player_id: uuid.UUID) -> Dict[str, Any]:
        session = self._lock_session(session_id)
        if session is None:
            return {"success": False, "reason": "session_not_found"}
        if session.target_id != player_id:
            return {"success": False, "reason": "not_target"}
        if session.status != PlayerTradeSessionStatus.PENDING_ACCEPT:
            return {"success": False, "reason": "not_pending_accept"}
        if self._expired(session):
            return self._expire(session)

        initiator, target = self._lock_players(session.initiator_id, session.target_id)
        if not initiator or not target:
            return {"success": False, "reason": "player_not_found"}
        if initiator.current_sector_id != target.current_sector_id:
            return self._cancel(session, "left_sector")

        session.status = PlayerTradeSessionStatus.OPEN
        session.expires_at = _now() + timedelta(seconds=SESSION_TTL_SECONDS)
        self.db.flush()
        return {"success": True, "session": self._public(session)}

    def stage_offer(
        self,
        session_id: uuid.UUID,
        player_id: uuid.UUID,
        offer: Dict[str, Any],
    ) -> Dict[str, Any]:
        session = self._lock_session(session_id)
        if session is None:
            return {"success": False, "reason": "session_not_found"}
        if session.status != PlayerTradeSessionStatus.OPEN:
            return {"success": False, "reason": "not_open"}
        if self._expired(session):
            return self._expire(session)
        if player_id not in (session.initiator_id, session.target_id):
            return {"success": False, "reason": "not_party"}

        normalized = _normalize_offer(offer)
        # Commodities require a named ship (canonical cargo owner).
        if normalized["commodities"] and not normalized["ship_id"]:
            return {"success": False, "reason": "ship_id_required_for_commodities"}

        if player_id == session.initiator_id:
            session.initiator_offer = normalized
        else:
            session.target_offer = normalized
        flag_modified(session, "initiator_offer")
        flag_modified(session, "target_offer")
        session.version = int(session.version or 1) + 1
        session.initiator_confirmed_version = None
        session.target_confirmed_version = None
        session.expires_at = _now() + timedelta(seconds=SESSION_TTL_SECONDS)
        self.db.flush()
        return {"success": True, "session": self._public(session)}

    def confirm(self, session_id: uuid.UUID, player_id: uuid.UUID) -> Dict[str, Any]:
        session = self._lock_session(session_id)
        if session is None:
            return {"success": False, "reason": "session_not_found"}
        if session.status != PlayerTradeSessionStatus.OPEN:
            return {"success": False, "reason": "not_open"}
        if self._expired(session):
            return self._expire(session)
        if player_id not in (session.initiator_id, session.target_id):
            return {"success": False, "reason": "not_party"}

        ver = int(session.version or 1)
        if player_id == session.initiator_id:
            session.initiator_confirmed_version = ver
        else:
            session.target_confirmed_version = ver
        self.db.flush()

        if (
            session.initiator_confirmed_version == ver
            and session.target_confirmed_version == ver
        ):
            return self.settle(session_id)

        return {"success": True, "session": self._public(session), "settled": False}

    def cancel(
        self, session_id: uuid.UUID, player_id: uuid.UUID, reason: str = "cancelled"
    ) -> Dict[str, Any]:
        session = self._lock_session(session_id)
        if session is None:
            return {"success": False, "reason": "session_not_found"}
        if player_id not in (session.initiator_id, session.target_id):
            return {"success": False, "reason": "not_party"}
        if session.status in (
            PlayerTradeSessionStatus.SETTLED,
            PlayerTradeSessionStatus.CANCELLED,
            PlayerTradeSessionStatus.EXPIRED,
            PlayerTradeSessionStatus.DECLINED,
        ):
            return {"success": False, "reason": "already_terminal"}
        return self._cancel(session, reason)

    def decline(self, session_id: uuid.UUID, player_id: uuid.UUID) -> Dict[str, Any]:
        session = self._lock_session(session_id)
        if session is None:
            return {"success": False, "reason": "session_not_found"}
        if session.target_id != player_id:
            return {"success": False, "reason": "not_target"}
        if session.status != PlayerTradeSessionStatus.PENDING_ACCEPT:
            return {"success": False, "reason": "not_pending_accept"}
        return self._cancel(session, "declined", status=PlayerTradeSessionStatus.DECLINED)

    def get(self, session_id: uuid.UUID, player_id: uuid.UUID) -> Dict[str, Any]:
        session = (
            self.db.query(PlayerTradeSession)
            .filter(PlayerTradeSession.id == session_id)
            .first()
        )
        if session is None:
            return {"success": False, "reason": "session_not_found"}
        if player_id not in (session.initiator_id, session.target_id):
            return {"success": False, "reason": "not_party"}
        return {"success": True, "session": self._public(session)}

    # ------------------------------------------------------------------
    # Settle
    # ------------------------------------------------------------------
    def settle(self, session_id: uuid.UUID) -> Dict[str, Any]:
        session = self._lock_session(session_id)
        if session is None:
            return {"success": False, "reason": "session_not_found"}
        if session.status != PlayerTradeSessionStatus.OPEN:
            return {"success": False, "reason": "not_open"}
        ver = int(session.version or 1)
        if (
            session.initiator_confirmed_version != ver
            or session.target_confirmed_version != ver
        ):
            return {"success": False, "reason": "not_fully_confirmed"}

        initiator, target = self._lock_players(session.initiator_id, session.target_id)
        if not initiator or not target:
            return {"success": False, "reason": "player_not_found"}
        if initiator.current_sector_id != target.current_sector_id:
            return self._cancel(session, "left_sector")

        init_offer = _normalize_offer(session.initiator_offer)
        tgt_offer = _normalize_offer(session.target_offer)

        init_ship_id = (
            uuid.UUID(init_offer["ship_id"]) if init_offer["ship_id"] else None
        )
        tgt_ship_id = (
            uuid.UUID(tgt_offer["ship_id"]) if tgt_offer["ship_id"] else None
        )
        # Receiving hull defaults to current_ship when party sends no commodities
        # but receives some — name the receiver's active ship.
        recv_init_ship = init_ship_id or initiator.current_ship_id
        recv_tgt_ship = tgt_ship_id or target.current_ship_id
        ships = self._lock_ships(
            init_ship_id, tgt_ship_id, recv_init_ship, recv_tgt_ship
        )

        err = self._validate_party_offer(initiator, init_offer, ships)
        if err:
            return {"success": False, "reason": err}
        err = self._validate_party_offer(target, tgt_offer, ships)
        if err:
            return {"success": False, "reason": err}

        # Capacity: each receiver must fit incoming commodities net of outgoing.
        err = self._validate_capacity(
            ships.get(recv_tgt_ship) if recv_tgt_ship else None,
            outgoing=tgt_offer["commodities"],
            incoming=init_offer["commodities"],
        )
        if err:
            return {"success": False, "reason": err}
        err = self._validate_capacity(
            ships.get(recv_init_ship) if recv_init_ship else None,
            outgoing=init_offer["commodities"],
            incoming=tgt_offer["commodities"],
        )
        if err:
            return {"success": False, "reason": err}

        gross = self._appraise(init_offer) + self._appraise(tgt_offer)
        tax = max(MIN_TAX_CR, int(math.ceil(gross * FLAT_TAX_RATE))) if gross > 0 else 0
        # Split tax across parties proportional to what they send (min 0).
        init_gross = self._appraise(init_offer)
        tgt_gross = self._appraise(tgt_offer)
        init_tax = (
            int(math.ceil(tax * (init_gross / gross))) if gross > 0 and init_gross else 0
        )
        tgt_tax = tax - init_tax
        if (initiator.credits or 0) < init_offer["credits"] + init_tax:
            return {"success": False, "reason": "initiator_insufficient_credits"}
        if (target.credits or 0) < tgt_offer["credits"] + tgt_tax:
            return {"success": False, "reason": "target_insufficient_credits"}

        # Apply transfers.
        initiator.credits = (initiator.credits or 0) - init_offer["credits"] - init_tax
        target.credits = (target.credits or 0) + init_offer["credits"]
        target.credits = (target.credits or 0) - tgt_offer["credits"] - tgt_tax
        initiator.credits = (initiator.credits or 0) + tgt_offer["credits"]

        if init_offer["commodities"]:
            self._move_commodities(
                ships[init_ship_id],
                ships[recv_tgt_ship],
                init_offer["commodities"],
            )
        if tgt_offer["commodities"]:
            self._move_commodities(
                ships[tgt_ship_id],
                ships[recv_init_ship],
                tgt_offer["commodities"],
            )

        session.status = PlayerTradeSessionStatus.SETTLED
        session.settled_at = _now()
        initiator.open_trade_session_id = None
        target.open_trade_session_id = None

        log = PlayerTradeLog(
            id=uuid.uuid4(),
            session_id=session.id,
            initiator_id=session.initiator_id,
            target_id=session.target_id,
            sector_id=session.sector_id,
            manifest={
                "initiator_offer": init_offer,
                "target_offer": tgt_offer,
            },
            appraised_value=gross,
            tax_paid=tax,
        )
        self.db.add(log)
        self.db.flush()
        logger.info(
            "Trade session %s settled gross=%d tax=%d",
            session.id,
            gross,
            tax,
        )
        return {
            "success": True,
            "settled": True,
            "session": self._public(session),
            "appraised_value": gross,
            "tax_paid": tax,
        }

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _lock_session(self, session_id: uuid.UUID) -> Optional[PlayerTradeSession]:
        return (
            self.db.query(PlayerTradeSession)
            .filter(PlayerTradeSession.id == session_id)
            .populate_existing()
            .with_for_update()
            .first()
        )

    def _expired(self, session: PlayerTradeSession) -> bool:
        exp = session.expires_at
        if exp is None:
            return False
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=timezone.utc)
        return _now() > exp

    def _expire(self, session: PlayerTradeSession) -> Dict[str, Any]:
        return self._cancel(
            session, "expired", status=PlayerTradeSessionStatus.EXPIRED
        )

    def _cancel(
        self,
        session: PlayerTradeSession,
        reason: str,
        status: PlayerTradeSessionStatus = PlayerTradeSessionStatus.CANCELLED,
    ) -> Dict[str, Any]:
        initiator, target = self._lock_players(session.initiator_id, session.target_id)
        session.status = status
        session.terminal_reason = reason
        if initiator and initiator.open_trade_session_id == session.id:
            initiator.open_trade_session_id = None
        if target and target.open_trade_session_id == session.id:
            target.open_trade_session_id = None
        self.db.flush()
        return {
            "success": True,
            "session": self._public(session),
            "cancelled": True,
            "reason": reason,
        }

    def _validate_party_offer(
        self,
        player: Player,
        offer: Dict[str, Any],
        ships: Dict[uuid.UUID, Ship],
    ) -> Optional[str]:
        if offer["credits"] > (player.credits or 0):
            return "insufficient_credits"
        if not offer["commodities"]:
            return None
        if not offer["ship_id"]:
            return "ship_id_required_for_commodities"
        sid = uuid.UUID(offer["ship_id"])
        ship = ships.get(sid)
        if ship is None:
            return "ship_not_found"
        if ship.owner_id != player.id and ship.registered_owner_id != player.id:
            return "ship_not_owned"
        cargo = ship.cargo if isinstance(ship.cargo, dict) else {}
        contents = cargo.get("contents") if isinstance(cargo.get("contents"), dict) else {}
        for slug, qty in offer["commodities"].items():
            if int(contents.get(slug, 0) or 0) < qty:
                return f"insufficient_commodity:{slug}"
        return None

    def _validate_capacity(
        self,
        recv_ship: Optional[Ship],
        *,
        outgoing: Dict[str, int],
        incoming: Dict[str, int],
    ) -> Optional[str]:
        if not incoming:
            return None
        if recv_ship is None:
            return "receiver_ship_missing"
        cargo = recv_ship.cargo if isinstance(recv_ship.cargo, dict) else {}
        used = int(cargo.get("used", 0) or 0)
        capacity = effective_cargo_capacity(recv_ship)
        out_units = sum(outgoing.values())
        in_units = sum(incoming.values())
        free = capacity - used + out_units
        if in_units > free:
            return "cargo_full"
        return None

    def _unit_price(self, asset_key: str) -> int:
        row = (
            self.db.query(PlayerTradeablePrice)
            .filter(PlayerTradeablePrice.asset_key == asset_key)
            .first()
        )
        if row is not None:
            return int(row.unit_value_cr)
        # Hard fallbacks matching seed / ADR-0082 bands.
        defaults = {
            "credits": 1,
            "ore": 15,
            "fuel_ore": 15,
            "organics": 18,
            "equipment": 35,
            "precious_metals": 120,
        }
        return int(defaults.get(asset_key, 10))

    def _appraise(self, offer: Dict[str, Any]) -> int:
        total = int(offer.get("credits") or 0)
        for slug, qty in (offer.get("commodities") or {}).items():
            total += self._unit_price(slug) * int(qty)
        return total

    def _move_commodities(
        self, src: Ship, dst: Ship, amounts: Dict[str, int]
    ) -> None:
        src_cargo = src.cargo if isinstance(src.cargo, dict) else {}
        dst_cargo = dst.cargo if isinstance(dst.cargo, dict) else {}
        src_contents = (
            src_cargo.get("contents")
            if isinstance(src_cargo.get("contents"), dict)
            else {}
        )
        dst_contents = (
            dst_cargo.get("contents")
            if isinstance(dst_cargo.get("contents"), dict)
            else {}
        )
        moved = 0
        for slug, qty in amounts.items():
            src_contents[slug] = int(src_contents.get(slug, 0) or 0) - qty
            if src_contents[slug] <= 0:
                src_contents.pop(slug, None)
            dst_contents[slug] = int(dst_contents.get(slug, 0) or 0) + qty
            moved += qty
        src_cargo["contents"] = src_contents
        dst_cargo["contents"] = dst_contents
        src_cargo["used"] = max(0, int(src_cargo.get("used", 0) or 0) - moved)
        dst_cargo["used"] = int(dst_cargo.get("used", 0) or 0) + moved
        if "capacity" not in dst_cargo:
            dst_cargo["capacity"] = effective_cargo_capacity(dst)
        src.cargo = src_cargo
        dst.cargo = dst_cargo
        flag_modified(src, "cargo")
        flag_modified(dst, "cargo")

    def _public(self, session: PlayerTradeSession) -> Dict[str, Any]:
        return {
            "id": str(session.id),
            "initiator_id": str(session.initiator_id),
            "target_id": str(session.target_id),
            "status": session.status.value
            if hasattr(session.status, "value")
            else str(session.status),
            "version": session.version,
            "initiator_confirmed_version": session.initiator_confirmed_version,
            "target_confirmed_version": session.target_confirmed_version,
            "sector_id": session.sector_id,
            "initiator_offer": session.initiator_offer or dict(_EMPTY_OFFER),
            "target_offer": session.target_offer or dict(_EMPTY_OFFER),
            "expires_at": session.expires_at.isoformat()
            if session.expires_at
            else None,
            "settled_at": session.settled_at.isoformat()
            if session.settled_at
            else None,
            "terminal_reason": session.terminal_reason,
        }
