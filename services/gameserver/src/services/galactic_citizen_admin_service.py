"""Manual Galactic Citizen grant/revoke — admin support tooling (LEG-3611 / ADR-0115).

Outside the PayPal webhook lifecycle: comping affected players or clawing back
status (fraud/chargeback) without calling PayPal APIs.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from sqlalchemy.orm import Session

from src.models.player import Player
from src.models.user import User

_CITIZEN_TIER = "galactic_citizen"
_MANUAL_GRANT_STATUS = "manual_grant"
_MANUAL_REVOKE_STATUS = "manual_revoke"


@dataclass(frozen=True)
class GcMutationOutcome:
    changed: bool
    already_in_target_state: bool
    details: Dict[str, Any]


def _is_manual_citizen(db: Session, player: Player) -> bool:
    if not player.is_galactic_citizen:
        return False
    user = db.query(User).filter(User.id == player.user_id).first()
    return user is not None and user.subscription_tier == _CITIZEN_TIER


def manual_grant_galactic_citizen(
    db: Session,
    player: Player,
    *,
    reason: str,
) -> GcMutationOutcome:
    """Grant GC manually (idempotent). Does not set paypal_subscription_id."""
    user = db.query(User).filter(User.id == player.user_id).first()
    if user is None:
        raise ValueError("Associated user not found")

    if _is_manual_citizen(db, player):
        return GcMutationOutcome(
            changed=False,
            already_in_target_state=True,
            details={
                "reason": reason,
                "source": "admin_manual",
                "player_id": str(player.id),
                "idempotent": True,
            },
        )

    player.is_galactic_citizen = True
    player.gc_lapsed_at = None
    player.gc_relocation_used_at = None
    user.subscription_tier = _CITIZEN_TIER
    user.subscription_status = _MANUAL_GRANT_STATUS
    user.payment_failure_count = 0
    if user.subscription_started_at is None:
        user.subscription_started_at = datetime.now(timezone.utc).replace(tzinfo=None)

    return GcMutationOutcome(
        changed=True,
        already_in_target_state=False,
        details={
            "reason": reason,
            "source": "admin_manual",
            "player_id": str(player.id),
            "user_id": str(user.id),
            "paypal_subscription_id": user.paypal_subscription_id,
        },
    )


def manual_revoke_galactic_citizen(
    db: Session,
    player: Player,
    *,
    reason: str,
) -> GcMutationOutcome:
    """Revoke GC manually (idempotent). Does not cancel PayPal remotely."""
    user = db.query(User).filter(User.id == player.user_id).first()
    if user is None:
        raise ValueError("Associated user not found")

    if not player.is_galactic_citizen and user.subscription_tier != _CITIZEN_TIER:
        return GcMutationOutcome(
            changed=False,
            already_in_target_state=True,
            details={
                "reason": reason,
                "source": "admin_manual",
                "player_id": str(player.id),
                "idempotent": True,
            },
        )

    player.is_galactic_citizen = False
    player.gc_lapsed_at = None
    user.subscription_tier = None
    user.subscription_status = _MANUAL_REVOKE_STATUS
    user.payment_failure_count = 0

    return GcMutationOutcome(
        changed=True,
        already_in_target_state=False,
        details={
            "reason": reason,
            "source": "admin_manual",
            "player_id": str(player.id),
            "user_id": str(user.id),
            "paypal_subscription_id": user.paypal_subscription_id,
        },
    )
