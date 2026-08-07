"""ADR-0050 SK22 — Phase 14 retry policy + refund trigger.

DB-free unit tests for the pure logic in
``src.services.region_attachment_service``: the exponential-backoff
schedule, the idempotency-key shape, the 5-failed-attempts exhaustion
transition, the ops-alert/ARIA-narration payload builders, and the
refund-trigger decision function (the safety rail: a delayed-but-fine
Nexus warp must NEVER trigger a refund).

The idempotent-insert / retry-sweep DB-touching paths
(``attempt_insert_warp_tunnel``, ``due_for_retry``, ``retry_one``,
``sweep_due_retries``) use a Postgres-dialect ``INSERT ... ON CONFLICT``
that isn't meaningfully fakeable without a live Postgres — same
"no local Postgres on the Mac" convention as
``test_region_lifecycle_schema.py``; the ci-schema-parity /
core-loop-playthrough CI gates are the authoritative apply proof for
those. This file covers everything reachable DB-free.
"""
from __future__ import annotations

import ast
import pathlib
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from src.models.region import Region, RegionStatus
from src.services.region_attachment_service import (
    PHASE14_BACKOFF_SCHEDULE_SECONDS,
    PHASE14_MAX_ATTEMPTS,
    PHASE14_OWNER_NARRATION_TEXT,
    RegionAttachmentService,
    attempts_exhausted,
    backoff_seconds_for_attempt,
    build_ops_alert_event,
    build_owner_narration_event,
    make_idempotency_key,
    refund_trigger_reason,
)


def _region(**overrides) -> Region:
    region = Region()
    region.id = overrides.pop("id", uuid.uuid4())
    region.name = overrides.pop("name", "Test Spoke Region")
    region.status = overrides.pop("status", RegionStatus.ACTIVE)
    region.owner_id = overrides.pop("owner_id", uuid.uuid4())
    region.nexus_attach_attempt_count = overrides.pop("nexus_attach_attempt_count", 0)
    region.nexus_attach_next_retry_at = overrides.pop("nexus_attach_next_retry_at", None)
    region.nexus_attach_operator_confirmed = overrides.pop(
        "nexus_attach_operator_confirmed", False
    )
    region.nexus_attach_completed_at = overrides.pop("nexus_attach_completed_at", None)
    for k, v in overrides.items():
        setattr(region, k, v)
    return region


# ---------------------------------------------------------------------------
# Backoff schedule
# ---------------------------------------------------------------------------

class TestBackoffSchedule:
    def test_schedule_pinned_to_canon(self):
        """ADR-0050 SK22 verbatim: 1s, 5s, 30s, 5min, 30min, 6h."""
        assert PHASE14_BACKOFF_SCHEDULE_SECONDS == (1, 5, 30, 300, 1800, 21600)

    def test_max_attempts_pinned_to_canon(self):
        """ADR-0050 SK22: "after 5 failed attempts"."""
        assert PHASE14_MAX_ATTEMPTS == 5

    @pytest.mark.parametrize(
        "attempt_n,expected",
        [(1, 1), (2, 5), (3, 30), (4, 300), (5, 1800), (6, 21600)],
    )
    def test_backoff_seconds_for_attempt(self, attempt_n, expected):
        assert backoff_seconds_for_attempt(attempt_n) == expected

    def test_backoff_clamps_past_schedule_length(self):
        assert backoff_seconds_for_attempt(99) == PHASE14_BACKOFF_SCHEDULE_SECONDS[-1]

    def test_backoff_rejects_non_positive_attempt(self):
        with pytest.raises(ValueError):
            backoff_seconds_for_attempt(0)

    @pytest.mark.parametrize(
        "attempt_n,expected",
        [(1, False), (2, False), (3, False), (4, False), (5, True), (6, True)],
    )
    def test_attempts_exhausted(self, attempt_n, expected):
        assert attempts_exhausted(attempt_n) is expected


# ---------------------------------------------------------------------------
# Idempotency key
# ---------------------------------------------------------------------------

class TestIdempotencyKey:
    def test_key_shape_is_region_id_colon_attempt_n(self):
        rid = uuid.uuid4()
        assert make_idempotency_key(rid, 3) == f"{rid}:3"

    def test_key_distinct_per_attempt(self):
        rid = uuid.uuid4()
        keys = {make_idempotency_key(rid, n) for n in range(1, 7)}
        assert len(keys) == 6

    def test_key_distinct_per_region(self):
        a, b = uuid.uuid4(), uuid.uuid4()
        assert make_idempotency_key(a, 1) != make_idempotency_key(b, 1)


# ---------------------------------------------------------------------------
# record_failure / record_success — pure Region mutation, no DB
# ---------------------------------------------------------------------------

class TestRecordFailureExhaustion:
    def test_first_failure_schedules_backoff_and_does_not_exhaust(self):
        region = _region()
        svc = RegionAttachmentService(db=None)
        now = datetime(2026, 8, 5, tzinfo=timezone.utc)

        exhausted = svc.record_failure(region, now=now)

        assert exhausted is False
        assert region.nexus_attach_attempt_count == 1
        assert region.nexus_attach_next_retry_at == now + timedelta(seconds=1)
        assert region.status == RegionStatus.ACTIVE

    def test_fifth_failure_exhausts_and_flips_status(self):
        region = _region(nexus_attach_attempt_count=4)
        svc = RegionAttachmentService(db=None)
        now = datetime(2026, 8, 5, tzinfo=timezone.utc)

        exhausted = svc.record_failure(region, now=now)

        assert exhausted is True
        assert region.nexus_attach_attempt_count == 5
        assert region.status == RegionStatus.ATTACHMENT_PENDING
        # No more automatic retries once exhausted.
        assert region.nexus_attach_next_retry_at is None

    def test_full_backoff_walk_matches_canon_schedule(self):
        """Simulate 5 consecutive failures; assert each scheduled delay
        matches the canon schedule and only the 5th exhausts."""
        region = _region()
        svc = RegionAttachmentService(db=None)
        now = datetime(2026, 8, 5, tzinfo=timezone.utc)
        expected_delays = [1, 5, 30, 300]  # attempts 1-4 schedule a retry

        for i, expected_delay in enumerate(expected_delays, start=1):
            exhausted = svc.record_failure(region, now=now)
            assert exhausted is False, f"attempt {i} should not exhaust"
            assert region.nexus_attach_next_retry_at == now + timedelta(seconds=expected_delay)

        # 5th failure exhausts.
        exhausted = svc.record_failure(region, now=now)
        assert exhausted is True
        assert region.nexus_attach_attempt_count == 5
        assert region.status == RegionStatus.ATTACHMENT_PENDING

    def test_record_success_clears_bookkeeping_and_stamps_completion(self):
        region = _region(
            nexus_attach_attempt_count=3,
            nexus_attach_next_retry_at=datetime(2026, 8, 5, tzinfo=timezone.utc),
        )
        svc = RegionAttachmentService(db=None)
        now = datetime(2026, 8, 6, tzinfo=timezone.utc)

        svc.record_success(region, now=now)

        assert region.nexus_attach_attempt_count == 0
        assert region.nexus_attach_next_retry_at is None
        assert region.nexus_attach_completed_at == now


# ---------------------------------------------------------------------------
# Ops alert / ARIA narration payloads
# ---------------------------------------------------------------------------

class TestEventBuilders:
    def test_ops_alert_event_shape(self):
        region = _region(nexus_attach_attempt_count=5)
        event = build_ops_alert_event(region)

        assert event["type"] == "region_attachment_pending"
        assert event["region_id"] == str(region.id)
        assert event["owner_id"] == str(region.owner_id)
        assert event["attempt_count"] == 5
        assert "attachment_pending" in event["message"]

    def test_ops_alert_event_owner_id_none_when_unowned(self):
        region = _region(owner_id=None)
        event = build_ops_alert_event(region)
        assert event["owner_id"] is None

    def test_owner_narration_event_uses_exact_canon_text(self):
        region = _region()
        event = build_owner_narration_event(region)

        assert event["type"] == "aria_narration"
        assert event["owner_id"] == str(region.owner_id)
        assert event["line"] == PHASE14_OWNER_NARRATION_TEXT
        assert event["line"] == (
            "Your home region's Nexus connection is taking longer than "
            "expected. You can travel there directly from your current "
            "location whenever you're ready."
        )


# ---------------------------------------------------------------------------
# Refund trigger — the safety rail
# ---------------------------------------------------------------------------

class TestRefundTriggerReason:
    def test_generation_corrupt_always_refunds(self):
        region = _region(status=RegionStatus.GENERATION_CORRUPT)
        assert refund_trigger_reason(region) == "generation_corrupt"

    def test_generation_corrupt_refunds_even_without_operator_confirmation(self):
        region = _region(
            status=RegionStatus.GENERATION_CORRUPT,
            nexus_attach_operator_confirmed=False,
        )
        assert refund_trigger_reason(region) == "generation_corrupt"

    def test_attachment_pending_with_operator_confirmation_refunds(self):
        region = _region(
            status=RegionStatus.ATTACHMENT_PENDING,
            nexus_attach_operator_confirmed=True,
        )
        assert refund_trigger_reason(region) == "attachment_pending_operator_confirmed"

    def test_attachment_pending_without_operator_confirmation_does_not_refund(self):
        """The core safety rail: retries exhausted alone is NOT enough."""
        region = _region(
            status=RegionStatus.ATTACHMENT_PENDING,
            nexus_attach_operator_confirmed=False,
        )
        assert refund_trigger_reason(region) is None

    def test_active_region_mid_retry_never_refunds(self):
        """A delayed-but-otherwise-functional region (still mid-backoff,
        status still ACTIVE) must never trigger a refund — ADR-0050 SK22's
        explicit "not refundable" case."""
        region = _region(
            status=RegionStatus.ACTIVE,
            nexus_attach_attempt_count=3,
            nexus_attach_operator_confirmed=True,  # even if somehow set
        )
        assert refund_trigger_reason(region) is None

    def test_active_healthy_region_does_not_refund(self):
        region = _region(status=RegionStatus.ACTIVE)
        assert refund_trigger_reason(region) is None

    def test_suspended_and_terminated_do_not_refund(self):
        for status in (RegionStatus.SUSPENDED, RegionStatus.TERMINATED, RegionStatus.GRACE, RegionStatus.PENDING):
            region = _region(status=status, nexus_attach_operator_confirmed=True)
            assert refund_trigger_reason(region) is None, status


# ---------------------------------------------------------------------------
# Migration shape (mirrors test_region_lifecycle_schema.py's AST convention —
# no local Postgres on the Mac to apply against)
# ---------------------------------------------------------------------------

class TestMigrationShape:
    _MIGRATION_PATH = (
        pathlib.Path(__file__).resolve().parents[2]
        / "alembic" / "versions"
        / "c4f9a2e17b83_sk22_phase14_attachment_retry.py"
    )

    def _tree(self):
        return ast.parse(self._MIGRATION_PATH.read_text(encoding="utf-8"))

    def test_revision_chains_onto_sk24_head(self):
        tree = self._tree()
        assigns = {
            n.targets[0].id: n.value.value
            for n in ast.walk(tree)
            if isinstance(n, ast.Assign)
            and isinstance(n.targets[0], ast.Name)
            and isinstance(n.value, ast.Constant)
        }
        assert assigns["revision"] == "c4f9a2e17b83"
        assert assigns["down_revision"] == "b3e8c1a94d70"

    def test_upgrade_adds_expected_region_columns(self):
        src = self._MIGRATION_PATH.read_text(encoding="utf-8")
        for col in (
            "nexus_attach_attempt_count",
            "nexus_attach_next_retry_at",
            "nexus_attach_operator_confirmed",
            "nexus_attach_completed_at",
        ):
            assert col in src

    def test_upgrade_adds_warp_tunnel_idempotency_key_with_unique_constraint(self):
        src = self._MIGRATION_PATH.read_text(encoding="utf-8")
        assert "idempotency_key" in src
        assert "uq_warp_tunnels_idempotency_key" in src

    def test_downgrade_reverses_every_upgrade_column(self):
        src = self._MIGRATION_PATH.read_text(encoding="utf-8")
        upgrade_src, downgrade_src = src.split("def downgrade")
        for col in (
            "nexus_attach_attempt_count",
            "nexus_attach_next_retry_at",
            "nexus_attach_operator_confirmed",
            "nexus_attach_completed_at",
            "idempotency_key",
        ):
            assert col in downgrade_src, f"downgrade() must drop {col}"


# ---------------------------------------------------------------------------
# RegionStatus enum values already shipped (ADR-0050's own index note) —
# re-verified per the WO's explicit "confirm, don't take on faith" ask.
# ---------------------------------------------------------------------------

class TestRegionStatusAlreadyShipped:
    def test_attachment_pending_value(self):
        assert RegionStatus.ATTACHMENT_PENDING.value == "attachment_pending"

    def test_generation_corrupt_value(self):
        assert RegionStatus.GENERATION_CORRUPT.value == "generation_corrupt"
