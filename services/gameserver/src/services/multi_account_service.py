"""Shared multi-account participation helpers (WO-P7-MULTIACCT-PARTICIPATION-WEIGHT).

Canon: ADR-0056 E-V5 / N-V3, DATA_MODELS/gameplay.md:178-194,
OPERATIONS/multi-account-detection.md Discount math (LEG-256).

This module owns the shared ``participation_weight`` seam that gated surfaces
read. Detection / admin decision live elsewhere; this seam applies the
documented discount ladder once flags exist.
"""

from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from src.models.multi_account import (
    MultiAccountCluster,
    MultiAccountFlag,
    MultiAccountSeverity,
)


def participation_weight(db: Session, player_id: uuid.UUID) -> float:
    """Return the ADR-0056 E-V5 participation multiplier for ``player_id``.

    Canon ladder (OPERATIONS/multi-account-detection.md §Discount math):

    1. No flag → ``1.0``
    2. Cluster ``all_paid_subscribers`` → ``1.0`` (paid-tier exemption)
    3. Most-severe HARD flag → ``0.0``
    4. Most-severe SOFT flag → ``0.5``
    """
    hard = (
        db.query(MultiAccountFlag)
        .filter(
            MultiAccountFlag.player_id == player_id,
            MultiAccountFlag.severity == MultiAccountSeverity.HARD,
        )
        .first()
    )
    soft = None
    if hard is None:
        soft = (
            db.query(MultiAccountFlag)
            .filter(
                MultiAccountFlag.player_id == player_id,
                MultiAccountFlag.severity == MultiAccountSeverity.SOFT,
            )
            .first()
        )
    flag = hard if hard is not None else soft
    if flag is None:
        return 1.0

    cluster_id = getattr(flag, "cluster_id", None)
    if cluster_id is not None:
        cluster = (
            db.query(MultiAccountCluster)
            .filter(MultiAccountCluster.id == cluster_id)
            .first()
        )
        if cluster is not None and bool(getattr(cluster, "all_paid_subscribers", False)):
            return 1.0

    if flag.severity == MultiAccountSeverity.HARD:
        return 0.0
    if flag.severity == MultiAccountSeverity.SOFT:
        return 0.5
    return 1.0


def eligible_for_contest(db: Session, player_id: uuid.UUID, planet_id: uuid.UUID) -> bool:
    """ADR-0091 §8 Amendment A: the ``settle_contest`` surface.

    Returns True iff ``player_id`` clears the anti-sybil thresholds to
    contest (settle) ``planet_id``. HARD (weight 0.0) is ineligible; SOFT
    (0.5) and clear (1.0) remain eligible. ``planet_id`` is accepted for
    API-shape parity with the ADR's per-planet-scoped surface; unused today
    since the underlying detection is account-scoped, not planet-scoped.
    """
    return participation_weight(db, player_id) > 0.0


def blocks_vote(db: Session, player_id: uuid.UUID) -> bool:
    """ADR-0056 N-V3 ``not multi_account_flag.blocks_vote`` gate.

    True when ``participation_weight`` is 0 (HARD-flagged, non-exempt
    cluster member) — the franchise is blocked. Soft-flagged accounts keep
    the franchise (weight 0.5 ≠ 0).
    """
    return participation_weight(db, player_id) == 0.0
