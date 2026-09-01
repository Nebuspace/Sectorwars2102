"""
Notification fan-out service — priority-driven delivery for player messaging.

This is the module the messaging canon names as the priority-driven fan-out
point (sw2102-docs/FEATURES/gameplay/messaging.md → "Source map":
`services/gameserver/src/services/notification_service.py integrating with the
realtime bus`). It owns the mapping from a message's `priority` to the set of
delivery surfaces, and dispatches the live WebSocket frame accordingly.

Canon priority → delivery (messaging.md "Priority levels", lines 53–58):

    | Priority | Behavior                                                    |
    |----------|-------------------------------------------------------------|
    | low      | Inbox only — no notification toast or push.                 |
    | normal   | Inbox + in-game notification toast on arrival.              |
    | high     | Inbox + toast + push notification (mobile/desktop) if the   |
    |          | recipient is offline.                                       |
    | urgent   | Inbox + toast + push + interrupts the recipient's current   |
    |          | action with a modal (admin-only — players can't send        |
    |          | `urgent`).                                                   |

The live WebSocket frame carries a `delivery` list naming the surfaces the
client should activate (`inbox`, `toast`, `push`, `modal`). The client (a) ALWAYS
refreshes its inbox + unread badge from any `new_message` frame, and (b) varies
the in-cockpit surface (silent / toast / modal) off `delivery`. Sending the frame
even for `low` keeps the unread badge live without a toast — "inbox only" means
no toast/modal, not "no realtime hint to refresh".

PARKED (not implemented here — flagged to the Orchestrator):
  * `push` (offline mobile/desktop push) is infrastructure that does not exist
    anywhere in the stack (no service worker, Web Push, or push-token store).
    `high`/`urgent` still EARN the `push` surface in their delivery list so the
    canon mapping is honest and a future push transport can act on it, but no
    push is actually dispatched. This service never claims a push was sent.

This service routes exclusively through the EXISTING ConnectionManager helper
`send_personal_message` (websocket_service.py) — it does not touch the enhanced
websocket service (WO-B7's lane) and adds no new broadcast primitive.
"""

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from src.models.message import Message
from src.models.player import Player

logger = logging.getLogger(__name__)


class MessageDeliveryError(Exception):
    """Live notification could not be delivered after the message was persisted.

    Mapped by the messages route to a structured HTTP response (never a bare
    500 with raw exception text). ``message_id`` is always set — the inbox row
    already exists when this is raised.
    """

    def __init__(self, code: str, message: str, *, message_id: Any) -> None:
        self.code = code
        self.message = message
        self.message_id = message_id
        super().__init__(message)


@dataclass
class NotificationDispatchResult:
    """Outcome of the live notification fan-out for one persisted message."""

    live_dispatched: bool = False
    warnings: List[Dict[str, str]] = field(default_factory=list)
    error_code: Optional[str] = None
    error_message: Optional[str] = None

    @property
    def failed(self) -> bool:
        return self.error_code is not None


# Canon delivery surfaces per priority. Every recognized priority includes
# "inbox" (the persistent record is always written). The escalation ladder
# adds toast → push → modal. An unrecognized priority is treated as `normal`
# (validated at the route layer to one of the four, so this is defensive).
_DELIVERY_BY_PRIORITY: Dict[str, List[str]] = {
    "low": ["inbox"],
    "normal": ["inbox", "toast"],
    "high": ["inbox", "toast", "push"],
    "urgent": ["inbox", "toast", "push", "modal"],
}


def delivery_surfaces_for(priority: Optional[str], sender_is_admin: bool) -> List[str]:
    """Resolve the canon delivery-surface list for an effective priority.

    Canon: `urgent` is admin-only. We do not block a player from STORING an
    `urgent` message (that send-side write-gate is a route/security concern,
    parked), but the modal interrupt — the only surface that distinguishes
    `urgent` from `high` — is reserved for admin senders. A non-admin `urgent`
    message therefore delivers as `high` (toast + push), never a modal. This
    keeps a player from being able to forcibly interrupt another player's
    action while still honoring everything else in the mapping.
    """
    effective = (priority or "normal").lower()
    if effective == "urgent" and not sender_is_admin:
        effective = "high"
    return list(_DELIVERY_BY_PRIORITY.get(effective, _DELIVERY_BY_PRIORITY["normal"]))


class NotificationService:
    """Priority-driven fan-out for messaging notifications."""

    @staticmethod
    def build_frame(
        message: Message,
        sender: Player,
        delivery: List[str],
        *,
        sender_pinned_medal_id: Optional[str] = None,
        sender_medal_count: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Build the `new_message` WebSocket frame for a recipient.

        Shape is backward-compatible with the prior `_send_notification`
        payload; additions are `delivery` (surface list the client switches on)
        and sender medal identity (same field names as inbox REST enrich).
        """
        return {
            "type": "new_message",
            "message_id": str(message.id),
            "sender_id": str(message.sender_id),
            "sender_name": sender.nickname,
            "sender_pinned_medal_id": sender_pinned_medal_id,
            "sender_medal_count": sender_medal_count,
            "preview": message.content[:100] if message.content else "",
            "sent_at": message.sent_at.isoformat() if message.sent_at else None,
            "priority": message.priority,
            "delivery": delivery,
        }

    @staticmethod
    async def notify_new_message(
        db: Session,
        message: Message,
        sender: Player,
        manager: Any,
    ) -> NotificationDispatchResult:
        """Fan a freshly-persisted message out to its recipient(s) by priority.

        `manager` is the EXISTING ConnectionManager instance (keyed by USER id);
        we call only its public `send_personal_message` helper. Returns a
        structured ``NotificationDispatchResult`` so ``MessageService.send_message``
        can surface honest client-facing errors instead of opaque 500s. The
        inbox row is already committed when this runs — callers decide whether
        a failed live frame becomes an HTTP error or a success with warnings.
        """
        result = NotificationDispatchResult()
        sender_is_admin = bool(getattr(getattr(sender, "user", None), "is_admin", False))
        delivery = delivery_surfaces_for(message.priority, sender_is_admin)
        from src.services.medal_service import count_earned_medals, public_medal_identity

        pinned_medal_id: Optional[str] = None
        medal_count: Optional[int] = None
        try:
            medal_identity = public_medal_identity(
                sender, medal_count=count_earned_medals(db, sender.id)
            )
            pinned_medal_id = medal_identity["pinned_medal_id"]
            medal_count = medal_identity["medal_count"]
        except Exception as medal_error:  # noqa: BLE001 — degrade, never 500 the send
            logger.warning(
                "Message %s: sender medal enrichment failed; dispatching without "
                "medal fields: %s",
                message.id,
                medal_error,
            )

        frame = NotificationService.build_frame(
            message,
            sender,
            delivery,
            sender_pinned_medal_id=pinned_medal_id,
            sender_medal_count=medal_count,
        )

        # `push` is parked infrastructure — be explicit in the log that the
        # mapping earned it but no transport exists, rather than silently
        # implying delivery.
        if "push" in delivery:
            logger.debug(
                "Message %s priority=%s earns 'push' surface, but offline push "
                "transport is not implemented (parked) — WS frame only.",
                message.id, message.priority,
            )
            result.warnings.append(
                {
                    "code": "push_transport_unavailable",
                    "message": (
                        "Offline push transport is not implemented; message was "
                        "saved to inbox and live WebSocket notification was attempted."
                    ),
                }
            )

        try:
            if message.recipient_id:
                recipient = (
                    db.query(Player)
                    .filter(Player.id == message.recipient_id)
                    .first()
                )
                if recipient and recipient.user_id:
                    await manager.send_personal_message(str(recipient.user_id), frame)
                    result.live_dispatched = True
                else:
                    logger.warning(
                        "Message %s delivered to inbox but recipient %s has no "
                        "user_id — no live notification dispatched.",
                        message.id, message.recipient_id,
                    )
            elif message.team_id:
                # Team broadcast: every active member except the sender. A member
                # with no user_id can't be addressed on the connection map; log
                # it clearly rather than passing a None-stringified key.
                team_members = (
                    db.query(Player)
                    .filter(
                        Player.team_id == message.team_id,
                        Player.id != message.sender_id,
                        Player.is_active == True,  # noqa: E712 (SQLAlchemy boolean)
                    )
                    .all()
                )
                dispatched = 0
                for member in team_members:
                    if member.user_id:
                        await manager.send_personal_message(str(member.user_id), frame)
                        dispatched += 1
                    else:
                        logger.warning(
                            "Team message %s: member %s has no user_id — skipped.",
                            message.id, member.id,
                        )
                if dispatched:
                    result.live_dispatched = True
                logger.info(
                    "Team message %s (priority=%s) notification fanned out to %d "
                    "of %d members.",
                    message.id, message.priority, dispatched, len(team_members),
                )
        except Exception as notify_error:  # noqa: BLE001 — structured, not raw 500
            logger.warning(
                "Message %s delivered but live notification failed: %s",
                message.id, notify_error,
            )
            result.error_code = "live_notification_failed"
            result.error_message = (
                "Message was saved to inbox but the live notification could not "
                "be delivered."
            )

        return result
