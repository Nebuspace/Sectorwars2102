"""Turn accounting — the single mutation point for Player.turns.

``Player.turns`` is a regenerating BALANCE; ``Player.lifetime_turns_spent``
is the monotonic cumulative clock the ADR-0042 police arrival watchers
compare against (``arrival_turn_threshold = offense_turn + 2``). A
watcher keyed to the balance would never fire reliably — regen pushes it
back up — so every spend site MUST route through these helpers to keep
the clock honest.

Callers keep their own affordability checks and locking; these helpers
only perform the paired mutation.

ADR-0004 (Continuous Turn Regeneration): the turn pool refills lazily on
read/spend rather than via a once-per-UTC-day reset. ``regenerate_turns``
is the FROZEN HOOK every turn-spend site calls (inside its own
``SELECT ... FOR UPDATE`` row lock, before checking affordability). It
advances ``Player.turns`` by the integer turns earned since the anchor at
the base rate ``1000 / 86400`` t/s, modulated by the player's ARIA
consciousness multiplier, capped at ``Player.max_turns``.
"""

import logging
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, Optional

from sqlalchemy.orm import Session

from src.models.player import Player

logger = logging.getLogger(__name__)

# ADR-0004 base regeneration rate: a full default pool (1000) per 24h.
BASE_TURNS_PER_DAY = 1000
SECONDS_PER_DAY = 86400
BASE_REGEN_RATE = BASE_TURNS_PER_DAY / SECONDS_PER_DAY  # ~0.011574 turns/second


def _aria_bonus_multiplier(player: Player) -> float:
    """Resolve the player's ARIA consciousness regen multiplier (1.0–1.5×).

    ARIA consciousness levelling writes ``Player.aria_bonus_multiplier``
    (see ``aria_personal_intelligence_service.CONSCIOUSNESS_BONUSES`` and the
    trade/movement/combat consciousness hooks). We read that stored value
    directly so regen speed tracks consciousness without a second source of
    truth. Defaults to 1.0× and is clamped to the spec range defensively.
    """
    multiplier = getattr(player, "aria_bonus_multiplier", 1.0)
    if multiplier is None:
        return 1.0
    try:
        multiplier = float(multiplier)
    except (TypeError, ValueError):
        return 1.0
    # Spec range is 1.0×–1.5×; clamp to avoid a corrupt value distorting regen.
    return max(1.0, min(1.5, multiplier))


def _medal_turn_regen_bonus(db: Session, player: Player) -> float:
    """WO-CG — the summed, capped medal ``turn_regen`` bonus (additive delta to
    the regen multiplier) for a player.

    Composed into the ``aria_multiplier`` term in :func:`regenerate_turns` — the
    REGEN RATE ONLY. It must NEVER reach ``_calculate_max_turns`` /
    ``RankingService.calculate_max_turns``: per ADR-0004 the turn CAP deliberately
    excludes the aria/regen multiplier, and the medal regen follows the identical
    exclusion (folding it into the cap would stretch the turn ceiling). The
    combined ``aria + medal`` multiplier is still hard-clamped to 1.5 at the call
    site, so medal regen can never push past the ADR-0004 ceiling.

    Defensive: resolved by ``getattr`` (the medals lane may be absent in some
    deployments) and degrading to 0.0 on any failure so regen is never broken by
    a medal lookup. Already clamped to the blessed +0.05 cap by
    ``get_active_medal_bonuses``."""
    try:
        if player is None or getattr(player, "id", None) is None:
            return 0.0
        import src.services.medal_service as _medal_service
        hook = getattr(_medal_service, "get_active_medal_bonuses", None)
        if not callable(hook):
            return 0.0
        bonuses = hook(db, player.id) or {}
        bonus = float(bonuses.get("turn_regen", 0.0) or 0.0)
        # Never negative (regen is a faucet, never a sink).
        return max(0.0, bonus)
    except Exception as e:  # never let a medal read break turn regen
        logger.error("Medal turn-regen bonus read failed (continuing without): %s", e)
        return 0.0


def regenerate_turns(db: Session, player: Player) -> Dict[str, Any]:
    """Lazily advance ``Player.turns`` for real time elapsed (ADR-0004).

    THE FROZEN HOOK. Every turn-SPEND site calls this inside its existing
    ``SELECT ... FOR UPDATE`` row lock, *before* its affordability check, so
    the pool reflects real elapsed time at the moment of action. Combat,
    movement, and trade-domain spend sites all route through here.

    Mechanics
    ---------
    - ``turns_added = floor(elapsed_seconds * (1000/86400) * aria_multiplier)``
    - anchor = ``player.last_turn_regeneration`` (fallback ``created_at``)
    - cap    = ``player.max_turns`` (recomputed from rank via
      ``RankingService.calculate_max_turns`` so promotions take effect)
    - **No carryover at cap**: if already at/above cap, the anchor is bumped
      to ``now`` so banked excess time does not accumulate.
    - **Anchor advance** only by the integer-turn-equivalent seconds, so the
      sub-turn remainder rolls forward — no rounding drift over long sessions.
    - **Clock-skew guard**: negative elapsed (anchor in the future) clamps to 0.

    Returns a small dict describing what happened (callers may ignore it).
    """
    now = datetime.now(timezone.utc)

    # Recompute the cap from rank so a fresh promotion lifts the ceiling
    # without a separate write path; persist it onto the stored column.
    max_turns = _calculate_max_turns(player)
    if player.max_turns != max_turns:
        player.max_turns = max_turns

    current_turns = player.turns or 0

    # Resolve the regen anchor. Never-regenerated accounts anchor on
    # created_at so a brand-new player isn't instantly flooded, and a
    # never-played account doesn't retroactively bank since the epoch.
    anchor = player.last_turn_regeneration or getattr(player, "created_at", None)
    if anchor is None:
        # No anchor at all — set it now and credit nothing this call.
        player.last_turn_regeneration = now
        return {
            "regenerated": False,
            "turns_added": 0,
            "old_turns": current_turns,
            "new_turns": current_turns,
            "max_turns": max_turns,
        }

    # Normalise to tz-aware UTC for a safe subtraction.
    if anchor.tzinfo is None:
        anchor = anchor.replace(tzinfo=timezone.utc)

    # Already at or over cap: bump the anchor to now (no-carryover) and stop.
    if current_turns >= max_turns:
        player.last_turn_regeneration = now
        return {
            "regenerated": False,
            "turns_added": 0,
            "old_turns": current_turns,
            "new_turns": current_turns,
            "max_turns": max_turns,
        }

    elapsed_seconds = (now - anchor).total_seconds()
    # Clock-skew guard: anchor in the future yields negative elapsed -> 0.
    if elapsed_seconds <= 0:
        if elapsed_seconds < 0:
            # Anchor is ahead of now (admin time edit / skew) — re-baseline.
            player.last_turn_regeneration = now
        return {
            "regenerated": False,
            "turns_added": 0,
            "old_turns": current_turns,
            "new_turns": current_turns,
            "max_turns": max_turns,
        }

    # WO-CG: compose the summed, capped medal turn_regen bonus into the
    # aria_multiplier term — the REGEN RATE ONLY (never the cap; see
    # _medal_turn_regen_bonus + ADR-0004). The combined multiplier is hard-clamped
    # to 1.5 so medal regen can never push past the ADR-0004 ceiling.
    aria_multiplier = _aria_bonus_multiplier(player)
    medal_regen_bonus = _medal_turn_regen_bonus(db, player)
    aria_multiplier = min(1.5, aria_multiplier + medal_regen_bonus)
    rate = BASE_REGEN_RATE * aria_multiplier  # turns per second
    turns_added = int(elapsed_seconds * rate)  # floor toward zero (rate >= 0)

    if turns_added <= 0:
        # Not yet a full turn's worth of time — leave the anchor untouched so
        # the sub-turn remainder keeps accruing toward the next whole turn.
        return {
            "regenerated": False,
            "turns_added": 0,
            "old_turns": current_turns,
            "new_turns": current_turns,
            "max_turns": max_turns,
        }

    new_turns = min(max_turns, current_turns + turns_added)
    actually_added = new_turns - current_turns
    player.turns = new_turns

    if new_turns >= max_turns:
        # Hit the cap this tick — no-carryover: anchor to now so excess time
        # past the cap does not bank for later.
        player.last_turn_regeneration = now
    else:
        # Advance the anchor only by the integer-turn-equivalent seconds so
        # the fractional remainder rolls over (no drift over long sessions).
        consumed_seconds = turns_added / rate if rate > 0 else 0.0
        player.last_turn_regeneration = anchor + _timedelta_seconds(consumed_seconds)

    logger.debug(
        "Regen player %s: +%d turns (%d -> %d, cap=%d, aria=%.2f, elapsed=%.1fs)",
        getattr(player, "id", "?"), actually_added, current_turns, new_turns,
        max_turns, aria_multiplier, elapsed_seconds,
    )

    # Authoritative push (SYSTEMS/turn-regeneration.md): a real credit (N>0)
    # emits ONE player-scoped turn_pool_updated frame so clients refresh
    # without polling. No-op regen (0 added) emits nothing — all the early
    # returns above short-circuit before reaching here. Best-effort only;
    # never fail the spend transaction over a quiet socket.
    if actually_added > 0:
        _emit_turn_pool_update(player, new_turns, max_turns, actually_added,
                               aria_multiplier)

    return {
        "regenerated": actually_added > 0,
        "turns_added": actually_added,
        "old_turns": current_turns,
        "new_turns": new_turns,
        "max_turns": max_turns,
    }


def _emit_turn_pool_update(player: Player, new_turns: int, max_turns: int,
                           turns_added: int, aria_multiplier: float) -> None:
    """Best-effort player-scoped ``turn_pool_updated`` WS push.

    ``regenerate_turns`` is sync and runs inside the caller's row-locked
    ``SELECT ... FOR UPDATE`` transaction; the WS send is async. We mirror the
    proven sync→async pattern in ``docking_service._notify_bumped``: import
    inside the function, grab the running loop, schedule the coroutine with
    ``loop.create_task`` (so it runs after the caller's transaction commits and
    yields — never blocking or breaking the sync spend path), and swallow any
    failure (no loop, no socket) so regen can never crash a spend.

    Routes on the owning User's id (``player.user_id``) — the key
    ``send_personal_message`` uses, per message_service / faction_service.

    Payload (NO-CANON for ``turns_added``): canon SYSTEMS/turn-regeneration.md
    specifies ``{player_id, turns, max_turns, bonus_multiplier}``; the WO also
    requires "turns added", so ``turns_added`` is added beyond canon and FLAGGED.
    """
    try:
        import asyncio
        from src.services.websocket_service import connection_manager

        user_id = getattr(player, "user_id", None)
        if user_id is None:
            return

        loop = asyncio.get_running_loop()
        loop.create_task(connection_manager.send_turn_pool_update(
            str(user_id),
            {
                "player_id": str(getattr(player, "id", "")),
                "turns": new_turns,
                "max_turns": max_turns,
                "turns_added": turns_added,  # NO-CANON: WO-required, beyond spec
                "bonus_multiplier": aria_multiplier,
            },
        ))
    except Exception:
        logger.debug("Skipped turn_pool_updated WS push (no loop or socket)",
                     exc_info=True)


def _timedelta_seconds(seconds: float):
    """timedelta for a (possibly fractional) number of seconds."""
    from datetime import timedelta
    return timedelta(seconds=seconds)


def _calculate_max_turns(player: Player) -> int:
    """Player turn cap = 1000 + military-rank bonus (ADR-0004).

    Delegates to ``RankingService.calculate_max_turns`` (the single rank-bonus
    source of truth). Imported lazily to avoid a service-layer import cycle.
    Falls back to the stored ``max_turns`` (or the 1000 base) if the ranking
    service is unavailable — regen must never crash a spend transaction.
    """
    try:
        from src.services.ranking_service import RankingService
        return RankingService.calculate_max_turns(player)
    except Exception:  # pragma: no cover - defensive
        stored = getattr(player, "max_turns", None)
        return int(stored) if stored else BASE_TURNS_PER_DAY


# Returning-player welcome-back bonus (OPERATIONS/retention.md §"Returning-player
# turn bonus"): a one-time top-up of Player.turns when the player returns after a
# >7-day absence, sized at min(WELCOME_BACK_MAX, days_inactive * WELCOME_BACK_PER_DAY).
WELCOME_BACK_THRESHOLD_DAYS = 7      # bonus only fires for absences strictly > 7 days
WELCOME_BACK_PER_DAY = 50            # turns granted per full day inactive
WELCOME_BACK_MAX = 500              # hard cap (anti-alt-abuse, per canon)
RETURN_BOOST_DAYS = 1               # WO-RE1: a qualifying return opens a 1-day emergent-rep ×1.5 window


def welcome_back(player: Player, prior_last_game_login: Optional[datetime]) -> Dict[str, Any]:
    """Apply the returning-player welcome-back turn bonus, ONCE per return.

    OPERATIONS/retention.md: ``Player.turns`` is topped up by a "welcome back"
    bonus if last login was > 7 days ago — ``min(500, days_inactive × 50)`` —
    capped to prevent alt-account abuse.

    Idempotency is structural, not a flag: the caller passes the player's OLD
    ``last_game_login`` (captured BEFORE this call), and this helper overwrites
    ``player.last_game_login`` to *now*. The next login within 7 days therefore
    measures a fresh, sub-threshold gap and grants 0 — the bonus can only fire
    once per genuine return. ``last_game_login`` is the live login-recency clock
    for the auth path (track_login is called without a db arg, so this is the
    only writer of that column on the live login route).

    The bonus is added to the balance and clamped to ``player.max_turns`` so a
    returning player is topped up *to* their cap at most — never overflowed past
    the ADR-0004 ceiling.

    Args:
        player: the returning Player row (mutated in place; NOT committed here —
            the caller owns the transaction).
        prior_last_game_login: the value of ``player.last_game_login`` captured
            *before* this call. ``None`` (never logged in) grants nothing.

    Returns a small dict describing the outcome (callers may ignore it).
    """
    now = datetime.now(timezone.utc)

    result: Dict[str, Any] = {
        "granted": False,
        "bonus": 0,
        "days_inactive": 0,
        "old_turns": player.turns or 0,
        "new_turns": player.turns or 0,
    }

    # Always advance the recency clock to now — this is what makes the grant
    # one-shot per return (and keeps last_game_login current for retention
    # signals even when no bonus is due).
    player.last_game_login = now

    # Never logged in (or unknown) — no prior absence to reward.
    if prior_last_game_login is None:
        return result

    # Normalise the prior anchor to tz-aware UTC for a safe subtraction.
    anchor = prior_last_game_login
    if anchor.tzinfo is None:
        anchor = anchor.replace(tzinfo=timezone.utc)

    elapsed_days = (now - anchor).total_seconds() / SECONDS_PER_DAY
    # Clock-skew / future-anchor guard: a negative gap rewards nothing.
    if elapsed_days <= 0:
        return result

    days_inactive = int(elapsed_days)  # whole days only (floor)
    result["days_inactive"] = days_inactive

    # Bonus only for absences STRICTLY greater than the threshold.
    if days_inactive <= WELCOME_BACK_THRESHOLD_DAYS:
        return result

    # WO-RE1: a qualifying return (>threshold days) opens a 1-day emergent-rep ×1.5 boost window
    # (the rep half of welcome-back; apply_emergent_action reads return_boost_until). Set alongside
    # the F4 turn bonus so one return-detection drives both.
    player.return_boost_until = now + timedelta(days=RETURN_BOOST_DAYS)

    bonus = min(WELCOME_BACK_MAX, days_inactive * WELCOME_BACK_PER_DAY)
    if bonus <= 0:
        return result

    current_turns = player.turns or 0
    # Top up toward the cap — never push the balance past max_turns.
    max_turns = _calculate_max_turns(player)
    new_turns = min(max_turns, current_turns + bonus)
    actually_added = new_turns - current_turns
    if actually_added <= 0:
        # Already at/above cap — nothing to grant this return.
        return result

    player.turns = new_turns

    logger.info(
        "Welcome-back bonus for player %s: +%d turns (%d -> %d, %d days inactive)",
        getattr(player, "id", "?"), actually_added, current_turns, new_turns,
        days_inactive,
    )

    result.update({
        "granted": True,
        "bonus": actually_added,
        "new_turns": new_turns,
    })
    return result


def spend_turns(player: Player, amount: int) -> None:
    """Deduct ``amount`` turns from the balance and advance the lifetime
    clock. The caller has already verified affordability."""
    player.turns -= amount
    player.lifetime_turns_spent = (player.lifetime_turns_spent or 0) + amount


def refund_turns(player: Player, amount: int) -> None:
    """Reverse a prior spend (e.g. ADR-0029 warp-gate Phase 3 cancel).
    Decrements the lifetime clock — a refunded action never happened for
    arrival-watcher purposes."""
    player.turns += amount
    player.lifetime_turns_spent = max(0, (player.lifetime_turns_spent or 0) - amount)
