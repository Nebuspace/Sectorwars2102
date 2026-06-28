"""Contraband service — server-authoritative black-market trading loop.

Implements the buildable KERNEL of ``audit/design-briefs/black-market.md`` (the
working brief) — a single-port-class contraband-trading loop with a detection
roll and reputation hooks. Greenfield (WO-BLACKMARKET): additive only — no
existing file is changed by this module.

DEFERRED (NOT built here, per the brief §1 "Out of kernel"): credit laundering,
Shadow-Syndicate fence venues, hidden-sector / abandoned-outpost discovery,
stealth-route multipliers + Stealth Systems equipment, counterfeit goods, bounty
placement. There is intentionally no syndicate fence.

The three entry points (all server-authoritative, all single-transaction):

* ``get_catalog(player, station)`` — the contraband catalog + computed prices for
  a qualifying venue. Returns ``None`` when the access gate is unmet (the route
  turns that into a 404, hiding the existence of the menu from un-vouched
  players).
* ``buy(player, ship, station, commodity, quantity)`` — haggle-priced purchase;
  charges credits, adds the goods to ``Ship.cargo`` under the ``illegal:<...>``
  key, records a flagged ``MarketTransaction``, fires faction rep deltas.
* ``sell(player, ship, station, commodity, quantity)`` — pays out, then runs the
  detection roll. On a CLEAN sale: credits + a notoriety nudge (personal_rep
  down). On a FAILED roll: confiscate the illegal cargo, levy a severity-scaled
  fine, apply the Federation rep delta, and flip ``is_suspect`` (LIGHT/MODERATE)
  or ``is_wanted`` (SEVERE).

ACCESS GATE (brief §1.1, [OPEN-1]): the venue qualifies iff
``Station.type == StationType.BLACK_MARKET`` AND the player's Fringe-Alliance
(``FactionType.OUTLAWS``, ADR-0033) reputation is at least
``ReputationLevel.RECOGNIZED``. Below the gate, the catalog/buy/sell paths refuse
with a stable ``gate_unmet`` reason.

PRICING (brief §1.3, [OPEN-3]): ``price = base_price × category_multiplier ×
personality_modifier``, where ``personality_modifier`` is a ±25% haggle roll for
``TraderPersonalityType.BLACK_MARKET`` (vs ±10% legal). Pricing IGNORES
supply/demand — contraband is never in ``Station.commodities``. RNG is
``secrets``-backed (anti-cheat parity with mining/haggle).

DETECTION (brief §1.4, [OPEN-5]): on SELL, roll
``P = base(0.05) + cargo·(illegal_value/cargo_cap)
     + sector·(1 − security_level/10) + rep·(1 − personal_reputation/1000)``
clamped to ``[0.0, 0.95]``. The dropped terms (ship_visibility, transit_history,
evasion_skill) have no inputs today and are deferred.

CONSEQUENCES (brief §1.4, [OPEN-6]/[OPEN-7]): fine = cargo value ×
{LIGHT 2, MODERATE 3, SEVERE 4}; heat = ``is_suspect`` for LIGHT/MODERATE,
``is_wanted`` for SEVERE.

SLAVES is permanently disabled (``core.illegal_commodities``): the catalog omits
it, and buy/sell reject it unconditionally — there is no code path that trades
it.

LESSONS BAKED IN (from the just-shipped mining wave):
* L1 — THE ROUTE OWNS THE COMMIT. This service FLUSHES only; each route handler
  commits on success / rolls back on failure.
* L2 — cargo capacity comes from the ``Ship.cargo`` JSONB
  ``{"used","capacity"(50),"contents"}`` convention; there is NO
  ``Ship.max_cargo`` column. Capacity is always ``cargo.get("capacity", 50)``.
* L3 — LOCK ORDER: lock the STATION row ``FOR UPDATE`` BEFORE the PLAYER row
  (then the ship row), mirroring ``trading.py``; all read-modify-write in one
  transaction.
"""

from __future__ import annotations

import logging
import secrets
import uuid
from datetime import UTC, datetime
from typing import Any, Dict, Optional, Tuple

from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from src.core.illegal_commodities import (
    ENABLED_COMMODITIES,
    IllegalCommodity,
    IllegalCommodityMeta,
    IllegalSeverity,
    cargo_key,
    get_meta,
    is_enabled,
)
from src.models.faction import Faction, FactionType
from src.models.market_transaction import MarketTransaction, TransactionType
from src.models.player import Player
from src.models.reputation import Reputation, ReputationLevel
from src.models.sector import Sector
from src.models.ship import Ship
from src.models.station import Station, StationType
from src.services.faction_service import apply_faction_rep_delta

logger = logging.getLogger(__name__)

# Cryptographically secure RNG — anti-cheat parity with mining_service /
# haggle (rolls must not be predictable so a client can't pre-compute the
# haggle multiplier or the detection outcome). Module-level SystemRandom per
# the stdlib guidance.
_RNG = secrets.SystemRandom()

# ── Access gate (brief §1.1, [OPEN-1]) ────────────────────────────────────────
# The Fringe-Alliance faction the gate keys on (ADR-0033: Fringe Alliance ==
# FactionType.OUTLAWS) and the minimum reputation tier to be vouched in.
GATE_FACTION = FactionType.OUTLAWS
GATE_MIN_LEVEL = ReputationLevel.RECOGNIZED

# ── Haggle swing (brief §1.3, [OPEN-3]) ───────────────────────────────────────
# Black-market personality_modifier is a ±25% roll (vs ±10% legal). Applied as a
# multiplier on (base_price × category_multiplier).
HAGGLE_SWING = 0.25

# ── Detection model (brief §1.4, [OPEN-5]) ────────────────────────────────────
# Coefficients for the four surviving terms. The base rate is the floor risk on
# any contraband sale; the three weighted terms scale 0..1 inputs.
DETECT_BASE_RATE = 0.05
DETECT_CARGO_WEIGHT = 1.0      # cargo_visibility · (illegal_value / cargo_cap)
DETECT_SECTOR_WEIGHT = 1.0     # sector_security · (1 − security_level / 10)
DETECT_REP_WEIGHT = 1.0        # player_reputation · (1 − personal_reputation / 1000)
DETECT_PROB_MIN = 0.0
DETECT_PROB_MAX = 0.95         # never a guaranteed bust — the smuggler always has a chance

# Reference cargo capacity divisor for the cargo-visibility term when a ship has
# no usable capacity figure (defensive; capacity always comes from the JSONB).
DEFAULT_CARGO_CAPACITY = 50

# ── Consequences (brief §1.4, [OPEN-6] / [OPEN-7]) ────────────────────────────
# Fine = cargo value × multiplier, keyed on legal severity.
FINE_MULTIPLIER_BY_SEVERITY: Dict[IllegalSeverity, int] = {
    IllegalSeverity.LIGHT: 2,
    IllegalSeverity.MODERATE: 3,
    IllegalSeverity.SEVERE: 4,
}

# Severity → heat outcome. LIGHT/MODERATE flip is_suspect; SEVERE flips is_wanted.
SUSPECT_SEVERITIES = frozenset({IllegalSeverity.LIGHT, IllegalSeverity.MODERATE})
WANTED_SEVERITIES = frozenset({IllegalSeverity.SEVERE})

# Notoriety nudge: a clean (undetected) contraband sale pushes personal_reputation
# negative — outlaw cred — which in turn RAISES future detection probability (the
# rep term above). Heat is self-reinforcing (brief §1.5).
NOTORIETY_NUDGE_PER_SALE = -2


class ContrabandService:
    """Server-authoritative black-market trading: gated catalog, haggle-priced
    buy, and sell-with-detection. Flushes only — the route owns the commit."""

    def __init__(self, db: Session):
        self.db = db

    # ------------------------------------------------------------------
    # Pricing
    # ------------------------------------------------------------------
    @staticmethod
    def _personality_modifier() -> float:
        """The BLACK_MARKET trader's haggle multiplier — a uniform ±25% roll
        (brief [OPEN-3]). Centered on 1.0, so the long-run mean is the posted
        ``base_price × category_multiplier``."""
        return 1.0 + _RNG.uniform(-HAGGLE_SWING, HAGGLE_SWING)

    @staticmethod
    def _unit_price(meta: IllegalCommodityMeta, personality_modifier: float) -> int:
        """``base_price × category_multiplier × personality_modifier`` (brief
        §1.3), floored at 1 cr/unit so a deep haggle can never zero the price."""
        price = meta.base_price * meta.category_multiplier * personality_modifier
        return max(1, int(round(price)))

    # ------------------------------------------------------------------
    # Access gate
    # ------------------------------------------------------------------
    def _is_black_market_venue(self, station: Optional[Station]) -> bool:
        """True iff the station is a BLACK_MARKET-type venue (brief §1.1)."""
        return station is not None and station.type == StationType.BLACK_MARKET

    def _passes_rep_gate(self, player_id: uuid.UUID) -> bool:
        """True iff the player's Fringe-Alliance (OUTLAWS) reputation is at least
        RECOGNIZED (brief §1.1 / [OPEN-1]).

        The Reputation row keys on ``faction_id``, so resolve the OUTLAWS faction
        first. A missing faction row (un-seeded roster) or a missing reputation
        row both read as "below gate" — the conservative default: a player with no
        Fringe standing is NOT vouched in.

        Tier comparison uses ``Reputation.numeric_level`` (an ordered −8..+8
        scale) so the gate is a clean ordinal compare rather than a fragile
        enum-identity check.
        """
        faction = (
            self.db.query(Faction)
            .filter(Faction.faction_type == GATE_FACTION)
            .first()
        )
        if faction is None:
            logger.warning(
                "black-market rep gate: no %s faction row exists — gate fails "
                "closed for player %s",
                GATE_FACTION.name, player_id,
            )
            return False

        reputation = (
            self.db.query(Reputation)
            .filter(
                Reputation.player_id == player_id,
                Reputation.faction_id == faction.id,
            )
            .first()
        )
        if reputation is None:
            return False

        return self._level_rank(reputation.current_level) >= self._level_rank(GATE_MIN_LEVEL)

    @staticmethod
    def _level_rank(level: ReputationLevel) -> int:
        """Ordered rank for a ReputationLevel (PUBLIC_ENEMY=−8 .. EXALTED=+8),
        matching ``Reputation.numeric_level`` but computed from the level alone
        (no row instance needed, so the gate can rank ``GATE_MIN_LEVEL``)."""
        order = {
            ReputationLevel.PUBLIC_ENEMY: -8,
            ReputationLevel.CRIMINAL: -7,
            ReputationLevel.OUTLAW: -6,
            ReputationLevel.PIRATE: -5,
            ReputationLevel.SMUGGLER: -4,
            ReputationLevel.UNTRUSTWORTHY: -3,
            ReputationLevel.SUSPICIOUS: -2,
            ReputationLevel.QUESTIONABLE: -1,
            ReputationLevel.NEUTRAL: 0,
            ReputationLevel.RECOGNIZED: 1,
            ReputationLevel.ACKNOWLEDGED: 2,
            ReputationLevel.TRUSTED: 3,
            ReputationLevel.RESPECTED: 4,
            ReputationLevel.VALUED: 5,
            ReputationLevel.HONORED: 6,
            ReputationLevel.REVERED: 7,
            ReputationLevel.EXALTED: 8,
        }
        return order.get(level, 0)

    # ------------------------------------------------------------------
    # Shared lookups
    # ------------------------------------------------------------------
    def _resolve_commodity(self, commodity: Any) -> Optional[IllegalCommodity]:
        """Coerce a request value (string or enum) to an ENABLED IllegalCommodity,
        or None. SLAVES (and any unknown value) resolves to None so buy/sell
        reject it unconditionally — there is no code path that trades a disabled
        commodity."""
        if isinstance(commodity, IllegalCommodity):
            resolved = commodity
        else:
            try:
                resolved = IllegalCommodity(str(commodity))
            except ValueError:
                return None
        # is_enabled() returns False for SLAVES (permanently disabled) regardless
        # of any flag — the unconditional rejection the reviewer asserts.
        return resolved if is_enabled(resolved) else None

    def _lock_station_player_ship(
        self,
        station_id: uuid.UUID,
        player_id: uuid.UUID,
        ship_id: Optional[uuid.UUID],
    ) -> Tuple[Optional[Station], Optional[Player], Optional[Ship], Optional[str]]:
        """LOCK ORDER (L3 / trading.py convention): lock the STATION row, THEN the
        PLAYER row, THEN the ship row — all ``FOR UPDATE`` — to avoid the AB-BA
        deadlock the legal trade path already guards against. Returns
        ``(station, player, ship, reason)``; ``reason`` is a stable code on
        failure."""
        station = (
            self.db.query(Station)
            .filter(Station.id == station_id)
            .with_for_update()
            .first()
        )
        if station is None:
            return None, None, None, "station_not_found"

        player = (
            self.db.query(Player)
            .filter(Player.id == player_id)
            .with_for_update()
            .first()
        )
        if player is None:
            return None, None, None, "player_not_found"

        ship = (
            self.db.query(Ship)
            .filter(Ship.id == ship_id, Ship.owner_id == player_id)
            .with_for_update()
            .first()
        )
        if ship is None:
            return None, None, None, "ship_not_found"

        return station, player, ship, None

    @staticmethod
    def _cargo(ship: Ship) -> Dict[str, Any]:
        """The ship's cargo JSONB as a dict, defaulting to the empty 50-capacity
        shell. NEVER references ``ship.max_cargo`` (L2 — it does not exist)."""
        cargo = ship.cargo if isinstance(ship.cargo, dict) else {}
        return cargo

    @staticmethod
    def _cargo_capacity(cargo: Dict[str, Any]) -> int:
        """Cargo capacity from the JSONB (default 50). L2: capacity ALWAYS comes
        from the JSONB convention, never from a ship column."""
        cap = cargo.get("capacity", DEFAULT_CARGO_CAPACITY)
        try:
            return int(cap)
        except (TypeError, ValueError):
            return DEFAULT_CARGO_CAPACITY

    # ------------------------------------------------------------------
    # Catalog
    # ------------------------------------------------------------------
    def get_catalog(self, player: Player, station: Station) -> Optional[Dict[str, Any]]:
        """The contraband catalog + computed prices for a qualifying venue, or
        ``None`` if the access gate is unmet (route → 404, hiding the menu).

        Pricing here is INDICATIVE: each row carries the per-unit price for a
        fresh ±25% haggle roll, so the displayed price is a live quote. The
        committed buy/sell re-rolls its own price under the row lock — the catalog
        does not reserve a price. SLAVES never appears (``ENABLED_COMMODITIES``).
        """
        if not self._is_black_market_venue(station):
            return None
        if not self._passes_rep_gate(player.id):
            return None

        listings = []
        for commodity in ENABLED_COMMODITIES:
            meta = get_meta(commodity)
            modifier = self._personality_modifier()
            listings.append(
                {
                    "commodity": commodity.value,
                    "base_price": meta.base_price,
                    "category_multiplier": meta.category_multiplier,
                    "severity": meta.severity.value,
                    "indicative_unit_price": self._unit_price(meta, modifier),
                    "federation_rep_delta": meta.federation_rep_delta,
                }
            )

        return {
            "station_id": str(station.id),
            "station_name": station.name,
            "haggle_swing": HAGGLE_SWING,
            "commodities": listings,
        }

    # ------------------------------------------------------------------
    # Buy
    # ------------------------------------------------------------------
    def buy(
        self,
        player: Player,
        ship: Ship,
        station: Station,
        commodity: Any,
        quantity: int,
    ) -> Dict[str, Any]:
        """Purchase ``quantity`` units of ``commodity`` at the venue. Charges
        credits, adds the goods to ``Ship.cargo`` under the ``illegal:<commodity>``
        key (counts against capacity), records a flagged ``MarketTransaction``, and
        fires faction rep deltas. FLUSH only — the route commits.

        Returns ``{success, reason, ...}``; ``reason`` is a stable code on a
        rejection (gate_unmet / disabled_commodity / not_docked /
        insufficient_credits / cargo_full / ...).
        """
        # Resolve + reject disabled (SLAVES) / unknown commodities up-front.
        resolved = self._resolve_commodity(commodity)
        if resolved is None:
            return {"success": False, "reason": "disabled_commodity"}
        if quantity is None or quantity <= 0:
            return {"success": False, "reason": "invalid_quantity"}

        station, player, ship, reason = self._lock_station_player_ship(
            station.id, player.id, ship.id
        )
        if reason:
            return {"success": False, "reason": reason}

        # Gate (re-checked under lock — the venue type + rep can't shift mid-trade,
        # but the gate must hold on the locked rows for a server-authoritative buy).
        if not self._is_black_market_venue(station):
            return {"success": False, "reason": "gate_unmet"}
        if not self._passes_rep_gate(player.id):
            return {"success": False, "reason": "gate_unmet"}

        # Docked + co-located preflight (mirror the legal trade path).
        if not player.is_docked or player.current_sector_id != station.sector_id:
            return {"success": False, "reason": "not_docked"}

        meta = get_meta(resolved)
        unit_price = self._unit_price(meta, self._personality_modifier())
        total_cost = unit_price * quantity

        if (player.credits or 0) < total_cost:
            return {
                "success": False,
                "reason": "insufficient_credits",
                "needed": total_cost,
                "have": player.credits or 0,
            }

        # Cargo capacity (L2 — from the JSONB, never ship.max_cargo). Contraband
        # counts against the same pool as legal goods.
        cargo = self._cargo(ship)
        used = int(cargo.get("used", 0) or 0)
        capacity = self._cargo_capacity(cargo)
        if used + quantity > capacity:
            return {
                "success": False,
                "reason": "cargo_full",
                "free": max(0, capacity - used),
                "needed": quantity,
            }

        # --- mutate under lock ------------------------------------------
        player.credits = (player.credits or 0) - total_cost

        key = cargo_key(resolved)
        contents = cargo.get("contents", {})
        if not isinstance(contents, dict):
            contents = {}
        contents[key] = int(contents.get(key, 0)) + quantity
        cargo["contents"] = contents
        cargo["used"] = used + quantity
        if "capacity" not in cargo:
            cargo["capacity"] = capacity
        ship.cargo = cargo
        flag_modified(ship, "cargo")

        transaction = MarketTransaction(
            player_id=player.id,
            station_id=station.id,
            transaction_type=TransactionType.BUY,
            commodity=resolved.value,
            quantity=quantity,
            unit_price=unit_price,
            total_value=total_cost,
            sector_id=player.current_sector_id,
            is_illegal=True,
            illegal_commodity=resolved.value,
            timestamp=datetime.now(UTC),
        )
        self.db.add(transaction)

        # Faction rep deltas — sync, flush-only helper INSIDE this txn (the async
        # update_reputation commits mid-txn, so it must NOT be used here).
        rep_deltas = self._apply_rep_deltas(player.id, meta, "black_market_buy")

        self.db.flush()

        return {
            "success": True,
            "reason": None,
            "commodity": resolved.value,
            "quantity": quantity,
            "unit_price": unit_price,
            "total_cost": total_cost,
            "remaining_credits": player.credits,
            "remaining_cargo_space": capacity - cargo["used"],
            "rep_deltas": rep_deltas,
        }

    # ------------------------------------------------------------------
    # Sell
    # ------------------------------------------------------------------
    def sell(
        self,
        player: Player,
        ship: Ship,
        station: Station,
        commodity: Any,
        quantity: int,
    ) -> Dict[str, Any]:
        """Sell ``quantity`` units of held contraband, then run the detection roll.

        CLEAN sale: pays out, nudges ``personal_reputation`` negative (notoriety),
        and fires faction rep deltas. FAILED roll: the SALE IS VOIDED — no payout —
        the entire held quantity of that commodity is CONFISCATED, a severity-scaled
        fine is levied, the Federation (+other) rep deltas apply, and ``is_suspect``
        (LIGHT/MODERATE) or ``is_wanted`` (SEVERE) flips. FLUSH only — route commits.

        Returns ``{success, detected, reason, ...}``.
        """
        resolved = self._resolve_commodity(commodity)
        if resolved is None:
            return {"success": False, "detected": False, "reason": "disabled_commodity"}
        if quantity is None or quantity <= 0:
            return {"success": False, "detected": False, "reason": "invalid_quantity"}

        station, player, ship, reason = self._lock_station_player_ship(
            station.id, player.id, ship.id
        )
        if reason:
            return {"success": False, "detected": False, "reason": reason}

        if not self._is_black_market_venue(station):
            return {"success": False, "detected": False, "reason": "gate_unmet"}
        if not self._passes_rep_gate(player.id):
            return {"success": False, "detected": False, "reason": "gate_unmet"}

        if not player.is_docked or player.current_sector_id != station.sector_id:
            return {"success": False, "detected": False, "reason": "not_docked"}

        meta = get_meta(resolved)
        key = cargo_key(resolved)
        cargo = self._cargo(ship)
        contents = cargo.get("contents", {})
        if not isinstance(contents, dict):
            contents = {}
        held = int(contents.get(key, 0) or 0)
        if held < quantity:
            return {
                "success": False,
                "detected": False,
                "reason": "insufficient_cargo",
                "held": held,
                "requested": quantity,
            }

        capacity = self._cargo_capacity(cargo)
        unit_price = self._unit_price(meta, self._personality_modifier())
        sale_value = unit_price * quantity

        # --- detection roll (brief §1.4) --------------------------------
        sector = self._resolve_sector(player)
        # The illegal_value the scan "sees" is the value of ALL contraband held —
        # a full hold of hot goods is more conspicuous than the single line sold.
        total_illegal_value = self._total_illegal_value(contents)
        p_detect = self._detection_probability(
            illegal_value=total_illegal_value,
            cargo_capacity=capacity,
            sector=sector,
            personal_reputation=player.personal_reputation or 0,
        )
        detected = _RNG.random() < p_detect

        if detected:
            return self._resolve_bust(
                player=player,
                ship=ship,
                station=station,
                cargo=cargo,
                contents=contents,
                meta=meta,
                commodity=resolved,
                p_detect=p_detect,
            )

        # --- CLEAN sale -------------------------------------------------
        remaining = held - quantity
        if remaining > 0:
            contents[key] = remaining
        else:
            contents.pop(key, None)
        cargo["contents"] = contents
        cargo["used"] = max(0, int(cargo.get("used", 0) or 0) - quantity)
        if "capacity" not in cargo:
            cargo["capacity"] = capacity
        ship.cargo = cargo
        flag_modified(ship, "cargo")

        player.credits = (player.credits or 0) + sale_value

        # Notoriety: a clean contraband sale earns outlaw cred (personal_rep down),
        # which raises future detection probability (self-reinforcing heat).
        self._adjust_notoriety(player.id, NOTORIETY_NUDGE_PER_SALE, "black_market_sale")

        # Faction rep deltas (sync, in-txn) — selling contraband still angers the
        # law even when undetected (the law watches the venue).
        rep_deltas = self._apply_rep_deltas(player.id, meta, "black_market_sell")

        # Record the (flagged) sale on the ledger — is_illegal / illegal_commodity
        # are the additive MarketTransaction columns this WO added (mirrors buy()).
        self.db.add(MarketTransaction(
            player_id=player.id,
            station_id=station.id,
            transaction_type=TransactionType.SELL,
            commodity=resolved.value,
            quantity=quantity,
            unit_price=unit_price,
            total_value=sale_value,
            sector_id=player.current_sector_id,
            is_illegal=True,
            illegal_commodity=resolved.value,
            timestamp=datetime.now(UTC),
        ))

        self.db.flush()

        return {
            "success": True,
            "detected": False,
            "reason": None,
            "commodity": resolved.value,
            "quantity": quantity,
            "unit_price": unit_price,
            "sale_value": sale_value,
            "detection_probability": round(p_detect, 4),
            "remaining_credits": player.credits,
            "rep_deltas": rep_deltas,
        }

    # ------------------------------------------------------------------
    # Detection
    # ------------------------------------------------------------------
    def _detection_probability(
        self,
        illegal_value: int,
        cargo_capacity: int,
        sector: Optional[Sector],
        personal_reputation: int,
    ) -> float:
        """The brief §1.4 four-term ``P(detected)``, clamped to [0.0, 0.95]::

            base(0.05)
            + cargo·(illegal_value / cargo_cap)
            + sector·(1 − security_level / 10)
            + rep·(1 − personal_reputation / 1000)

        Notes on the term inputs:
        * cargo term — ``illegal_value / cargo_cap`` is the value-density of hot
          goods. Capacity is the JSONB capacity (never a ship column). Clamped to
          [0, 1] so a small hold in a huge ship can't drive the term negative and
          a value spike can't blow past 1.
        * sector term — higher ``security_level`` (1..10) LOWERS risk at the venue;
          a low-security frontier sector raises it. A missing sector / NULL
          security reads as the mid default (5) so it neither helps nor over-
          penalises. Clamped [0, 1].
        * rep term — a more-negative ``personal_reputation`` (outlaw cred) RAISES
          risk: ``(1 − personal_reputation/1000)`` is 1.0 at neutral, <1 for a
          lawful player, >1 for a notorious one. Clamped to [0, 2] so a maxed-out
          villain still can't deterministically bust past the 0.95 cap on its own.
        """
        cap = cargo_capacity if cargo_capacity and cargo_capacity > 0 else DEFAULT_CARGO_CAPACITY
        cargo_term = self._clamp(illegal_value / cap, 0.0, 1.0)

        security_level = 5
        if sector is not None and sector.security_level is not None:
            security_level = sector.security_level
        sector_term = self._clamp(1.0 - (security_level / 10.0), 0.0, 1.0)

        rep_term = self._clamp(1.0 - (personal_reputation / 1000.0), 0.0, 2.0)

        p = (
            DETECT_BASE_RATE
            + DETECT_CARGO_WEIGHT * cargo_term
            + DETECT_SECTOR_WEIGHT * sector_term
            + DETECT_REP_WEIGHT * rep_term
        )
        return self._clamp(p, DETECT_PROB_MIN, DETECT_PROB_MAX)

    def _resolve_sector(self, player: Player) -> Optional[Sector]:
        """The player's current sector. ``Player.current_sector_id`` is the GLOBAL
        ``Sector.sector_id`` integer (NOT the UUID PK)."""
        if player.current_sector_id is None:
            return None
        return (
            self.db.query(Sector)
            .filter(Sector.sector_id == player.current_sector_id)
            .first()
        )

    @staticmethod
    def _total_illegal_value(contents: Dict[str, Any]) -> int:
        """Sum the catalog-base value of ALL contraband held in the cargo
        ``contents`` dict (keys ``illegal:<commodity>``). Uses ``base_price`` (the
        pre-haggle reference) so the value the scan "sees" is stable, not the
        haggled sale price. Disabled/unknown keys are skipped defensively."""
        total = 0
        for raw_key, qty in (contents or {}).items():
            if not isinstance(raw_key, str) or not raw_key.startswith("illegal:"):
                continue
            value_str = raw_key.split("illegal:", 1)[1]
            try:
                commodity = IllegalCommodity(value_str)
            except ValueError:
                continue
            if not is_enabled(commodity):
                continue
            try:
                meta = get_meta(commodity)
            except (KeyError, ValueError):
                continue
            try:
                total += meta.base_price * int(qty)
            except (TypeError, ValueError):
                continue
        return total

    # ------------------------------------------------------------------
    # Bust resolution (failed detection roll)
    # ------------------------------------------------------------------
    def _resolve_bust(
        self,
        player: Player,
        ship: Ship,
        station: Station,
        cargo: Dict[str, Any],
        contents: Dict[str, Any],
        meta: IllegalCommodityMeta,
        commodity: IllegalCommodity,
        p_detect: float,
    ) -> Dict[str, Any]:
        """Confiscate ALL illegal cargo, levy a severity-scaled fine, apply the
        Federation (+other) rep deltas, and flip the heat flag. No payout — the
        sale is voided. FLUSH only.

        Fine = TOTAL confiscated value × {LIGHT 2, MODERATE 3, SEVERE 4}, clamped
        to the player's available credits (credits never go negative — the unpaid
        remainder is simply unrecoverable here; debt is owned elsewhere).
        """
        # Confiscated value across ALL contraband (the whole hot hold is seized).
        confiscated_value = self._total_illegal_value(contents)

        # Strip every illegal:* key from the cargo and decrement 'used'.
        confiscated_units = 0
        for raw_key in list(contents.keys()):
            if isinstance(raw_key, str) and raw_key.startswith("illegal:"):
                try:
                    confiscated_units += int(contents.get(raw_key, 0) or 0)
                except (TypeError, ValueError):
                    pass
                contents.pop(raw_key, None)
        cargo["contents"] = contents
        cargo["used"] = max(0, int(cargo.get("used", 0) or 0) - confiscated_units)
        ship.cargo = cargo
        flag_modified(ship, "cargo")

        # Fine: severity multiplier × confiscated value, clamped to held credits.
        multiplier = FINE_MULTIPLIER_BY_SEVERITY.get(meta.severity, 2)
        fine = confiscated_value * multiplier
        available = player.credits or 0
        fine_charged = min(fine, available)
        player.credits = available - fine_charged

        # Heat flip (severity → suspect/wanted).
        heat = self._apply_heat(player, meta.severity)

        # Faction rep deltas — getting BUSTED applies the contraband rep deltas
        # (Federation down hardest). Sync, in-txn helper.
        rep_deltas = self._apply_rep_deltas(player.id, meta, "black_market_bust")

        self.db.flush()

        logger.info(
            "black-market BUST: player %s at station %s — commodity %s severity %s "
            "p=%.4f confiscated %d units (value %d) fine %d heat=%s",
            player.id, station.id, commodity.value, meta.severity.value,
            p_detect, confiscated_units, confiscated_value, fine_charged, heat,
        )

        return {
            "success": True,
            "detected": True,
            "reason": "detected",
            "commodity": commodity.value,
            "confiscated_units": confiscated_units,
            "confiscated_value": confiscated_value,
            "fine": fine_charged,
            "fine_assessed": fine,
            "heat": heat,
            "detection_probability": round(p_detect, 4),
            "remaining_credits": player.credits,
            "rep_deltas": rep_deltas,
        }

    @staticmethod
    def _apply_heat(player: Player, severity: IllegalSeverity) -> str:
        """Flip the player's heat flag per severity (brief [OPEN-7]):
        LIGHT/MODERATE → ``is_suspect``; SEVERE → ``is_wanted`` (which also implies
        suspect). Records the declared-at timestamp on a fresh flip. Returns the
        heat state set ("wanted" / "suspect")."""
        now = datetime.now(UTC)
        if severity in WANTED_SEVERITIES:
            if not player.is_wanted:
                player.is_wanted = True
                player.wanted_declared_at = now
            # A wanted player is implicitly also a suspect.
            if not player.is_suspect:
                player.is_suspect = True
                player.suspect_declared_at = now
            return "wanted"
        # LIGHT / MODERATE → suspect.
        if not player.is_suspect:
            player.is_suspect = True
            player.suspect_declared_at = now
        return "suspect"

    # ------------------------------------------------------------------
    # Reputation hooks
    # ------------------------------------------------------------------
    def _apply_rep_deltas(
        self, player_id: uuid.UUID, meta: IllegalCommodityMeta, reason: str
    ) -> Dict[str, int]:
        """Apply the Federation rep delta + every other-faction delta from the
        commodity metadata, all via the SYNC, flush-only
        ``apply_faction_rep_delta`` (the async ``update_reputation`` commits mid-
        txn and would break this caller-owned transaction).

        Other-faction deltas key on the *string value* of ``FactionType`` in the
        catalog; resolve each back to a ``FactionType`` member and route it. A
        value that does not map to a current roster member is skipped here (and
        ``apply_faction_rep_delta`` itself also logs-and-returns on an unknown
        faction), so a missing faction degrades to a dropped delta — never an
        exception. Returns the deltas actually dispatched (by faction value).
        """
        applied: Dict[str, int] = {}

        # Federation — always present, always negative.
        if meta.federation_rep_delta:
            apply_faction_rep_delta(
                self.db, player_id, FactionType.FEDERATION,
                meta.federation_rep_delta, reason=reason,
            )
            applied[FactionType.FEDERATION.value] = meta.federation_rep_delta

        # Other factions — keyed on the FactionType string value in the catalog.
        for faction_value, delta in (meta.other_faction_rep_deltas or {}).items():
            if not delta:
                continue
            try:
                faction_type = FactionType(faction_value)
            except ValueError:
                # Not a current roster member (e.g. a future Nova Scientific) —
                # silent no-op until the roster expands (brief §2 footnote).
                logger.debug(
                    "black-market rep delta: faction %r not in roster — skipped",
                    faction_value,
                )
                continue
            apply_faction_rep_delta(
                self.db, player_id, faction_type, delta, reason=reason,
            )
            applied[faction_type.value] = delta

        return applied

    def _adjust_notoriety(self, player_id: uuid.UUID, amount: int, reason: str) -> None:
        """Nudge the player's personal_reputation (notoriety axis) via the
        flush-only ``PersonalReputationService.adjust_reputation``. Defensive —
        a notoriety hiccup must never void an otherwise-clean sale (the credit
        payout already happened under the lock)."""
        try:
            from src.services.personal_reputation_service import PersonalReputationService
            PersonalReputationService(self.db).adjust_reputation(player_id, amount, reason)
        except Exception:
            logger.warning("black-market notoriety nudge failed (non-fatal)", exc_info=True)

    # ------------------------------------------------------------------
    # Utilities
    # ------------------------------------------------------------------
    @staticmethod
    def _clamp(value: float, lo: float, hi: float) -> float:
        """Clamp ``value`` to ``[lo, hi]``."""
        return max(lo, min(hi, value))
