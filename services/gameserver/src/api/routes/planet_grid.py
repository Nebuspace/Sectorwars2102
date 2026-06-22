"""
Planet grid management API (CRT-2 BUILD WORKER A).

Owner-only player endpoints over the now-AUTHORITATIVE citadel grid (CRT-1 shipped:
``structures.derive_citadel_level`` is authoritative + size-gated). These routes WIRE the
existing pure grid primitives in ``src.services.structures`` into player-facing actions; the
heavy logic (placement validity, research gate, refund maths, the level derivation) is already
built and lives in ``structures`` / ``building_catalog`` / ``research_service``.

Responsibility split (mirrors the structures.py docstrings):
  * ``structures.*`` stay PURE — they validate/mutate the ``planet.structures`` dict only.
  * THIS route owns the SIDE-EFFECTS: ownership/auth, the credit/material charge, the
    planet-row-then-player-row lock order (credit safety, matching ``planets.claim``), the
    ``flag_modified(planet, "structures")`` + ``db.commit()``.

Endpoints (all owner-only; 404 missing planet, 403 not owner):
  GET  /planets/{planet_id}/grid                — read the grid (seeds if structures is null)
  POST /planets/{planet_id}/grid/place          — research-gated, cost-charged placement
  POST /planets/{planet_id}/grid/decommission   — teardown with partial credit refund

Cost / refund / lock handling:
  * Cost credits come from ``building_catalog.get(kind)["cost"][level]["credits"]``, charged from
    the OWNING PLAYER's treasury (402 on insufficient funds). Per-planet MATERIALS in the cost row
    (e.g. MINE→fuel_ore, SPACEPORT→equipment, THERMAL_RIG→organics) are charged from the matching
    ``Planet`` integer columns when one exists and the planet holds enough; any material WITHOUT a
    matching column, or that the planet cannot afford, is surfaced as a ``materials_deferred`` flag
    in the response and credits-only are charged — NEVER silently skipped.
  * Refund = ``structures.decommission_with_refund`` (0.25 × cumulative invested credits), credited
    back to the player.
  * Lock order: planet row ``with_for_update()`` FIRST, then the player row ``with_for_update()``
    (mirrors ``planets.claim`` — planet before player). Commit at the route.
"""

import logging
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified
from pydantic import BaseModel, Field

from src.core.database import get_db
from src.auth.dependencies import get_current_player
from src.models.player import Player
from src.models.planet import Planet
from src.services import structures as structures_svc
from src.services import building_catalog

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/planets", tags=["planet-grid"])


# ---------------------------------------------------------------------------
# Request models (Pydantic, mirroring planets.py)
# ---------------------------------------------------------------------------
class GridPlaceRequest(BaseModel):
    """Place a building of ``kind`` at grid cell (x, y)."""
    kind: str = Field(..., min_length=1, max_length=64)
    x: int = Field(..., ge=0)
    y: int = Field(..., ge=0)
    level: int = Field(default=1, ge=1, le=10)


class GridDecommissionRequest(BaseModel):
    """Tear down the building with the given grid building id (e.g. ``b_3``)."""
    building_id: str = Field(..., min_length=1, max_length=64)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------
def _parse_planet_id(planet_id: str) -> UUID:
    try:
        return UUID(planet_id)
    except (ValueError, AttributeError, TypeError):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid planet ID format",
        )


def _researched_set(player: Player):
    """The owning player's unlocked research-node set, for the placement gate.

    ``research_service`` may be absent in a stripped build; treat any failure as an EMPTY set so
    gated kinds are rejected (fail-closed — never grant a gate the ledger can't prove).
    """
    try:
        from src.services import research_service
        if research_service is None:
            return set()
        led = research_service.ledger_of(player)
        unlocked = (led or {}).get("unlocked") if isinstance(led, dict) else None
        return set(unlocked or [])
    except Exception:
        logger.exception("research ledger read failed for player %s — gating closed",
                          getattr(player, "id", "?"))
        return set()


def _grid_payload(planet: Planet) -> dict:
    """The read-shape returned by every endpoint: grid dims, plots, buildings, the derived citadel
    level, the size cap, and (for placement) which research the owning player has. Pure read of the
    (already-seeded) ``planet.structures``."""
    st = planet.structures if isinstance(planet.structures, dict) else {}
    grid = st.get("grid") if isinstance(st.get("grid"), dict) else {"cols": 0, "rows": 0}
    return {
        "grid": {"cols": int(grid.get("cols", 0) or 0), "rows": int(grid.get("rows", 0) or 0)},
        "plots": st.get("plots", []) or [],
        "buildings": st.get("buildings", []) or [],
        "citadel_level": structures_svc.derive_citadel_level(st),
        "max_citadel_level": structures_svc.max_citadel_level_for_size(
            int(getattr(planet, "size", 5) or 5)
        ),
    }


def _ensure_seeded(planet: Planet, db: Session) -> None:
    """Cold-start the grid if ``planet.structures`` is null/empty so reads/writes have a grid to
    work on (idempotent — ``structures.seed`` no-ops once the spine anchor exists). Caller commits."""
    st = planet.structures
    if not (isinstance(st, dict) and isinstance(st.get("grid"), dict) and isinstance(st.get("plots"), list)):
        structures_svc.seed(planet, db=db)


def _charge_materials(planet: Planet, cost: dict) -> list:
    """Charge the per-planet MATERIALS in a cost row from the matching ``Planet`` integer columns
    (``fuel_ore`` / ``organics`` / ``equipment`` / ...) when straightforward; return a list of
    ``{material, required, available, reason}`` entries for any material that could NOT be charged
    (no matching column, or the planet holds too few). Credits are handled by the caller. NEVER
    silently skips a required material — every un-charged material lands in the returned list so the
    route can attach a ``materials_deferred`` flag.

    The caller has already locked the planet row; this mutates the planet's material columns in place.
    """
    deferred = []
    for material, required in (cost or {}).items():
        if material == "credits":
            continue
        need = int(required or 0)
        if need <= 0:
            continue
        # A material maps to a Planet column ONLY if that integer attribute exists.
        current = getattr(planet, material, None)
        if not isinstance(current, int):
            deferred.append({
                "material": material, "required": need, "available": None,
                "reason": "no planet stockpile column for this material",
            })
            continue
        if current < need:
            deferred.append({
                "material": material, "required": need, "available": int(current),
                "reason": "insufficient planet stockpile",
            })
            continue
        setattr(planet, material, int(current) - need)
    return deferred


def _load_owned_planet(planet_id: str, player: Player, db: Session, *, lock: bool) -> Planet:
    """Resolve the planet, 404 if missing, 403 if the caller is not the owner. When ``lock`` is set
    the planet row is locked ``with_for_update()`` (write paths) so two pilots can't race a placement
    / decommission against the same grid (lost-update). Ownership mirrors ``planets`` land/claim:
    ``planet.owner_id == player.id``."""
    planet_uuid = _parse_planet_id(planet_id)
    q = db.query(Planet).filter(Planet.id == planet_uuid)
    if lock:
        q = q.with_for_update()
    planet = q.first()
    if not planet:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Planet not found")
    if planet.owner_id is None or planet.owner_id != player.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not own this planet",
        )
    return planet


# ---------------------------------------------------------------------------
# 1. GET /planets/{planet_id}/grid — read-only
# ---------------------------------------------------------------------------
@router.get("/{planet_id}/grid")
async def get_planet_grid(
    planet_id: str,
    player: Player = Depends(get_current_player),
    db: Session = Depends(get_db),
):
    """Read the owning player's planetary grid: dims, plots, buildings, the derived citadel level,
    the size-cap on the citadel ladder, and the player's unlocked research nodes (so the UI can show
    which gated buildings are reachable). Seeds the grid if ``planet.structures`` is null. Owner-only;
    404 if the planet is missing, 403 if the caller is not the owner."""
    # No row lock needed for a pure read, but seeding writes structures, so commit if we seeded.
    planet = _load_owned_planet(planet_id, player, db, lock=False)
    seeded_before = (
        isinstance(planet.structures, dict)
        and isinstance(planet.structures.get("grid"), dict)
        and isinstance(planet.structures.get("plots"), list)
    )  # mirror _ensure_seeded's condition so a partial-structures seed-write isn't lost
    _ensure_seeded(planet, db)
    if not seeded_before:
        flag_modified(planet, "structures")
        db.commit()

    payload = _grid_payload(planet)
    return {
        "planet_id": str(planet.id),
        "grid": payload["grid"],
        "plots": payload["plots"],
        "buildings": payload["buildings"],
        "citadel_level": payload["citadel_level"],
        "max_citadel_level": payload["max_citadel_level"],
        "researched": sorted(_researched_set(player)),
        # CRT-2 fix: the UI sources its placeable-building catalog from view.catalog —
        # without this the grid can't place anything (dead feature). Each BUILDING_CATALOG
        # row is already CatalogEntry-shaped (kind/domain/name/footprint/cost/tech_gate);
        # JSON stringifies the int cost-level keys so the client's cost["1"] reads fine.
        "catalog": list(building_catalog.BUILDING_CATALOG.values()),
    }


# ---------------------------------------------------------------------------
# 2. POST /planets/{planet_id}/grid/place — research-gated, cost-charged
# ---------------------------------------------------------------------------
@router.post("/{planet_id}/grid/place")
async def place_building(
    planet_id: str,
    body: GridPlaceRequest,
    player: Player = Depends(get_current_player),
    db: Session = Depends(get_db),
):
    """Place a building on the owning player's grid. Owner-only.

    Flow (defensive, single transaction, planet-row-then-player-row lock order):
      1. lock the planet row, resolve ownership (404/403), seed the grid if null;
      2. resolve the player's researched set, run ``structures.can_place_gated`` — on failure return
         403 for a research-gate reason, else 400, with the primitive's reason;
      3. read the credit cost from ``building_catalog``; lock the player row; 402 on insufficient
         credits; charge per-planet materials (deferring any un-chargeable material, never skipping);
      4. ``structures.place`` (PURE), ``flag_modified``, commit;
      5. return the placed building + the fresh grid view + new derived citadel level + remaining credits.
    """
    # 1. planet row locked FIRST (lock order: planet before player).
    planet = _load_owned_planet(planet_id, player, db, lock=True)
    _ensure_seeded(planet, db)
    structures = planet.structures if isinstance(planet.structures, dict) else {}

    kind = body.kind
    spec = building_catalog.get(kind)
    if not spec:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail=f"Unknown building kind {kind!r}")

    # 2. research gate + grid validity (the pure gated check).
    researched = _researched_set(player)
    ok, reason = structures_svc.can_place_gated(structures, kind, body.x, body.y, researched)
    if not ok:
        # A research-gate failure is a 403 (you lack the tech); a grid-invalidity is a 400.
        gate = structures_svc.kind_tech_gate(kind)
        is_research_gate = gate is not None and gate not in researched
        raise HTTPException(
            status_code=(status.HTTP_403_FORBIDDEN if is_research_gate
                         else status.HTTP_400_BAD_REQUEST),
            detail=reason,
        )

    # 3a. credit cost for the requested level.
    cost_table = spec.get("cost") or {}
    cost = cost_table.get(body.level) or cost_table.get(str(body.level))
    if not isinstance(cost, dict):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"{kind} has no cost defined for level {body.level}",
        )
    cost_credits = int(cost.get("credits", 0) or 0)

    # 3b. lock the player row (AFTER the planet row) for credit safety.
    locked_player = db.query(Player).filter(Player.id == player.id).with_for_update().first()
    if locked_player is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Player not found")
    if int(locked_player.credits or 0) < cost_credits:
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail=(f"Insufficient credits: {kind} L{body.level} costs "
                    f"{cost_credits:,}, you have {int(locked_player.credits or 0):,}."),
        )

    # 3c. charge per-planet materials (planet row already locked); defer any un-chargeable.
    materials_deferred = _charge_materials(planet, cost)

    # 4. PURE placement on the grid, then debit credits + persist.
    try:
        building = structures_svc.place(structures, kind, body.x, body.y, level=body.level)
    except ValueError as e:
        # Defensive: can_place_gated already validated, but place() re-validates and may raise.
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

    locked_player.credits = int(locked_player.credits or 0) - cost_credits
    planet.structures = structures
    flag_modified(planet, "structures")
    db.commit()

    payload = _grid_payload(planet)
    response = {
        "success": True,
        "building": building,
        "grid": payload["grid"],
        "plots": payload["plots"],
        "buildings": payload["buildings"],
        "citadel_level": payload["citadel_level"],
        "remaining_credits": int(locked_player.credits or 0),
    }
    if materials_deferred:
        # Materials we could NOT charge from the planet's stockpile — surfaced, never skipped.
        response["materials_deferred"] = materials_deferred
    return response


# ---------------------------------------------------------------------------
# 3. POST /planets/{planet_id}/grid/decommission — teardown + partial refund
# ---------------------------------------------------------------------------
@router.post("/{planet_id}/grid/decommission")
async def decommission_building(
    planet_id: str,
    body: GridDecommissionRequest,
    player: Player = Depends(get_current_player),
    db: Session = Depends(get_db),
):
    """Tear down a building on the owning player's grid and refund part of its invested credits.
    Owner-only. 404 if the planet OR the building id is missing.

    Flow (planet-row-then-player-row lock order, single transaction):
      1. lock the planet row, resolve ownership (404/403), seed if null;
      2. ``structures.decommission_with_refund`` (PURE) — 404 if the building id isn't on the grid;
      3. lock the player row, credit the refund, ``flag_modified``, commit;
      4. return the removed building, the refund, the fresh grid view + new derived citadel level.
    """
    planet = _load_owned_planet(planet_id, player, db, lock=True)
    _ensure_seeded(planet, db)
    structures = planet.structures if isinstance(planet.structures, dict) else {}

    res = structures_svc.decommission_with_refund(structures, body.building_id)
    if res is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Building {body.building_id!r} not found on this planet's grid",
        )

    refund = int(res.get("refund_credits", 0) or 0)

    # Credit the refund to the owning player (lock the player row after the planet row).
    locked_player = db.query(Player).filter(Player.id == player.id).with_for_update().first()
    if locked_player is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Player not found")
    locked_player.credits = int(locked_player.credits or 0) + refund

    planet.structures = structures
    flag_modified(planet, "structures")
    db.commit()

    payload = _grid_payload(planet)
    return {
        "success": True,
        "removed": res.get("removed"),
        # Both keys: refund_credits matches the decommission_with_refund primitive +
        # the client's read (it showed 0 reading the old `refund`-only key); refund
        # kept for back-compat.
        "refund_credits": refund,
        "refund": refund,
        "grid": payload["grid"],
        "plots": payload["plots"],
        "buildings": payload["buildings"],
        "citadel_level": payload["citadel_level"],
        "remaining_credits": int(locked_player.credits or 0),
    }
