"""Intra-system celestial %-space layout — server-side port of the player-
client's windshieldTableauLayout.ts (WO-TRANCHE-0716 ISP-PARITY).

DETERMINISM / PARITY CONTRACT
==============================
This module exists so a server-generated NPC leg can target the SAME %-space
coordinate the player's own windshield actually renders a planet/station at.
Two things must match the client bit-for-bit / value-for-value:

1. The star anchor's seeded (x_pct, y_pct) — derived from a SplitMix32 stream
   seeded by fnv1a32("windshield-tableau:star:{sector_id}"), taking TWO
   sequential draws off that ONE stream (x then y). This is the exact
   algorithm services/player-client/src/vista/core/rng.ts's SeededRng +
   deriveChildSeed implement, and windshieldTableauLayout.ts's own
   starAnchor() calls with the same seed string. (A prior server-side
   attempt at this, since replaced, derived x/y from two INDEPENDENT
   `:x`/`:y`-suffixed hashes instead of one continuing stream — a different
   number sequence than the client. Fixed here.)
2. safeOrbitRadii + orbitalPosition's T1-A "safe box" math — the client no
   longer uses the old symmetric AU_SEMI_X_PCT/AU_SEMI_Y_PCT ellipse alone;
   it derives four independent per-direction radii from the star's own
   off-center anchor plus the ACTUAL measured `.ssv-tableau` band box, then
   fans bodies out primarily by orbit_au (X) with phase_deg as a secondary
   wiggle, and phase-dominant on Y — see windshieldTableauLayout.ts's own
   orbitalPosition doc-comment for the full T0-1/T1-A history. The server
   has no DOM to measure, so it uses the FIXED reference band literal
   WindshieldTableau.test.tsx's own FLIGHT_BAND constant asserts against
   (1440 x 334.7px, remPx 18.09 — the real resolved geometry at the
   1440x900 reference cockpit resolution, cockpit-shell.css's flight-mode
   `.band` at rest). NOTE: intrasystem_movement_service.py's own
   REF_BAND_H=335.0 (used only for heading_deg's default band aspect) is a
   0.3px/0.09% rounding of this same 334.7 value — an existing, immaterial
   divergence from a different lane, left alone here.

Everything else windshieldTableauLayout.ts exports (moon orbits, hazard
arcs, warp-arrival obstacle avoidance, cosmetic "other ship" flight poses)
is rendering-only and has no server-side leg-targeting analog — not ported.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

# ---------------------------------------------------------------------------
# SplitMix32 — byte-for-byte port of vista/core/rng.ts's SeededRng.nextU32.
# NOT the same algorithm as celestial_service.py's SplitMix64 (that PRNG
# generates the composition itself: orbit_au/phase_deg/kind/etc; this one
# only ever seeds the star anchor's cosmetic screen position).
# ---------------------------------------------------------------------------

_MASK32 = 0xFFFFFFFF
_GOLDEN_GAMMA_32 = 0x9E3779B9


def fnv1a32(s: str) -> int:
    """FNV-1a 32-bit hash — matches rng.ts's fnv1a32 (ASCII-only seed
    strings here, so UTF-8 byte iteration == JS charCodeAt iteration)."""
    h = 0x811C9DC5
    for ch in s.encode("utf-8"):
        h ^= ch
        h = (h * 0x01000193) & _MASK32
    return h


def derive_child_seed(parent_seed: str, child_name: str) -> int:
    return fnv1a32(f"{parent_seed}:{child_name}")


class SeededRng:
    """Stateful SplitMix32 stream — same seed, same draw ORDER -> same
    sequence, matching rng.ts's SeededRng class (not a one-shot hash)."""

    def __init__(self, seed: int) -> None:
        self._s = seed & _MASK32

    def next01(self) -> float:
        self._s = (self._s + _GOLDEN_GAMMA_32) & _MASK32
        t = self._s ^ (self._s >> 16)
        t = (t * 0x21F0AAAD) & _MASK32
        t = t ^ (t >> 15)
        t = (t * 0x735A2D97) & _MASK32
        t = (t ^ (t >> 15)) & _MASK32
        return t / 4294967296.0


NS = "windshield-tableau"

# celestial_service.py's own composition contract (_make_body / extra
# bodies): orbit_au always in [0.2, 0.95] — same ceiling/floor
# windshieldTableauLayout.ts's ORBIT_AU_MIN/MAX cite.
ORBIT_AU_MIN = 0.2
ORBIT_AU_MAX = 0.95

# windshieldTableauLayout.ts's X_SECONDARY_WIGGLE_FRACTION — Max's ruling:
# "primarily vertical + secondary horizontal, but must NEVER zero out the
# horizontal spread."
X_SECONDARY_WIGGLE_FRACTION = 0.15

# QUEUE-XPCT-SATURATION-STACK: byte-equivalent port of windshieldTableauLayout
# .ts's own X_WIGGLE_TAPER_START_ORBIT_T/xWiggleTaper — see that file's own
# doc-comment for the full saturation-bug analysis and monotonicity proof.
# Summary: the primary X term alone reaches safe_radii.x_max_pct EXACTLY at
# orbit_t=1; the wiggle term used to add MORE on top of that unconditionally,
# so at cos(phase)=1 every orbit_t >= 1-X_SECONDARY_WIGGLE_FRACTION (~0.85,
# i.e. orbit_au >= ~0.8375) overshot and collapsed onto the identical
# hard-clamped pixel (repro: orbit_au 0.85 and 0.95 -> the same x_pct). The
# taper below ramps the wiggle's own contribution to zero by orbit_t=1 so the
# sum can never overshoot in the first place -- the final hard clamp becomes
# a provable no-op for the upper bound instead of doing the (lossy) work.
X_WIGGLE_TAPER_START_ORBIT_T = 0.84


def _x_wiggle_taper(orbit_t: float) -> float:
    if orbit_t <= X_WIGGLE_TAPER_START_ORBIT_T:
        return 1.0
    return (1.0 - orbit_t) / (1.0 - X_WIGGLE_TAPER_START_ORBIT_T)

# The real resolved `.ssv-tableau` band geometry at the reference cockpit
# resolution (1440x900) — WindshieldTableau.test.tsx's own FLIGHT_BAND
# literal (widthPx/heightPx/remPx), reused verbatim here since the server
# has no DOM to measure.
LAYOUT_BAND_WIDTH_PX = 1440.0
LAYOUT_BAND_HEIGHT_PX = 334.7
LAYOUT_BAND_REM_PX = 18.09

# WindshieldTableau.tsx's PLANET_FOOTPRINT_EM_MAX / STATION_FOOTPRINT_EM_*.
BODY_SIZE_EM_MAX = 2.4
PLANET_FOOTPRINT_EM_MAX = BODY_SIZE_EM_MAX + 0.2
STATION_FOOTPRINT_EM_WIDTH_MAX = 20.0
STATION_FOOTPRINT_EM_HEIGHT_MAX = 5.0


@dataclass(frozen=True)
class StarAnchor:
    x_pct: float
    y_pct: float


@dataclass(frozen=True)
class SafeOrbitRadii:
    left_pct_per_au: float
    right_pct_per_au: float
    up_pct_per_au: float
    down_pct_per_au: float
    x_min_pct: float
    x_max_pct: float
    y_min_pct: float
    y_max_pct: float


def star_anchor(sector_id: int) -> StarAnchor:
    """windshieldTableauLayout.ts's starAnchor() position math (its sizeEm
    is a rendering-only concern, not ported — leg targeting never needs the
    star's own drawn size). ONE seeded stream, TWO sequential draws (x then
    y) — order matters, matches the client exactly."""
    rng = SeededRng(derive_child_seed(NS, f"star:{sector_id}"))
    x_pct = 9.0 + rng.next01() * 5.0  # ~9-14%
    y_pct = 42.0 + rng.next01() * 8.0  # ~42-50%
    return StarAnchor(x_pct=x_pct, y_pct=y_pct)


def safe_orbit_radii(
    star: StarAnchor,
    band_width_px: float,
    band_height_px: float,
    rem_px: float,
    max_object_em_width: float,
    max_object_em_height: Optional[float] = None,
) -> SafeOrbitRadii:
    """Direct port of windshieldTableauLayout.ts's safeOrbitRadii — see that
    function's own doc-comment for the full T1-A rationale (four independent
    per-direction radii off the star's off-center anchor, sized so a real
    body/station's own rendered footprint never lands outside [0,100]% on
    either axis)."""
    if max_object_em_height is None:
        max_object_em_height = max_object_em_width
    half_width_px = (max_object_em_width / 2.0) * rem_px
    half_height_px = (max_object_em_height / 2.0) * rem_px
    margin_x_pct = (half_width_px / band_width_px) * 100.0 if band_width_px > 0 else 100.0
    margin_y_pct = (half_height_px / band_height_px) * 100.0 if band_height_px > 0 else 100.0

    def room(room_pct: float) -> float:
        return max(0.0, room_pct) / ORBIT_AU_MAX

    # Clamp margins that exceed half the box so xMin<=xMax/yMin<=yMax always
    # holds (mirrors the client's own safeMarginX/Y clamp).
    safe_margin_x = min(margin_x_pct, 50.0)
    safe_margin_y = min(margin_y_pct, 50.0)
    return SafeOrbitRadii(
        left_pct_per_au=room(star.x_pct - margin_x_pct),
        right_pct_per_au=room(100.0 - margin_x_pct - star.x_pct),
        up_pct_per_au=room(star.y_pct - margin_y_pct),
        down_pct_per_au=room(100.0 - margin_y_pct - star.y_pct),
        x_min_pct=safe_margin_x,
        x_max_pct=100.0 - safe_margin_x,
        y_min_pct=safe_margin_y,
        y_max_pct=100.0 - safe_margin_y,
    )


def orbital_position(star: StarAnchor, orbit_au: float, phase_deg: float, safe_radii: SafeOrbitRadii) -> tuple:
    """Direct port of windshieldTableauLayout.ts's orbitalPosition() WITH a
    safeRadii box (the client's post-T1-A path — the only path this server
    module implements; the client's own DOM-free pre-measurement fallback
    branch has no server analog since LAYOUT_BAND_* is a fixed constant,
    never "not yet measured"). Returns (x_pct, y_pct).

    NaN-safety: `orbit_au`/`phase_deg` are the only externally-sourced
    inputs here (e.g. a malformed JSONB body/station entry) — everything
    else this function touches (star, safe_radii, the module constants) is
    always finite by construction. A NaN is coerced to 0.0 up front rather
    than left to propagate, because Python's min/max are ORDER-dependent on
    NaN (`max(nan, x) is nan` but `max(x, nan) is x`), so a caller that
    happens to pass args in the "safe" order today isn't a real guarantee —
    this codebase has a documented NaN-blind burn history (vista pipeline
    invariants). The final clamps below also use the NaN-safe
    `max(lo, min(hi, v))` ordering (finite bound first) as defense-in-depth,
    so this stays safe even if a future edit removes the upfront guard."""
    if math.isnan(orbit_au):
        orbit_au = 0.0
    if math.isnan(phase_deg):
        phase_deg = 0.0
    rad = math.radians(phase_deg)
    cos_v = math.cos(rad)
    sin_v = math.sin(rad)
    au = max(ORBIT_AU_MIN, min(ORBIT_AU_MAX, abs(orbit_au)))
    orbit_t = (au - ORBIT_AU_MIN) / (ORBIT_AU_MAX - ORBIT_AU_MIN)
    x_spread_pct = safe_radii.right_pct_per_au * ORBIT_AU_MAX
    # QUEUE-XPCT-SATURATION-STACK: taper the wiggle to zero near orbit_t=1 so
    # primary+wiggle can never overshoot x_max_pct -- see
    # X_WIGGLE_TAPER_START_ORBIT_T's own doc-comment for the saturation bug
    # this closes and the monotonicity proof (byte-equivalent to the client's
    # xWiggleTaper, same operation order).
    x_wiggle_pct = ((cos_v + 1.0) / 2.0) * x_spread_pct * X_SECONDARY_WIGGLE_FRACTION * _x_wiggle_taper(orbit_t)
    ry = au * (safe_radii.down_pct_per_au if sin_v >= 0 else safe_radii.up_pct_per_au)
    x_pct = max(safe_radii.x_min_pct, min(safe_radii.x_max_pct, star.x_pct + orbit_t * x_spread_pct + x_wiggle_pct))
    y_pct = max(safe_radii.y_min_pct, min(safe_radii.y_max_pct, star.y_pct + sin_v * ry))
    return (x_pct, y_pct)


class SectorLayout:
    """One sector's star anchor + its two safe-radii sets (planet vs
    station — the client computes these SEPARATELY, see
    STATION_FOOTPRINT_EM_WIDTH_MAX's own doc-comment: a station's rendered
    footprint grows with its name length so needs a much wider margin than
    a fixed-size planet disc), computed once and reused for every
    body/station in the system — mirrors WindshieldTableau.tsx's own
    safeRadiiPlanets/safeRadiiStations useMemo pair."""

    __slots__ = ("star", "planet_radii", "station_radii")

    def __init__(self, sector_id: int) -> None:
        self.star = star_anchor(sector_id)
        self.planet_radii = safe_orbit_radii(
            self.star, LAYOUT_BAND_WIDTH_PX, LAYOUT_BAND_HEIGHT_PX, LAYOUT_BAND_REM_PX,
            PLANET_FOOTPRINT_EM_MAX,
        )
        self.station_radii = safe_orbit_radii(
            self.star, LAYOUT_BAND_WIDTH_PX, LAYOUT_BAND_HEIGHT_PX, LAYOUT_BAND_REM_PX,
            STATION_FOOTPRINT_EM_WIDTH_MAX, STATION_FOOTPRINT_EM_HEIGHT_MAX,
        )

    def planet_position(self, orbit_au: float, phase_deg: float) -> tuple:
        return orbital_position(self.star, orbit_au, phase_deg, self.planet_radii)

    def station_position(self, orbit_au: float, phase_deg: float) -> tuple:
        return orbital_position(self.star, orbit_au, phase_deg, self.station_radii)
