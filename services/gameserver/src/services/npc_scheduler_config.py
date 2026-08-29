"""NPC scheduler tuning — SYSTEMS/npc-scheduler.md § Configuration (LEG-78).

Resolves the five canon knobs through one typed path:

  cycle_hours_default
  off_duty_rotation_target_pct
  kia_cooldown_seconds
  engagement_no_response_grace_seconds
  route_engagement_max_distance_hops

Precedence (more specific wins): ``NPCRoster.config`` JSONB → environment
→ hard-coded defaults. Invalid values raise ``NpcSchedulerConfigError``
(observable); callers must not silently coerce.

Defaults preserve shipped *effective* behavior where the Configuration
section gives no replacement number:

- cycle: 4.0 h (Marshal pacing); Sentinel kinds default 3.0 h
- off-duty target: 0.20 (canon Loop C ~20%)
- KIA refill cooldown: 0 s (shipped fill has no gate; set env/roster for 7d)
- grace: unset → random [300, 900] s band (shipped 5–15 min)
- hop cap: unset → class table 5 / 8 / 3
"""

from __future__ import annotations

import logging
import os
import random
from dataclasses import dataclass
from typing import Any, Mapping, MutableMapping, Optional

logger = logging.getLogger(__name__)

CANON_KEYS: frozenset[str] = frozenset(
    {
        "cycle_hours_default",
        "off_duty_rotation_target_pct",
        "kia_cooldown_seconds",
        "engagement_no_response_grace_seconds",
        "route_engagement_max_distance_hops",
    }
)

ENV_KEYS: Mapping[str, str] = {
    "cycle_hours_default": "NPC_SCHEDULER_CYCLE_HOURS_DEFAULT",
    "off_duty_rotation_target_pct": "NPC_SCHEDULER_OFF_DUTY_ROTATION_TARGET_PCT",
    "kia_cooldown_seconds": "NPC_SCHEDULER_KIA_COOLDOWN_SECONDS",
    "engagement_no_response_grace_seconds": (
        "NPC_SCHEDULER_ENGAGEMENT_NO_RESPONSE_GRACE_SECONDS"
    ),
    "route_engagement_max_distance_hops": (
        "NPC_SCHEDULER_ROUTE_ENGAGEMENT_MAX_DISTANCE_HOPS"
    ),
}

# Role-keyed cycle hours matching PATROL_MINUTES_PER_SECTOR (minutes/60).
_ROLE_CYCLE_HOURS: Mapping[str, float] = {
    "federation_marshal": 4.0,
    "marshal_captain": 4.0,
    "pirate_captain": 4.0,
    "nexus_sentinel": 3.0,
    "sentinel_captain": 3.0,
}

DEFAULT_CYCLE_HOURS = 4.0
DEFAULT_OFF_DUTY_ROTATION_TARGET_PCT = 0.20
# Shipped Loop B refill has no predecessor-cooldown gate.
DEFAULT_KIA_COOLDOWN_SECONDS = 0

GRACE_MIN_SECONDS = 5 * 60
GRACE_MAX_SECONDS = 15 * 60

# ADR-0063 N-I1 / npc-scheduler.md class hop caps (when hop key unset).
MARSHAL_MAX_HOPS = 5
CAPTAIN_MAX_HOPS = 8
PIRATE_LORD_MAX_HOPS = 3


class NpcSchedulerConfigError(ValueError):
    """Invalid scheduler tuning value — fail closed, never coerce."""


@dataclass(frozen=True)
class NpcSchedulerTuning:
    cycle_hours_default: float
    off_duty_rotation_target_pct: float
    kia_cooldown_seconds: int
    # None ⇒ sample the shipped 5–15 min band at use site.
    engagement_no_response_grace_seconds: Optional[int]
    # None ⇒ use per-class hop table at use site.
    route_engagement_max_distance_hops: Optional[int]
    # key → "roster" | "env" | "default"
    sources: Mapping[str, str]


def _reject(key: str, raw: Any, reason: str) -> None:
    raise NpcSchedulerConfigError(
        f"invalid npc scheduler config {key}={raw!r}: {reason}"
    )


def _as_number(key: str, raw: Any) -> float:
    if isinstance(raw, bool):
        _reject(key, raw, "boolean not allowed")
    if isinstance(raw, (int, float)):
        return float(raw)
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            _reject(key, raw, "empty string")
        try:
            return float(text)
        except ValueError:
            _reject(key, raw, "not a number")
    _reject(key, raw, "expected number")


def parse_cycle_hours(raw: Any) -> float:
    value = _as_number("cycle_hours_default", raw)
    if value <= 0:
        _reject("cycle_hours_default", raw, "must be > 0")
    if value != value:  # NaN
        _reject("cycle_hours_default", raw, "NaN not allowed")
    return value


def parse_off_duty_pct(raw: Any) -> float:
    value = _as_number("off_duty_rotation_target_pct", raw)
    if value < 0.0 or value > 1.0:
        _reject(
            "off_duty_rotation_target_pct",
            raw,
            "must be in [0.0, 1.0] (fraction, not percent)",
        )
    return value


def parse_nonneg_int(key: str, raw: Any) -> int:
    value = _as_number(key, raw)
    if value != int(value):
        _reject(key, raw, "must be an integer (no silent truncation)")
    ivalue = int(value)
    if ivalue < 0:
        _reject(key, raw, "must be >= 0")
    return ivalue


def parse_positive_int(key: str, raw: Any) -> int:
    ivalue = parse_nonneg_int(key, raw)
    if ivalue <= 0:
        _reject(key, raw, "must be > 0")
    return ivalue


def validate_config_mapping(
    raw: Optional[Mapping[str, Any]],
    *,
    origin: str,
) -> dict[str, Any]:
    """Return only valid canon keys from ``raw``.

    Unknown keys are logged and skipped (roster JSONB may carry other
    admin metadata later). Invalid values for known keys raise.
    """
    if raw is None:
        return {}
    if not isinstance(raw, Mapping):
        raise NpcSchedulerConfigError(
            f"invalid npc scheduler config from {origin}: expected mapping, "
            f"got {type(raw).__name__}"
        )
    out: dict[str, Any] = {}
    for key, value in raw.items():
        if key not in CANON_KEYS:
            logger.warning(
                "Ignoring unknown npc scheduler config key %r from %s",
                key,
                origin,
            )
            continue
        if key == "cycle_hours_default":
            out[key] = parse_cycle_hours(value)
        elif key == "off_duty_rotation_target_pct":
            out[key] = parse_off_duty_pct(value)
        elif key == "kia_cooldown_seconds":
            out[key] = parse_nonneg_int(key, value)
        elif key == "engagement_no_response_grace_seconds":
            out[key] = parse_nonneg_int(key, value)
        elif key == "route_engagement_max_distance_hops":
            out[key] = parse_positive_int(key, value)
    return out


def _env_layer(environ: Mapping[str, str]) -> dict[str, Any]:
    raw: dict[str, Any] = {}
    for key, env_name in ENV_KEYS.items():
        if env_name in environ and environ[env_name] != "":
            raw[key] = environ[env_name]
    return validate_config_mapping(raw, origin="environment")


def default_cycle_hours_for_role(role: Optional[str]) -> float:
    if role and role in _ROLE_CYCLE_HOURS:
        return _ROLE_CYCLE_HOURS[role]
    return DEFAULT_CYCLE_HOURS


def resolve_npc_scheduler_tuning(
    *,
    roster_config: Optional[Mapping[str, Any]] = None,
    role: Optional[str] = None,
    environ: Optional[Mapping[str, str]] = None,
) -> NpcSchedulerTuning:
    """Resolve all five knobs for one faction/role context.

    ``role`` only affects the cycle-hours hard default (Sentinel 3h vs
    Marshal 4h). Env and roster layers are already role-scoped when the
    caller passes that roster's ``config``.
    """
    env = _env_layer(environ if environ is not None else os.environ)
    try:
        roster = validate_config_mapping(roster_config, origin="NPCRoster.config")
    except NpcSchedulerConfigError:
        logger.exception(
            "NPCRoster.config invalid — falling back to env/defaults "
            "(role=%s)",
            role,
        )
        roster = {}

    sources: MutableMapping[str, str] = {}
    merged: dict[str, Any] = {}

    def take(key: str, default: Any) -> Any:
        if key in roster:
            sources[key] = "roster"
            return roster[key]
        if key in env:
            sources[key] = "env"
            return env[key]
        sources[key] = "default"
        return default

    cycle = take("cycle_hours_default", default_cycle_hours_for_role(role))
    off_duty = take(
        "off_duty_rotation_target_pct", DEFAULT_OFF_DUTY_ROTATION_TARGET_PCT
    )
    kia = take("kia_cooldown_seconds", DEFAULT_KIA_COOLDOWN_SECONDS)
    # Optional knobs: absent from all layers ⇒ None (shipped band / class table).
    if "engagement_no_response_grace_seconds" in roster:
        grace: Optional[int] = roster["engagement_no_response_grace_seconds"]
        sources["engagement_no_response_grace_seconds"] = "roster"
    elif "engagement_no_response_grace_seconds" in env:
        grace = env["engagement_no_response_grace_seconds"]
        sources["engagement_no_response_grace_seconds"] = "env"
    else:
        grace = None
        sources["engagement_no_response_grace_seconds"] = "default"

    if "route_engagement_max_distance_hops" in roster:
        hops: Optional[int] = roster["route_engagement_max_distance_hops"]
        sources["route_engagement_max_distance_hops"] = "roster"
    elif "route_engagement_max_distance_hops" in env:
        hops = env["route_engagement_max_distance_hops"]
        sources["route_engagement_max_distance_hops"] = "env"
    else:
        hops = None
        sources["route_engagement_max_distance_hops"] = "default"

    merged.update(
        {
            "cycle_hours_default": cycle,
            "off_duty_rotation_target_pct": off_duty,
            "kia_cooldown_seconds": kia,
            "engagement_no_response_grace_seconds": grace,
            "route_engagement_max_distance_hops": hops,
        }
    )
    return NpcSchedulerTuning(
        cycle_hours_default=float(cycle),
        off_duty_rotation_target_pct=float(off_duty),
        kia_cooldown_seconds=int(kia),
        engagement_no_response_grace_seconds=grace,
        route_engagement_max_distance_hops=hops,
        sources=dict(sources),
    )


def sample_grace_seconds(
    tuning: NpcSchedulerTuning,
    *,
    rng: Optional[random.Random] = None,
) -> int:
    """Seconds until short-handed engagement response; preserves 5–15 min band."""
    if tuning.engagement_no_response_grace_seconds is not None:
        return int(tuning.engagement_no_response_grace_seconds)
    picker = rng if rng is not None else random
    return int(picker.randint(GRACE_MIN_SECONDS, GRACE_MAX_SECONDS))


def cycle_minutes(tuning: NpcSchedulerTuning) -> int:
    """Patrol minutes-per-sector derived from resolved cycle hours."""
    return max(1, int(round(tuning.cycle_hours_default * 60.0)))


def hop_cap_or_default(
    tuning: NpcSchedulerTuning,
    class_default: int,
) -> int:
    if tuning.route_engagement_max_distance_hops is not None:
        return int(tuning.route_engagement_max_distance_hops)
    return int(class_default)


def class_hop_default_for_titles(*, title: Optional[str]) -> int:
    """Mirror ``npc_engagement_service.hop_cap_for_npc`` class defaults."""
    if (title or "") == "Pirate Lord":
        return PIRATE_LORD_MAX_HOPS
    if "Captain" in (title or ""):
        return CAPTAIN_MAX_HOPS
    return MARSHAL_MAX_HOPS
