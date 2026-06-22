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

from src.core.game_time import GAME_TIME_SCALE, canonical_hours_since

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

# CRT-1 CUTOVER FLAG (Max-ruled 2026-06-21): when True, settle()'s step-3 derive becomes
# AUTHORITATIVE — derive_citadel_level over the grid WRITES planet.citadel_level (+ the three caps)
# instead of merely shadow-logging the divergence. The size-gated upgrade ladder
# (max_citadel_level_for_size) makes derive a faithful inverse by construction, so this never
# regresses a legitimately-built citadel. False = the original read-only K1b-1 shadow.
# Cutover (WO-CRT-1): TRUE = derive is AUTHORITATIVE (settle writes citadel_level
# from the grid). REQUIRED per-env before flipping: a one-time backfill that
# FULL-re-seeds (structures=None → seed()) every divergent citadel planet so
# derive==shipped (clearing only buildings re-seeds onto stale plots and fails —
# use a full re-seed). Verified 0-divergence on dev before this flip. The
# derived>=1 floor (below) + _get_settle_anchor's empty-grid backfill guard the
# residual cases; a populated-but-suboptimal grid still needs the backfill.
CITADEL_DERIVE_AUTHORITATIVE = True


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
    """Read the spine anchor; cold-start via seed() if the anchor OR the grid is missing, AND
    backfill the buildings list of a citadel'd planet whose grid exists but is empty (seed() no-ops
    once the anchor is stamped, so the empty-buildings case is backfilled directly via
    _seed_buildings_from_legacy — idempotent). This keeps derive_citadel_level from returning 0 on a
    planet seeded before the building-backfill landed (which, post-cutover, would wipe its citadel).
    Caller commits — the seed/backfill is atomic with the settle()."""
    anchor = _read_settle_anchor(planet)
    grid_present = isinstance(planet.structures, dict) and isinstance(planet.structures.get("grid"), dict)
    if anchor is None or not grid_present:
        seed(planet)
        anchor = _read_settle_anchor(planet)
    # CRT-1: a citadel'd planet with a grid but an EMPTY buildings list (seeded
    # before the building-backfill landed, or a future buildings-cleared state)
    # would derive 0 → an authoritative WIPE. seed() above no-ops once the anchor
    # exists, so backfill the buildings directly. _seed_buildings_from_legacy is
    # idempotent (returns immediately if buildings are already present), so this
    # only fires on the empty-grid case and never re-places a built planet.
    if (int(getattr(planet, "citadel_level", 0) or 0) >= 1
            and isinstance(planet.structures, dict)
            and not planet.structures.get("buildings")):
        _seed_buildings_from_legacy(planet, planet.structures)
    return anchor


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


def _footprint_area(kind: str) -> int:
    """Plot-cell count of `kind`'s footprint (w×h), 0 for an unknown kind."""
    from src.services import building_catalog
    spec = building_catalog.get(kind)
    if not spec:
        return 0
    w, h = spec["footprint"]
    return int(w) * int(h)


def _place_largest_first(structures: dict, requests: list) -> list:
    """LARGEST-FOOTPRINT-FIRST, prereq-aware placement of a LIST of ``(kind, level)`` requests
    (CRT-1 GATE fix). The first-fit ``_seed_place`` placed small [1,1] buildings top-left first,
    fragmenting tight grids so a multi-cell block (ADMIN_SPIRE [2,2], SPACEPORT [2,1]) could no
    longer find a contiguous rectangle → ``key_buildings_present`` failed → ``derive_citadel_level``
    capped below the true level. Reserving the largest footprints FIRST lets the big blocks claim
    their contiguous space before the [1,1] fill fragments the grid.

    Placement order ≠ building SET: this changes only WHERE (which plots) each building lands, never
    WHICH buildings are placed, so ``derive_citadel_level`` (position-agnostic) is unchanged on grids
    where everything already fit — and ENABLED to reach the top tier on tight grids where the spire
    previously could not pack.

    Prereq-aware: a request whose ``prereqs`` are not yet on the grid is deferred (ADMIN_SPIRE
    requires SPACEPORT) — at each step we place the LARGEST request whose prereqs are already
    satisfied, repeating until none remain placeable. So SPACEPORT lands, THEN the larger spire claims
    its 2×2 block before any 1×1 fill. Returns ``[(kind, level, placed_bool), ...]`` for callers that
    care; a building the grid had no room for is reported ``placed_bool=False`` (and simply absent)."""
    from src.services import building_catalog
    placed = []
    pending = [(k, int(lv)) for (k, lv) in requests]
    while pending:
        present = {b.get("kind") for b in structures.get("buildings", []) if isinstance(b, dict)}
        ready = [
            (k, lv) for (k, lv) in pending
            if all(pre in present for pre in (building_catalog.get(k) or {}).get("prereqs", []))
        ]
        if not ready:
            # Remaining requests can never satisfy their prereqs (the prereq itself failed to place,
            # e.g. no room for SPACEPORT → the spire is unreachable). Stop; the grid lacks room.
            break
        ready.sort(key=lambda r: _footprint_area(r[0]), reverse=True)
        kind, level = ready[0]
        pending.remove((kind, level))
        b = _seed_place(structures, kind, level)
        placed.append((kind, level, b is not None))
    return placed


def _dome_level_for_housing(target_level: int, dome_count: int) -> int:
    """The per-dome HAB_DOME level so ``dome_count`` domes cover HOUSING[target_level] (each dome
    houses ``capacity_per_level`` × level; default 50k/level). Shared seed/ensure dome-sizing math."""
    from src.services import building_catalog
    effect = (building_catalog.get("HAB_DOME") or {}).get("effect") or {}
    cap_per = int(effect.get("capacity_per_level", 50000)) or 50000
    need = HOUSING.get(int(target_level), 0)
    if not need or dome_count <= 0:
        return 1
    per_dome = (need + dome_count - 1) // dome_count
    return min(5, max(1, (per_dome + cap_per - 1) // cap_per))


def _key_building_requests(target_level: int) -> list:
    """The cumulative per-tier KEY-building ``(kind, level)`` request list for ``target_level``,
    mirroring ``key_buildings_present`` EXACTLY (same buildings, same counts). HAB_DOMEs are sized to
    cover HOUSING[target] via ``_dome_level_for_housing``; SPACEPORT/POWER_PLANT also satisfy the
    eco≥3 / eco≥1 demands of the gate (both are economy-domain). The list is intended for
    ``_place_largest_first`` (it carries the spire's SPACEPORT prereq for the prereq-aware order)."""
    L = int(target_level)
    dome_count = 2 if L >= 3 else 1
    dome_level = _dome_level_for_housing(L, dome_count)
    reqs = [("HAB_DOME", dome_level), ("MINE", 1)]      # L1: ≥1 dome + ≥1 economy
    if L >= 2:
        reqs.append(("SCANNER_ARRAY", 1))
    if L >= 3:
        reqs.append(("HAB_DOME", dome_level))           # 2nd dome
        reqs.append(("POWER_PLANT", 1))                 # economy + the L3 power key
    if L >= 4:
        reqs.append(("SPACEPORT", 1))                   # economy + the L4 spaceport key
    if L >= 5:
        reqs.append(("ADMIN_SPIRE", 1))                 # civic; prereq SPACEPORT (placed first)
    return reqs


def _operational_eco_count(structures: dict) -> int:
    """Count of operational economy-domain buildings (the eco≥N input to key_buildings_present)."""
    from src.services import building_catalog
    return sum(1 for b in structures.get("buildings", [])
               if _operational(b) and (building_catalog.get(b.get("kind")) or {}).get("domain") == "economy")


def _backfill_floor_area(structures: dict, target_level: int) -> None:
    """Raise ``powered_floor_area`` to FLOOR_AREA[target_level] by adding economy buildings — leveled
    so a TIGHT grid still reaches the target (PFA = footprint-cells × LEVEL, so a level-N MINE on one
    plot adds N). Place a MINE at the level that closes the remaining deficit (capped at max_level);
    when no plot remains, UPGRADE the lowest-level operational eco/civic building instead (no new
    cell). Stops when the target is met or neither a place nor an upgrade is possible (a genuinely
    too-small grid simply falls short — the (size,level) packing floor, never a number change)."""
    from src.services import building_catalog
    need = FLOOR_AREA.get(int(target_level), 0)
    guard = 0
    while powered_floor_area(structures) < need and guard < 300:
        guard += 1
        deficit = need - powered_floor_area(structures)
        mine_max = int((building_catalog.get("MINE") or {}).get("max_level", 5))
        level = min(mine_max, max(1, deficit))
        if _seed_place(structures, "MINE", level) is not None:
            continue
        # No room for a new building — upgrade the lowest-level operational eco/civic building.
        upgradable = [
            b for b in structures.get("buildings", [])
            if _operational(b)
            and (building_catalog.get(b.get("kind")) or {}).get("domain") in ("economy", "civic")
            and int(b.get("level", 1)) < int((building_catalog.get(b.get("kind")) or {}).get("max_level", 1))
        ]
        if not upgradable:
            break
        upgradable.sort(key=lambda b: int(b.get("level", 1)))
        upgradable[0]["level"] = int(upgradable[0].get("level", 1)) + 1


def _seed_buildings_from_legacy(planet, structures: dict) -> None:
    """Cold-start the grid's BUILDINGS from the legacy scalars so the shadow derivation reproduces
    the shipped ladder: place HAB_DOME(s) + economy + research + per-tier key buildings + defense so
    ``derive_citadel_level(structures) == planet.citadel_level`` (K1b-1 calibration target, spec
    §1.2). Idempotent: no-op if buildings already present. A planet with citadel_level 0
    (forming/uncolonized) seeds no buildings.

    CRT-1 GATE: all placements route through ``_place_largest_first`` (largest footprint reserved
    FIRST, prereq-aware) so the ADMIN_SPIRE [2,2] / SPACEPORT [2,1] blocks pack on tight grids that
    first-fit fragmented; the floor-area backfill is the shared leveled ``_backfill_floor_area``. The
    building SET is unchanged from the shipped seed (same kinds, same counts) — only the placement
    ORDER and the backfill LEVELING change, so reproduce-exactly holds (positions may move; the
    derived level does not, and now matches on small grids where the spire previously could not fit)."""
    if structures.get("buildings"):
        return
    L = int(getattr(planet, "citadel_level", 0) or 0)
    if L < 1:
        return

    requests: list = []

    # Economy floor — at least one (the L1 key needs ≥1 economy); upgrade to the legacy levels.
    requests.append(("MINE", max(1, int(getattr(planet, "mine_level", 0) or 0))))
    if int(getattr(planet, "farm_level", 0) or 0) > 0:
        requests.append(("FARM", int(planet.farm_level)))
    if int(getattr(planet, "factory_level", 0) or 0) > 0:
        requests.append(("FABRICATOR", int(planet.factory_level)))
    if int(getattr(planet, "research_level", 0) or 0) > 0:
        requests.append(("LAB", int(planet.research_level)))

    # Housing — one dome (two at L3+), sized to cover HOUSING[L] (shared dome-sizing math).
    dome_count = 2 if L >= 3 else 1
    dome_level = _dome_level_for_housing(L, dome_count)
    for _ in range(dome_count):
        requests.append(("HAB_DOME", dome_level))

    # Per-tier key buildings (spec §1.2). SPACEPORT/POWER_PLANT are economy-domain (count toward eco≥3).
    if L >= 2:
        requests.append(("SCANNER_ARRAY", 1))
    if L >= 3:
        requests.append(("POWER_PLANT", 1))
    if L >= 4:
        requests.append(("SPACEPORT", 1))
    if L >= 5:
        requests.append(("ADMIN_SPIRE", 1))   # prereq SPACEPORT — _place_largest_first orders it

    # Place the whole SET largest-footprint-first (reserves the 2×2 spire / 2×1 spaceport blocks).
    _place_largest_first(structures, requests)

    # eco≥3 for L4+ (POWER_PLANT + SPACEPORT are 2 eco; top up with MINEs to reach 3).
    if L >= 4:
        while _operational_eco_count(structures) < 3 and _seed_place(structures, "FABRICATOR", 1) is not None:
            pass

    # Defense from the shipped active_events['defense_buildings'] counts (CT1 store).
    events = planet.active_events if isinstance(planet.active_events, dict) else {}
    dbld = events.get("defense_buildings") if isinstance(events.get("defense_buildings"), dict) else {}
    for kind in ("TURRET_NETWORK", "ORBITAL_PLATFORM", "SCANNER_ARRAY"):
        for _ in range(int(dbld.get(kind.lower(), 0) or 0)):
            _seed_place(structures, kind, 1)

    # Floor-area backfill — leveled so tight grids still reach FLOOR_AREA[L] (shared helper).
    _backfill_floor_area(structures, L)


def ensure_citadel_level(structures: dict, planet, target_level: int) -> None:
    """Make ``derive_citadel_level(structures) >= target_level`` on a grid with room — IDEMPOTENT
    and ADDITIVE (CRT-1 GATE). Counts the buildings already operational on the grid (same operational
    / economy-count logic as ``key_buildings_present``) and places ONLY what is MISSING:
      * the cumulative per-tier KEY buildings (``_key_building_requests`` — mirrors
        ``key_buildings_present`` exactly), sized for HOUSING[target] domes;
      * an eco≥3 top-up of MINEs for L4+ (POWER_PLANT + SPACEPORT count as 2 eco);
      * a leveled floor-area backfill to FLOOR_AREA[target] (``_backfill_floor_area``).
    Every placement routes through ``_place_largest_first`` so the multi-cell ADMIN_SPIRE / SPACEPORT
    blocks pack before [1,1] fill fragments a tight grid.

    Re-running is a no-op once the target is met (missing-only requests resolve to nothing new; the
    floor-area loop exits immediately). Does NOT change any canon number — FLOOR_AREA/HOUSING/the key
    ladder are untouched; it only PLACES buildings so the ladder's own gates are satisfied. On a grid
    too small to pack the tier (the (size,level) packing floor) it falls short rather than inventing
    capacity — the caller/test treats that as the intrinsic limit."""
    if not isinstance(structures, dict):
        return
    L = int(target_level)
    if L < 1:
        return
    from collections import Counter
    from src.services import building_catalog

    # What is already operational on the grid (additive: never duplicate an existing key building).
    ops = [b for b in structures.get("buildings", []) if _operational(b)]
    have = Counter(b.get("kind") for b in ops)

    # Desired cumulative key set, then subtract what we already have (place only the deficit).
    want = Counter()
    for kind, level in _key_building_requests(L):
        want[(kind, level)] += 1
    # Group desired counts by kind so we can compare to existing counts of that kind.
    desired_by_kind = Counter()
    level_by_kind = {}
    for (kind, level), n in want.items():
        desired_by_kind[kind] += n
        # remember the highest requested level per kind (housing domes carry the sizing)
        level_by_kind[kind] = max(level_by_kind.get(kind, 1), level)

    requests: list = []
    for kind, desired_n in desired_by_kind.items():
        missing = desired_n - int(have.get(kind, 0))
        for _ in range(max(0, missing)):
            requests.append((kind, level_by_kind[kind]))

    if requests:
        _place_largest_first(structures, requests)

    # eco≥3 for L4+ (additive — top up MINEs only if short).
    if L >= 4:
        while _operational_eco_count(structures) < 3 and _seed_place(structures, "FABRICATOR", 1) is not None:
            pass

    # If existing housing under-covers HOUSING[L] (e.g. pre-existing small domes), upgrade domes.
    need_house = HOUSING.get(L, 0)
    guard = 0
    while need_house and population_housed(structures) < need_house and guard < 50:
        guard += 1
        domes = [b for b in structures.get("buildings", [])
                 if _operational(b) and b.get("kind") == "HAB_DOME"
                 and int(b.get("level", 1)) < int((building_catalog.get("HAB_DOME") or {}).get("max_level", 5))]
        if not domes:
            if _seed_place(structures, "HAB_DOME", 1) is None:
                break
            continue
        domes.sort(key=lambda b: int(b.get("level", 1)))
        domes[0]["level"] = int(domes[0].get("level", 1)) + 1

    # Leveled floor-area backfill to FLOOR_AREA[L] (shared helper; idempotent once met).
    _backfill_floor_area(structures, L)


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
# Research gate at placement (K1b-4) — the point-of-use tech_gate enforcement that
# place()/can_place() leave to the caller ("the caller checks tech_gate (point-of-use)").
#
# GATE KEY RECONCILIATION: the grid catalog (building_catalog.py) carries the gate on each
# row as ``tech_gate: str|None`` (e.g. SPACEPORT "t.prod.2", RAIL_GUN "t.def.railgun.1"). The
# *separate* shipped DEFENSE_BUILDINGS dict in citadel_service.py uses ``research_node`` for its
# own placement flow — a different catalog with a different schema. For GRID content the one
# consistent key is the one these rows actually carry: ``tech_gate``. We read ``tech_gate`` here
# and document the divergence rather than inventing a second key on the grid rows.
#
# The check gates UP (a kind whose gate isn't researched is rejected) and never changes WHAT
# exists. ``place()``/``can_place()`` stay PURE — this is a SEPARATE helper the placement callers
# invoke with the owning player's researched set; it does NOT couple structures.py to the Player
# model (the caller resolves the set via research_service.ledger_of(player)["unlocked"]).
# ---------------------------------------------------------------------------
def kind_tech_gate(kind: str) -> Optional[str]:
    """The research node a kind requires to be BUILT, or None if ungated. Reads the grid catalog's
    ``tech_gate`` row key (NOT DEFENSE_BUILDINGS' ``research_node`` — that is a separate catalog).
    Unknown kind → None (place()/can_place() own the unknown-kind rejection)."""
    from src.services import building_catalog
    spec = building_catalog.get(kind)
    if not spec:
        return None
    return spec.get("tech_gate")


def research_satisfied_for_kind(kind: str, researched: Optional[Set[str]]) -> tuple:
    """(ok: bool, reason: str) — is the player's research sufficient to BUILD ``kind``? Gates UP:
    a kind whose ``tech_gate`` is set is buildable ONLY if that node is in ``researched``; a kind
    with ``tech_gate=None`` (or an unknown kind) is always research-satisfied. Pure read — does NOT
    touch the grid. ``researched`` is the player's unlocked-node set (research_service.ledger_of
    (player)["unlocked"]); a None/empty set means "nothing researched" (gates everything gated)."""
    gate = kind_tech_gate(kind)
    if gate is None:
        return True, "ok"
    if researched and gate in researched:
        return True, "ok"
    return False, f"requires research node {gate!r} to build {kind}"


def can_place_gated(structures: dict, kind: str, x: int, y: int,
                    researched: Optional[Set[str]]) -> tuple:
    """(ok, reason) — the research-gated placement check the callers use instead of bare
    ``can_place()``. Enforces the point-of-use tech_gate FIRST (gate UP), then the pure grid
    invariants of ``can_place()``. ``can_place()`` itself stays pure; this is the thin gated wrapper
    the placement callers invoke. ``researched`` = the owning player's unlocked-node set."""
    ok, reason = research_satisfied_for_kind(kind, researched)
    if not ok:
        return False, reason
    return can_place(structures, kind, x, y)


def assert_research_for_kind(kind: str, researched: Optional[Set[str]]) -> None:
    """Raise ValueError if the player's research does not gate-UP to BUILD ``kind``; no-op when
    satisfied (gate met or ungated). The raise mirrors place()'s ValueError-on-invalid contract so a
    placement caller can guard with this before calling place()."""
    ok, reason = research_satisfied_for_kind(kind, researched)
    if not ok:
        raise ValueError(f"cannot place {kind}: {reason}")


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

# CRT-1 SIZE-GATE (Max-ruled 2026-06-21): the cumulative key-building footprint each citadel level
# requires, in plot-cells. So derive_citadel_level is a FAITHFUL INVERSE by construction — a level
# is reachable on a grid IFF the grid has at least this many plots to pack the tier's key buildings:
#   L1 HAB_DOME(1)+MINE(1)=2 · L2 +SCANNER(1)=3 · L3 +HAB_DOME(1)+POWER(1)=5 ·
#   L4 +SPACEPORT(2)=7 (eco POWER+SPACEPORT+MINE) · L5 +ADMIN_SPIRE(2×2=4)=11.
# Single source of truth for the size→max-level cap (the calibration test imports this).
CITADEL_MIN_CELLS = {1: 2, 2: 3, 3: 5, 4: 7, 5: 11}


def max_citadel_level_for_size(size: int) -> int:
    """The highest citadel level (1..5) whose key-building footprint physically packs onto a planet
    of ``size``'s grid — i.e. the largest L where ``_grid_dims_for(size)[2] >= CITADEL_MIN_CELLS[L]``.
    Always ≥1 (every grid clamps to ≥6 plots, which packs L3). With the clamped grid sizing this
    yields s1→3, s2→4, s3→4, s4→5, s5-10→5: the size→max-level cap that makes derive_citadel_level
    a faithful inverse of the ladder by construction (a player can never start an upgrade the planet
    can't pack)."""
    plot_count = _grid_dims_for(size)[2]
    cap = 1
    for level in range(1, 6):
        if plot_count >= CITADEL_MIN_CELLS[level]:
            cap = level
    return cap


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


# ---------------------------------------------------------------------------
# Terraform field model on the grid (K1b-2, own-plot-flat kernel). SHADOW: advances the per-plot
# axes + instability and RETURNS the grid-derived habitability; the CALLER logs divergence vs the
# shipped habitability_score and does NOT write it (habitability-from-grid is a Max-gated ADR-0002
# amendment — same staging as the citadel button→derived cutover). NOT wired into settle() yet.
# ---------------------------------------------------------------------------
# natural_band decay targets per planet type, {thermal, hydro}. Canon gives one habitability band
# per type (colonization.md: BARREN 0 · VOLCANIC 10-25 · OCEANIC 60-75 · ICE 35-50); the per-AXIS
# split is NO-CANON (conservative). Propose for bless.
NATURAL_BAND = {
    "BARREN": {"thermal": 0, "hydro": 0},
    "VOLCANIC": {"thermal": 25, "hydro": 10},
    "OCEANIC": {"thermal": 65, "hydro": 75},
    "ICE": {"thermal": 35, "hydro": 50},
    "DESERT": {"thermal": 60, "hydro": 10},
    "TERRAN": {"thermal": 55, "hydro": 55},
    "MOUNTAINOUS": {"thermal": 45, "hydro": 40},
}
_DEFAULT_NATURAL_BAND = {"thermal": 30, "hydro": 30}
TERRA_DECAY_RATE = 2                       # NO-CANON: axis points/tick toward natural_band when unfed
TERRA_INTENSITY_MULT = {"conservative": 0.5, "standard": 1.0, "aggressive": 1.5}  # NO-CANON
TERRA_UNFED_FLOOR = 0.4                    # NO-CANON: an unfed/browned-out rig pushes at 40% → net decay
TERRA_INSTAB_PEN_DIVISOR = 5              # NO-CANON: instability_penalty = instability // 5
TERRA_INSTAB_ACCRUAL = 0.5               # NO-CANON: aggressive instability += Σ(push) × this
TERRA_AXES = ("thermal", "hydro")        # the kernel's two axes (atmo/biosphere are T2)
GRID_TICK_PERIOD_HOURS = 1.0   # K1b-2 CUTOVER: 1 grid field tick = 1 canonical hour (mirrors the
                               # legacy terraform tick cadence so habitability advances time-accurately)
GRID_TICK_CAP = 2000           # safety cap on ticks applied in one settle (a long-dormant planet
                               # can't run away / spin for ages on a single sweep)


def _planet_type_name(planet) -> Optional[str]:
    """Resolve the planet's type NAME for natural_band decay. The authoritative column is the
    ``type`` enum (Planet.type); the ``planet_type`` String is an often-null API-compat mirror. Read
    the enum first (its ``.name``), then the string, else None (→ _natural_band default band)."""
    t = getattr(planet, "type", None)
    if t is not None:
        return getattr(t, "name", None) or str(t)
    return getattr(planet, "planet_type", None)


def _natural_band(planet_type: Optional[str], axis: str) -> int:
    pt = (planet_type or "").upper()
    for key, bands in NATURAL_BAND.items():
        if key in pt:
            return int(bands.get(axis, _DEFAULT_NATURAL_BAND[axis]))
    return int(_DEFAULT_NATURAL_BAND[axis])


def terraform_grid_tick(structures: dict, planet_type: Optional[str], intensity: str = "standard") -> int:
    """One terraform field tick over the grid (K1b-2 own-plot-flat kernel, spec §2.2/§2.3/§2.6):
      * each operational ``domain:"terraform"`` rig pushes ITS OWN plot's axis, FLAT (no falloff) —
        an unfed/browned rig pushes at the floor;
      * every other plot's axes DECAY toward the type's natural_band (the loop-maker);
      * ``instability`` accrues with aggressive push, penalising habitability;
      * RETURNS grid habitability = floor(area-weighted mean of per-plot axes) − instability_penalty.

    Mutates structures.plots[].axes + structures.instability (the terraform field advancing — the
    grid is terraform's to write). SHADOW: does NOT write the shipped habitability_score column; the
    caller logs the divergence (Max-gated cutover). Idempotent shape: a rig-less planet simply decays
    toward natural_band each tick."""
    plots = {(p["x"], p["y"]): p for p in structures.get("plots", []) if isinstance(p, dict)}
    if not plots:
        return 0
    imult = float(TERRA_INTENSITY_MULT.get(intensity, 1.0))
    fed_plots = set()
    anchored_cells = set()   # K1b-3: Climate Anchor plots hold their axes (excluded from decay)
    push_total = 0.0
    for b in structures.get("buildings", []):
        # A built terraform rig pushes even when browned-out (at the floor, below) — so gate on
        # build-completion (complete_at is None), NOT _operational() (which excludes browned-out).
        if not (isinstance(b, dict) and b.get("domain") == "terraform" and b.get("complete_at") is None):
            continue
        cell = (b.get("x"), b.get("y"))
        # K1b-3 Climate Anchor: PINS its plot (no push) — recorded so the decay loop skips it.
        if (b.get("effect") or {}).get("kind") == "climate_anchor" or b.get("kind") == "CLIMATE_ANCHOR":
            anchored_cells.add(cell)
            continue
        plot = plots.get(cell)
        if plot is None:
            continue
        axis = b.get("axis", "thermal")
        factor = TERRA_UNFED_FLOOR if b.get("browned_out") else 1.0
        push = float(b.get("push_base", 1.0)) * int(b.get("level", 1)) * imult * factor
        ax = dict(plot.get("axes") or {})
        ax[axis] = max(0, min(100, int(round(ax.get(axis, 0) + push))))
        plot["axes"] = ax
        fed_plots.add(cell)
        push_total += push
    # decay unfed plots toward natural_band (anchored plots HOLD — K1b-3 Climate Anchor)
    for cell, plot in plots.items():
        if cell in fed_plots or cell in anchored_cells:
            continue
        ax = dict(plot.get("axes") or {})
        for axis in TERRA_AXES:
            cur = int(ax.get(axis, 0))
            target = _natural_band(planet_type, axis)
            if cur != target:
                step = min(TERRA_DECAY_RATE, abs(cur - target))
                ax[axis] = cur - step if cur > target else cur + step
        plot["axes"] = ax
    # instability (aggressive pushing destabilises)
    instab = float(structures.get("instability", 0) or 0)
    if intensity == "aggressive":
        instab += push_total * TERRA_INSTAB_ACCRUAL
    instab = max(0.0, min(100.0, instab))
    structures["instability"] = int(instab)
    return grid_habitability(structures) or 0


def grid_habitability(structures: dict) -> Optional[int]:
    """Pure READ of the current grid-derived habitability (K1b-2 SHADOW formula, spec §2.6):
    floor(area-weighted mean of per-plot {thermal,hydro} axes, flat 50/50) − instability//5. Does
    NOT mutate (no push/decay — unlike terraform_grid_tick, which calls this for its return) and
    never touches the shipped habitability_score column. The settle() step-2 shadow logs this vs the
    shipped column (habitability-from-grid is a Max-gated ADR-0002 amendment). Returns None when the
    grid has no plots (nothing to derive)."""
    plots = [p for p in structures.get("plots", []) if isinstance(p, dict)]
    if not plots:
        return None
    mean = sum((int(p.get("axes", {}).get("thermal", 0)) + int(p.get("axes", {}).get("hydro", 0))) / 2.0
               for p in plots) / len(plots)
    penalty = int(structures.get("instability", 0) or 0) // TERRA_INSTAB_PEN_DIVISOR
    return max(0, int(mean) - penalty)


BIOME_CONFIRM_TOLERANCE = 10   # NO-CANON: axis points within the target band to count as "confirmed"


def confirm_biome(structures: dict, target_biome: Optional[str]) -> dict:
    """K1b-5 capstone — PURE READ. Is the grid's area-weighted per-axis mean within the target
    biome's NATURAL_BAND (± BIOME_CONFIRM_TOLERANCE), and how many consecutive hold-ticks has it held
    (read from ``structures.terraform_meta['biome_hold'][target_biome]``, 0 if unmaintained)?

    Returns ``{confirmed, hold_ticks, axes}``. **NEVER writes planet.type** — the hold-tick
    maintenance + the planet.type biome-reclass are the Max-gated activation step (PL2-adjacent), NOT
    done here. This read is the thin capstone the CRT T1-exit gate confirms against."""
    plots = [p for p in structures.get("plots", []) if isinstance(p, dict)]
    if not plots:
        return {"confirmed": False, "hold_ticks": 0, "axes": {}}
    axes_mean = {
        axis: sum(int(p.get("axes", {}).get(axis, 0)) for p in plots) / len(plots)
        for axis in TERRA_AXES
    }
    confirmed = bool(target_biome) and all(
        abs(axes_mean[axis] - _natural_band(target_biome, axis)) <= BIOME_CONFIRM_TOLERANCE
        for axis in TERRA_AXES
    )
    tmeta = structures.get("terraform_meta") if isinstance(structures.get("terraform_meta"), dict) else {}
    hold = tmeta.get("biome_hold") if isinstance(tmeta.get("biome_hold"), dict) else {}
    hold_ticks = int(hold.get((target_biome or "").upper(), 0) or 0)
    return {"confirmed": confirmed, "hold_ticks": hold_ticks,
            "axes": {a: round(axes_mean[a], 1) for a in TERRA_AXES}}


# ---------------------------------------------------------------------------
# K1b-5 biome CAPSTONE — hold-tick maintenance + planet.type reclassification
# (CRT-3, folds WO-PL2; Max APPROVED the planet.type write + the default-TRUE flag).
# ---------------------------------------------------------------------------
# Single-target reclass map (PL2): a barren rock terraformed to its target band hardens to a real
# biome. Keyed by the PlanetType ENUM NAME (resolve via _planet_type_name(planet)). VOLCANIC/DESERT
# are themselves the NATURAL_BAND target the capstone confirms against (so BARREN→VOLCANIC means
# "hold the VOLCANIC band", ICE→DESERT means "hold the DESERT band").
BIOME_RECLASS_MAP = {"BARREN": "VOLCANIC", "ICE": "DESERT"}
# Spec 03-spec-terraform.md E4 / 04-implementation-plan.md: the capstone is held for CAPSTONE_HOLD_TICKS
# consecutive maintained ticks before the reclass ACTION is allowed.
CAPSTONE_HOLD_TICKS = 24
# Reversible flag (the ADR amendment — Max approved shipping it default TRUE). Flip to False to
# disable the capstone entirely (the endpoint 403s and reclass_planet_type no-ops) without a revert.
BIOME_RECLASS_ENABLED = True


def _maintain_biome_hold(planet, structures: dict, applied_ticks: int) -> None:
    """K1b-5 hold-tick MAINTENANCE — the WRITE half of the capstone (confirm_biome only READS the
    counter this maintains). Called from _step2_terraform AFTER the grid field advanced, and ONLY
    when ``applied_ticks > 0`` (a caught-up/duplicate settle applies 0 ticks → must NOT double-count
    the hold). For a planet whose type NAME is reclass-eligible:
      * grid still within the target band → increment ``terraform_meta.biome_hold[TARGET]`` by the
        applied ticks, capped at CAPSTONE_HOLD_TICKS (a maintained hold accrues toward the capstone);
      * grid drifted out of band → reset the counter to 0 (an unfed/decayed band loses its hold).
    Mutates via the dict-reassign + flag_modified discipline. Fully defensive — never breaks the tick."""
    if applied_ticks <= 0:
        return
    try:
        type_name = (_planet_type_name(planet) or "").upper()
        target = BIOME_RECLASS_MAP.get(type_name)
        if not target:
            return
        confirmed = bool(confirm_biome(structures, target)["confirmed"])
        tmeta = dict(structures.get("terraform_meta")) if isinstance(structures.get("terraform_meta"), dict) else {}
        hold = dict(tmeta.get("biome_hold")) if isinstance(tmeta.get("biome_hold"), dict) else {}
        if confirmed:
            # Credit at most +1 per settle (NOT +applied_ticks): we observe the
            # POST-advance confirmed state only once per settle, so a long-dormant
            # catch-up (many ticks) must NOT instantly grant the full hold just because
            # the band was reached on the final tick. Conservative + exploit-free; the
            # accrual unit is "confirmed settles" (NO-CANON — flagged to Max).
            current = int(hold.get(target, 0) or 0)
            hold[target] = min(CAPSTONE_HOLD_TICKS, current + 1)
        else:
            hold[target] = 0
        tmeta["biome_hold"] = hold
        structures["terraform_meta"] = tmeta
        flag_modified(planet, "structures")
    except Exception:
        logger.exception("K1b-5 biome-hold maintenance failed (non-fatal) for planet %s",
                         getattr(planet, "id", "?"))


def reclass_planet_type(planet) -> Optional[str]:
    """K1b-5 capstone ACTION (folds WO-PL2): if the planet has held its target biome band for
    ``CAPSTONE_HOLD_TICKS`` maintained ticks, reclassify ``planet.type`` to the hardened biome and
    return the new type NAME; otherwise return None. PURE w.r.t. structures (reads the grid + the
    maintained hold counter, writes NOTHING to structures) — it sets only ``planet.type`` (and the
    ``planet_type`` String API-mirror). The CALLER commits. Production type-efficiency auto-recomputes
    via ``planetary_service.type_efficiency_for(planet.type)`` on the next production tick — no
    explicit recompute is needed. Gated behind the reversible BIOME_RECLASS_ENABLED flag."""
    if not BIOME_RECLASS_ENABLED:
        return None
    from src.models.planet import PlanetType
    type_name = (_planet_type_name(planet) or "").upper()
    target = BIOME_RECLASS_MAP.get(type_name)
    if not target:
        return None
    structures = planet.structures if isinstance(planet.structures, dict) else {}
    res = confirm_biome(structures, target)
    if res["confirmed"] and int(res["hold_ticks"]) >= CAPSTONE_HOLD_TICKS:
        planet.type = PlanetType[target]
        if hasattr(planet, "planet_type"):
            planet.planet_type = target   # API-compat String mirror (Planet.planet_type)
        return target
    return None


def _advance_grid_field(planet, structures: dict) -> int:
    """K1b-2 CUTOVER: advance the terraform grid field by the CANONICAL hours elapsed since its own
    wall-clock inner anchor ``terraform_meta.last_grid_tick_at`` — 1 tick = GRID_TICK_PERIOD_HOURS
    canonical hours, capped at GRID_TICK_CAP. OWN-ANCHOR-GATED + idempotent (a caught-up planet
    advances 0 ticks; a gated/duplicate settle no-ops), mirroring _advance_terraforming's discipline
    so habitability advances time-accurately rather than once-per-settle-call. Seeds the anchor once
    (no advance on the seeding call); consumes only the wall-time the applied ticks represent. Flags
    structures dirty when it mutates (like seed()); returns ticks applied (0 if seeded or caught-up)."""
    tmeta = dict(structures.get("terraform_meta")) if isinstance(structures.get("terraform_meta"), dict) else {}
    anchor_raw = tmeta.get("last_grid_tick_at")
    if not anchor_raw:
        tmeta["last_grid_tick_at"] = _canonical_now().isoformat()   # wall-clock now; seed once
        structures["terraform_meta"] = tmeta
        flag_modified(planet, "structures")
        return 0
    anchor = _aware(datetime.fromisoformat(anchor_raw))
    ticks = int(canonical_hours_since(anchor) // GRID_TICK_PERIOD_HOURS)
    if ticks <= 0:
        return 0                                                    # caught-up: no mutation
    ticks = min(ticks, GRID_TICK_CAP)
    pt = _planet_type_name(planet)
    for _ in range(ticks):
        terraform_grid_tick(structures, pt, "standard")
    wall_hours_consumed = (ticks * GRID_TICK_PERIOD_HOURS) / (GAME_TIME_SCALE or 1.0)
    tmeta["last_grid_tick_at"] = (anchor + timedelta(hours=wall_hours_consumed)).isoformat()
    structures["terraform_meta"] = tmeta
    flag_modified(planet, "structures")
    return ticks


def rebaseline_habitability_to_grid(db) -> dict:
    """K1b-2 CUTOVER one-time pass (Max-ruled 2026-06-21 fresh re-baseline): for every planet, ensure
    the grid is seeded, then set ``habitability_score = grid_habitability()`` of the current grid.
    Established planets drop to their grid value (terraform re-lifts them — accepted). Logs pre/post.
    Per-planet SAVEPOINT isolation (a bad planet rolls back only itself, like WO-B1). Idempotent:
    re-running re-reads the grid → same value. Returns {planets, changed}."""
    from src.models.planet import Planet
    result = {"planets": 0, "changed": 0}
    for planet in db.query(Planet).all():
        sp = db.begin_nested()
        try:
            seed(planet, db=db)   # idempotent: builds the grid if missing
            st = planet.structures if isinstance(planet.structures, dict) else {}
            new_hab = grid_habitability(st)
            if new_hab is None:
                sp.rollback()
                continue
            result["planets"] += 1
            old_hab = int(getattr(planet, "habitability_score", 0) or 0)
            if int(new_hab) != old_hab:
                logger.info("K1b-2 re-baseline: planet %s habitability %s -> %s (grid-derived)",
                            getattr(planet, "id", "?"), old_hab, int(new_hab))
                planet.habitability_score = int(new_hab)
                result["changed"] += 1
            sp.commit()
        except Exception:
            sp.rollback()
            logger.exception("K1b-2 re-baseline failed (skipped) for planet %s",
                             getattr(planet, "id", "?"))
    db.commit()
    return result


def place_terraform_preset(structures: dict, level: int) -> list:
    """Place the legacy terraforming level's rig BUNDLE on the grid (K1b-2 §1.3): the preserved
    ``start_terraforming(level=N)`` API becomes a bundle of THERMAL_RIG/HYDRO_PLANT rigs (N rigs,
    alternating axes, each at building-level N) that push the grid toward the legacy
    habitability_boost (L1+10 … L5+30). Reuses place(); stamps ``axis``/``push_base`` from the
    catalog onto each placed rig so terraform_grid_tick reads them. Returns the placed rigs (fewer
    than N if the grid runs out of room). Caller commits. NO-CANON: the rig→hab calibration
    (bundle composition vs the legacy boost ±2) is refined with the settle-wiring live-proof."""
    from src.services import building_catalog
    level = max(1, min(5, int(level)))
    kinds = ["THERMAL_RIG", "HYDRO_PLANT"]
    placed = []
    for i in range(level):
        kind = kinds[i % 2]
        spot = _seed_find_spot(structures, kind)
        if spot is None:
            break
        rig = place(structures, kind, spot[0], spot[1], level=level)
        spec = building_catalog.get(kind) or {}
        rig["axis"] = spec.get("push_axis", "thermal")
        rig["push_base"] = float(spec.get("push_base", 2.0))
        rig["intensity"] = "standard"
        placed.append(rig)
    return placed


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


DECOMM_REFUND_PCT = 0.25   # NO-CANON: salvage fraction of cumulative invested credits on decommission


def _invested_credits(kind: Optional[str], level: int) -> int:
    """Cumulative catalog credit cost for building ``kind`` across levels 1..level (the total credits
    sunk into it). Unknown kind / missing cost → 0."""
    from src.services import building_catalog
    spec = building_catalog.get(kind) or {}
    cost = spec.get("cost") or {}
    total = 0
    for L in range(1, int(level or 1) + 1):
        c = cost.get(L) or cost.get(str(L)) or {}
        total += int(c.get("credits", 0) or 0)
    return total


def decommission_with_refund(structures: dict, building_id: str,
                             refund_pct: float = DECOMM_REFUND_PCT) -> Optional[dict]:
    """K1b-3 refund kernel: decommission a building (via decommission()) AND compute its salvage
    refund = ``refund_pct`` × cumulative invested credits. Returns ``{removed, refund_credits}`` or
    None if not found. PURE — the CALLER credits the player + commits (no player write here). The
    refund fraction is NO-CANON (DECOMM_REFUND_PCT)."""
    removed = decommission(structures, building_id)
    if removed is None:
        return None
    invested = _invested_credits(removed.get("kind"), removed.get("level", 1))
    return {"removed": removed, "refund_credits": int(invested * refund_pct)}


# ---------------------------------------------------------------------------
# The six steps — each CALLS the unchanged shipped body with NO `now` threaded in.
# ---------------------------------------------------------------------------
def _step1_build_queue(planet, db) -> bool:
    """KERNEL stub. K1b: complete_at → operational; decommission_at teardown + plot reclaim."""
    return False


def _step2_terraform(planet, ts) -> bool:
    """K1b-2 CUTOVER (Max-ruled 2026-06-21, fresh re-baseline — "game is not released yet"):
    grid-habitability is AUTHORITATIVE, NO calibration to legacy. Three substeps:
      1. advance the legacy terraform body (still owns the terraforming_active lifecycle / target /
         resource costs on its own canonical anchor),
      2. advance the grid FIELD by canonical-elapsed (own-anchor-gated + idempotent, see
         _advance_grid_field) — push fed rigs, decay unfed plots toward natural_band, accrue
         instability,
      3. WRITE ``habitability_score = grid_habitability()`` (the post-advance grid value). Ends the
         read-only shadow.
    The own-plot-flat-mean value is accepted as-is (no legacy +10…+30 reproduction). Fully defensive:
    a grid hiccup never breaks the tick (the legacy advance already ran)."""
    changed = False
    if getattr(planet, "terraforming_active", False):
        changed = bool(ts._advance_terraforming(planet, _via_settle=True))
    try:
        st = planet.structures if isinstance(planet.structures, dict) else None
        if st is not None and grid_habitability(st) is not None:
            applied_ticks = _advance_grid_field(planet, st)   # flags structures itself when it mutates
            # K1b-5 biome capstone: maintain the hold-tick counter ONLY for ticks actually applied
            # (a caught-up/duplicate settle applies 0 → must not double-count the hold).
            _maintain_biome_hold(planet, st, applied_ticks)
            new_hab = grid_habitability(st)
            old_hab = int(getattr(planet, "habitability_score", 0) or 0)
            if new_hab is not None and int(new_hab) != old_hab:
                planet.habitability_score = int(new_hab)  # scalar column → dirtied on assign
                changed = True
    except Exception:
        logger.exception("K1b-2 grid field-advance/habitability-write failed (non-fatal) for planet %s",
                         getattr(planet, "id", "?"))
    return changed


def _step3_power_siege(planet, ps) -> bool:
    """Siege morale substep FIRST (guarded), then the KERNEL near-empty reproduce-exactly derive +
    the citadel derivation (SHADOW when CITADEL_DERIVE_AUTHORITATIVE is False, else AUTHORITATIVE)."""
    changed = False
    if planet.under_siege and planet.siege_started_at:
        changed = bool(ps.advance_siege(planet, _via_settle=True))
    # CRT-1 CUTOVER (Max-ruled 2026-06-21): compute derive_citadel_level over the (now-seeded) grid.
    #   * CITADEL_DERIVE_AUTHORITATIVE False → the original K1b-1 SHADOW: READ-ONLY, LOG divergence
    #     from the shipped citadel_level (button authoritative).
    #   * CITADEL_DERIVE_AUTHORITATIVE True → AUTHORITATIVE: on a divergence WRITE
    #     planet.citadel_level = derived AND recompute the three caps (citadel_safe_max /
    #     citadel_drone_capacity / citadel_max_population) from CITADEL_LEVELS[derived], matching the
    #     button's column mapping. The size-gated upgrade ladder keeps derive a faithful inverse, so
    #     this only ever corrects an un-packed grid, never regresses a legitimately-built citadel.
    # Fully defensive in BOTH modes: a derive hiccup must never break the tick. The grid was
    # backfilled by _get_settle_anchor before the steps run.
    try:
        derived = derive_citadel_level(planet.structures)
        shipped = int(getattr(planet, "citadel_level", 0) or 0)
        if derived != shipped:
            if CITADEL_DERIVE_AUTHORITATIVE and derived >= 1:
                # derived>=1 FLOOR (mirrors check_upgrade_completion's guard): NEVER
                # write a derived==0 — that would WIPE a real citadel whose grid is
                # empty/un-backfilled. An empty-grid citadel planet is re-seeded by
                # _get_settle_anchor before this runs; this floor is defense-in-depth.
                from src.services.citadel_service import CITADEL_LEVELS
                info = CITADEL_LEVELS.get(derived) or CITADEL_LEVELS[0]
                planet.citadel_level = derived
                planet.citadel_safe_max = info["safe_storage"]
                planet.citadel_drone_capacity = info["drone_capacity"]
                planet.citadel_max_population = info["max_population"]
                changed = True
                logger.info(
                    "citadel AUTHORITATIVE write: planet %s citadel_level %s -> %s "
                    "(grid-derived; caps recomputed)",
                    getattr(planet, "id", "?"), shipped, derived,
                )
            elif CITADEL_DERIVE_AUTHORITATIVE and derived < 1 and shipped >= 1:
                # Un-built grid on a real citadel — do NOT wipe; leave shipped + flag.
                logger.warning(
                    "citadel derive=0 on planet %s (shipped=%s) — grid unbuilt; "
                    "skipping authoritative write (needs backfill/seed)",
                    getattr(planet, "id", "?"), shipped,
                )
            elif not CITADEL_DERIVE_AUTHORITATIVE:
                logger.info(
                    "citadel SHADOW divergence: planet %s derived=%s vs shipped=%s "
                    "(button authoritative; cutover Max-gated)",
                    getattr(planet, "id", "?"), derived, shipped,
                )
    except Exception:
        logger.exception("citadel derive (shadow/authoritative) failed (non-fatal) for planet %s",
                         getattr(planet, "id", "?"))
    return changed


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
