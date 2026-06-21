"""
Medal service — relational award lifecycle (ADR-0028).

Medals now live in two relational tables instead of ``Player.settings`` JSONB:

* ``medals`` (catalog)        — seeded from :mod:`src.services.medal_catalog`.
* ``player_medals`` (awards)  — one row per (player, medal); UNIQUE(player_id, medal_id).

The UNIQUE constraint is the idempotency keystone (ADR-0028): a medal can be
awarded at most once per player, and concurrent award attempts are defeated at
the DB layer.

This module exposes:

* :func:`award_medal` — module-level idempotent core award (exact signature per task).
* :func:`check_and_award_combat_medals` — FROZEN dispatcher hook the combat lane
  calls: ``(db, killer_player, context)``. Defensive: never raises into combat.
* Analogous trade / exploration dispatchers.
* :class:`MedalService` — preserves the legacy method surface
  (``get_player_medals``, ``check_combat_medals``, ``check_trading_medals``,
  ``check_exploration_medals``) now backed by the relational tables, so existing
  callers in ``combat_service``, ``trading.py`` and ``ranking.py`` keep working.

Legacy JSONB readers: this module no longer writes ``Player.settings['medals']``.
Any code that *reads* that JSONB will simply see no medals; the only known
readers route through ``MedalService.get_player_medals`` (this module) which is
now relational. See the task report's ``jsonb_readers_handled`` for the grep.
"""

import logging
import uuid
from typing import Dict, Any, Optional, List

from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified
from sqlalchemy.exc import IntegrityError

from src.models.player import Player
from src.models.medal import Medal, PlayerMedal
from src.services.medal_catalog import (
    MEDAL_CATALOG,
    LEGACY_KEY_TO_ID,
    get_catalog_entry,
    medals_for_trigger,
    seed_medals,
)

# Re-export so legacy importers of MEDAL_DEFINITIONS keep resolving. The shape
# is the relational catalog now; keys are the stable namespaced ids.
MEDAL_DEFINITIONS = MEDAL_CATALOG

logger = logging.getLogger(__name__)

# WO-DBB-RT2 — realtime progress bands (sw2102-docs/SYSTEMS/medal-service.md:290,
# the `medal_progress` "Fired at 25%, 50%, 75%, 90%, 99% of threshold" row).
# These percentages are CANON (not NO-CANON). A COUNT_THRESHOLD medal fires one
# `medal_progress` frame the FIRST time its counter crosses each band — never a
# re-emit. Crossing dedup is recorded in a dedicated Player.settings key
# (Player award data stays fully relational; this is only ephemeral notification
# bookkeeping, mirroring bounty_service's Player.settings usage — no migration).
MEDAL_PROGRESS_BANDS = (25, 50, 75, 90, 99)

# Dedicated, namespaced settings key for the per-medal highest-notified band.
# Deliberately NOT `settings['medals']` (that JSONB award store was retired by
# ADR-0028 — see the module docstring); this holds only progress-notice dedup.
_MEDAL_PROGRESS_SETTINGS_KEY = "_medal_progress"


# ---------------------------------------------------------------------------
# Core award — module-level, idempotent. Exact signature per task.
# ---------------------------------------------------------------------------
def award_medal(
    db: Session,
    player_id: uuid.UUID,
    medal_id: str,
    *,
    source_event_key: Optional[str] = None,
    source_combat_log_id: Optional[uuid.UUID] = None,
    awarded_via: str = "system",
    context_payload: Optional[Dict[str, Any]] = None,
    awarded_by_user_id: Optional[uuid.UUID] = None,
) -> bool:
    """Award ``medal_id`` to ``player_id``, idempotently.

    Idempotency has two layers (ADR-0028):

    1. Pre-check: ``SELECT`` for an existing (player, medal) row → skip.
    2. ``UNIQUE (player_id, medal_id)``: a concurrent INSERT that races past the
       pre-check raises ``IntegrityError``. We contain that INSERT in a
       SAVEPOINT (``db.begin_nested``) so the violation rolls back ONLY the
       failed award — never the caller's open transaction. This is critical:
       ``award_medal`` is dispatched from inside the combat unit of work (which
       holds an uncommitted CombatLog / ship-destruction / turn-spend), and a
       bare ``db.rollback()`` here would silently discard all of it.

    ``medal_id`` accepts either the stable namespaced id (``combat.bronze_star``)
    or a legacy short key (``bronze_star``), which is resolved to the stable id.

    Returns ``True`` if a new award row was created, ``False`` if already held
    or the medal_id is unknown.
    """
    # Resolve legacy short keys → stable id.
    resolved_id = medal_id if medal_id in MEDAL_CATALOG else LEGACY_KEY_TO_ID.get(medal_id)
    if not resolved_id:
        logger.warning("award_medal: unknown medal_id %r — skipping", medal_id)
        return False

    # Layer 1: pre-check.
    existing = (
        db.query(PlayerMedal)
        .filter(PlayerMedal.player_id == player_id, PlayerMedal.medal_id == resolved_id)
        .first()
    )
    if existing is not None:
        return False

    award = PlayerMedal(
        player_id=player_id,
        medal_id=resolved_id,
        awarded_via=awarded_via,
        source_event_key=source_event_key,
        source_combat_log_id=source_combat_log_id,
        awarded_by_user_id=awarded_by_user_id,
        context_payload=context_payload,
    )
    try:
        # SAVEPOINT-scoped INSERT: the UNIQUE violation (if we lost the race)
        # rolls back to the savepoint ONLY, leaving the caller's transaction —
        # the open combat unit of work — fully intact.
        with db.begin_nested():
            db.add(award)
            db.flush()  # surface the UNIQUE violation here, inside the savepoint
    except IntegrityError:
        # Layer 2: lost the race — another transaction inserted the same pair.
        # begin_nested already rolled back to the savepoint; nothing else lost.
        logger.info(
            "award_medal: %s already held by player %s (race resolved by UNIQUE)",
            resolved_id, player_id,
        )
        return False

    # WO-CG one-time grants (FINAL spec): fire ONLY on the genuine INSERT path
    # (we reach here only when the award row was created — both idempotency
    # layers already short-circuited a re-award), so the grant is idempotent —
    # a re-award is a no-op and can never double-mint. Applied inline in the
    # caller's open transaction (combat/trade/exploration unit of work), inside
    # the same flush. Defensive: a grant hiccup is logged and swallowed — it
    # must never roll back an already-recorded award or break the caller.
    _apply_one_time_grant(db, player_id, resolved_id)

    # WO-B7 — realtime: announce the GENUINE award to the player who earned it.
    # We reach here only on the true INSERT path (both idempotency layers already
    # short-circuit a re-award), so the toast fires exactly once per medal — never
    # on a re-award no-op. The emit is scheduled (loop.create_task) so it lands
    # AFTER the caller's transaction commits and yields, never broadcasting an
    # award a later rollback would void; and it is fully defensive — a broadcast
    # hiccup is swallowed and can never roll back the recorded award or break the
    # caller's unit of work (combat/trade/first-login).
    _dispatch_medal_awarded_event(db, player_id, resolved_id, awarded_via)

    # WO-F9 — offline notification durability (medals.md:201). The realtime toast
    # above only lands if the player is connected; the canon's "Cross-session
    # (offline-earned)" flow requires the award ALSO persist so it survives a
    # logged-out earn: a `system`/`high` Message in the inbox PLUS the medal_id
    # appended to Player.settings.medal_privacy.unviewed_awards (the login-splash
    # queue). Written inline in the caller's open transaction (so it commits/rolls
    # back atomically WITH the award row — never a phantom notice for a voided
    # award), and fully defensive — a notification hiccup is logged and swallowed
    # and can never roll back the recorded award or break the caller.
    _persist_offline_award_notification(db, player_id, resolved_id)

    logger.info("Medal awarded: %s -> player %s (via=%s)", resolved_id, player_id, awarded_via)
    return True


# WO-F9 — settings keys for the cross-session (offline-earned) notification flow
# (medals.md:201). Award data itself is fully relational (ADR-0028); these JSONB
# keys hold only the login-splash queue, mirroring the ephemeral-notice pattern
# already used by `_MEDAL_PROGRESS_SETTINGS_KEY` above (read/modify/write +
# flag_modified, no migration).
_MEDAL_PRIVACY_SETTINGS_KEY = "medal_privacy"
_UNVIEWED_AWARDS_KEY = "unviewed_awards"


def _persist_offline_award_notification(
    db: Session, player_id: uuid.UUID, medal_id: str
) -> None:
    """Persist the durable offline-earned award notice (medals.md:201).

    Two writes, both in the caller's open transaction so they commit/roll back
    atomically with the award row:

    1. A persistent system inbox Message (``message_type="system"``,
       ``priority="high"``) announcing the medal — so an offline earn is still
       waiting in the inbox at next login. The Message model requires a non-null
       ``sender_id`` (FK → players), and a medal has no human sender, so the
       notice is self-addressed (``sender_id == recipient_id == player_id``):
       the FK is guaranteed valid (the player exists) and it reads cleanly as a
       system-to-you commendation.
    2. Append ``medal_id`` to ``Player.settings['medal_privacy']['unviewed_awards']``
       — the login-splash queue the client drains. Read/modify/write defensively
       (settings may be absent/None or hold a non-list value) with
       ``flag_modified`` so the JSONB mutation is flushed. The medal_id is not
       re-appended if already queued (award_medal only reaches here on a genuine
       first INSERT, but the de-dup keeps the list clean defensively).

    Fully defensive end-to-end: any failure (player gone, JSONB hiccup, FK race)
    is logged and swallowed — this is best-effort durability and must never roll
    back the recorded award or break the caller's open transaction.
    """
    try:
        from src.models.message import Message

        player = db.query(Player).filter(Player.id == player_id).first()
        if player is None:
            return

        entry = get_catalog_entry(medal_id) or {}
        medal_name = entry.get("name") or "a commendation"

        # 1) Persistent system inbox message (offline-survivable notice).
        message = Message(
            sender_id=player_id,      # self-addressed system notice (FK requires non-null)
            recipient_id=player_id,
            subject=f"Medal awarded: {medal_name}",
            content=f"You have been awarded the {medal_name} medal. View it in your Trophy Room.",
            message_type="system",
            priority="high",
        )
        db.add(message)

        # 2) Append to the login-splash unviewed-awards queue (defensive R/M/W).
        settings = player.settings or {}
        privacy = settings.get(_MEDAL_PRIVACY_SETTINGS_KEY)
        if not isinstance(privacy, dict):
            privacy = {}
        unviewed = privacy.get(_UNVIEWED_AWARDS_KEY)
        if not isinstance(unviewed, list):
            unviewed = []
        if medal_id not in unviewed:
            unviewed.append(medal_id)
        privacy[_UNVIEWED_AWARDS_KEY] = unviewed
        settings[_MEDAL_PRIVACY_SETTINGS_KEY] = privacy
        player.settings = settings
        flag_modified(player, "settings")

        db.flush()
    except Exception as e:  # never break the award / caller's transaction
        logger.error(
            "offline award notification persist failed for %s/%s: %s",
            medal_id, player_id, e,
        )


def _dispatch_medal_awarded_event(
    db: Session, player_id: uuid.UUID, medal_id: str, awarded_via: str
) -> None:
    """Schedule the async player-scoped ``medal_awarded`` WS push for a new award.

    Mirrors ``movement_service._dispatch_hostile_detected`` /
    ``turn_service._emit_turn_pool_update``: resolve the recipient + payload from
    already-loaded relational state, grab the running loop, schedule the coroutine
    with ``loop.create_task`` so it runs after this sync award's transaction
    commits and yields (never blocking, never pre-commit), and swallow EVERY
    failure (no loop, no socket, unknown medal) so a quiet socket can never break
    the award or the caller's unit of work.

    ``award_medal`` only ever has the ``Player.id``; the WS connection manager
    routes on the ``User.id``, so we resolve ``player.user_id`` here with a single
    scalar query. A player with no resolvable user (shouldn't happen) is silently
    skipped — never raised.
    """
    try:
        user_id = (
            db.query(Player.user_id).filter(Player.id == player_id).scalar()
        )
        if not user_id:
            return

        entry = get_catalog_entry(medal_id) or {}
        criteria = entry.get("criteria") or {}
        medal_payload = {
            "medal_id": medal_id,
            "medal_name": entry.get("name"),
            "medal_category": entry.get("category"),
            "medal_tier": entry.get("tier"),
            "medal_description": entry.get("description"),
            "medal_icon": criteria.get("icon"),
            "awarded_via": awarded_via,
        }

        import asyncio
        from src.services.enhanced_websocket_service import (
            get_enhanced_websocket_service,
        )

        loop = asyncio.get_running_loop()
        loop.create_task(
            get_enhanced_websocket_service().send_medal_awarded(
                str(user_id), medal_payload
            )
        )
    except Exception:
        # No running loop (sync worker/scheduler context), no socket, or any
        # other hiccup: the award is already recorded — the realtime notice is
        # strictly best-effort and must never propagate.
        logger.debug(
            "Skipped medal_awarded WS notice for %s/%s (no loop or socket)",
            medal_id, player_id, exc_info=True,
        )


def _dispatch_medal_revoked_event(
    db: Session,
    player_id: uuid.UUID,
    medal_id: str,
    revoking_user_id: Optional[uuid.UUID],
    reason: Optional[str],
) -> None:
    """Schedule the async personal-only ``medal_revoked`` WS push (WO-DBB-RT2).

    Mirrors :func:`_dispatch_medal_awarded_event`: resolve the affected player's
    ``User.id`` from already-loaded relational state, build the canon payload
    (sw2102-docs/SYSTEMS/medal-service.md: ``{medal_id, reason,
    revoking_admin_username}``), grab the running loop, schedule the coroutine so
    it lands AFTER the revoke's transaction commits and yields, and swallow EVERY
    failure so a quiet socket can never break the revoke. The revoking admin's
    username is resolved from ``User`` when ``revoking_user_id`` is supplied
    (``None`` otherwise — backward-compatible with the current 2-arg route).
    Routed via the base ``connection_manager.send_personal_message`` (the same
    server-originated personal-unicast path ``send_medal_awarded`` uses).
    """
    try:
        user_id = (
            db.query(Player.user_id).filter(Player.id == player_id).scalar()
        )
        if not user_id:
            return

        revoking_admin_username = None
        if revoking_user_id is not None:
            from src.models.user import User
            revoking_admin_username = (
                db.query(User.username).filter(User.id == revoking_user_id).scalar()
            )

        message = {
            "type": "medal_revoked",
            "medal_id": medal_id,
            "reason": reason,
            "revoking_admin_username": revoking_admin_username,
        }

        import asyncio
        from src.services.enhanced_websocket_service import (
            get_enhanced_websocket_service,
        )

        loop = asyncio.get_running_loop()
        loop.create_task(
            get_enhanced_websocket_service().connection_manager.send_personal_message(
                str(user_id), message
            )
        )
    except Exception:
        logger.debug(
            "Skipped medal_revoked WS notice for %s/%s (no loop or socket)",
            medal_id, player_id, exc_info=True,
        )


# ---------------------------------------------------------------------------
# WO-CG — bespoke effect layer (DECISIONS.md:479; blessed
# audit/design-briefs/medal-effects-spec.md FINAL section).
# ---------------------------------------------------------------------------

# Blessed HARD caps on the summed PASSIVE medal contribution per hook (FINAL):
# the flat per-hook cap is the hard ceiling — the redundant diminishing-returns
# rule is dropped. Units match each resolver's native term (see the per-hook
# notes below and in medal_catalog).
MEDAL_BONUS_CAPS: Dict[str, float] = {
    "combat_damage": 3.0,       # ≤ +3% combat damage (percent; folds into attacker_damage_mult)
    "trading_discount": 2.0,    # ≤ −2% buy / +2% sell (percent; folds into rank_rate)
    "turn_regen": 0.05,         # ≤ +0.05 added to the aria/turn-regen multiplier (additive delta)
    "haggle_band": 0.08,        # ≤ +0.08 haggle band ease (band-factor delta)
}


def _apply_one_time_grant(db: Session, player_id: uuid.UUID, medal_id: str) -> None:
    """Apply a medal's one_time credit/turn grant ONCE on award (FINAL table).

    Idempotent by construction: award_medal only calls this on the genuine
    INSERT path, and the UNIQUE(player_id, medal_id) row is the guard — a
    re-award never reaches here. Mints credits/turns ONLY for medals carrying a
    one_time grant; never mints outside this blessed table. Defensive: any
    failure is logged and swallowed so a grant hiccup cannot roll back the
    recorded award or break the combat/trade/exploration unit of work.
    """
    try:
        entry = get_catalog_entry(medal_id)
        effect = (entry or {}).get("effect") or {}
        if effect.get("kind") != "one_time":
            return
        grants = effect.get("grants") or {}
        credits = int(grants.get("credits", 0) or 0)
        turns = int(grants.get("turns", 0) or 0)
        if credits <= 0 and turns <= 0:
            return

        player = db.query(Player).filter(Player.id == player_id).first()
        if player is None:
            return

        if credits > 0:
            player.credits = (player.credits or 0) + credits
        if turns > 0:
            # Top up the turn balance, never above the player's cap (mirrors the
            # ADR-0004 no-overflow rule). max_turns is the persisted ceiling.
            cap = getattr(player, "max_turns", None)
            new_turns = (player.turns or 0) + turns
            if cap is not None:
                new_turns = min(new_turns, cap)
            player.turns = new_turns
        db.flush()
        logger.info(
            "Medal one-time grant: %s -> player %s (+%dcr, +%dt)",
            medal_id, player_id, credits, turns,
        )
    except Exception as e:  # never break the award / caller's transaction
        logger.error("one-time medal grant failed for %s/%s: %s", medal_id, player_id, e)


def get_active_medal_bonuses(db: Session, player_id: uuid.UUID) -> Dict[str, float]:
    """Sum a held player's PASSIVE medal effects PER HOOK, clamped to the blessed
    hard caps (FINAL spec). The single read path resolvers call.

    Returns ``{"combat_damage", "trading_discount", "turn_regen", "haggle_band"}``
    (always all four keys; 0.0 when the player holds no passive medal for a hook).

    Folding rules (FINAL — the diminishing-returns rule is dropped; the flat
    per-hook cap is the hard ceiling):

      1. **Tier-supersession within (category, hook)**: when a player holds
         multiple passive medals hooking the SAME stack in the SAME category
         (e.g. Bronze Star + Silver Star → combat_damage in Combat), only the
         single HIGHEST magnitude applies — progressing a track UPGRADES the
         effect, it does not stack a pile of slivers.
      2. **Cross-category same-hook sums** (rare by design, since hook is
         loop-scoped), then is clamped to the cap.
      3. **Clamp** the per-hook total to MEDAL_BONUS_CAPS — the hard guarantee.

    EXCLUDED from this fold (handled elsewhere, by design):
      * Orange Cat Society (effect kind "special") — applied through
        haggle_service's dedicated lever and EXEMPT from the haggle_band cap;
        folding it here would double-apply it and wrongly subject it to the cap.
      * one_time grants — applied in :func:`_apply_one_time_grant` on award.

    Defensive: any failure returns the neutral all-zero dict so a resolver is
    never broken by a medal lookup.
    """
    neutral = {hook: 0.0 for hook in MEDAL_BONUS_CAPS}
    try:
        rows = (
            db.query(Medal.category, Medal.effect)
            .join(PlayerMedal, PlayerMedal.medal_id == Medal.id)
            .filter(PlayerMedal.player_id == player_id)
            .all()
        )
        if not rows:
            return neutral

        # (category, hook) -> highest passive magnitude in that bucket (rule 1).
        per_bucket: Dict[tuple, float] = {}
        for category, effect in rows:
            if not effect or not isinstance(effect, dict):
                continue
            kind = effect.get("kind")
            # Collect every passive (hook, magnitude) this effect contributes —
            # a plain passive, plus the Genesis-style hybrid's passive_extra.
            contributions = []
            if kind == "passive":
                contributions.append((effect.get("hook"), effect.get("magnitude")))
            # Hybrid (e.g. Genesis Award): a one_time grant carrying a passive_extra.
            extra = effect.get("passive_extra")
            if isinstance(extra, dict):
                contributions.append((extra.get("hook"), extra.get("magnitude")))
            # kind "special" (Orange Cat) is intentionally NOT folded here.

            for hook, magnitude in contributions:
                if hook not in MEDAL_BONUS_CAPS or magnitude is None:
                    continue
                try:
                    mag = float(magnitude)
                except (TypeError, ValueError):
                    continue
                key = (category, hook)
                # Rule 1: keep only the highest magnitude per (category, hook).
                if mag > per_bucket.get(key, 0.0):
                    per_bucket[key] = mag

        # Rule 2: sum the surviving per-bucket magnitudes across categories per hook.
        totals = dict(neutral)
        for (category, hook), mag in per_bucket.items():
            totals[hook] += mag

        # Rule 3: clamp each hook to its blessed hard cap.
        for hook, cap in MEDAL_BONUS_CAPS.items():
            if totals[hook] > cap:
                totals[hook] = cap
        return totals
    except Exception as e:
        logger.error("get_active_medal_bonuses failed for %s: %s", player_id, e)
        return neutral


# ---------------------------------------------------------------------------
# Stat resolution — read current counter values for a player.
# ---------------------------------------------------------------------------
def _combat_victory_count(db: Session, player_id: uuid.UUID) -> int:
    """Count this player's PvP combat victories (mirrors combat_service)."""
    from src.models.combat_log import CombatLog, CombatOutcome

    return (
        db.query(CombatLog)
        .filter(
            CombatLog.defender_id.isnot(None),
            ((CombatLog.attacker_id == player_id) & (CombatLog.outcome == CombatOutcome.ATTACKER_WIN.value))
            | ((CombatLog.defender_id == player_id) & (CombatLog.outcome == CombatOutcome.DEFENDER_WIN.value)),
        )
        .count()
    )


def _evaluate_and_award(
    db: Session,
    player_id: uuid.UUID,
    trigger_type: str,
    current_value: int,
    *,
    source_event_key: Optional[str] = None,
    source_combat_log_id: Optional[uuid.UUID] = None,
    awarded_via: str = "system",
) -> List[str]:
    """Award every catalog medal for ``trigger_type`` whose threshold is met.

    Returns the list of newly-awarded stable medal ids. Idempotency handled by
    :func:`award_medal`.
    """
    newly: List[str] = []
    for entry in medals_for_trigger(trigger_type):
        threshold = entry["criteria"].get("threshold", 0)
        if current_value >= threshold:
            if award_medal(
                db,
                player_id,
                entry["id"],
                source_event_key=source_event_key,
                source_combat_log_id=source_combat_log_id,
                awarded_via=awarded_via,
                context_payload={"trigger": trigger_type, "value_at_award": current_value},
            ):
                newly.append(entry["id"])
        else:
            # WO-DBB-RT2 — below threshold: fire a one-shot `medal_progress` frame
            # the first time this counter crosses a band (25/50/75/90/99%). Only
            # meaningful for COUNT_THRESHOLD medals (threshold > 1); a threshold-1
            # FIRST_TIME medal has no progress arc. Fully defensive — a hiccup here
            # can never break the caller's award unit of work.
            if threshold > 1:
                _maybe_emit_medal_progress(
                    db, player_id, entry, trigger_type, current_value, threshold
                )
    return newly


def _maybe_emit_medal_progress(
    db: Session,
    player_id: uuid.UUID,
    entry: Dict[str, Any],
    counter_key: str,
    current_value: int,
    threshold: int,
) -> None:
    """Fire ONE ``medal_progress`` frame the first time a counter crosses a band.

    Canon (sw2102-docs/SYSTEMS/medal-service.md): the dispatcher additionally
    fires ``medal_progress`` at 25/50/75/90/99% of a COUNT_THRESHOLD medal's
    threshold — personal-only. We persist the highest band already notified per
    (player, medal) in a dedicated ``Player.settings`` key so the same band can
    never re-emit across the many incremental dispatches that recompute the full
    counter each earn event. Award data itself stays fully relational (ADR-0028);
    this writes only ephemeral notice-dedup bookkeeping (mirrors bounty_service).

    Defensive end-to-end: any failure (player gone, JSONB hiccup, no loop/socket)
    is swallowed — a progress notice is strictly best-effort and must never roll
    back or break the caller's open transaction.
    """
    try:
        if threshold <= 0:
            return
        percent = (current_value / threshold) * 100.0
        # Highest CANON band this value has reached (None below the first band).
        reached = None
        for band in MEDAL_PROGRESS_BANDS:
            if percent >= band:
                reached = band
        if reached is None:
            return  # not yet at the 25% mark — nothing to announce

        player = db.query(Player).filter(Player.id == player_id).first()
        if player is None:
            return

        settings = player.settings or {}
        progress_map = settings.get(_MEDAL_PROGRESS_SETTINGS_KEY) or {}
        already = progress_map.get(entry["id"], 0)
        try:
            already = int(already)
        except (TypeError, ValueError):
            already = 0
        if reached <= already:
            return  # this band (or a higher one) was already announced

        # Record the new high-water band so this band never re-emits.
        progress_map[entry["id"]] = reached
        settings[_MEDAL_PROGRESS_SETTINGS_KEY] = progress_map
        player.settings = settings
        flag_modified(player, "settings")
        db.flush()

        _dispatch_medal_progress_event(
            db, player_id, entry, counter_key, current_value, threshold, reached
        )
    except Exception as e:  # never break the caller's award unit of work
        logger.debug(
            "medal_progress check failed for %s/%s: %s",
            entry.get("id"), player_id, e,
        )


def _dispatch_medal_progress_event(
    db: Session,
    player_id: uuid.UUID,
    entry: Dict[str, Any],
    counter_key: str,
    current_value: int,
    threshold: int,
    percent_band: int,
) -> None:
    """Schedule the async personal-only ``medal_progress`` WS push.

    Mirrors :func:`_dispatch_medal_awarded_event`: resolve the recipient
    ``User.id`` from already-loaded relational state, build the canon payload
    (sw2102-docs/SYSTEMS/medal-service.md: ``{medal_id, counter_key, current,
    threshold, percent}``), grab the running loop, schedule the coroutine so it
    lands AFTER the caller's transaction commits and yields, and swallow EVERY
    failure (no loop, no socket) so a quiet socket can never break the caller.
    Routed via the base ``connection_manager.send_personal_message`` (the same
    server-originated personal-unicast path ``send_medal_awarded`` uses).
    """
    try:
        user_id = (
            db.query(Player.user_id).filter(Player.id == player_id).scalar()
        )
        if not user_id:
            return

        message = {
            "type": "medal_progress",
            "medal_id": entry["id"],
            "counter_key": counter_key,
            "current": int(current_value),
            "threshold": int(threshold),
            "percent": int(percent_band),
        }

        import asyncio
        from src.services.enhanced_websocket_service import (
            get_enhanced_websocket_service,
        )

        loop = asyncio.get_running_loop()
        loop.create_task(
            get_enhanced_websocket_service().connection_manager.send_personal_message(
                str(user_id), message
            )
        )
    except Exception:
        logger.debug(
            "Skipped medal_progress WS notice for %s/%s (no loop or socket)",
            entry.get("id"), player_id, exc_info=True,
        )


# ---------------------------------------------------------------------------
# FROZEN HOOK — the combat lane calls this. EXACT signature required.
# ---------------------------------------------------------------------------
def check_and_award_combat_medals(
    db: Session,
    killer_player: Player,
    context: Dict[str, Any],
) -> List[str]:
    """Dispatcher: evaluate combat medals for ``killer_player`` and award earned.

    ``context`` is a dict like ``{victim_id, combat_log_id, kind}``. Defensive —
    NEVER raises into the combat lane; on any error it logs and returns ``[]``.

    Returns the list of newly-awarded stable medal ids.
    """
    try:
        if killer_player is None:
            return []

        context = context or {}
        combat_log_id = context.get("combat_log_id")
        # Normalize combat_log_id to UUID if a string slipped through.
        if isinstance(combat_log_id, str):
            try:
                combat_log_id = uuid.UUID(combat_log_id)
            except (ValueError, TypeError):
                combat_log_id = None

        victory_count = _combat_victory_count(db, killer_player.id)

        awarded = _evaluate_and_award(
            db,
            killer_player.id,
            "combat_victories",
            victory_count,
            source_event_key=context.get("kind") or "combat.victory",
            source_combat_log_id=combat_log_id,
            awarded_via="combat",
        )

        # Rank-upset medal (Quantum Cross): only when context flags it.
        rank_upset = int(context.get("rank_upset_levels", 0) or 0)
        if rank_upset >= 5:
            awarded += _evaluate_and_award(
                db, killer_player.id, "rank_upset", rank_upset,
                source_event_key="combat.rank_upset",
                source_combat_log_id=combat_log_id,
                awarded_via="combat",
            )

        return awarded
    except Exception as e:  # defensive: never break combat
        logger.error("check_and_award_combat_medals failed for %s: %s", getattr(killer_player, "id", "?"), e)
        return []


def check_and_award_trade_medals(
    db: Session,
    player: Player,
    context: Dict[str, Any],
) -> List[str]:
    """Trade-lane dispatcher (analogous to the combat hook). Defensive.

    ``context`` may carry ``total_trades`` and ``lifetime_credits``; falls back
    to reading the player row's ``credits`` when not supplied.
    """
    try:
        if player is None:
            return []
        context = context or {}
        awarded: List[str] = []

        total_trades = context.get("total_trades")
        if total_trades is not None:
            awarded += _evaluate_and_award(
                db, player.id, "total_trades", int(total_trades),
                source_event_key="trade.completed", awarded_via="trade",
            )

        lifetime_credits = context.get("lifetime_credits")
        if lifetime_credits is None:
            lifetime_credits = getattr(player, "credits", None)
        if lifetime_credits is not None:
            awarded += _evaluate_and_award(
                db, player.id, "lifetime_credits", int(lifetime_credits),
                source_event_key="trade.completed", awarded_via="trade",
            )
        return awarded
    except Exception as e:
        logger.error("check_and_award_trade_medals failed for %s: %s", getattr(player, "id", "?"), e)
        return []


def check_and_award_exploration_medals(
    db: Session,
    player: Player,
    context: Dict[str, Any],
) -> List[str]:
    """Exploration-lane dispatcher (analogous to the combat hook). Defensive."""
    try:
        if player is None:
            return []
        context = context or {}
        awarded: List[str] = []
        for trigger in ("sectors_visited", "planets_created", "planets_colonized"):
            value = context.get(trigger)
            if value is not None:
                awarded += _evaluate_and_award(
                    db, player.id, trigger, int(value),
                    source_event_key="exploration", awarded_via="exploration",
                )
        return awarded
    except Exception as e:
        logger.error("check_and_award_exploration_medals failed for %s: %s", getattr(player, "id", "?"), e)
        return []


# ---------------------------------------------------------------------------
# WO-CG2 — additional earn-event dispatchers. Each mirrors the frozen-hook
# pattern above: compute the documented counter from EXISTING data (no new
# columns), then route through _evaluate_and_award (idempotent via award_medal /
# UNIQUE(player_id, medal_id)). Every dispatcher is DEFENSIVE — it NEVER raises
# into its caller's unit of work; on any error it logs and returns []. Each is
# called from a genuinely-completed action under the caller's open transaction.
# ---------------------------------------------------------------------------
def check_and_award_fleet_medals(db: Session, player_id: uuid.UUID) -> List[str]:
    """Award fleet medals (combat.fleet_commander, ``ships_owned``) for a player.

    Counter: the player's live, non-destroyed ship count (``Ship.owner_id``).
    Dispatched after a genuine ship acquisition (create_ship). Defensive.
    """
    try:
        from src.models.ship import Ship

        ships_owned = (
            db.query(Ship)
            .filter(Ship.owner_id == player_id, Ship.is_destroyed.is_(False))
            .count()
        )
        return _evaluate_and_award(
            db, player_id, "ships_owned", ships_owned,
            source_event_key="ship.acquired", awarded_via="system",
        )
    except Exception as e:
        logger.error("check_and_award_fleet_medals failed for %s: %s", player_id, e)
        return []


def check_and_award_port_medals(db: Session, player_id: uuid.UUID) -> List[str]:
    """Award port medals (economic.port_baron, ``ports_owned``) for a player.

    Counter: the player's owned-station count from the ``player_stations``
    association table. Dispatched after an ownership transfer. Defensive.
    """
    try:
        from sqlalchemy import select, func
        from src.models.station import player_stations

        ports_owned = db.execute(
            select(func.count())
            .select_from(player_stations)
            .where(player_stations.c.player_id == player_id)
        ).scalar() or 0
        return _evaluate_and_award(
            db, player_id, "ports_owned", int(ports_owned),
            source_event_key="port.acquired", awarded_via="system",
        )
    except Exception as e:
        logger.error("check_and_award_port_medals failed for %s: %s", player_id, e)
        return []


def check_and_award_bounty_medals(db: Session, collector_id: uuid.UUID) -> List[str]:
    """Award bounty medals (combat.bounty_hunter, ``bounties_collected``).

    Counter: the number of DISTINCT bounty-collection KILL EVENTS this collector
    has resolved — counted as distinct ``target_id`` over PAID ``BountyClaim``
    rows for this collector. A single kill writes one row per bounty source
    (player-placed + system pot), so counting distinct targets folds those to one
    "bounty collected" per head, matching the documented criterion. Dispatched
    after a paying collect_bounty in the combat unit of work. Defensive.
    """
    try:
        from sqlalchemy import func, distinct
        from src.models.bounty_claim import BountyClaim, BountyClaimStatus

        bounties_collected = (
            db.query(func.count(distinct(BountyClaim.target_id)))
            .filter(
                BountyClaim.claimant_id == collector_id,
                BountyClaim.status == BountyClaimStatus.PAID,
            )
            .scalar()
        ) or 0
        return _evaluate_and_award(
            db, collector_id, "bounties_collected", int(bounties_collected),
            source_event_key="bounty.collected", awarded_via="combat",
        )
    except Exception as e:
        logger.error("check_and_award_bounty_medals failed for %s: %s", collector_id, e)
        return []


def check_and_award_faction_medals(db: Session, player_id: uuid.UUID) -> List[str]:
    """Award diplomatic faction medals (peacemaker @3, ambassadors_star @10).

    Counter: the number of factions with which this player has reached HONORED
    (``Reputation.current_level == ReputationLevel.HONORED``). This is the
    SIMPLIFIED catalog interpretation of ``faction_honored`` (a straight HONORED
    count); the docs' "mutually-rivalrous factions simultaneously" nuance for
    Ambassador's Star is NOT enforced here (NO-CANON — routed to orchestrator).
    Dispatched on a reputation level transition that reaches HONORED. Defensive.
    """
    try:
        from src.models.reputation import Reputation, ReputationLevel

        honored = (
            db.query(Reputation)
            .filter(
                Reputation.player_id == player_id,
                Reputation.current_level == ReputationLevel.HONORED,
            )
            .count()
        )
        return _evaluate_and_award(
            db, player_id, "faction_honored", honored,
            source_event_key="faction.honored", awarded_via="system",
        )
    except Exception as e:
        logger.error("check_and_award_faction_medals failed for %s: %s", player_id, e)
        return []


def check_and_award_team_founder_medal(
    db: Session, leader_id: uuid.UUID, member_count: int
) -> List[str]:
    """Award diplomatic.team_founder (``team_members`` >= 5) to a team's FOUNDER.

    The founder is the team's ``leader_id``. Rather than trust the caller's
    passed ``member_count`` (which can drift from the persisted roster), this
    RECOUNTS the live member tally from ``team_members`` for the team(s) this
    player leads, taking the largest (a player may have founded more than one
    team; the founder medal turns on the best roster they have grown). The passed
    ``member_count`` is kept only as a defensive fallback if the recount cannot
    run. Dispatched after a member join. Defensive.
    """
    try:
        from sqlalchemy import func
        from src.models.team import Team
        from src.models.team_member import TeamMember

        # Per-team member tally for teams this player founded (leads), then the
        # largest. A player may have founded more than one team; the founder
        # medal turns on the best roster they have grown.
        per_team_counts = (
            db.query(func.count(TeamMember.id))
            .join(Team, Team.id == TeamMember.team_id)
            .filter(Team.leader_id == leader_id)
            .group_by(TeamMember.team_id)
            .all()
        )
        recounted = max((c for (c,) in per_team_counts), default=None)
        effective_count = (
            int(recounted) if recounted is not None else int(member_count)
        )
        return _evaluate_and_award(
            db, leader_id, "team_members", effective_count,
            source_event_key="team.member_joined", awarded_via="system",
        )
    except Exception as e:
        logger.error("check_and_award_team_founder_medal failed for %s: %s", leader_id, e)
        return []


def check_and_award_governance_medals(db: Session, player_id: uuid.UUID) -> List[str]:
    """Award diplomatic.lawgiver (``ordinances_passed`` >= 1) to a policy author.

    Counter: the number of regional policies this player authored that reached
    IMPLEMENTED (``RegionalPolicy.proposed_by == player AND status==IMPLEMENTED``).
    Dispatched from the governance finalize sweep when a policy is enacted.
    Defensive. (Note: ``diplomatic.first_citizen`` / governance_votes is NOT
    wired here — its only earn-event lives behind an AsyncSession and is parked.)
    """
    try:
        from src.models.region import RegionalPolicy, PolicyStatus

        ordinances_passed = (
            db.query(RegionalPolicy)
            .filter(
                RegionalPolicy.proposed_by == player_id,
                RegionalPolicy.status == PolicyStatus.IMPLEMENTED,
            )
            .count()
        )
        return _evaluate_and_award(
            db, player_id, "ordinances_passed", ordinances_passed,
            source_event_key="governance.ordinance_passed", awarded_via="system",
        )
    except Exception as e:
        logger.error("check_and_award_governance_medals failed for %s: %s", player_id, e)
        return []


def check_and_award_first_login_special_medals(
    db: Session, session_id: uuid.UUID
) -> List[str]:
    """Award the first-login special cat medals from a completed first-login session.

    WO-CG3 — the two concrete-canon special medals, each on its OWN trigger_type
    (so the legacy ``special_discovery`` group can never be swept):

    * ``special.orange_cat_society`` (trigger ``cat_mention_first_login``) — the
      player mentioned a cat during first-login dialogue (medals.md: "Mention the
      cat … during first-login dialogue"). Detected by re-scanning the session's
      persisted ``DialogueExchange.player_response`` text with the SAME detector the
      live dialogue uses (``CatBoostDetector.detect_cat_mention``) — no new column.
    * ``special.honorary_tabby`` (trigger ``honorary_tabby_combo``) — the composite
      criterion (medals.md): cat-mention AND ``negotiation_skill == STRONG`` AND the
      awarded ship's ``rarity_tier >= 3`` — all in this one session. The dispatcher
      gates the conjunction, then fires the medal at threshold 1.

    Recounts authoritative state from the session row + persisted exchanges; never
    trusts a caller-passed flag. Idempotent via ``award_medal`` /
    UNIQUE(player_id, medal_id). Dispatched from ``complete_first_login`` inside the
    caller's open transaction. Defensive — never raises into first-login completion.
    """
    try:
        from src.models.first_login import (
            FirstLoginSession,
            DialogueExchange,
            NegotiationSkillLevel,
            ShipRarityConfig,
        )
        from src.services.enhanced_manual_provider import CatBoostDetector

        session = (
            db.query(FirstLoginSession)
            .filter(FirstLoginSession.id == session_id)
            .first()
        )
        if session is None:
            return []

        player_id = session.player_id

        # Leg 1 — cat mention: re-scan the player's responses for this session with
        # the live dialogue detector. (No cat_mentioned column exists; the criterion
        # is the documented cat-mention event, so we read it back from the record.)
        responses = (
            db.query(DialogueExchange.player_response)
            .filter(DialogueExchange.session_id == session_id)
            .all()
        )
        cat_mentioned = any(
            CatBoostDetector.detect_cat_mention(text)
            for (text,) in responses
            if text
        )
        if not cat_mentioned:
            return []  # neither special medal is earnable without the cat mention

        awarded: List[str] = []

        # orange_cat_society — cat mention alone.
        awarded += _evaluate_and_award(
            db, player_id, "cat_mention_first_login", 1,
            source_event_key="first_login.cat_mention", awarded_via="first_login",
        )

        # honorary_tabby — cat mention AND strong negotiation AND rarity_tier>=3 ship.
        strong = session.negotiation_skill == NegotiationSkillLevel.STRONG
        rarity_ok = False
        if session.awarded_ship is not None:
            tier = (
                db.query(ShipRarityConfig.rarity_tier)
                .filter(ShipRarityConfig.ship_type == session.awarded_ship)
                .scalar()
            )
            rarity_ok = tier is not None and int(tier) >= 3

        if strong and rarity_ok:
            awarded += _evaluate_and_award(
                db, player_id, "honorary_tabby_combo", 1,
                source_event_key="first_login.honorary_tabby",
                awarded_via="first_login",
            )

        return awarded
    except Exception as e:
        logger.error(
            "check_and_award_first_login_special_medals failed for session %s: %s",
            session_id, e,
        )
        return []


# ---------------------------------------------------------------------------
# Legacy-compatible service class — same public surface, relational backing.
# ---------------------------------------------------------------------------
class MedalService:
    def __init__(self, db: Session):
        self.db = db

    # ── Queries ─────────────────────────────────────────────────────
    def get_player_medals(self, player_id: uuid.UUID) -> Dict[str, Any]:
        """Earned (from player_medals) + available (catalog minus earned).

        Preserves the legacy return shape consumed by ranking.py /player/medals.
        """
        try:
            player = self.db.query(Player).filter(Player.id == player_id).first()
            if not player:
                return {"success": False, "error": "Player not found"}

            rows = (
                self.db.query(PlayerMedal, Medal)
                .join(Medal, PlayerMedal.medal_id == Medal.id)
                .filter(PlayerMedal.player_id == player_id)
                .all()
            )
            earned_ids = {pm.medal_id for pm, _ in rows}

            earned = []
            for pm, medal in rows:
                criteria = medal.criteria or {}
                earned.append({
                    "key": medal.id,
                    "name": medal.name,
                    "category": medal.category,
                    "description": medal.description,
                    "icon": criteria.get("icon"),
                    "tier": medal.tier,
                    "awarded_at": pm.awarded_at.isoformat() if pm.awarded_at else None,
                    "awarded_via": pm.awarded_via,
                    "value_at_award": (pm.context_payload or {}).get("value_at_award"),
                })

            available = []
            for medal_id, entry in MEDAL_CATALOG.items():
                if medal_id in earned_ids:
                    continue
                criteria = entry["criteria"]
                available.append({
                    "key": medal_id,
                    "name": entry["name"],
                    "category": entry["category"],
                    "description": entry["description"],
                    "icon": criteria.get("icon"),
                    "tier": entry["tier"],
                    "trigger_type": criteria.get("type"),
                    "threshold": criteria.get("threshold"),
                })

            return {
                "success": True,
                "earned": earned,
                "available": available,
                "total_earned": len(earned),
                "total_available": len(available),
            }
        except Exception as e:
            logger.error(f"Error retrieving medals for player {player_id}: {e}")
            return {"success": False, "error": str(e)}

    # ── Award convenience (legacy-compatible) ───────────────────────
    def check_combat_medals(
        self,
        player_id: uuid.UUID,
        combat_victories: int,
        rank_upset_levels: int = 0,
    ) -> List[str]:
        """Legacy signature preserved (combat_service calls this).

        Backed by the relational award path. Returns newly-awarded medal ids.
        """
        try:
            awarded = _evaluate_and_award(
                self.db, player_id, "combat_victories", combat_victories,
                source_event_key="combat.victory", awarded_via="combat",
            )
            if rank_upset_levels >= 5:
                awarded += _evaluate_and_award(
                    self.db, player_id, "rank_upset", rank_upset_levels,
                    source_event_key="combat.rank_upset", awarded_via="combat",
                )
            return awarded
        except Exception as e:
            logger.error(f"Error checking combat medals for player {player_id}: {e}")
            return []

    def check_trading_medals(
        self,
        player_id: uuid.UUID,
        total_trades: int,
        lifetime_credits: int,
    ) -> List[str]:
        """Legacy signature preserved (trading.py calls this)."""
        try:
            awarded = _evaluate_and_award(
                self.db, player_id, "total_trades", total_trades,
                source_event_key="trade.completed", awarded_via="trade",
            )
            awarded += _evaluate_and_award(
                self.db, player_id, "lifetime_credits", lifetime_credits,
                source_event_key="trade.completed", awarded_via="trade",
            )
            return awarded
        except Exception as e:
            logger.error(f"Error checking trading medals for player {player_id}: {e}")
            return []

    def check_exploration_medals(
        self,
        player_id: uuid.UUID,
        sectors_visited: int,
        planets_created: int,
        planets_colonized: int,
    ) -> List[str]:
        """Legacy signature preserved."""
        try:
            awarded = _evaluate_and_award(
                self.db, player_id, "sectors_visited", sectors_visited,
                source_event_key="exploration", awarded_via="exploration",
            )
            awarded += _evaluate_and_award(
                self.db, player_id, "planets_created", planets_created,
                source_event_key="exploration", awarded_via="exploration",
            )
            awarded += _evaluate_and_award(
                self.db, player_id, "planets_colonized", planets_colonized,
                source_event_key="exploration", awarded_via="exploration",
            )
            return awarded
        except Exception as e:
            logger.error(f"Error checking exploration medals for player {player_id}: {e}")
            return []

    # ── Admin ────────────────────────────────────────────────────────
    def admin_grant(
        self,
        player_id: uuid.UUID,
        medal_id: str,
        granting_user_id: uuid.UUID,
        reason: Optional[str] = None,
    ) -> bool:
        """Admin grant. Returns True if newly awarded, False if already held/unknown."""
        return award_medal(
            self.db, player_id, medal_id,
            awarded_via="admin_grant",
            awarded_by_user_id=granting_user_id,
            source_event_key="admin.grant",
            context_payload={"reason": reason} if reason else None,
        )

    def admin_revoke(
        self,
        player_id: uuid.UUID,
        medal_id: str,
        revoking_user_id: Optional[uuid.UUID] = None,
        reason: Optional[str] = None,
    ) -> bool:
        """Admin revoke. Returns True if a row was removed, else False.

        ``revoking_user_id`` / ``reason`` are OPTIONAL (backward-compatible with
        the existing 2-arg route caller). When a row is genuinely removed, a
        personal ``medal_revoked`` frame is announced to the affected player
        (WO-DBB-RT2). The emit is fully defensive — a WS hiccup never breaks the
        revoke or its transaction.
        """
        resolved_id = medal_id if medal_id in MEDAL_CATALOG else LEGACY_KEY_TO_ID.get(medal_id, medal_id)
        row = (
            self.db.query(PlayerMedal)
            .filter(PlayerMedal.player_id == player_id, PlayerMedal.medal_id == resolved_id)
            .first()
        )
        if row is None:
            return False
        self.db.delete(row)
        self.db.flush()
        logger.info("Medal revoked: %s from player %s", resolved_id, player_id)

        # WO-DBB-RT2 — announce the GENUINE revoke to the affected player only.
        # Reached only after a real row delete, so the frame fires exactly once
        # per revoke (a no-op revoke returns above and never notifies).
        _dispatch_medal_revoked_event(self.db, player_id, resolved_id, revoking_user_id, reason)
        return True


__all__ = [
    "MEDAL_DEFINITIONS",
    "MedalService",
    "award_medal",
    "seed_medals",
    "check_and_award_combat_medals",
    "check_and_award_trade_medals",
    "check_and_award_exploration_medals",
    "check_and_award_fleet_medals",
    "check_and_award_port_medals",
    "check_and_award_bounty_medals",
    "check_and_award_faction_medals",
    "check_and_award_team_founder_medal",
    "check_and_award_governance_medals",
    "get_active_medal_bonuses",
    "MEDAL_BONUS_CAPS",
    "MEDAL_PROGRESS_BANDS",
]
