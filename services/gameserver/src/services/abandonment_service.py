"""Planet abandonment / inactivity-reclamation service (PL4b — buildable core).

The SINGLE WRITER (invariant I9) of the abandonment/reclaim lifecycle:
``planets.reclaimable_at``, ``planets.abandoned_at``, and the displaced-owner
compensation. Nothing else in the codebase writes these fields — the daily
scheduler sweep, the ``POST /planets/{id}/abandon`` route, and the
``POST /planets/{id}/reclaim`` route ALL route their mutations through this
module.

Two paths to losing an owned planet (PL4b master §2.1), deliberately ASYMMETRIC:

  1. INVOLUNTARY inactivity reclamation.
       * A DAILY idempotent scheduler sweep (``flag_inactive_planets``) stamps
         ``reclaimable_at = now()`` on every owned, non-hub planet whose owner's
         ``Player.last_game_login`` is older than ``INACTIVITY_DAYS`` (90
         wall-clock days). The stamp is ADVISORY + REVERSIBLE (I5): it never
         deletes the row and never reassigns ownership. It is cleared the moment
         the owner logs back in (``clear_flag_for_player``, called from the auth
         login path in a follow-on; the sweep ALSO self-heals — a planet whose
         owner is no longer stale is un-flagged on the next pass).
       * A ``RECLAIM_GRACE_DAYS`` (7-day) window after the flag gives the
         returning owner deterministic priority: a reclaim is REJECTED until
         ``now() > reclaimable_at + 7d`` (I5).
       * Once eligible, a third party fires ``reclaim_planet`` — pays the flat
         reclaim price, the planet reverts to unowned, and the displaced owner
         is paid a deliberate HAIRCUT of ``0.4 × actual sunk cost`` (I2),
         ONLY if their tenure was ≥ ``TENURE_FLOOR_DAYS`` (7 days, I4).

  2. VOLUNTARY abandon (``abandon_planet``). The owner forfeits the planet. Pays
     NOTHING (I3 — forfeiture, not settlement; this is the money-pump kill: a
     deterministic exit must not pay).

Either path REVERTS the planet to unowned (I7) — ``owner_id=NULL``,
``status=HABITABLE``, the ``player_planets`` row deleted, ``landing_rights``
reset to public (NULL) — while STRUCTURES / CITADEL / POPULATION / RESOURCES /
``region_id`` PERSIST on the row (that developed-world inheritance is the reclaim
premium). One transaction, single-writer.

COMPENSATION basis (PL4b master §2.2, review Fix 1 — the money-pump kill):

    comp = COMPENSATION_FRACTION
           × ( claim_fee + Σ CITADEL_LEVELS[n].upgrade_cost for n = 2..level )

anchored to the REAL in-code citadel upgrade ladder (``CITADEL_LEVELS`` —
L2=50k, L3=150k, L4=500k, L5=2M), NEVER the doc-only ``PLANET_COMPENSATION_TABLE``
(I10). For L0/L1 (upgrade_cost 0) comp = 0.4 × 10,000 = 4,000 against a 10,000
claim ⇒ a guaranteed loss, so abandon/reclaim is NEVER net-positive (I2).

RECLAIM price (the reclaimer's barrier, master §2.3): a FLAT 50,000 cr +
5,000 each ore/organics/equipment, charged to the reclaimer atomically with the
ownership flip — distinct from the displaced-owner payout above.

SCHEMA NOTE — the three PL4b columns (``tax_rate``, ``reclaimable_at``,
``abandoned_at``) are added by migration ``d7a2f1c9e3b5`` but are NOT declared on
the ``Planet`` ORM model (that file is out of this worker's lane). This module
therefore reaches them via raw ``text()`` SQL on the SAME session/transaction as
the ORM mutations, so reads/writes stay atomic with the reversion. When the model
later declares the columns, these raw helpers continue to work unchanged.

ALL ADDITIVE, NULLABLE-MIGRATION-ONLY, SINGLE-WRITER. No abandonment EVER
auto-fires without the 90-day inactivity + 7-day grace + 7-day tenure gates.
"""

import logging
from datetime import datetime, timedelta, UTC
from typing import Any, Dict, Optional
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from src.models.planet import Planet, PlanetStatus, player_planets
from src.models.player import Player
from src.models.ship import Ship
from src.services.citadel_service import CITADEL_LEVELS

logger = logging.getLogger(__name__)

# --- PL4b CONSTANTS (Max-APPROVED numbers; PL4b master §2) -------------------

# Wall-clock days of owner inactivity (Player.last_game_login age) before a
# planet is FLAGGED reclaimable. Inactivity is measured in WALL clock, not
# canonical game time — a 90-day real-world absence, not 90 scaled days.
INACTIVITY_DAYS = 90

# Grace window (days) AFTER the flag during which a reclaim is rejected, giving
# a returning owner deterministic priority to log in and clear the flag (I5).
RECLAIM_GRACE_DAYS = 7

# Minimum days of CONTINUOUS ownership (player_planets.acquired_at age) before
# the displaced owner is eligible for ANY compensation (I4). A planet owned for
# less than this pays 0 on either path.
TENURE_FLOOR_DAYS = 7

# The deliberate haircut: the displaced INACTIVE owner is paid this fraction of
# their verifiable sunk cost (claim fee + citadel upgrade ladder). Strictly < 1
# so abandon/reclaim is never net-positive by construction (I2). Voluntary
# abandon pays 0 regardless (I3).
COMPENSATION_FRACTION = 0.4

# The founding-grant claim fee (mirrors api/routes/planets.py:CLAIM_CREDIT_COST).
# Re-declared here (not imported) to avoid a service→route import edge and to
# keep the compensation basis self-contained; both reference the SAME canon
# magnitude (colonization.md). If the claim fee ever changes, update both.
CLAIM_CREDIT_COST = 10_000

# Flat reclaim price charged to the RECLAIMER, atomic with the ownership flip
# (master §2.3, colonization.md:247). Distinct from the displaced-owner payout.
RECLAIM_CREDIT_COST = 50_000
RECLAIM_RESOURCE_COST = 5_000  # each of ore / organics / equipment

# Perma-siege relief bound (I8): an attacker must not be able to freeze a planet
# forever by keeping it under siege. Inactivity-reclaim is PERMITTED on a sieged
# planet once the siege has run beyond this many wall-clock days (a stale,
# weaponized siege no longer blocks reclamation). Voluntary abandon by the owner
# is still blocked while sieged (the owner should break the siege or lose the
# fight, not flee mid-battle).
SIEGE_RECLAIM_RELIEF_DAYS = 14


# --- Raw-SQL helpers for the three PL4b columns (model not in this lane) -----


def _read_pl4b_cols(db: Session, planet_id: UUID) -> Dict[str, Any]:
    """Read the three PL4b columns for a planet via raw SQL (the columns are not
    on the ORM model in this lane). Returns {} if the planet row is gone."""
    row = db.execute(
        text(
            "SELECT tax_rate, reclaimable_at, abandoned_at "
            "FROM planets WHERE id = :pid"
        ),
        {"pid": str(planet_id)},
    ).first()
    if row is None:
        return {}
    return {
        "tax_rate": row[0],
        "reclaimable_at": row[1],
        "abandoned_at": row[2],
    }


def _set_reclaimable_at(db: Session, planet_id: UUID, value: Optional[datetime]) -> None:
    """Single-writer set of planets.reclaimable_at (NULL clears the flag)."""
    db.execute(
        text("UPDATE planets SET reclaimable_at = :val WHERE id = :pid"),
        {"val": value, "pid": str(planet_id)},
    )


def _set_abandoned_at(db: Session, planet_id: UUID, value: Optional[datetime]) -> None:
    """Single-writer set of planets.abandoned_at (the reversion audit stamp)."""
    db.execute(
        text("UPDATE planets SET abandoned_at = :val WHERE id = :pid"),
        {"val": value, "pid": str(planet_id)},
    )


# --- Compensation math (I2 / I4 / I10) --------------------------------------


def sunk_cost_for(citadel_level: int) -> int:
    """The verifiable sunk capital in a planet: the claim founding-grant fee plus
    the citadel upgrade ladder ACTUALLY paid to reach the current level (I10 —
    sourced from the in-code CITADEL_LEVELS, never the doc-only
    PLANET_COMPENSATION_TABLE).

    Σ CITADEL_LEVELS[n].upgrade_cost for n = 2..level. L0/L1 add 0 (upgrade_cost
    is 0 at tiers 0 and 1), so an undeveloped world's sunk cost is just the
    10,000 claim fee."""
    level = int(citadel_level or 0)
    ladder = 0
    for n in range(2, level + 1):
        ladder += int(CITADEL_LEVELS.get(n, {}).get("upgrade_cost", 0) or 0)
    return CLAIM_CREDIT_COST + ladder


def compensation_for(citadel_level: int) -> int:
    """The displaced INACTIVE owner's payout: COMPENSATION_FRACTION × sunk cost
    (I2). Hard-capped at the sunk cost so it can NEVER be net-positive (the
    fraction is < 1, so this cap is belt-and-braces). Floored at 0.

    NOTE: this returns the GROSS comp for the level; the tenure floor (I4) and
    the voluntary-vs-involuntary asymmetry (I3) are applied by the callers, not
    here — this is the pure money-pump-safe magnitude."""
    sunk = sunk_cost_for(citadel_level)
    comp = int(round(COMPENSATION_FRACTION * sunk))
    return max(0, min(comp, sunk))


def _tenure_days(db: Session, planet_id: UUID, owner_id: UUID) -> Optional[float]:
    """Wall-clock days the current owner has held this planet, from
    player_planets.acquired_at (the canonical per-owner acquisition stamp, set on
    claim). Returns None if no association row exists (treat as ineligible)."""
    row = db.execute(
        text(
            "SELECT acquired_at FROM player_planets "
            "WHERE planet_id = :pid AND player_id = :owner"
        ),
        {"pid": str(planet_id), "owner": str(owner_id)},
    ).first()
    if row is None or row[0] is None:
        return None
    acquired = row[0]
    if acquired.tzinfo is None:
        acquired = acquired.replace(tzinfo=UTC)
    return (datetime.now(UTC) - acquired).total_seconds() / 86400.0


# --- Reversion (the asset-preserving ownership flip, I7) ---------------------


def _revert_to_unowned(db: Session, planet: Planet) -> None:
    """Revert a planet to unowned, PRESERVING the developed asset (I7).

    Mutates ONLY ownership/access state:
      * owner_id        → NULL
      * status          → HABITABLE
      * landing_rights  → NULL  (= public; the canon backward-compatible default)
      * player_planets  → the association row(s) for this planet deleted
      * abandoned_at    → now()  (audit stamp)

    UNCHANGED (the reclaim premium): structures, citadel_*, population,
    colonists, fuel_ore/organics/equipment/fighters, defenses, region_id,
    terraforming state — the next claimant inherits a built world.

    Callers MUST hold the planet row lock (with_for_update) before invoking this;
    callers also clear any landed player's is_landed/current_planet_id. This
    helper does NOT commit — it rides the caller's single transaction."""
    # Delete the association row(s) for this planet (per-owner ledger). Raw delete
    # against the association table — there is no ORM relationship write here.
    db.execute(
        player_planets.delete().where(player_planets.c.planet_id == planet.id)
    )
    planet.owner_id = None
    planet.status = PlanetStatus.HABITABLE
    planet.landing_rights = None
    _set_abandoned_at(db, planet.id, datetime.now(UTC))
    # The inactivity flag is meaningless once unowned — clear it so a freshly
    # reverted (now-claimable) planet is not also marked "reclaimable".
    _set_reclaimable_at(db, planet.id, None)


# --- Eligibility / siege gates ----------------------------------------------


def _siege_blocks_abandon(planet: Planet) -> bool:
    """Voluntary abandon is blocked while the planet is under siege (I8) — the
    owner should fight or lose, not flee mid-battle."""
    return bool(planet.under_siege)


def _siege_blocks_reclaim(planet: Planet) -> bool:
    """Inactivity-reclaim is blocked by an ACTIVE, RECENT siege — but a STALE
    siege (older than SIEGE_RECLAIM_RELIEF_DAYS) must NOT let an attacker freeze
    an inactive owner's planet forever (I8). So a long-running siege stops
    blocking reclamation."""
    if not planet.under_siege:
        return False
    started = planet.siege_started_at
    if started is None:
        # Sieged but no start stamp — treat as recent (conservative block).
        return True
    if started.tzinfo is None:
        started = started.replace(tzinfo=UTC)
    age_days = (datetime.now(UTC) - started).total_seconds() / 86400.0
    return age_days < SIEGE_RECLAIM_RELIEF_DAYS


# --- DAILY SCHEDULER PASS (I5 — advisory, reversible, idempotent) -----------


def flag_inactive_planets(db: Session) -> Dict[str, int]:
    """Daily idempotent sweep: stamp ``reclaimable_at`` on every owned, non-hub
    planet whose owner has been inactive (``last_game_login`` older than
    ``INACTIVITY_DAYS``), and CLEAR the stamp on any flagged planet whose owner
    is no longer stale (self-healing — the auth login path also clears it
    eagerly via ``clear_flag_for_player``).

    IDEMPOTENT (I5): a planet already correctly flagged/unflagged for the current
    state is left untouched (no churn). The flag is ADVISORY — this pass NEVER
    deletes a row and NEVER reassigns ownership; it only sets/clears the marker.
    A re-run in the same period is a no-op for steady-state rows.

    SINGLE-WRITER: reclaimable_at is written ONLY here (set) and in
    ``clear_flag_for_player`` / ``_revert_to_unowned`` (clear). The scheduler
    shell (npc_scheduler_service) owns the advisory-lock + own-session discipline;
    this function takes the session it is handed and does NOT commit (the shell's
    per-batch commit owns persistence) so it composes inside that discipline.

    Returns {"flagged": n_newly_flagged, "cleared": n_cleared}."""
    result = {"flagged": 0, "cleared": 0}
    now = datetime.now(UTC)
    stale_before = now - timedelta(days=INACTIVITY_DAYS)

    # --- (a) FLAG newly-stale owned worlds -----------------------------------
    # Candidates: owned (owner_id NOT NULL), not a population hub, not already
    # flagged, whose owner's last_game_login is older than the cutoff (a NULL
    # last_game_login means the player has NEVER logged a game session — treat as
    # stale, conservative for inactivity reclamation).
    to_flag = db.execute(
        text(
            """
            SELECT p.id
            FROM planets p
            JOIN players pl ON pl.id = p.owner_id
            WHERE p.owner_id IS NOT NULL
              AND COALESCE(p.is_population_hub, FALSE) = FALSE
              AND p.reclaimable_at IS NULL
              AND (pl.last_game_login IS NULL OR pl.last_game_login < :cutoff)
            """
        ),
        {"cutoff": stale_before},
    ).fetchall()
    for (planet_id,) in to_flag:
        _set_reclaimable_at(db, planet_id, now)
        result["flagged"] += 1

    # --- (b) CLEAR flags whose owner is no longer stale ----------------------
    # Self-heal: a flagged planet whose owner has since logged in (last_game_login
    # now newer than the cutoff) loses its flag. Also clear an orphaned flag on a
    # now-unowned planet (defensive — _revert_to_unowned already clears, but a
    # legacy/edge row should not stay flagged-yet-unowned).
    to_clear = db.execute(
        text(
            """
            SELECT p.id
            FROM planets p
            LEFT JOIN players pl ON pl.id = p.owner_id
            WHERE p.reclaimable_at IS NOT NULL
              AND (
                    p.owner_id IS NULL
                 OR (pl.last_game_login IS NOT NULL AND pl.last_game_login >= :cutoff)
              )
            """
        ),
        {"cutoff": stale_before},
    ).fetchall()
    for (planet_id,) in to_clear:
        _set_reclaimable_at(db, planet_id, None)
        result["cleared"] += 1

    return result


def clear_flag_for_player(db: Session, player_id: UUID) -> int:
    """Eagerly clear the inactivity flag on every planet owned by a player who
    just logged in (I5 — the flag is reversible and cleared on owner login). Call
    this from the auth login path (a follow-on wiring step). Idempotent: clears
    only rows currently flagged for THIS owner. Does NOT commit. Returns the
    count cleared."""
    rows = db.execute(
        text(
            "SELECT id FROM planets "
            "WHERE owner_id = :owner AND reclaimable_at IS NOT NULL"
        ),
        {"owner": str(player_id)},
    ).fetchall()
    for (planet_id,) in rows:
        _set_reclaimable_at(db, planet_id, None)
    return len(rows)


# --- VOLUNTARY ABANDON (I3 — zero-pay forfeiture) ---------------------------


def abandon_planet(db: Session, planet: Planet, owner: Player) -> Dict[str, Any]:
    """Voluntary abandon (master §2.1): the owner forfeits the planet. Pays
    NOTHING (I3). Reverts the planet to unowned, asset preserved (I7).

    Preconditions (the caller MUST have row-locked the planet + verified
    ownership; this re-checks defensively):
      * the player must own the planet,
      * the planet must not be under siege (I8 — fight or lose, don't flee).

    Returns {"compensation": 0, "planet_id": ...}. Raises ValueError on a
    precondition failure (the route maps these to 4xx). Does NOT commit — rides
    the route's single transaction."""
    if planet.owner_id is None or planet.owner_id != owner.id:
        raise ValueError("not_owner")
    if _siege_blocks_abandon(planet):
        raise ValueError("under_siege")

    # If the abandoning owner was standing on the planet, lift them off (the
    # planet is about to become unowned; a stale landed pointer would be wrong).
    if owner.current_planet_id == planet.id:
        owner.is_landed = False
        owner.current_planet_id = None

    _revert_to_unowned(db, planet)

    # Voluntary = pure forfeiture: ZERO settlement (I3). This is the money-pump
    # kill — a deterministic exit pays nothing.
    logger.info(
        "abandonment: player %s VOLUNTARILY abandoned planet %s (comp=0, forfeiture)",
        owner.id, planet.id,
    )
    return {"compensation": 0, "planet_id": str(planet.id), "path": "voluntary"}


# --- INVOLUNTARY RECLAIM (I2/I4/I5/I6/I7/I8) --------------------------------


def reclaim_planet(
    db: Session,
    planet: Planet,
    reclaimer: Player,
    ship: Optional[Ship] = None,
) -> Dict[str, Any]:
    """Involuntary inactivity reclamation (master §2.1): a third party takes over
    an inactive owner's flagged planet AFTER the grace window, pays the flat
    reclaim price, and the displaced owner is paid the 0.4 haircut (only if their
    tenure ≥ 7 days, I4).

    The flat reclaim price (master §2.3) is 50,000 cr + 5,000 each of ore /
    organics / equipment. Commodities live in SHIP CARGO, so the resource legs
    are charged from ``ship.cargo['contents']`` and the credit leg from
    ``reclaimer.credits`` — all validated BEFORE any mutation so the charge is
    all-or-nothing. Charging both legs + the displaced-owner payout + the
    ownership flip in this one function keeps the whole transaction atomic and
    single-writer.

    Preconditions (the caller MUST have row-locked the planet + the reclaimer
    Player row, in that order — planet before player, matching the claim route's
    lock order; this re-checks defensively, I6):
      * the planet must be owned (you can't 'reclaim' an unowned world — that's a
        normal claim),
      * the reclaimer must not be the current owner,
      * the planet must be FLAGGED reclaimable AND past the grace window (I5),
      * the planet must not be under a recent active siege (I8),
      * the reclaimer must afford the flat price: 50k cr AND 5k each commodity in
        ship cargo.

    On success: charge the reclaimer (credits + cargo commodities), pay the
    displaced owner the haircut (tenure-gated), revert the planet to unowned
    (asset preserved, I7), then re-found ownership under the reclaimer (mirrors
    the claim route's owner_id/status/association write). Returns a summary dict.
    Raises ValueError on a precondition failure (the route maps these to 4xx).
    Does NOT commit — rides the route's single transaction."""
    if planet.owner_id is None:
        raise ValueError("not_owned")  # use the normal claim flow for unowned
    if planet.owner_id == reclaimer.id:
        raise ValueError("already_owner")

    cols = _read_pl4b_cols(db, planet.id)
    reclaimable_at = cols.get("reclaimable_at")
    if reclaimable_at is None:
        raise ValueError("not_flagged")  # owner is not (yet) inactive
    if reclaimable_at.tzinfo is None:
        reclaimable_at = reclaimable_at.replace(tzinfo=UTC)
    grace_ends = reclaimable_at + timedelta(days=RECLAIM_GRACE_DAYS)
    if datetime.now(UTC) <= grace_ends:
        # Returning-owner priority window still open (I5).
        raise ValueError("within_grace")

    if _siege_blocks_reclaim(planet):
        raise ValueError("under_siege")

    # --- Validate the full flat price BEFORE any mutation (all-or-nothing) ----
    # Credits leg (the reclaimer Player row is locked by the caller).
    if (reclaimer.credits or 0) < RECLAIM_CREDIT_COST:
        raise ValueError("insufficient_credits")
    # Resource legs: 5,000 each commodity, paid from the reclaimer's ship cargo
    # (where commodities live — 1 commodity unit = 1 cargo unit, matching the
    # claim route's colonist accounting).
    if ship is None:
        raise ValueError("no_ship")
    cargo = ship.cargo or {"used": 0, "capacity": 50, "contents": {}}
    contents = dict(cargo.get("contents", {}) or {})
    have_ore = int(contents.get("ore", contents.get("fuel_ore", 0)) or 0)
    have_org = int(contents.get("organics", 0) or 0)
    have_equ = int(contents.get("equipment", 0) or 0)
    if (
        have_ore < RECLAIM_RESOURCE_COST
        or have_org < RECLAIM_RESOURCE_COST
        or have_equ < RECLAIM_RESOURCE_COST
    ):
        raise ValueError("insufficient_resources")

    # The displaced owner + their tenure (for the haircut, I4).
    displaced_owner_id = planet.owner_id
    citadel_level = int(planet.citadel_level or 0)
    tenure = _tenure_days(db, planet.id, displaced_owner_id)
    eligible = tenure is not None and tenure >= TENURE_FLOOR_DAYS
    comp = compensation_for(citadel_level) if eligible else 0

    # --- All checks passed: charge the reclaimer (credits + cargo). -----------
    reclaimer.credits = (reclaimer.credits or 0) - RECLAIM_CREDIT_COST
    # Charge the commodity legs from cargo; normalize on the 'ore' key the rest
    # of the cargo model uses, then drop the legacy 'fuel_ore' alias if present.
    contents["ore"] = have_ore - RECLAIM_RESOURCE_COST
    contents.pop("fuel_ore", None)
    contents["organics"] = have_org - RECLAIM_RESOURCE_COST
    contents["equipment"] = have_equ - RECLAIM_RESOURCE_COST
    cargo["contents"] = contents
    cargo["used"] = max(0, int(cargo.get("used", 0)) - 3 * RECLAIM_RESOURCE_COST)
    ship.cargo = cargo
    flag_modified(ship, "cargo")

    # --- Pay the displaced owner the haircut into THEIR Player wallet ----------
    # The displaced owner keeps their Player row (only the planet ownership is
    # severed), so Player.credits IS their claimable wallet — the comp is there
    # the next time they log in (master's "claimable on next login").
    if comp > 0 and displaced_owner_id is not None:
        displaced = (
            db.query(Player)
            .filter(Player.id == displaced_owner_id)
            .with_for_update()
            .first()
        )
        if displaced is not None:
            displaced.credits = (displaced.credits or 0) + comp
            # If the displaced owner happened to be landed here, lift them off.
            if displaced.current_planet_id == planet.id:
                displaced.is_landed = False
                displaced.current_planet_id = None

    # --- Revert the planet to unowned (asset preserved, I7) -------------------
    _revert_to_unowned(db, planet)

    # --- Re-found ownership under the reclaimer (mirrors the claim route) ------
    planet.owner_id = reclaimer.id
    planet.status = PlanetStatus.COLONIZED
    # abandoned_at was stamped by the reversion as the audit trail of the
    # ownership change; leave it (the planet was, momentarily, abandoned). The
    # inactivity flag is already cleared by the reversion.
    db.execute(
        player_planets.insert().values(
            player_id=reclaimer.id,
            planet_id=planet.id,
        )
    )

    logger.info(
        "abandonment: player %s RECLAIMED planet %s from inactive owner %s "
        "(reclaim_credits=%d, displaced_comp=%d, tenure_eligible=%s, citadel=L%d)",
        reclaimer.id, planet.id, displaced_owner_id,
        RECLAIM_CREDIT_COST, comp, eligible, citadel_level,
    )
    return {
        "planet_id": str(planet.id),
        "path": "inactivity_reclaim",
        "reclaim_credits_charged": RECLAIM_CREDIT_COST,
        "reclaim_resource_cost_each": RECLAIM_RESOURCE_COST,
        "displaced_owner_id": str(displaced_owner_id) if displaced_owner_id else None,
        "displaced_compensation": comp,
        "tenure_eligible": eligible,
        "citadel_level": citadel_level,
    }
