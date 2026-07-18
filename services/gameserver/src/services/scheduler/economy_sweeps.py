"""Per-player economic faucet + market-pricing sweeps
(WO-QUALITY-techdebt-scheduler-split).

Idle passive-income faucet, daily reputation stipend, system-bounty pot
accrual, port operating-cost accrual + insolvency force-sell, station
destruction-recovery, inactivity reclaim-flag sweep, price-recompute flush,
price-alert sweep, and the price-history hourly/daily/weekly rollup writer.

Region/galaxy-wide administrative + reporting sweeps (governance, treasury
reconciliation, genesis, planetary/construction advance, economy-metrics
snapshot) live in ``economy_governance_sweeps`` instead; route-optimization-
run retention lives in ``presence_helpers`` alongside the other periodic
telemetry-pruning sweeps — both moves were needed to land every module under
the 1500-line cap.

Moved verbatim from the old ``npc_scheduler_service.py``.
"""

import logging
from datetime import datetime, timedelta, UTC
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import text
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from src.services.scheduler._common import (
    _PASSIVE_INCOME_ANCHOR_KEY,
    PRICE_HISTORY_HOURLY_RETENTION_DAYS,
    PRICE_HISTORY_DAILY_RETENTION_DAYS,
    _IDLE_INCOME_LOCK_KEY,
    _DAILY_STIPEND_LOCK_KEY,
    _BOUNTY_ACCRUAL_LOCK_KEY,
    _PORT_OPERATING_COSTS_LOCK_KEY,
    _STATION_RECOVERY_LOCK_KEY,
    _RECLAIM_FLAG_LOCK_KEY,
    _PRICE_HISTORY_LOCK_KEY,
    canonical_day_number,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Idle passive-income faucet — daily credit grant for harvester-equipped ships
# ---------------------------------------------------------------------------

def _run_idle_income_sweep_sync() -> Dict[str, int]:
    """Credit each ship's owner the passive_income its installed equipment grants,
    once per UTC day.

    The quantum_harvester equipment carries {"passive_income": 100}
    (ship_upgrade_service.EQUIPMENT_DEFINITIONS), but nothing ever credited it —
    the purchased effect was inert. ship-systems.md §passive_income: "applied
    per-tick by an idle-income job (periodic credit grant scheduler)". This is
    that job.

    DISCIPLINE — mirrors the genesis/planetary/governance/snapshot sweeps:
      * own SessionLocal (never the request session, never the async engine);
      * xact-level advisory lock so a second gameserver instance skips instead
        of double-crediting (and the lock auto-releases on commit/rollback);
      * a candidate-id query, then a per-row with_for_update re-read so a
        concurrent install/uninstall/equip mutation can't race the credit;
      * per-row commit and per-row try/except — one bad ship cannot abort the
        batch or roll back already-credited ships.

    IDEMPOTENCY ACROSS RESTARTS (this is a CREDIT FAUCET, so this is mandatory):
    the durable per-ship anchor is the UTC date string under
    Ship.equipment_slots[_PASSIVE_INCOME_ANCHOR_KEY]. A ship is credited only
    when that anchor is BEHIND today's UTC date; the anchor is then advanced to
    today in the SAME per-row transaction as the credit. A restart, a duplicate
    wake, or a re-run within the same UTC day re-reads the anchor and skips —
    NEVER a double-credit. Additive JSONB only; NO migration, NO new table.

    ELIGIBILITY — a real player's living ship that carries passive_income:
    owner_id NOT NULL (player-owned), is_npc False, is_destroyed False, and
    ShipUpgradeService.get_passive_income(ship) > 0 (the authoritative amount,
    read from EQUIPMENT_DEFINITIONS and summed across multiple sources).

    LOCK ORDER (Player BEFORE Ship — the canonical Station/Planet -> Player ->
    Ship -> Sector contract): the candidate scan is a column-only query
    (Ship.id, Ship.owner_id — no ORM entity hydration, so it never touches the
    identity map), giving us owner_id up front without locking anything. Each
    row then locks the OWNER's player row first, then the ship row, matching
    ship_upgrade_service._get_ship_and_player / mining_service /
    contraband_service (all Player-before-Ship). Because owner_id came from an
    UNLOCKED scan, a concurrent transfer could have moved the ship to a
    different owner in the gap; the freshly-locked ship row's owner_id is
    re-checked against the locked player before crediting, so a stale-owner
    race is caught and skipped (picked up correctly by the owner's own
    candidacy on the next sweep) rather than misdirecting the credit.

    CADENCE + MAGNITUDE ARE NO-CANON (ship-systems.md §passive_income is
    📐 Design-only) — daily + the EQUIPMENT_DEFINITIONS figure of 100; flagged
    for a DECISIONS.md ruling.

    Returns {"ships": n_credited, "players": n_distinct_players, "credits":
    total_cr_granted}; all zero on the lock-held / nothing-due no-op paths.
    """
    from src.core.database import SessionLocal
    from src.models.player import Player
    from src.models.ship import Ship
    from src.services.ship_upgrade_service import ShipUpgradeService

    result = {"ships": 0, "players": 0, "credits": 0}
    credited_players: set = set()

    db = SessionLocal()
    try:
        got_lock = db.execute(
            text("SELECT pg_try_advisory_xact_lock(:key)"),
            {"key": _IDLE_INCOME_LOCK_KEY},
        ).scalar()
        if not got_lock:
            return result
        # Release the lock-acquiring transaction before per-row work: each
        # per-row commit below would otherwise release the xact lock on the
        # first commit anyway, so we keep the lock only long enough to claim the
        # sweep, then proceed (a second instance that lost the race has already
        # returned above). Commit here so the lock isn't pinned to a long-open
        # transaction while we iterate candidates.
        db.commit()

        # Durable per-ship anchor key: today's UTC date string. A ship whose
        # stored anchor already equals (or is ahead of) today is skipped.
        today = datetime.now(UTC).date()
        today_str = today.isoformat()

        # Candidate ships: player-owned, living, real (non-NPC). We cannot
        # cheaply filter "carries passive_income" in SQL (it lives in the
        # equipment_slots JSONB as an effect of an arbitrary equipment key), so
        # the candidate set is intentionally broad and get_passive_income()
        # below is the authoritative gate. equipment_slots is non-empty for any
        # ship that could qualify, so we prefilter on that to keep the candidate
        # list small on a galaxy of mostly-unequipped hulls.
        candidate_rows = (
            db.query(Ship.id, Ship.owner_id)
            .filter(
                Ship.owner_id.isnot(None),
                Ship.is_npc.is_(False),
                Ship.is_destroyed.is_(False),
                Ship.equipment_slots != text("'{}'::jsonb"),
            )
            .all()
        )

        for ship_id, candidate_owner_id in candidate_rows:
            try:
                # Lock order: Player BEFORE Ship (Station/Planet -> Player ->
                # Ship -> Sector). candidate_owner_id is a point-in-time value
                # from the unlocked scan above; the freshly-locked ship row's
                # owner_id is re-confirmed against it below before crediting.
                player = (
                    db.query(Player)
                    .filter(Player.id == candidate_owner_id)
                    .with_for_update()
                    .first()
                )
                if player is None:
                    db.rollback()  # orphaned owner_id — skip
                    continue

                ship = (
                    db.query(Ship)
                    .filter(Ship.id == ship_id)
                    .with_for_update()
                    .first()
                )
                if (
                    ship is None
                    or ship.is_destroyed
                    or ship.owner_id is None
                    or ship.owner_id != player.id
                ):
                    # ship.owner_id != player.id means a concurrent transfer
                    # moved the ship since the unlocked scan — skip; the new
                    # owner's own candidacy picks it up on a later sweep.
                    db.rollback()  # release row locks; nothing to do
                    continue

                amount = ShipUpgradeService.get_passive_income(ship)
                if amount <= 0:
                    db.rollback()
                    continue

                # Durable idempotency gate: only credit when the per-ship anchor
                # is behind today's UTC date. A restart/re-run within the same
                # day re-reads this and skips — never a double-credit.
                slots = ship.equipment_slots if isinstance(ship.equipment_slots, dict) else {}
                last_str = slots.get(_PASSIVE_INCOME_ANCHOR_KEY)
                if isinstance(last_str, str) and last_str >= today_str:
                    # ISO date strings compare lexicographically == chronologically.
                    db.rollback()
                    continue

                player.credits = int(player.credits or 0) + amount

                # Advance the durable per-ship anchor in the SAME transaction as
                # the credit, so the grant and the idempotency mark commit (or
                # roll back) atomically.
                new_slots = dict(slots)
                new_slots[_PASSIVE_INCOME_ANCHOR_KEY] = today_str
                ship.equipment_slots = new_slots
                flag_modified(ship, "equipment_slots")

                db.commit()
                result["ships"] += 1
                result["credits"] += amount
                credited_players.add(str(player.id))
            except Exception:
                logger.exception(
                    "Idle-income sweep: grant failed for ship %s", ship_id
                )
                db.rollback()

        result["players"] = len(credited_players)
        if result["ships"]:
            logger.info(
                "Idle-income sweep: %s — credited %d ship(s) across %d player(s), "
                "%d cr granted",
                today_str, result["ships"], result["players"], result["credits"],
            )
        return result
    except Exception:
        logger.exception("Idle-income sweep failed")
        db.rollback()
        return result
    finally:
        db.close()


def _run_daily_stipend_sweep_sync() -> Dict[str, int]:
    """Credit each ACTIVE-TODAY player their PER-FACTION guild stipend, once per
    UTC day (Max's final per-faction ruling 2026-06-20).

    The reputation stipend used to ride the weekly economy faucet (paid to every
    active player on the citizen-perk cadence). It is now DAILY and gated on the
    player having actually logged in THAT UTC day — engagement is rewarded, an
    idle day pays 0. The amount is the SUM of each good-standing faction's
    level-scaled contribution (economy_faucet_service.PER_FACTION_DAILY_BY_LEVEL),
    clamped to GLOBAL_DAILY_STIPEND_CAP so a multi-faction-favored player can
    never out-earn the paid weekly citizen perk. The per-faction reputations are
    read by apply_daily_rep_stipend_for_player through the player's OWN (locked)
    session — this sweep opens no extra session for them.

    DISCIPLINE — mirrors _run_idle_income_sweep_sync EXACTLY:
      * own SessionLocal (never the request session, never the async engine);
      * xact-level advisory lock so a second gameserver instance skips instead
        of double-crediting (the lock auto-releases on the first commit), then
        commit immediately to claim the sweep without pinning the lock;
      * a candidate-id query, then a per-row with_for_update re-read so a
        concurrent login/credit mutation can't race the grant;
      * per-row commit and per-row try/except — one bad player cannot abort the
        batch or roll back already-credited players.

    ACTIVE-THAT-DAY GATE: a player is a candidate iff their User.last_login's
    UTC date == today's UTC date. This is a WALL-CLOCK UTC comparison (not the
    canonical clock) so it matches the durable UTC-date anchor and the everyday
    meaning of "logged in today". Idle players are simply not candidates → 0.

    IDEMPOTENCY ACROSS RESTARTS (mandatory — this is a CREDIT FAUCET): the
    durable per-player anchor is the UTC date string under
    Player.settings[_daily_stipend_last_utc_date], advanced in the SAME per-row
    transaction as the credit by apply_daily_rep_stipend_for_player. A player is
    credited only when that anchor is BEHIND today; a restart, duplicate wake, or
    re-run within the same UTC day re-reads it and skips — NEVER a double-credit.
    Additive JSONB only; NO migration, NO new table. A player with NO good-
    standing faction is still anchored for today (0 credits) so subsequent
    same-day wakes short-circuit cheaply.

    CADENCE + per-faction MAGNITUDES + global cap + good-standing threshold are
    NO-CANON (PER_FACTION_DAILY_BY_LEVEL, GLOBAL_DAILY_STIPEND_CAP,
    _GOOD_STANDING_MIN_NUMERIC_LEVEL) — flagged for a DECISIONS.md ruling.

    Returns {"players": n_credited, "credits": total_cr_granted}; all zero on the
    lock-held / nothing-due no-op paths. ``players`` counts rows that received a
    NONZERO grant (a 0-tier player anchored-but-unpaid is not counted)."""
    from src.core.database import SessionLocal
    from src.models.player import Player
    from src.models.user import User
    from src.services.economy_faucet_service import (
        apply_daily_rep_stipend_for_player,
    )

    result = {"players": 0, "credits": 0}

    db = SessionLocal()
    try:
        got_lock = db.execute(
            text("SELECT pg_try_advisory_xact_lock(:key)"),
            {"key": _DAILY_STIPEND_LOCK_KEY},
        ).scalar()
        if not got_lock:
            return result
        # Release the lock-acquiring transaction before per-row work (same
        # rationale as the idle-income sweep): claim the sweep, then iterate.
        db.commit()

        today = datetime.now(UTC).date()
        today_str = today.isoformat()

        # Candidate players: active accounts whose USER logged in today (UTC).
        # The durable login timestamp is User.last_login, written on every login
        # by user_service.update_user_last_login / authenticate_player (auth
        # flow). NOTE: Player has no last_login column (it was renamed to
        # last_game_login), and last_game_login is NOT written by the live auth
        # path (track_login is called without a db arg) — so User.last_login is
        # the only reliable active-that-day signal. Filter on the
        # [today 00:00 UTC, tomorrow 00:00 UTC) half-open window in SQL (join
        # Player->User) so the gate is cheap and idle players never enter the set.
        day_start = datetime(today.year, today.month, today.day, tzinfo=UTC)
        day_end = day_start + timedelta(days=1)
        candidate_ids = (
            db.query(Player.id)
            .join(User, User.id == Player.user_id)
            .filter(
                Player.is_active.is_(True),
                User.last_login.isnot(None),
                User.last_login >= day_start,
                User.last_login < day_end,
            )
            .all()
        )

        for (player_id,) in candidate_ids:
            try:
                player = (
                    db.query(Player)
                    .filter(Player.id == player_id)
                    .with_for_update()
                    .first()
                )
                if player is None or not player.is_active:
                    db.rollback()  # release row lock; nothing to do
                    continue

                # Re-confirm the active-that-day gate on the locked row via the
                # player's User.last_login (a concurrent re-login could have
                # moved it since the candidate query; the gate must hold on the
                # row we credit). player.user lazy-loads the User (read-only).
                last_login = player.user.last_login if player.user else None
                if last_login is None or not (day_start <= last_login < day_end):
                    db.rollback()
                    continue

                granted = apply_daily_rep_stipend_for_player(player, today_str)

                db.commit()  # grant + anchor advance commit atomically
                if granted:
                    result["players"] += 1
                    result["credits"] += granted
            except Exception:
                logger.exception(
                    "Daily-stipend sweep: grant failed for player %s", player_id
                )
                db.rollback()

        if result["players"]:
            logger.info(
                "Daily-stipend sweep: %s — credited %d active player(s), "
                "%d cr granted",
                today_str, result["players"], result["credits"],
            )
        return result
    except Exception:
        logger.exception("Daily-stipend sweep failed")
        db.rollback()
        return result
    finally:
        db.close()


def _run_bounty_accrual_sweep_sync() -> Dict[str, int]:
    """Grow every criminal's STORED system-bounty pot once per canonical day
    (WO-BN). This is the GROWTH half of the stored-pot model — bounty_service's
    collect_bounty / collect_bounty_share are the PAYOUT-then-RESET half.

    A criminal (personal_reputation <= the shallowest tier threshold, -500 — the
    SAME proxy the old on-demand model used to decide WHO carried a bounty) has
    its pot bumped by a per-tier daily accrual (base rate × the deeper-pit
    dastardly multiplier), capped at the deepest-matched tier figure. The actual
    accrual + idempotency live in BountyService.accrue_system_bounty_pot; this
    sweep is the scheduler shell that DRIVES it under the standard discipline.

    DISCIPLINE — mirrors _run_idle_income_sweep_sync / _run_daily_stipend_sweep_
    sync EXACTLY:
      * own SessionLocal (never the request session, never the async engine);
      * xact-level advisory lock so a second gameserver instance skips instead
        of double-accruing (the lock auto-releases on the first commit), then
        commit immediately to claim the sweep without pinning the lock;
      * a candidate-id query, then a per-row with_for_update re-read so a
        concurrent kill (collect_bounty zeroes the pot under the SAME row lock) or
        a reputation change can't race the accrual;
      * per-row commit and per-row try/except — one bad criminal cannot abort the
        batch or roll back already-accrued criminals.

    IDEMPOTENCY ACROSS RESTARTS (mandatory — the pot is a CREDIT FAUCET): the
    durable per-player anchor is the canonical-day index under
    Player.settings[system_bounty_pot_period], advanced in the SAME per-row
    transaction as the accrual by accrue_system_bounty_pot. A criminal accrues at
    most ONE period's worth per call and only when the anchor is BEHIND today's
    canonical day; a restart, duplicate wake, or re-run within the same canonical
    day re-reads it and skips — NEVER a double-accrual. Additive JSONB only; NO
    migration, NO new table.

    CONCURRENCY vs PAYOUT (anti-faucet): the accrual locks the criminal's Player
    row (with_for_update) before reading+writing the pot, exactly as collect_
    bounty locks it before zeroing. So an accrual and a kill can never interleave
    on the same criminal — whichever takes the row lock first runs to its commit
    first; the other sees the post-state. An accrual that lands just after a kill
    re-grows the pot from 0 (correct: the bounty re-accrues over time).

    CANDIDATE GATE: only criminals are candidates — personal_reputation <=
    SYSTEM_BOUNTY_CRIMINAL_THRESHOLD AND is_active. A non-criminal never enters
    the set (so its pot stays 0 and its anchor is never written). The per-row
    re-read re-confirms criminal status on the locked row (a concurrent rep
    recovery could have lifted them out of criminal range since the candidate
    query — accrue_system_bounty_pot adds 0 in that case but still advances the
    anchor so the period isn't re-evaluated).

    Returns {"criminals": n_accrued, "credits": total_cr_added}; all zero on the
    lock-held / nothing-due no-op paths. ``criminals`` counts rows that received a
    NONZERO accrual (a criminal already at cap, anchored-but-unbumped, is not
    counted)."""
    from src.core.database import SessionLocal
    from src.models.player import Player
    from src.services.bounty_service import (
        BountyService,
        SYSTEM_BOUNTY_CRIMINAL_THRESHOLD,
    )

    result = {"criminals": 0, "credits": 0}

    db = SessionLocal()
    try:
        got_lock = db.execute(
            text("SELECT pg_try_advisory_xact_lock(:key)"),
            {"key": _BOUNTY_ACCRUAL_LOCK_KEY},
        ).scalar()
        if not got_lock:
            return result
        # Release the lock-acquiring transaction before per-row work (same
        # rationale as the idle-income / stipend sweeps): claim the sweep, then
        # iterate.
        db.commit()

        # Canonical-day index drives both the per-period accrual and the durable
        # idempotency anchor — self-consistent with the scheduler's canonical
        # clock and observable on dev.
        period = canonical_day_number()

        # Candidate criminals: active accounts deep enough in negative rep to be
        # wanted. The per-row re-read re-confirms on the locked row.
        candidate_ids = (
            db.query(Player.id)
            .filter(
                Player.is_active.is_(True),
                Player.personal_reputation <= SYSTEM_BOUNTY_CRIMINAL_THRESHOLD,
            )
            .all()
        )

        for (player_id,) in candidate_ids:
            try:
                player = (
                    db.query(Player)
                    .filter(Player.id == player_id)
                    .with_for_update()
                    .first()
                )
                if player is None or not player.is_active:
                    db.rollback()  # release row lock; nothing to do
                    continue

                added = BountyService.accrue_system_bounty_pot(player, period)

                db.commit()  # accrual + anchor advance commit atomically
                if added > 0:
                    result["criminals"] += 1
                    result["credits"] += added
            except Exception:
                logger.exception(
                    "Bounty-accrual sweep: accrual failed for player %s",
                    player_id,
                )
                db.rollback()

        if result["criminals"]:
            logger.info(
                "Bounty-accrual sweep: canonical-day %d — grew %d criminal "
                "pot(s) by %d cr total",
                period, result["criminals"], result["credits"],
            )
        return result
    except Exception:
        logger.exception("Bounty-accrual sweep failed")
        db.rollback()
        return result
    finally:
        db.close()




# ---------------------------------------------------------------------------
# Port-cost / station-recovery / reclaim-flag / price-recompute / price-alert
# sweeps
# ---------------------------------------------------------------------------

def _run_port_operating_costs_sync() -> Dict[str, int]:
    """Charge each player-owned port its accrued maintenance/upkeep and force-sell
    any port that has been insolvent for the canon threshold (WO-B3). This is the
    AUTONOMOUS half of the operating-cost engine — port_ownership_service.
    accrue_operating_costs is the LAZY, idempotent engine (charge math +
    insolvency 3-month auto-sell); before this sweep it ONLY fired via the manual
    POST /stations/{id}/accrue-costs endpoint, so an unvisited port never paid
    upkeep and an abandoned port never force-sold. This sweep is the scheduler
    shell that DRIVES that existing engine under the standard discipline. It does
    NOT reimplement any cost math, the maintenance rate, or the insolvency
    threshold — it calls accrue_operating_costs per port and lets it manage its
    own charge, anchor advance, insolvency tally, and inline auto_sell_insolvent.

    DISCIPLINE — mirrors _run_bounty_accrual_sweep_sync / _run_idle_income_sweep_
    sync EXACTLY (this runs on the LIVE scheduler):
      * own SessionLocal (never the request session, never the async engine);
      * xact-level advisory lock so a second gameserver instance skips instead of
        double-charging (the lock auto-releases on the first commit), then commit
        immediately to CLAIM the sweep without pinning the lock across the
        iteration — MANDATORY: if this commit is omitted, the first per-station
        commit below would release the advisory lock mid-iteration and let a
        second instance enter concurrently (double-accrual);
      * a candidate-id query (no batch lock), then a per-station with_for_update
        re-read so a concurrent manual /accrue-costs call or a transfer can't race
        the charge. NOTE: accrue_operating_costs calls _lock_station internally
        (.with_for_update()) — that IS the row lock; we do NOT re-lock here, and
        we acquire NO other row lock before calling it, preserving the service's
        station-then-player-ascending lock order (auto_sell_insolvent locks a
        Player row for the reputation hit);
      * per-station commit and per-station try/except — one bad port cannot abort
        the batch or roll back already-charged ports.

    IDEMPOTENCY ACROSS RESTARTS (mandatory — the charge is a DEBIT and the
    insolvency path force-SELLS): the durable per-station anchor is the wall-clock
    ISO timestamp under Station.ownership['costs_accrued_at'], advanced in the
    SAME per-station transaction as the debit by accrue_operating_costs (whole
    elapsed days only; sub-day remainder stays pending). A re-run, duplicate wake,
    or restart within the same canonical day re-reads the anchor, computes
    elapsed_days <= 0, and returns status 'current' — NEVER a double-charge and
    NEVER a double-increment of the insolvency clock (so NEVER a double
    force-sell). Additive JSONB only; NO migration, NO new column.

    CANDIDATE GATE: owned, player-ownable, not-destroyed stations. owner_id IS NOT
    NULL is the real ownership gate (accrue_operating_costs no-ops on unowned
    stations anyway); is_player_ownable excludes structural non-ownable stations;
    is_destroyed excludes ruined ones. Listability flags (spacedock/tradedock/…)
    gate SALE, not cost accrual — an owned port pays upkeep regardless.

    Returns {"ports": n_charged, "insolvent": n_force_sold}; ``ports`` counts
    stations that took a NONZERO charge (a station already current for the period
    is not counted). All zero on the lock-held / nothing-due no-op paths."""
    from src.core.database import SessionLocal
    from src.models.station import Station
    from src.services import port_ownership_service

    result = {"ports": 0, "insolvent": 0}

    db = SessionLocal()
    try:
        got_lock = db.execute(
            text("SELECT pg_try_advisory_xact_lock(:key)"),
            {"key": _PORT_OPERATING_COSTS_LOCK_KEY},
        ).scalar()
        if not got_lock:
            return result
        # Release the lock-acquiring transaction before per-row work (same
        # rationale as the bounty / idle-income / stipend sweeps): claim the
        # sweep, then iterate. MANDATORY — see the docstring.
        db.commit()

        # Candidate ports: owned + player-ownable + not destroyed. IDs only (no
        # batch lock); the per-station re-read takes the row lock.
        candidate_ids = (
            db.query(Station.id)
            .filter(
                Station.owner_id.isnot(None),
                Station.is_player_ownable.is_(True),
                Station.is_destroyed.is_(False),
            )
            .all()
        )

        for (station_id,) in candidate_ids:
            try:
                station = (
                    db.query(Station)
                    .filter(Station.id == station_id)
                    .with_for_update()
                    .first()
                )
                if station is None or station.owner_id is None:
                    db.rollback()  # release row lock; nothing to do
                    continue

                # accrue_operating_costs re-locks the station internally
                # (_lock_station), charges whole elapsed canonical days, advances
                # the durable anchor, tallies insolvency months, and inline-
                # force-sells at the canon threshold — all in THIS transaction,
                # no commit of its own.
                outcome = port_ownership_service.accrue_operating_costs(db, station)

                db.commit()  # charge + anchor advance (+ any force-sell) atomic

                if (outcome.get("charged") or 0) > 0:
                    result["ports"] += 1
                if outcome.get("insolvency"):
                    result["insolvent"] += 1
            except Exception:
                logger.exception(
                    "Port operating-cost sweep: accrual failed for station %s",
                    station_id,
                )
                db.rollback()

        if result["ports"] or result["insolvent"]:
            logger.info(
                "Port operating-cost sweep: charged %d port(s); %d force-sold "
                "(insolvent)",
                result["ports"], result["insolvent"],
            )
        return result
    except Exception:
        logger.exception("Port operating-cost sweep failed")
        db.rollback()
        return result
    finally:
        db.close()


def _run_station_recovery_sync() -> Dict[str, int]:
    """Auto-rebuild every destroyed station whose 24-CANONICAL-hour recovery
    window has elapsed (WO-DBB-EC6; FEATURES/economy/trading.md § Destruction
    & recovery). This is the AUTONOMOUS half of the station destruction/recovery
    engine — station_service holds the lazy, idempotent rebuild logic
    (recover_station: rebuild commodities to 50% of the destruction-time
    snapshot, clear the destroyed flag/timer, zero re-purchasable defenses).
    This sweep is the scheduler shell that DRIVES that engine. It does NOT
    reimplement the 24h window or the 50% rebuild fraction (both CANON, owned by
    station_service) — it queries due stations and calls recover_station per row.

    DISCIPLINE — mirrors _run_port_operating_costs_sync EXACTLY (runs on the
    LIVE scheduler):
      * own SessionLocal (never the request session, never the async engine);
      * xact-level advisory lock so a second gameserver instance skips instead
        of double-rebuilding, then commit IMMEDIATELY to claim the sweep without
        pinning the lock across the iteration — MANDATORY: omitting this commit
        lets the first per-station commit below release the lock mid-iteration
        and a second instance enter concurrently;
      * a candidate-id query (no batch lock) pre-filtered to destroyed stations
        whose absolute deadline (recovery_time) has passed, then a per-station
        with_for_update re-read so a concurrent path can't race the rebuild;
      * per-station commit and per-station try/except — one bad station cannot
        abort the batch or roll back already-rebuilt stations.

    IDEMPOTENCY ACROSS RESTARTS: the durable per-station state is the existing
    Station.is_destroyed flag + Station.recovery_time deadline + the
    ownership['destroyed_at'] canonical-hours anchor. is_recovery_due re-checks
    the canonical-hours window inside the row lock; recover_station re-checks the
    destroyed flag. A re-run, duplicate wake, or restart finds is_destroyed False
    (already rebuilt) or the window not yet elapsed and no-ops — NEVER a
    double-rebuild. Additive JSONB only; NO migration, NO new column.

    CANDIDATE GATE: is_destroyed = True AND recovery_time <= now (the absolute
    wall-clock deadline encodes the scaled 24-canonical-hour window). The
    per-station is_recovery_due() then confirms via the canonical-hours anchor
    before recover_station runs (defensive against a stale deadline column).

    Returns {"recovered": n_rebuilt}; counts stations actually rebuilt this
    sweep. Zero on the lock-held / nothing-due no-op paths."""
    from src.core.database import SessionLocal
    from src.models.station import Station
    from src.services import station_service

    result = {"recovered": 0}

    db = SessionLocal()
    try:
        got_lock = db.execute(
            text("SELECT pg_try_advisory_xact_lock(:key)"),
            {"key": _STATION_RECOVERY_LOCK_KEY},
        ).scalar()
        if not got_lock:
            return result
        # Release the lock-acquiring transaction before per-row work (same
        # rationale as the port-cost / bounty / idle-income sweeps): claim the
        # sweep, then iterate. MANDATORY — see the docstring.
        db.commit()

        now = datetime.now(UTC)

        # Candidate stations: destroyed + deadline elapsed. IDs only (no batch
        # lock); the per-station re-read takes the row lock. is_recovery_due
        # re-confirms via the canonical-hours anchor inside the lock.
        candidate_ids = (
            db.query(Station.id)
            .filter(
                Station.is_destroyed.is_(True),
                Station.recovery_time.isnot(None),
                Station.recovery_time <= now,
            )
            .all()
        )

        for (station_id,) in candidate_ids:
            try:
                station = (
                    db.query(Station)
                    .filter(Station.id == station_id)
                    .with_for_update()
                    .first()
                )
                if station is None or not station.is_destroyed:
                    db.rollback()  # release row lock; nothing to do
                    continue

                # Re-confirm the canonical-hours window inside the row lock
                # (restart-proof anchor); skip if not actually due yet.
                if not station_service.is_recovery_due(station, now):
                    db.rollback()
                    continue

                outcome = station_service.recover_station(db, station, now)

                db.commit()  # rebuild + flag clear atomic

                if outcome.get("status") == "recovered":
                    result["recovered"] += 1
            except Exception:
                logger.exception(
                    "Station recovery sweep: rebuild failed for station %s",
                    station_id,
                )
                db.rollback()

        if result["recovered"]:
            logger.info(
                "Station recovery sweep: rebuilt %d destroyed station(s) at "
                "50%% inventory",
                result["recovered"],
            )
        return result
    except Exception:
        logger.exception("Station recovery sweep failed")
        db.rollback()
        return result
    finally:
        db.close()


def _run_reclaim_flag_sweep_sync() -> Dict[str, int]:
    """Stamp the inactivity-reclamation flag on planets whose owner has gone
    inactive, and clear it on any planet whose owner is no longer stale (PL4b —
    planet abandonment/reclaim, master §2.1). This is the AUTONOMOUS half of the
    abandonment lifecycle — abandonment_service.flag_inactive_planets is the
    ADVISORY, idempotent, REVERSIBLE flagger (it NEVER deletes a row, NEVER
    reassigns ownership; it only sets/clears planets.reclaimable_at). This sweep
    is the scheduler shell that DRIVES that engine under the standard discipline.
    It does NOT reimplement the 90-day inactivity rule, the grace window, or any
    compensation math — it calls flag_inactive_planets once per cadence.

    DISCIPLINE — mirrors _run_station_recovery_sync / _run_port_operating_costs_
    sync / _run_bounty_accrual_sweep_sync (this runs on the LIVE scheduler):
      * own SessionLocal (never the request session, never the async engine);
      * xact-level advisory lock so a second gameserver instance skips instead of
        double-flagging (the lock auto-releases on the first commit), then commit
        immediately to CLAIM the sweep before the flag work, so the flag commit
        does not release the advisory lock mid-iteration;
      * a single set/clear pass (flag_inactive_planets) committed once — the pass
        touches only the marker column, so a batch commit is safe (no per-row
        money movement happens here; the credit movement is the PLAYER-driven
        reclaim route, gated behind grace + tenure).

    IDEMPOTENCY ACROSS RESTARTS: the durable per-planet anchor is the marker
    itself (planets.reclaimable_at — NULL = not flagged). A re-run, duplicate
    wake, or restart re-evaluates the SAME condition (owner stale? already
    flagged?) and is a no-op for steady-state rows — NEVER a double-flag and (the
    important safety property) NEVER an auto-reclaim: this sweep ONLY moves the
    advisory marker; ownership only changes when a PLAYER fires the reclaim route
    AFTER the 90d + 7d-grace gates AND the displaced owner's 7d tenure floor.
    Additive marker write only; NO new table, NO money faucet.

    Returns {"flagged": n_newly_flagged, "cleared": n_cleared}; both zero on the
    lock-held no-op path."""
    from src.core.database import SessionLocal
    from src.services import abandonment_service

    result = {"flagged": 0, "cleared": 0}

    db = SessionLocal()
    try:
        got_lock = db.execute(
            text("SELECT pg_try_advisory_xact_lock(:key)"),
            {"key": _RECLAIM_FLAG_LOCK_KEY},
        ).scalar()
        if not got_lock:
            return result
        # Release the lock-acquiring transaction before the flag work (same
        # rationale as the recovery / port-cost / bounty sweeps): claim the
        # sweep, then run the pass.
        db.commit()

        outcome = abandonment_service.flag_inactive_planets(db)
        db.commit()  # the set/clear pass commits as one (marker-only writes)
        result["flagged"] = int(outcome.get("flagged", 0))
        result["cleared"] = int(outcome.get("cleared", 0))

        if result["flagged"] or result["cleared"]:
            logger.info(
                "Reclaim-flag sweep: flagged %d inactive-owner planet(s), "
                "cleared %d returned-owner flag(s)",
                result["flagged"], result["cleared"],
            )
        return result
    except Exception:
        logger.exception("Reclaim-flag sweep failed")
        db.rollback()
        return result
    finally:
        db.close()


def _run_price_recompute_flush_sync() -> int:
    """Settle every station flagged pending_price_recomputation (WO-DBB-EC4,
    ADR-0051 SK30) — the deferred half of the per-station 1s price-recompute
    rate limit. The hot market-read path debounces full recomputes to once per
    ~1 wall-clock second per station; a suppressed recompute sets
    Station.pending_price_recomputation. This sweep DRIVES
    TradingService.flush_pending_recomputes (which holds the per-station lock +
    flag re-check + per-station try/except) so a flagged station does not stay
    stale.

    DISCIPLINE — own SessionLocal (never the request session, never the async
    engine), commit after the flush, never reuse the request connection. The
    durable per-station state is the pending_price_recomputation flag (survives
    a restart; a re-run finds the flag cleared and no-ops). No advisory lock is
    taken here: flush_pending_recomputes locks each station row individually, so
    a second instance racing the sweep is serialized per-row by with_for_update
    and the post-lock flag re-check makes a duplicate reprice a no-op.

    Returns the count of stations recomputed (0 on the nothing-pending path)."""
    from src.core.database import SessionLocal
    from src.services.trading_service import TradingService

    db = SessionLocal()
    try:
        flushed = TradingService(db).flush_pending_recomputes()
        db.commit()
        return flushed
    except Exception:
        logger.exception("Price-recompute flush sweep failed")
        db.rollback()
        return 0
    finally:
        db.close()


def _run_price_alert_sweep_sync() -> int:
    """Evaluate every active PriceAlert against current MarketPrice rows and
    emit price_alert_triggered WS frames to any owner whose threshold was
    crossed.

    Before this sweep existed, sweep_price_alerts (price_alert_service) was
    never called from anywhere — alerts only fired from the request path when a
    *player* traded the exact (station, commodity) pair, so NPC-driven price
    moves (Loop A trades via npc_trading_service, flush_pending_recomputes,
    production restocks) silently skipped threshold evaluation. This sweep
    closes that gap: every active alert is checked on the scheduler cadence so
    an NPC move past an alert threshold triggers delivery within one tick.

    PRICE REFERENCE — uses MarketPrice.sell_price (what the station charges
    players to buy the commodity) as the canonical price fed to
    evaluate_price_alerts.  This matches the dominant alert use-case ("alert me
    when commodity X costs more than Y at this station") and is consistent with
    the midpoint published by publish_trade_tick.  Once the PriceAlert model
    gains a price_kind column the injected price_lookup can be split per kind
    without touching this sweep.

    DISCIPLINE — own SessionLocal (never the request session, never the async
    engine), commit after the sweep so last_triggered_at stamps persist,
    close on exit.  No advisory lock needed: sweep_price_alerts does no
    row-locking (it only writes last_triggered_at on matched alerts) and a
    second instance racing the sweep would merely re-evaluate cooldowns, which
    are idempotent.

    Returns the count of alerts fired (0 when nothing crossed a threshold)."""
    from src.core.database import SessionLocal
    from src.models.market_transaction import MarketPrice
    from src.services.price_alert_service import sweep_price_alerts

    db = SessionLocal()
    try:
        def price_lookup(station_id, commodity):
            row = (
                db.query(MarketPrice)
                .filter(
                    MarketPrice.station_id == station_id,
                    MarketPrice.commodity == commodity,
                )
                .first()
            )
            if row is None:
                return None
            return float(row.sell_price)

        fired = sweep_price_alerts(db, price_lookup)
        db.commit()
        return len(fired)
    except Exception:
        logger.exception("Price-alert sweep failed")
        db.rollback()
        return 0
    finally:
        db.close()



# ---------------------------------------------------------------------------
# Price-history time-series sweep (WO-ECON-MKT-TIMESERIES) — hourly
# PriceHistory snapshots + daily/weekly rollups + retention pruning
# ---------------------------------------------------------------------------

def _price_history_rollup(
    db: Session, snapshot_type: str, source_type: str,
    window_start: datetime, window_end: datetime, target_date: datetime,
) -> int:
    """Aggregate every ``source_type`` PriceHistory row in
    [window_start, window_end) into one ``snapshot_type`` row per (station,
    commodity), dated ``target_date``. Shared by the daily (hourly->daily) and
    weekly (daily->weekly) rollups — same shape, different source/target
    granularity. Idempotent: skips any (station, commodity) that already has
    a ``snapshot_type`` row dated ``target_date`` (restart-safe — a missed or
    duplicate wake is a clean no-op, mirroring the daily EconomicMetrics
    anchor)."""
    from src.models.market_transaction import PriceHistory

    already = set(
        db.query(PriceHistory.station_id, PriceHistory.commodity)
        .filter(
            PriceHistory.snapshot_type == snapshot_type,
            PriceHistory.snapshot_date == target_date,
        )
        .all()
    )

    source_rows = (
        db.query(PriceHistory)
        .filter(
            PriceHistory.snapshot_type == source_type,
            PriceHistory.snapshot_date >= window_start,
            PriceHistory.snapshot_date < window_end,
        )
        .all()
    )
    by_key: Dict[Tuple[Any, str], List[Any]] = {}
    for row in source_rows:
        by_key.setdefault((row.station_id, row.commodity), []).append(row)

    written = 0
    for (station_id, commodity), rows in by_key.items():
        if (station_id, commodity) in already:
            continue
        n = len(rows)
        total_volume = sum(r.daily_volume for r in rows)
        total_txns = sum(r.transactions_count for r in rows)
        db.add(PriceHistory(
            station_id=station_id,
            commodity=commodity,
            buy_price=round(sum(r.buy_price for r in rows) / n),
            sell_price=round(sum(r.sell_price for r in rows) / n),
            quantity=round(sum(r.quantity for r in rows) / n),
            daily_volume=total_volume,
            transactions_count=total_txns,
            average_transaction_size=(
                float(total_volume) / total_txns if total_txns else 0.0
            ),
            demand_level=sum(r.demand_level for r in rows) / n,
            supply_level=sum(r.supply_level for r in rows) / n,
            snapshot_date=target_date,
            snapshot_type=snapshot_type,
        ))
        written += 1
    db.flush()
    return written


def sweep_price_history(db: Session, now: Optional[datetime] = None) -> Dict[str, int]:
    """Core PriceHistory sweep logic — hourly snapshot of every current
    MarketPrice row, plus daily/weekly rollups on their calendar boundaries.

    Deliberately takes an injected ``db`` and does no session lifecycle,
    advisory-lock, or commit/rollback of its own (mirrors
    TradingService.flush_pending_recomputes / price_alert_service.
    sweep_price_alerts) — that discipline lives in the
    ``_run_price_history_sweep_sync`` wrapper below. This split is what makes
    the sweep unit-testable directly against the ``db`` fixture rather than
    only provable live.

    HOURLY — one row per (station, commodity) with a MarketPrice row,
    snapshotting price/quantity/demand/supply as of ``now`` (hour-truncated)
    plus interval volume (sum of MarketTransaction.quantity / count over the
    trailing hour, one aggregate GROUP BY query — no per-station loop).
    Idempotent within the hour: a (station, commodity) that already has an
    hourly row dated this hour is skipped, so a second tick inside the same
    hour never duplicates.

    DAILY / WEEKLY — rolled up via the shared ``_price_history_rollup``
    helper only on the relevant calendar boundary (daily: the first tick of
    a new UTC day, rolling up yesterday's hourly rows; weekly: the first
    tick of a new ISO week — Monday — rolling up the last 7 days' daily
    rows), so the aggregation query only ever runs once per period.

    PRUNING — deletes hourly rows older than PRICE_HISTORY_HOURLY_RETENTION_
    DAYS and daily rows older than PRICE_HISTORY_DAILY_RETENTION_DAYS every
    tick (cheap — indexed on snapshot_date); weekly rows are never pruned
    (NO-CANON retention policy, see the constants above).

    Returns {"hourly": int, "daily": int, "weekly": int, "pruned": int} —
    counts of rows written/deleted this call.
    """
    from src.models.market_transaction import MarketPrice, MarketTransaction, PriceHistory
    from sqlalchemy import func as sa_func

    now = now or datetime.utcnow()
    hour_start = now.replace(minute=0, second=0, microsecond=0)
    day_start = hour_start.replace(hour=0)

    # --- Hourly snapshot -----------------------------------------------
    already_hourly = set(
        db.query(PriceHistory.station_id, PriceHistory.commodity)
        .filter(
            PriceHistory.snapshot_type == "hourly",
            PriceHistory.snapshot_date == hour_start,
        )
        .all()
    )

    vol_rows = (
        db.query(
            MarketTransaction.station_id,
            MarketTransaction.commodity,
            sa_func.coalesce(sa_func.sum(MarketTransaction.quantity), 0),
            sa_func.count(MarketTransaction.id),
        )
        .filter(MarketTransaction.timestamp >= hour_start)
        .group_by(MarketTransaction.station_id, MarketTransaction.commodity)
        .all()
    )
    volume_by_key = {(r[0], r[1]): (int(r[2] or 0), int(r[3] or 0)) for r in vol_rows}

    hourly_written = 0
    for mp in db.query(MarketPrice).all():
        key = (mp.station_id, mp.commodity)
        if key in already_hourly:
            continue
        volume, txn_count = volume_by_key.get(key, (0, 0))
        db.add(PriceHistory(
            station_id=mp.station_id,
            commodity=mp.commodity,
            buy_price=mp.buy_price,
            sell_price=mp.sell_price,
            quantity=mp.quantity,
            daily_volume=volume,
            transactions_count=txn_count,
            average_transaction_size=(float(volume) / txn_count if txn_count else 0.0),
            demand_level=mp.demand_level,
            supply_level=mp.supply_level,
            snapshot_date=hour_start,
            snapshot_type="hourly",
        ))
        hourly_written += 1
    db.flush()

    # --- Daily / weekly rollups, only on their calendar boundary --------
    daily_written = 0
    if hour_start == day_start:  # first tick of a new UTC day
        daily_written = _price_history_rollup(
            db, "daily", "hourly",
            window_start=day_start - timedelta(days=1), window_end=day_start,
            target_date=day_start - timedelta(days=1),
        )

    weekly_written = 0
    if hour_start == day_start and day_start.weekday() == 0:  # Monday
        weekly_written = _price_history_rollup(
            db, "weekly", "daily",
            window_start=day_start - timedelta(days=7), window_end=day_start,
            target_date=day_start - timedelta(days=7),
        )

    # --- Retention pruning -----------------------------------------------
    hourly_cutoff = now - timedelta(days=PRICE_HISTORY_HOURLY_RETENTION_DAYS)
    daily_cutoff = now - timedelta(days=PRICE_HISTORY_DAILY_RETENTION_DAYS)
    pruned = (
        db.query(PriceHistory)
        .filter(PriceHistory.snapshot_type == "hourly", PriceHistory.snapshot_date < hourly_cutoff)
        .delete(synchronize_session=False)
    )
    pruned += (
        db.query(PriceHistory)
        .filter(PriceHistory.snapshot_type == "daily", PriceHistory.snapshot_date < daily_cutoff)
        .delete(synchronize_session=False)
    )

    return {
        "hourly": hourly_written, "daily": daily_written,
        "weekly": weekly_written, "pruned": pruned,
    }


def _run_price_history_sweep_sync() -> Dict[str, int]:
    """Own-session wrapper around ``sweep_price_history`` — SessionLocal +
    advisory lock + commit/rollback, same discipline as every other sweep in
    this module. A second gameserver instance racing the same tick skips
    (pg_try_advisory_xact_lock) rather than double-writing; a mid-sweep
    failure rolls back the whole tick (nothing partially written — the next
    hourly wake retries cleanly, same as the EconomicMetrics snapshot)."""
    from src.core.database import SessionLocal

    not_written = {"hourly": 0, "daily": 0, "weekly": 0, "pruned": 0}
    db = SessionLocal()
    try:
        got_lock = db.execute(
            text("SELECT pg_try_advisory_xact_lock(:key)"),
            {"key": _PRICE_HISTORY_LOCK_KEY},
        ).scalar()
        if not got_lock:
            return not_written

        result = sweep_price_history(db)
        db.commit()  # releases the xact lock
        return result
    except Exception:
        logger.exception("Price-history sweep failed")
        db.rollback()
        return not_written
    finally:
        db.close()


