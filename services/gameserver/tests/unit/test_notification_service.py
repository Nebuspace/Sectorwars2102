"""Unit tests — notification_service.py (priority-driven messaging fan-out,
sw2102-docs/FEATURES/gameplay/messaging.md "Priority levels" / "Source map").

No test file existed for this service. DB-free throughout: `delivery_
surfaces_for` and `NotificationService.build_frame` are pure functions;
`notify_new_message` is exercised via a purpose-built `_FakeDb` (a single
FIFO queue for the one query shape each branch issues: a scalar `Player`
lookup for a direct message, a list `Player` query for a team broadcast)
and a `_FakeManager` recording every `send_personal_message` call.

Sections:
  TestDeliverySurfacesFor — the priority -> surface-list canon mapping,
    incl. the non-admin urgent-downgrades-to-high rule.
  TestBuildFrame — the new_message WS frame shape, incl. content-preview
    truncation and a None sent_at/content.
  TestNotifyNewMessageDirect — recipient lookup, dispatch, the no-user_id
    warning path, and delivery-exception swallowing (never raises).
  TestNotifyNewMessageTeam — the exclude-sender/inactive-filtered broadcast,
    partial-dispatch counting, and neither-recipient-nor-team no-op.
"""

from uuid import uuid4

import pytest

from src.models.message import Message
from src.models.player import Player
from src.services import notification_service
from src.services.notification_service import NotificationDispatchResult, NotificationService, delivery_surfaces_for


@pytest.fixture(autouse=True)
def _stub_count_earned_medals(monkeypatch):
    """notify_new_message counts sender medals; keep notify tests DB-free."""
    monkeypatch.setattr(
        "src.services.medal_service.count_earned_medals",
        lambda _db, _player_id: 0,
    )


class _FakeQuery:
    def __init__(self, value):
        self._value = value

    def filter(self, *_args, **_kwargs):
        return self

    def first(self):
        return self._value

    def all(self):
        return self._value if self._value is not None else []


class _FakeDb:
    def __init__(self, results=None):
        self._queues = {k: list(v) for k, v in (results or {}).items()}

    def query(self, model):
        queue = self._queues.get(model, [])
        value = queue.pop(0) if queue else None
        return _FakeQuery(value)


class _FakeManager:
    def __init__(self, raise_on=None):
        self.sent = []
        self._raise_on = raise_on

    async def send_personal_message(self, user_id, frame):
        if self._raise_on is not None and user_id == self._raise_on:
            raise RuntimeError("boom")
        self.sent.append((user_id, frame))


class _User:
    def __init__(self, is_admin=False):
        self.is_admin = is_admin


def _player(
    id=None,
    user_id=None,
    nickname="Nova",
    team_id=None,
    is_active=True,
    user=None,
    pinned_medal_id=None,
    show_count_publicly=True,
):
    p = Player()
    p.id = id or uuid4()
    p.user_id = user_id
    p.nickname = nickname
    p.team_id = team_id
    p.is_active = is_active
    privacy = {}
    if pinned_medal_id is not None:
        privacy["pinned_medal_id"] = pinned_medal_id
    if show_count_publicly is not False:
        privacy["show_count_publicly"] = True
    else:
        privacy["show_count_publicly"] = False
    if privacy:
        p.settings = {"medal_privacy": privacy}
    # Bypass the mapped relationship's instrumented setter (it expects a
    # mapped instance) -- write straight into the instance dict so a plain
    # _User stand-in is readable back via getattr(sender, "user", None).
    p.__dict__["user"] = user
    return p


def _message(id=None, sender_id=None, recipient_id=None, team_id=None,
             content="hello", priority="normal", sent_at=None):
    m = Message()
    m.id = id or uuid4()
    m.sender_id = sender_id or uuid4()
    m.recipient_id = recipient_id
    m.team_id = team_id
    m.content = content
    m.priority = priority
    m.sent_at = sent_at
    return m


class TestDeliverySurfacesFor:
    def test_low_is_inbox_only(self):
        assert delivery_surfaces_for("low", sender_is_admin=False) == ["inbox"]

    def test_normal_is_inbox_and_toast(self):
        assert delivery_surfaces_for("normal", sender_is_admin=False) == ["inbox", "toast"]

    def test_high_adds_push(self):
        assert delivery_surfaces_for("high", sender_is_admin=False) == ["inbox", "toast", "push"]

    def test_admin_urgent_adds_modal(self):
        assert delivery_surfaces_for("urgent", sender_is_admin=True) == [
            "inbox", "toast", "push", "modal",
        ]

    def test_non_admin_urgent_downgrades_to_high(self):
        assert delivery_surfaces_for("urgent", sender_is_admin=False) == [
            "inbox", "toast", "push",
        ]

    def test_none_priority_defaults_to_normal(self):
        assert delivery_surfaces_for(None, sender_is_admin=False) == ["inbox", "toast"]

    def test_unrecognized_priority_defaults_to_normal(self):
        assert delivery_surfaces_for("critical", sender_is_admin=True) == ["inbox", "toast"]

    def test_priority_is_case_insensitive(self):
        assert delivery_surfaces_for("HIGH", sender_is_admin=False) == ["inbox", "toast", "push"]

    def test_returned_list_is_a_fresh_copy(self):
        first = delivery_surfaces_for("low", sender_is_admin=False)
        first.append("mutated")
        assert delivery_surfaces_for("low", sender_is_admin=False) == ["inbox"]


class TestBuildFrame:
    def test_shape_and_fields(self):
        sender = _player(nickname="Vex")
        msg = _message(content="hi there", priority="high")
        frame = NotificationService.build_frame(msg, sender, ["inbox", "toast", "push"])
        assert frame["type"] == "new_message"
        assert frame["message_id"] == str(msg.id)
        assert frame["sender_id"] == str(msg.sender_id)
        assert frame["sender_name"] == "Vex"
        assert frame["preview"] == "hi there"
        assert frame["sent_at"] is None
        assert frame["priority"] == "high"
        assert frame["delivery"] == ["inbox", "toast", "push"]
        assert frame["sender_pinned_medal_id"] is None
        assert frame["sender_medal_count"] is None

    def test_sender_medal_fields_on_frame(self):
        sender = _player(pinned_medal_id="bronze_cluster")
        msg = _message()
        frame = NotificationService.build_frame(
            msg,
            sender,
            ["inbox"],
            sender_pinned_medal_id="bronze_cluster",
            sender_medal_count=3,
        )
        assert frame["sender_pinned_medal_id"] == "bronze_cluster"
        assert frame["sender_medal_count"] == 3

    def test_sender_medal_count_hidden_when_privacy_disabled(self):
        sender = _player(pinned_medal_id="silver_star", show_count_publicly=False)
        msg = _message()
        frame = NotificationService.build_frame(
            msg,
            sender,
            ["inbox"],
            sender_pinned_medal_id="silver_star",
            sender_medal_count=None,
        )
        assert frame["sender_pinned_medal_id"] == "silver_star"
        assert frame["sender_medal_count"] is None

    def test_preview_truncates_to_100_chars(self):
        sender = _player()
        msg = _message(content="x" * 250)
        frame = NotificationService.build_frame(msg, sender, ["inbox"])
        assert frame["preview"] == "x" * 100

    def test_empty_content_yields_empty_preview(self):
        sender = _player()
        msg = _message(content="")
        frame = NotificationService.build_frame(msg, sender, ["inbox"])
        assert frame["preview"] == ""

    def test_sent_at_is_isoformatted_when_present(self):
        from datetime import UTC, datetime

        sender = _player()
        ts = datetime(2026, 8, 9, 12, 0, 0, tzinfo=UTC)
        msg = _message(sent_at=ts)
        frame = NotificationService.build_frame(msg, sender, ["inbox"])
        assert frame["sent_at"] == ts.isoformat()


class TestNotifyNewMessageDirect:
    @pytest.mark.asyncio
    async def test_dispatches_to_recipient_with_a_user_id(self):
        recipient = _player(user_id=uuid4())
        sender = _player()
        msg = _message(recipient_id=recipient.id, priority="normal")
        db = _FakeDb(results={Player: [recipient]})
        manager = _FakeManager()

        result = await NotificationService.notify_new_message(db, msg, sender, manager)

        assert len(manager.sent) == 1
        sent_user_id, frame = manager.sent[0]
        assert sent_user_id == str(recipient.user_id)
        assert frame["delivery"] == ["inbox", "toast"]

    @pytest.mark.asyncio
    async def test_dispatched_frame_includes_sender_medal_fields(self):
        recipient = _player(user_id=uuid4())
        sender = _player(pinned_medal_id="bronze_cluster")
        msg = _message(recipient_id=recipient.id)
        db = _FakeDb(results={Player: [recipient]})
        manager = _FakeManager()

        await NotificationService.notify_new_message(db, msg, sender, manager)

        _, frame = manager.sent[0]
        assert frame["sender_pinned_medal_id"] == "bronze_cluster"
        assert frame["sender_medal_count"] == 0

    @pytest.mark.asyncio
    async def test_recipient_with_no_user_id_skips_dispatch(self):
        recipient = _player(user_id=None)
        sender = _player()
        msg = _message(recipient_id=recipient.id)
        db = _FakeDb(results={Player: [recipient]})
        manager = _FakeManager()

        await NotificationService.notify_new_message(db, msg, sender, manager)

        assert manager.sent == []

    @pytest.mark.asyncio
    async def test_missing_recipient_row_skips_dispatch(self):
        sender = _player()
        msg = _message(recipient_id=uuid4())
        db = _FakeDb(results={Player: [None]})
        manager = _FakeManager()

        await NotificationService.notify_new_message(db, msg, sender, manager)

        assert manager.sent == []

    @pytest.mark.asyncio
    async def test_admin_sender_urgent_message_earns_modal(self):
        recipient = _player(user_id=uuid4())
        sender = _player(user=_User(is_admin=True))
        msg = _message(recipient_id=recipient.id, priority="urgent")
        db = _FakeDb(results={Player: [recipient]})
        manager = _FakeManager()

        await NotificationService.notify_new_message(db, msg, sender, manager)

        _, frame = manager.sent[0]
        assert frame["delivery"] == ["inbox", "toast", "push", "modal"]

    @pytest.mark.asyncio
    async def test_high_priority_earns_push_transport_warning(self):
        recipient = _player(user_id=uuid4())
        sender = _player()
        msg = _message(recipient_id=recipient.id, priority="high")
        db = _FakeDb(results={Player: [recipient]})
        manager = _FakeManager()

        result = await NotificationService.notify_new_message(db, msg, sender, manager)

        assert result.live_dispatched is True
        assert any(w["code"] == "push_transport_unavailable" for w in result.warnings)

    @pytest.mark.asyncio
    async def test_non_admin_sender_urgent_message_has_no_modal(self):
        recipient = _player(user_id=uuid4())
        sender = _player(user=_User(is_admin=False))
        msg = _message(recipient_id=recipient.id, priority="urgent")
        db = _FakeDb(results={Player: [recipient]})
        manager = _FakeManager()

        await NotificationService.notify_new_message(db, msg, sender, manager)

        _, frame = manager.sent[0]
        assert frame["delivery"] == ["inbox", "toast", "push"]

    @pytest.mark.asyncio
    async def test_sender_with_no_user_relationship_is_treated_as_non_admin(self):
        recipient = _player(user_id=uuid4())
        sender = _player(user=None)
        msg = _message(recipient_id=recipient.id, priority="urgent")
        db = _FakeDb(results={Player: [recipient]})
        manager = _FakeManager()

        await NotificationService.notify_new_message(db, msg, sender, manager)

        _, frame = manager.sent[0]
        assert frame["delivery"] == ["inbox", "toast", "push"]

    @pytest.mark.asyncio
    async def test_medal_enrichment_failure_degrades_without_error(self, monkeypatch):
        recipient = _player(user_id=uuid4())
        sender = _player()
        msg = _message(recipient_id=recipient.id, priority="normal")
        db = _FakeDb(results={Player: [recipient]})
        manager = _FakeManager()

        def _boom(_db, _player_id):
            raise RuntimeError("secret-medal-db-should-not-500-send")

        monkeypatch.setattr(
            "src.services.medal_service.count_earned_medals",
            _boom,
        )

        result = await NotificationService.notify_new_message(db, msg, sender, manager)

        assert result.failed is False
        assert result.live_dispatched is True
        _, frame = manager.sent[0]
        assert frame["sender_pinned_medal_id"] is None
        assert frame["sender_medal_count"] is None

    @pytest.mark.asyncio
    async def test_delivery_failure_returns_structured_result_not_raised(self):
        recipient = _player(user_id=uuid4())
        sender = _player()
        msg = _message(recipient_id=recipient.id)
        db = _FakeDb(results={Player: [recipient]})
        manager = _FakeManager(raise_on=str(recipient.user_id))

        result = await NotificationService.notify_new_message(db, msg, sender, manager)

        assert result.failed is True
        assert result.error_code == "live_notification_failed"
        assert "saved to inbox" in result.error_message
        assert manager.sent == []


class TestNotifyNewMessageTeam:
    @pytest.mark.asyncio
    async def test_dispatches_to_every_addressable_member(self):
        team_id = uuid4()
        sender = _player()
        m1 = _player(user_id=uuid4())
        m2 = _player(user_id=uuid4())
        msg = _message(sender_id=sender.id, team_id=team_id, priority="low")
        db = _FakeDb(results={Player: [[m1, m2]]})
        manager = _FakeManager()

        await NotificationService.notify_new_message(db, msg, sender, manager)

        dispatched_ids = {uid for uid, _frame in manager.sent}
        assert dispatched_ids == {str(m1.user_id), str(m2.user_id)}

    @pytest.mark.asyncio
    async def test_member_with_no_user_id_is_skipped_not_fatal(self):
        team_id = uuid4()
        sender = _player()
        addressable = _player(user_id=uuid4())
        unaddressable = _player(user_id=None)
        msg = _message(sender_id=sender.id, team_id=team_id)
        db = _FakeDb(results={Player: [[addressable, unaddressable]]})
        manager = _FakeManager()

        result = await NotificationService.notify_new_message(db, msg, sender, manager)

        assert len(manager.sent) == 1
        assert manager.sent[0][0] == str(addressable.user_id)

    @pytest.mark.asyncio
    async def test_empty_member_list_dispatches_nothing(self):
        team_id = uuid4()
        sender = _player()
        msg = _message(sender_id=sender.id, team_id=team_id)
        db = _FakeDb(results={Player: [[]]})
        manager = _FakeManager()

        await NotificationService.notify_new_message(db, msg, sender, manager)

        assert manager.sent == []

    @pytest.mark.asyncio
    async def test_neither_recipient_nor_team_dispatches_nothing(self):
        sender = _player()
        msg = _message(recipient_id=None, team_id=None)
        db = _FakeDb()
        manager = _FakeManager()

        await NotificationService.notify_new_message(db, msg, sender, manager)

        assert manager.sent == []

    @pytest.mark.asyncio
    async def test_delivery_failure_mid_broadcast_returns_structured_result(self):
        team_id = uuid4()
        sender = _player()
        m1 = _player(user_id=uuid4())
        msg = _message(sender_id=sender.id, team_id=team_id)
        db = _FakeDb(results={Player: [[m1]]})
        manager = _FakeManager(raise_on=str(m1.user_id))

        result = await NotificationService.notify_new_message(db, msg, sender, manager)

        assert result.failed is True
        assert result.error_code == "live_notification_failed"

    @pytest.mark.asyncio
    async def test_recipient_id_takes_precedence_over_team_id(self):
        recipient = _player(user_id=uuid4())
        sender = _player()
        msg = _message(recipient_id=recipient.id, team_id=uuid4())
        db = _FakeDb(results={Player: [recipient]})
        manager = _FakeManager()

        result = await NotificationService.notify_new_message(db, msg, sender, manager)

        assert len(manager.sent) == 1
        assert manager.sent[0][0] == str(recipient.user_id)


def test_module_import_smoke():
    assert notification_service.NotificationService is NotificationService
