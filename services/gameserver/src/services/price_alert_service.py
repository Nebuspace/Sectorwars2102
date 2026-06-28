"""Price-alert evaluator — ops/admin surface only.

``PriceAlert`` is an operations model (``station_id`` NOT NULL,
``alert_type``, ``threshold_value``, ``current_value``, ``is_active``,
``triggered_at``, acknowledgement fields). It has no ``player_id`` and is not
a player-facing notification channel.

The evaluator:
  1. Loads active alerts matching ``(commodity, station_id_or_any)``.
  2. For each alert, compares the current price against ``threshold_value``
     using the alert's direction (``alert_type`` heuristic → ``<=`` / ``>=``).
  3. On a crossing NOT inside the cooldown window: stamps
     ``last_triggered_at`` (where the model carries it) and records the alert
     as fired — returning it to the caller for ops handling (logging,
     auto-resolve, acknowledgement).

No player notification is emitted. The evaluator is a clean, side-effect-
scoped callable; it does not wire itself into any scheduler or route.
"""

import logging
from datetime import datetime, timezone
from typing import Any, List, Optional

from sqlalchemy import or_
from sqlalchemy.orm import Session

from src.models.market_transaction import PriceAlert

logger = logging.getLogger(__name__)


# ── NO-CANON micro-defaults ──────────────────────────────────────────────────
# market-pricing.md specifies the alert "honors the alert's cooldown" and the
# failure-mode table names a per-alert ``cooldown_seconds`` field, but does not
# fix a default value for alerts that omit one. A conservative non-zero default
# prevents a threshold-straddling price from retriggering on every recompute
# (the "Alert flap" failure mode). The model has no cooldown column today, so
# this default applies whenever the resolved cooldown is None.
DEFAULT_COOLDOWN_SECONDS = 300  # NO-CANON: 5 min anti-flap cooldown default

# An alert whose direction cannot be resolved from alert_type falls back to
# ">=" (price rose to/above threshold) — the most common ops-alert case.
_DEFAULT_OP = ">="  # NO-CANON: default comparison when alert omits a direction


def _aware(dt: Optional[datetime]) -> Optional[datetime]:
    """Coerce a possibly-naive datetime to UTC-aware for safe subtraction."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _resolve_threshold(alert: PriceAlert) -> Optional[float]:
    """The price level the alert watches.

    Canon names the field ``threshold``; the current model column is
    ``threshold_value``. Prefer the canon name if present, fall back to the
    real column.
    """
    raw = getattr(alert, "threshold", None)
    if raw is None:
        raw = getattr(alert, "threshold_value", None)
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _resolve_op(alert: PriceAlert) -> str:
    """The canon comparison operator: ``"<="`` (dropped to/below) or ``">="``.

    Reads, in order: an explicit ``comparison``/``direction`` attribute, then
    the admin ``alert_type`` heuristic (``*drop*``/``*low*`` ⇒ ``<=``,
    ``*spike*``/``*high*`` ⇒ ``>=``), else the conservative default.
    """
    for attr in ("comparison", "direction", "op"):
        val = getattr(alert, attr, None)
        if isinstance(val, str):
            v = val.strip().lower()
            if v in ("<=", "lte", "below", "drop", "down", "le"):
                return "<="
            if v in (">=", "gte", "above", "spike", "rise", "up", "ge"):
                return ">="
    alert_type = (getattr(alert, "alert_type", None) or "").lower()
    if any(k in alert_type for k in ("drop", "low", "below", "down")):
        return "<="
    if any(k in alert_type for k in ("spike", "high", "above", "up", "rise")):
        return ">="
    return _DEFAULT_OP


def _resolve_cooldown_seconds(alert: PriceAlert) -> int:
    """Per-alert cooldown in seconds, or the conservative default."""
    raw = getattr(alert, "cooldown_seconds", None)
    if raw is None:
        return DEFAULT_COOLDOWN_SECONDS
    try:
        seconds = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_COOLDOWN_SECONDS
    return seconds if seconds >= 0 else DEFAULT_COOLDOWN_SECONDS


def _resolve_last_triggered(alert: PriceAlert) -> Optional[datetime]:
    """When the alert last fired (canon ``last_triggered_at``).

    Falls back to the model's ``triggered_at`` only if a canon column is
    absent — but note ``triggered_at`` is server-defaulted at row creation, so
    it is used solely as a best-effort floor, never to suppress a first fire.
    """
    return _aware(getattr(alert, "last_triggered_at", None))


def _in_cooldown(alert: PriceAlert, now: datetime) -> bool:
    """True if the alert fired within its cooldown window and must be skipped."""
    last = _resolve_last_triggered(alert)
    if last is None:
        return False
    cooldown = _resolve_cooldown_seconds(alert)
    if cooldown <= 0:
        return False
    return (now - last).total_seconds() < cooldown


def _crosses(op: str, new_price: float, threshold: float) -> bool:
    """Canon comparison: ``>=`` price rose to/above, ``<=`` dropped to/below."""
    if op == "<=":
        return new_price <= threshold
    return new_price >= threshold


def evaluate_price_alerts(
    db: Session,
    station_id: Any,
    commodity: str,
    new_price: float,
    now: Optional[datetime] = None,
) -> List[PriceAlert]:
    """Evaluate active ops price alerts for one ``(station, commodity, new_price)``.

    Loads active alerts matching ``(commodity, station_id_or_any)`` — an alert
    with a NULL ``station_id`` (where the schema permits it) matches any station
    for that commodity — compares ``new_price`` against each alert's threshold
    per its direction (``alert_type`` heuristic), and for each crossing NOT
    inside its cooldown window:

      * stamps ``last_triggered_at = now`` (only where the model carries it),
      * appends the alert to the returned list for the caller to handle
        (ops logging, auto-resolve, acknowledgement).

    No player notification is emitted. PriceAlert is an ops/admin model.

    Args:
        db:         active SQLAlchemy session.
        station_id: the station whose price moved (UUID or str).
        commodity:  commodity name (e.g. ``"ore"``).
        new_price:  the freshly-recomputed price to test against thresholds.
        now:        evaluation instant; defaults to UTC now.

    Returns:
        The list of alerts that fired this call (useful for ops telemetry).
    """
    if now is None:
        now = datetime.now(timezone.utc)
    else:
        now = _aware(now) or datetime.now(timezone.utc)

    try:
        price = float(new_price)
    except (TypeError, ValueError):
        logger.debug("evaluate_price_alerts: non-numeric new_price=%r", new_price)
        return []

    # Active-flag column: canon ``active`` if present, else the model's
    # ``is_active``.
    active_col = getattr(PriceAlert, "active", None)
    if active_col is None:
        active_col = getattr(PriceAlert, "is_active", None)

    query = db.query(PriceAlert).filter(PriceAlert.commodity == commodity)
    if active_col is not None:
        query = query.filter(active_col.is_(True))

    # ``(station_id == this)`` OR ``(station_id IS NULL)`` (canon "any station"),
    # the latter only meaningful where the column is nullable.
    query = query.filter(
        or_(
            PriceAlert.station_id == station_id,
            PriceAlert.station_id.is_(None),
        )
    )

    alerts = query.all()
    fired: List[PriceAlert] = []

    for alert in alerts:
        threshold = _resolve_threshold(alert)
        if threshold is None:
            continue

        op = _resolve_op(alert)
        if not _crosses(op, price, threshold):
            continue

        if _in_cooldown(alert, now):
            continue

        # Stamp the last-fire time on whichever column the model carries so
        # the cooldown is honored next cycle. Guarded so the current schema
        # (no ``last_triggered_at``) is never assigned a column that doesn't
        # exist and can't be persisted by SQLAlchemy.
        if hasattr(alert, "last_triggered_at"):
            alert.last_triggered_at = now

        fired.append(alert)

    return fired


def sweep_price_alerts(
    db: Session,
    price_lookup,
    now: Optional[datetime] = None,
) -> List[PriceAlert]:
    """Periodic sweep variant of :func:`evaluate_price_alerts`.

    For schedulers that want to re-check every active alert against the latest
    price rather than reacting to a single recompute. ``price_lookup`` is a
    callable ``(station_id, commodity) -> Optional[float]`` the caller supplies
    (e.g. a lambda over ``MarketPrice``); alerts whose current price cannot be
    resolved are skipped. Returns the union of all alerts that fired.

    The lookup is injected (not hard-wired to ``MarketPrice``) so this evaluator
    stays a clean, dependency-light callable the lead can wire as it sees fit.
    """
    if now is None:
        now = datetime.now(timezone.utc)
    else:
        now = _aware(now) or datetime.now(timezone.utc)

    active_col = getattr(PriceAlert, "active", None)
    if active_col is None:
        active_col = getattr(PriceAlert, "is_active", None)

    query = db.query(PriceAlert)
    if active_col is not None:
        query = query.filter(active_col.is_(True))

    fired: List[PriceAlert] = []
    seen_ids = set()

    for alert in query.all():
        commodity = getattr(alert, "commodity", None)
        if not commodity:
            continue
        station_id = getattr(alert, "station_id", None)
        try:
            current = price_lookup(station_id, commodity)
        except Exception:
            logger.debug(
                "sweep_price_alerts: price_lookup failed for "
                "station=%s commodity=%s",
                station_id, commodity, exc_info=True,
            )
            current = None
        if current is None:
            continue

        # Reuse the single-alert path by evaluating just this alert's
        # (station, commodity, price). evaluate_price_alerts re-queries, so to
        # avoid double-firing across a sweep we de-dupe on alert id below.
        for hit in evaluate_price_alerts(db, station_id, commodity, current, now):
            hid = getattr(hit, "id", None)
            if hid not in seen_ids:
                seen_ids.add(hid)
                fired.append(hit)

    return fired
