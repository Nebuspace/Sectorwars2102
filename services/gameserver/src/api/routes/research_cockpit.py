"""Citadel Research — the empire R&D notification cockpit (CRT-T1.5-9 / CRT-4).

The player-facing capstone of the CRT economy tranche. Player brand: "Citadel
Research" (Max-ruled). These endpoints SURFACE the now-live governed-flywheel
economy (the per-empire faucet governor + the perishable contract sink + the
recurring faucet copay) and the GENERATED, perishable Research-Directive offers.

NOTIFICATION-DRIVEN, not monitoring-driven (CRT-MASTER §9): a healthy empire
needs ~0 clicks/day. The offers are PUSHED (the contract_offer WS frame, emitted
by the writer in research_service.settle_contracts/maybe_generate_offer and
broadcast post-commit); these read-on-demand surfaces are opened when the player
is curious, never required to watch.

FROZEN cross-zone contract (the client EmpireResearchPanel + researchCockpitAPI
consume these EXACTLY — do not drift the shapes):
  GET  /research/cockpit  -> the empire R&D summary + governor headroom (§5.4/§5.5)
  GET  /research/offers   -> the live generated offers (§5.7) — NEVER a catalogue
  POST /research/contracts/start  -> accept an offer / start a kind (existing start_contract)
  POST /research/contracts/cancel -> cancel an active/accepted directive (existing cancel_contract)

All endpoints are player-authed (get_current_player). The writes wrap the EXISTING
research_service pipeline (start_contract / cancel_contract) — this router adds NO
new economy logic; it is the player surface over the shipped single-writer ledger.
The single-writer law is preserved (research_service.py is the sole ledger writer;
the route only calls its functions and commits).

INVARIANT preserved end-to-end: credits are NEVER minted from RP (the no-RP→credit
laundering invariant §2.4). This router only READS the ledger (cockpit/offers) and
calls the spend-only start/cancel (cancel refunds 0% on active, 0% RP — §3.2/E5).
"""

import logging
import math
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel

from src.core.database import get_db
from src.auth.dependencies import get_current_player
from src.models.player import Player
from src.models.planet import Planet
from src.services import research_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/research", tags=["research-cockpit"])


# --- Request models (frozen contract) ---------------------------------------

class StartContractRequest(BaseModel):
    # Accept a generated offer (offerId) OR start a kind directly (kind + planetId).
    offerId: Optional[str] = None
    kind: Optional[str] = None
    planetId: str


class CancelContractRequest(BaseModel):
    contractId: str


# --- Helpers (pure reads) ---------------------------------------------------

def _empire_raw_rp_per_day(db: Session, player: Player) -> float:
    """Sum the per-planet RAW research-point yield/day across the empire (§5.4/§5.5).

    Reads each owned planet's documented research rate via the production-rate
    calculator (the same per-planet rate the faucet banks into active_events each
    tick). Pure read — drives no production, mutates nothing. The GOVERNED RP/day
    is this raw total passed through the governor (governed_rp); the throughput %
    is governed/raw. A 0-lab empire reads 0 (the governor never engages)."""
    from src.services.planetary_service import PlanetaryService

    ps = PlanetaryService(db)
    owned = db.query(Planet).filter(Planet.owner_id == player.id).all()
    total = 0.0
    for planet in owned:
        try:
            rates = ps._calculate_production_rates(planet)
            total += float(rates.get("research", 0) or 0)
        except Exception:
            # A malformed planet must never blank the whole summary — skip it.
            logger.debug("cockpit raw-RP skipped planet %s",
                         getattr(planet, "id", "?"), exc_info=True)
    return total


def _empire_world_rollup(db: Session, player: Player) -> Dict[str, int]:
    """Count owned worlds by class (frontier vs done; §5.4). Contested worlds are
    counted as frontier for the summary (they are where the contract economy still
    lives — not "done"). Pure read via research_service.classify_world."""
    owned = db.query(Planet).filter(Planet.owner_id == player.id).all()
    frontier = 0
    done = 0
    for planet in owned:
        wc = research_service.classify_world(planet)
        if wc == "done":
            done += 1
        else:
            frontier += 1     # frontier + contested both still "in play"
    return {"frontier": frontier, "done": done}


# --- Endpoints --------------------------------------------------------------

@router.get("/cockpit")
async def get_cockpit(
    current_player: Player = Depends(get_current_player),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """The empire R&D summary + governor headroom (§5.4/§5.5) — one empire-level read.

    Shows the player the LOOP they regulate (more labs → RP → governed → spend on
    directives → frontier decays → buy Stabilize → …): RP/day in, throughput %,
    banked/spent, contracts active, worlds frontier vs done, governor headroom +
    soft cap. The headroom copy is TRUE on day one — the governor bends, never
    clips, and finishing/expanding worlds lifts the cap (the §2.6 capstone lift is
    shipped ON; §5.10 guard: no "free to hold" / "Doctrine" promises here).
    """
    led = research_service.ledger_of(current_player)
    rows = research_service._contracts_of(led)

    raw_rp = _empire_raw_rp_per_day(db, current_player)
    soft_cap = research_service._empire_soft_cap(db, current_player.id)
    governed_rp = research_service.governed_rp(raw_rp, soft_cap)

    rp_per_day = int(math.floor(governed_rp))
    throughput_pct = int(round((governed_rp / raw_rp) * 100)) if raw_rp > 0 else 100

    # Governor headroom (§5.5): RP/day of raw faucet still under the soft cap before
    # the taper begins to bite. Capped at >= 0; inf soft_cap (the off value) reads a
    # large finite headroom sentinel so the client renders a number, not Infinity.
    if soft_cap == research_service.GOV_SOFT_CAP_OFF or math.isinf(soft_cap):
        governor_headroom = 0          # ungoverned — "no taper" (the off baseline)
        soft_cap_out = 0
    else:
        governor_headroom = max(0, int(math.floor(soft_cap - raw_rp)))
        soft_cap_out = int(math.floor(soft_cap))

    # Spent = total credits sunk into directives (every non-offered row was charged
    # its cr_cost on accept/start; offered rows were never charged). The bottomless
    # sink's lifetime drain on this empire.
    spent = 0
    contracts_active = 0
    for r in rows:
        state = r.get("state")
        if state == "offered":
            continue
        spent += int(r.get("cr_cost", 0) or 0)
        if state == "active":
            contracts_active += 1

    worlds = _empire_world_rollup(db, current_player)

    # Deliver any frames staged since the player last looked (§5.2). This route runs
    # ON THE EVENT LOOP, so the post-commit broadcaster is safe to await here — a
    # working delivery path independent of the scheduler one-liner. Best-effort.
    try:
        await research_service.broadcast_pending_research_frames()
    except Exception:
        logger.debug("cockpit frame-drain best-effort failed", exc_info=True)

    return {
        "rpPerDay": rp_per_day,
        "rpThroughputPct": throughput_pct,
        "banked": int(led.get("rp", 0) or 0),
        "spent": spent,
        "contractsActive": contracts_active,
        "worldsFrontier": worlds["frontier"],
        "worldsDone": worlds["done"],
        "governorHeadroom": governor_headroom,
        "softCap": soft_cap_out,
    }


@router.get("/offers")
async def get_offers(
    current_player: Player = Depends(get_current_player),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """The live generated, perishable directive offers (§5.7) — NEVER a catalogue.

    Returns only the empire's currently ``offered`` rows (the ones the sweep
    generated for a frontier/contested world); a done/uncontested world raises
    none. An offer perishes free at expiresAt (settle_contracts flips it to
    expired-offer). The player reacts (accept via /contracts/start, or ignore and
    let it perish) — never browses. Pure read of the ledger.
    """
    led = research_service.ledger_of(current_player)
    rows = research_service._contracts_of(led)

    offers: List[Dict[str, Any]] = []
    for r in rows:
        if r.get("state") != "offered":
            continue
        planet_id = r.get("target_planet_id")
        planet_name = research_service._planet_name_for(db, planet_id)
        offers.append({
            "id": str(r.get("id", "")),
            "kind": str(r.get("kind", "")),
            "planetId": str(planet_id) if planet_id is not None else None,
            "planetName": planet_name,
            "rpCost": int(r.get("rp_cost", 0) or 0),
            "crCost": int(r.get("cr_cost", 0) or 0),
            "magnitude": research_service.display_magnitude(
                str(r.get("kind", "")), float(r.get("magnitude", 0) or 0)
            ),
            "expiresAt": r.get("offer_expires_at"),
        })
    return {"offers": offers}


@router.post("/contracts/start")
async def start_contract_endpoint(
    request: StartContractRequest,
    current_player: Player = Depends(get_current_player),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Accept a generated offer (offerId) or start a directive kind directly
    (kind + planetId). Wraps the EXISTING research_service.start_contract — debits
    the RP gate + the credit sink, flips the offered row to active (or appends a
    fresh active row), and applies an instant kind's effect (Rush/Stabilize) on the
    same tick. This router adds no economy logic; start_contract is the single
    writer and we commit on success.

    A failure (insufficient credits/RP, unknown kind, perished offer, duplicate
    active Overclock) returns success=False from start_contract — surfaced as a
    402/400 HTTPException carrying the human message (the client shows it verbatim).
    """
    kind = request.kind
    # When accepting an offer, resolve the kind from the offered row so the caller
    # need only pass offerId + planetId (the frozen client shape sends both).
    if request.offerId:
        led = research_service.ledger_of(current_player)
        offer_row = next(
            (r for r in research_service._contracts_of(led)
             if r.get("id") == request.offerId),
            None,
        )
        if offer_row is None:
            raise HTTPException(status_code=404, detail="Offer not found.")
        kind = offer_row.get("kind")

    if not kind:
        raise HTTPException(
            status_code=400,
            detail="A directive kind (or a valid offerId) is required.",
        )

    result = research_service.start_contract(
        db,
        current_player.id,
        kind,
        target_planet_id=request.planetId,
        offer_id=request.offerId,
    )
    if not result.get("success"):
        msg = result.get("message", "Could not start directive.")
        # Map the dominant failure (insufficient credits) to 402; others to 400.
        status = 402 if "credit" in msg.lower() else 400
        raise HTTPException(status_code=status, detail=msg)

    db.commit()
    return {"success": True, "contract": result.get("contract")}


@router.post("/contracts/cancel")
async def cancel_contract_endpoint(
    request: CancelContractRequest,
    current_player: Player = Depends(get_current_player),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Cancel an active/accepted directive (wraps the EXISTING cancel_contract).

    Anti-arbitrage refund (§3.2/E5): 0% credits on an ACTIVE contract (you bought
    it, you spent it), 0% RP ALWAYS (the no-launder invariant §2.4). The effect
    ends immediately. cancel_contract is the single writer; we commit on success.
    """
    result = research_service.cancel_contract(
        db, current_player.id, request.contractId
    )
    if not result.get("success"):
        raise HTTPException(
            status_code=400,
            detail=result.get("message", "Could not cancel directive."),
        )

    db.commit()
    return {"success": True}
