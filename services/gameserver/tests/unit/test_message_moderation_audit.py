"""Unit coverage for WO-RT-MOD-AUDIT-KERNEL.

Canon (messaging.md:115): "Moderated messages remain in the database for
the audit log even after content removal." Before this WO,
``MessageService.moderate_message``'s 'delete' branch did a hard
``db.delete(message)`` -- destroying the audit trail it had just stamped
``moderated_at``/``moderated_by`` onto. This file proves:

  1. the 'delete' action now sets ``moderation_status = 'deleted'`` on the
     row, the moderator stamps still land, and ``db.delete`` is never called
     (Accept #1-#2);
  2. every player-facing read in message_service.py (inbox, unread count,
     team messages, conversations/thread listing) excludes a
     moderation_status='deleted' row (Accept #3);
  3. the admin `/admin/messages/all` route (admin_messages.py, untouched by
     this WO) still surfaces a moderation_status='deleted' row -- full
     visibility is preserved there BY CONSTRUCTION, since that file carries
     no moderation_status filter (Accept #4);
  4. the 'flag'/'unflag' moderate_message branches are byte-unchanged
     (Accept #5);
  5. the migration is additive-only and reversible, chained onto the
     verified single head (Accept #6).

DB-free, per house convention (see test_route_runs_retention.py's
FakeRouteRunQuery / test_movement_drone_encounters.py's FakeDroneQuery):
``_eval_clause`` interprets the REAL SQLAlchemy ``and_``/``or_``/``==``/
``.is_(None)`` clauses the SUT builds, against real (transient, unpersisted)
``Message`` ORM instances held in a plain Python list -- so exclusion is
exercised for real, not merely asserted by inspection of call args. The
get_conversations subquery+join dance (group-by-thread / max(sent_at)) is
handled the same way: the fake ``.subquery()`` returns precomputed
per-thread group rows, and comparisons against its ``.c.<col>`` accessors
resolve through a small marker class rather than a real SQL subquery.
"""
from __future__ import annotations

import ast
import pathlib
import uuid
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from sqlalchemy.sql import operators
from sqlalchemy.sql.elements import BooleanClauseList, False_, Grouping, Null, True_

from src.models.message import Message
from src.models.player import Player
from src.models.team import Team
from src.models.team_member import TeamMember
from src.services.message_service import MessageService

# --------------------------------------------------------------------------- #
# Shared real-clause interpreter (DB-free row filtering)
# --------------------------------------------------------------------------- #

class _SubqueryColRef:
    """Marker returned by FakeGroupResult.c.<col> -- stands in for a real
    subquery Column. Comparing a real Message column against one of these
    (``Message.thread_id == latest_messages.c.thread_id``) produces a
    BindParameter whose .value IS this marker (SQLAlchemy just wraps an
    unrecognized right-hand operand as a bound literal), letting
    ``_eval_clause`` recognize and resolve it against ``group_lookup``."""

    def __init__(self, name: str) -> None:
        self.name = name


def _right_value(right):
    if isinstance(right, Null):
        return None
    if isinstance(right, False_):
        return False
    if isinstance(right, True_):
        return True
    return right.value  # BindParameter


def _eval_clause(row, clause, group_lookup=None) -> bool:
    if isinstance(clause, Grouping):
        # and_() parenthesizes a nested or_() (mixed-precedence) as a
        # Grouping wrapper -- unwrap to the real BooleanClauseList inside.
        clause = clause.element
    if isinstance(clause, BooleanClauseList):
        vals = [_eval_clause(row, c, group_lookup) for c in clause.clauses]
        return all(vals) if clause.operator is operators.and_ else any(vals)

    col = clause.left.key
    actual = getattr(row, col)
    raw_right = _right_value(clause.right)

    if isinstance(raw_right, _SubqueryColRef):
        grp = (group_lookup or {}).get(row.thread_id)
        if grp is None:
            return False
        raw_right = grp[raw_right.name]

    op = clause.operator
    if op is operators.eq:
        return actual == raw_right
    if op is operators.ne:
        return actual != raw_right
    if op is operators.is_:
        return actual is raw_right
    raise AssertionError(f"unhandled operator {op!r} on column {col!r}")


class FakeGroupResult:
    """Stands in for ``.subquery()``'s result on the
    ``(Message.thread_id, func.max(Message.sent_at).label('latest_sent'))``
    projection: exposes ``.c.thread_id`` / ``.c.latest_sent`` marker
    columns, and carries the precomputed per-thread group rows for the
    join step to consult."""

    def __init__(self, groups: dict) -> None:
        self.groups = groups
        self.c = SimpleNamespace(
            thread_id=_SubqueryColRef("thread_id"),
            latest_sent=_SubqueryColRef("latest_sent"),
        )


class FakeGroupQuery:
    """Stands in for ``db.query(Message.thread_id, func.max(...))`` --
    filter() records the real clause, group_by() is a no-op (grouping
    happens in subquery()), subquery() computes the real max(sent_at) per
    thread_id over the filtered rows."""

    def __init__(self, store: list) -> None:
        self._store = store
        self._conditions: tuple = ()

    def filter(self, *conditions):
        self._conditions = self._conditions + conditions
        return self

    def group_by(self, *cols):
        return self

    def subquery(self):
        matching = [r for r in self._store if all(_eval_clause(r, c) for c in self._conditions)]
        groups: dict = {}
        for r in matching:
            g = groups.setdefault(r.thread_id, {"thread_id": r.thread_id, "latest_sent": None})
            if g["latest_sent"] is None or r.sent_at > g["latest_sent"]:
                g["latest_sent"] = r.sent_at
        return FakeGroupResult(groups)


class FakeMessageQuery:
    """Stands in for ``db.query(Message)``: filter()/join()/options()/
    order_by()/limit()/offset()/count()/all() all operate on the REAL
    clauses the SUT builds, against the in-memory ``store``."""

    def __init__(self, store: list) -> None:
        self._store = store
        self._conditions: tuple = ()
        self._join_target = None
        self._join_condition = None
        self._order = None
        self._limit = None
        self._offset = None

    def filter(self, *conditions):
        self._conditions = self._conditions + conditions
        return self

    def join(self, target, condition):
        self._join_target = target
        self._join_condition = condition
        return self

    def options(self, *a, **k):
        return self

    def order_by(self, clause):
        self._order = clause
        return self

    def limit(self, n):
        self._limit = n
        return self

    def offset(self, n):
        self._offset = n
        return self

    def _matching(self):
        rows = self._store
        if self._join_target is not None:
            rows = [r for r in rows if _eval_clause(r, self._join_condition, self._join_target.groups)]
        return [r for r in rows if all(_eval_clause(r, c) for c in self._conditions)]

    def count(self):
        return len(self._matching())

    def all(self):
        rows = self._matching()
        if self._order is not None:
            key = self._order.element.key
            reverse = self._order.modifier is operators.desc_op
            rows = sorted(rows, key=lambda r: getattr(r, key), reverse=reverse)
        if self._offset:
            rows = rows[self._offset:]
        if self._limit is not None:
            rows = rows[: self._limit]
        return rows


class _FakeTeamQuery:
    def __init__(self, team):
        self._team = team

    def filter(self, *a, **k):
        return self

    def first(self):
        return self._team


class FakeDB:
    """Routes ``db.query(...)`` to the right fake based on the SUT's own
    call shape: ``Team`` -> membership lookup, ``Message`` (single arg) ->
    row-filtering query, multi-column projection -> the group/subquery
    builder used by get_conversations."""

    def __init__(self, messages=None, team=None):
        self.messages = messages or []
        self.team = team

    def query(self, *args):
        if len(args) == 1 and args[0] is Team:
            return _FakeTeamQuery(self.team)
        if len(args) == 1 and args[0] is Message:
            return FakeMessageQuery(self.messages)
        return FakeGroupQuery(self.messages)


class _PlayerLookupQuery:
    """Stands in for ``db.query(Player).filter(Player.id == msg.sender_id)
    .first()`` (teams.py's per-message sender lookup): resolves the REAL
    ``Player.id == <sender_id>`` clause against a small id->Player dict."""

    def __init__(self, players_by_id: dict) -> None:
        self._players_by_id = players_by_id
        self._conditions: tuple = ()

    def filter(self, *conditions):
        self._conditions = self._conditions + conditions
        return self

    def first(self):
        sender_id = _right_value(self._conditions[0].right)
        return self._players_by_id.get(sender_id)


class _TeamsRouteFakeDB:
    """Fake for teams.py's ``get_team_messages`` route: routes ``TeamMember``
    (TeamService.get_user_permissions' membership check), ``Message`` (the
    route's own direct query -- the thing this WO's follow-up patched), and
    ``Player`` (per-message sender-name lookup)."""

    def __init__(self, messages, team_member, players_by_id) -> None:
        self.messages = messages
        self.team_member = team_member
        self.players_by_id = players_by_id

    def query(self, *args):
        if len(args) == 1 and args[0] is TeamMember:
            return _FakeTeamQuery(self.team_member)  # filter().first() shape
        if len(args) == 1 and args[0] is Message:
            return FakeMessageQuery(self.messages)
        if len(args) == 1 and args[0] is Player:
            return _PlayerLookupQuery(self.players_by_id)
        raise AssertionError(f"unexpected query model(s) {args!r}")


def _msg(**kwargs) -> Message:
    """A real, transient (unpersisted) Message ORM instance -- to_dict()
    and relationship access (sender=None) both work without a session."""
    defaults = dict(
        id=uuid.uuid4(),
        sender_id=uuid.uuid4(),
        recipient_id=uuid.uuid4(),
        team_id=None,
        content="hi",
        sent_at=datetime(2026, 7, 8, 12, 0, 0),
        read_at=None,
        thread_id=uuid.uuid4(),
        deleted_by_sender=False,
        deleted_by_recipient=False,
        moderation_status=None,
        flagged=False,
        flagged_reason=None,
        moderated_at=None,
        moderated_by=None,
    )
    defaults.update(kwargs)
    return Message(**defaults)


def _run(coro):
    import asyncio

    return asyncio.run(coro)


# --------------------------------------------------------------------------- #
# (1) moderate_message: 'delete' soft-deletes, audit stamps land, no
#     db.delete
# --------------------------------------------------------------------------- #

def _first_mock(value):
    q = MagicMock()
    q.filter.return_value.first.return_value = value
    return q


def _make_db(*query_results):
    db = MagicMock()
    db.query.side_effect = list(query_results)
    return db


@pytest.mark.asyncio
async def test_moderate_delete_sets_status_and_stamps_no_hard_delete():
    message_id, moderator_id = uuid.uuid4(), uuid.uuid4()
    message_obj = SimpleNamespace(
        moderation_status=None, moderated_at=None, moderated_by=None,
        flagged=True, flagged_reason="spam",
    )
    db = _make_db(_first_mock(message_obj))

    result = await MessageService.moderate_message(
        db, message_id=message_id, action="delete", moderator_id=moderator_id,
    )

    assert result is True
    assert message_obj.moderation_status == "deleted"
    assert message_obj.moderated_at is not None
    assert message_obj.moderated_by == moderator_id
    db.delete.assert_not_called()
    db.commit.assert_called_once()


@pytest.mark.asyncio
async def test_moderate_delete_not_found_returns_false_untouched():
    db = _make_db(_first_mock(None))
    result = await MessageService.moderate_message(
        db, message_id=uuid.uuid4(), action="delete", moderator_id=uuid.uuid4(),
    )
    assert result is False
    db.delete.assert_not_called()
    db.commit.assert_not_called()


# --------------------------------------------------------------------------- #
# (2) moderate_message: 'flag' / 'unflag' branches byte-unchanged
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_moderate_unflag_clears_flag_untouched_status():
    message_id, moderator_id = uuid.uuid4(), uuid.uuid4()
    message_obj = SimpleNamespace(
        moderation_status=None, moderated_at=None, moderated_by=None,
        flagged=True, flagged_reason="spam",
    )
    db = _make_db(_first_mock(message_obj))

    result = await MessageService.moderate_message(
        db, message_id=message_id, action="unflag", moderator_id=moderator_id,
    )

    assert result is True
    assert message_obj.flagged is False
    assert message_obj.flagged_reason is None
    assert message_obj.moderation_status is None  # untouched by unflag
    db.delete.assert_not_called()


@pytest.mark.asyncio
async def test_moderate_flag_sets_flag_untouched_status():
    message_id, moderator_id = uuid.uuid4(), uuid.uuid4()
    message_obj = SimpleNamespace(
        moderation_status=None, moderated_at=None, moderated_by=None,
        flagged=False, flagged_reason=None,
    )
    db = _make_db(_first_mock(message_obj))

    result = await MessageService.moderate_message(
        db, message_id=message_id, action="flag", moderator_id=moderator_id, reason="rule_break",
    )

    assert result is True
    assert message_obj.flagged is True
    assert message_obj.flagged_reason == "rule_break"
    assert message_obj.moderation_status is None  # untouched by flag
    db.delete.assert_not_called()


# --------------------------------------------------------------------------- #
# (3) get_inbox / unread_count exclude a moderation_status='deleted' row
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_inbox_excludes_moderated_deleted_message():
    player_id = uuid.uuid4()
    visible = _msg(recipient_id=player_id, read_at=None)
    deleted = _msg(recipient_id=player_id, read_at=None, moderation_status="deleted")
    db = FakeDB(messages=[visible, deleted])

    result = await MessageService.get_inbox(db, player_id=player_id)

    ids = {m["id"] for m in result["messages"]}
    assert str(visible.id) in ids
    assert str(deleted.id) not in ids
    assert result["total"] == 1
    assert result["unread_count"] == 1  # the deleted one must not inflate this


@pytest.mark.asyncio
async def test_inbox_unread_only_still_excludes_moderated_deleted():
    player_id = uuid.uuid4()
    deleted_unread = _msg(recipient_id=player_id, read_at=None, moderation_status="deleted")
    db = FakeDB(messages=[deleted_unread])

    result = await MessageService.get_inbox(db, player_id=player_id, unread_only=True)

    assert result["messages"] == []
    assert result["total"] == 0
    assert result["unread_count"] == 0


# --------------------------------------------------------------------------- #
# (4) get_team_messages excludes a moderation_status='deleted' row
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_team_messages_excludes_moderated_deleted_message():
    player_id, team_id = uuid.uuid4(), uuid.uuid4()
    team = SimpleNamespace(members=[SimpleNamespace(id=player_id)])
    visible = _msg(team_id=team_id, sender_id=player_id)
    deleted = _msg(team_id=team_id, sender_id=player_id, moderation_status="deleted")
    db = FakeDB(messages=[visible, deleted], team=team)

    result = await MessageService.get_team_messages(db, player_id=player_id, team_id=team_id)

    ids = {m["id"] for m in result["messages"]}
    assert str(visible.id) in ids
    assert str(deleted.id) not in ids
    assert result["total"] == 1


# --------------------------------------------------------------------------- #
# (4b) teams.py's OWN direct route query (api/routes/teams.py:697,
#      get_team_messages) bypasses MessageService.get_team_messages entirely
#      with its own db.query(Message) -- a pre-existing duplicate the
#      original WO's Concern #1 flagged as a live leak. Direct-call pattern
#      (mirrors the admin/all pin above): invoke the route coroutine itself.
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_teams_route_direct_query_excludes_moderated_deleted_message():
    from src.api.routes.teams import get_team_messages as teams_route_get_team_messages

    team_id, player_id, sender_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    team_member = SimpleNamespace(
        can_invite=False, can_kick=False, can_manage_treasury=False,
        can_manage_missions=False, can_manage_alliances=False, role="member",
    )
    visible = _msg(team_id=team_id, sender_id=sender_id, subject="Visible", priority="normal")
    deleted = _msg(
        team_id=team_id, sender_id=sender_id, subject="Deleted",
        priority="normal", moderation_status="deleted",
    )
    sender = SimpleNamespace(nickname="Trader")
    db = _TeamsRouteFakeDB(
        messages=[visible, deleted],
        team_member=team_member,
        players_by_id={sender_id: sender},
    )
    player = SimpleNamespace(id=player_id)

    result = await teams_route_get_team_messages(
        team_id=team_id, skip=0, limit=20, player=player, db=db,
    )

    ids = {m.id for m in result}
    assert visible.id in ids
    assert deleted.id not in ids
    assert len(result) == 1


# --------------------------------------------------------------------------- #
# (5) get_conversations (thread listing) excludes a moderated-deleted
#     latest-in-thread message, and drops a thread entirely when its only
#     message was moderator-deleted.
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_conversations_falls_back_to_prior_message_when_latest_is_deleted():
    player_id, other_id = uuid.uuid4(), uuid.uuid4()
    thread_id = uuid.uuid4()
    older = _msg(
        sender_id=player_id, recipient_id=other_id, thread_id=thread_id,
        sent_at=datetime(2026, 7, 8, 10, 0, 0),
    )
    newer_deleted = _msg(
        sender_id=other_id, recipient_id=player_id, thread_id=thread_id,
        sent_at=datetime(2026, 7, 8, 11, 0, 0), moderation_status="deleted",
    )
    db = FakeDB(messages=[older, newer_deleted])

    result = await MessageService.get_conversations(db, player_id=player_id)

    conv_ids = {c["id"] for c in result["conversations"]}
    assert str(older.id) in conv_ids
    assert str(newer_deleted.id) not in conv_ids
    assert result["total"] == 1


@pytest.mark.asyncio
async def test_conversations_drops_thread_whose_only_message_is_deleted():
    player_id, other_id = uuid.uuid4(), uuid.uuid4()
    only_message = _msg(
        sender_id=player_id, recipient_id=other_id, moderation_status="deleted",
    )
    db = FakeDB(messages=[only_message])

    result = await MessageService.get_conversations(db, player_id=player_id)

    assert result["conversations"] == []
    assert result["total"] == 0


# --------------------------------------------------------------------------- #
# (6) Admin `/admin/messages/all` (admin_messages.py, untouched by this WO)
#     still surfaces a moderation_status='deleted' row -- full visibility.
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_admin_all_messages_still_sees_moderated_deleted_row():
    from src.api.routes.admin_messages import get_all_messages

    visible = _msg()
    deleted = _msg(moderation_status="deleted", moderated_at=datetime(2026, 7, 8), moderated_by=uuid.uuid4())
    db = FakeDB(messages=[visible, deleted])

    result = await get_all_messages(page=1, flagged=None, admin=SimpleNamespace(), db=db)

    ids = {m["id"] for m in result["messages"]}
    assert str(visible.id) in ids
    assert str(deleted.id) in ids  # admin keeps full visibility, incl. deleted
    assert result["total"] == 2


# --------------------------------------------------------------------------- #
# (7) Migration: additive-only, reversible, chained onto the verified head
# --------------------------------------------------------------------------- #

_MIGRATION_PATH = (
    pathlib.Path(__file__).resolve().parents[2]
    / "alembic" / "versions" / "34d0fe6c1af1_add_message_moderation_status.py"
)


@pytest.mark.unit
class TestMigrationAdditiveOnly:
    def test_migration_file_exists(self) -> None:
        assert _MIGRATION_PATH.is_file()

    def test_upgrade_only_adds_the_one_nullable_column(self) -> None:
        source = _MIGRATION_PATH.read_text()
        tree = ast.parse(source)
        upgrade_fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "upgrade")
        upgrade_src = ast.get_source_segment(source, upgrade_fn) or ""
        assert upgrade_src.count("op.add_column(") == 1
        assert "'messages'" in upgrade_src
        assert "moderation_status" in upgrade_src
        assert "nullable=True" in upgrade_src
        for banned in ("op.alter_column", "op.drop_column", "op.create_table", "op.drop_table", "op.drop_index"):
            assert banned not in upgrade_src

    def test_downgrade_is_reversible_drop_column(self) -> None:
        source = _MIGRATION_PATH.read_text()
        tree = ast.parse(source)
        downgrade_fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "downgrade")
        downgrade_src = ast.get_source_segment(source, downgrade_fn) or ""
        assert downgrade_src.count("op.drop_column(") == 1
        assert "'messages'" in downgrade_src
        assert "moderation_status" in downgrade_src

    def test_revision_chain_targets_verified_single_head(self) -> None:
        source = _MIGRATION_PATH.read_text()
        tree = ast.parse(source)
        assigns = {
            n.targets[0].id: n.value.value
            for n in tree.body
            if isinstance(n, ast.Assign)
            and isinstance(n.targets[0], ast.Name)
            and isinstance(n.value, ast.Constant)
        }
        assert assigns.get("revision") == "34d0fe6c1af1"
        # 8b9aa2bd781d was confirmed the single alembic head (poetry run
        # alembic heads) before this migration was authored.
        assert assigns.get("down_revision") == "8b9aa2bd781d"
