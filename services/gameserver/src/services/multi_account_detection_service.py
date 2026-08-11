"""Multi-account detection sweep (WO-BUILD-MULTI-ACCOUNT-DETECTION-SWEEP).

Canon: OPERATIONS/multi-account-detection.md, ADR-0056, DATA_MODELS/gameplay.md.

Populates ``MultiAccountCluster`` / ``MultiAccountFlag`` so the admin review
queue and ``participation_weight`` (HARD → 0.0) have rows to read.

Severity policy for this WO:
- HARD flags: shared ``User.paypal_subscription_id`` (payment_method).
- SOFT flags: shared IP within 24h (PlayerSession), shared device fingerprint
  (RegionInviteRedemption) — written for admin monitoring only.
- Soft-tier ``participation_weight`` 0.5 discount stays OUT OF SCOPE
  (``multi_account_service.participation_weight`` remains HARD/OK only).

Idempotent: re-running with the same evidence does not duplicate flags.
OVERRIDDEN clusters for the same (players, signal) are not recreated.
"""

from __future__ import annotations

import logging
import uuid
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from sqlalchemy.orm import Session

from src.models.multi_account import (
    MultiAccountAdminDecision,
    MultiAccountCluster,
    MultiAccountFlag,
    MultiAccountSeverity,
)
from src.models.player import Player
from src.models.player_analytics import PlayerSession
from src.models.region_invite import RegionInviteRedemption
from src.models.user import User

logger = logging.getLogger(__name__)

SIGNAL_PAYMENT_METHOD = "payment_method"
SIGNAL_IP_24H = "ip_24h"
SIGNAL_DEVICE_FINGERPRINT = "device_fingerprint"

_PAID_TIERS = frozenset({"galactic_citizen", "region_owner"})
_ACTIVE_SUB_STATUSES = frozenset({"active", "ACTIVE"})


def _player_ids_all_paid(db: Session, player_ids: Sequence[uuid.UUID]) -> bool:
    if not player_ids:
        return False
    rows = (
        db.query(User.subscription_tier, User.subscription_status)
        .join(Player, Player.user_id == User.id)
        .filter(Player.id.in_(list(player_ids)))
        .all()
    )
    if len(rows) != len(set(player_ids)):
        return False
    for tier, status in rows:
        if (tier or "").lower() not in _PAID_TIERS:
            return False
        if (status or "").lower() not in {s.lower() for s in _ACTIVE_SUB_STATUSES}:
            return False
    return True


def _existing_overridden(
    db: Session, player_ids: Set[uuid.UUID], signal: str
) -> bool:
    """True if an OVERRIDDEN cluster already covers exactly these players for signal."""
    if len(player_ids) < 2:
        return False
    flags = (
        db.query(MultiAccountFlag)
        .join(MultiAccountCluster)
        .filter(
            MultiAccountFlag.signal == signal,
            MultiAccountCluster.admin_decision == MultiAccountAdminDecision.OVERRIDDEN,
            MultiAccountFlag.player_id.in_(list(player_ids)),
        )
        .all()
    )
    by_cluster: Dict[uuid.UUID, Set[uuid.UUID]] = defaultdict(set)
    for f in flags:
        by_cluster[f.cluster_id].add(f.player_id)
    return any(members == player_ids for members in by_cluster.values())


def _existing_open_cluster(
    db: Session, player_ids: Set[uuid.UUID], signal: str
) -> Optional[MultiAccountCluster]:
    """Return a non-overridden cluster that already flags exactly these players."""
    flags = (
        db.query(MultiAccountFlag)
        .join(MultiAccountCluster)
        .filter(
            MultiAccountFlag.signal == signal,
            MultiAccountCluster.admin_decision != MultiAccountAdminDecision.OVERRIDDEN,
            MultiAccountFlag.player_id.in_(list(player_ids)),
        )
        .all()
    )
    by_cluster: Dict[uuid.UUID, List[MultiAccountFlag]] = defaultdict(list)
    for f in flags:
        by_cluster[f.cluster_id].append(f)
    for cluster_id, flist in by_cluster.items():
        members = {f.player_id for f in flist}
        if members == player_ids:
            return db.query(MultiAccountCluster).filter_by(id=cluster_id).first()
    return None


def upsert_cluster(
    db: Session,
    *,
    player_ids: Iterable[uuid.UUID],
    signal: str,
    severity: MultiAccountSeverity,
    evidence: Dict[str, Any],
) -> Optional[MultiAccountCluster]:
    """Create or refresh a cluster for ``player_ids`` under ``signal``.

    Returns None when the set is too small, already OVERRIDDEN, or unchanged.
    """
    ids = {pid for pid in player_ids if pid is not None}
    if len(ids) < 2:
        return None
    if _existing_overridden(db, ids, signal):
        return None
    existing = _existing_open_cluster(db, ids, signal)
    if existing is not None:
        # Refresh cached all-paid + evidence; keep admin_decision intact.
        existing.all_paid_subscribers = _player_ids_all_paid(db, list(ids))
        summary = dict(existing.signal_summary or {})
        summary["evidence"] = evidence
        bucket = "hard" if severity == MultiAccountSeverity.HARD else "soft"
        signals = list(summary.get(bucket) or [])
        if signal not in signals:
            signals.append(signal)
        summary[bucket] = signals
        existing.signal_summary = summary
        if severity == MultiAccountSeverity.HARD and existing.severity != MultiAccountSeverity.HARD:
            existing.severity = MultiAccountSeverity.HARD
        return existing

    hard = [signal] if severity == MultiAccountSeverity.HARD else []
    soft = [signal] if severity == MultiAccountSeverity.SOFT else []
    cluster = MultiAccountCluster(
        signal_summary={"hard": hard, "soft": soft, "evidence": evidence},
        severity=severity,
        all_paid_subscribers=_player_ids_all_paid(db, list(ids)),
        admin_decision=MultiAccountAdminDecision.PENDING,
    )
    db.add(cluster)
    db.flush()
    for pid in ids:
        db.add(
            MultiAccountFlag(
                player_id=pid,
                cluster_id=cluster.id,
                signal=signal,
                severity=severity,
            )
        )
    return cluster


def _clusters_shared_paypal(db: Session) -> List[Tuple[Set[uuid.UUID], Dict[str, Any]]]:
    """HARD: ≥2 players whose users share the same non-empty paypal_subscription_id."""
    rows = (
        db.query(User.paypal_subscription_id, Player.id)
        .join(Player, Player.user_id == User.id)
        .filter(
            User.paypal_subscription_id.isnot(None),
            User.paypal_subscription_id != "",
            Player.is_active.is_(True),
        )
        .all()
    )
    by_sub: Dict[str, Set[uuid.UUID]] = defaultdict(set)
    for sub_id, player_id in rows:
        by_sub[str(sub_id)].add(player_id)
    out: List[Tuple[Set[uuid.UUID], Dict[str, Any]]] = []
    for sub_id, members in by_sub.items():
        if len(members) >= 2:
            out.append(
                (
                    members,
                    {
                        "paypal_subscription_id_hash": sub_id[:8] + "…",
                        "member_count": len(members),
                    },
                )
            )
    return out


def _clusters_shared_ip_24h(
    db: Session, *, now: datetime
) -> List[Tuple[Set[uuid.UUID], Dict[str, Any]]]:
    """SOFT: ≥2 distinct players with a PlayerSession on the same IP in the last 24h."""
    cutoff = now - timedelta(hours=24)
    rows = (
        db.query(PlayerSession.ip_address, PlayerSession.player_id)
        .filter(
            PlayerSession.start_time >= cutoff,
            PlayerSession.ip_address.isnot(None),
            PlayerSession.ip_address != "",
        )
        .all()
    )
    by_ip: Dict[str, Set[uuid.UUID]] = defaultdict(set)
    for ip, player_id in rows:
        by_ip[str(ip)].add(player_id)
    out: List[Tuple[Set[uuid.UUID], Dict[str, Any]]] = []
    for ip, members in by_ip.items():
        if len(members) >= 2:
            out.append(
                (
                    members,
                    {"ip_address": ip, "window_hours": 24, "member_count": len(members)},
                )
            )
    return out


def _clusters_shared_device_fingerprint(
    db: Session,
) -> List[Tuple[Set[uuid.UUID], Dict[str, Any]]]:
    """SOFT: ≥2 players who redeemed invites with the same device_fingerprint_hash."""
    rows = (
        db.query(RegionInviteRedemption.device_fingerprint_hash, RegionInviteRedemption.redeemed_by_player_id)
        .filter(
            RegionInviteRedemption.device_fingerprint_hash.isnot(None),
            RegionInviteRedemption.device_fingerprint_hash != "",
            RegionInviteRedemption.redeemed_by_player_id.isnot(None),
        )
        .all()
    )
    by_fp: Dict[str, Set[uuid.UUID]] = defaultdict(set)
    for fp, player_id in rows:
        by_fp[str(fp)].add(player_id)
    out: List[Tuple[Set[uuid.UUID], Dict[str, Any]]] = []
    for fp, members in by_fp.items():
        if len(members) >= 2:
            out.append(
                (
                    members,
                    {
                        "device_fingerprint_hash_prefix": fp[:12] + "…",
                        "member_count": len(members),
                    },
                )
            )
    return out


def run_detection_sweep(db: Session, *, now: Optional[datetime] = None) -> Dict[str, int]:
    """Score hard+soft signals and upsert clusters/flags. Caller owns commit."""
    now = now or datetime.now(UTC)
    created = 0
    refreshed = 0
    hard_groups = 0
    soft_groups = 0

    for members, evidence in _clusters_shared_paypal(db):
        hard_groups += 1
        before = _existing_open_cluster(db, members, SIGNAL_PAYMENT_METHOD)
        cluster = upsert_cluster(
            db,
            player_ids=members,
            signal=SIGNAL_PAYMENT_METHOD,
            severity=MultiAccountSeverity.HARD,
            evidence=evidence,
        )
        if cluster is None:
            continue
        if before is None:
            created += 1
        else:
            refreshed += 1

    for members, evidence in _clusters_shared_ip_24h(db, now=now):
        soft_groups += 1
        before = _existing_open_cluster(db, members, SIGNAL_IP_24H)
        cluster = upsert_cluster(
            db,
            player_ids=members,
            signal=SIGNAL_IP_24H,
            severity=MultiAccountSeverity.SOFT,
            evidence=evidence,
        )
        if cluster is None:
            continue
        if before is None:
            created += 1
        else:
            refreshed += 1

    for members, evidence in _clusters_shared_device_fingerprint(db):
        soft_groups += 1
        before = _existing_open_cluster(db, members, SIGNAL_DEVICE_FINGERPRINT)
        cluster = upsert_cluster(
            db,
            player_ids=members,
            signal=SIGNAL_DEVICE_FINGERPRINT,
            severity=MultiAccountSeverity.SOFT,
            evidence=evidence,
        )
        if cluster is None:
            continue
        if before is None:
            created += 1
        else:
            refreshed += 1

    return {
        "clusters_created": created,
        "clusters_refreshed": refreshed,
        "hard_signal_groups": hard_groups,
        "soft_signal_groups": soft_groups,
    }


# Alias matching the target class name in OPERATIONS/multi-account-detection.md
class MultiAccountDetectionService:
    """Thin OOP façade over the module-level sweep helpers."""

    def __init__(self, db: Session):
        self.db = db

    def run_sweep(self, *, now: Optional[datetime] = None) -> Dict[str, int]:
        return run_detection_sweep(self.db, now=now)
