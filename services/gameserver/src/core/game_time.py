"""
Game-time scaling for duration-based systems.

Canon durations (construction days, grace windows, slip tenures, takeover
months) stay canonical in code; GAME_TIME_SCALE compresses wall-clock time
so they can be exercised on dev. Production runs at the default 1.0.

  GAME_TIME_SCALE=144  →  1 canonical day elapses in 10 wall-clock minutes.

Every new duration computation in the docking / construction / ownership
systems must go through these helpers — never raw datetime arithmetic —
so the scale applies uniformly.
"""
import os
from datetime import datetime, timedelta, UTC


def _read_scale() -> float:
    raw = os.environ.get("GAME_TIME_SCALE", "1.0")
    try:
        scale = float(raw)
    except (TypeError, ValueError):
        return 1.0
    # Guard against zero/negative misconfiguration freezing all timers
    return scale if scale > 0 else 1.0


GAME_TIME_SCALE = _read_scale()


def _aware(dt: datetime) -> datetime:
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt


def scaled_elapsed(since: datetime, now: datetime | None = None) -> timedelta:
    """Wall-clock time since `since`, stretched by GAME_TIME_SCALE.

    Compare the result against CANONICAL durations:
        if scaled_elapsed(slip.docked_at) >= timedelta(hours=4): ...
    """
    now = now or datetime.now(UTC)
    return (now - _aware(since)) * GAME_TIME_SCALE


def scaled_deadline(hours: float, start: datetime | None = None) -> datetime:
    """Absolute wall-clock deadline for a CANONICAL duration in hours.

    A 48-hour canonical window at scale 144 yields a deadline 20 wall-clock
    minutes from `start`.
    """
    start = _aware(start) if start else datetime.now(UTC)
    return start + timedelta(hours=hours / GAME_TIME_SCALE)


def canonical_hours_since(since: datetime, now: datetime | None = None) -> float:
    """How many CANONICAL hours have elapsed since `since` (scaled)."""
    return scaled_elapsed(since, now).total_seconds() / 3600.0
