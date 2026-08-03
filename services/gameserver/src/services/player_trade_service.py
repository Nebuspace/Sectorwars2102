"""Player-to-player trade window service (ADR-0089).

Credits, commodities, ship-bundle. Progressive anti-RMT surcharge + value
caps from ADR-0089 § Ratified parameters (pinned). FLUSH-ONLY — route owns commit.
"""

from __future__ import annotations

import logging
import math
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from src.models.player import Player
from src.models.ship import Ship, ShipType, effective_cargo_capacity
from src.models.player_trade import (
    PlayerTradeLog,
    PlayerTradeSession,
    PlayerTradeSessionStatus,
    PlayerTradeablePrice,
)
from src.models.ship_registry import RegistryEventType
from src.services.ship_registry_service import append_registry_event
from src.services import player_trade_antirmt as antirmt

logger = logging.getLogger(__name__)

SESSION_TTL_SECONDS = 5 * 60
FLAT_TAX_RATE = antirmt.FLAT_TAX_RATE
MIN_TAX_CR = antirmt.MIN_TAX_CR

_EMPTY_OFFER = {"credits": 0, "commodities": {}, "ship_id": None, "ships": []}


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
    ships: List[str] = []
    seen = set()
    for sid in raw.get("ships") or []:
        if sid is None:
            continue
        s = str(sid)
        if s not in seen:
            seen.add(s)
            ships.append(s)
    return {
        "credits": credits,
        "commodities": commodities,
        "ship_id": ship_id,
        "ships": ships,
    }


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

        throttle_err = self._check_initiate_throttle(initiator_id)
        if throttle_err:
            return {"success": False, "reason": throttle_err}

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

        init_ship_ids = self._parse_ship_ids(init_offer.get("ships") or [])
        tgt_ship_ids = self._parse_ship_ids(tgt_offer.get("ships") or [])
        ship_trade = bool(init_ship_ids or tgt_ship_ids)
        if ship_trade:
            err = self._validate_dock_gate(initiator, target)
            if err:
                return self._cancel(session, err)
            session.port_id = initiator.current_port_id

        init_cargo_id = (
            uuid.UUID(init_offer["ship_id"]) if init_offer["ship_id"] else None
        )
        tgt_cargo_id = (
            uuid.UUID(tgt_offer["ship_id"]) if tgt_offer["ship_id"] else None
        )
        # Receiving hull defaults to current_ship when party sends no commodities
        # but receives some — name the receiver's active ship.
        recv_init_ship = init_cargo_id or initiator.current_ship_id
        recv_tgt_ship = tgt_cargo_id or target.current_ship_id
        ships = self._lock_ships(
            init_cargo_id,
            tgt_cargo_id,
            recv_init_ship,
            recv_tgt_ship,
            *init_ship_ids,
            *tgt_ship_ids,
        )

        err = self._validate_party_offer(initiator, init_offer, ships)
        if err:
            return {"success": False, "reason": err}
        err = self._validate_party_offer(target, tgt_offer, ships)
        if err:
            return {"success": False, "reason": err}
        err = self._validate_ship_bundle(initiator, init_ship_ids, ships)
        if err:
            return {"success": False, "reason": err}
        err = self._validate_ship_bundle(target, tgt_ship_ids, ships)
        if err:
            return {"success": False, "reason": err}

        # Capacity: each receiver must fit incoming commodities net of outgoing.
        # Skip capacity against a hull that is itself leaving in this settle —
        # the receiving hull must remain owned by the receiver after transfers.
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

        init_gross = self._appraise(init_offer, ships)
        tgt_gross = self._appraise(tgt_offer, ships)
        gross = init_gross + tgt_gross
        flat_tax = (
            max(MIN_TAX_CR, int(math.ceil(gross * FLAT_TAX_RATE))) if gross > 0 else 0
        )
        init_flat = (
            int(math.ceil(flat_tax * (init_gross / gross)))
            if gross > 0 and init_gross
            else 0
        )
        tgt_flat = flat_tax - init_flat

        # --- ADR-0089 anti-RMT: progressive surcharge + value-window caps ---
        init_nlr = antirmt.is_new_or_low_rep(
            created_at=initiator.created_at,
            personal_reputation=int(initiator.personal_reputation or 0),
        )
        tgt_nlr = antirmt.is_new_or_low_rep(
            created_at=target.created_at,
            personal_reputation=int(target.personal_reputation or 0),
        )
        init_prior = self._trade_window_totals(initiator.id, days=antirmt.WINDOW_DAYS)
        tgt_prior = self._trade_window_totals(target.id, days=antirmt.WINDOW_DAYS)
        init_cp = self._counterparty_flow(
            initiator.id, target.id, days=antirmt.COUNTERPARTY_WINDOW_DAYS
        )
        tgt_cp = self._counterparty_flow(
            target.id, initiator.id, days=antirmt.COUNTERPARTY_WINDOW_DAYS
        )
        cp_this = init_gross + tgt_gross

        err = antirmt.check_caps(
            prior_7d=init_prior,
            send_this=init_gross,
            receive_this=tgt_gross,
            is_new_low_rep=init_nlr,
            prior_cp_flow_30d=init_cp,
            cp_flow_this=cp_this,
        )
        if err:
            return {"success": False, "reason": f"initiator_{err}"}
        err = antirmt.check_caps(
            prior_7d=tgt_prior,
            send_this=tgt_gross,
            receive_this=init_gross,
            is_new_low_rep=tgt_nlr,
            prior_cp_flow_30d=tgt_cp,
            cp_flow_this=cp_this,
        )
        if err:
            return {"success": False, "reason": f"target_{err}"}

        cluster_err = self._check_cluster_outflow(
            initiator.id, add_external=init_gross
        )
        if cluster_err:
            return {"success": False, "reason": cluster_err}
        cluster_err = self._check_cluster_outflow(
            target.id, add_external=tgt_gross
        )
        if cluster_err:
            return {"success": False, "reason": cluster_err}

        # Net-sent increase this trade = send - receive for that party.
        init_net_add = init_gross - tgt_gross
        tgt_net_add = tgt_gross - init_gross
        init_surcharge = antirmt.progressive_surcharge_on_increase(
            init_prior.net_sent, init_net_add
        )
        tgt_surcharge = antirmt.progressive_surcharge_on_increase(
            tgt_prior.net_sent, tgt_net_add
        )
        # New/low-rep receive surcharge is paid by the party sending into them.
        if tgt_nlr:
            init_surcharge += antirmt.new_low_rep_receive_surcharge(
                init_gross, is_new_low_rep=True
            )
        if init_nlr:
            tgt_surcharge += antirmt.new_low_rep_receive_surcharge(
                tgt_gross, is_new_low_rep=True
            )

        init_tax = init_flat + init_surcharge
        tgt_tax = tgt_flat + tgt_surcharge
        tax = init_tax + tgt_tax
        surcharge_total = init_surcharge + tgt_surcharge

        if (initiator.credits or 0) < init_offer["credits"] + init_tax:
            return {"success": False, "reason": "initiator_insufficient_credits"}
        if (target.credits or 0) < tgt_offer["credits"] + tgt_tax:
            return {"success": False, "reason": "target_insufficient_credits"}

        # Apply transfers — ownership first (ADR), then credits/commodities.
        for sid in init_ship_ids:
            self._transfer_ship(
                ships[sid],
                previous_owner=initiator,
                new_owner=target,
                port_id=session.port_id,
            )
        for sid in tgt_ship_ids:
            self._transfer_ship(
                ships[sid],
                previous_owner=target,
                new_owner=initiator,
                port_id=session.port_id,
            )

        initiator.credits = (initiator.credits or 0) - init_offer["credits"] - init_tax
        target.credits = (target.credits or 0) + init_offer["credits"]
        target.credits = (target.credits or 0) - tgt_offer["credits"] - tgt_tax
        initiator.credits = (initiator.credits or 0) + tgt_offer["credits"]

        if init_offer["commodities"]:
            self._move_commodities(
                ships[init_cargo_id],
                ships[recv_tgt_ship],
                init_offer["commodities"],
            )
        if tgt_offer["commodities"]:
            self._move_commodities(
                ships[tgt_cargo_id],
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
                "flat_tax": flat_tax,
                "surcharge": surcharge_total,
                "initiator_surcharge": init_surcharge,
                "target_surcharge": tgt_surcharge,
            },
            appraised_value=gross,
            tax_paid=tax,
            initiator_appraised=init_gross,
            target_appraised=tgt_gross,
            surcharge_paid=surcharge_total,
        )
        self.db.add(log)
        self.db.flush()
        logger.info(
            "Trade session %s settled gross=%d tax=%d surcharge=%d ships=%d",
            session.id,
            gross,
            tax,
            surcharge_total,
            len(init_ship_ids) + len(tgt_ship_ids),
        )
        return {
            "success": True,
            "settled": True,
            "session": self._public(session),
            "appraised_value": gross,
            "tax_paid": tax,
            "surcharge_paid": surcharge_total,
        }

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _check_initiate_throttle(self, initiator_id: uuid.UUID) -> Optional[str]:
        """ADR-0089: 20 free initiates/UTC-day, then 30s; 60s after cancel/expire."""
        now = _now()
        day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        initiates_today = (
            self.db.query(PlayerTradeSession)
            .filter(
                PlayerTradeSession.initiator_id == initiator_id,
                PlayerTradeSession.created_at >= day_start,
            )
            .count()
        )
        last_terminal = (
            self.db.query(PlayerTradeSession)
            .filter(
                PlayerTradeSession.initiator_id == initiator_id,
                PlayerTradeSession.status.in_(
                    (
                        PlayerTradeSessionStatus.CANCELLED,
                        PlayerTradeSessionStatus.EXPIRED,
                    )
                ),
            )
            .order_by(PlayerTradeSession.updated_at.desc())
            .first()
        )
        if last_terminal is not None and last_terminal.updated_at is not None:
            term_at = last_terminal.updated_at
            if term_at.tzinfo is None:
                term_at = term_at.replace(tzinfo=timezone.utc)
            cool = timedelta(seconds=antirmt.POST_CANCEL_EXPIRE_COOLDOWN_SECONDS)
            if now < term_at + cool:
                return "initiate_cooldown_after_cancel"

        if initiates_today >= antirmt.FREE_INITIATES_PER_DAY:
            last_init = (
                self.db.query(PlayerTradeSession)
                .filter(PlayerTradeSession.initiator_id == initiator_id)
                .order_by(PlayerTradeSession.created_at.desc())
                .first()
            )
            if last_init is not None and last_init.created_at is not None:
                created = last_init.created_at
                if created.tzinfo is None:
                    created = created.replace(tzinfo=timezone.utc)
                cool = timedelta(seconds=antirmt.POST_FREE_INITIATE_COOLDOWN_SECONDS)
                if now < created + cool:
                    return "initiate_cooldown"

        return None

    def _trade_window_totals(
        self, player_id: uuid.UUID, *, days: int
    ) -> antirmt.WindowTotals:
        since = _now() - timedelta(days=days)
        rows = (
            self.db.query(PlayerTradeLog)
            .filter(
                PlayerTradeLog.created_at >= since,
                (
                    (PlayerTradeLog.initiator_id == player_id)
                    | (PlayerTradeLog.target_id == player_id)
                ),
            )
            .all()
        )
        return antirmt.accumulate_window(player_id, rows)

    def _counterparty_flow(
        self, player_id: uuid.UUID, counterparty_id: uuid.UUID, *, days: int
    ) -> int:
        since = _now() - timedelta(days=days)
        rows = (
            self.db.query(PlayerTradeLog)
            .filter(
                PlayerTradeLog.created_at >= since,
                (
                    (
                        (PlayerTradeLog.initiator_id == player_id)
                        & (PlayerTradeLog.target_id == counterparty_id)
                    )
                    | (
                        (PlayerTradeLog.initiator_id == counterparty_id)
                        & (PlayerTradeLog.target_id == player_id)
                    )
                ),
            )
            .all()
        )
        return antirmt.counterparty_flow(player_id, counterparty_id, rows)

    def _check_cluster_outflow(
        self, player_id: uuid.UUID, *, add_external: int
    ) -> Optional[str]:
        """Cluster combined external outflow cap (ADR-0089). Soft-skip if schema unused."""
        try:
            from src.models.multi_account import MultiAccountFlag
        except Exception:
            return None
        cluster_ids = [
            row[0]
            for row in self.db.query(MultiAccountFlag.cluster_id)
            .filter(MultiAccountFlag.player_id == player_id)
            .distinct()
            .all()
            if row[0] is not None
        ]
        if not cluster_ids:
            return None
        since = _now() - timedelta(days=antirmt.WINDOW_DAYS)
        for cid in cluster_ids:
            member_ids = [
                r[0]
                for r in self.db.query(MultiAccountFlag.player_id)
                .filter(MultiAccountFlag.cluster_id == cid)
                .distinct()
                .all()
                if r[0] is not None
            ]
            if not member_ids:
                continue
            member_set = set(member_ids)
            rows = (
                self.db.query(PlayerTradeLog)
                .filter(
                    PlayerTradeLog.created_at >= since,
                    (
                        (PlayerTradeLog.initiator_id.in_(member_ids))
                        | (PlayerTradeLog.target_id.in_(member_ids))
                    ),
                )
                .all()
            )
            outflow = 0
            for row in rows:
                # External = value sent by a member to a non-member.
                if (
                    row.initiator_id in member_set
                    and row.target_id not in member_set
                ):
                    outflow += int(row.initiator_appraised or 0)
                if (
                    row.target_id in member_set
                    and row.initiator_id not in member_set
                ):
                    outflow += int(row.target_appraised or 0)
            # This trade's add_external counts if counterparties are outside —
            # settle caller passes the party's gross send; we conservatively add it.
            if outflow + add_external > antirmt.CLUSTER_OUTFLOW_CAP_7D:
                return "cluster_cap_exceeded"
        return None

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
        staged = set(offer.get("ships") or [])
        if offer["ship_id"] and offer["ship_id"] in staged and offer["commodities"]:
            # Hull transfers as a bundle — cargo on that hull cannot also be staged.
            return "commodities_on_staged_ship"
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

    def _parse_ship_ids(self, raw: List[str]) -> List[uuid.UUID]:
        out: List[uuid.UUID] = []
        for s in raw:
            try:
                out.append(uuid.UUID(str(s)))
            except (ValueError, TypeError):
                continue
        return out

    def _validate_dock_gate(self, a: Player, b: Player) -> Optional[str]:
        if not a.is_docked or not b.is_docked:
            return "not_docked"
        if a.current_port_id is None or b.current_port_id is None:
            return "not_docked"
        if a.current_port_id != b.current_port_id:
            return "different_port"
        return None

    def _fleet_tradeable_count(self, player_id: uuid.UUID) -> int:
        """Non-destroyed non-escape-pod hulls owned by player (ADR last-ship rule)."""
        return (
            self.db.query(Ship)
            .filter(
                Ship.owner_id == player_id,
                Ship.is_destroyed.is_(False),
                Ship.type != ShipType.ESCAPE_POD,
            )
            .count()
        )

    def _validate_ship_bundle(
        self,
        player: Player,
        ship_ids: List[uuid.UUID],
        ships: Dict[uuid.UUID, Ship],
    ) -> Optional[str]:
        if not ship_ids:
            return None
        for sid in ship_ids:
            ship = ships.get(sid)
            if ship is None:
                return "ship_not_found"
            if ship.is_destroyed:
                return "ship_destroyed"
            owner_ok = ship.owner_id == player.id or ship.registered_owner_id == player.id
            if not owner_ok:
                return "ship_not_owned"
            if ship.stolen_status:
                return "ship_stolen"
            if player.current_ship_id == sid:
                return "ship_currently_piloted"
            stype = ship.type.value if hasattr(ship.type, "value") else str(ship.type)
            if stype == ShipType.ESCAPE_POD.value:
                return "escape_pod_not_tradeable"
        remaining = self._fleet_tradeable_count(player.id) - len(ship_ids)
        if remaining < 1:
            return "last_ship"
        return None

    def _transfer_ship(
        self,
        ship: Ship,
        *,
        previous_owner: Player,
        new_owner: Player,
        port_id: Optional[uuid.UUID],
    ) -> None:
        prev_id = previous_owner.id
        new_id = new_owner.id
        original = ship.registered_owner_id or ship.owner_id or prev_id
        ship.owner_id = new_id
        ship.registered_owner_id = new_id
        if ship.current_pilot_id == prev_id:
            ship.current_pilot_id = None
        ship.for_sale_price = None
        ship.for_sale_listed_by_id = None
        if ship.insurance is not None:
            ship.insurance = None
            flag_modified(ship, "insurance")
        append_registry_event(
            self.db,
            ship=ship,
            event_type=RegistryEventType.OWNERSHIP_TRANSFER,
            original_owner_id=original,
            previous_owner_id=prev_id,
            new_owner_id=new_id,
            acting_party_id=prev_id,
            port_id=port_id,
            event_metadata={"via": "player_trade"},
        )

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
        # Hard fallbacks matching seed / ADR-0082 bands. Hulls use ship:<TYPE>.
        defaults = {
            "credits": 1,
            "ore": 15,
            "fuel_ore": 15,
            "organics": 18,
            "equipment": 35,
            "precious_metals": 120,
        }
        if asset_key.startswith("ship:"):
            return 5000
        return int(defaults.get(asset_key, 10))

    def _appraise(
        self,
        offer: Dict[str, Any],
        ships: Optional[Dict[uuid.UUID, Ship]] = None,
    ) -> int:
        total = int(offer.get("credits") or 0)
        for slug, qty in (offer.get("commodities") or {}).items():
            total += self._unit_price(slug) * int(qty)
        ships = ships or {}
        for sid in offer.get("ships") or []:
            try:
                ship = ships.get(uuid.UUID(str(sid)))
            except (ValueError, TypeError):
                ship = None
            if ship is None:
                total += 5000
                continue
            stype = ship.type.value if hasattr(ship.type, "value") else str(ship.type)
            total += self._unit_price(f"ship:{stype}")
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
            "port_id": str(session.port_id) if session.port_id else None,
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
