"""Research service — point-of-use readers + unlock + the faucet sweep (CRT WO-K0-2).

The keystone discipline (CRT-MASTER §1.2): **research is a leaf in the call
graph**. Every reader here is a *pure read* of ``Player.research_ledger`` against
the static ``tech_tree`` catalog. Downstream systems (citadel, terraform,
production, combat) CALL these readers at the moment they consume a value; they
are NEVER called *from* research, and a research effect is NEVER written onto the
entity it buffs. That is the zero-migration guarantee for every buffed system.

What lives here:
  * ``ledger_of`` / ``_seed`` — the NULL-ledger lazy-seed contract (NULL means
    ``{rp:0, insight:0, doctrine:0, unlocked:[t.root.0]}``).
  * Point-of-use readers: ``player_has_tech`` / ``tech_modifier`` / ``gate_value``
    / ``has_tool``. Pure reads; safe to call anywhere, hot-path cheap.
  * ``can_unlock`` / ``unlock_node`` — spend banked RP to unlock a node.
  * ``sweep_research_faucet`` — drains the shipped per-planet
    ``active_events['research_points']`` faucet into the owner's ledger ON THE
    EXISTING production tick, and applies the **A.4 one-time WIPE + REFUND TO
    CREDITS** for any player who has never had a ledger. Written idempotent so it
    survives re-homing into ``settle()`` step 5 at K1a.

Lock discipline: the sweep is called from inside ``_run_planetary_advance_sync``
while the PLANET row is already locked (``with_for_update``). It acquires the
owner's PLAYER row in the SAME transaction (planet-then-player — the shipped
invariant at citadel_service.py:920) before touching credits/ledger, and never
releases the planet lock first.
"""

from __future__ import annotations

import logging
import math
import uuid
from datetime import datetime, UTC
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from src.core.game_time import GAME_TIME_SCALE
from src.models.player import Player
from src.models.planet import Planet
from src.services import tech_tree
from src.services.structures import _via_settle_guard

logger = logging.getLogger(__name__)

# --- A.4 RULING (Max): WIPE + REFUND TO CREDITS -----------------------------
# On a player's first-ever sweep, their accrued research_points (summed across
# their planets' active_events on that sweep) are ZEROED and an equivalent in
# CREDITS is refunded. The RP->credit conversion rate is [NO-CANON]; ship
# CONSERVATIVE. Rationale: RP in the kernel is "expensive money you make from
# money" (CRT-MASTER §1.3) — accrued at 25 RP / lab-level / day. A small
# credits-per-RP keeps the refund modest (a generous rate would dump a credit
# windfall on long-running planets). 10 cr/RP is conservative relative to the
# ~150k–500k credit defense-building costs the refunded credits can be spent on.
RP_TO_CREDIT_RATE = 10  # credits refunded per 1 banked RP (NO-CANON — Max to bless)


# --- T1.5-1 FLYWHEEL GOVERNOR (CRT-4 / CRT-T15-MASTER §2) ---------------------
# A soft per-EMPIRE-SUM RP/day S-curve applied at the faucet sweep BEFORE crediting
# research_ledger['rp']. The runaway (`01-ground-economy.md` §3: a reinvesting
# empire's faucet is monotonically rising, ~4,690 RP/day from one 5-world subsystem
# ungoverned) is bounded at its mathematical root. The headline deliverable is NOT
# the curve (a one-liner) but the per-empire, per-canonical-day RUNNING SUM applied
# incrementally + idempotently across the per-planet sweep call site (§2.2).
#
# REPRODUCE-EXACTLY OFF-SWITCH: GOV_SOFT_CAP_OFF (= math.inf). With the soft_cap at
# infinity, governed_rp(raw, inf) == raw for every input, so the governor credits
# exactly the raw banked RP — byte-identical to today across an empire of any size
# (acceptance §2.7.1). Ship the RULED finite base AND keep the off value reachable
# via the constant so the lever is reversible.
GOV_BASE_SOFT_CAP = 1500.0          # per-empire RP/day where the taper begins (Max-ruled, empire-anchored)
GOV_TAPER = 0.5                     # excess-compression strength (Max-ruled)
GOV_DOCTRINE_LIFT = 0.0             # RP/day the cap rises per Doctrine point — 0 in T1.5 (no Doctrine; lit T2)
GOV_CAPSTONE_LIFT = 150.0           # RP/day the cap rises per capstone-activated world (Orch default, ON)
GOV_SOFT_CAP_OFF = math.inf         # the reproduce-exactly off value: governed_rp(raw, inf) == raw


def governed_rp(raw_daily_rp: float, soft_cap: float) -> float:
    """Soft per-empire RP/day governor (CRT-T15-MASTER §2.2).

    Below ``soft_cap``: full value. Above: each further RP is discounted on a smooth
    diminishing log curve so the per-empire daily total ASYMPTOTES rather than clips
    (continuous + monotonic: the next lab always yields *some* more RP, but strictly
    less than ungoverned). ``raw`` == today's ungoverned amount; ``soft_cap=inf`` ==
    today byte-for-byte (math.log1p(excess/inf) is never reached because raw <= inf).
    """
    if raw_daily_rp <= soft_cap:
        return raw_daily_rp                         # full value under the threshold (incl. soft_cap=inf)
    excess = raw_daily_rp - soft_cap
    return soft_cap + soft_cap * GOV_TAPER * math.log1p(excess / soft_cap)


# Fixed canonical epoch for the running-sum day bucket. The bucket index is the
# count of canonical days since this epoch (GAME_TIME_SCALE scales ELAPSED, never
# the absolute timestamp — `core/game_time.py`), so the bucket rolls correctly on
# dev (scale 144 → a canonical day every 10 wall-min) and prod (scale 1.0).
_GOV_DAY_EPOCH = datetime(2026, 1, 1, tzinfo=UTC)


def _canonical_day_bucket(now: Optional[datetime] = None) -> int:
    """The integer canonical-day bucket for the per-empire running sum (§2.2).

    Pure read of wall-clock now scaled into canonical days. Day-roll resets the
    running sum so the governor caps RP/CANONICAL-day, not RP/wall-day.
    """
    now = now or datetime.now(UTC)
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    canonical_hours = ((now - _GOV_DAY_EPOCH).total_seconds() / 3600.0) * GAME_TIME_SCALE
    return int(canonical_hours // 24.0)


def _empire_soft_cap(db: Session, owner_id: Any) -> float:
    """The empire's current RP/day soft cap = base + doctrine-lift + capstone-lift (§2.2/§2.6).

    ``GOV_BASE_SOFT_CAP`` is the empire-anchored threshold; ``GOV_CAPSTONE_LIFT`` raises
    it per capstone-activated world (reads ``structures.terraform_meta.confirmed_biome``,
    the SAME flag the decay-rescope reads, §4.4 — written by the Max-gated CRT-3 WO).
    Until CRT-3 ships ``confirmed_biome``, ``capstoned_worlds == 0`` so the capstone-lift
    contributes nothing — and with ``GOV_BASE_SOFT_CAP`` set to the off value (inf) the
    whole expression collapses to inf, reproducing today. Doctrine-lift is 0 in T1.5.

    Pure read (no lock) — the soft cap is advisory headroom; a marginally-stale capstone
    count only nudges the taper threshold and never mints or launds anything.
    """
    if GOV_BASE_SOFT_CAP == GOV_SOFT_CAP_OFF:
        return GOV_SOFT_CAP_OFF
    capstoned = 0
    rows = (
        db.query(Planet.structures)
        .filter(Planet.owner_id == owner_id)
        .all()
    )
    for (structures,) in rows:
        if not isinstance(structures, dict):
            continue
        tmeta = structures.get("terraform_meta")
        if isinstance(tmeta, dict) and tmeta.get("confirmed_biome"):
            capstoned += 1
    return GOV_BASE_SOFT_CAP + GOV_DOCTRINE_LIFT * 0 + GOV_CAPSTONE_LIFT * capstoned


def _gov_apply(led: Dict[str, Any], raw_drained: int, soft_cap: float,
               now: Optional[datetime] = None) -> int:
    """Apply the per-empire running-sum governor to ``raw_drained`` RP, IN PLACE on
    the ledger dict, and return the GOVERNED DELTA to credit to ``led['rp']`` (§2.2).

    The hard part of the whole tranche: this per-planet sweep adds its raw RP to a
    per-empire, per-canonical-day running sum carried in the ledger, recomputes
    ``governed_rp`` over the running TOTAL, and returns the delta between the governed
    total and what was already credited this canonical day — so the credit is
    INCREMENTAL and IDEMPOTENT:

      * Multi-planet same day: each planet's raw folds into ``gov_raw_today``; the
        governor is applied to the EMPIRE SUM, not per-planet (kills the lab-spread
        dodge — 10 sub-per-planet-cap planets summing > the cap are governed).
      * Re-settle same day with ``raw_drained == 0`` (faucet already zeroed): the
        running total is unchanged → governed total unchanged → delta == 0 → ZERO new
        RP credited (no double-count on a partial-settle redeploy, acceptance §2.7.2).
      * Day-roll: ``gov_day`` mismatch resets ``gov_raw_today`` / ``gov_credited_today``
        to 0 so the cap is per CANONICAL day.

    REPRODUCE-EXACTLY: with ``soft_cap == inf`` the governed total == the raw total
    every day, so the returned delta == ``raw_drained`` exactly (the running-sum keys
    are still written, but they never change the credited amount — byte-identical RP).

    NOTE — the no-RP→credit invariant (§2.4): this governs the RP COLUMN only. RP is
    NEVER convertible to credits at a steady-state rate (the legacy one-shot A.4
    first-sweep refund, gated by ``swept_at``, is the sole RP→credit event). Never add
    a "sell surplus RP" converter — it would launder the governor.
    """
    bucket = _canonical_day_bucket(now)
    if led.get("gov_day") != bucket:
        # Day-roll (or first ever): start a fresh per-empire daily running sum.
        led["gov_day"] = bucket
        led["gov_raw_today"] = 0
        led["gov_credited_today"] = 0

    raw_today = int(led.get("gov_raw_today", 0) or 0) + int(raw_drained)
    governed_total = governed_rp(float(raw_today), soft_cap)
    already_credited = int(led.get("gov_credited_today", 0) or 0)
    # Floor the governed total to int and take the delta over what's already credited
    # this day. Flooring the cumulative total (not each delta) keeps the running
    # quantity exact across many small per-planet sweeps (no per-sweep rounding drift).
    governed_total_int = int(math.floor(governed_total))
    delta = governed_total_int - already_credited
    if delta < 0:
        # Defensive: the curve is monotonic in raw_today, so a growing running sum can
        # never lower the governed total; clamp to 0 so a freak float edge never DEBITS
        # banked RP (the governor only ever bends the faucet down, never claws back).
        delta = 0
    led["gov_raw_today"] = raw_today
    led["gov_credited_today"] = already_credited + delta
    return delta


# --- T1.5-2 FAUCET CREDIT-COPAY (the recurring involuntary floor, §3.3) -------
# The central E3 fix: the old promotion levy was NOT a floor (it dead-ended at
# citadel L5 — a finished empire paid it zero forever while the faucet kept
# minting). The copay is the REAL recurring floor: every canonical day a world
# banks RP it pays a small credit copay proportional to the GOVERNED RP banked.
#   * Involuntary — you cannot bank RP without paying it.
#   * Recurring — every day, forever (the thing the promotion levy never was).
#   * Self-scaling to the rich — more labs → more governed RP → more copay (the
#     faucet IS the wealth signal; no EMPIRE_SCALE_K math needed).
#   * Counters the RP→credit-notional 10:1 edge (E2) by bleeding a slice back out
#     as a REAL credit sink — without ever minting credits FROM RP (it only DEBITS).
#
# REPRODUCE-EXACTLY OFF-SWITCH: FAUCET_CREDIT_COPAY = 0.0 → copay == 0 → no debit,
# behaviour byte-identical to today (acceptance §3.6.2). Ship the RULED 0.05.
FAUCET_CREDIT_COPAY = 0.05          # × governed_rp × RP_TO_CREDIT_RATE cr/day (Orch default — the headline E3 number)


def faucet_copay(governed_rp_banked: int) -> int:
    """The cr/day copay owed for banking ``governed_rp_banked`` RP this day (§3.3).

    ``FAUCET_CREDIT_COPAY × governed_rp × RP_TO_CREDIT_RATE`` — a slice of the
    RP→credit-notional, charged back out as a real credit sink. COPAY=0 → 0.
    """
    if FAUCET_CREDIT_COPAY <= 0 or governed_rp_banked <= 0:
        return 0
    return int(math.floor(FAUCET_CREDIT_COPAY * governed_rp_banked * RP_TO_CREDIT_RATE))


def _apply_faucet_copay(player: Player, governed_rp_banked: int) -> int:
    """Debit the faucet copay from ``player.credits`` IN PLACE, returning the amount
    debited (§3.3). Charged on the GOVERNED RP actually credited (so a governed-down
    whale pays copay on the governed amount, not the raw — the copay rides the same
    governed quantity the ledger received). Credits are floored at 0 (the copay is an
    involuntary drain, never a path to negative credits). NEVER mints credits FROM RP
    (it only debits) — the no-RP→credit invariant (§2.4) holds.
    """
    owed = faucet_copay(governed_rp_banked)
    if owed <= 0:
        return 0
    current = player.credits or 0
    debit = min(owed, current)          # never drive credits negative on a broke world
    player.credits = current - debit
    if debit > 0:
        logger.debug(
            "Faucet copay: player %s paid %s cr (owed %s) on %s governed RP",
            player.id, debit, owed, governed_rp_banked,
        )
    return debit


def _default_ledger() -> Dict[str, Any]:
    """The canonical cold-start ledger (NULL column == this value)."""
    return {
        "rp": 0,
        "insight": 0,
        "doctrine": 0,
        "unlocked": [tech_tree.FREE_ROOT_ID],
    }


def ledger_of(player: Player) -> Dict[str, Any]:
    """Return a player's ledger, lazy-seeding the default for a NULL column.

    Does NOT persist the seed (a pure read returns a fresh default for a NULL
    ledger so readers behave correctly on a never-swept player). Mutating helpers
    (``unlock_node`` / the sweep) assign the seeded dict back to the column and
    flag it modified.
    """
    led = player.research_ledger
    if not isinstance(led, dict):
        return _default_ledger()
    # Defensive: fill any missing kernel keys without clobbering present ones, so
    # an older partial ledger still reads sanely.
    seeded = _default_ledger()
    seeded.update(led)
    if not isinstance(seeded.get("unlocked"), list):
        seeded["unlocked"] = [tech_tree.FREE_ROOT_ID]
    if tech_tree.FREE_ROOT_ID not in seeded["unlocked"]:
        seeded["unlocked"] = [tech_tree.FREE_ROOT_ID] + list(seeded["unlocked"])
    return seeded


# --- Point-of-use readers (PURE READS — call these from buffed systems) ------

def player_has_tech(player: Player, node_id: str) -> bool:
    """True iff ``node_id`` is in the player's unlocked set. Pure read."""
    return node_id in ledger_of(player).get("unlocked", [])


def has_tool(player: Player, tool_key: str) -> bool:
    """True iff the player has unlocked any node whose effect is a matching tool.

    Inert in K0 (no consumer calls it yet); the placeholder tool nodes wire here
    at K1b. Pure read.
    """
    unlocked = set(ledger_of(player).get("unlocked", []))
    for nid in unlocked:
        node = tech_tree.get_node(nid)
        if not node:
            continue
        eff = node.get("effect", {})
        if eff.get("kind") == "tool" and eff.get("key") == tool_key:
            return True
    return False


def gate_value(player: Player, gate_key: str, floor: int = 1) -> int:
    """Return the highest unlocked gate ceiling for ``gate_key``, else ``floor``.

    A gate raises a stage/intensity ceiling UP, never OUT (CRT-MASTER §K1b).
    Inert in K0. Pure read.
    """
    best = floor
    for nid in ledger_of(player).get("unlocked", []):
        node = tech_tree.get_node(nid)
        if not node:
            continue
        eff = node.get("effect", {})
        if eff.get("kind") == "gate" and eff.get("key") == gate_key:
            best = max(best, int(eff.get("gate", floor)))
    return best


def tech_modifier(player: Player, modifier_key: str, base: float = 0.0) -> float:
    """Return the SUMMED modifier magnitude for ``modifier_key`` (additive on base).

    e.g. ``rate = base_rate * (1 + tech_modifier(player, "production_rate"))``.
    Inert in K0 (no consumer calls it yet). Pure read.
    """
    total = base
    for nid in ledger_of(player).get("unlocked", []):
        node = tech_tree.get_node(nid)
        if not node:
            continue
        eff = node.get("effect", {})
        if eff.get("kind") == "modifier" and eff.get("key") == modifier_key:
            total += float(eff.get("magnitude", 0.0))
    return total


# --- Unlock pipeline ---------------------------------------------------------

def can_unlock(player: Player, node_id: str) -> Dict[str, Any]:
    """Return {ok: bool, reason: str} for whether the player may unlock node_id.

    Checks: node exists, not already unlocked, all prereqs unlocked, enough
    banked RP. Pure read (does not mutate). The caller (``unlock_node``)
    re-checks under a row lock before spending.
    """
    node = tech_tree.get_node(node_id)
    if node is None:
        return {"ok": False, "reason": f"Unknown tech node '{node_id}'."}

    led = ledger_of(player)
    if node_id in led.get("unlocked", []):
        return {"ok": False, "reason": "Already unlocked."}

    missing = [p for p in node["prereqs"] if p not in led.get("unlocked", [])]
    if missing:
        return {"ok": False, "reason": f"Missing prerequisites: {', '.join(missing)}."}

    rp_cost = int(node["cost"].get("rp", 0))
    if int(led.get("rp", 0)) < rp_cost:
        return {
            "ok": False,
            "reason": f"Insufficient research points. Need {rp_cost}, have {int(led.get('rp', 0))}.",
        }

    return {"ok": True, "reason": "", "rp_cost": rp_cost}


def unlock_node(db: Session, player_id: Any, node_id: str) -> Dict[str, Any]:
    """Spend banked RP to unlock ``node_id`` for a player. Mutates the ledger.

    Locks the player row, re-checks ``can_unlock`` under the lock (prevents a
    concurrent double-spend), deducts the RP cost, appends the node to
    ``unlocked``, and flushes. The CALLER commits (mirrors citadel_service's
    deduct-and-flush, commit-at-route pattern).
    """
    player = db.query(Player).filter(Player.id == player_id).with_for_update().first()
    if not player:
        return {"success": False, "message": "Player not found"}

    check = can_unlock(player, node_id)
    if not check["ok"]:
        return {"success": False, "message": check["reason"]}

    led = ledger_of(player)
    led["rp"] = int(led.get("rp", 0)) - int(check["rp_cost"])
    led["unlocked"] = list(led.get("unlocked", [])) + [node_id]
    player.research_ledger = led
    flag_modified(player, "research_ledger")
    db.flush()

    logger.info(
        "Player %s unlocked tech node %s (spent %s RP, %s RP remaining)",
        player_id, node_id, check["rp_cost"], led["rp"],
    )
    return {
        "success": True,
        "node_id": node_id,
        "rp_remaining": led["rp"],
        "unlocked": led["unlocked"],
        "message": f"Unlocked {tech_tree.get_node(node_id)['name']}.",
    }


# --- The faucet sweep (rides the EXISTING production tick) --------------------

def _zero_planet_faucet(planet: Planet) -> int:
    """Zero one planet's ``active_events['research_points']`` faucet, returning
    the banked amount that was drained. Pure mutation on the (already-locked)
    planet row; flags the JSONB modified so SQLAlchemy persists it."""
    events = planet.active_events if isinstance(planet.active_events, dict) else {}
    rp = int(events.get("research_points", 0) or 0)
    if rp <= 0:
        return 0
    new_events = dict(events)
    new_events["research_points"] = 0
    planet.active_events = new_events
    flag_modified(planet, "active_events")
    return rp


def sweep_research_faucet(db: Session, planet: Planet, *, _via_settle: bool = False) -> bool:
    """Drain one planet's research_points faucet into its owner's ledger.

    Called from inside ``_run_planetary_advance_sync`` AFTER
    ``realize_production`` has written the planet's accrued
    ``active_events['research_points']``. The PLANET row is already locked by the
    caller; this acquires the OWNER's player row in the same transaction
    (planet-then-player lock order) before touching credits/ledger.

    Behaviour per call:
      * Read ``active_events['research_points']`` (the banked faucet balance).
      * Lock the owning player; lazy-seed a NULL ledger.
      * **A.4 first-ever sweep (research_ledger was NULL / no swept_at):** the
        refund is the player's *TOTAL banked RP across ALL their owned planets*,
        not just this planet's (orchestrator FIX-1 ruling). On first contact the
        sweep AGGREGATES ``active_events['research_points']`` over every planet
        the player owns, refunds the SUM at ``RP_TO_CREDIT_RATE`` to
        ``player.credits``, ZEROES every one of those planets' faucets, and
        stamps ``swept_at`` — ONCE, atomically in the held transaction. Without
        the aggregate, the FIRST-swept planet was refunded but the player's OTHER
        planets' pre-kernel banked RP then drained as spendable ledger on later
        sweeps (a windfall). swept_at gates the whole thing so a re-tick / restart
        never double-refunds (idempotent).
      * **Steady state (swept_at present):** ADD this planet's faucet balance to
        ``ledger['rp']`` (normal per-planet drain — RP becomes spendable research
        currency).
      * Either way, the drained faucet(s) are ZEROED so RP is never counted twice.

    Lock order (deadlock-safe vs player-facing citadel/build ops, which lock
    planet→player): the first-sweep aggregate locks the owner's OTHER planets
    ORDERED BY id, then the player LAST. The caller already holds THIS planet's
    row; re-locking it inside the ordered set is a re-entrant no-op. Because the
    player row is acquired only AFTER all planet rows, the aggregate never holds
    the player while waiting on a planet, so no cycle with a planet→player op can
    form.

    Returns True iff any state changed (so the caller commits only on a real
    change, matching the realize_production/_advance_terraforming contract).

    The CALLER commits (per-planet commit/rollback isolation in the sweep loop).

    ``_via_settle`` (CRT spine, WO-K1a): True from structures.settle() step 5 (the re-home target).
    The scheduler's chained call (:1758) stays False until the Max-gated cutover removes it in the
    SAME edit that flips the scheduler to settle(); guarded so a post-cutover stray trips loudly.
    """
    _via_settle_guard("sweep_research_faucet", _via_settle)
    events = planet.active_events if isinstance(planet.active_events, dict) else {}
    this_planet_rp = int(events.get("research_points", 0) or 0)

    if planet.owner_id is None:
        # Unowned planet should not have been selected by the sweep filter, but
        # be defensive: zero the orphaned faucet so it cannot accumulate forever.
        if this_planet_rp <= 0:
            return False
        _zero_planet_faucet(planet)
        return True

    # Determine the owner's first-sweep status WITHOUT taking the player lock yet
    # — but we cannot read swept_at reliably until we hold the row, so we lock the
    # player. To preserve the deadlock-safe order (planets-by-id THEN player),
    # peek at the unlocked owner row only to decide whether this is plausibly a
    # first sweep; the authoritative re-check happens under the lock below.
    owner_peek = (
        db.query(Player.research_ledger)
        .filter(Player.id == planet.owner_id)
        .first()
    )
    if owner_peek is None:
        # Owner row vanished (race with deletion). Leave the faucet untouched; a
        # later sweep with a live owner will drain it. Treat as no-op.
        return False
    peek_ledger = owner_peek[0]
    plausibly_first = (
        not isinstance(peek_ledger, dict) or "swept_at" not in peek_ledger
    )

    # Does the owner have any live (offered/active) directive that might need settling
    # this tick? If so we must proceed even on a zero-RP planet so contracts expire on
    # quiet empires. Pure read of the peeked ledger — when there are NO contracts (the
    # reproduce-exactly baseline) this is always False and the guard behaves as before.
    peek_has_live_contracts = False
    if isinstance(peek_ledger, dict):
        for r in peek_ledger.get("contracts", []) or []:
            if isinstance(r, dict) and r.get("state") in ("offered", "active"):
                peek_has_live_contracts = True
                break

    if not plausibly_first and this_planet_rp <= 0 and not peek_has_live_contracts:
        # Steady-state, nothing to drain on this planet AND no live directive to settle.
        # Pure no-op — no player lock acquired. (The aggregate-needing case must still
        # proceed even with this planet at 0, because OTHER planets may carry pre-kernel
        # RP; and a live contract must still be allowed to settle on a quiet planet.)
        return False

    # --- First-sweep aggregate path -----------------------------------------
    if plausibly_first:
        from src.models.planet import Planet as _Planet

        # Lock ALL the owner's planets ORDERED BY id (re-locking the held current
        # planet is a re-entrant no-op). Acquiring planet rows in a single, total
        # id order — and the player row LAST — is what makes this deadlock-safe
        # against citadel/build ops that lock planet→player.
        owned_planets = (
            db.query(_Planet)
            .filter(_Planet.owner_id == planet.owner_id)
            .order_by(_Planet.id)
            .with_for_update()
            .all()
        )

        # Lock the owner LAST, and re-check first-sweep status under the lock
        # (another sweep/op may have stamped swept_at between the peek and here).
        player = (
            db.query(Player)
            .filter(Player.id == planet.owner_id)
            .with_for_update()
            .first()
        )
        if player is None:
            return False

        led = ledger_of(player)
        if isinstance(player.research_ledger, dict) and "swept_at" in player.research_ledger:
            # Lost the first-sweep race — fall through to steady-state drain of
            # THIS planet only (using the now-authoritative locked ledger).
            drained = _zero_planet_faucet(planet)
            if drained <= 0:
                # Nothing to do. Don't roll back — the caller may still have
                # realize_production changes to commit; locks release at txn end.
                return False
            soft_cap = _empire_soft_cap(db, player.id)
            credited = _gov_apply(led, drained, soft_cap)
            led["rp"] = int(led.get("rp", 0)) + credited
            _apply_faucet_copay(player, credited)
            player.research_ledger = led
            flag_modified(player, "research_ledger")
            logger.debug(
                "Research faucet drain (post-race): planet %s owner %s — raw +%s RP, "
                "governed +%s RP (soft_cap %s)",
                planet.id, player.id, drained, credited, soft_cap,
            )
            return True

        # A.4 AGGREGATE WIPE + REFUND, exactly once. Sum every owned planet's
        # banked RP, refund the TOTAL as credits, zero every faucet, stamp once.
        total_rp = 0
        for owned in owned_planets:
            total_rp += _zero_planet_faucet(owned)
        # The held current planet may not appear in owned_planets if its in-memory
        # owner_id was just set this transaction; fold its faucet in defensively.
        if all(op.id != planet.id for op in owned_planets):
            total_rp += _zero_planet_faucet(planet)

        refund = total_rp * RP_TO_CREDIT_RATE
        player.credits = (player.credits or 0) + refund
        led["rp"] = int(led.get("rp", 0))  # banked RP stays as-is (0 at cold start)
        led["swept_at"] = datetime.now(UTC).isoformat()
        player.research_ledger = led
        flag_modified(player, "research_ledger")
        logger.info(
            "Research faucet A.4 AGGREGATE wipe+refund: owner %s — wiped %s RP "
            "across %s planet(s), refunded %s credits (rate %s cr/RP)",
            player.id, total_rp, len(owned_planets), refund, RP_TO_CREDIT_RATE,
        )
        # True iff anything actually changed (a refund OR the swept_at stamp on a
        # cold-start player with no banked RP — the stamp itself is a real change
        # that the caller must commit so the one-time gate is persisted).
        return True

    # --- Steady-state per-planet drain --------------------------------------
    # Lock the owner in the SAME transaction as the held planet lock
    # (planet-then-player — shipped invariant).
    player = (
        db.query(Player)
        .filter(Player.id == planet.owner_id)
        .with_for_update()
        .first()
    )
    if player is None:
        return False

    # T1.5-2: settle the player's live directives on this held-player tick (expire due
    # active rows, perish stale offers). Idempotent; rides the sweep's player lock so no
    # new scheduler/writer is introduced. It writes research_ledger itself, so re-read
    # `led` AFTER it (else the governor below would clobber the just-settled contracts).
    contracts_changed = settle_contracts(db, player, _via_settle=_via_settle)
    led = ledger_of(player)
    drained = _zero_planet_faucet(planet)
    if drained <= 0:
        # Faucet empty this planet — but a contract may have just settled (already
        # persisted by settle_contracts). Commit iff so.
        return bool(contracts_changed)
    # T1.5-1: govern the per-empire daily SUM before crediting (incremental +
    # idempotent — §2.2). soft_cap=inf credits the raw amount byte-for-byte.
    soft_cap = _empire_soft_cap(db, player.id)
    credited = _gov_apply(led, drained, soft_cap)
    led["rp"] = int(led.get("rp", 0)) + credited
    # T1.5-2: the recurring involuntary copay — every day a world banks (governed) RP
    # it pays a small credit copay. COPAY=0 reproduces today (§3.3).
    _apply_faucet_copay(player, credited)
    player.research_ledger = led
    flag_modified(player, "research_ledger")
    logger.debug(
        "Research faucet drain: planet %s owner %s — raw +%s RP, governed +%s RP "
        "(now %s, soft_cap %s)",
        planet.id, player.id, drained, credited, led["rp"], soft_cap,
    )
    return True


# ===========================================================================
# T1.5-2 BOTTOMLESS SINK — the Citadel-Research directive contract pipeline
# (player-facing name "Citadel Research"; CRT-T15-MASTER §3.2, MASTER cancel
# fix §3.2 / E5). Rides the SHIPPED research_ledger.contracts[] JSONB (K0
# column) — ZERO migration. SINGLE-WRITTEN by research_service.py; settled
# INSIDE settle() step 5 beside the faucet sweep — no new scheduler.
#
# The credit column is the SINK; the RP column is the GATE (recoverable from
# the faucet). RP is NEVER refunded (the anti-launder invariant, §2.4 — a
# contract cannot re-mint banked RP into anything spendable elsewhere).
# ===========================================================================

# The kernel contract set (Overclock + Rush only; Stabilize/Expedition deferred
# to T1.5-6 / T2 on the same pipeline). All magnitudes [NO-CANON]; Max-ruled
# defaults below. There is no OFF-SWITCH constant here: an empire that buys no
# contract spends nothing — "contracts off ≡ no spend" IS the reproduce-exactly
# baseline (an empty contracts[] is byte-identical to today).
CONTRACT_KINDS: Dict[str, Dict[str, Any]] = {
    "overclock": {
        "rp_cost": 300,             # the GATE (recoverable; governs pacing)
        "cr_cost": 50000,           # the SINK (Max-ruled — tune up as the glut inflates)
        "duration_days": 3,         # the effect lasts 3 canonical days, then reverts
        "magnitude": 0.15,          # +15% production on one planet
        "instant": False,
        "free_tier": True,          # no tree node required (cold-start sink access)
    },
    "rush": {
        "rp_cost": 200,             # the GATE
        "cr_cost": 30000,           # the SINK (Max-ruled)
        "duration_days": 0,         # instant — collapses one live build/terraform timer
        "magnitude": 1.0,
        "instant": True,
        "free_tier": True,
    },
}

# Keep at most this many terminal (settled/cancelled/expired-offer) rows for the
# cockpit history; live (offered/active) rows are never pruned. [NO-CANON].
CONTRACT_HISTORY_KEEP = 20


def _contracts_of(led: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Return the ledger's contracts[] list (a fresh empty list if absent/malformed).

    Does NOT persist — the caller assigns the mutated list back and flag_modifies.
    """
    rows = led.get("contracts")
    return [dict(r) for r in rows] if isinstance(rows, list) else []


def _canonical_days_from_now(days: float, now: datetime) -> str:
    """Wall-clock ISO timestamp ``days`` CANONICAL days ahead of ``now`` (GAME_TIME_SCALE
    compresses the wall span on dev exactly like every other CRT duration)."""
    from datetime import timedelta
    wall_hours = (days * 24.0) / (GAME_TIME_SCALE or 1.0)
    return (now + timedelta(hours=wall_hours)).isoformat()


def active_overclock_multiplier(player: Player, planet_id: Any) -> float:
    """Point-of-use READER (leaf-discipline, mirrors ``tech_modifier``): the production
    multiplier from any ACTIVE Overclock contract on ``planet_id``, else 1.0.

    Pure read — production calls this at the moment it consumes the rate; the effect is
    NEVER written onto the planet (zero-migration guarantee). Stacks additively if (by a
    future tier) more than one active overclock targets the same world; today the start
    path prevents a duplicate active overclock per planet so this returns at most 1.15.
    """
    led = ledger_of(player)
    mult = 1.0
    pid = str(planet_id)
    for row in _contracts_of(led):
        if (
            row.get("kind") == "overclock"
            and row.get("state") == "active"
            and str(row.get("target_planet_id")) == pid
        ):
            mult += float(row.get("magnitude", 0.0) or 0.0)
    return mult


def start_contract(
    db: Session,
    player_id: Any,
    kind: str,
    *,
    target_planet_id: Any = None,
    target_build_id: Any = None,
    offer_id: Optional[str] = None,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Accept/buy a Citadel-Research directive: debit RP (gate) + credits (sink), and
    flip an ``offered`` row to ``active`` (or append a fresh ``active`` row for a direct
    buy). Single-writer on ``research_ledger``; the CALLER commits (mirrors ``unlock_node``).

    Lock order: PLANET row then PLAYER row (the shipped race-safety invariant) when a
    target planet is given; player-only for a player-wide directive.
    """
    now = now if now is not None else datetime.now(UTC)
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)

    spec = CONTRACT_KINDS.get(kind)
    if spec is None:
        return {"success": False, "message": f"Unknown directive kind '{kind}'."}

    # Lock the target planet FIRST (planet-then-player), if any.
    if target_planet_id is not None:
        planet = (
            db.query(Planet)
            .filter(Planet.id == target_planet_id)
            .with_for_update()
            .first()
        )
        if planet is None:
            return {"success": False, "message": "Target planet not found."}
        if planet.owner_id is not None and str(planet.owner_id) != str(player_id):
            return {"success": False, "message": "You do not own the target planet."}

    player = db.query(Player).filter(Player.id == player_id).with_for_update().first()
    if player is None:
        return {"success": False, "message": "Player not found."}

    if not spec["free_tier"]:
        # Tree-gated kinds (none in the T1.5 kernel) would check a node here.
        return {"success": False, "message": f"The {kind} directive is not yet unlocked."}

    led = ledger_of(player)
    rows = _contracts_of(led)

    # Resolve the offer row being accepted (if any). An offer that has already perished
    # or been consumed cannot be accepted.
    offer_row = None
    if offer_id is not None:
        offer_row = next((r for r in rows if r.get("id") == offer_id), None)
        if offer_row is None:
            return {"success": False, "message": "Offer not found."}
        if offer_row.get("state") != "offered":
            return {"success": False, "message": "Offer is no longer available."}
        exp = offer_row.get("offer_expires_at")
        if exp and _parse_iso(exp) is not None and _parse_iso(exp) <= now:
            return {"success": False, "message": "Offer has expired."}

    # One active Overclock per planet (prevents stacked +15% on the same world; also
    # keeps active_overclock_multiplier bounded). Re-buy is allowed once the prior one
    # settles.
    if kind == "overclock" and target_planet_id is not None:
        for r in rows:
            if (
                r.get("kind") == "overclock"
                and r.get("state") == "active"
                and str(r.get("target_planet_id")) == str(target_planet_id)
            ):
                return {"success": False, "message": "This planet already has an active Overclock."}

    rp_cost = int(spec["rp_cost"])
    cr_cost = int(spec["cr_cost"])
    banked_rp = int(led.get("rp", 0) or 0)
    if banked_rp < rp_cost:
        return {
            "success": False,
            "message": f"Insufficient research points. Need {rp_cost}, have {banked_rp}.",
        }
    if (player.credits or 0) < cr_cost:
        return {
            "success": False,
            "message": f"Insufficient credits. Need {cr_cost:,}, have {(player.credits or 0):,}.",
        }

    # Debit the GATE (RP) + the SINK (credits). The credit spend is the bottomless drain.
    led["rp"] = banked_rp - rp_cost
    player.credits = (player.credits or 0) - cr_cost

    instant = bool(spec["instant"])
    started_at = now.isoformat()
    complete_at = None if instant else _canonical_days_from_now(spec["duration_days"], now)

    if offer_row is not None:
        # Flip the existing offered row to active in place.
        for r in rows:
            if r.get("id") == offer_id:
                r["state"] = "active"
                r["rp_cost"] = rp_cost
                r["cr_cost"] = cr_cost
                r["started_at"] = started_at
                r["complete_at"] = complete_at
                if target_planet_id is not None:
                    r["target_planet_id"] = str(target_planet_id)
                if target_build_id is not None:
                    r["target_build_id"] = str(target_build_id)
                new_row = r
                break
    else:
        new_row = {
            "id": f"ctr_{uuid.uuid4().hex}",
            "kind": kind,
            "state": "active",
            "target_planet_id": str(target_planet_id) if target_planet_id is not None else None,
            "target_build_id": str(target_build_id) if target_build_id is not None else None,
            "rp_cost": rp_cost,
            "cr_cost": cr_cost,
            "magnitude": float(spec["magnitude"]),
            "offered_at": None,
            "offer_expires_at": None,
            "started_at": started_at,
            "complete_at": complete_at,
        }
        rows.append(new_row)

    # Instant kinds (Rush) settle the SAME tick they start: mark settled immediately.
    # The actual timer-collapse on the target build is the consuming system's job
    # (read at point-of-use / applied by the build pipeline) — research only records
    # the spend and the settled directive (leaf-discipline).
    if instant:
        new_row["state"] = "settled"
        new_row["complete_at"] = started_at

    led["contracts"] = _prune_contracts(rows)
    player.research_ledger = led
    flag_modified(player, "research_ledger")
    db.flush()

    logger.info(
        "Citadel-Research directive started: player %s kind %s (RP -%s gate, cr -%s sink) "
        "target_planet %s",
        player_id, kind, rp_cost, cr_cost, target_planet_id,
    )
    return {
        "success": True,
        "contract": new_row,
        "rp_remaining": led["rp"],
        "credits_remaining": player.credits,
        "message": f"{kind.capitalize()} directive started.",
    }


def settle_contracts(db: Session, player: Player, now: Optional[datetime] = None,
                     *, _via_settle: bool = False) -> bool:
    """Settle a player's live directives — called INSIDE ``settle()`` step 5 beside the
    faucet sweep (the player row is already held by the sweep). Idempotent:

      * Expire due ``active`` rows (``complete_at <= now``): revert the effect (the
        effect is point-of-use-read so reverting == flipping state to ``settled``),
        mark ``settled``.
      * Perish stale ``offered`` rows (``offer_expires_at <= now`` → ``expired-offer``).
      * Prune terminal rows to ``CONTRACT_HISTORY_KEEP``.

    Returns True iff any row changed (so the caller commits only on a real change).
    The CALLER commits. ``_via_settle`` mirrors the spine guard discipline.
    """
    now = now if now is not None else datetime.now(UTC)
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)

    led = ledger_of(player)
    rows = _contracts_of(led)
    if not rows:
        return False

    changed = False
    for r in rows:
        state = r.get("state")
        if state == "active":
            comp = _parse_iso(r.get("complete_at")) if r.get("complete_at") else None
            if comp is not None and comp <= now:
                r["state"] = "settled"
                changed = True
        elif state == "offered":
            exp = _parse_iso(r.get("offer_expires_at")) if r.get("offer_expires_at") else None
            if exp is not None and exp <= now:
                r["state"] = "expired-offer"
                changed = True

    if not changed:
        return False

    led["contracts"] = _prune_contracts(rows)
    player.research_ledger = led
    flag_modified(player, "research_ledger")
    logger.debug("settle_contracts: player %s — directives settled/expired this tick", player.id)
    return True


def cancel_contract(db: Session, player_id: Any, contract_id: str,
                    now: Optional[datetime] = None) -> Dict[str, Any]:
    """Cancel a directive. Anti-arbitrage refund discipline (MASTER §3.2 / E5):

      * An ACTIVE contract refunds **0% credits** (you bought it, you spent it —
        refunding a near-complete Overclock is timing arbitrage). The effect ends.
      * An accepted-but-not-yet-started OFFER refunds **50% credits** (it never ran).
        In the T1.5 kernel an accepted offer becomes ``active`` immediately, so this
        50% rung applies only to a future "scheduled-but-pending" state; today an
        ``offered`` row was never charged, so cancelling it is a free perish (0 refund,
        nothing was spent).
      * **RP is NEVER refunded** — the hard no-launder invariant (§2.4): no contract
        path returns banked RP into anything spendable.

    Single-writer on ``research_ledger``; the CALLER commits.
    """
    now = now if now is not None else datetime.now(UTC)
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)

    player = db.query(Player).filter(Player.id == player_id).with_for_update().first()
    if player is None:
        return {"success": False, "message": "Player not found."}

    led = ledger_of(player)
    rows = _contracts_of(led)
    row = next((r for r in rows if r.get("id") == contract_id), None)
    if row is None:
        return {"success": False, "message": "Directive not found."}

    state = row.get("state")
    cr_cost = int(row.get("cr_cost", 0) or 0)
    refund = 0
    if state == "active":
        # 0% credit refund on an active contract (anti-arbitrage). Effect ends now.
        refund = 0
        row["state"] = "cancelled"
    elif state == "offered":
        # An un-accepted offer was never charged — perishing it costs/refunds nothing.
        # (The 50% pre-start rung is reserved for a future accepted-but-pending state.)
        refund = 0
        row["state"] = "expired-offer"
    else:
        return {"success": False, "message": f"Directive is '{state}' and cannot be cancelled."}

    # RP is NEVER refunded — invariant §2.4.
    if refund > 0:
        player.credits = (player.credits or 0) + refund

    led["contracts"] = _prune_contracts(rows)
    player.research_ledger = led
    flag_modified(player, "research_ledger")
    db.flush()

    logger.info(
        "Citadel-Research directive cancelled: player %s %s (state was %s, refund %s cr, 0 RP)",
        player_id, contract_id, state, refund,
    )
    return {
        "success": True,
        "refund": refund,
        "credits_remaining": player.credits,
        "message": "Directive cancelled. No RP is refunded.",
    }


def _prune_contracts(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Keep ALL live (offered/active) rows; trim terminal rows to the most recent
    ``CONTRACT_HISTORY_KEEP`` (settled/cancelled/expired-offer) for cockpit history."""
    live = [r for r in rows if r.get("state") in ("offered", "active")]
    terminal = [r for r in rows if r.get("state") not in ("offered", "active")]
    if len(terminal) > CONTRACT_HISTORY_KEEP:
        terminal = terminal[-CONTRACT_HISTORY_KEEP:]
    return live + terminal


def _parse_iso(value: Any) -> Optional[datetime]:
    """Parse an ISO timestamp to an aware UTC datetime, or None on any malformed input."""
    if not isinstance(value, str):
        return None
    try:
        dt = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt
