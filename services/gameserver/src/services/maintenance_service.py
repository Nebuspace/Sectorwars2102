"""maintenance_service — ship maintenance decay + performance bands.

Canon: FEATURES/gameplay/ships.md "Maintenance system". Condition (0-100) lives
in the ship.maintenance JSONB; it decays per real day by hull class and drives a
performance-band penalty. Decay is applied lazily (advance-on-read), mirroring
PlanetaryService.apply_population_growth / the market regen anchor — no scheduler.

The combat-effectiveness band is consumed in combat, and the speed band is now
consumed in the move-cost path (WO-MAINTBANDS, movement_service). The fuel
modifier stays unconsumed because the game has no per-move fuel sink (movement
costs turns, not fuel); the status payload reports applied-vs-unconsumed effects
honestly rather than pretending the fuel band bites.
"""
from datetime import datetime, timezone
import logging

from sqlalchemy.orm.attributes import flag_modified

from src.models.ship import Ship, ShipType

logger = logging.getLogger(__name__)

# Canon decay (% of condition lost per real day), by hull class (ships.md:58-64).
# ESCAPE_POD is intentionally absent — pods do not decay.
DECAY_PCT_PER_DAY = {
    ShipType.LIGHT_FREIGHTER: 1.0,
    ShipType.FAST_COURIER: 1.0,
    # FC mirror per ship-roster.md Citizen Clipper — P2W firewall: no edge, no deficit.
    ShipType.CITIZEN_CLIPPER: 1.0,
    ShipType.SCOUT_SHIP: 1.0,
    ShipType.CARGO_HAULER: 2.0,
    ShipType.COLONY_SHIP: 2.0,
    ShipType.DEFENDER: 2.0,
    ShipType.CARRIER: 3.0,
    ShipType.WARP_JUMPER: 3.0,
}

# Canon performance bands (ships.md:68-75). speed/combat/fuel are fractional
# modifiers; failure_chance is per-jump. Ordered high → low; first match wins.
_BANDS = [
    (90.0, {"tier": "Pristine", "speed": 0.05, "combat": 0.05, "fuel": -0.05, "failure": 0.0, "failure_tier": None}),
    (75.0, {"tier": "Good", "speed": 0.0, "combat": 0.0, "fuel": 0.0, "failure": 0.0, "failure_tier": None}),
    (50.0, {"tier": "Worn", "speed": -0.05, "combat": -0.05, "fuel": 0.05, "failure": 0.0, "failure_tier": None}),
    (25.0, {"tier": "Degraded", "speed": -0.15, "combat": -0.20, "fuel": 0.20, "failure": 0.05, "failure_tier": "MINOR"}),
    (10.0, {"tier": "Failing", "speed": -0.30, "combat": -0.40, "fuel": 0.50, "failure": 0.15, "failure_tier": "MAJOR"}),
    (0.0, {"tier": "Critical", "speed": -0.50, "combat": -0.75, "fuel": 1.00, "failure": 0.30, "failure_tier": "CATASTROPHIC"}),
]


def maintenance_band(condition: float) -> dict:
    """The performance band for a condition value (0-100)."""
    c = max(0.0, min(100.0, float(condition)))
    for threshold, band in _BANDS:
        if c >= threshold:
            return band
    return _BANDS[-1][1]


def _decay_pct_per_day(ship: Ship) -> float:
    return DECAY_PCT_PER_DAY.get(ship.type, 0.0)


def _parse_anchor(anchor_str):
    if not anchor_str:
        return None
    try:
        dt = datetime.fromisoformat(str(anchor_str).replace("Z", "+00:00"))
    except (ValueError, AttributeError, TypeError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def effective_condition(ship: Ship, now: datetime = None) -> float:
    """Current condition after lazy decay — PURE (no mutation).

    Used by combat so the penalty always reflects real elapsed time even if the
    stored condition hasn't been refreshed by a read endpoint yet.
    """
    m = ship.maintenance or {}
    cond = float(m.get("condition", 100.0))
    rate = _decay_pct_per_day(ship)
    if rate <= 0:
        return cond
    anchor = _parse_anchor(m.get("last_maintenance"))
    if anchor is None:
        return cond
    now = now or datetime.now(timezone.utc)
    elapsed_days = max(0.0, (now - anchor).total_seconds() / 86400.0)
    return max(0.0, cond - rate * elapsed_days)


def combat_multiplier(ship: Ship) -> float:
    """Combat-effectiveness multiplier from the current band (floored at 0.1)."""
    if ship is None:
        return 1.0
    band = maintenance_band(effective_condition(ship))
    return max(0.1, 1.0 + band["combat"])


def apply_maintenance_decay(ship: Ship) -> float:
    """Persist lazy decay into ship.maintenance; returns the new condition.

    Mirrors apply_population_growth: only advance the anchor once a measurable
    amount (>=0.01) has decayed, otherwise bank the sub-threshold remainder so
    frequent reads can't round decay away to zero.
    """
    m = dict(ship.maintenance or {})
    cond = float(m.get("condition", 100.0))
    rate = _decay_pct_per_day(ship)
    if rate <= 0:
        return cond
    now = datetime.now(timezone.utc)
    anchor = _parse_anchor(m.get("last_maintenance"))
    if anchor is None:
        m["condition"] = cond
        m["last_maintenance"] = now.isoformat()
        ship.maintenance = m
        flag_modified(ship, "maintenance")
        return cond
    elapsed_days = max(0.0, (now - anchor).total_seconds() / 86400.0)
    lost = rate * elapsed_days
    if lost < 0.01:
        return cond
    new_cond = max(0.0, round(cond - lost, 2))
    m["condition"] = new_cond
    m["last_maintenance"] = now.isoformat()
    m["repair_needed"] = new_cond < 75.0
    ship.maintenance = m
    flag_modified(ship, "maintenance")
    return new_cond
