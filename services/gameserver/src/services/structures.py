"""structures.py — the single planetary time-advance entry-point (CRT spine, WO-K1a).

Built from `audit/design-briefs/crt-unified/03-spec-spine.md` (SUBSUME). This module is:
  * the ONLY code that writes ``Planet.structures``, AND
  * the single deterministic, ordered, six-step planetary tick: ``settle(planet, now, *, db)``.

THE LOAD-BEARING PIN (do NOT violate)
-------------------------------------
``settle()`` does **NOT** pass ``now`` into the three legacy bodies. Each body keeps reading its
**own** inner anchor in its **own** clock domain exactly as shipped:

  | clock      | body                         | inner anchor                              | domain     |
  |------------|------------------------------|-------------------------------------------|------------|
  | production | apply_resource_production    | last_production + active_events carry      | WALL-CLOCK |
  | siege      | advance_siege                | siege_started_at + siege_turns             | canonical  |
  | terraform  | _advance_terraforming        | active_events['terraforming']['last_tick_at'] | canonical  |

``settle()``'s own ``now`` is consumed ONLY by NEW spine code — the ``last_settle_at`` monotonic
gate, the cold-start seed, and the (K1b) step-6 event window key. Because the bodies are called
with **zero new arguments**, they cannot be mis-scaled and the first ``settle()`` over a
never-changed planet reproduces byte-identical results (reproduce-exactly, I10).

CLOCK DOMAIN (the subtlety the spec flags)
------------------------------------------
In this codebase canonical and wall-clock share the **same absolute instants** — every inner
anchor (``last_production``, ``siege_started_at``, ``last_tick_at``) is stored as a wall-clock
``datetime``; ``game_time.canonical_hours_since`` only scales *elapsed* by ``GAME_TIME_SCALE``.
So the spine's monotonic gate compares wall-instant ``now`` against the wall-instant
``last_settle_at`` (wall-vs-wall — never wall-vs-canonical), and the cold-start seed ``max()`` is
computed in a single consistent domain (wall-clock), converting siege's *canonical* turn-span
(``SIEGE_TURN_HOURS`` canonical hours per applied turn) into wall-hours via ``/ GAME_TIME_SCALE``
before adding it to the wall-clock ``siege_started_at``. The seed value is used ONLY as a gate /
(future) window key — the bodies still read their own raw anchors — so reproduce-exactly holds
regardless. See the orchestrator STATUS note flagging the spec's "convert to canonical" premise
vs. the actual wall-clock storage (equivalent for a single global scale; argmax of wall instants
== argmin of canonical-hours-since).

K1a-1 lands this DORMANT: ``settle()`` calls the unchanged bodies behind ``_via_settle=True`` but
NO call-site is flipped to it yet (the cutover is Max-gated, spec §8).
"""

import logging
import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta, UTC
from typing import Optional, Set

from sqlalchemy.orm.attributes import flag_modified

from src.core.game_time import GAME_TIME_SCALE

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# _via_settle guard (COEXIST graft, spec §3 / I5)
# ---------------------------------------------------------------------------
# Each legacy body gains a ``_via_settle: bool = False`` kwarg and calls this guard. POST-CUTOVER
# (K1a-3) settle() is the single clock-writer — every legit body call comes through settle() with
# ``_via_settle=True`` (the grep-gate test, I4, proves zero other callers). So a ``_via_settle=False``
# call is now a STRAY clock-advancing caller: the guard WARN-logs it loudly. We deliberately do NOT
# raise in production by default (``STRICT_VIA_SETTLE=False``) — crashing a live planetary tick /
# player read on a stray is worse than a loud WARNING + the CI grep-gate. Tests flip
# ``STRICT_VIA_SETTLE=True`` to turn a stray into an AssertionError, proving the guard trips (I5).
STRICT_VIA_SETTLE = False


def _via_settle_guard(name: str, via_settle: bool) -> None:
    if not via_settle:
        logger.warning(
            "%s() called directly, NOT via structures.settle() — stray clock-advancing caller "
            "(post-cutover all clock advances must route through settle()).", name,
        )
        if STRICT_VIA_SETTLE:
            raise AssertionError(
                f"{name}() called outside structures.settle() with STRICT_VIA_SETTLE — "
                "a stray clock-advancing caller survived the cutover."
            )


# ---------------------------------------------------------------------------
# SettleResult (COEXIST graft, spec §1.1)
# ---------------------------------------------------------------------------
@dataclass
class SettleResult:
    """What moved during one settle() — so call-sites and tests can assert."""
    changed: bool
    steps_changed: Set[str] = field(default_factory=set)
    window_consumed_seconds: float = 0.0

    @classmethod
    def noop(cls) -> "SettleResult":
        return cls(False, set(), 0.0)


# ---------------------------------------------------------------------------
# Time helpers
# ---------------------------------------------------------------------------
def _aware(dt: Optional[datetime]) -> Optional[datetime]:
    """Coerce a naive datetime to UTC-aware; pass through None."""
    if dt is None:
        return None
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


def _canonical_now() -> datetime:
    """The spine's monotonic instant. Wall-clock UTC — which IS the canonical instant in this
    codebase (GAME_TIME_SCALE scales elapsed, not absolute timestamps)."""
    return datetime.now(UTC)


# ---------------------------------------------------------------------------
# Spine anchor: structures['terraform_meta']['last_settle_at'] (spec §1.3)
# ---------------------------------------------------------------------------
def _read_settle_anchor(planet) -> Optional[datetime]:
    structures = planet.structures if isinstance(planet.structures, dict) else None
    if not structures:
        return None
    tmeta = structures.get("terraform_meta")
    if not isinstance(tmeta, dict):
        return None
    raw = tmeta.get("last_settle_at")
    if not raw:
        return None
    try:
        return _aware(datetime.fromisoformat(raw))
    except (TypeError, ValueError):
        return None


def _set_settle_anchor(planet, when: datetime) -> None:
    """Single writer of the spine anchor. Reassigns structures so SQLAlchemy detects the mutation
    (JSONB dict-reassign pattern, mirrors active_events writers); also flag_modified for safety."""
    structures = dict(planet.structures) if isinstance(planet.structures, dict) else {}
    tmeta = dict(structures.get("terraform_meta")) if isinstance(structures.get("terraform_meta"), dict) else {}
    tmeta["last_settle_at"] = _aware(when).isoformat()
    structures["terraform_meta"] = tmeta
    planet.structures = structures
    flag_modified(planet, "structures")


def _seed_anchor_value(planet) -> datetime:
    """Cold-start seed (spec §6.2): the MAX of the existing inner anchors, in ONE consistent
    (wall-clock) domain, so the first spine window is ``[max-of-existing-anchors, now]`` — no
    consumed window re-awarded, no unsettled window skipped. A brand-new planet (no anchors) seeds
    to ``now``.

    Siege contributes ``siege_started_at + applied_turns × SIEGE_TURN_HOURS / GAME_TIME_SCALE``:
    SIEGE_TURN_HOURS is *canonical* hours per applied turn, divided by GAME_TIME_SCALE to express
    the already-applied span in WALL hours (the storage domain of the other anchors)."""
    from src.services.planetary_service import SIEGE_TURN_HOURS, SIEGE_TURNS_THRESHOLD

    candidates = []

    lp = _aware(planet.last_production)
    if lp is not None:
        candidates.append(lp)

    # Terraform inner anchor lives in active_events. Canonical storage is a LIST of event dicts —
    # the {type: "terraforming", last_tick_at, ...} item that
    # terraforming_service._get_terraforming_meta reads; some paths instead store active_events as a
    # dict keyed by "terraforming". Read BOTH shapes so the seed sees the same anchor the body does.
    ae = planet.active_events
    tmeta = None
    if isinstance(ae, list):
        for ev in ae:
            if isinstance(ev, dict) and ev.get("type") == "terraforming":
                tmeta = ev
                break
    elif isinstance(ae, dict):
        t = ae.get("terraforming")
        tmeta = t if isinstance(t, dict) else None
    if tmeta:
        terra_raw = tmeta.get("last_tick_at") or tmeta.get("started_at")
        if terra_raw:
            try:
                candidates.append(_aware(datetime.fromisoformat(terra_raw)))
            except (TypeError, ValueError):
                pass

    if planet.under_siege and planet.siege_started_at:
        applied_turns = max(0, (planet.siege_turns or 0) - SIEGE_TURNS_THRESHOLD)
        wall_span_hours = (applied_turns * SIEGE_TURN_HOURS) / (GAME_TIME_SCALE or 1.0)
        candidates.append(_aware(planet.siege_started_at) + timedelta(hours=wall_span_hours))

    if not candidates:
        return _canonical_now()
    return max(candidates)


def _get_settle_anchor(planet) -> datetime:
    """Read the spine anchor; full cold-start via seed() if null. Caller commits — the seed is
    atomic with the first settle() (SEED-determinism, I8)."""
    anchor = _read_settle_anchor(planet)
    if anchor is not None:
        return anchor
    seed(planet)
    return _read_settle_anchor(planet)


# ---------------------------------------------------------------------------
# seed() — cold-start owner of Planet.structures (K1a-2, spec §10)
# ---------------------------------------------------------------------------
def _legacy_layout_map(planet) -> dict:
    """FORWARD-METADATA snapshot of the legacy planet fields the K1b grid will hydrate from
    (size→grid, factory/farm/mine→economy, research_level→lab, defense→defense, terrain/temp/water
    →plots+axes). NOTHING reads this in K1a — captured now so K1b builds the grid layout without
    re-deriving. A pure read-snapshot: touches no derived field (reproduce-exactly safe). Missing
    columns default safely (this is provisional; K1b refines the schema)."""
    def g(name, default=0):
        return getattr(planet, name, default)
    return {
        "size": g("size"),
        "citadel_level": g("citadel_level"),
        "research_level": g("research_level"),
        "economy": {
            "factory_level": g("factory_level"),
            "farm_level": g("farm_level"),
            "mine_level": g("mine_level"),
        },
        "defense": {
            "defense_level": g("defense_level"),
            "defense_shields": g("defense_shields"),
            "defense_fighters": g("defense_fighters"),
        },
        "terrain": {
            "terrain": g("terrain", None),
            "temperature": g("temperature", None),
            "water_coverage": g("water_coverage", None),
        },
    }


def _seed_find_spot(structures: dict, kind: str):
    """First (x,y) where `kind`'s footprint fits (cleared, hazard-free, unoccupied). None if no fit."""
    grid = structures.get("grid") or {}
    for y in range(int(grid.get("rows", 0))):
        for x in range(int(grid.get("cols", 0))):
            ok, _ = can_place(structures, kind, x, y)
            if ok:
                return x, y
    return None


def _seed_place(structures: dict, kind: str, level: int = 1) -> Optional[dict]:
    """Place an OPERATIONAL building of `kind` on the first fitting plot (complete_at=None). Returns
    the building or None if the grid has no room (a too-small grid simply seeds fewer buildings)."""
    spot = _seed_find_spot(structures, kind)
    if spot is None:
        return None
    return place(structures, kind, spot[0], spot[1], level=int(level))


def _seed_buildings_from_legacy(planet, structures: dict) -> None:
    """Cold-start the grid's BUILDINGS from the legacy scalars so the shadow derivation reproduces
    the shipped ladder: place HAB_DOME(s) + economy + research + per-tier key buildings + defense so
    ``derive_citadel_level(structures) == planet.citadel_level`` (K1b-1 calibration target, spec
    §1.2). Idempotent: no-op if buildings already present. A planet with citadel_level 0
    (forming/uncolonized) seeds no buildings."""
    if structures.get("buildings"):
        return
    L = int(getattr(planet, "citadel_level", 0) or 0)
    if L < 1:
        return

    from src.services import building_catalog

    # Economy floor — at least one (the L1 key needs ≥1 economy); upgrade to the legacy levels.
    _seed_place(structures, "MINE", max(1, int(getattr(planet, "mine_level", 0) or 0)))
    if int(getattr(planet, "farm_level", 0) or 0) > 0:
        _seed_place(structures, "FARM", int(planet.farm_level))
    if int(getattr(planet, "factory_level", 0) or 0) > 0:
        _seed_place(structures, "FABRICATOR", int(planet.factory_level))
    if int(getattr(planet, "research_level", 0) or 0) > 0:
        _seed_place(structures, "LAB", int(planet.research_level))

    # Housing — one dome (two at L3+), sized to cover HOUSING[L] (HAB_DOME caps 50k/level).
    cap_per = int(((building_catalog.get("HAB_DOME") or {}).get("effect") or {}).get("capacity_per_level", 50000)) or 50000
    dome_count = 2 if L >= 3 else 1
    need = HOUSING.get(L, 0)
    per_dome = (need + dome_count - 1) // dome_count if need else 0
    dome_level = min(5, max(1, (per_dome + cap_per - 1) // cap_per)) if per_dome else 1
    for _ in range(dome_count):
        _seed_place(structures, "HAB_DOME", dome_level)

    # Per-tier key buildings (spec §1.2).
    if L >= 2:
        _seed_place(structures, "SCANNER_ARRAY", 1)
    if L >= 3:
        _seed_place(structures, "POWER_PLANT", 1)
    if L >= 4:
        _seed_place(structures, "SPACEPORT", 1)
        eco = sum(1 for b in structures["buildings"]
                  if (building_catalog.get(b.get("kind")) or {}).get("domain") == "economy")
        while eco < 3 and _seed_place(structures, "FABRICATOR", 1) is not None:
            eco += 1
    if L >= 5:
        _seed_place(structures, "ADMIN_SPIRE", 1)

    # Defense from the shipped active_events['defense_buildings'] counts (CT1 store).
    events = planet.active_events if isinstance(planet.active_events, dict) else {}
    dbld = events.get("defense_buildings") if isinstance(events.get("defense_buildings"), dict) else {}
    for kind in ("TURRET_NETWORK", "ORBITAL_PLATFORM", "SCANNER_ARRAY"):
        for _ in range(int(dbld.get(kind.lower(), 0) or 0)):
            _seed_place(structures, kind, 1)

    # Floor-area backfill — if still short of FLOOR_AREA[L], add baseline MINEs until met or full.
    guard = 0
    while powered_floor_area(structures) < FLOOR_AREA.get(L, 0) and guard < 40:
        if _seed_place(structures, "MINE", 1) is None:
            break
        guard += 1


def seed(planet, *, db=None) -> dict:
    """Cold-start owner of Planet.structures for a planet with null/empty structures. Idempotent —
    if the spine anchor is already present, returns the existing dict unchanged (never re-seeds).

    Seeds: ``version``; ``terraform_meta.last_settle_at`` = the domain-consistent ``max()`` of the
    existing inner anchors (spec §6.2, computed ONCE, atomic with the first settle, I8); the grid
    (``grid``/``plots`` sized from ``Planet.size``, axes/hazard seeded from the dormant
    temperature/water_coverage/radiation_level columns — read-only mirrors thereafter); an empty
    ``buildings`` list + ``instability``; and a forward-metadata ``legacy_seed`` snapshot. Touches
    NO derived field (citadel_level/habitability/max_population/etc. stay exactly as stored) →
    reproduce-exactly (I10). Caller commits. Genesis creation routes through here so every new
    planet owns a structures column from birth.

    K1b-1: this builds the structural grid (plots placement can occupy) but does NOT yet seed
    buildings from legacy levels — that calibration (so derive_citadel_level == shipped) lands with
    the shadow-derive wiring."""
    # Fully IDEMPOTENT cold-start: each piece is guarded so re-calling seed() (incl. BACKFILLING a
    # planet seeded before the grid code landed — anchor present, grid/buildings missing) adds only
    # what's absent. The spine anchor is stamped EXACTLY ONCE (never re-stamped — I8).
    base = dict(planet.structures) if isinstance(planet.structures, dict) else {}
    base.setdefault("version", 1)
    tmeta = dict(base.get("terraform_meta")) if isinstance(base.get("terraform_meta"), dict) else {}
    if not tmeta.get("last_settle_at"):
        tmeta["last_settle_at"] = _seed_anchor_value(planet).isoformat()
    base["terraform_meta"] = tmeta
    if not isinstance(base.get("grid"), dict) or not isinstance(base.get("plots"), list):
        cols, rows, count = _grid_dims_for(getattr(planet, "size", 5) or 5)
        base["grid"] = {"cols": cols, "rows": rows}
        base["plots"] = _seed_plots(planet, cols, rows, count)
    base.setdefault("buildings", [])
    base.setdefault("instability", 0)
    # K1b-1 calibration: cold-start the grid's buildings from the legacy scalars so the SHADOW
    # derive reproduces the shipped citadel_level. Touches only structures.buildings — no shipped
    # derived field (citadel_level/max_population/habitability) changes, so reproduce-exactly holds.
    _seed_buildings_from_legacy(planet, base)
    base.setdefault("legacy_seed", _legacy_layout_map(planet))
    planet.structures = base
    flag_modified(planet, "structures")
    return base


# ---------------------------------------------------------------------------
# Grid construction (K1b-1) — sized by Planet.size; plots are the scarce resource
# ---------------------------------------------------------------------------
def _grid_dims_for(size: int) -> tuple:
    """(cols, rows, plot_count) from Planet.size (1-10): plot_count = clamp(4 + 2·size, 6, 30)
    (CRT-MASTER §1.4, NO-CANON), laid out in a near-square cols×rows bounding box."""
    plot_count = max(6, min(30, 4 + 2 * int(size or 5)))
    cols = int(math.ceil(math.sqrt(plot_count)))
    rows = int(math.ceil(plot_count / cols))
    return cols, rows, plot_count


def _seed_terrain(planet) -> str:
    """Map the planet type to an initial plot terrain (a modifier, not a wall — §2.3). Conservative
    default FLAT; refined per-plot by terraform in T2."""
    t = (getattr(planet, "planet_type", None) or "").upper()
    mapping = {
        "VOLCANIC": "VOLCANIC_VENT", "OCEANIC": "COASTAL", "ICE": "FROZEN",
        "TERRAN": "FERTILE", "TERRA": "FERTILE", "DESERT": "ARID",
        "BARREN": "BARREN", "GAS": "FLAT", "ROCKY": "HIGHLAND",
    }
    for key, terrain in mapping.items():
        if key in t:
            return terrain
    return "FLAT"


def _seed_plots(planet, cols: int, rows: int, count: int) -> list:
    """Build the plot list (row-major, first `count` cells). Axes seeded from the dormant
    temperature/water_coverage columns (NO-CANON mapping — K1b-2 terraform owns the dynamics
    thereafter); a hazard from radiation_level makes a plot uncleared until cleared (§2.3)."""
    temperature = getattr(planet, "temperature", 0.0) or 0.0
    water = getattr(planet, "water_coverage", 0.0) or 0.0
    radiation = getattr(planet, "radiation_level", 0.0) or 0.0
    thermal = max(0, min(100, int(round(50 + temperature))))   # 0°C→50, ±50°C→0/100 (NO-CANON)
    hydro = max(0, min(100, int(round(water))))
    terrain = _seed_terrain(planet)
    # radiation_level (0-1) → hazard severity 1-3 above a threshold; hazardous plots start uncleared.
    hazard = None
    if radiation >= 0.34:
        hazard = {"kind": "radiation", "sev": min(3, max(1, int(round(radiation * 3))))}
    plots = []
    for i in range(count):
        x, y = i % cols, i // cols
        plots.append({
            "x": x, "y": y,
            "terrain": terrain,
            "hazard": dict(hazard) if hazard else None,
            "axes": {"thermal": thermal, "hydro": hydro},
            "axes_at": None,
            "cleared": hazard is None,
            "surveyed": False,
            "building_id": None,
        })
    return plots


# ---------------------------------------------------------------------------
# place() / decommission() — the grid-mutation API (K1b-1). PURE grid ops on the
# structures dict; the CALLER owns tech_gate/cost/lock checks + commit.
# ---------------------------------------------------------------------------
def _plot_index(structures: dict) -> dict:
    return {(p["x"], p["y"]): p for p in structures.get("plots", []) if isinstance(p, dict)}


def _footprint_cells(x: int, y: int, kind: str) -> list:
    from src.services import building_catalog
    spec = building_catalog.get(kind)
    if not spec:
        return []
    w, h = spec["footprint"]
    return [(x + dx, y + dy) for dy in range(int(h)) for dx in range(int(w))]


def can_place(structures: dict, kind: str, x: int, y: int) -> tuple:
    """Validate a placement (§2.2 invariants). Returns (ok: bool, reason: str). Pure read."""
    from src.services import building_catalog
    spec = building_catalog.get(kind)
    if not spec:
        return False, f"unknown building kind {kind!r}"
    cells = _footprint_cells(x, y, kind)
    if not cells:
        return False, "empty footprint"
    plots = _plot_index(structures)
    for (cx, cy) in cells:
        plot = plots.get((cx, cy))
        if plot is None:
            return False, f"cell ({cx},{cy}) off-grid"
        if not plot.get("cleared") or plot.get("hazard") is not None:
            return False, f"cell ({cx},{cy}) not cleared (hazard/blocked)"
        if plot.get("building_id") is not None:
            return False, f"cell ({cx},{cy}) already occupied"
    # prereqs: each prereq KIND must already exist on the planet
    present = {b.get("kind") for b in structures.get("buildings", []) if isinstance(b, dict)}
    for pre in spec.get("prereqs", []):
        if pre not in present:
            return False, f"missing prerequisite building {pre}"
    return True, "ok"


def _next_building_id(structures: dict) -> str:
    existing = [b.get("id", "") for b in structures.get("buildings", []) if isinstance(b, dict)]
    n = 0
    for bid in existing:
        if isinstance(bid, str) and bid.startswith("b_"):
            try:
                n = max(n, int(bid[2:]))
            except ValueError:
                pass
    return f"b_{n + 1}"


def place(structures: dict, kind: str, x: int, y: int, *, level: int = 1,
          now_iso: Optional[str] = None, complete_at: Optional[str] = None) -> dict:
    """Place a building on the grid (validates §2.2; raises ValueError if invalid). PURE grid op —
    the caller checks tech_gate (point-of-use), debits cost (planet-then-player lock order), and
    commits. Mutates ``structures`` in place; the caller flag_modifies + persists. Returns the new
    building dict. The building is enqueued (``complete_at`` set) and goes operational in settle()
    step 1 when ``now >= complete_at``."""
    from src.services import building_catalog
    ok, reason = can_place(structures, kind, x, y)
    if not ok:
        raise ValueError(f"cannot place {kind} at ({x},{y}): {reason}")
    spec = building_catalog.get(kind)
    bid = _next_building_id(structures)
    building = {
        "id": bid,
        "domain": spec["domain"],
        "kind": kind,
        "x": x, "y": y,
        "level": int(level),
        "crew": spec["crew"].get(level, 0),
        "power_draw": spec["power_draw"].get(level, 0),
        "condition": 100,
        "built_at": now_iso,
        "complete_at": complete_at,   # None == already operational
    }
    structures.setdefault("buildings", []).append(building)
    plots = _plot_index(structures)
    for (cx, cy) in _footprint_cells(x, y, kind):
        plots[(cx, cy)]["building_id"] = bid
    return building


# ---------------------------------------------------------------------------
# derive_citadel_level (K1b-1) — the SHADOW faucet (spec §2.1). A pure read of the
# placed/powered/populated grid that reproduces the shipped CITADEL_LEVELS ladder.
# DORMANT: not wired into settle() yet, and not yet calibrated against the live planet
# distribution (that lands with seed-buildings-from-legacy). The button stays authoritative.
# ---------------------------------------------------------------------------
# NO-CANON derivation thresholds (spec §1.2 "tuned to reproduce the ladder"). These are
# PLACEHOLDERS — the calibration target is derive_citadel_level(seed(legacy)) == the planet's
# shipped citadel_level for every live planet, tuned with the seed-buildings step. Propose for bless.
FLOOR_AREA = {1: 2, 2: 4, 3: 8, 4: 14, 5: 24}        # Σ(footprint cells × level) over operational eco/civic
HOUSING = {1: 0, 2: 0, 3: 50000, 4: 100000, 5: 200000}  # Σ powered HAB_DOME capacity


def _operational(b: dict) -> bool:
    """A building counts once it has gone operational (build-queue complete) and is not browned out
    (the brown-out floor is K1b power/crew; absent here a building simply counts)."""
    return isinstance(b, dict) and b.get("complete_at") is None and not b.get("browned_out")


def powered_floor_area(structures: dict) -> int:
    """Σ (footprint cells × level) over operational, not-browned-out economy/civic buildings
    (spec §1.2). A browned-out grid derives DOWN — a legible penalty."""
    from src.services import building_catalog
    total = 0
    for b in structures.get("buildings", []):
        if not _operational(b):
            continue
        spec = building_catalog.get(b.get("kind"))
        if spec and spec["domain"] in ("economy", "civic"):
            w, h = spec["footprint"]
            total += int(w) * int(h) * int(b.get("level", 1))
    return total


def population_housed(structures: dict) -> int:
    """Σ HAB_DOME housing capacity over operational, powered domes (the L-derivation demographic
    input, spec §1.2)."""
    from src.services import building_catalog
    spec = building_catalog.get("HAB_DOME")
    cap_per_level = int(((spec or {}).get("effect") or {}).get("capacity_per_level", 0))
    total = 0
    for b in structures.get("buildings", []):
        if _operational(b) and b.get("kind") == "HAB_DOME":
            total += cap_per_level * int(b.get("level", 1))
    return total


def key_buildings_present(structures: dict, level: int) -> bool:
    """The named per-tier gate buildings (spec §1.2), cumulative: a level requires all lower tiers'
    keys plus its own. This is what makes derivation read like progression, not an opaque area count."""
    from collections import Counter
    from src.services import building_catalog
    ops = [b for b in structures.get("buildings", []) if _operational(b)]
    cnt = Counter(b.get("kind") for b in ops)
    eco = sum(1 for b in ops if (building_catalog.get(b.get("kind")) or {}).get("domain") == "economy")
    if level >= 1 and not (cnt.get("HAB_DOME", 0) >= 1 and eco >= 1):
        return False
    if level >= 2 and not (cnt.get("SCANNER_ARRAY", 0) >= 1):
        return False
    if level >= 3 and not (cnt.get("HAB_DOME", 0) >= 2 and cnt.get("POWER_PLANT", 0) >= 1):
        return False
    if level >= 4 and not (cnt.get("SPACEPORT", 0) >= 1 and eco >= 3):
        return False
    if level >= 5 and not (cnt.get("ADMIN_SPIRE", 0) >= 1):
        return False
    return True


def derive_citadel_level(structures: dict) -> int:
    """Pure derivation of the citadel level from the placed/powered/populated grid (spec §2.1):
    L5→1, first match wins, on powered_floor_area ≥ FLOOR_AREA[L] AND key_buildings_present(L) AND
    population_housed ≥ HOUSING[L]. Returns 0 for an empty/ungridded planet. SHADOW only in K1b-1 —
    the shipped citadel_level column stays authoritative; settle() will log derived-vs-button
    divergence (the cutover is a separate Max-gated WO after calibration proves byte-identical)."""
    if not isinstance(structures, dict):
        return 0
    pfa = powered_floor_area(structures)
    housed = population_housed(structures)
    for level in (5, 4, 3, 2, 1):
        if pfa >= FLOOR_AREA[level] and housed >= HOUSING[level] and key_buildings_present(structures, level):
            return level
    return 0


def decommission(structures: dict, building_id: str) -> Optional[dict]:
    """Tear down a building and RECLAIM its plots (§2.3/§6.1 step 1). Returns the removed building
    dict, or None if not found. PURE grid op (no hab revert, no refund — the caller applies the
    partial refund per the rig-decommission rules). Caller commits."""
    buildings = structures.get("buildings", [])
    removed = None
    for b in buildings:
        if isinstance(b, dict) and b.get("id") == building_id:
            removed = b
            break
    if removed is None:
        return None
    structures["buildings"] = [b for b in buildings if b is not removed]
    for p in structures.get("plots", []):
        if isinstance(p, dict) and p.get("building_id") == building_id:
            p["building_id"] = None
    return removed


# ---------------------------------------------------------------------------
# The six steps — each CALLS the unchanged shipped body with NO `now` threaded in.
# ---------------------------------------------------------------------------
def _step1_build_queue(planet, db) -> bool:
    """KERNEL stub. K1b: complete_at → operational; decommission_at teardown + plot reclaim."""
    return False


def _step2_terraform(planet, ts) -> bool:
    """CALLS _advance_terraforming UNCHANGED (own canonical inner anchor last_tick_at)."""
    if not getattr(planet, "terraforming_active", False):
        return False
    return bool(ts._advance_terraforming(planet, _via_settle=True))


def _step3_power_siege(planet, ps) -> bool:
    """Siege morale substep FIRST (guarded), then the KERNEL near-empty reproduce-exactly derive
    (point-of-use reads; zero cross-write — citadel_level/habitability/capacity stay stored)."""
    if planet.under_siege and planet.siege_started_at:
        return bool(ps.advance_siege(planet, _via_settle=True))
    return False


def _step4_production(planet, ps) -> bool:
    """PRODUCTION accrual substep: CALLS apply_resource_production UNCHANGED (own WALL-CLOCK inner
    anchor last_production + production_carry; 24h wall cap)."""
    return bool(ps.apply_resource_production(planet, _via_settle=True))


def _step5_research(planet, db) -> bool:
    """RESEARCH sweep: CALLS sweep_research_faucet UNCHANGED (re-homed from scheduler :1758;
    drains research_points into Player.research_ledger; idempotent). Lock order: planet row held,
    then player row (acquired inside the faucet)."""
    from src.services.research_service import sweep_research_faucet
    return bool(sweep_research_faucet(db, planet, _via_settle=True))


def _step6_event_roll(planet, anchor_before: datetime, now: datetime) -> bool:
    """KERNEL stub. K1b: catastrophe/windfall keyed to (step-2 instability band, canonical window
    [anchor_before, now]); idempotent per spec §2.3."""
    return False


# ---------------------------------------------------------------------------
# THE SINGLE ENTRY-POINT
# ---------------------------------------------------------------------------
def settle(planet, now: Optional[datetime] = None, *, db=None) -> SettleResult:
    """The single deterministic, ordered, six-step planetary tick (spec §1, §4).

    Idempotent, monotonic. Each step CALLS the unchanged shipped body, which reads/advances its OWN
    inner anchor in its OWN clock domain. ``now`` is used ONLY by the spine gate + seed + (K1b)
    step-6 window key — NEVER passed into a legacy body. Single transaction; the CALLER commits
    (matching the per-planet commit/rollback discipline in _run_planetary_advance_sync).

    DORMANT in K1a-1: built + provable, but no call-site routes through it yet (cutover is
    Max-gated, spec §8)."""
    if db is None:
        raise ValueError("structures.settle() requires a db session to advance planetary clocks")

    now = _aware(now) if now is not None else _canonical_now()

    from src.services.planetary_service import PlanetaryService
    from src.services.terraforming_service import TerraformingService

    ps = PlanetaryService(db)
    ts = TerraformingService(db)

    anchor_before = _get_settle_anchor(planet)   # canonical/wall; seeded if null (§6.2)
    gated = now <= anchor_before                  # MONOTONIC GATE (wall-vs-wall, §1.4)

    steps_changed: Set[str] = set()
    # The six steps run even on a gated no-op (belt-and-suspenders, §1.4): each body independently
    # no-ops on its own inner anchor (elapsed<=0 / pending<=0 / ticks==0), so a stale call
    # regresses nothing.
    if _step1_build_queue(planet, db):
        steps_changed.add("build_queue")
    if _step2_terraform(planet, ts):
        steps_changed.add("terraform")
    if _step3_power_siege(planet, ps):
        steps_changed.add("siege")
    if _step4_production(planet, ps):
        steps_changed.add("production")
    if _step5_research(planet, db):
        steps_changed.add("research")
    if _step6_event_roll(planet, anchor_before, now):
        steps_changed.add("event_roll")

    if gated:
        # Spine no-op: do NOT advance the spine anchor (the spine saw this instant already).
        # The DISCRETE bodies (terraform/siege/research/event) gate on their own inner anchors, so
        # on a healthy gated call they no-op; if one moved anyway that is a genuine anomaly worth a
        # WARN breadcrumb. PRODUCTION is CONTINUOUS wall-clock — it reads real-now (never the passed
        # `now`), so it legitimately accrues real elapsed production even on a stale/duplicate
        # gated call (no double-credit: last_production only advances by consumed window). That is
        # EXPECTED, not an anomaly — DEBUG only, no spam.
        discrete_changed = steps_changed - {"production"}
        if discrete_changed:
            logger.warning(
                "settle() gated (now<=last_settle_at) but discrete bodies changed on planet %s: %s",
                getattr(planet, "id", "?"), sorted(discrete_changed),
            )
        elif steps_changed:
            logger.debug(
                "settle() gated; continuous production accrued on planet %s (expected on a "
                "duplicate/stale-now call)", getattr(planet, "id", "?"),
            )
        return SettleResult.noop()

    _set_settle_anchor(planet, now)              # advance spine anchor to `now` (NOT capped, §1.4)
    window = max(0.0, (now - anchor_before).total_seconds())
    return SettleResult(changed=True, steps_changed=steps_changed, window_consumed_seconds=window)
