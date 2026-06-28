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
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from src.models.message import Message
from src.models.player import Player

logger = logging.getLogger(__name__)


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
    def build_frame(message: Message, sender: Player, delivery: List[str]) -> Dict[str, Any]:
        """Build the `new_message` WebSocket frame for a recipient.

        Shape is backward-compatible with the prior `_send_notification`
        payload; the only addition is `delivery`, the surface list the client
        switches on (inbox-always, toast/modal conditional).
        """
        return {
            "type": "new_message",
            "message_id": str(message.id),
            "sender_id": str(message.sender_id),
            "sender_name": sender.nickname,
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
    ) -> None:
        """Fan a freshly-persisted message out to its recipient(s) by priority.

        `manager` is the EXISTING ConnectionManager instance (keyed by USER id);
        we call only its public `send_personal_message` helper. Delivery
        failures never raise — the message is already committed; a missed live
        frame is a degraded-but-acceptable outcome (the recipient still sees it
        in their inbox on next load).
        """
        sender_is_admin = bool(getattr(getattr(sender, "user", None), "is_admin", False))
        delivery = delivery_surfaces_for(message.priority, sender_is_admin)
        frame = NotificationService.build_frame(message, sender, delivery)

        # `push` is parked infrastructure — be explicit in the log that the
        # mapping earned it but no transport exists, rather than silently
        # implying delivery.
        if "push" in delivery:
            logger.debug(
                "Message %s priority=%s earns 'push' surface, but offline push "
                "transport is not implemented (parked) — WS frame only.",
                message.id, message.priority,
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
                logger.info(
                    "Team message %s (priority=%s) notification fanned out to %d "
                    "of %d members.",
                    message.id, message.priority, dispatched, len(team_members),
                )
        except Exception as notify_error:  # noqa: BLE001 — must not fail a committed send
            logger.warning(
                "Message %s delivered but live notification failed: %s",
                message.id, notify_error,
            )
