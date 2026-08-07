"""Gameserver-canonical galaxy validation gate (ADR-0050 SK20, ADR-0069).

ADR-0050 SK20 requires: "the gameserver generator is canonical for runtime
behavior. Bang imports run the gameserver's own validation gate against the
imported content ... rule violation -> reject with
``ERR_BANG_VALIDATION_FAILED`` listing the failing invariants."

**Verify-first note (2026-08-05, WO-build-adr0050-sk20-bang-canonicality):**
the ADR text (and ``AISPEC/bang-integration.md:281``) call this "the
gameserver's own Phase 13 validators," implying a pre-existing native
generator validation phase that this module merely relocates. That code
never shipped: ``git log -S "Phase 13"`` across the whole history only ever
touches docs, never ``services/gameserver/src/**``, and the legacy
synchronous Python galaxy generator itself was fully removed in the Phase 4
bang cutover (see ``api/routes/admin.py::generate_galaxy``, HTTP 410). So
there is no native "Phase 13" module to extract — this file is a first
build of the invariant set ADR-0050 describes, derived from (a) the
structural assumptions the translator already relies on implicitly
(:mod:`src.services.bang_import_service`'s ``InsertPlan`` dataclasses,
``_validate_universe_shape``, ``_apply_terran_space_invariants``), and
(b) what a gameserver-generated galaxy must satisfy to function at all
(referential integrity between sectors/warps/clusters/stations/planets/
formations, structural counts, a unique capital sector). It is the best-faith
canonical equivalence gate available given the removal, not a literal
relocation.

The validated unit is :class:`~src.services.bang_import_service.InsertPlan`
-- the translator's own pure, DB-write-free intermediate representation
(``translate()``'s output, ``apply()``'s input). That is the actual
cleavage point ADR-0069 names as the gameserver-canonical boundary, so
validating there (rather than the raw bang JSON) means both bang's future
generation experiments *and* the translator's own field mapping are held to
one shared gate, per "Bang can experiment ... it cannot ship output the
gameserver wouldn't generate itself."

Deliberately duck-typed against the ``InsertPlan``/``RegionInsertPlan``
dataclass *shapes* (attribute access only, no runtime import of
``bang_import_service``) so this module can be imported from either side of
the translator boundary without a circular import.

**Known coverage gap (self-audit finding, 2026-08-05):** ADR-0069 names
station/planet inventories and ``NPCRoster`` rows as part of bang's
canonical snapshot contract alongside clusters/formations. ``InsertPlan``
does carry NPC-roster data (``raw_npc_rosters``) and station/planet
inventory fields, but no invariant here checks either -- only structural/
referential integrity for sectors/warps/clusters/stations/planets/
formations is covered (see ``_REGION_CHECKS`` below). NPC-roster
count-and-host-sector correctness and inventory-content validity remain
unvalidated by this gate today; a follow-on WO would extend the invariant
set to cover them.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Dict, Iterable, List

if TYPE_CHECKING:  # pragma: no cover - typing only, avoids runtime circular import
    from src.services.bang_import_service import InsertPlan, RegionInsertPlan


#: Error code surfaced to callers (job ``error_message`` prefix / API body)
#: per ADR-0050 SK20: "reject the import with ``ERR_BANG_VALIDATION_FAILED``
#: listing the failing invariants."
ERR_BANG_VALIDATION_FAILED = "ERR_BANG_VALIDATION_FAILED"


@dataclass
class InvariantFailure:
    """One failing invariant, itemized (never a generic bulk error)."""

    invariant: str
    region_type: str
    message: str
    data: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "invariant": self.invariant,
            "region_type": self.region_type,
            "message": self.message,
            "data": self.data,
        }


class GalaxyValidationError(ValueError):
    """Raised when a plan fails one or more canonical invariants.

    Carries the itemized :attr:`failures` list so callers (job error
    persistence, API error bodies) can surface each failing invariant
    individually rather than a single opaque message.
    """

    def __init__(self, failures: List[InvariantFailure]) -> None:
        self.failures = failures
        summary = "; ".join(f"[{f.region_type}] {f.invariant}: {f.message}" for f in failures)
        super().__init__(
            f"{ERR_BANG_VALIDATION_FAILED}: {len(failures)} invariant(s) failed: {summary}"
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "error": ERR_BANG_VALIDATION_FAILED,
            "failures": [f.to_dict() for f in self.failures],
        }


# ---------------------------------------------------------------------------
# Per-region invariant checks
# ---------------------------------------------------------------------------


def _check_sector_count(region: "RegionInsertPlan") -> Iterable[InvariantFailure]:
    """The materialised sector count must match the region's declared total.

    Mirrors (defense-in-depth against) ``_validate_universe_shape``'s check
    on the *raw* payload's ``totalSectors`` -- this re-checks the same
    invariant on the *translated* plan, catching a translator bug that
    drops or duplicates sectors between the shape check and here.
    """
    actual = len(region.sectors)
    if actual != region.total_sectors:
        yield InvariantFailure(
            invariant="sector_count",
            region_type=region.region_type,
            message=(
                f"declared total_sectors={region.total_sectors} but plan "
                f"materialised {actual} SectorSpec rows"
            ),
            data={"declared": region.total_sectors, "actual": actual},
        )


def _check_sector_numbers_unique_and_contiguous(
    region: "RegionInsertPlan",
) -> Iterable[InvariantFailure]:
    """Region-local ``sector_number`` must be a unique 1..N contiguous range.

    ``Sector.sector_number`` is the compound-key component every downstream
    consumer (capital anchoring, offset math, player-facing sector labels)
    assumes is dense and duplicate-free per region.
    """
    numbers = [s.sector_number for s in region.sectors]
    n = len(numbers)
    if n == 0:
        return
    duplicates = sorted({x for x in numbers if numbers.count(x) > 1})
    if duplicates:
        yield InvariantFailure(
            invariant="sector_number_uniqueness",
            region_type=region.region_type,
            message=f"duplicate sector_number values: {duplicates[:10]}",
            data={"duplicates": duplicates},
        )
    expected = set(range(1, n + 1))
    actual = set(numbers)
    missing = sorted(expected - actual)
    extra = sorted(actual - expected)
    if missing or extra:
        yield InvariantFailure(
            invariant="sector_number_contiguity",
            region_type=region.region_type,
            message=(
                f"sector_number set is not a contiguous 1..{n} range "
                f"(missing={missing[:10]}, out_of_range={extra[:10]})"
            ),
            data={"missing": missing, "out_of_range": extra},
        )


def _check_capital_sector(region: "RegionInsertPlan") -> Iterable[InvariantFailure]:
    """Exactly one sector is capital, and it matches the declared number."""
    capitals = [s for s in region.sectors if s.is_capital]
    if len(capitals) != 1:
        yield InvariantFailure(
            invariant="capital_sector_uniqueness",
            region_type=region.region_type,
            message=f"expected exactly 1 capital sector, found {len(capitals)}",
            data={"capital_sector_numbers": [s.sector_number for s in capitals]},
        )
        return
    if capitals[0].sector_number != region.capital_sector_number:
        yield InvariantFailure(
            invariant="capital_sector_number_match",
            region_type=region.region_type,
            message=(
                f"declared capital_sector_number={region.capital_sector_number} "
                f"but the is_capital-flagged sector is "
                f"sector_number={capitals[0].sector_number}"
            ),
            data={
                "declared": region.capital_sector_number,
                "actual": capitals[0].sector_number,
            },
        )


def _check_warp_referential_integrity(
    region: "RegionInsertPlan",
) -> Iterable[InvariantFailure]:
    """Every warp endpoint must reference a sector that exists in the plan."""
    sector_ids = {s.sector_id for s in region.sectors}
    dangling: List[Dict[str, int]] = []
    for w in region.warps:
        if w.from_sector_int not in sector_ids or w.to_sector_int not in sector_ids:
            dangling.append({"from": w.from_sector_int, "to": w.to_sector_int})
    if dangling:
        yield InvariantFailure(
            invariant="warp_referential_integrity",
            region_type=region.region_type,
            message=f"{len(dangling)} warp(s) reference a sector not in this region's plan",
            data={"dangling": dangling[:10], "total_dangling": len(dangling)},
        )


# NOTE: a "every sector reachable from the capital via the warp graph"
# invariant was drafted and dropped during verify-first testing against the
# real captured v1.3.0 fixtures (WO-build-adr0050-sk20-bang-canonicality,
# 2026-08-05): all three fixtures (player_owned, terran_space,
# central_nexus) legitimately contain sectors unreachable by plain warp
# traversal -- ADR-0070's cross-island warp restriction means islands are
# intentionally disconnected from the main graph pre-discovery (reachable
# only via later scan/quantum-tunnel/formation mechanics this module has no
# visibility into). A blanket reachability check would reject every real
# bang import. Left out rather than shipped wrong; a correct version would
# need to model per-island discovery, which is out of scope for this pass.


def _check_cluster_referential_integrity(
    region: "RegionInsertPlan",
) -> Iterable[InvariantFailure]:
    """Every sector's cluster must exist, and each cluster's declared
    ``sector_count`` must match the number of sectors that actually
    reference it."""
    cluster_ids = {c.cluster_int_id for c in region.clusters}
    orphaned = sorted({s.sector_id for s in region.sectors if s.cluster_int_id not in cluster_ids})
    if orphaned:
        yield InvariantFailure(
            invariant="cluster_referential_integrity",
            region_type=region.region_type,
            message=f"{len(orphaned)} sector(s) reference a cluster_int_id not in this plan",
            data={"orphaned_sector_ids": orphaned[:10]},
        )

    member_counts: Dict[int, int] = {}
    for s in region.sectors:
        member_counts[s.cluster_int_id] = member_counts.get(s.cluster_int_id, 0) + 1
    mismatched = []
    for c in region.clusters:
        actual = member_counts.get(c.cluster_int_id, 0)
        if actual != c.sector_count:
            mismatched.append(
                {"cluster_int_id": c.cluster_int_id, "declared": c.sector_count, "actual": actual}
            )
    if mismatched:
        yield InvariantFailure(
            invariant="cluster_sector_count_match",
            region_type=region.region_type,
            message=f"{len(mismatched)} cluster(s) declare a sector_count that doesn't match their members",
            data={"mismatched": mismatched[:10]},
        )


def _check_station_planet_referential_integrity(
    region: "RegionInsertPlan",
) -> Iterable[InvariantFailure]:
    """Stations and planets must sit in a sector that exists in the plan."""
    sector_ids = {s.sector_id for s in region.sectors}
    dangling_stations = sorted({st.sector_int_id for st in region.stations if st.sector_int_id not in sector_ids})
    if dangling_stations:
        yield InvariantFailure(
            invariant="station_referential_integrity",
            region_type=region.region_type,
            message=f"{len(dangling_stations)} station(s) reference a sector not in this plan",
            data={"dangling_sector_ids": dangling_stations[:10]},
        )
    dangling_planets = sorted({p.sector_int_id for p in region.planets if p.sector_int_id not in sector_ids})
    if dangling_planets:
        yield InvariantFailure(
            invariant="planet_referential_integrity",
            region_type=region.region_type,
            message=f"{len(dangling_planets)} planet(s) reference a sector not in this plan",
            data={"dangling_sector_ids": dangling_planets[:10]},
        )


def _check_formation_referential_integrity(
    region: "RegionInsertPlan",
) -> Iterable[InvariantFailure]:
    """Formation anchor + interior sectors must exist in the plan."""
    sector_ids = {s.sector_id for s in region.sectors}
    dangling: List[Dict[str, Any]] = []
    for f in region.formations:
        missing_interior = [i for i in f.interior_sector_ints if i not in sector_ids]
        if f.anchor_sector_int not in sector_ids or missing_interior:
            dangling.append(
                {
                    "formation_int_id": f.formation_int_id,
                    "anchor_missing": f.anchor_sector_int not in sector_ids,
                    "missing_interior": missing_interior[:10],
                }
            )
    if dangling:
        yield InvariantFailure(
            invariant="formation_referential_integrity",
            region_type=region.region_type,
            message=f"{len(dangling)} formation(s) reference a sector not in this plan",
            data={"dangling": dangling[:10]},
        )


#: Every per-region check the gate runs, in report order.
_REGION_CHECKS = (
    _check_sector_count,
    _check_sector_numbers_unique_and_contiguous,
    _check_capital_sector,
    _check_warp_referential_integrity,
    _check_cluster_referential_integrity,
    _check_station_planet_referential_integrity,
    _check_formation_referential_integrity,
)


def validate_insert_plan(plan: "InsertPlan") -> List[InvariantFailure]:
    """Run every canonical invariant against ``plan``; return all failures.

    Never raises -- callers that want reject-on-violation use
    :func:`validate_insert_plan_or_raise`. Runs every check for every
    region rather than short-circuiting on the first failure, so a caller
    gets the full itemized failure list in one pass (ADR-0050 SK20:
    "listing the failing invariants," plural).
    """
    failures: List[InvariantFailure] = []
    for region in plan.regions.values():
        for check in _REGION_CHECKS:
            failures.extend(check(region))
    return failures


def validate_region_plan(region: "RegionInsertPlan") -> List[InvariantFailure]:
    """Run every canonical invariant against a single region plan.

    Used by the single-region write paths (``apply_additional_region``'s
    "Add Player-Owned Region" flow) which never build a full multi-region
    :class:`InsertPlan`.
    """
    failures: List[InvariantFailure] = []
    for check in _REGION_CHECKS:
        failures.extend(check(region))
    return failures


def validate_region_plan_or_raise(region: "RegionInsertPlan") -> None:
    """Run :func:`validate_region_plan`; raise on any failure."""
    failures = validate_region_plan(region)
    if failures:
        raise GalaxyValidationError(failures)


def validate_insert_plan_or_raise(plan: "InsertPlan") -> None:
    """Run :func:`validate_insert_plan`; raise :class:`GalaxyValidationError`
    on any failure.

    This is the gate ADR-0050 SK20 describes: called by the translator's
    three write paths (``run_generation_job``, ``run_regeneration_job``,
    ``run_add_region_job``'s single-region counterpart
    :func:`validate_region_plan_or_raise`), after ``translate()`` and
    before ``apply()`` commits, so a rule violation aborts the import
    before any canonical row is written. **Not yet wired into the
    ``--validate-only`` preview path** (``validate_only()``) — that path
    still only surfaces bang's own report, not this gate's findings; a
    follow-on WO would give the preview path the same failure visibility
    the write paths get today.
    """
    failures = validate_insert_plan(plan)
    if failures:
        raise GalaxyValidationError(failures)


__all__ = [
    "ERR_BANG_VALIDATION_FAILED",
    "InvariantFailure",
    "GalaxyValidationError",
    "validate_insert_plan",
    "validate_insert_plan_or_raise",
]
