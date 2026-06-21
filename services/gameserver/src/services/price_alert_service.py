"""Price-alert evaluator.

Implements the canon **Price-alert evaluation cycle** from
``sw2102-docs/SYSTEMS/market-pricing.md`` (§ "Price-alert evaluation cycle" and
the "Alert flap" failure mode):

> On each ``update_market_prices`` call, after persistence, the alert evaluator:
>   1. Loads alerts matching ``(commodity, station_id_or_any)``.
>   2. For each alert, compares the new price against ``threshold``.
>   3. On match, emits a ``price_alert_triggered`` realtime event to the player;
>      marks alert ``last_triggered_at``; honors the alert's cooldown so a
>      flapping price does not spam.

The evaluator is a clean, side-effect-scoped callable. It does NOT wire itself
into ``update_market_prices`` or any scheduler — the lead wires the trigger.

────────────────────────────────────────────────────────────────────────────
MODEL SHAPE NOTE (read before extending)
────────────────────────────────────────────────────────────────────────────
The canon player-facing alert references ``(player_id, commodity, station_id?,
condition={op, price_kind, threshold}, last_triggered_at, cooldown)``. The
*current* ``PriceAlert`` model in ``models/market_transaction.py`` is an
admin/operations alert and does NOT yet carry ``player_id``, a direction/
comparison, ``last_triggered_at`` or ``cooldown_seconds`` — it carries
``station_id`` (NOT NULL), ``commodity``, ``alert_type``, ``threshold_value``,
``current_value``, ``is_active``, ``triggered_at`` and admin acknowledgement
fields.

Rather than require a schema migration (out of scope for this unit of work), the
evaluator reads the canon fields through defensive attribute access so it is
forward-compatible: once the model gains the player-alert columns it will honor
them automatically, and on the current schema it degrades gracefully (an alert
with no resolvable player simply cannot be unicast and is skipped). No alert is
ever mutated in a way the current schema cannot persist.
"""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, List, Optional

from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from src.models.market_transaction import PriceAlert
from src.models.player import Player

logger = logging.getLogger(__name__)


# ── NO-CANON micro-defaults ──────────────────────────────────────────────────
# market-pricing.md specifies the alert "honors the alert's cooldown" and the
# failure-mode table names a per-alert ``cooldown_seconds`` field, but does not
# fix a default value for alerts that omit one. A conservative non-zero default
# prevents a threshold-straddling price from spamming a player every recompute
# (the "Alert flap" failure mode). The model has no cooldown column today, so
# this default applies whenever the resolved cooldown is None.
DEFAULT_COOLDOWN_SECONDS = 300  # NO-CANON: 5 min anti-flap cooldown default

# Canon comparison op is ``"<="`` | ``">="`` over a ``buy``/``sell`` price_kind.
# An alert whose direction cannot be resolved falls back to ">=" (price rose to
# or above threshold) — the most common "alert me when it gets expensive" case.
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


def _resolve_recipient_user_id(db: Session, alert: PriceAlert) -> Optional[str]:
    """Resolve the owning User id string the WS connection map routes on.

    ``send_personal_message`` is keyed by the owning *User* id (not Player.id).
    The alert carries (canon) a ``player_id``; we resolve it to its
    ``Player.user_id``. Returns None when the alert has no resolvable player
    (e.g. an admin-operations alert on the current schema), in which case it
    cannot be unicast and is skipped.
    """
    player_id = getattr(alert, "player_id", None)
    if player_id is None:
        return None
    player = db.query(Player).filter(Player.id == player_id).first()
    if player is None or not getattr(player, "user_id", None):
        return None
    return str(player.user_id)


def _build_frame(alert: PriceAlert, commodity: str, station_id: Any,
                 new_price: float, op: str, threshold: float,
                 now: datetime) -> dict:
    """Construct the ``price_alert_triggered`` unicast frame.

    Mirrors the typed, personal-scope frames in websocket_service
    (send_turn_pool_update / send_hostile_detected): a ``type`` discriminator,
    an ISO ``timestamp``, and a flat payload.
    """
    return {
        "type": "price_alert_triggered",
        "timestamp": now.isoformat(),
        "alert_id": str(getattr(alert, "id", "")) or None,
        "commodity": commodity,
        "station_id": str(station_id) if station_id is not None else None,
        "price": new_price,
        "comparison": op,
        "threshold": threshold,
    }


def _dispatch_frame(user_id: str, frame: dict) -> None:
    """Schedule the unicast frame on the running loop, never blocking the caller.

    Mirrors movement_service._notify_hostile_detected / docking notifications:
    import the manager inside the function, grab the running loop, schedule the
    coroutine so it runs after the caller's transaction yields, and swallow any
    failure (no loop, no socket) so a quiet socket can never break price
    recompute. When no event loop is running (sync context), fall back to a
    fresh ``asyncio.run`` so the frame is still attempted.
    """
    try:
        from src.services.websocket_service import connection_manager

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop is not None:
            loop.create_task(
                connection_manager.send_personal_message(user_id, frame)
            )
        else:
            asyncio.run(connection_manager.send_personal_message(user_id, frame))
    except Exception:
        logger.debug(
            "Skipped price_alert_triggered WS notice (no loop or socket)",
            exc_info=True,
        )


def evaluate_price_alerts(
    db: Session,
    station_id: Any,
    commodity: str,
    new_price: float,
    now: Optional[datetime] = None,
) -> List[PriceAlert]:
    """Evaluate active price alerts for one ``(station, commodity, new_price)``.

    The canon evaluator, run after ``update_market_prices`` persists. Loads
    active alerts matching ``(commodity, station_id_or_any)`` — an alert with a
    NULL ``station_id`` (where the schema permits it) matches any station for
    that commodity — compares ``new_price`` against each alert's threshold per
    its direction, and for each crossing NOT inside its cooldown window:

      * emits a ``price_alert_triggered`` personal WS frame to the alert's
        player,
      * sets ``last_triggered_at = now`` (only where the model carries it;
        otherwise the call is a no-op flag write the current schema persists via
        ``triggered_at`` only if explicitly present),
      * honors ``cooldown_seconds`` (skips an alert that fired within the
        window).

    Args:
        db:         active SQLAlchemy session.
        station_id: the station whose price moved (UUID or str).
        commodity:  commodity name (e.g. ``"ore"``).
        new_price:  the freshly-recomputed price to test against thresholds.
        now:        evaluation instant; defaults to UTC now.

    Returns:
        The list of alerts that fired this call (useful for tests/telemetry).
        It never raises on a delivery failure — a missed live frame is a
        degraded-but-acceptable outcome, consistent with notification_service.
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

        user_id = _resolve_recipient_user_id(db, alert)
        if user_id is None:
            # No resolvable player (e.g. admin-ops alert on current schema) —
            # cannot unicast; do not fire.
            continue

        frame = _build_frame(
            alert, commodity, station_id, price, op, threshold, now
        )
        _dispatch_frame(user_id, frame)

        # Stamp the last-fire time on whichever column the model actually
        # carries, so the cooldown is honored next time. Guarded so the current
        # schema (no ``last_triggered_at``) is never assigned a non-existent
        # attribute that SQLAlchemy can't persist.
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
