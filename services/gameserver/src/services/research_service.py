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
import threading
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


# ===========================================================================
# T1.5-9 / CRT-4 — THE NOTIFICATION COCKPIT: the post-commit emit seam
# (CRT-T15-MASTER §5.2/§5.3/§5.8). Notification-driven, ~0 clicks/day on a
# healthy empire. The transport (send_personal_message → the new_message
# escalation ladder) is shipped + proven; this is the PRODUCER side.
#
# WHY A PENDING BUFFER (not a direct broadcast):
#   The faucet sweep + settle_contracts run inside settle()'s step 5, which is
#   driven from a WORKER THREAD (asyncio.to_thread in the scheduler) — there is
#   NO running event loop there, so a worker-thread→loop bridge is FORBIDDEN
#   (the same discipline genesis_progress follows: compose the frame BEFORE the
#   commit, broadcast it AFTER, on the loop). So the writer STAGES composed
#   frames into this module-level buffer; the loop-side caller drains them via
#   ``drain_pending_research_frames()`` and hands them to ``_broadcast_events``
#   POST-COMMIT (no-rollback: a WS hiccup can never roll back the settle). Each
#   staged frame carries the recipient User id so the drainer can route per-user
#   via ``connection_manager.send_personal_message`` — exactly the frozen
#   cross-zone contract (the client handles these in WebSocketContext's
#   generalHandler). The buffer is bounded so a never-draining process (e.g. the
#   cockpit-only deploy before the scheduler drains) can't grow unbounded.
# ===========================================================================
_PENDING_FRAMES: List[Dict[str, Any]] = []
_PENDING_FRAMES_LOCK = threading.Lock()
_PENDING_FRAMES_MAX = 5000          # hard bound so a non-draining process can't grow it forever


def _stage_frame(recipient_user_id: Any, frame: Dict[str, Any]) -> None:
    """Stage a composed WS frame for POST-COMMIT broadcast to one player (§5.2).

    ``recipient_user_id`` is the owning User's id (the key send_personal_message
    routes on — Planet.owner_id is the PLAYER id, so callers resolve the User id
    via player.user_id before staging). Best-effort: a malformed/owner-less frame
    is dropped, never raised — the underlying settle must not be disturbed by an
    emit. Bounded: once at capacity the oldest staged frame is dropped (a stale
    notification matters less than memory safety).
    """
    if recipient_user_id is None or not isinstance(frame, dict):
        return
    staged = dict(frame)
    staged["_recipient_user_id"] = str(recipient_user_id)
    with _PENDING_FRAMES_LOCK:
        _PENDING_FRAMES.append(staged)
        if len(_PENDING_FRAMES) > _PENDING_FRAMES_MAX:
            # Drop the oldest overflow (FIFO) — never block the writer.
            del _PENDING_FRAMES[: len(_PENDING_FRAMES) - _PENDING_FRAMES_MAX]


def drain_pending_research_frames() -> List[Dict[str, Any]]:
    """Atomically drain + return all staged frames (the loop-side post-commit
    broadcaster calls this AFTER the settle commit, then routes each frame to
    ``send_personal_message`` keyed on ``_recipient_user_id``). Returns an empty
    list on the steady-state quiet path (no offers generated, no contracts
    settled, no band-cross) so the broadcast step is a cheap no-op.
    """
    with _PENDING_FRAMES_LOCK:
        if not _PENDING_FRAMES:
            return []
        drained = list(_PENDING_FRAMES)
        _PENDING_FRAMES.clear()
    return drained


async def broadcast_pending_research_frames() -> int:
    """Loop-side POST-COMMIT broadcaster (§5.2): drain the staged frames and send
    each to its recipient via the shipped per-user transport
    (``connection_manager.send_personal_message`` — the same primitive
    genesis_progress uses). MUST be awaited ON THE EVENT LOOP, AFTER the settle
    commit succeeded (never from the worker thread the sweep runs in — a
    worker-thread→loop bridge is forbidden). Best-effort per frame: a WS hiccup is
    logged, never raised, so a quiet/closed socket can never disturb the economy.

    Returns the number of frames actually dispatched (0 on the quiet path). Wire
    points (both on the loop): the cockpit route calls this on read (delivers any
    frames staged since the player last looked), and the scheduler's
    ``_run_planetary_advance_sync`` consumer should call it POST-COMMIT each tick
    (the one-line true-push wiring — mirrors how genesis_progress is broadcast
    after its sweep, npc_scheduler_service ~4475). Strips the internal
    ``_recipient_user_id`` routing key before sending the frozen client shape.
    """
    frames = drain_pending_research_frames()
    if not frames:
        return 0
    from src.services.websocket_service import connection_manager

    sent = 0
    for frame in frames:
        recipient = frame.pop("_recipient_user_id", None)
        if not recipient:
            continue
        try:
            await connection_manager.send_personal_message(str(recipient), frame)
            sent += 1
        except Exception:
            logger.exception(
                "Citadel-Research frame broadcast failed for recipient %s (type %s)",
                recipient, frame.get("type"),
            )
    return sent


# --- The proactive-ARIA emit seam (§5.8) — PURE READ, writes NOTHING ---------
# ARIA has no proactive emit path today; the "ARIA: buy Overclock on Planet X"
# telegraph is net-new CRT-4 work composed HERE as the human text of each frame.
# These composers READ settled state only — they fire NOTHING into the substrate
# and the offer GENERATION (which world/kind/magnitude) lives in the writer
# (settle_contracts); ARIA only NARRATES the already-decided frame. Copy is the
# §5.5/§5.10 day-one-true guard: NO "Doctrine" string (the lever does not exist
# in T1.5); headroom copy points at a real T1.5 action (finish/expand worlds).

def _aria_offer_text(kind: str, planet_name: Optional[str]) -> str:
    """ARIA narration for a generated directive offer (§5.8). Pure read."""
    where = f" on {planet_name}" if planet_name else ""
    if kind == "overclock":
        return f"ARIA: {planet_name or 'a frontier world'} could push harder — buy Overclock{where} for a temporary production surge."
    if kind == "rush":
        return f"ARIA: a build{where} is dragging — Rush it to collapse the timer."
    if kind == "stabilize":
        return f"ARIA: {planet_name or 'a contested world'} is slipping — Stabilize{where} to bleed off instability."
    return f"ARIA: a research directive{where} is available."


def _aria_settled_text(kind: str, planet_name: Optional[str]) -> str:
    """ARIA narration for a settled/expired active directive (§5.8). Pure read."""
    where = f" on {planet_name}" if planet_name else ""
    label = (kind or "directive").capitalize()
    return f"ARIA: the {label} directive{where} has run its course."


def _aria_governor_text(rp_per_day: int, throughput_pct: int) -> str:
    """ARIA narration for the band-cross governor ping (§5.5/§5.10). Pure read.

    DAY-ONE-TRUE: never names a non-existent lever (no "Doctrine"). Points at a
    real T1.5 action — finishing/expanding worlds raises the cap (the capstone
    lift is shipped ON, §2.6) — and frames the taper honestly as "full throughput
    for the current frontier," never "you've been capped."
    """
    return (
        f"ARIA: your empire's research is at full throughput for its current frontier "
        f"({rp_per_day} RP/day, ~{throughput_pct}% of raw) — finishing or expanding worlds raises it."
    )


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


def _maybe_stage_governor_status(player: Player, led: Dict[str, Any], soft_cap: float,
                                 raw_before: int, raw_after: int) -> None:
    """Stage the ``rp_governor_status`` frame ONCE on a band-cross into the taper
    (§5.2/§5.5), and NEVER on a healthy under-cap player (§5.9 #3).

    Band-cross = the per-empire daily raw RP sum crossed ``soft_cap`` THIS sweep
    (``raw_before <= soft_cap < raw_after``). Gated to fire at most once per
    canonical day via ``gov_status_pinged_day`` in the ledger (reset implicitly on
    day-roll because the bucket changes), so a whale that stays over-cap all day is
    pinged exactly once, not every per-planet sweep. With ``soft_cap == inf`` (the
    reproduce-exactly off value) the cross condition can never be True → no frame
    ever fires (byte-identical to today). Copy is day-one-TRUE: it names no
    non-existent lever (§5.10 — no "Doctrine") and points at a real T1.5 action.

    PURE composer + stage — writes the ledger's ping-flag only (a notification
    bookkeeping key, not an economy quantity); fires nothing into the substrate.
    """
    if soft_cap == GOV_SOFT_CAP_OFF or math.isinf(soft_cap):
        return                                   # off-switch: never pings (== today)
    if not (raw_before <= soft_cap < raw_after):
        return                                   # no band-cross this sweep (the common case)
    bucket = led.get("gov_day")
    if led.get("gov_status_pinged_day") == bucket:
        return                                   # already pinged this canonical day

    led["gov_status_pinged_day"] = bucket
    # The headroom readout numbers (§5.5): RP/day = the GOVERNED daily total; the
    # throughput % = governed / raw (how much of the raw faucet survives the taper).
    governed = int(math.floor(governed_rp(float(raw_after), soft_cap)))
    throughput_pct = int(round((governed / raw_after) * 100)) if raw_after > 0 else 100
    _stage_frame(
        getattr(player, "user_id", None),
        {
            "type": "rp_governor_status",
            "rpPerDay": governed,
            "throughputPct": throughput_pct,
            "ariaText": _aria_governor_text(governed, throughput_pct),
            "priority": "normal",
            "delivery": ["inbox", "toast"],     # inbox/toast, NEVER modal (§5.2)
        },
    )
    logger.info(
        "rp_governor_status band-cross: player %s — governed %s RP/day (~%s%% throughput, soft_cap %s)",
        player.id, governed, throughput_pct, soft_cap,
    )


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
# behaviour byte-identical to today (acceptance §3.6.2).
FAUCET_CREDIT_COPAY = 0.10          # × governed_rp × RP_TO_CREDIT_RATE cr/day (Max-RULED, WO-COPAY/#9: raised 0.05→0.10 so the idle-whale floor clears −3k — idle net +1,060−4,471 ≈ −3,411/day, inside the [−8k,−3k] gate band)


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


# --- World classification (CRT-4 §5.3/§5.4) — PURE READ ----------------------
# The offer generator + the empire R&D summary need a per-PLANET rollup of the
# same frontier/contested/done axis the decay-rescope (structures.decay_pressure)
# resolves per-PLOT. A world is:
#   contested — under_siege OR within the post-siege tail (a contested world is
#               where the contract economy lives; it RAISES offers but never the
#               "done, free to hold" relief).
#   done      — capstoned (CRT-3 confirmed_biome) AND its grid is in-band — a
#               finished, peaceful world. RAISES NO OFFER (§5.3: ~0 clicks/day on
#               a healthy empire); the §5.10 guard means until CRT-3 ships
#               confirmed_biome no world reads "done" (it reads frontier/banded).
#   frontier  — everything else (not yet in-band / not yet capstoned) — the world
#               the contract economy should keep paying to push. RAISES OFFERS.
# Reuses the structures helpers so classification can never drift from the decay
# tiers they drive. Fully defensive — any hiccup collapses to "frontier".

def _planet_grid_in_band(structures: Dict[str, Any], planet: Planet) -> bool:
    """True iff EVERY terraform plot on the planet's grid is within the DONE band
    (reuses structures._plot_is_banded). The plots are a flat list directly under
    ``structures['plots']`` (structures.py:645). An empty/absent grid is NOT
    in-band (a world with no terraform grid is still frontier — nothing has
    settled yet)."""
    from src.services import structures as _st

    if not isinstance(structures, dict):
        return False
    plots = structures.get("plots")
    if not isinstance(plots, list) or not plots:
        return False
    try:
        return all(_st._plot_is_banded(p, planet) for p in plots if isinstance(p, dict))
    except Exception:
        return False


def classify_world(planet: Planet, now: Optional[datetime] = None) -> str:
    """Return "contested" | "done" | "frontier" for one owned planet (§5.3/§5.4).

    Pure read of already-persisted state (Planet columns + the structures blob);
    writes nothing, fires nothing. Mirrors the contested-wins-over-relief order
    of structures.decay_pressure so the cockpit's worlds-frontier-vs-done count
    never contradicts the decay the player feels."""
    from src.services import structures as _st

    now = now if now is not None else datetime.now(UTC)
    try:
        structures = planet.structures if isinstance(planet.structures, dict) else {}
        if getattr(planet, "under_siege", False):
            return "contested"
        if _st._recently_contested(planet, structures, _st._aware(now)):
            return "contested"
        # DONE only if capstoned AND in-band — the §5.10 guard keeps a pre-CRT-3
        # world out of "done" (confirmed_biome absent → not capstoned → frontier).
        if _st._planet_is_capstoned(structures) and _planet_grid_in_band(structures, planet):
            return "done"
        return "frontier"
    except Exception:
        logger.debug("classify_world fell back to frontier for planet %s",
                     getattr(planet, "id", "?"), exc_info=True)
        return "frontier"


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
    player = db.query(Player).filter(Player.id == player_id).populate_existing().with_for_update().first()
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
    # Capture the per-empire daily raw sum BEFORE this sweep folds in `drained`, for
    # the band-cross governor ping (§5.2). Day-roll → raw_before is 0 (a new day's
    # sum starts at 0 inside _gov_apply); same day → the running sum carried so far.
    raw_before = (
        int(led.get("gov_raw_today", 0) or 0)
        if led.get("gov_day") == _canonical_day_bucket()
        else 0
    )
    credited = _gov_apply(led, drained, soft_cap)
    led["rp"] = int(led.get("rp", 0)) + credited
    # T1.5-9: ping rp_governor_status ONCE on the band-cross into the taper, NEVER on
    # a healthy under-cap player (§5.9 #3). raw_after is the post-fold running sum
    # _gov_apply just wrote. soft_cap=inf never crosses → no frame (== today).
    _maybe_stage_governor_status(
        player, led, soft_cap, raw_before, int(led.get("gov_raw_today", 0) or 0)
    )
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
# --- T1.5-6 STABILIZE INSTABILITY COUPLING (CRT-T15-MASTER §4.5, line ~609) ---
# Stabilize is the ONE designed credit<->instability touch-point: spend credits
# (the sink) to bleed instability off a target planet. Instability lives in the
# SHARED JSONB key terraform_meta.instability (a float INSIDE the planet.structures
# blob; absent/0 == today/off). On SETTLE a Stabilize contract DECREMENTS it by
# STABILIZE_INSTAB_REDUCTION, clamped >= 0. C1 (structures.decay_pressure) READS the
# same key as an additive decay term (higher instability => faster decay; <=0 == today).
# The coupling is via this shared key ONLY — no cross-file call.
#
# REPRODUCE-EXACTLY OFF-SWITCH: this whole strand is purely additive. An empire that
# offers/accepts no Stabilize spends nothing and writes nothing (an empty contracts[]
# is byte-identical to today). The instability write is a NO-OP when instability is
# already 0/absent (max(0, 0 - 10) == 0 leaves the key effectively today). Lowering
# STABILIZE_INSTAB_REDUCTION to 0 makes the settle a no-op too.
STABILIZE_INSTAB_REDUCTION = 10.0   # instability points removed when a Stabilize settles (frozen cross-worker contract)


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
    "stabilize": {
        "rp_cost": 150,             # the GATE (recoverable; governs pacing — spec line ~609)
        "cr_cost": 20000,           # the SINK (Max-ruled — the credit<->instability touch-point)
        "duration_days": 0,         # instant — the instability bleed applies on settle (same tick)
        "magnitude": STABILIZE_INSTAB_REDUCTION,   # -10 instability on the target planet (frozen contract #1)
        "instant": True,
        "free_tier": True,
    },
}

# Keep at most this many terminal (settled/cancelled/expired-offer) rows for the
# cockpit history; live (offered/active) rows are never pruned. [NO-CANON].
CONTRACT_HISTORY_KEEP = 20

# --- T1.5-9 / CRT-4 OFFER GENERATION (§5.3 — generated, NEVER browsed) --------
# The sweep GENERATES a perishable contract_offer for a frontier/contested world
# on a band crossing; a done/uncontested world raises NONE → ~0 clicks/day on a
# healthy empire (§5.3/§5.9). The offer perishes free at offer_expires_at (it
# was never charged — accepting it is the only spend). [NO-CANON] windows below.
#
# RATE-LIMIT (the ~0-clicks budget): at most ONE live offer per empire at a time
# AND a per-empire cooldown between generations, both keyed in the ledger so the
# generation is idempotent across the per-planet sweep (every owned planet visits
# the sweep, but only the FIRST eligible one this cooldown window raises an offer).
# An empire with a live offer or in cooldown generates nothing; a done empire
# (no frontier/contested world) generates nothing. OFF-SWITCH: OFFER_GEN_ENABLED
# = False makes generation a pure no-op (an empty offer set is byte-identical to
# the contract-pipeline reproduce-exactly baseline).
OFFER_GEN_ENABLED = True
OFFER_EXPIRES_DAYS = 1.0            # canonical days an offer survives before perishing free (§9.3)
OFFER_GEN_COOLDOWN_DAYS = 1.0      # min canonical days between offer generations per empire (anti-spam)


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


def display_magnitude(kind: str, raw_magnitude: float) -> int:
    """The human-meaningful integer effect size for an offer/frame (frozen contract
    ``magnitude:int``). Overclock's raw magnitude is a 0.15 FRACTION → present it as
    the PERCENT (15) so the client's ``⬆ {magnitude}`` reads as a real number, not a
    rounded-to-0. Rush/Stabilize raw magnitudes are already whole effect sizes
    (1 timer collapsed / 10 instability removed) → round as-is. Pure read."""
    raw = float(raw_magnitude or 0.0)
    if kind == "overclock":
        return int(round(raw * 100))     # 0.15 → 15 (%)
    return int(round(raw))               # rush 1.0 → 1; stabilize 10.0 → 10


def _has_live_offer(rows: List[Dict[str, Any]]) -> bool:
    """True iff the empire already has an un-perished offered directive (anti-spam:
    one live offer per empire at a time — §5.3 ~0-clicks budget)."""
    return any(r.get("state") == "offered" for r in rows)


def _offer_cooldown_active(led: Dict[str, Any], now: datetime) -> bool:
    """True iff the empire generated an offer within OFFER_GEN_COOLDOWN_DAYS
    (canonical). Keyed by ``last_offer_gen_at`` in the ledger so generation is
    idempotent across the per-planet sweep (only the first eligible planet this
    window raises an offer; the rest see the cooldown stamp and skip)."""
    raw = led.get("last_offer_gen_at")
    seen = _parse_iso(raw) if raw else None
    if seen is None:
        return False
    elapsed_canonical_days = (
        (now - seen).total_seconds() / 3600.0 * GAME_TIME_SCALE / 24.0
    )
    return 0.0 <= elapsed_canonical_days < OFFER_GEN_COOLDOWN_DAYS


def _pick_offer_for_world(world_class: str, planet: Planet,
                          rows: List[Dict[str, Any]]) -> Optional[str]:
    """Choose which directive KIND to offer a frontier/contested world (§5.3), or
    None if this world should raise nothing right now. Pure read.

      * contested → Stabilize (bleed instability — the contract economy's home).
      * frontier  → Overclock (the workhorse re-buyable drain) UNLESS the world
                    already carries an active Overclock (one per planet, mirrors
                    start_contract's guard) — then nothing (don't dangle a buy
                    the engine will reject).
      * done      → None (a finished world raises no offer — §5.3).
    """
    if world_class == "done":
        return None
    if world_class == "contested":
        return "stabilize"
    # frontier
    pid = str(planet.id)
    for r in rows:
        if (
            r.get("kind") == "overclock"
            and r.get("state") == "active"
            and str(r.get("target_planet_id")) == pid
        ):
            return None
    return "overclock"


def maybe_generate_offer(db: Session, player: Player, now: datetime) -> bool:
    """Generate at most ONE perishable directive offer for a frontier/contested
    world this empire owns, on a band crossing (§5.3 — generated, NEVER browsed).

    Stages a ``contract_offer`` frame for POST-COMMIT broadcast (the writer; ARIA
    only narrates it, §5.8). Single-writer on ``research_ledger`` — mutates the
    ledger IN PLACE (the CALLER assigns + flag_modifies + commits). Returns True
    iff an offer was generated (so the caller knows the ledger changed).

    Gating (the ~0-clicks budget): no-op if disabled, if the empire already has a
    live offer, if the per-empire generation cooldown is active, or if the empire
    has no frontier/contested world (a done empire raises none). A perished offer
    cost nothing — the only spend is accepting it (start_contract).
    """
    if not OFFER_GEN_ENABLED:
        return False
    led = ledger_of(player)
    rows = _contracts_of(led)
    if _has_live_offer(rows) or _offer_cooldown_active(led, now):
        return False

    # Find the player's owned colonized planets (owner_id == the PLAYER id; the
    # secondary player_planets m2m mirrors owner_id, which the kernel sets on
    # colonize — owner_id is the authoritative single-owner column here).
    owned = (
        db.query(Planet)
        .filter(Planet.owner_id == player.id)
        .order_by(Planet.id)
        .all()
    )
    if not owned:
        return False

    chosen_planet: Optional[Planet] = None
    chosen_kind: Optional[str] = None
    for planet in owned:
        wc = classify_world(planet, now)
        if wc == "done":
            continue                              # a finished world raises none (§5.3)
        kind = _pick_offer_for_world(wc, planet, rows)
        if kind is not None:
            chosen_planet = planet
            chosen_kind = kind
            break

    if chosen_planet is None or chosen_kind is None:
        # No frontier/contested world wants an offer right now (e.g. a done empire,
        # or every frontier already carries an active Overclock). Stamp the cooldown
        # anyway so we don't re-scan every per-planet sweep — and return False (no
        # ledger change worth committing for the cooldown stamp alone is acceptable;
        # we DON'T stamp on a pure scan-miss so a world finishing a build inside the
        # cooldown window can still raise its offer promptly).
        return False

    spec = CONTRACT_KINDS.get(chosen_kind, {})
    offer_id = f"ctr_{uuid.uuid4().hex}"
    planet_name = getattr(chosen_planet, "name", None) or "Unnamed World"
    offer_row = {
        "id": offer_id,
        "kind": chosen_kind,
        "state": "offered",
        "target_planet_id": str(chosen_planet.id),
        "target_build_id": None,
        "rp_cost": int(spec.get("rp_cost", 0)),
        "cr_cost": int(spec.get("cr_cost", 0)),
        "magnitude": float(spec.get("magnitude", 0.0) or 0.0),
        "offered_at": now.isoformat(),
        "offer_expires_at": _canonical_days_from_now(OFFER_EXPIRES_DAYS, now),
        "started_at": None,
        "complete_at": None,
    }
    rows.append(offer_row)
    led["contracts"] = _prune_contracts(rows)
    led["last_offer_gen_at"] = now.isoformat()
    player.research_ledger = led
    flag_modified(player, "research_ledger")

    # Stage the headline contract_offer frame for post-commit broadcast (§5.2).
    # ARIA narrates the already-decided offer (§5.8 — pure read, writes nothing).
    _stage_frame(
        getattr(player, "user_id", None),
        {
            "type": "contract_offer",
            "offer": {
                "id": offer_id,
                "kind": chosen_kind,
                "planetId": str(chosen_planet.id),
                "planetName": planet_name,
                "rpCost": int(spec.get("rp_cost", 0)),
                "crCost": int(spec.get("cr_cost", 0)),
                "magnitude": display_magnitude(chosen_kind, float(spec.get("magnitude", 0.0) or 0.0)),
                "expiresAt": offer_row["offer_expires_at"],
            },
            "ariaText": _aria_offer_text(chosen_kind, planet_name),
            "priority": "normal",
            "delivery": ["inbox", "toast"],
        },
    )
    logger.info(
        "Citadel-Research offer generated: player %s kind %s on planet %s (%s), perishes %s",
        player.id, chosen_kind, chosen_planet.id, planet_name, offer_row["offer_expires_at"],
    )
    return True


def _settle_stabilize_instability(db: Session, target_planet_id: Any) -> bool:
    """The one designed credit<->instability touch-point (T1.5-6, §4.5).

    On a Stabilize SETTLE, DECREMENT the shared JSONB key ``terraform_meta.instability``
    by ``STABILIZE_INSTAB_REDUCTION``, clamped ``>= 0``, on the target planet. The key
    lives INSIDE the ``planet.structures`` blob (NOT its own column), so we re-read the
    locked planet's structures, mutate in place, re-assign, and ``flag_modified`` the
    ``structures`` column (frozen cross-worker contract #1 — the writer of the structures
    blob must flag it). C1 (``structures.decay_pressure``) READS this same key.

    Re-reads ``structures`` FRESH under the held planet lock to avoid clobbering a
    concurrent C1 decay write to the same blob. Returns True iff the key was changed.

    OFF-SWITCH / no-op: instability absent or already 0 → ``max(0, 0 - 10) == 0`` leaves
    the key effectively today (we still set it to 0.0 / leave it absent — no terrain
    change). With ``STABILIZE_INSTAB_REDUCTION == 0`` this is a pure no-op.
    """
    if target_planet_id is None or STABILIZE_INSTAB_REDUCTION <= 0:
        return False
    planet = (
        db.query(Planet)
        .filter(Planet.id == target_planet_id)
        .with_for_update()
        .first()
    )
    if planet is None:
        return False
    structures = planet.structures if isinstance(planet.structures, dict) else {}
    tmeta = structures.get("terraform_meta")
    current = 0.0
    if isinstance(tmeta, dict):
        try:
            current = float(tmeta.get("instability", 0.0) or 0.0)
        except (TypeError, ValueError):
            current = 0.0
    if current <= 0.0:
        # Already at/below the floor — nothing to reduce. No-op (today byte-identical:
        # absent/0 instability means C1 applies no extra decay term).
        return False
    new_val = max(0.0, current - STABILIZE_INSTAB_REDUCTION)
    new_structures = dict(structures)
    new_tmeta = dict(tmeta) if isinstance(tmeta, dict) else {}
    new_tmeta["instability"] = new_val
    new_structures["terraform_meta"] = new_tmeta
    planet.structures = new_structures
    flag_modified(planet, "structures")  # frozen contract #1: flag the structures blob
    logger.info(
        "Stabilize settled: planet %s instability %.3f -> %.3f (-%s, clamped >=0)",
        planet.id, current, new_val, STABILIZE_INSTAB_REDUCTION,
    )
    return True


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

    player = db.query(Player).filter(Player.id == player_id).populate_existing().with_for_update().first()
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

    # Instant kinds (Rush, Stabilize) settle the SAME tick they start: mark settled
    # immediately. For Rush the actual timer-collapse on the target build is the
    # consuming system's job (read at point-of-use / applied by the build pipeline) —
    # research only records the spend and the settled directive (leaf-discipline). For
    # Stabilize the settle IS the designed credit<->instability touch-point: on settle
    # it DECREMENTS the shared terraform_meta.instability key (frozen contract #1, §4.5).
    if instant:
        new_row["state"] = "settled"
        new_row["complete_at"] = started_at
        if kind == "stabilize":
            _settle_stabilize_instability(db, target_planet_id)

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


def _planet_name_for(db: Session, planet_id: Any) -> Optional[str]:
    """Best-effort planet display name for a settled/offered frame (pure read).
    None on any miss — the frame just omits the name then."""
    if planet_id is None:
        return None
    try:
        row = db.query(Planet.name).filter(Planet.id == planet_id).first()
        return row[0] if row else None
    except Exception:
        return None


def settle_contracts(db: Session, player: Player, now: Optional[datetime] = None,
                     *, _via_settle: bool = False) -> bool:
    """Settle a player's live directives — called INSIDE ``settle()`` step 5 beside the
    faucet sweep (the player row is already held by the sweep). Idempotent:

      * Expire due ``active`` rows (``complete_at <= now``): revert the effect (the
        effect is point-of-use-read so reverting == flipping state to ``settled``),
        mark ``settled``, and STAGE a one-shot ``contract_settled`` frame (§5.2).
      * Perish stale ``offered`` rows (``offer_expires_at <= now`` → ``expired-offer``).
        A perished offer fires NO frame (silence is correct — an ignored offer
        perishing free is expected, never a ping; §5.3/§5.9 #4).
      * GENERATE at most one fresh perishable offer for a frontier/contested world
        (``maybe_generate_offer`` — the §5.3 keystone; stages a ``contract_offer``).
      * Prune terminal rows to ``CONTRACT_HISTORY_KEEP``.

    Returns True iff any row changed (so the caller commits only on a real change).
    The CALLER commits; the staged frames are broadcast POST-COMMIT by the
    loop-side drainer (``drain_pending_research_frames``). ``_via_settle`` mirrors
    the spine guard discipline.
    """
    now = now if now is not None else datetime.now(UTC)
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)

    led = ledger_of(player)
    rows = _contracts_of(led)

    changed = False
    settled_frames: List[Dict[str, Any]] = []
    for r in rows:
        state = r.get("state")
        if state == "active":
            comp = _parse_iso(r.get("complete_at")) if r.get("complete_at") else None
            if comp is not None and comp <= now:
                r["state"] = "settled"
                changed = True
                # T1.5-6: a Stabilize that settles via the timer (not the instant path)
                # applies the SAME credit<->instability touch-point on settle — DECREMENT
                # the shared terraform_meta.instability key (§4.5, frozen contract #1).
                # In the T1.5 kernel Stabilize is instant (settles in start_contract), so
                # this branch only fires if a future tier gives Stabilize a duration; it
                # keeps the bleed co-located with the `settled` transition either way.
                if r.get("kind") == "stabilize":
                    _settle_stabilize_instability(db, r.get("target_planet_id"))
                # Compose the one-shot contract_settled frame (§5.2). Stage it AFTER
                # the commit succeeds — collect now, stage once the ledger write is in.
                pname = _planet_name_for(db, r.get("target_planet_id"))
                kind = r.get("kind") or "directive"
                settled_frames.append({
                    "type": "contract_settled",
                    "planetName": pname,
                    "kind": kind,
                    "ariaText": _aria_settled_text(kind, pname),
                    "priority": "normal",
                    "delivery": ["inbox", "toast"],
                })
        elif state == "offered":
            exp = _parse_iso(r.get("offer_expires_at")) if r.get("offer_expires_at") else None
            if exp is not None and exp <= now:
                r["state"] = "expired-offer"
                changed = True       # a perished offer fires NO frame (§5.3 — silence is correct)

    if changed:
        led["contracts"] = _prune_contracts(rows)
        player.research_ledger = led
        flag_modified(player, "research_ledger")
        logger.debug("settle_contracts: player %s — directives settled/expired this tick", player.id)

    # GENERATE a fresh perishable offer for a frontier/contested world (§5.3). This
    # writes the ledger itself (and stages the contract_offer frame) when it fires.
    offer_made = maybe_generate_offer(db, player, now)

    # Stage the contract_settled frames only now (after the ledger mutation above)
    # — they ride the SAME post-commit broadcast as the offer frame.
    for f in settled_frames:
        _stage_frame(getattr(player, "user_id", None), f)

    return bool(changed or offer_made)


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

    player = db.query(Player).filter(Player.id == player_id).populate_existing().with_for_update().first()
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
