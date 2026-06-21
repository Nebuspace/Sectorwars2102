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


def seed(planet, *, db=None) -> dict:
    """Cold-start owner of Planet.structures for a planet with null/empty structures. Idempotent —
    if the spine anchor is already present, returns the existing dict unchanged (never re-seeds).

    Seeds: ``version``; ``terraform_meta.last_settle_at`` = the domain-consistent ``max()`` of the
    existing inner anchors (spec §6.2, computed ONCE, atomic with the first settle, I8); and a
    forward-metadata ``legacy_seed`` snapshot for K1b. Touches NO derived field
    (citadel_level/habitability/max_population/etc. stay exactly as stored) → reproduce-exactly
    (I10). Caller commits. Genesis creation routes through here so every new planet owns a
    structures column from birth."""
    if isinstance(planet.structures, dict) and _read_settle_anchor(planet) is not None:
        return planet.structures
    base = dict(planet.structures) if isinstance(planet.structures, dict) else {}
    base.setdefault("version", 1)
    tmeta = dict(base.get("terraform_meta")) if isinstance(base.get("terraform_meta"), dict) else {}
    tmeta["last_settle_at"] = _seed_anchor_value(planet).isoformat()
    base["terraform_meta"] = tmeta
    base["legacy_seed"] = _legacy_layout_map(planet)
    planet.structures = base
    flag_modified(planet, "structures")
    return base


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
