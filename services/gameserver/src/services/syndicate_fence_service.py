"""Shadow Syndicate fence venues — first slice (LEG-300).

Canon: ``FEATURES/economy/black-market.md`` Syndicate fence venues.
DATA_MODELS: ``Station.has_syndicate_fence``.

In this slice:
- host eligibility + ~8% galaxy-gen roll
- fence-tab visibility (Syndicate ≥ NEUTRAL AND personal_reputation ≤ 0)
- cargo fencing at a flat 70% of legal market (``commodity_economy.base_price``)
- ``flagged_origin`` consumption on ``Ship.cargo``

Out of scope: laundering, counterfeit, bounty placement, Federation raids.
"""

from __future__ import annotations

import logging
import random
from typing import Any, Dict, Optional, Tuple
from uuid import UUID

from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from src.core.commodity_economy import base_price
from src.models.faction import Faction, FactionType
from src.models.player import Player
from src.models.reputation import Reputation, ReputationLevel
from src.models.ship import Ship
from src.models.station import Station

logger = logging.getLogger(__name__)

# black-market.md cargo fencing: "Pays ~70% of market value"
FENCE_PAYOUT_PERCENT = 70
# black-market.md host eligibility: "Approximately 8% of eligible stations"
FENCE_ELIGIBLE_RATE = 0.08

_LEVEL_RANK = {
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


def level_rank(level: Optional[ReputationLevel]) -> Optional[int]:
    if level is None:
        return None
    return _LEVEL_RANK.get(level)


def cargo_fence_payout(market_value: int) -> int:
    """Player credits for a fence sale: flat 70% of legal market value.

    Integer floor so a 1-cr unit pays 0 — same as (value * 70) // 100.
    The 65/25/10 operator/Syndicate split is accounting-only in this slice
    (no operator credit accounts exist); WO Accept is the 70% player payout.
    """
    if market_value <= 0:
        return 0
    return (int(market_value) * FENCE_PAYOUT_PERCENT) // 100


def host_station_fence_eligible(
    *,
    tradedock_tier: Optional[str],
    faction_affiliation: Optional[str],
) -> bool:
    """True when a host may receive ``has_syndicate_fence`` at worldgen.

    Ineligible: Terran Federation, Nova Scientific Institute, any TradeDock.
    Unaligned / missing affiliation is Independent → eligible.
    """
    if tradedock_tier:
        return False
    raw = (faction_affiliation or "").strip()
    if not raw:
        return True
    lower = raw.lower()
    if lower in {"federation", FactionType.FEDERATION.value.lower()}:
        return False
    if lower in {"explorers", FactionType.EXPLORERS.value.lower()}:
        return False
    if "terran federation" in lower:
        return False
    if "nova scientific" in lower:
        return False
    return True


def roll_has_syndicate_fence(eligible: bool, rng: random.Random) -> bool:
    if not eligible:
        return False
    return rng.random() < FENCE_ELIGIBLE_RATE


def assign_has_syndicate_fence(
    *,
    universe_seed: int,
    sector_int_id: int,
    name: str,
    tradedock_tier: Optional[str],
    faction_affiliation: Optional[str] = None,
    rng: Optional[random.Random] = None,
) -> bool:
    """Deterministic per-station roll used by bang import (Path A galaxy gen)."""
    eligible = host_station_fence_eligible(
        tradedock_tier=tradedock_tier,
        faction_affiliation=faction_affiliation,
    )
    if rng is None:
        rng = random.Random(f"{universe_seed}:syndicate-fence:{sector_int_id}:{name}")
    return roll_has_syndicate_fence(eligible, rng)


def syndicate_fence_tab_visible(
    *,
    syndicate_level: Optional[ReputationLevel],
    personal_reputation: int,
) -> bool:
    """Fence tab: Syndicate ≥ NEUTRAL and personal_reputation ≤ 0.

    Missing Syndicate row = no first contact → hidden (conservative).
    """
    rank = level_rank(syndicate_level)
    if rank is None:
        return False
    if rank < _LEVEL_RANK[ReputationLevel.NEUTRAL]:
        return False
    if int(personal_reputation) > 0:
        return False
    return True


def flagged_origin_qty(cargo: Dict[str, Any], commodity: str) -> int:
    flagged = cargo.get("flagged_origin")
    if not isinstance(flagged, dict):
        return 0
    try:
        return max(0, int(flagged.get(commodity, 0) or 0))
    except (TypeError, ValueError):
        return 0


def contents_qty(cargo: Dict[str, Any], commodity: str) -> int:
    contents = cargo.get("contents")
    if not isinstance(contents, dict):
        return 0
    raw = contents.get(commodity, 0)
    try:
        return max(0, int(raw or 0))
    except (TypeError, ValueError):
        return 0


def consume_flagged_origin(
    cargo: Dict[str, Any], commodity: str, quantity: int
) -> Tuple[bool, str, int]:
    """Remove flagged units from cargo. Returns (ok, reason, market_value)."""
    if quantity <= 0:
        return False, "invalid_quantity", 0
    unit = base_price(commodity)
    if unit <= 0:
        return False, "unknown_commodity", 0
    held_flagged = flagged_origin_qty(cargo, commodity)
    held_contents = contents_qty(cargo, commodity)
    if held_flagged < quantity:
        return False, "insufficient_flagged_origin", 0
    if held_contents < quantity:
        return False, "insufficient_cargo", 0
    contents = dict(cargo.get("contents") or {})
    flagged = dict(cargo.get("flagged_origin") or {})
    contents[commodity] = held_contents - quantity
    remaining_flagged = held_flagged - quantity
    if remaining_flagged:
        flagged[commodity] = remaining_flagged
    else:
        flagged.pop(commodity, None)
    used = int(cargo.get("used", 0) or 0)
    cargo["contents"] = contents
    cargo["flagged_origin"] = flagged
    cargo["used"] = max(0, used - quantity)
    return True, "ok", unit * quantity


class SyndicateFenceService:
    def __init__(self, db: Session):
        self.db = db

    def _syndicate_level(self, player_id: UUID) -> Optional[ReputationLevel]:
        faction = (
            self.db.query(Faction)
            .filter(Faction.faction_type == FactionType.SYNDICATE)
            .first()
        )
        if faction is None:
            return None
        row = (
            self.db.query(Reputation)
            .filter(
                Reputation.player_id == player_id,
                Reputation.faction_id == faction.id,
            )
            .first()
        )
        if row is None:
            return None
        return row.current_level

    def tab_visible_for(self, player: Player, station: Station) -> bool:
        if not getattr(station, "has_syndicate_fence", False):
            return False
        return syndicate_fence_tab_visible(
            syndicate_level=self._syndicate_level(player.id),
            personal_reputation=int(player.personal_reputation or 0),
        )

    def fence_cargo(
        self,
        player: Player,
        ship: Ship,
        station: Station,
        commodity: str,
        quantity: int,
    ) -> Dict[str, Any]:
        """Flush-only; route owns commit. 404-equivalent reasons: gate_unmet."""
        if not getattr(station, "has_syndicate_fence", False):
            return {"success": False, "reason": "gate_unmet"}
        if not syndicate_fence_tab_visible(
            syndicate_level=self._syndicate_level(player.id),
            personal_reputation=int(player.personal_reputation or 0),
        ):
            return {"success": False, "reason": "gate_unmet"}
        port_id = getattr(player, "current_port_id", None)
        docked_here = bool(getattr(player, "is_docked", False)) and (
            (port_id is not None and str(port_id) == str(station.id))
            or (port_id is None and player.current_sector_id == station.sector_id)
        )
        if not docked_here:
            return {"success": False, "reason": "not_docked"}

        cargo = dict(ship.cargo) if isinstance(ship.cargo, dict) else {}
        ok, reason, market_value = consume_flagged_origin(cargo, commodity, quantity)
        if not ok:
            return {"success": False, "reason": reason}

        payout = cargo_fence_payout(market_value)
        ship.cargo = cargo
        try:
            flag_modified(ship, "cargo")
        except Exception:
            pass
        player.credits = int(player.credits or 0) + payout

        # LEG-3388 / LEG-3389 — emergent SS rep on successful fence.
        # Fence requires flagged_origin (stolen/early-grace salvage), so every
        # success is a STOLEN_FLAGGED_SALE_SS transaction (+10 once) and also
        # accrues FENCE_SYNDICATE_VOLUME_SS (+5 / 5,000 cr gross market_value).
        # Defensive: never fail the fence txn on a rep hiccup.
        try:
            from src.services.emergent_reputation_service import (
                apply_emergent_action,
                apply_trade_volume_rep,
            )

            sector_ctx = {"sector_id": getattr(player, "current_sector_id", None)}
            apply_emergent_action(
                self.db,
                player,
                "STOLEN_FLAGGED_SALE_SS",
                sector_ctx,
            )
            apply_trade_volume_rep(
                self.db,
                player,
                "FENCE_SYNDICATE_VOLUME_SS",
                market_value,
                sector_ctx,
            )
        except Exception:
            logging.getLogger(__name__).warning(
                "emergent SS fence rep failed (non-fatal)",
                exc_info=True,
            )

        return {
            "success": True,
            "reason": "ok",
            "commodity": commodity,
            "quantity": quantity,
            "market_value": market_value,
            "payout": payout,
            "payout_percent": FENCE_PAYOUT_PERCENT,
            "credits": player.credits,
        }
