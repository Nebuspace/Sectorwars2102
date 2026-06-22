"""CRT-1 GATE calibration — derive_citadel_level is a FAITHFUL INVERSE of the citadel ladder.

The CRT-1 bug: first-fit (top-left) seed placement laid small [1,1] buildings first, fragmenting
tight grids so the multi-cell ADMIN_SPIRE [2,2] / SPACEPORT [2,1] blocks could no longer find a
contiguous rectangle → ``key_buildings_present`` failed → ``derive_citadel_level`` capped below the
true level (both live L5 planets diverged to 4). The fix is PLACEMENT only (no canon-number change):
``_place_largest_first`` reserves the largest footprints FIRST (prereq-aware so SPACEPORT lands
before the spire that requires it), and a LEVELED floor-area backfill lets a tight grid still reach
FLOOR_AREA (PFA = footprint-cells × level).

This test proves the inverse over the full (size 1..10) × (level 1..5) matrix, via BOTH faucets:
  * ``ensure_citadel_level(empty_grid, planet, L)`` — the target-driven builder, AND
  * ``seed(planet @ (size, L))`` — the cold-start legacy seed.
On every grid with ROOM the derived level must equal the target. The only cells that fall short are
the intrinsic PACKING FLOOR — sizes whose plot count is below the cumulative key-building footprint
for that level (L4 needs ≥7 plots, L5 needs ≥11; the tiniest planets cannot physically pack the
tier). Those are asserted as the KNOWN floor (and printed), so the matrix stays interpretable rather
than masking a regression.

DB-free / headless: ``flag_modified`` is stubbed (SimpleNamespace planets are not mapped instances)
and ``_seed_anchor_value`` is stubbed to a fixed instant (its real body imports planetary_service /
models purely for the siege anchor, irrelevant to a never-sieged calibration planet).
"""
import types
from datetime import datetime, UTC

import pytest

import src.services.structures as S


# Cumulative minimum plot-cells the key-building ladder occupies per level (footprint sums, with
# SPACEPORT/POWER_PLANT doubling as economy so eco≥3 needs only one extra MINE):
#   L1 HAB_DOME(1)+MINE(1)=2 · L2 +SCANNER(1)=3 · L3 +HAB_DOME(1)+POWER(1)=5 ·
#   L4 +SPACEPORT(2)=7 (eco: POWER+SPACEPORT+MINE) · L5 +ADMIN_SPIRE(2x2=4)=11.
# A grid whose plot count is below the level's minimum is the intrinsic packing FLOOR — no placement
# order can fit the tier. (Empirically verified to match the exact boundary: L4 floors size 1; L5
# floors sizes 1-3.) Imported from structures so there is ONE source of truth (CRT-1 SIZE-GATE).
MIN_CELLS = S.CITADEL_MIN_CELLS


@pytest.fixture(autouse=True)
def _headless(monkeypatch):
    """Stub the two surfaces that need a mapped instance / heavy imports (mirrors the spine test)."""
    monkeypatch.setattr(S, "flag_modified", lambda *a, **k: None)
    # _seed_anchor_value imports planetary_service for the siege anchor; a never-sieged calibration
    # planet doesn't need it — pin a deterministic instant so seed() stays DB-free.
    monkeypatch.setattr(S, "_seed_anchor_value", lambda planet: datetime(2026, 6, 21, tzinfo=UTC))


def _fake_planet(size, level, planet_type="TERRAN"):
    """A minimal SimpleNamespace planet carrying every scalar seed()/ensure read (all-zero legacy
    economy/research so the seed's building SET is the pure citadel ladder for the level)."""
    p = types.SimpleNamespace()
    p.structures = None
    p.last_production = None
    p.active_events = {}
    p.under_siege = False
    p.siege_started_at = None
    p.siege_turns = 0
    p.id = f"calib-s{size}-L{level}"
    for n in ("research_level", "factory_level", "farm_level", "mine_level",
              "defense_level", "defense_shields", "defense_fighters", "radiation_level"):
        setattr(p, n, 0)
    p.size = size
    p.citadel_level = level
    for n in ("terrain", "temperature", "water_coverage"):
        setattr(p, n, None)
    p.planet_type = planet_type
    return p


def _empty_grid(size):
    """A bare grid sized by the real ``_grid_dims_for`` helper — plots placement can occupy, no
    buildings yet (what seed() builds before _seed_buildings_from_legacy, minus the buildings)."""
    cols, rows, count = S._grid_dims_for(size)
    plots = S._seed_plots(_fake_planet(size, 0), cols, rows, count)
    return {"version": 1, "grid": {"cols": cols, "rows": rows}, "plots": plots,
            "buildings": [], "instability": 0}


def _packable(size, level):
    """Does size's grid have ROOM to pack the level's key-building footprint? (plot count ≥ floor)."""
    _, _, count = S._grid_dims_for(size)
    return count >= MIN_CELLS[level]


def _run_matrix(builder):
    """builder(size, level) -> structures dict already built to `level`. Returns (matrix, failures)
    where matrix[(size,level)] = derived level and failures lists any cell whose result contradicts
    the packability expectation (green-when-packable / floor-when-not)."""
    matrix = {}
    failures = []
    for level in range(1, 6):
        for size in range(1, 11):
            st = builder(size, level)
            derived = S.derive_citadel_level(st)
            matrix[(size, level)] = derived
            if _packable(size, level):
                if derived != level:
                    failures.append(f"PACKABLE s{size} L{level}: derived {derived} != {level}")
            else:
                # intrinsic packing floor: the tier cannot fit — must fall SHORT, never over-claim.
                if derived >= level:
                    failures.append(f"FLOOR s{size} L{level}: derived {derived} should be < {level}")
    return matrix, failures


def _print_matrix(title, matrix):
    print(f"\n=== {title} (derive vs target; * = intrinsic packing floor) ===")
    header = "      " + " ".join(f"s{s:<2}" for s in range(1, 11))
    print(header)
    for level in range(1, 6):
        cells = []
        for size in range(1, 11):
            d = matrix[(size, level)]
            if _packable(size, level):
                cells.append("OK " if d == level else f"!{d} ")
            else:
                cells.append(f"*{d} ")
        print(f"  L{level}: " + " ".join(c.ljust(3) for c in cells))


def _build_via_ensure(size, level):
    st = _empty_grid(size)
    S.ensure_citadel_level(st, _fake_planet(size, level), level)
    return st


def _build_via_seed(size, level):
    return S.seed(_fake_planet(size, level))


def test_ensure_citadel_level_is_faithful_inverse_matrix():
    matrix, failures = _run_matrix(_build_via_ensure)
    _print_matrix("ensure_citadel_level", matrix)
    assert not failures, "ensure_citadel_level calibration failures:\n" + "\n".join(failures)


def test_seed_legacy_is_faithful_inverse_matrix():
    matrix, failures = _run_matrix(_build_via_seed)
    _print_matrix("seed(planet@(size,L))", matrix)
    assert not failures, "seed() calibration failures:\n" + "\n".join(failures)


def test_ensure_and_seed_agree_where_packable():
    """The two faucets must derive the SAME level on every packable grid (one inverse, two paths)."""
    mismatches = []
    for level in range(1, 6):
        for size in range(1, 11):
            if not _packable(size, level):
                continue
            de = S.derive_citadel_level(_build_via_ensure(size, level))
            ds = S.derive_citadel_level(_build_via_seed(size, level))
            if de != ds:
                mismatches.append(f"s{size} L{level}: ensure={de} seed={ds}")
    assert not mismatches, "ensure/seed disagree:\n" + "\n".join(mismatches)


def test_ensure_is_idempotent():
    """Re-running ensure_citadel_level adds nothing and never lowers the derived level."""
    st = _empty_grid(8)
    S.ensure_citadel_level(st, _fake_planet(8, 5), 5)
    n1, d1 = len(st["buildings"]), S.derive_citadel_level(st)
    S.ensure_citadel_level(st, _fake_planet(8, 5), 5)
    n2, d2 = len(st["buildings"]), S.derive_citadel_level(st)
    assert (n1, d1) == (n2, d2) == (n2, 5)


def test_ensure_is_additive_l3_then_l5():
    """Building to L3 then to L5 reaches L5 on a grid with room (additive, not a rebuild)."""
    st = _empty_grid(9)
    S.ensure_citadel_level(st, _fake_planet(9, 3), 3)
    assert S.derive_citadel_level(st) == 3
    S.ensure_citadel_level(st, _fake_planet(9, 5), 5)
    assert S.derive_citadel_level(st) == 5


def test_admin_spire_packs_contiguously_on_tight_grid():
    """The 2x2 ADMIN_SPIRE must claim a contiguous block on the tightest L5-packable grid (size 4)."""
    st = _empty_grid(4)
    S.ensure_citadel_level(st, _fake_planet(4, 5), 5)
    spires = [b for b in st["buildings"] if b["kind"] == "ADMIN_SPIRE"]
    assert spires, "ADMIN_SPIRE not placed on the tightest packable grid (CRT-1 bug regressed)"
    b = spires[0]
    cells = S._footprint_cells(b["x"], b["y"], "ADMIN_SPIRE")
    idx = S._plot_index(st)
    assert len(cells) == 4
    assert all(idx.get(c, {}).get("building_id") == b["id"] for c in cells), \
        "ADMIN_SPIRE footprint not fully owned (non-contiguous placement)"


def test_largest_first_places_spire_before_small_fill():
    """Direct unit on _place_largest_first: on a grid where first-fit would fragment, the prereq-
    aware largest-first order places SPACEPORT then the 2x2 ADMIN_SPIRE successfully."""
    st = _empty_grid(5)
    placed = S._place_largest_first(st, [
        ("MINE", 1), ("MINE", 1), ("HAB_DOME", 1), ("HAB_DOME", 1),
        ("SCANNER_ARRAY", 1), ("POWER_PLANT", 1), ("SPACEPORT", 1), ("ADMIN_SPIRE", 1),
    ])
    placed_kinds = {k for (k, _lv, ok) in placed if ok}
    assert "SPACEPORT" in placed_kinds and "ADMIN_SPIRE" in placed_kinds, \
        f"largest-first failed to pack the multi-cell blocks: {placed}"
