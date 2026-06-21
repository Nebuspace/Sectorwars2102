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
from datetime import datetime, UTC
from typing import Any, Dict, Optional

from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from src.models.player import Player
from src.models.planet import Planet
from src.services import tech_tree

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


def sweep_research_faucet(db: Session, planet: Planet) -> bool:
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
    """
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

    if not plausibly_first and this_planet_rp <= 0:
        # Steady-state, nothing to drain on this planet. Pure no-op — no player
        # lock acquired. (The aggregate-needing case must still proceed even with
        # this planet at 0, because OTHER planets may carry pre-kernel RP.)
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
            led["rp"] = int(led.get("rp", 0)) + drained
            player.research_ledger = led
            flag_modified(player, "research_ledger")
            logger.debug(
                "Research faucet drain (post-race): planet %s owner %s — +%s RP",
                planet.id, player.id, drained,
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

    led = ledger_of(player)
    drained = _zero_planet_faucet(planet)
    if drained <= 0:
        # Nothing changed. Don't roll back — the caller decides commit/rollback
        # based on realize_production too; locks release at txn end.
        return False
    led["rp"] = int(led.get("rp", 0)) + drained
    player.research_ledger = led
    flag_modified(player, "research_ledger")
    logger.debug(
        "Research faucet drain: planet %s owner %s — +%s RP (now %s)",
        planet.id, player.id, drained, led["rp"],
    )
    return True
