"""Deterministic planet-name generator (ADR-0073).

Draws a base name from the >=500-name corpus and, with weighted probability,
attaches a prefix and/or suffix to form compound variations ("New Eden",
"Zeta Kepler Prime"). Deterministic: same seed -> same name, so worldgen and
procedural display bodies agree and names are stable across requests.

Uses celestial_service's SplitMix64 so the PRNG stream matches the rest of the
celestial pipeline (trivially seedable, cross-platform stable).
"""

from src.services.celestial_service import SplitMix64, _MASK64
from src.data.planet_names import (
    PLANET_BASE_NAMES,
    PLANET_PREFIXES,
    PLANET_SUFFIXES,
)

# Probability a generated name gets a prefix / a suffix (independent rolls).
_PREFIX_CHANCE = 0.22
_SUFFIX_CHANCE = 0.30


def generate_planet_name(seed: int) -> str:
    """A deterministic compound planet name from an integer seed."""
    rng = SplitMix64(seed & _MASK64)
    base = PLANET_BASE_NAMES[rng.next_u64() % len(PLANET_BASE_NAMES)]
    parts = [base]
    if rng.random() < _PREFIX_CHANCE:
        parts.insert(0, PLANET_PREFIXES[rng.next_u64() % len(PLANET_PREFIXES)])
    if rng.random() < _SUFFIX_CHANCE:
        parts.append(PLANET_SUFFIXES[rng.next_u64() % len(PLANET_SUFFIXES)])
    return " ".join(parts)


def _fold_uuid(value) -> int:
    """Fold a UUID (or its string form) to a stable 64-bit int — same scheme
    celestial_service uses for per-planet sub-seeds."""
    uuid_int = value.int if hasattr(value, "int") else int(str(value).replace("-", ""), 16)
    return ((uuid_int >> 64) ^ uuid_int) & _MASK64


def name_for_planet(planet) -> str:
    """Deterministic auto-name for a real Planet row (seeded by its id)."""
    return generate_planet_name(_fold_uuid(planet.id))


def name_for_body(sector_id: int, slot: int) -> str:
    """Deterministic name for a composer-only (non-real) display body, replacing
    the old '<sector>-<letter>' designation."""
    return generate_planet_name((sector_id * 100003 + slot * 9176 + 7) & _MASK64)
