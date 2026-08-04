"""Shared multi-account participation helpers (WO-P7-MULTIACCT-PARTICIPATION-WEIGHT).

Canon: ADR-0056 E-V5 / N-V3, DATA_MODELS/gameplay.md:178-194,
OPERATIONS/multi-account-detection.md discount math.

This module owns the shared ``participation_weight`` seam that gated surfaces
read. Detection / admin decision / soft+paid discount math are separate WOs —
today the shipped semantics match the beacon SOFT-DEP that already landed:
HARD-flagged cluster members weight 0.0; everyone else weights 1.0.
"""

from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from src.models.multi_account import MultiAccountFlag, MultiAccountSeverity


def participation_weight(db: Session, player_id: uuid.UUID) -> float:
    """Return the ADR-0056 E-V5 participation multiplier for ``player_id``.

    HARD-severity ``MultiAccountFlag`` → ``0.0``; otherwise ``1.0``. Soft /
    paid-tier discount math (0.5× / all-paid exemption) is a follow-up; this
    preserves the beacon SOFT-DEP semantics that already shipped.
    """
    flagged = (
        db.query(MultiAccountFlag)
        .filter(
            MultiAccountFlag.player_id == player_id,
            MultiAccountFlag.severity == MultiAccountSeverity.HARD,
        )
        .first()
    )
    return 0.0 if flagged is not None else 1.0


def eligible_for_contest(db: Session, player_id: uuid.UUID, planet_id: uuid.UUID) -> bool:
    """ADR-0091 §8 Amendment A: the ``settle_contest`` surface.

    Returns True iff ``player_id`` clears the anti-sybil thresholds to
    contest (settle) ``planet_id``. v1 reuses the existing HARD-flag
    detection substrate 1:1 with :func:`participation_weight` — a
    HARD-flagged cluster member is ineligible (False); everyone else is
    eligible (True). ``planet_id`` is accepted for API-shape parity with the
    ADR's per-planet-scoped surface and future refinement (e.g. per-planet
    soft-flag tie-loss / no-relief-between-linked-accounts math); unused
    today since the underlying detection is account-scoped, not planet-scoped.
    """
    return participation_weight(db, player_id) > 0.0


def blocks_vote(db: Session, player_id: uuid.UUID) -> bool:
    """ADR-0056 N-V3 ``not multi_account_flag.blocks_vote`` gate.

    True when ``participation_weight`` is 0 (HARD-flagged cluster member) —
    the franchise is blocked; Soft-flagged accounts are not blocked by this
    seam (weight stays 1.0 until soft discount math ships).
    """
    return participation_weight(db, player_id) == 0.0
