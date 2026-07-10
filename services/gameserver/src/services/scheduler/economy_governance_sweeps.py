"""Governance & regional-administration sweeps (WO-QUALITY-techdebt-scheduler-split).

Genesis formation completion, planetary lazy-advance (terraforming/siege/
production via structures.settle()), regional treasury reconciliation, the
regional governance sweep (elections + policy finalize, including its medal
dispatch), TradeDock construction-advance, and the daily economy-metrics
snapshot — the periodic galaxy/region-wide administrative + reporting sweeps.

Price-history/route-runs-retention and the per-player economic faucets
(idle income, stipend, bounty, port costs, station recovery, reclaim-flag)
live in ``economy_sweeps`` instead — this module is scoped to
region/galaxy-level administration and reporting rather than per-player
economy, which is what keeps both files under the 1500-line cap.

Moved verbatim from the old ``npc_scheduler_service.py``.
"""

import logging
import uuid
from datetime import datetime, timedelta, UTC
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import text
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from src.models.player import Player

from src.services.scheduler._common import (
    _ACTIVE_PLAYERS_RECOMPUTE_STATE_KEY,
    _ACTIVE_PLAYERS_WINDOW_DAYS,
    _TREASURY_RECON_STATE_KEY,
    _GENESIS_COMPLETION_LOCK_KEY,
    _PLANETARY_ADVANCE_LOCK_KEY,
    _GOVERNANCE_SWEEP_LOCK_KEY,
    _CONSTRUCTION_ADVANCE_LOCK_KEY,
    _ECONOMIC_METRICS_LOCK_KEY,
    canonical_day_number,
)

logger = logging.getLogger(__name__)


def _dispatch_governance_medals(db: Session, player_id) -> None:
    """Fire the medals-lane governance hook
    ``medal_service.check_and_award_governance_medals(db, player_id)`` after a
    policy authored by ``player_id`` is enacted (diplomatic.lawgiver /
    ordinances_passed).

    Defensive: resolved by ``getattr`` (the medals lane may be absent),
    idempotent on the medals side, and any failure is logged and swallowed — a
    medal hiccup must NEVER break the governance finalize sweep."""
    try:
        import src.services.medal_service as _medal_module
        hook = getattr(_medal_module, "check_and_award_governance_medals", None)
        if callable(hook):
            hook(db, player_id)
    except Exception as e:  # never let a medal hiccup break the sweep
        logger.error("Governance medal dispatch hook failed: %s", e)



# ---------------------------------------------------------------------------
# Genesis — scheduled formation completion
# ---------------------------------------------------------------------------

def _run_genesis_completion_sync() -> Tuple[int, List[Dict[str, Any]]]:
    """Complete forming genesis planets whose timer has elapsed.

    Before this tick, formation completion settled ONLY lazily — GenesisService.
    complete_due_formations runs on a player's owned-planets fetch and is scoped
    to that one player. A colony whose owner never re-checks the Colonial
    Registry (or an abandoned/unowned forming planet) would therefore stay
    "forming" forever past its 48h timer. This periodic sweep makes the timer
    authoritative for everyone. Cheap (an indexed forming/past-due filter that
    returns nothing on a steady galaxy), idempotent, xact-advisory-lock-gated
    so a second instance skips instead of double-completing.

    WO-G4: returns ``(completed_count, events)`` where ``events`` is a list of
    best-effort ``genesis_progress`` frames — one per OWNED planet that just
    advanced to complete — collected via the GenesisService out-param. Like
    every tick-body event, the caller broadcasts these POST-COMMIT on the EVENT
    LOOP (``_broadcast_events``), NOT from this worker thread (no running loop
    here — a worker-thread→loop bridge is forbidden). The frames are composed
    inside complete_all_due_formations BEFORE its internal commit and handed
    back only after it returns (i.e. after that commit succeeded), so a WS
    hiccup can never roll back a completion."""
    from src.core.database import SessionLocal
    from src.services.genesis_service import GenesisService

    db = SessionLocal()
    events: List[Dict[str, Any]] = []
    try:
        got_lock = db.execute(
            text("SELECT pg_try_advisory_xact_lock(:key)"),
            {"key": _GENESIS_COMPLETION_LOCK_KEY},
        ).scalar()
        if not got_lock:
            return 0, events
        # GenesisService.complete_all_due_formations commits internally when it
        # completes any planet; that commit also releases this xact lock. The
        # genesis_progress frames are appended to ``events`` (out-param) as each
        # owned planet completes, returned post-commit for loop-side broadcast.
        completed = GenesisService(db).complete_all_due_formations(events=events)
        if not completed:
            db.commit()  # release the lock on the no-op path
        return completed, events
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Planetary lazy-advance sweep — terraforming progress + siege turns
# ---------------------------------------------------------------------------

def _run_planetary_advance_sync() -> Dict[str, int]:
    """Drive terraforming, siege AND commodity production forward for planets.

    Before this sweep, TerraformingService._advance_terraforming,
    PlanetaryService.advance_siege and PlanetaryService.realize_production
    (commodity accrual) only ever ran when a player happened to read the
    affected planet (advance-on-read) — a colony whose owner never re-opened
    its screen would freeze mid-terraform, sit at full morale under siege, or
    stop banking the fuel/organics/equipment its colonists produce. This
    periodic sweep makes the canonical clock authoritative for ALL such
    planets, mirroring the genesis-completion sweep above.

    All three underlying advance methods are time-accurate (they apply exactly
    the ticks/elapsed accrued since the durable per-planet anchor —
    terraforming_progress, siege_turns, and last_production + the
    active_events['production_carry'] fractional bank respectively) and
    idempotent (a caught-up planet is a no-op), so running them on a fixed
    cadence neither over- nor under-awards: a planet read by its owner in
    between is simply already current when the sweep arrives, and the sweep
    + an interleaved read accrue exactly elapsed × rate ONCE.

    Cheap on a steady galaxy: the indexed filters (terraforming_active /
    under_siege / owned-and-colonized) return nothing or no-op rows when no
    planet qualifies, so the sweep is a safe no-op there. xact-advisory-lock-
    gated so a second instance skips instead of double-advancing. Per-planet
    failure is isolated and rolled back so one bad planet cannot abort the
    rest of the sweep.

    Returns {terraforming, siege, production} — the count of planets that
    actually moved in each phase.
    """
    from src.core.database import SessionLocal
    from src.models.planet import Planet
    from sqlalchemy import or_, and_
    from src.services.structures import settle

    result = {"terraforming": 0, "siege": 0, "production": 0}
    db = SessionLocal()
    try:
        got_lock = db.execute(
            text("SELECT pg_try_advisory_xact_lock(:key)"),
            {"key": _PLANETARY_ADVANCE_LOCK_KEY},
        ).scalar()
        if not got_lock:
            return result

        # ONE unioned candidate set (CRT WO-K1a §5.3): terraforming_active OR (under_siege AND
        # siege_started_at) OR (owner_id AND colonists>0). Each planet is visited ONCE through
        # structures.settle() — the single planetary tick that advances terraform + (held) siege
        # morale + commodity production and drains the research faucet (now step 5 of settle(),
        # re-homed from the prior chained sweep_research_faucet call), each on its OWN inner anchor
        # in its OWN clock domain. This collapses the prior three filtered phase-loops and
        # eliminates the double-visit when a planet sat in two phase-sets. Per-planet
        # commit/rollback discipline is preserved (one bad planet rolls back only itself); settle()
        # leaves the commit to the caller and self-no-ops every step that doesn't apply, so this
        # stays a cheap no-op on a steady galaxy. NOTE: siege LIFECYCLE (_detect_siege) is
        # intentionally NOT run here — the sweep has no owner/enemy context; settle() only ADVANCES
        # a held siege's morale, exactly as the prior advance_siege phase did (neither started nor
        # lifted sieges).
        candidates = (
            db.query(Planet.id)
            .filter(
                or_(
                    Planet.terraforming_active.is_(True),
                    and_(
                        Planet.under_siege.is_(True),
                        Planet.siege_started_at.isnot(None),
                    ),
                    and_(
                        Planet.owner_id.isnot(None),
                        Planet.colonists > 0,
                    ),
                )
            )
            .all()
        )
        for (planet_id,) in candidates:
            try:
                planet = (
                    db.query(Planet)
                    .filter(Planet.id == planet_id)
                    .with_for_update()
                    .first()
                )
                if planet is None:
                    continue
                res = settle(planet, db=db)
                if res.changed:
                    db.commit()
                    if "terraform" in res.steps_changed:
                        result["terraforming"] += 1
                    if "siege" in res.steps_changed:
                        result["siege"] += 1
                    if "production" in res.steps_changed or "research" in res.steps_changed:
                        result["production"] += 1
                else:
                    db.rollback()  # release the row lock; nothing changed
            except Exception:
                logger.exception(
                    "Planetary advance (settle) failed for planet %s", planet_id,
                )
                db.rollback()

        # Release the advisory lock held on this session's transaction. Each per-planet commit
        # above already released it once; a final commit closes out any open transaction (e.g. the
        # rollback after the last no-op planet) so the lock is not held on the pooled connection.
        db.commit()
        return result
    except Exception:
        logger.exception("Planetary advance sweep failed")
        db.rollback()
        return result
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Treasury reconciliation — ADR-0059 N-I4 / WO-REGOV-TREASURY-RECON
# ---------------------------------------------------------------------------

def reconcile_region_treasuries(db: Session) -> Dict[str, int]:
    """Verify SUM(RegionalTreasuryEntry.delta) == Region.treasury_balance for
    every ACTIVE region — the exact invariant RegionalTreasuryEntry's own
    docstring (region.py) names as this table's purpose.

    Two bounded queries, NOT one per region: a single grouped SUM aggregate
    across every region's ledger rows, then a single filtered fetch of every
    ACTIVE region's id + treasury_balance. A region with zero ledger entries
    never appears in the grouped aggregate's result set at all (nothing to
    group), so its ledger sum is read as the Python default 0 rather than a
    SQL NULL — comparing cleanly against treasury_balance without a crash or
    a COALESCE.

    ALERT-ONLY: a mismatch is logged via ``logger.error`` naming the region
    and both figures. This function NEVER writes to treasury_balance or the
    ledger — it is a verification pass, not a repair pass. (NO-CANON: no
    ops-alert bus exists yet; ``logger.error`` is the interim channel pending
    a DECISIONS ruling on an admin-facing notification surface.)

    Returns {"checked": <active regions examined>, "mismatched": <count>}.
    """
    from src.models.region import Region, RegionStatus, RegionalTreasuryEntry
    from sqlalchemy import func as sa_func

    ledger_sums = dict(
        db.query(
            RegionalTreasuryEntry.region_id,
            sa_func.sum(RegionalTreasuryEntry.delta),
        )
        .group_by(RegionalTreasuryEntry.region_id)
        .all()
    )

    active_regions = (
        db.query(Region.id, Region.treasury_balance)
        .filter(Region.status == RegionStatus.ACTIVE)
        .all()
    )

    mismatched = 0
    for region_id, balance in active_regions:
        ledger_sum = int(ledger_sums.get(region_id, 0) or 0)
        balance = int(balance or 0)
        if ledger_sum != balance:
            mismatched += 1
            logger.error(
                "Treasury reconciliation MISMATCH region_id=%s ledger_sum=%d "
                "treasury_balance=%d drift=%d",
                region_id, ledger_sum, balance, balance - ledger_sum,
            )
    return {"checked": len(active_regions), "mismatched": mismatched}


def _run_treasury_reconciliation_gated(db: Session) -> Dict[str, Any]:
    """Day-gate wrapper around ``reconcile_region_treasuries`` — takes an
    already-open session so it is independently testable (fake session, no
    live DB) without spinning up the whole governance sweep. Mirrors
    ``_run_governance_sweep_sync`` Phase 4's Galaxy.state day-anchor
    discipline EXACTLY, including reading the canonical day via the SAME
    no-arg ``canonical_day_number()`` call (real aware ``datetime.now(UTC)``,
    never the sweep's naive ``now`` — see Phase 4's own comment on why). The
    caller (Phase 6 of the governance sweep) owns the commit/rollback around
    this call, same as every other phase in that sweep.

    Returns {"treasury_checked", "treasury_mismatched", "treasury_recon_skipped"}.
    """
    from src.models.galaxy import Galaxy

    result: Dict[str, Any] = {
        "treasury_checked": 0, "treasury_mismatched": 0, "treasury_recon_skipped": False,
    }

    this_day = canonical_day_number()
    galaxy = db.query(Galaxy).order_by(Galaxy.created_at.asc()).first()
    gstate = dict(galaxy.state or {}) if galaxy is not None else {}
    last_day = gstate.get(_TREASURY_RECON_STATE_KEY)
    already_today = (
        galaxy is not None
        and last_day is not None
        and int(last_day) >= this_day
    )
    if already_today:
        result["treasury_recon_skipped"] = True
        return result

    stats = reconcile_region_treasuries(db)
    result["treasury_checked"] = stats["checked"]
    result["treasury_mismatched"] = stats["mismatched"]

    if galaxy is not None:
        gstate = dict(galaxy.state or {})
        gstate[_TREASURY_RECON_STATE_KEY] = this_day
        galaxy.state = gstate
        flag_modified(galaxy, "state")
    return result


# ---------------------------------------------------------------------------
# Regional governance sweep — open/close elections + finalize policies
# ---------------------------------------------------------------------------

def _run_governance_sweep_sync() -> Dict[str, int]:
    """Drive the regional democratic loop forward on the canonical clock.

    Idempotent phases mirroring the planetary advance sweep's discipline (own
    session, xact advisory lock, per-row with_for_update + per-row commit,
    per-row failure isolation):

      0. AUTO-CREATE due recurring elections: for every active region whose last
         RECURRING_ELECTION_POSITION (governor) election ENDED >= the region's
         election_frequency_days ago (or that has never held one, gauged from
         region.created_at), and that has no in-flight (PENDING/ACTIVE) governor
         election, open the NEXT one in the SCHEDULED phase (status PENDING with
         voting_opens_at = now + lead, voting_closes_at = opens + 7d). This is
         the entry edge of the state machine (canon "Election scheduling") —
         citizens then self-nominate during the SCHEDULED window before Phase 1
         flips it ACTIVE and locks the candidate list.
      1. OPEN due elections: PENDING elections whose voting_opens_at has passed
         become ACTIVE (so voting can begin) — this IS the SCHEDULED -> ACTIVE
         transition that locks the candidate list.
      2. CLOSE + TALLY elections past voting_closes_at: ACTIVE -> COMPLETED with
         the winner persisted to results, RegionalElection.winner_id AND the
         single-seat Region.{position}_id column (governor_id / ambassador_id)
         per SYSTEMS step 3 — exactly as the async tally_election does (a
         voided/inconclusive election leaves the seat untouched). A COMPLETED
         election is never re-tallied (the status filter excludes it).
      3. FINALIZE policies past voting_closes_at: VOTING -> {IMPLEMENTED |
         REJECTED}, applying a passed policy's effect onto the region CLAMPED to
         the CHECK bounds. Quorum/tally count distinct voters from the real
         regional_policy_votes ledger (migration c5a8e2f1b9d3), and a
         treasury-touching enactment writes a RegionalTreasuryEntry in the same
         per-row transaction — mirroring the async finalize_policy. A non-VOTING
         policy is never re-finalized.

    All logic is reimplemented SYNCHRONOUSLY here against the sync session and
    reuses the PURE, session-agnostic helpers in regional_governance_service
    (compute_quorum / quorum_pct_for_region / determine_election_winner /
    enact_changes_onto_region / threshold_for_policy /
    compute_treasury_adjustment) so the sweep applies IDENTICAL canon to the
    async vote-time path. We cannot await the async service methods here without
    poisoning the shared async engine pool — the same constraint that forces the
    faction/ARIA decay to be reimplemented in sync (see
    _apply_faction_decay_sync). Idempotent + a clean no-op when nothing is due.

    Phase 4 additionally recomputes Region.active_players_30d (WO-G18) —
    self-gated to once per canonical day by a durable Galaxy.state anchor — so
    the region dashboard's activity figure is no longer permanently zero.

    Phase 5 EXPIRES stale treaties (WO-TREATY): any 'active' treaty past its
    expires_at is flipped to 'expired' here on the sweep — GALAXY-WIDE, not
    scoped to a region — so a treaty in an UNOPENED region (whose owner never
    issues a GET /my-region/treaties) still expires. Previously the ONLY thing
    that flipped a stale treaty was RegionalGovernanceService._expire_stale_treaties,
    invoked lazily on read; an unread region's treaties therefore never expired.
    The flip uses the SAME 'active' -> 'expired' literals as the lazy path, so a
    treaty caught by either path is byte-identical.

    Phase 6 reconciles regional treasuries (ADR-0059 N-I4 / WO-REGOV-TREASURY-
    RECON): verifies SUM(RegionalTreasuryEntry.delta) == Region.treasury_balance
    for every ACTIVE region via ``_run_treasury_reconciliation_gated`` /
    ``reconcile_region_treasuries``, self-gated to once per canonical day by a
    durable Galaxy.state anchor (mirroring Phase 4's discipline exactly).
    ALERT-ONLY — a mismatch is logged, never auto-corrected; this phase writes
    nothing to any balance.

    Returns {auto_created, opened, tallied, enacted, rejected,
    regions_recomputed, treaties_expired, treasury_checked,
    treasury_mismatched}.
    """
    from src.core.database import SessionLocal
    from src.models.region import (
        Region, RegionStatus, RegionalElection, RegionalPolicy, RegionalVote,
        RegionalPolicyVote, RegionalTreasuryEntry, RegionalTreaty,
        RegionalMembership, ElectionStatus, PolicyStatus,
    )
    from src.models.planet import Planet, player_planets
    from src.models.sector import Sector
    from src.models.galaxy import Galaxy
    from src.models.player_analytics import PlayerActivity
    from src.services.regional_governance_service import (
        compute_quorum, quorum_pct_for_region, threshold_for_policy,
        determine_election_winner, enact_changes_onto_region,
        compute_treasury_adjustment,
        ELECTION_TALLYING, POLICY_VOTERS_KEY,
        RECURRING_ELECTION_POSITION, ELECTION_VOTING_WINDOW_DAYS,
        ELECTION_SCHEDULED_LEAD_DAYS,
    )
    from sqlalchemy import func as sa_func, update
    from sqlalchemy.orm.attributes import flag_modified

    result = {"auto_created": 0, "opened": 0, "tallied": 0, "enacted": 0,
              "rejected": 0, "regions_recomputed": 0, "treaties_expired": 0,
              "treasury_checked": 0, "treasury_mismatched": 0}
    now = datetime.utcnow()

    db = SessionLocal()
    try:
        got_lock = db.execute(
            text("SELECT pg_try_advisory_xact_lock(:key)"),
            {"key": _GOVERNANCE_SWEEP_LOCK_KEY},
        ).scalar()
        if not got_lock:
            return result

        # --- Phase 0: auto-create due recurring elections --------------------
        # Canon "Election scheduling": for every active region whose previous
        # governor election ended >= election_frequency_days ago (gauged from
        # region.created_at if it has never held one) and that has no in-flight
        # (PENDING/ACTIVE) governor election, open the NEXT governor election in
        # the SCHEDULED phase (PENDING). voting_opens_at is set a lead-window in
        # the future so citizens can self-nominate before Phase 1 flips it ACTIVE
        # and locks the candidate list. Reproduce-exactly: a manually-created
        # election (born ACTIVE for the same position) registers as in-flight, so
        # the auto-scheduler never duplicates it.
        recurring_regions = (
            db.query(Region.id, Region.election_frequency_days, Region.created_at)
            .filter(Region.status == RegionStatus.ACTIVE)
            .all()
        )
        for (rid, freq_days, created_at) in recurring_regions:
            try:
                # An in-flight (PENDING or ACTIVE) governor election blocks a new
                # one — at most one live election per position (canon step 3).
                in_flight = (
                    db.query(RegionalElection.id)
                    .filter(
                        RegionalElection.region_id == rid,
                        RegionalElection.position == RECURRING_ELECTION_POSITION,
                        RegionalElection.status.in_(
                            [ElectionStatus.PENDING, ElectionStatus.ACTIVE]
                        ),
                    )
                    .first()
                )
                if in_flight is not None:
                    continue

                # The most recent terminal governor election's close time anchors
                # the cadence; with none on record, fall back to region birth so a
                # never-elected region opens its first election once it is old
                # enough.
                last_close = (
                    db.query(sa_func.max(RegionalElection.voting_closes_at))
                    .filter(
                        RegionalElection.region_id == rid,
                        RegionalElection.position == RECURRING_ELECTION_POSITION,
                    )
                    .scalar()
                )
                anchor = last_close or created_at
                if anchor is None:
                    continue
                freq = int(freq_days or 90)
                if (now - anchor) < timedelta(days=freq):
                    continue

                voting_opens_at = now + timedelta(days=ELECTION_SCHEDULED_LEAD_DAYS)
                voting_closes_at = voting_opens_at + timedelta(
                    days=ELECTION_VOTING_WINDOW_DAYS
                )
                new_election = RegionalElection(
                    region_id=rid,
                    position=RECURRING_ELECTION_POSITION,
                    candidates=[],
                    voting_opens_at=voting_opens_at,
                    voting_closes_at=voting_closes_at,
                    status=ElectionStatus.PENDING,
                )
                db.add(new_election)
                db.commit()
                result["auto_created"] += 1
            except Exception:
                logger.exception(
                    "Governance sweep: auto-create failed for region %s", rid
                )
                db.rollback()

        # --- Phase 1: open due PENDING elections -----------------------------
        due_open = (
            db.query(RegionalElection.id)
            .filter(
                RegionalElection.status == ElectionStatus.PENDING,
                RegionalElection.voting_opens_at <= now,
                RegionalElection.voting_closes_at > now,
            )
            .all()
        )
        for (eid,) in due_open:
            try:
                election = (
                    db.query(RegionalElection)
                    .filter(RegionalElection.id == eid)
                    .with_for_update()
                    .first()
                )
                if election is None or election.status != ElectionStatus.PENDING:
                    db.rollback()
                    continue
                election.status = ElectionStatus.ACTIVE
                db.commit()
                result["opened"] += 1
            except Exception:
                logger.exception("Governance sweep: open failed for election %s", eid)
                db.rollback()

        # --- Phase 2: close + tally elections past their window --------------
        due_close = (
            db.query(RegionalElection.id)
            .filter(
                RegionalElection.status == ElectionStatus.ACTIVE,
                RegionalElection.voting_closes_at <= now,
            )
            .all()
        )
        for (eid,) in due_close:
            try:
                election = (
                    db.query(RegionalElection)
                    .filter(RegionalElection.id == eid)
                    .with_for_update()
                    .first()
                )
                # Idempotency: skip anything that left ACTIVE since we listed it.
                if election is None or election.status != ElectionStatus.ACTIVE:
                    db.rollback()
                    continue
                region = (
                    db.query(Region)
                    .filter(Region.id == election.region_id)
                    .first()
                )
                if region is None:
                    db.rollback()
                    continue

                election.status = ELECTION_TALLYING
                rows = (
                    db.query(
                        RegionalVote.candidate_id,
                        sa_func.coalesce(sa_func.sum(RegionalVote.weight), 0),
                    )
                    .filter(RegionalVote.election_id == election.id)
                    .group_by(RegionalVote.candidate_id)
                    .all()
                )
                tallies = {str(cid): float(total) for cid, total in rows}
                winner, payload = determine_election_winner(region, election, tallies)
                if not tallies:
                    payload["inconclusive"] = True
                election.results = payload
                flag_modified(election, "results")

                # Persist the winner (SYSTEMS step 3), mirroring
                # tally_election: winner_id is the winning candidate's player_id,
                # or None when voided/inconclusive (no candidate cleared the
                # supermajority gate / no votes cast). A voided/inconclusive
                # election leaves the incumbent Region.{position}_id untouched
                # (a failed election does not vacate the seat).
                winner_uuid: Optional[uuid.UUID] = None
                if winner is not None:
                    try:
                        winner_uuid = uuid.UUID(str(winner))
                    except (TypeError, ValueError):
                        winner_uuid = None
                election.winner_id = winner_uuid
                if winner_uuid is not None:
                    # Region.{position}_id for single-seat positions
                    # (governor_id / ambassador_id). council_member is multi-seat
                    # and has no single-occupant column — it persists to the
                    # election row only.
                    position_column = f"{election.position}_id"
                    if hasattr(region, position_column):
                        setattr(region, position_column, winner_uuid)
                        region.updated_at = now

                election.status = ElectionStatus.COMPLETED
                db.commit()
                result["tallied"] += 1
            except Exception:
                logger.exception("Governance sweep: tally failed for election %s", eid)
                db.rollback()

        # --- Phase 3: finalize policies past their window --------------------
        due_policies = (
            db.query(RegionalPolicy.id)
            .filter(
                RegionalPolicy.status == PolicyStatus.VOTING,
                RegionalPolicy.voting_closes_at <= now,
            )
            .all()
        )
        for (pid,) in due_policies:
            try:
                policy = (
                    db.query(RegionalPolicy)
                    .filter(RegionalPolicy.id == pid)
                    .with_for_update()
                    .first()
                )
                # Idempotency: only a still-VOTING policy is finalized.
                if policy is None or policy.status != PolicyStatus.VOTING:
                    db.rollback()
                    continue
                region = (
                    db.query(Region)
                    .filter(Region.id == policy.region_id)
                    .with_for_update()
                    .first()
                )
                if region is None:
                    db.rollback()
                    continue

                # Eligible-voter roll (quorum denominator), colony-aware per WO-CF
                # PATH A — mirrors the async _count_eligible_voters: a player is
                # eligible if they have a can_vote membership row OR own ≥1 colony
                # in the region (resolved through the planet's SECTOR, since
                # Planet.region_id is unreliable). Counted as DISTINCT players so a
                # colony owner with an eligible membership row is not double-counted.
                eligible_member_ids = {
                    pid for (pid,) in db.query(RegionalMembership.player_id)
                    .filter(
                        RegionalMembership.region_id == region.id,
                        RegionalMembership.membership_type.in_(["citizen", "resident"]),
                        RegionalMembership.voting_power > 0,
                    )
                    .all()
                }
                colony_owner_ids = {
                    pid for (pid,) in db.query(player_planets.c.player_id)
                    .select_from(Planet)
                    .join(Sector, Planet.sector_uuid == Sector.id)
                    .join(player_planets, Planet.id == player_planets.c.planet_id)
                    .filter(Sector.region_id == region.id)
                    .distinct()
                    .all()
                }
                eligible = len(eligible_member_ids | colony_owner_ids)
                quorum = compute_quorum(int(eligible), quorum_pct_for_region(region))

                # Quorum denominator: number of distinct voters who actually
                # voted, counted from the real regional_policy_votes ledger
                # (migration c5a8e2f1b9d3), mirroring finalize_policy. Falls back
                # to the legacy proposed_changes['_voters'] list (then raw tally
                # presence) ONLY for legacy/manual rows predating the table —
                # strictly a backward-compat read; nothing writes _voters now.
                votes_cast = int(
                    db.query(sa_func.count(RegionalPolicyVote.id))
                    .filter(RegionalPolicyVote.policy_id == policy.id)
                    .scalar()
                    or 0
                )
                changes = dict(policy.proposed_changes or {})
                if votes_cast == 0:
                    legacy_voters = changes.get(POLICY_VOTERS_KEY)
                    votes_cast = (
                        len(legacy_voters) if isinstance(legacy_voters, list)
                        else (1 if (policy.votes_for or 0) + (policy.votes_against or 0) > 0 else 0)
                    )

                threshold = threshold_for_policy(region, policy.policy_type)
                total_weight = int(policy.votes_for or 0) + int(policy.votes_against or 0)
                approval = (
                    float(policy.votes_for or 0) / total_weight
                    if total_weight > 0 else 0.0
                )

                if votes_cast < quorum:
                    policy.status = PolicyStatus.REJECTED
                    db.commit()
                    result["rejected"] += 1
                elif approval >= float(threshold):
                    policy.status = PolicyStatus.PASSED
                    enact_changes_onto_region(region, policy.proposed_changes)
                    region.updated_at = now

                    # Treasury-touching enactment (ADR-0059 N-I4), mirroring
                    # finalize_policy: if the policy carries a treasury
                    # adjustment, mutate Region.treasury_balance and write a
                    # RegionalTreasuryEntry row in THIS SAME per-row transaction
                    # so the running balance stays reconcilable
                    # (SUM(delta) == treasury_balance). No current canon policy
                    # type carries it, so existing policies are unaffected.
                    treasury_delta = compute_treasury_adjustment(
                        region, policy.proposed_changes
                    )
                    if treasury_delta is not None:
                        before = int(region.treasury_balance or 0)
                        after = before + treasury_delta
                        region.treasury_balance = after
                        db.add(RegionalTreasuryEntry(
                            region_id=region.id,
                            before_balance=before,
                            after_balance=after,
                            delta=treasury_delta,
                            cause_type=RegionalTreasuryEntry.CAUSE_POLICY_ENACTMENT,
                            cause_id=policy.id,
                            reason=f"Policy enacted: {policy.title}",
                        ))

                    cleaned = dict(policy.proposed_changes or {})
                    cleaned.pop(POLICY_VOTERS_KEY, None)
                    policy.proposed_changes = cleaned
                    flag_modified(policy, "proposed_changes")
                    policy.status = PolicyStatus.IMPLEMENTED
                    # Medal: diplomatic.lawgiver (ordinances_passed >= 1) — awarded
                    # to the policy AUTHOR (proposed_by) on genuine enactment, in
                    # this same per-policy transaction (before the commit below);
                    # idempotent on the medals side. Defensive — never breaks the
                    # governance sweep.
                    _dispatch_governance_medals(db, policy.proposed_by)
                    db.commit()
                    result["enacted"] += 1
                else:
                    policy.status = PolicyStatus.REJECTED
                    db.commit()
                    result["rejected"] += 1
            except Exception:
                logger.exception("Governance sweep: finalize failed for policy %s", pid)
                db.rollback()

        # --- Phase 4: recompute Region.active_players_30d (WO-G18) ------------
        # Region.active_players_30d was always 0 (nothing ever wrote it), so the
        # region dashboard's activity figure was dead. Recompute it here, gated
        # to once per canonical DAY by a durable Galaxy.state anchor (the
        # COUNT(DISTINCT) aggregate over a 30-day window is too heavy to run on
        # every 5-minute governance sweep). A player counts as "active in a
        # region" if they logged any PlayerActivity in one of that region's
        # SECTORS within the trailing 30 days — the activity's recorded
        # sector_id (the GLOBAL human-readable Sector.sector_id integer, NOT the
        # Sector.id UUID) resolves to the region it happened in, the same
        # sector→region path the quorum roll above uses because object-level
        # region_id is unreliable. Per-region write + per-region commit with
        # per-region failure isolation, mirroring the sweep's discipline above;
        # always defensive, never fatal to the governance sweep.
        try:
            # No-arg → canonical_day_number defaults to an aware datetime.now(UTC);
            # passing the sweep's naive datetime.utcnow() would make .timestamp()
            # interpret it as LOCAL time and shift the day anchor. Mirrors
            # _run_weekly_decay_sync's this_week = canonical_week_number().
            this_day = canonical_day_number()
            galaxy = db.query(Galaxy).order_by(Galaxy.created_at.asc()).first()
            gstate = dict(galaxy.state or {}) if galaxy is not None else {}
            last_day = gstate.get(_ACTIVE_PLAYERS_RECOMPUTE_STATE_KEY)
            already_today = (
                galaxy is not None
                and last_day is not None
                and int(last_day) >= this_day
            )
            if not already_today:
                window_start = now - timedelta(days=_ACTIVE_PLAYERS_WINDOW_DAYS)
                # DISTINCT-player count per region in one grouped aggregate:
                #   COUNT(DISTINCT player_id) of activities in the last 30 days,
                #   joined activity.sector_id (global int) -> Sector.sector_id
                #   -> Sector.region_id.
                counts = dict(
                    db.query(
                        Sector.region_id,
                        sa_func.count(sa_func.distinct(PlayerActivity.player_id)),
                    )
                    .select_from(PlayerActivity)
                    .join(Sector, PlayerActivity.sector_id == Sector.sector_id)
                    .filter(
                        PlayerActivity.timestamp >= window_start,
                        Sector.region_id.isnot(None),
                    )
                    .group_by(Sector.region_id)
                    .all()
                )
                # Iterate ALL regions (not just those with activity) so a region
                # that went quiet is reset to 0 rather than left stale. Per-row
                # commit + per-row isolation: one region's error never aborts the
                # rest.
                region_ids = [rid for (rid,) in db.query(Region.id).all()]
                for rid in region_ids:
                    try:
                        new_count = int(counts.get(rid, 0))
                        updated = (
                            db.query(Region)
                            .filter(Region.id == rid)
                            .update(
                                {Region.active_players_30d: new_count},
                                synchronize_session=False,
                            )
                        )
                        db.commit()
                        if updated:
                            result["regions_recomputed"] += 1
                    except Exception:
                        logger.exception(
                            "Governance sweep: active_players_30d recompute "
                            "failed for region %s", rid,
                        )
                        db.rollback()
                # Advance the durable per-day anchor (best-effort; a failure here
                # just means a harmless re-run next sweep — the recompute is
                # idempotent).
                if galaxy is not None:
                    try:
                        gstate = dict(galaxy.state or {})
                        gstate[_ACTIVE_PLAYERS_RECOMPUTE_STATE_KEY] = this_day
                        galaxy.state = gstate
                        flag_modified(galaxy, "state")
                        db.commit()
                    except Exception:
                        logger.exception(
                            "Governance sweep: active_players_30d day-anchor "
                            "advance failed (recompute will re-run next sweep)"
                        )
                        db.rollback()
        except Exception:
            # The recompute must NEVER break the governance sweep proper.
            logger.exception(
                "Governance sweep: active_players_30d recompute phase failed"
            )
            db.rollback()

        # --- Phase 5: expire stale treaties (WO-TREATY) ----------------------
        # GALAXY-WIDE expiry of every 'active' treaty past its expires_at, so a
        # treaty in an UNOPENED region (whose owner never reads it) still
        # expires. Mirrors RegionalGovernanceService._expire_stale_treaties's
        # 'active' -> 'expired' transition but is NOT region-scoped — the lazy
        # read path only ever touched the region being read. Idempotent (a clean
        # no-op once nothing is past its expiry); a failure here must NEVER break
        # the governance sweep proper.
        try:
            expired_result = db.execute(
                update(RegionalTreaty)
                .where(
                    RegionalTreaty.status == "active",
                    RegionalTreaty.expires_at.isnot(None),
                    RegionalTreaty.expires_at < now,
                )
                .values(status="expired")
            )
            expired_count = expired_result.rowcount or 0
            if expired_count:
                db.commit()
                result["treaties_expired"] += expired_count
                logger.info(
                    "Governance sweep: expired %d stale treaty(ies)",
                    expired_count,
                )
            else:
                # Nothing flipped — settle the no-op statement so the advisory
                # lock is not held on an idle transaction.
                db.commit()
        except Exception:
            logger.exception("Governance sweep: treaty expiry phase failed")
            db.rollback()

        # --- Phase 6: treasury reconciliation (WO-REGOV-TREASURY-RECON) ------
        # RegionalTreasuryEntry's own docstring (region.py) names this
        # verification as the ledger's purpose: SUM(delta) must equal
        # Region.treasury_balance for every ACTIVE region. Self-gated to once
        # per canonical day (see _run_treasury_reconciliation_gated), mirroring
        # Phase 4's day-anchor discipline exactly. ALERT-ONLY — a mismatch is
        # logged via logger.error naming the region and both figures; this
        # phase NEVER writes to any balance. A failure here must NEVER break
        # the governance sweep proper.
        try:
            recon = _run_treasury_reconciliation_gated(db)
            result["treasury_checked"] = recon["treasury_checked"]
            result["treasury_mismatched"] = recon["treasury_mismatched"]
            db.commit()
        except Exception:
            logger.exception("Governance sweep: treasury reconciliation phase failed")
            db.rollback()

        # Final commit closes out any open (no-op) transaction so the advisory
        # lock is not held on the pooled connection.
        db.commit()
        return result
    except Exception:
        logger.exception("Governance sweep failed")
        db.rollback()
        return result
    finally:
        db.close()


# ---------------------------------------------------------------------------
# TradeDock SHIPYARD construction-advance sweep — drive the berth pipeline
# ---------------------------------------------------------------------------

def _run_construction_advance_sync() -> Dict[str, int]:
    """Drive the TradeDock shipyard construction pipeline forward on the
    canonical clock for every station with a live build.

    Before this sweep, construction_service._advance_station — hold-expiry
    forfeiture (with the 50% deposit split to the next-in-queue reservation),
    queue→slip promotions, build phase progression, slip-rent forfeiture, and
    claim-window expiry — ran ONLY when a player synchronously touched the
    station through the construction API (quote/status/deliver/pay/claim/cancel,
    all of which lazily settle the station first). A build whose owner stopped
    logging in simply froze: an expired hold never released its slip, the next
    reservation in the queue never got promoted, a finished hull never entered
    (or aged out of) its 7-day claim window. This sweep makes the canonical
    clock authoritative for ALL stations with a non-terminal reservation,
    mirroring the planetary / governance sweeps' discipline.

    _advance_station is the AUTHORITY on what is due — it gates every transition
    on the durable per-reservation timestamps/states (hold_expires_at,
    phase_deadline, rent_paid_until, claim_expires_at). The sweep merely DRIVES
    it, so it is time-accurate (settles exactly the windows that elapsed since
    each durable anchor) and idempotent: a caught-up station — or one already
    settled by an interleaved API read between sweeps — is a clean no-op, so a
    re-run (including after a restart) never double-applies a forfeiture or
    double-promotes a slip.

    Candidate set: DISTINCT station ids that have a NON-TERMINAL
    ConstructionReservation (state NOT IN claimed/cancelled/forfeited). A station
    with nothing but terminal rows is skipped without a lock — we never scan
    every station. xact-advisory-lock-gated so a second instance skips instead
    of double-advancing. Per station: with_for_update lock on the Station row
    (the per-station serialization point _advance_station expects its caller to
    hold), then _advance_station + a per-station commit; per-station try/except
    so one bad station cannot abort the batch.

    Returns {stations} — the count of stations whose pipeline actually advanced
    (a transition was logged); a no-op station does not increment it.
    """
    from src.core.database import SessionLocal
    from src.models.construction import ConstructionReservation
    from src.models.station import Station
    from src.services import construction_service

    result = {"stations": 0}
    now = datetime.now(UTC)
    db = SessionLocal()
    try:
        got_lock = db.execute(
            text("SELECT pg_try_advisory_xact_lock(:key)"),
            {"key": _CONSTRUCTION_ADVANCE_LOCK_KEY},
        ).scalar()
        if not got_lock:
            return result

        # Distinct stations carrying at least one live (non-terminal) build.
        # Querying the indexed reservation set — not every station — keeps the
        # sweep cheap on a steady galaxy with few in-flight builds.
        station_rows = (
            db.query(ConstructionReservation.station_id)
            .filter(
                ConstructionReservation.state.notin_(
                    list(construction_service.TERMINAL_STATES)
                )
            )
            .distinct()
            .all()
        )

        for (station_id,) in station_rows:
            try:
                station = (
                    db.query(Station)
                    .filter(Station.id == station_id)
                    .with_for_update()
                    .first()
                )
                if station is None:
                    db.rollback()  # release any open txn; station gone
                    continue
                # _advance_station settles the whole pipeline under the held
                # station lock and flushes; it leaves the commit to the caller.
                # We always commit (it may have advanced phases, granted holds,
                # or surfaced rent markers without logging a discrete event), but
                # only count stations that actually transitioned a reservation.
                snapshot = _construction_state_snapshot(db, station_id)
                construction_service._advance_station(db, station, now)
                changed = _construction_state_snapshot(db, station_id) != snapshot
                db.commit()
                if changed:
                    result["stations"] += 1
            except Exception:
                logger.exception(
                    "Construction advance: pipeline failed for station %s",
                    station_id,
                )
                db.rollback()

        # Final commit closes out any open (no-op) transaction so the advisory
        # lock is not held on the pooled connection.
        db.commit()
        return result
    except Exception:
        logger.exception("Construction advance sweep failed")
        db.rollback()
        return result
    finally:
        db.close()


def _construction_state_snapshot(db: Session, station_id) -> tuple:
    """A cheap (reservation_id, state) fingerprint of a station's non-terminal
    builds, used only to detect whether _advance_station moved anything this
    pass (so the sweep's count reflects real transitions, not no-op passes).
    Read-only; ordered for stable comparison."""
    from src.models.construction import ConstructionReservation

    rows = (
        db.query(ConstructionReservation.id, ConstructionReservation.state)
        .filter(
            ConstructionReservation.station_id == station_id,
            ConstructionReservation.state.notin_(
                ["claimed", "cancelled", "forfeited"]
            ),
        )
        .order_by(ConstructionReservation.id)
        .all()
    )
    return tuple((str(rid), state) for rid, state in rows)


# ---------------------------------------------------------------------------
# Economy-metrics snapshot — daily galaxy-wide economic state writer
# ---------------------------------------------------------------------------

def _compute_daily_economic_enrichment(
    db: Session,
    *,
    window_start: datetime,
    credit_velocity: float,
) -> Dict[str, Any]:
    """Populate the ~13 EconomicMetrics columns the daily snapshot wrote as
    bare column defaults (DATA_MODELS/economy.md:134-140) -- inflation,
    health, volatility, commodity/sector/station leaders, wealth disparity,
    and new-trader count. Pure/session-injectable so it's testable DB-free,
    mirroring reconcile_region_treasuries's pure-fn/day-gate-wrapper split
    (this function has no day-gate of its own -- the caller already owns
    that via the EconomicMetrics.date uniqueness check).

    DEGRADATION -- each field group has its own try/except: a single
    calculator raising leaves ONLY the fields it feeds at the
    EconomicMetrics column default (logged at warning, never the caller's
    problem, never aborts the snapshot). Defaults below are copy-exact from
    the model (market_transaction.py).

    BOUNDEDNESS -- every query here is a single aggregate/fetch round trip,
    never a per-player or per-transaction Python loop:
      - richest_player_credits: one Player fetch (row count = active
        players, not transactions).
      - most/least traded commodity, most_active_sector,
        most_valuable_station, new_traders: one GROUP BY over the trailing
        24h MarketTransaction window each (row count = distinct
        commodities/sectors/stations/traders that day, not raw rows).
      - commodity_price_index / average_profit_margin: ONE shared,
        unfiltered MarketPrice fetch -- bounded by station x commodity
        cardinality (not by trade volume), the same table the pre-existing
        _calculate_market_liquidity / _get_average_prices calculators
        already scan per-commodity.
    economic_health_score / inflation_rate / market_volatility /
    economic_disparity_index / median_player_credits reuse the EXISTING
    EconomyAnalyticsService calculators (_calculate_inflation_rates,
    _calculate_price_volatility, _calculate_wealth_distribution,
    _calculate_health_score) rather than re-deriving them -- their own
    query cost is that service's existing, already-relied-upon behavior.

    NO-CANON: commodity_price_index's "base period" is canon-silent (the
    model defaults to 100.0 with no documented reference point). This uses
    COMMODITY_BASE_PRICES (the static canonical price table) as the
    denominator -- current buy price vs. canonical base price, summed
    across every live MarketPrice row (each station-commodity row counted
    once, so coverage naturally weights toward commodities carried at more
    stations). Flagged for DECISIONS.md: a rolling prior-snapshot baseline
    is an equally valid reading and would produce a different index
    trajectory over time.
    """
    from sqlalchemy import func as sa_func
    from src.core.commodity_economy import COMMODITY_BASE_PRICES
    from src.models.market_transaction import MarketPrice, MarketTransaction
    from src.services.economy_analytics_service import EconomyAnalyticsService

    fields: Dict[str, Any] = {
        "inflation_rate": 0.0,
        "economic_health_score": 0.5,
        "market_volatility": 0.0,
        "most_traded_commodity": None,
        "least_traded_commodity": None,
        "commodity_price_index": 100.0,
        "most_active_sector": None,
        "most_valuable_station": None,
        "economic_disparity_index": 0.0,
        "richest_player_credits": 0,
        "median_player_credits": 0,
        "new_traders": 0,
        "average_profit_margin": 0.0,
    }

    analytics = EconomyAnalyticsService(db)
    volatility_by_commodity: Dict[str, float] = {}
    wealth_dist: Dict[str, Any] = {}

    try:
        inflation_by_commodity = analytics._calculate_inflation_rates()
        if inflation_by_commodity:
            fields["inflation_rate"] = sum(inflation_by_commodity.values()) / len(inflation_by_commodity)
    except Exception:
        logger.warning("Economy snapshot enrichment: inflation_rate failed, left at default", exc_info=True)

    try:
        volatility_by_commodity = analytics._calculate_price_volatility()
        if volatility_by_commodity:
            fields["market_volatility"] = sum(volatility_by_commodity.values()) / len(volatility_by_commodity)
    except Exception:
        volatility_by_commodity = {}
        logger.warning("Economy snapshot enrichment: market_volatility failed, left at default", exc_info=True)

    try:
        wealth_dist = analytics._calculate_wealth_distribution()
        fields["economic_disparity_index"] = float(wealth_dist.get("gini_coefficient", 0.0))
        fields["median_player_credits"] = int(wealth_dist.get("median_wealth", 0))
    except Exception:
        wealth_dist = {}
        logger.warning("Economy snapshot enrichment: wealth distribution failed, left at default", exc_info=True)

    try:
        # _calculate_health_score returns a 0-100 scale; the column is
        # documented 0-1 (market_transaction.py:205, DATA_MODELS/economy.md).
        raw_score = analytics._calculate_health_score(
            {"price_volatility": volatility_by_commodity}, credit_velocity, wealth_dist,
        )
        fields["economic_health_score"] = raw_score / 100.0
    except Exception:
        logger.warning("Economy snapshot enrichment: economic_health_score failed, left at default", exc_info=True)

    try:
        active_credits = (
            db.query(Player.credits)
            .filter(Player.is_active.is_(True))
            .all()
        )
        if active_credits:
            fields["richest_player_credits"] = max(c for (c,) in active_credits)
    except Exception:
        logger.warning("Economy snapshot enrichment: richest_player_credits failed, left at default", exc_info=True)

    try:
        commodity_rows = (
            db.query(MarketTransaction.commodity, sa_func.sum(MarketTransaction.quantity))
            .filter(MarketTransaction.timestamp >= window_start)
            .group_by(MarketTransaction.commodity)
            .order_by(sa_func.sum(MarketTransaction.quantity).desc())
            .all()
        )
        if commodity_rows:
            fields["most_traded_commodity"] = commodity_rows[0][0]
            fields["least_traded_commodity"] = commodity_rows[-1][0]
    except Exception:
        logger.warning("Economy snapshot enrichment: most/least traded commodity failed, left at default", exc_info=True)

    try:
        sector_row = (
            db.query(MarketTransaction.sector_id, sa_func.count(MarketTransaction.id))
            .filter(
                MarketTransaction.timestamp >= window_start,
                MarketTransaction.sector_id.isnot(None),
            )
            .group_by(MarketTransaction.sector_id)
            .order_by(sa_func.count(MarketTransaction.id).desc())
            .first()
        )
        if sector_row:
            fields["most_active_sector"] = int(sector_row[0])
    except Exception:
        logger.warning("Economy snapshot enrichment: most_active_sector failed, left at default", exc_info=True)

    try:
        station_row = (
            db.query(MarketTransaction.station_id, sa_func.sum(MarketTransaction.total_value))
            .filter(
                MarketTransaction.timestamp >= window_start,
                MarketTransaction.station_id.isnot(None),
            )
            .group_by(MarketTransaction.station_id)
            .order_by(sa_func.sum(MarketTransaction.total_value).desc())
            .first()
        )
        if station_row:
            fields["most_valuable_station"] = station_row[0]
    except Exception:
        logger.warning("Economy snapshot enrichment: most_valuable_station failed, left at default", exc_info=True)

    try:
        # A "new trader" is a player whose EARLIEST-ever transaction falls
        # inside this window -- GROUP BY + HAVING MIN(timestamp), not a
        # window-only COUNT, so a long-time trader who simply traded today
        # doesn't get miscounted as new.
        new_trader_rows = (
            db.query(MarketTransaction.player_id)
            .filter(MarketTransaction.player_id.isnot(None))
            .group_by(MarketTransaction.player_id)
            .having(sa_func.min(MarketTransaction.timestamp) >= window_start)
            .all()
        )
        fields["new_traders"] = len(new_trader_rows)
    except Exception:
        logger.warning("Economy snapshot enrichment: new_traders failed, left at default", exc_info=True)

    try:
        price_rows = db.query(MarketPrice.commodity, MarketPrice.buy_price, MarketPrice.sell_price).all()
        index_numerator = index_denominator = 0.0
        margin_values: List[float] = []
        for commodity, buy_price, sell_price in price_rows:
            base = COMMODITY_BASE_PRICES.get(commodity, {}).get("base")
            if base:
                index_numerator += float(buy_price)
                index_denominator += float(base)
            if sell_price and sell_price > 0:
                margin_values.append((sell_price - buy_price) / sell_price * 100.0)
        if index_denominator > 0:
            fields["commodity_price_index"] = (index_numerator / index_denominator) * 100.0
        if margin_values:
            fields["average_profit_margin"] = sum(margin_values) / len(margin_values)
    except Exception:
        logger.warning(
            "Economy snapshot enrichment: commodity_price_index/average_profit_margin failed, left at default",
            exc_info=True,
        )

    return fields


def _run_economic_metrics_snapshot_sync() -> Dict[str, Any]:
    """Write ONE daily EconomicMetrics row so the admin economy dashboard has
    real data instead of zeros.

    economy_analytics_service.get_economic_metrics() reads the most-recent
    EconomicMetrics row (``order_by(date.desc()).first()``) and surfaces four
    fields in the dashboard's "latest metrics" panel:
      - total_credits_in_circulation
      - total_trade_volume        (shown as "total_resources")
      - total_players_trading     (shown as "active_traders")
      - credit_velocity           (shown as "market_liquidity")
    Nothing ever WROTE an EconomicMetrics row, so that panel was permanently
    0/empty. This sweep populates exactly those fields (plus the cheap
    complementary columns) once per day.

    DISCIPLINE — mirrors the genesis/planetary/governance sweeps exactly:
      * own SessionLocal (never the request session, never the async engine);
      * xact-level advisory lock so a second gameserver instance skips instead
        of double-writing (and the lock auto-releases on commit/rollback);
      * commit releases the lock; failure is isolated (rolled back, logged,
        loop continues).

    IDEMPOTENCY — at most one snapshot per calendar day. The durable anchor is
    the unique, midnight-truncated ``EconomicMetrics.date`` column: we check for
    an existing row dated today (>= midnight UTC) BEFORE computing/inserting and
    no-op if present. The midnight truncation (rather than ``utcnow()``) is what
    makes the daily guard robust — two wakes on the same day resolve to the same
    ``date`` value, so the second is skipped (and, even if a race slipped past
    the check, the UNIQUE constraint on ``date`` would reject the duplicate,
    which the outer except rolls back without aborting the scheduler). This is
    the same durable-per-row-anchor pattern the weekly decay uses, keyed off a
    DB column instead of Galaxy.state.

    CIRCULATION — total_credits_in_circulation sums every credit pool the game
    actually tracks: active-player wallets (the analytics _calculate_money_supply
    number), NPC trader wallets (TRADER NPCs are full market actors —
    market_transaction.py), and the Region + Station treasuries (Integer credit
    pools). credits_in_player_accounts / credits_in_npc_accounts break that out.

    VOLUME — total_trade_volume / total_transactions / average_transaction_value
    come from the trailing-24h MarketTransaction window (the same window the
    analytics GDP/velocity calcs use), and credit_velocity = volume / money
    supply (mirroring _calculate_market_velocity), so the snapshot is internally
    consistent with the live-computed indicators on the same dashboard.

    Returns {"written": bool, "date": iso|None, "total_credits": int,
    "trade_volume": int, "active_traders": int}; written=False on the
    already-snapshotted / lock-held / no-galaxy no-op paths.
    """
    from src.core.database import SessionLocal
    from src.models.market_transaction import EconomicMetrics, MarketTransaction
    from src.models.npc_character import NPCCharacter
    from src.models.player import Player
    from src.models.region import Region
    from src.models.station import Station
    from sqlalchemy import func as sa_func

    not_written = {
        "written": False, "date": None,
        "total_credits": 0, "trade_volume": 0, "active_traders": 0,
    }

    db = SessionLocal()
    try:
        got_lock = db.execute(
            text("SELECT pg_try_advisory_xact_lock(:key)"),
            {"key": _ECONOMIC_METRICS_LOCK_KEY},
        ).scalar()
        if not got_lock:
            return not_written

        # Durable daily anchor: midnight-truncated UTC. One row per calendar day.
        now = datetime.utcnow()
        today_midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)

        existing = (
            db.query(EconomicMetrics.id)
            .filter(EconomicMetrics.date >= today_midnight)
            .first()
        )
        if existing is not None:
            # Already snapshotted today — clean no-op (release the lock).
            db.commit()
            return not_written

        # --- Credit circulation -------------------------------------------
        player_credits = int(
            db.query(sa_func.coalesce(sa_func.sum(Player.credits), 0))
            .filter(Player.is_active.is_(True))
            .scalar() or 0
        )
        npc_credits = int(
            db.query(sa_func.coalesce(sa_func.sum(NPCCharacter.credits), 0))
            .scalar() or 0
        )
        region_treasury = int(
            db.query(sa_func.coalesce(sa_func.sum(Region.treasury_balance), 0))
            .scalar() or 0
        )
        station_treasury = int(
            db.query(sa_func.coalesce(sa_func.sum(Station.treasury_balance), 0))
            .scalar() or 0
        )
        total_credits = (
            player_credits + npc_credits + region_treasury + station_treasury
        )

        # --- Market volume (trailing 24h, same window as the analytics GDP) -
        window_start = now - timedelta(days=1)
        vol_row = (
            db.query(
                sa_func.coalesce(sa_func.sum(MarketTransaction.total_value), 0),
                sa_func.count(MarketTransaction.id),
            )
            .filter(MarketTransaction.timestamp >= window_start)
            .first()
        )
        total_trade_volume = int(vol_row[0] or 0) if vol_row else 0
        total_transactions = int(vol_row[1] or 0) if vol_row else 0
        avg_transaction_value = (
            float(total_trade_volume) / total_transactions
            if total_transactions > 0 else 0.0
        )

        # Distinct players that traded in the window (the dashboard's
        # "active_traders"); NPC trades carry npc_id, not player_id.
        active_traders = int(
            db.query(
                sa_func.count(sa_func.distinct(MarketTransaction.player_id))
            )
            .filter(
                MarketTransaction.timestamp >= window_start,
                MarketTransaction.player_id.isnot(None),
            )
            .scalar() or 0
        )

        # Credit velocity = trailing-24h volume / money supply (active-player
        # credits), mirroring _calculate_market_velocity so the stored value
        # matches the live-computed one on the same dashboard.
        credit_velocity = (
            float(total_trade_volume) / player_credits
            if player_credits > 0 else 0.0
        )

        # Enrichment (WO-ECON-METRICS-ENRICH) -- inflation/health/volatility/
        # commodity+sector+station leaders/wealth disparity/new-traders. Its
        # own internal try/except degrades any single failed calculator to
        # that field's column default; it never raises, so it can't abort
        # this snapshot.
        enrichment = _compute_daily_economic_enrichment(
            db, window_start=window_start, credit_velocity=credit_velocity,
        )

        snapshot = EconomicMetrics(
            date=today_midnight,
            metric_type="daily",
            total_trade_volume=total_trade_volume,
            total_transactions=total_transactions,
            average_transaction_value=avg_transaction_value,
            total_credits_in_circulation=total_credits,
            credits_in_player_accounts=player_credits,
            credits_in_npc_accounts=npc_credits,
            credit_velocity=credit_velocity,
            total_players_trading=active_traders,
            **enrichment,
        )
        db.add(snapshot)
        db.commit()  # releases the xact lock

        logger.info(
            "Economy snapshot: %s — circulation=%d cr (player=%d npc=%d "
            "region=%d station=%d), 24h volume=%d cr over %d txn, "
            "active_traders=%d, velocity=%.4f",
            today_midnight.date().isoformat(), total_credits, player_credits,
            npc_credits, region_treasury, station_treasury, total_trade_volume,
            total_transactions, active_traders, credit_velocity,
        )
        return {
            "written": True,
            "date": today_midnight.isoformat(),
            "total_credits": total_credits,
            "trade_volume": total_trade_volume,
            "active_traders": active_traders,
        }
    except Exception:
        # Includes the unique-constraint race on EconomicMetrics.date: roll back
        # the duplicate insert; tomorrow's wake retries cleanly.
        logger.exception("Economy snapshot sweep failed")
        db.rollback()
        return not_written
    finally:
        db.close()


