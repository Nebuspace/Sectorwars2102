"""ADR-0050 SK22 — Phase 14 (Nexus-warp attachment) retry policy + refund
trigger.

Scope note: this module builds ONLY the SK22 retry/refund mechanism —
NOT the broader "owner relocation flow" (transport-to-new-region, first
login placement), which is separately held pending a Max ruling on
ADR-0050's heavy remainder.

Canon (sw2102-docs/ADR/0050-batch3-provisioning-lifecycle-hardening.md
:194-206): Phase 14 is the cross-region warp-tunnel attachment that links
a freshly-provisioned spoke region's Frontier Gateway Plaza landing sector
to the Central Nexus (ADR-0043). Phases 1-13 succeeding means the region
itself is fine; Phase 14 failing only means the Nexus warp isn't wired
yet. At-least-once retry with idempotency (``region.id + attempt_n`` key,
``INSERT ... ON CONFLICT DO NOTHING`` on the warp-tunnel row), exponential
backoff 1s/5s/30s/5min/30min/6h, and after 5 failed attempts the region
flips to ``attachment_pending`` with an ops alert + ARIA narration to the
owner.

Refund only fires on ``generation_corrupt`` or on ``attachment_pending``
AFTER retries are exhausted AND an operator has confirmed a persistent
infrastructure issue (``Region.nexus_attach_operator_confirmed``) — a
delayed-but-eventually-working Nexus warp must NEVER trigger a refund.
That operator-confirmation gate is deliberate: it is what keeps this a
human decision, not an automatic one, per the ADR text.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from src.models.region import Region, RegionStatus
from src.models.sector import Sector
from src.models.warp_tunnel import WarpTunnel, WarpTunnelType

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Backoff schedule (ADR-0050 SK22, verbatim): 1s, 5s, 30s, 5min, 30min, 6h.
# Index i is the delay (seconds) before retrying after attempt (i+1) fails.
# PHASE14_MAX_ATTEMPTS is "5 failed attempts" from the ADR text — the region
# flips to attachment_pending once the 5th attempt has failed, so only the
# first 4 backoff values are ever consumed as an actual wait; the 5th/6th
# (30min, 6h) are retained verbatim from canon for completeness/documentation
# of the full published schedule and in case a future ops-driven manual retry
# wants to reuse them, but this module's automatic loop gives up at 5.
PHASE14_BACKOFF_SCHEDULE_SECONDS: tuple = (1, 5, 30, 300, 1800, 21600)
PHASE14_MAX_ATTEMPTS = 5

#: ARIA narration text to the owner, verbatim from ADR-0050 SK22.
PHASE14_OWNER_NARRATION_TEXT = (
    "Your home region's Nexus connection is taking longer than expected. "
    "You can travel there directly from your current location whenever "
    "you're ready."
)


def make_idempotency_key(region_id: uuid.UUID, attempt_n: int) -> str:
    """``region.id + attempt_n`` idempotency key, per ADR-0050 SK22."""
    return f"{region_id}:{attempt_n}"


def backoff_seconds_for_attempt(attempt_n: int) -> int:
    """Delay (seconds) to wait after ``attempt_n`` fails before retrying.

    ``attempt_n`` is 1-indexed. Clamped to the schedule's last entry if
    called past its length (defensive; the retry loop itself stops at
    ``PHASE14_MAX_ATTEMPTS``).
    """
    if attempt_n < 1:
        raise ValueError("attempt_n is 1-indexed and must be >= 1")
    idx = min(attempt_n - 1, len(PHASE14_BACKOFF_SCHEDULE_SECONDS) - 1)
    return PHASE14_BACKOFF_SCHEDULE_SECONDS[idx]


def attempts_exhausted(attempt_n: int) -> bool:
    """True once ``attempt_n`` failed attempts have been recorded."""
    return attempt_n >= PHASE14_MAX_ATTEMPTS


def build_ops_alert_event(region: Region) -> Dict[str, Any]:
    """Ops-alert payload for a Phase-14 attachment-retry exhaustion.

    Shape mirrors the existing scheduler ops-alert convention (see
    ``services/scheduler/_common.py::_broadcast_events`` — routed via
    ``connection_manager.broadcast_to_admins`` on the event loop by the
    caller, the same admin/ops fan-out every other scheduler alert uses).
    """
    return {
        "type": "region_attachment_pending",
        "region_id": str(region.id),
        "region_name": region.name,
        "owner_id": str(region.owner_id) if region.owner_id else None,
        "attempt_count": region.nexus_attach_attempt_count,
        "message": (
            f"Region {region.name} ({region.id}) exhausted "
            f"{PHASE14_MAX_ATTEMPTS} Phase-14 Nexus-attachment retries; "
            "flipped to attachment_pending. Not refundable automatically — "
            "requires operator confirmation of a persistent infrastructure "
            "issue before any refund trigger fires."
        ),
    }


def build_owner_narration_event(region: Region) -> Dict[str, Any]:
    """Personal-frame ARIA narration payload to the region owner.

    Routed via ``connection_manager.send_personal_message`` (the same
    per-user delivery primitive ``_broadcast_events`` uses for
    ``genesis_progress`` — a personal frame, not a sector/region room
    broadcast) by the caller.
    """
    return {
        "type": "aria_narration",
        "event": "region_attachment_pending",
        "region_id": str(region.id),
        "owner_id": str(region.owner_id) if region.owner_id else None,
        "line": PHASE14_OWNER_NARRATION_TEXT,
    }


def refund_trigger_reason(region: Region) -> Optional[str]:
    """Pure decision function: does this region's current state license a
    refund? Returns a reason string, or ``None`` if no refund should fire.

    Per ADR-0050 SK22 "Refund only fires" clause:
    - ``generation_corrupt`` (Phase 11/12/13 failure) always qualifies.
    - ``attachment_pending`` qualifies ONLY when retries are exhausted
      (implied by the region having reached this status at all — see
      :func:`attempts_exhausted`) AND an operator has explicitly confirmed
      a persistent infrastructure issue
      (``Region.nexus_attach_operator_confirmed``). A region that is merely
      slow-to-attach-but-otherwise-functional (status still ``active``,
      or ``attachment_pending`` without operator confirmation) is NOT
      refundable — this is the safety rail the ADR calls out explicitly.

    This function does not itself call any payment provider — see the
    module docstring / SK22 report for why (no PayPal refund capability
    exists in this codebase today; wiring the actual refund call is a
    genuine follow-on gap, not something this WO builds).
    """
    if region.status == RegionStatus.GENERATION_CORRUPT:
        return "generation_corrupt"
    if (
        region.status == RegionStatus.ATTACHMENT_PENDING
        and bool(region.nexus_attach_operator_confirmed)
    ):
        return "attachment_pending_operator_confirmed"
    return None


def sweep_due_retries(
    db: Session, *, now: Optional[datetime] = None
) -> Dict[str, Any]:
    """Retry every region whose Phase-14 backoff window has elapsed.

    Thin pure-logic entrypoint the sync scheduler sweep wraps (mirrors
    ``BountyService(db).expire_due_bounties`` /
    ``message_beacon_service.sweep_expired`` — the session/lock/due-check
    wrapper lives in ``scheduler/economy_sweeps.py``, this function is the
    testable core). Returns
    ``{"retried": n, "succeeded": n, "exhausted": n, "events": [...]}``.
    """
    now = now or datetime.now(timezone.utc)
    svc = RegionAttachmentService(db)
    due = svc.due_for_retry(now=now)
    events: List[Dict[str, Any]] = []
    succeeded = 0
    exhausted = 0
    for region in due:
        was_pending_before = region.nexus_attach_completed_at is None
        region_events = svc.retry_one(region, now=now)
        if region_events:
            exhausted += 1
            events.extend(region_events)
        elif was_pending_before and region.nexus_attach_completed_at is not None:
            succeeded += 1
    return {
        "retried": len(due),
        "succeeded": succeeded,
        "exhausted": exhausted,
        "events": events,
    }


class RegionAttachmentService:
    """Phase-14 (Nexus-warp attachment) retry state machine.

    Sync-``Session`` service, mirroring the existing scheduler-sweep
    convention (``BountyService(db).expire_due_bounties`` etc.) — the
    per-region retry sweep is a coarse-cadence scan gated per-row by each
    region's own ``nexus_attach_next_retry_at`` timestamp, so individual
    regions retry on the exponential-backoff schedule independent of how
    often the sweep itself runs.
    """

    def __init__(self, db: Session):
        self.db = db

    def record_success(
        self, region: Region, *, now: Optional[datetime] = None
    ) -> None:
        """Phase 14 succeeded — clear retry bookkeeping and stamp completion.

        ``nexus_attach_completed_at`` (not ``nexus_warp_sector``, which is
        set earlier during region-content generation regardless of tunnel
        success) is the actual "done" signal :meth:`due_for_retry` reads.
        """
        region.nexus_attach_attempt_count = 0  # type: ignore[assignment]
        region.nexus_attach_next_retry_at = None  # type: ignore[assignment]
        region.nexus_attach_completed_at = now or datetime.now(timezone.utc)  # type: ignore[assignment]

    def record_failure(self, region: Region, *, now: Optional[datetime] = None) -> bool:
        """Record one failed Phase-14 attempt.

        Increments the attempt counter and schedules the next retry per
        the backoff schedule. Once :data:`PHASE14_MAX_ATTEMPTS` failed
        attempts have been recorded, flips ``region.status`` to
        ``attachment_pending`` and clears the retry timer (no more
        automatic retries once exhausted — ops/operator take over from
        there). Returns ``True`` iff this call is the one that exhausted
        retries (so the caller knows to fire the ops alert + ARIA
        narration exactly once, not on every subsequent sweep pass).
        """
        now = now or datetime.now(timezone.utc)
        attempt_n = (region.nexus_attach_attempt_count or 0) + 1
        region.nexus_attach_attempt_count = attempt_n  # type: ignore[assignment]

        if attempts_exhausted(attempt_n):
            region.status = RegionStatus.ATTACHMENT_PENDING  # type: ignore[assignment]
            region.nexus_attach_next_retry_at = None  # type: ignore[assignment]
            return True

        delay = backoff_seconds_for_attempt(attempt_n)
        region.nexus_attach_next_retry_at = now + timedelta(seconds=delay)  # type: ignore[assignment]
        return False

    def attempt_insert_warp_tunnel(
        self,
        *,
        region_id: uuid.UUID,
        attempt_n: int,
        name: str,
        origin_sector_id: uuid.UUID,
        destination_sector_id: uuid.UUID,
        description: str,
    ) -> bool:
        """Idempotent Phase-14 warp-tunnel insert (sync path, for the retry
        sweep). ``INSERT ... ON CONFLICT (idempotency_key) DO NOTHING``,
        keyed on ``region.id + attempt_n`` per ADR-0050 SK22. Returns
        ``True`` iff a row was actually inserted (``False`` on a duplicate
        no-op — the caller treats that as "already attached", not a
        failure).
        """
        key = make_idempotency_key(region_id, attempt_n)
        stmt = (
            pg_insert(WarpTunnel)
            .values(
                name=name,
                origin_sector_id=origin_sector_id,
                destination_sector_id=destination_sector_id,
                type=WarpTunnelType.NATURAL,
                is_bidirectional=True,
                is_latent=False,
                description=description,
                idempotency_key=key,
            )
            .on_conflict_do_nothing(index_elements=["idempotency_key"])
        )
        result = self.db.execute(stmt)
        return bool(result.rowcount)

    def due_for_retry(self, *, now: Optional[datetime] = None) -> List[Region]:
        """Regions with a Phase-14 attachment still outstanding and whose
        backoff window has elapsed. Excludes regions that have already
        given up (``attachment_pending``) or already succeeded
        (``nexus_warp_sector`` set)."""
        now = now or datetime.now(timezone.utc)
        stmt = select(Region).where(
            Region.nexus_attach_completed_at.is_(None),
            Region.status == RegionStatus.ACTIVE,
            Region.nexus_attach_attempt_count > 0,
            Region.nexus_attach_next_retry_at.isnot(None),
            Region.nexus_attach_next_retry_at <= now,
        )
        return list(self.db.execute(stmt).scalars().all())

    def retry_one(self, region: Region, *, now: Optional[datetime] = None) -> List[Dict[str, Any]]:
        """Retry Phase-14 attachment for one due region.

        Re-derives the spoke landing sector (``Region.nexus_warp_sector`` —
        the region-local sector number chosen during region-content
        generation, ADR-0043) and the current Central Nexus gate sector
        purely from DB state (no in-memory plan survives across retries).
        Attempts the idempotent insert; on success stamps
        ``nexus_attach_completed_at`` via :meth:`record_success`. On
        failure (including "no qualifying sector found" — a degraded
        region) records the failure via :meth:`record_failure`.

        Returns ``[ops_alert_event, owner_narration_event]`` iff this call
        is the one that exhausted retries (mirrors :meth:`record_failure`'s
        "only once" contract) — ``[]`` otherwise (success, or a failure
        that still has retries left). Caller broadcasts these on the event
        loop, mirroring every other scheduler sweep's events-list contract
        (``_broadcast_events``).
        """
        now = now or datetime.now(timezone.utc)

        spoke_sector = self.db.execute(
            select(Sector).where(
                Sector.region_id == region.id,
                Sector.sector_number == region.nexus_warp_sector,
            )
        ).scalars().first()
        nexus_gate_row = self.db.execute(
            text(
                "SELECT s.id FROM sectors s "
                "JOIN regions r ON s.region_id = r.id "
                "WHERE r.region_type = 'central_nexus' "
                "ORDER BY s.sector_id ASC LIMIT 1"
            )
        ).first()

        if spoke_sector is None or nexus_gate_row is None:
            logger.error(
                "ADR-0050 SK22: Phase-14 retry for region %s has no "
                "resolvable spoke/nexus endpoint (spoke=%s, nexus=%s)",
                region.id, spoke_sector, nexus_gate_row,
            )
            exhausted = self.record_failure(region, now=now)
            if exhausted:
                return [build_ops_alert_event(region), build_owner_narration_event(region)]
            return []

        attempt_n = (region.nexus_attach_attempt_count or 0) + 1
        try:
            self.attempt_insert_warp_tunnel(
                region_id=region.id,
                attempt_n=attempt_n,
                name="Player Owned ↔ Central Nexus",
                origin_sector_id=spoke_sector.id,
                destination_sector_id=nexus_gate_row[0],
                description=(
                    "Natural Nexus warp linking player_owned region "
                    "(Frontier Gateway Plaza) to central_nexus — "
                    "pre-discovered gateway, visible to every player "
                    "(Terran + Nexus canon). Phase-14 retry attempt "
                    f"{attempt_n}."
                ),
            )
        except Exception:
            logger.exception(
                "ADR-0050 SK22: Phase-14 retry insert failed for region %s "
                "(attempt %d)", region.id, attempt_n,
            )
            exhausted = self.record_failure(region, now=now)
            if exhausted:
                return [build_ops_alert_event(region), build_owner_narration_event(region)]
            return []

        self.record_success(region, now=now)
        return []
