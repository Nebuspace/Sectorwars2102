"""Authoritative intra-system pose — burn legs, halt, derive-at-time.

Canon: sector presence is who-is-in-sector; this module owns x/y/heading
*inside* a sector. REST commits legs; WS fans out plans; clients interpolate
with the same profile timings as WindshieldTableau TRAVEL_* / OTHER_FLIGHT_*.

Burn cost: FREE (0 turns) — Max ratified 2026-07-16.
Empty-space Travel To: allowed in v1.
"""
from __future__ import annotations

import hashlib
import logging
import math
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from src.services.intrasystem_layout import ORBIT_AU_MAX, ORBIT_AU_MIN, SectorLayout

logger = logging.getLogger(__name__)

# Lockstep with player-client windshieldTableauLayout OTHER_FLIGHT_* /
# WindshieldTableau TRAVEL_* (6.4s move window).
ORIENT_MS = 1000
ACCEL_MS = 1800
COAST_MS = 1100
FLIP_MS = 1300
DECEL_MS = 2200
SETTLE_MS = 800
MOVE_MS = ACCEL_MS + COAST_MS + FLIP_MS + DECEL_MS
HALT_FLIP_MS = 1800
HALT_BRAKE_MS = 1600

PROFILE_MS = {
    "orient_ms": ORIENT_MS,
    "accel_ms": ACCEL_MS,
    "coast_ms": COAST_MS,
    "flip_ms": FLIP_MS,
    "decel_ms": DECEL_MS,
    "settle_ms": SETTLE_MS,
    "move_ms": MOVE_MS,
    "halt_flip_ms": HALT_FLIP_MS,
    "halt_brake_ms": HALT_BRAKE_MS,
}

# Fixed reference band for %-math authority (Max: Implementer default).
REF_BAND_W = 1440.0
REF_BAND_H = 335.0
REF_BAND_ASPECT = REF_BAND_H / REF_BAND_W  # ~0.2326


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


def _parse_iso(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def _smoothstep(t: float) -> float:
    x = _clamp(t, 0.0, 1.0)
    return x * x * (3.0 - 2.0 * x)


def _shortest_delta(from_deg: float, to_deg: float) -> float:
    return ((to_deg - from_deg + 540.0) % 360.0) - 180.0


def _normalize_heading_deg(deg: float) -> float:
    """Canonical [0,360) heading. EVERY heading_deg write site in this
    module runs through this (Max's live fly-by: a completed leg's arrival
    heading was observed at 531deg -- `face = prograde + 360.0`'s own
    doc-comment used to compute a genuinely correct "keep spinning past
    360 for a continuous CSS rotation" value, but that's a client-side
    ANIMATION convenience, not what a persisted/API heading should store
    -- accumulating unboundedly across legs since each new leg's `parked`
    base reads the PREVIOUS leg's already-out-of-range stored value).
    Python's `%` follows the divisor's sign for floats, so a negative
    atan2() output (e.g. -45) also normalizes correctly to 315, not just
    positive overflow like 531 -> 171."""
    return deg % 360.0


def heading_deg(x0: float, y0: float, x1: float, y1: float, band_aspect: float = REF_BAND_ASPECT) -> float:
    dx = x1 - x0
    dy = (y1 - y0) * band_aspect
    if abs(dx) < 1e-9 and abs(dy) < 1e-9:
        return 0.0
    return _normalize_heading_deg(math.degrees(math.atan2(dy, dx)))


def resting_anchor(sector_id: int, ship_key: str) -> Dict[str, float]:
    """Deterministic parked spawn when no pose exists yet."""
    h = hashlib.sha256(f"isp-rest:{sector_id}:{ship_key}".encode()).digest()
    x = 20.0 + (h[0] / 255.0) * 55.0
    y = 20.0 + (h[1] / 255.0) * 55.0
    return {"x_pct": round(x, 3), "y_pct": round(y, 3), "heading_deg": float(h[2])}


def empty_idle_pose(sector_id: int, ship_key: str) -> Dict[str, Any]:
    a = resting_anchor(sector_id, ship_key)
    return {
        "x_pct": a["x_pct"],
        "y_pct": a["y_pct"],
        "heading_deg": a["heading_deg"],
        "phase": "idle",
        "burning": False,
        "leg": None,
    }


def seeded_waypoints(sector_id: int, ship_key: str, n: int = 3) -> List[Tuple[float, float]]:
    """Fallback docks when celestial layout isn't available — deterministic per ship."""
    pts: List[Tuple[float, float]] = []
    for i in range(n):
        h = hashlib.sha256(f"isp-wp:{sector_id}:{ship_key}:{i}".encode()).digest()
        pts.append((10.0 + (h[0] / 255.0) * 80.0, 12.0 + (h[1] / 255.0) * 76.0))
    return pts


# ---------------------------------------------------------------------------
# Destination catalog — habitable/station vs barren vs warp-out (Max 2026-07-16)
# Ballpark: ~60% trade/habitable, ~20% outbound, ~20% barren — biased by role.
#
# Body/station %-positions are computed by intrasystem_layout.SectorLayout —
# a faithful server-side port of the player-client's windshieldTableauLayout
# %-space math (WO-TRANCHE-0716 ISP-PARITY), so a destination picked here
# lands where the client's own windshield actually renders that body/station,
# not a placeholder. See that module's own docstring for the parity contract.
# ---------------------------------------------------------------------------

# celestial_service.generate_system caps merged real planets at MAX_BODIES=9
# (`sorted(planets, key=_planet_sort_key)[:MAX_BODIES]`, celestial_service.py)
# -- correct for RENDERING (the client's own /contents response never shows
# more than 9 real planets either, same shared function), but a sector with
# more than 9 real planets otherwise silently drops the overflow from THIS
# function's pools with no error (Mack, WO-TRANCHE-0716 ISP-PARITY gate).
# Every real planet still needs a reachable destination even when the
# render-side cap leaves it unmerged into `system["bodies"]`. Distinct from
# celestial_service.py's own BODY_SEED_SALT/STATION_SEED_SALT so this
# fallback stream can never collide with a real merged body/station's seed
# for the same planet UUID.
_OVERFLOW_PLANET_SEED_SALT = 0xBEEF01


def _overflow_planet_position(layout: SectorLayout, planet_id, root_seed: int) -> Tuple[float, float]:
    """A real Planet MAX_BODIES truncation left unmerged: mirrors
    celestial_service._make_stations's own per-UUID SplitMix64 idiom (folded
    UUID XOR root_seed XOR a salt) rather than inventing a new ad hoc hash
    scheme, then feeds the result through the SAME SectorLayout box every
    other body/station in this sector uses -- reachable and deterministic,
    even though (like every overflow planet) the client itself never renders
    it either, so there is no client position to match here."""
    from src.services.celestial_service import _MASK64, SplitMix64

    uuid_int = planet_id.int if hasattr(planet_id, "int") else int(str(planet_id).replace("-", ""), 16)
    folded = ((uuid_int >> 64) ^ uuid_int) & _MASK64
    rng = SplitMix64((folded ^ root_seed ^ _OVERFLOW_PLANET_SEED_SALT) & _MASK64)
    orbit_au = rng.uniform(ORBIT_AU_MIN, ORBIT_AU_MAX)
    phase_deg = float(rng.randint(0, 359))
    return layout.planet_position(orbit_au, phase_deg)


def _is_habitable_body(kind: Optional[str], habitability: Optional[float]) -> bool:
    k = (kind or "").upper().replace("PLANETTYPE.", "")
    if k in ("TERRAN", "OCEANIC", "TROPICAL", "JUNGLE"):
        return True
    try:
        return habitability is not None and float(habitability) >= 50
    except (TypeError, ValueError):
        return False


def _outbound_rim_points(sector_id: int, ship_key: str, n: int = 3) -> List[Dict[str, Any]]:
    pts: List[Dict[str, Any]] = []
    for i in range(n):
        h = hashlib.sha256(f"isp-outbound:{sector_id}:{ship_key}:{i}".encode()).digest()
        edge = h[0] % 4
        if edge == 0:
            x, y = 4.0 + (h[1] / 255.0) * 6.0, 15.0 + (h[2] / 255.0) * 70.0
        elif edge == 1:
            x, y = 90.0 + (h[1] / 255.0) * 6.0, 15.0 + (h[2] / 255.0) * 70.0
        elif edge == 2:
            x, y = 15.0 + (h[1] / 255.0) * 70.0, 6.0 + (h[2] / 255.0) * 8.0
        else:
            x, y = 15.0 + (h[1] / 255.0) * 70.0, 86.0 + (h[2] / 255.0) * 8.0
        pts.append({
            "x_pct": x, "y_pct": y,
            "target_kind": "outbound",
            "target_id": None,
            "bucket": "outbound",
        })
    return pts


def sector_destination_pools(db: Session, sector_id: int, ship_key: str) -> Dict[str, List[Dict[str, Any]]]:
    """Habitable/barren/outbound destination candidates for weighted NPC leg
    picking, positioned via SectorLayout so a chosen destination lands where
    the player's own windshield actually renders that body/station.

    Sources `bodies`/`stations` from celestial_service.generate_system —
    the SAME merge GET /sectors/{id}/contents uses to build the client's
    SystemSnapshot (real planets/stations merged onto the procedural
    skeleton via a seeded slot assignment, celestial_service.py's own
    _merge_real_planets/_make_stations) — not a locally-reconstructed
    approximation. A prior version of this function read
    SectorCelestial.composition directly and re-derived real-planet/
    -station orbit_au/phase_deg via ad hoc sha256 hashes when a real row
    wasn't already present in the persisted skeleton; for STATIONS that was
    never even close (real stations are never persisted into `composition`
    at all — celestial_service always computes their orbit/phase fresh via
    _make_stations, an entirely different seeded formula than the sha256
    fallback), so every station destination server-side previously landed
    nowhere near where the client renders that station. read_only=True
    matches the old code's own "never write" behavior (no first-visit
    skeleton-materialization side effect from a background NPC tick).

    generate_system's own MAX_BODIES=9 cap can leave real planets beyond the
    9th unmerged into `bodies` (correct for rendering — see
    _overflow_planet_position's own doc-comment); any planet_id not present
    among the merged bodies still gets a reachable, deterministic position
    below rather than being silently dropped from the pools.
    """
    from src.models.planet import Planet
    from src.models.sector import Sector
    from src.models.station import Station
    from src.services.celestial_service import _MASK64 as _CELESTIAL_MASK64
    from src.services.celestial_service import SECTOR_SEED_SALT, generate_system

    pools: Dict[str, List[Dict[str, Any]]] = {
        "habitable": [],
        "barren": [],
        "outbound": _outbound_rim_points(sector_id, ship_key),
    }

    sector = db.query(Sector).filter(Sector.sector_id == sector_id).first()
    if sector is None:
        return pools

    planets = db.query(Planet).filter(Planet.sector_id == sector_id).all()
    stations = db.query(Station).filter(Station.sector_id == sector_id).all()
    # Belt-and-suspenders fallback only — generate_system's _merge_real_planets
    # always sets body["habitability"] directly for every real planet it merges.
    planet_hab: Dict[str, float] = {str(p.id): float(p.habitability_score or 0) for p in planets}

    system = generate_system(db, sector, planets, stations, read_only=True)
    layout = SectorLayout(sector_id)

    merged_planet_ids: set = set()
    for b in (system.get("bodies") or []):
        if not isinstance(b, dict):
            continue
        orbit = float(b.get("orbit_au") or 0.4)
        phase = float(b.get("phase_deg") or 0)
        x, y = layout.planet_position(orbit, phase)
        pid = b.get("planet_id")
        hab_score = b.get("habitability")
        if hab_score is None and pid:
            hab_score = planet_hab.get(str(pid))
        bucket = "habitable" if _is_habitable_body(b.get("kind"), hab_score) else "barren"
        if pid:
            merged_planet_ids.add(str(pid))
        pools[bucket].append({
            "x_pct": x, "y_pct": y,
            "target_kind": "planet",
            "target_id": str(pid) if pid else None,
            "bucket": bucket,
        })

    overflow_planets = [p for p in planets if str(p.id) not in merged_planet_ids]
    if overflow_planets:
        root_seed = (int(sector.sector_id) * SECTOR_SEED_SALT) & _CELESTIAL_MASK64
        for p in overflow_planets:
            x, y = _overflow_planet_position(layout, p.id, root_seed)
            kind = p.type.name if getattr(p, "type", None) else None
            hab_score = float(p.habitability_score or 0)
            bucket = "habitable" if _is_habitable_body(kind, hab_score) else "barren"
            pools[bucket].append({
                "x_pct": x, "y_pct": y,
                "target_kind": "planet",
                "target_id": str(p.id),
                "bucket": bucket,
            })

    for st in (system.get("stations") or []):
        if not isinstance(st, dict):
            continue
        orbit = float(st.get("orbit_au") or 0.5)
        phase = float(st.get("phase_deg") or 0)
        x, y = layout.station_position(orbit, phase)
        sid = st.get("station_id")
        pools["habitable"].append({
            "x_pct": x, "y_pct": y,
            "target_kind": "station",
            "target_id": str(sid) if sid else None,
            "bucket": "habitable",
        })

    return pools


def destination_weights(
    *,
    archetype: Optional[str] = None,
    activity: Optional[str] = None,
    mission: Optional[str] = None,
) -> Dict[str, float]:
    arch = (archetype or "").upper()
    act = (activity or "").upper()
    miss = (mission or "").lower()

    w = {"habitable": 0.60, "outbound": 0.20, "barren": 0.20}
    if miss == "science" or arch == "RESEARCHER":
        w = {"habitable": 0.40, "outbound": 0.20, "barren": 0.40}
    elif miss == "colonist":
        w = {"habitable": 0.72, "outbound": 0.20, "barren": 0.08}
    elif miss == "commerce" or arch == "TRADER":
        w = {"habitable": 0.68, "outbound": 0.22, "barren": 0.10}
    elif act == "PATROL" or arch == "LAW_ENFORCEMENT":
        w = {"habitable": 0.55, "outbound": 0.30, "barren": 0.15}
    elif arch == "HOSTILE_RAIDER":
        w = {"habitable": 0.45, "outbound": 0.35, "barren": 0.20}
    return w


def pick_npc_destination(
    pools: Dict[str, List[Dict[str, Any]]],
    *,
    ship_key: str,
    leg_index: int,
    from_xy: Tuple[float, float],
    archetype: Optional[str] = None,
    activity: Optional[str] = None,
    mission: Optional[str] = None,
) -> Dict[str, Any]:
    weights = destination_weights(archetype=archetype, activity=activity, mission=mission)
    available = {k: v for k, v in pools.items() if v}
    if not available:
        x, y = seeded_waypoints(0, ship_key, 1)[0]
        return {"x_pct": x, "y_pct": y, "target_kind": "point", "target_id": None, "bucket": "habitable"}

    total = sum(weights.get(k, 0.0) for k in available) or float(len(available))
    h = hashlib.sha256(f"isp-pick:{ship_key}:{leg_index}".encode()).digest()
    roll = (h[0] + h[1] * 256) / 65535.0 * total
    chosen_bucket = next(iter(available))
    for k in available:
        roll -= weights.get(k, 0.0) if total else 1.0
        if roll <= 0:
            chosen_bucket = k
            break

    opts = available[chosen_bucket]
    cx, cy = from_xy
    ranked = sorted(
        opts,
        key=lambda o: math.hypot(float(o["x_pct"]) - cx, float(o["y_pct"]) - cy),
        reverse=True,
    )
    top = ranked[: max(1, len(ranked) // 2)]
    return dict(top[h[2] % len(top)])


def derive_pose(pose: Optional[Dict[str, Any]], now: Optional[datetime] = None) -> Dict[str, Any]:
    """Sample absolute pose at `now` from a stored plan (pure)."""
    now = now or _utcnow()
    if not pose:
        return empty_idle_pose(0, "unknown")

    leg = pose.get("leg")
    if not leg or not leg.get("started_at"):
        return {
            "x_pct": float(pose.get("x_pct", 50)),
            "y_pct": float(pose.get("y_pct", 50)),
            # Normalize on READ too, not just at write sites — self-heals any
            # stored heading_deg persisted before this fix shipped.
            "heading_deg": _normalize_heading_deg(float(pose.get("heading_deg", 0))),
            "phase": "idle",
            "burning": False,
            "leg": None,
        }

    started = _parse_iso(leg.get("started_at")) or now
    elapsed_ms = max(0.0, (now - started).total_seconds() * 1000.0)
    kind = (leg.get("kind") or "burn").lower()

    fx, fy = float(leg["from_x"]), float(leg["from_y"])
    tx, ty = float(leg["to_x"]), float(leg["to_y"])
    prograde = float(leg.get("prograde_deg", heading_deg(fx, fy, tx, ty)))
    parked = float(leg.get("parked_heading_deg", pose.get("heading_deg", prograde)))

    if kind == "halt":
        return _derive_halt(fx, fy, tx, ty, prograde, parked, elapsed_ms, leg)

    return _derive_burn(fx, fy, tx, ty, prograde, parked, elapsed_ms, leg)


def _lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def _derive_burn(
    fx: float, fy: float, tx: float, ty: float,
    prograde: float, parked: float, elapsed_ms: float, leg: Dict[str, Any],
) -> Dict[str, Any]:
    retrograde = prograde + 180.0
    face = prograde + 360.0
    move_start = ORIENT_MS
    accel_end = move_start + ACCEL_MS
    coast_end = accel_end + COAST_MS
    flip_end = coast_end + FLIP_MS
    move_end = move_start + MOVE_MS
    settle_end = move_end + SETTLE_MS

    if elapsed_ms < move_start:
        t = _smoothstep(elapsed_ms / ORIENT_MS)
        hdg = parked + _shortest_delta(parked, prograde) * t
        return {
            "x_pct": fx, "y_pct": fy, "heading_deg": hdg,
            "phase": "orienting", "burning": False, "leg": leg,
        }

    if elapsed_ms < move_end:
        p = _smoothstep((elapsed_ms - move_start) / MOVE_MS)
        x, y = _lerp(fx, tx, p), _lerp(fy, ty, p)
        if elapsed_ms < accel_end:
            return {
                "x_pct": x, "y_pct": y, "heading_deg": prograde,
                "phase": "accelerating", "burning": True, "leg": leg,
            }
        if elapsed_ms < coast_end:
            return {"x_pct": x, "y_pct": y, "heading_deg": prograde, "phase": "gliding", "burning": False, "leg": leg}
        if elapsed_ms < flip_end:
            ft = _smoothstep((elapsed_ms - coast_end) / FLIP_MS)
            return {
                "x_pct": x, "y_pct": y,
                "heading_deg": prograde + 180.0 * ft,
                "phase": "brake_turn", "burning": False, "leg": leg,
            }
        return {"x_pct": x, "y_pct": y, "heading_deg": retrograde, "phase": "braking", "burning": True, "leg": leg}

    if elapsed_ms < settle_end:
        t = _smoothstep((elapsed_ms - move_end) / SETTLE_MS)
        hdg = retrograde + (face - retrograde) * t
        return {"x_pct": tx, "y_pct": ty, "heading_deg": hdg, "phase": "final_orient", "burning": False, "leg": leg}

    # Arrived — idle at destination. face = prograde+360 is the client's OWN
    # unwrapped "continuous spin" convention during the final_orient sweep
    # above (windshieldTableauLayout.ts's otherShipFlightPose mirrors it
    # exactly) — harmless there since CSS rotate() is periodic, but this IS
    # the value that gets PERSISTED as the ship's long-term REST heading and
    # re-read as the next leg's `parked` baseline (start_burn, below) — must
    # be canonical [0,360) at the point it's stored, not carried forward
    # unbounded (Max's live fly-by: 531deg observed).
    return {
        "x_pct": tx, "y_pct": ty, "heading_deg": _normalize_heading_deg(face),
        "phase": "idle", "burning": False, "leg": None,
    }


def _derive_halt(
    fx: float, fy: float, tx: float, ty: float,
    prograde: float, parked: float, elapsed_ms: float, leg: Dict[str, Any],
) -> Dict[str, Any]:
    # Halt: flip then brake into stop (matches client TRAVEL_HALT_*).
    retrograde = prograde + 180.0
    total = HALT_FLIP_MS + HALT_BRAKE_MS
    if elapsed_ms < HALT_FLIP_MS:
        t = _smoothstep(elapsed_ms / HALT_FLIP_MS)
        # Coast a fraction toward stop while flipping
        p = 0.38 * t
        x, y = _lerp(fx, tx, p), _lerp(fy, ty, p)
        return {
            "x_pct": x, "y_pct": y,
            "heading_deg": prograde + 180.0 * t,
            "phase": "halt_turn", "burning": False, "leg": leg,
        }
    if elapsed_ms < total:
        t = _smoothstep((elapsed_ms - HALT_FLIP_MS) / HALT_BRAKE_MS)
        p = 0.38 + 0.62 * t
        x, y = _lerp(fx, tx, p), _lerp(fy, ty, p)
        return {
            "x_pct": x, "y_pct": y, "heading_deg": retrograde,
            "phase": "halt_brake", "burning": True, "leg": leg,
        }
    # Arrived (halted) — same rest-heading canonicalization as _derive_burn's
    # own arrival branch; this becomes the next leg's `parked` baseline.
    return {
        "x_pct": tx, "y_pct": ty, "heading_deg": _normalize_heading_deg(retrograde),
        "phase": "idle", "burning": False, "leg": None,
    }


def materialize(pose: Optional[Dict[str, Any]], now: Optional[datetime] = None) -> Dict[str, Any]:
    """Collapse a finished leg into idle storage shape."""
    sample = derive_pose(pose, now)
    if sample.get("phase") == "idle" and sample.get("leg") is None:
        return {
            "x_pct": sample["x_pct"],
            "y_pct": sample["y_pct"],
            "heading_deg": sample["heading_deg"],
            "phase": "idle",
            "burning": False,
            "leg": None,
        }
    # Still in flight — keep plan, refresh tip fields for readers
    out = dict(pose or {})
    out.update({
        "x_pct": sample["x_pct"],
        "y_pct": sample["y_pct"],
        "heading_deg": sample["heading_deg"],
        "phase": sample["phase"],
        "burning": sample["burning"],
    })
    return out


def start_burn(
    pose: Optional[Dict[str, Any]],
    *,
    to_x: float,
    to_y: float,
    sector_id: int,
    ship_key: str,
    target_kind: Optional[str] = None,
    target_id: Optional[str] = None,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Commit a new burn leg from the derived current pose to (to_x, to_y)."""
    now = now or _utcnow()
    to_x = _clamp(float(to_x), 0.0, 100.0)
    to_y = _clamp(float(to_y), 0.0, 100.0)

    current = derive_pose(pose or empty_idle_pose(sector_id, ship_key), now)
    # If previous leg finished, materialize idle first
    if current.get("phase") == "idle":
        base = current
    else:
        base = current  # mid-course redirect from live sample

    fx, fy = float(base["x_pct"]), float(base["y_pct"])
    # WO-XPCT-heading-redirect-fix (Mack): a mid-course redirect samples
    # derive_pose() while base["phase"] is a TRANSIENT phase (brake_turn:
    # prograde+180*ft, etc.) -- those are DELIBERATELY left unwrapped (see
    # _normalize_heading_deg's own doc-comment: matches the client's own
    # continuous-through-360 convention for smooth CSS rotation, harmless
    # while merely SAMPLED). This is the promotion point where that
    # transient value gets PERSISTED as a new leg's parked_heading_deg
    # baseline and the pose's own heading_deg -- a storage boundary exactly
    # like the arrival branches already normalize, just missed here
    # (reproduced live: persisted heading 539.89deg). Normalize ONCE here,
    # not at each of the 3 use sites below, so they can't drift apart.
    base_heading_deg = _normalize_heading_deg(float(base["heading_deg"]))
    if math.hypot(to_x - fx, to_y - fy) < 0.15:
        return {
            "x_pct": fx, "y_pct": fy,
            "heading_deg": base_heading_deg,
            "phase": "idle", "burning": False, "leg": None,
        }

    prograde = heading_deg(fx, fy, to_x, to_y)
    leg = {
        "kind": "burn",
        "from_x": fx,
        "from_y": fy,
        "to_x": to_x,
        "to_y": to_y,
        "target_kind": target_kind,
        "target_id": target_id,
        "started_at": _iso(now),
        "prograde_deg": prograde,
        "parked_heading_deg": base_heading_deg,
        "profile": PROFILE_MS,
    }
    return {
        "x_pct": fx,
        "y_pct": fy,
        "heading_deg": base_heading_deg,
        "phase": "orienting",
        "burning": False,
        "leg": leg,
    }


def start_halt(
    pose: Optional[Dict[str, Any]],
    *,
    sector_id: int,
    ship_key: str,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    now = now or _utcnow()
    current = derive_pose(pose or empty_idle_pose(sector_id, ship_key), now)
    if current.get("phase") == "idle":
        return {
            "x_pct": current["x_pct"], "y_pct": current["y_pct"],
            "heading_deg": current["heading_deg"],
            "phase": "idle", "burning": False, "leg": None,
        }

    fx, fy = float(current["x_pct"]), float(current["y_pct"])
    # Stop a short coast ahead along current heading
    rad = math.radians(float(current["heading_deg"]))
    # %-space: advance ~4% of band width along heading (aspect-naive, good enough)
    coast = 4.0
    tx = _clamp(fx + math.cos(rad) * coast, 4.0, 96.0)
    ty = _clamp(fy + math.sin(rad) * coast / max(REF_BAND_ASPECT, 0.05) * 0.15, 6.0, 94.0)
    # WO-XPCT-heading-redirect-fix (Mack, same risk class as start_burn's own
    # fix above): current["heading_deg"] can be a TRANSIENT, deliberately
    # unwrapped sample (e.g. mid brake_turn) when halting mid-flight -- this
    # is the promotion point where it becomes a persisted leg's
    # parked_heading_deg/prograde_deg baseline. rad above is unaffected
    # (cos/sin are periodic, same result either way); prograde is what gets
    # STORED, so it's normalized here, once.
    prograde = _normalize_heading_deg(float(current["heading_deg"]))
    leg = {
        "kind": "halt",
        "from_x": fx,
        "from_y": fy,
        "to_x": tx,
        "to_y": ty,
        "started_at": _iso(now),
        "prograde_deg": prograde,
        "parked_heading_deg": prograde,
        "profile": PROFILE_MS,
    }
    return {
        "x_pct": fx, "y_pct": fy, "heading_deg": prograde,
        "phase": "halt_turn", "burning": False, "leg": leg,
    }


def pose_public(pose: Optional[Dict[str, Any]], now: Optional[datetime] = None) -> Dict[str, Any]:
    """API / presence / WS payload — always includes server_time + derived tip."""
    now = now or _utcnow()
    sample = derive_pose(pose, now)
    return {
        "server_time": _iso(now),
        "x_pct": sample["x_pct"],
        "y_pct": sample["y_pct"],
        "heading_deg": sample["heading_deg"],
        "phase": sample["phase"],
        "burning": bool(sample["burning"]),
        "leg": sample.get("leg") or (pose or {}).get("leg"),
        "profile": PROFILE_MS,
    }


def build_presence_entry(
    *,
    player_id: Any,
    username: str,
    ship_id: Optional[Any],
    ship_name: Optional[str],
    ship_type: Optional[str],
    team_id: Optional[Any],
    arrived_at: Optional[datetime] = None,
) -> Dict[str, Any]:
    """QUEUE-HEAL-ENTRY-SHAPE (2026-07-16): the SINGLE canonical
    ``players_present`` entry constructor -- used by BOTH the organic
    arrival path (``movement_service._update_player_presence``, the
    reference shape) and the presence sweep's heal pass
    (``presence_helpers._heal_missing_or_poseless_presence_sync``), so a
    healed entry and an organically-arrived entry for the same player
    state are KEY-SET AND VALUE identical (mod the live ``arrived_at``
    instant).

    Live bug this closes: the heal pass hardcoded ``ship_name``/
    ``ship_type`` to the literal string ``"None"`` even when its own
    candidate query already had the ship join data available (live
    evidence: a healed entry with a correct ``ship_id`` but null name/
    type). Fixed by making BOTH callers resolve ``ship_name``/
    ``ship_type`` themselves -- ``movement_service`` via the already-
    loaded ``player.current_ship`` relationship, heal via its own new
    ``Ship`` join in ``_heal_candidates_query`` -- and pass the resolved
    values in here, so there is exactly ONE place left that decides the
    final shape and fallback semantics.

    ``ship_name``/``ship_type`` fall back to the literal string ``"None"``
    (not Python ``None``, not omitted) to match the pre-existing, already-
    shipped client contract -- the reference shape has always used this
    fallback for a player with no current ship.

    ``arrived_at`` accepts a ``datetime`` (a naive one is treated as UTC)
    or defaults to ``now(UTC)`` -- standardizes both call sites onto the
    timezone-aware convention this codebase's newer presence code already
    uses; ``movement_service``'s prior naive ``datetime.now()`` is
    corrected here rather than preserved, since converging both callers
    onto one constructor means picking exactly one convention."""
    if arrived_at is None:
        arrived_at = datetime.now(timezone.utc)
    elif arrived_at.tzinfo is None:
        arrived_at = arrived_at.replace(tzinfo=timezone.utc)
    return {
        "player_id": str(player_id),
        "username": username,
        "ship_id": str(ship_id) if ship_id else None,
        "ship_name": ship_name if ship_name else "None",
        "ship_type": ship_type if ship_type else "None",
        "team_id": str(team_id) if team_id else None,
        "arrived_at": arrived_at.isoformat(),
    }


def mirror_into_presence_entry(
    entry: Dict[str, Any], pose: Optional[Dict[str, Any]], now: Optional[datetime] = None
) -> Dict[str, Any]:
    pub = pose_public(pose, now)
    out = dict(entry)
    out["pose"] = {
        "x_pct": pub["x_pct"],
        "y_pct": pub["y_pct"],
        "heading_deg": pub["heading_deg"],
        "phase": pub["phase"],
        "burning": pub["burning"],
        "leg": pub["leg"],
        "server_time": pub["server_time"],
    }
    return out


def _enrich_npc_lookup_query(db: Session, npc_ids: List[Any]):
    """Builds (does not execute) enrich_presence_with_live_pose's NPC lookup
    -- split out (2026-07-16 crash-fix DoD hardening) so a real-SQLAlchemy
    unit test can construct this exact query directly (see
    test_presence_mirror.py's TestEnrichmentQueriesRealSQLAlchemy),
    matching the "every ORM query-construction path gets a real-engine
    build/compile test" norm established for the presence-sweep heal query.
    Both columns here are real Columns (no property-as-column risk), but
    the norm is now blanket, not case-by-case."""
    from src.models.npc_character import NPCCharacter
    return db.query(NPCCharacter).filter(NPCCharacter.id.in_(npc_ids))


def _enrich_player_lookup_query(db: Session, human_ids: List[Any]):
    """Builds (does not execute) enrich_presence_with_live_pose's human
    lookup -- see _enrich_npc_lookup_query's own doc-comment for why this
    is split out."""
    from src.models.player import Player
    return db.query(Player).filter(Player.id.in_(human_ids))


def enrich_presence_with_live_pose(db: Session, present: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """P0-FIX-PRESENCE-MIRROR: pure read that overwrites EVERY presence
    entry's `pose` (and, for NPCs, activity/mission/archetype — the
    pre-existing enrichment this extends) with the CURRENT authoritative
    source (Player.intrasystem_pose / NPCCharacter.intrasystem_pose),
    instead of trusting whatever was last mirrored into
    sector.players_present at WRITE time.

    Live repro this closes: ensure_player_pose (called from GET /helm/
    intrasystem/pose) lazily creates a player's FIRST pose but never calls
    set_player_pose/_sync_player_presence_pose — the pose exists on the
    Player row, but that player's own players_present entry has NO pose
    keys at all, so every OTHER player's client renders them "porting"
    (mirrors the pre-existing NPC-only enrichment in sectors.py/player.py,
    which is why NPCs were never affected by this bug class — their pose
    is ALWAYS re-derived fresh here, never trusted from the stale mirror).
    Any FUTURE write path that forgets to mirror on write is automatically
    covered too, since every read re-derives instead of trusting storage.

    Kept in ADDITION to (not instead of) the existing write-time mirror
    (_sync_player_presence_pose/_sync_npc_presence_pose) — that mirror is
    still the only freshness guarantee for any OTHER consumer that reads
    sector.players_present directly without calling this function (e.g. a
    future WS broadcast of the raw array); removing it would trade one
    stale-read class for another. Cheap defense in depth, not redundant
    work — the write-time mirror was already paid for."""
    if not present:
        return present
    npc_ids = [
        e.get("player_id") for e in present
        if isinstance(e, dict) and e.get("is_npc") and e.get("player_id")
    ]
    human_ids = [
        e.get("player_id") for e in present
        if isinstance(e, dict) and not e.get("is_npc") and e.get("player_id")
    ]
    if not npc_ids and not human_ids:
        return present

    npc_by_id: Dict[str, Any] = {}
    if npc_ids:
        npc_by_id = {str(n.id): n for n in _enrich_npc_lookup_query(db, npc_ids).all()}

    player_by_id: Dict[str, Any] = {}
    if human_ids:
        player_by_id = {str(p.id): p for p in _enrich_player_lookup_query(db, human_ids).all()}

    enriched: List[Dict[str, Any]] = []
    for e in present:
        if not isinstance(e, dict):
            enriched.append(e)
            continue
        if e.get("is_npc"):
            n = npc_by_id.get(str(e.get("player_id")))
            if n is not None:
                e = dict(e)
                act = n.current_activity
                e["activity"] = (act.name if hasattr(act, "name") else str(act)) if act else None
                e["mission"] = (n.daily_schedule or {}).get("mission") or "commerce"
                e["archetype"] = n.archetype.name if n.archetype else None
                if n.intrasystem_pose is not None:
                    e["pose"] = pose_public(n.intrasystem_pose)
        else:
            p = player_by_id.get(str(e.get("player_id")))
            if p is not None and p.intrasystem_pose is not None:
                e = dict(e)
                e["pose"] = pose_public(p.intrasystem_pose)
        enriched.append(e)
    return enriched


def ensure_player_pose(player, ship_key: Optional[str] = None) -> Dict[str, Any]:
    key = ship_key or str(getattr(player, "current_ship_id", None) or player.id)
    pose = getattr(player, "intrasystem_pose", None)
    if not pose:
        pose = empty_idle_pose(int(player.current_sector_id), key)
        player.intrasystem_pose = pose
    return pose


# ---------------------------------------------------------------------------
# Server-gated dock/land proximity (WO-ISP-DOCKPROX)
#
# The player-client ALREADY gates the Dock/Land button on distance — see
# WindshieldTableau.tsx's DOCK_RANGE_EM=5 + withinLandRange/withinDockRange
# (distancePx(shipPos, targetPos, bandBox) <= DOCK_RANGE_EM * bandBox.remPx)
# — but that gate only ever hid the BUTTON. Nothing stopped a direct
# POST /trading/dock or /planets/land call from anywhere in the sector; the
# server never checked the player's own pose against the target's position.
#
# THIS IS A TUNABLE DIAL, NOT A FIXED FACT — DOCK_LAND_PROXIMITY_RANGE_EM is
# flagged prominently here (and in this WO's own report) for Max to confirm
# or override, not a unilateral pick. Proposed default: 5.0em, matching the
# client's own DOCK_RANGE_EM verbatim, so server enforcement stays INVISIBLE
# to a legitimate player already relying on the shipped, playtested UI gate
# — the button only ever appears when the server would also allow the
# request. Reuses the SAME fixed reference band (LAYOUT_BAND_WIDTH_PX/
# HEIGHT_PX/REM_PX, intrasystem_layout.py) the rest of this file's %-math
# already runs on, for the identical reason: the server has no DOM to
# measure, so the fixed 1440x334.7 band IS the shared coordinate system.
# ---------------------------------------------------------------------------
DOCK_LAND_PROXIMITY_RANGE_EM = 5.0


def _pose_distance_px(ax_pct: float, ay_pct: float, bx_pct: float, by_pct: float) -> float:
    """Euclidean px distance between two %-space points at the fixed
    reference band — server analog of WindshieldTableau.tsx's distancePx
    (which uses the REAL measured band; %-of-width and %-of-height are not
    interchangeable units on a wide-short band, so this must convert to px
    before comparing, not diff the raw percentages)."""
    from src.services.intrasystem_layout import LAYOUT_BAND_HEIGHT_PX, LAYOUT_BAND_WIDTH_PX

    dx_px = ((ax_pct - bx_pct) / 100.0) * LAYOUT_BAND_WIDTH_PX
    dy_px = ((ay_pct - by_pct) / 100.0) * LAYOUT_BAND_HEIGHT_PX
    return math.hypot(dx_px, dy_px)


def is_within_dock_land_range(
    player_x_pct: float, player_y_pct: float, target_x_pct: float, target_y_pct: float
) -> bool:
    from src.services.intrasystem_layout import LAYOUT_BAND_REM_PX

    threshold_px = DOCK_LAND_PROXIMITY_RANGE_EM * LAYOUT_BAND_REM_PX
    return _pose_distance_px(player_x_pct, player_y_pct, target_x_pct, target_y_pct) <= threshold_px


def current_player_pose_xy(player) -> Tuple[float, float]:
    """The player's CURRENT %-space position — mid-flight-interpolation-aware
    (derive_pose samples an in-progress burn leg at `now`, not just the last
    stored idle point), so a player who burned toward the target and is now
    passing near it reads as near, not stuck at their departure point. This
    is the authoritative input to the dock/land proximity gate below."""
    pose = ensure_player_pose(player)
    sample = derive_pose(pose)
    return float(sample["x_pct"]), float(sample["y_pct"])


def resolve_target_position(
    db: Session, sector_id: int, ship_key: str, target_kind: str, target_id: str
) -> Optional[Tuple[float, float]]:
    """Locate a real planet's/station's current %-space position for the
    dock/land proximity gate, by reusing sector_destination_pools — the SAME
    already-tested, Mack-gated position-resolution path this module uses to
    build NPC leg destinations (celestial_service.generate_system's real-body
    merge for planets, including its MAX_BODIES overflow fallback; a real
    station's _make_stations position). One canonical "where is X in this
    sector" lookup instead of a second, independently-maintained copy of the
    same merge/fallback logic. Returns None if the target can't be located
    (should not happen given the route's own prior existence/sector checks —
    callers should fail CLOSED, not open, if this ever returns None)."""
    pools = sector_destination_pools(db, sector_id, ship_key)
    for bucket in ("habitable", "barren"):
        for entry in pools.get(bucket, []):
            if entry.get("target_kind") == target_kind and entry.get("target_id") == target_id:
                return (float(entry["x_pct"]), float(entry["y_pct"]))
    return None


def assert_dock_land_proximity(
    db: Session,
    player,
    *,
    sector_id: int,
    target_kind: str,
    target_id: str,
    target_label: str,
    action_word: str,
) -> None:
    """The single call site both /trading/dock and /planets/land use — raises
    HTTPException(400) when the player's own current pose is farther than
    DOCK_LAND_PROXIMITY_RANGE_EM from the target, silently returns (does
    nothing) when within range. Kept as ONE small, directly-testable
    function (rather than duplicating the resolve+compare+raise sequence in
    each route) so both call sites share identical semantics and this
    module's own test suite can prove both the reject and allow paths
    without needing a full FastAPI/docking_service route-level harness —
    each route's own footprint is just this one line."""
    from fastapi import HTTPException

    player_xy = current_player_pose_xy(player)
    ship_key = f"proxcheck:{player.id}"
    target_xy = resolve_target_position(db, sector_id, ship_key, target_kind, target_id)
    if target_xy is None or not is_within_dock_land_range(*player_xy, *target_xy):
        raise HTTPException(
            status_code=400,
            detail=f"You are too far from {target_label} to {action_word} — move closer and try again",
        )


def set_player_pose(db: Session, player, pose: Dict[str, Any]) -> Dict[str, Any]:
    player.intrasystem_pose = pose
    flag_modified(player, "intrasystem_pose")
    _sync_player_presence_pose(db, player, pose)
    return pose


def set_npc_pose(db: Session, npc, pose: Dict[str, Any]) -> Dict[str, Any]:
    npc.intrasystem_pose = pose
    flag_modified(npc, "intrasystem_pose")
    _sync_npc_presence_pose(db, npc, pose)
    return pose


def _sync_player_presence_pose(db: Session, player, pose: Dict[str, Any]) -> None:
    from src.models.sector import Sector

    sector = (
        db.query(Sector)
        .filter(Sector.sector_id == player.current_sector_id)
        .with_for_update()
        .first()
    )
    if not sector:
        return
    pid = str(player.id)
    present = list(sector.players_present or [])
    changed = False
    for i, e in enumerate(present):
        if isinstance(e, dict) and str(e.get("player_id")) == pid and not e.get("is_npc"):
            present[i] = mirror_into_presence_entry(e, pose)
            changed = True
            break
    if changed:
        sector.players_present = present
        flag_modified(sector, "players_present")


def _sync_npc_presence_pose(db: Session, npc, pose: Dict[str, Any]) -> None:
    from sqlalchemy import text

    from src.models.sector import Sector

    if npc.current_sector_id is None:
        return

    # Fail fast under row-lock contention — same discipline as
    # npc_movement_service.move_npc/relocate_npc. tick_npc_legs wraps each
    # NPC's own work in a SAVEPOINT (db.begin_nested), so a lock timeout here
    # only skips THIS NPC's leg for this tick, not every NPC processed
    # earlier in the same batch (a full session.rollback used to wipe all of
    # them — the same failure class Loop A's own drive loop already guards
    # against; see npc_tick_loops.py's per-NPC SAVEPOINT doc-comment).
    try:
        db.execute(text("SET LOCAL lock_timeout = '3s'"))
    except Exception:
        logger.debug("_sync_npc_presence_pose: could not set lock_timeout", exc_info=True)

    sector = (
        db.query(Sector)
        .filter(Sector.sector_id == npc.current_sector_id)
        .with_for_update()
        .first()
    )
    if not sector:
        return
    nid = str(npc.id)
    present = list(sector.players_present or [])
    changed = False
    for i, e in enumerate(present):
        if isinstance(e, dict) and e.get("is_npc") and str(e.get("player_id")) == nid:
            present[i] = mirror_into_presence_entry(e, pose)
            changed = True
            break
    if changed:
        sector.players_present = present
        flag_modified(sector, "players_present")


# QUEUE-ISP-WS-EMIT-THREAD: tick_npc_legs (and therefore emit_leg_started)
# runs INSIDE core_loop.py's npc_scheduler_loop, which dispatches Loop A's
# whole tick body onto a worker thread via `await asyncio.to_thread(
# _run_due_ticks_sync, elapsed)` so the event loop never blocks on sync DB
# work. asyncio.get_event_loop() -- the previous implementation below --
# only resolves a loop bound to the CURRENT thread; called from that worker
# thread it raises `RuntimeError: There is no current event loop in thread
# 'ThreadPoolExecutor-0_0'` (confirmed live, heimdall gameserver logs,
# 2026-07-16, firing on every single leg-started attempt). Captured ONCE at
# scheduler startup (npc_scheduler_loop itself, the async context that IS
# running on the real loop -- see core_loop.py) via set_scheduler_event_loop,
# as a FALLBACK for exactly that worker-thread case.
_scheduler_event_loop: Optional[Any] = None


def set_scheduler_event_loop(loop: Any) -> None:
    """Call once from the async context that owns the real event loop
    (core_loop.py's npc_scheduler_loop, via asyncio.get_running_loop())."""
    global _scheduler_event_loop
    _scheduler_event_loop = loop


def _resolve_broadcast_loop() -> Any:
    """Prefer the ACTUAL running loop of the calling thread (correct for a
    real async route handler like /helm/intrasystem/halt, and doesn't
    depend on the scheduler having started at all); fall back to the loop
    captured at scheduler startup only when there ISN'T one on this thread
    (tick_npc_legs's worker-thread case, the actual bug this WO fixes)."""
    import asyncio

    try:
        return asyncio.get_running_loop()
    except RuntimeError:
        return _scheduler_event_loop


def _emit_leg_ws_event(event_type: str, sector_id: int, ship_id: str, is_npc: bool, pose: Dict[str, Any]) -> None:
    """Shared best-effort WS broadcast core for emit_leg_started/
    emit_leg_halted (mirrors combat_service's own fire-and-forget
    convention) — every failure mode here is swallowed, a dead socket or a
    not-yet-captured/closed loop must never break the caller (a scheduler
    tick, or a player's own burn/halt REST request). The two EXPECTED
    lifecycle states (no loop resolvable yet; loop closed during shutdown)
    log a short line at debug, not a full traceback — the previous
    exc_info=True on every single leg-start attempt was exactly the
    "per-leg DEBUG traceback spam" this WO also asks to kill. A traceback
    is still captured for anything genuinely unexpected."""
    import asyncio

    loop = _resolve_broadcast_loop()
    if loop is None or loop.is_closed():
        logger.debug("%s emit skipped: no live event loop resolvable", event_type)
        return
    try:
        from src.services.websocket_service import connection_manager

        pub = pose_public(pose)
        frame = {
            "type": event_type,
            "sector_id": sector_id,
            "ship_id": ship_id,
            "is_npc": is_npc,
            **pub,
        }
        asyncio.run_coroutine_threadsafe(
            connection_manager.broadcast_to_sector(int(sector_id), frame), loop
        )
    except Exception:
        logger.debug("%s emit failed unexpectedly", event_type, exc_info=True)


def emit_leg_started(sector_id: int, ship_id: str, is_npc: bool, pose: Dict[str, Any]) -> None:
    _emit_leg_ws_event("intrasystem.leg_started", sector_id, ship_id, is_npc, pose)


def emit_leg_halted(sector_id: int, ship_id: str, is_npc: bool, pose: Dict[str, Any]) -> None:
    _emit_leg_ws_event("intrasystem.leg_halted", sector_id, ship_id, is_npc, pose)


def tick_npc_legs(db: Session, *, limit: int = 40) -> int:
    """Advance finished legs + schedule new burns for active NPCs in-sector.

    SLEEP → stay parked. PATROL/COMMUTE/WORK_STATION → weighted dest burns
    (mostly habitable/stations, some outbound rim, rarely barren).
    """
    from src.models.npc_character import NPCCharacter, NPCStatus

    now = _utcnow()
    live = (
        db.query(NPCCharacter)
        .filter(
            NPCCharacter.status.in_([NPCStatus.ON_DUTY, NPCStatus.OFF_DUTY]),
            NPCCharacter.current_sector_id.isnot(None),
            NPCCharacter.ship_id.isnot(None),
        )
        .limit(200)
        .all()
    )
    moved = 0
    for npc in live:
        if moved >= limit:
            break
        # SAVEPOINT per NPC (same discipline as npc_tick_loops.py's Loop A
        # drive loop, and the retention-sweep's per-player savepoint): a
        # lock-timeout or any other error for ONE NPC (most likely
        # _sync_npc_presence_pose's sector row lock, itself now guarded by
        # a 3s lock_timeout) must not roll back legs already committed for
        # every OTHER NPC processed earlier in this same tick_npc_legs call
        # — a bare session-wide failure previously would have.
        try:
            with db.begin_nested():
                key = str(npc.ship_id or npc.id)
                sid = int(npc.current_sector_id)
                pose = npc.intrasystem_pose
                if not pose:
                    pose = empty_idle_pose(sid, key)
                    npc.intrasystem_pose = pose
                    flag_modified(npc, "intrasystem_pose")

                sample = derive_pose(pose, now)
                # Materialize completed legs
                if pose.get("leg") and sample.get("phase") == "idle" and sample.get("leg") is None:
                    pose = {
                        "x_pct": sample["x_pct"],
                        "y_pct": sample["y_pct"],
                        "heading_deg": sample["heading_deg"],
                        "phase": "idle",
                        "burning": False,
                        "leg": None,
                    }
                    set_npc_pose(db, npc, pose)
                    moved += 1
                    continue

                act = npc.current_activity
                act_name = act.name if hasattr(act, "name") else str(act or "")
                if act_name not in ("PATROL", "COMMUTE", "WORK_STATION"):
                    continue
                if sample.get("phase") != "idle":
                    continue

                # Weighted destination: mostly habitable worlds + stations,
                # some warp-out rim loiters, rarely barren rocks (science
                # biased higher).
                mission = (npc.daily_schedule or {}).get("mission") if isinstance(npc.daily_schedule, dict) else None
                arch = npc.archetype.name if getattr(npc, "archetype", None) else None
                pools = sector_destination_pools(db, sid, key)
                # leg_index from wall clock so successive idle picks advance
                leg_index = int(now.timestamp() // 10) + (hash(key) & 0xFFFF)
                dest = pick_npc_destination(
                    pools,
                    ship_key=key,
                    leg_index=leg_index,
                    from_xy=(float(sample["x_pct"]), float(sample["y_pct"])),
                    archetype=arch,
                    activity=act_name,
                    mission=mission,
                )
                new_pose = start_burn(
                    pose,
                    to_x=float(dest["x_pct"]),
                    to_y=float(dest["y_pct"]),
                    sector_id=sid,
                    ship_key=key,
                    target_kind=dest.get("target_kind") or "point",
                    target_id=dest.get("target_id"),
                    now=now,
                )
                set_npc_pose(db, npc, new_pose)
                emit_leg_started(sid, str(npc.ship_id), True, new_pose)
                moved += 1
        except Exception:
            logger.exception("tick_npc_legs: leg tick failed for NPC %s (skipped)", npc.id)
    return moved
