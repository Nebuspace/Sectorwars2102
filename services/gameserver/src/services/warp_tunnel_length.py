"""Canonical WarpTunnel length → turn-cost bands (LEG-88 / movement.md).

Hop-units approximate Euclidean distance between endpoint sector 3D
coordinates. Generator/import time persists ``properties.length``,
``properties.traversal_cost``, and mirrors ``WarpTunnel.turn_cost``.

Bands (FEATURES/gameplay/movement.md § Long warp tunnels):
  ≤ 5 hop-units → 1 turn
  6–10          → 2 turns
  11+           → 3 turns (hard cap)

Ship speed must NEVER modify this persisted base cost.
"""
from __future__ import annotations

import math
from typing import Any, Mapping, MutableMapping, Optional, Tuple


def euclidean_hop_units(
    origin: Mapping[str, Any] | Any,
    destination: Mapping[str, Any] | Any,
) -> float:
    """Return Euclidean hop-units between two sector-like objects/dicts.

    Each endpoint must expose numeric ``x_coord``, ``y_coord``, ``z_coord``
    (Sector ORM columns or an equivalent mapping). Raises ``ValueError`` when
    any coordinate is missing or non-numeric — callers must not invent coords.
    """
    ox, oy, oz = _coords(origin, "origin")
    dx, dy, dz = _coords(destination, "destination")
    return math.sqrt((ox - dx) ** 2 + (oy - dy) ** 2 + (oz - dz) ** 2)


def turn_cost_from_length(length: float) -> int:
    """Map hop-unit length to the canonical 1/2/3 turn-cost band."""
    if length < 0 or math.isnan(length) or math.isinf(length):
        raise ValueError(f"invalid tunnel length: {length!r}")
    if length <= 5:
        return 1
    if length <= 10:
        return 2
    return 3


def natural_tunnel_cost_fields(
    origin: Mapping[str, Any] | Any,
    destination: Mapping[str, Any] | Any,
    *,
    base_properties: Optional[MutableMapping[str, Any]] = None,
) -> Tuple[float, int, dict]:
    """Compute ``(length, turn_cost, properties)`` for a NATURAL WarpTunnel.

    ``properties`` merges ``base_properties`` (when provided) and always sets
    ``length`` + ``traversal_cost`` to the canonical mirrored pair.
    """
    length = euclidean_hop_units(origin, destination)
    turn_cost = turn_cost_from_length(length)
    props: dict = dict(base_properties or {})
    props["length"] = float(length)
    props["traversal_cost"] = turn_cost
    return length, turn_cost, props


def _coords(endpoint: Mapping[str, Any] | Any, label: str) -> Tuple[float, float, float]:
    try:
        if isinstance(endpoint, Mapping):
            raw = (endpoint.get("x_coord"), endpoint.get("y_coord"), endpoint.get("z_coord"))
        else:
            raw = (
                getattr(endpoint, "x_coord", None),
                getattr(endpoint, "y_coord", None),
                getattr(endpoint, "z_coord", None),
            )
    except Exception as exc:  # pragma: no cover - defensive
        raise ValueError(f"{label} sector coordinates unavailable") from exc

    if any(v is None for v in raw):
        raise ValueError(f"{label} sector missing x_coord/y_coord/z_coord")
    try:
        return float(raw[0]), float(raw[1]), float(raw[2])
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} sector coordinates are not numeric: {raw!r}") from exc
