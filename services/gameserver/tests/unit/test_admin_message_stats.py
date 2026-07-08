"""WO-QTI-DISPLAY-NAME-EXPR — regression pin: admin_messages `/stats` no
longer leaks an empty-string nickname.

Before this WO, `admin_messages.get_message_statistics`'s active-senders
query used `func.coalesce(Player.nickname, User.username)` with no
`nullif('')` — a sender whose Player.nickname was '' (not NULL) surfaced as
`nickname: ""` instead of falling through to the linked User's username.
Converging onto `Player.display_name_expr(label='nickname', fallback=None)`
fixes that while intentionally preserving the pre-existing `nickname: null`
shape when a sender's Player/User row is missing entirely (fallback=None —
see test_player_display_name_expr.py for that contract).

DB-free: the route uses a synchronous SQLAlchemy `Session` (`db.query(...)`
ORM-style, not `select()`), so `_FakeDB` fakes just enough of the chained
Query API. Rather than canning a return value, the active-senders branch
interprets the REAL `Player.display_name_expr(...)` clause tree the route
passes in (reusing the same evaluator proven in
test_player_display_name_expr.py) against seeded (nickname, username) rows —
proving the route actually calls the real helper, not a stand-in.
"""
import asyncio
from types import SimpleNamespace
from uuid import uuid4

from src.api.routes.admin_messages import get_message_statistics
from tests.unit.test_player_display_name_expr import _eval_expr


class _FakeScalarQuery:
    """Stands in for the four `db.query(func.count(Message.id))...scalar()`
    chains (total / today / this-week / flagged) — value is fixed at
    construction, `.filter()` is a no-op passthrough."""

    def __init__(self, value):
        self._value = value

    def filter(self, *args, **kwargs):
        return self

    def scalar(self):
        return self._value


class _FakeSendersQuery:
    def __init__(self, rows):
        self._rows = rows

    def outerjoin(self, *args, **kwargs):
        return self

    def group_by(self, *args, **kwargs):
        return self

    def order_by(self, *args, **kwargs):
        return self

    def limit(self, *args, **kwargs):
        return self

    def all(self):
        return self._rows


class _FakeDB:
    """The route makes 5 `db.query(...)` calls: 4 single-column scalar
    counts, then a 3-column active-senders projection
    (Message.sender_id, display_name_expr(...), func.count(...)). Dispatch
    on arg count since that shape is fixed by the route's own code."""

    def __init__(self, counts, sender_seed):
        self._counts = iter(counts)
        # sender_seed: list of (sender_id, {(table,col): value, ...}, count)
        self._sender_seed = sender_seed

    def query(self, *cols):
        if len(cols) != 3:
            return _FakeScalarQuery(next(self._counts))

        name_expr = cols[1]
        rows = [
            (sender_id, _eval_expr(name_expr, values), count)
            for sender_id, values, count in self._sender_seed
        ]
        return _FakeSendersQuery(rows)


def _run(coro):
    return asyncio.run(coro)


def test_empty_string_nickname_falls_through_to_username():
    sender_id = uuid4()
    fake_db = _FakeDB(
        counts=[10, 2, 5, 1],
        sender_seed=[
            (
                sender_id,
                {("players", "nickname"): "", ("users", "username"): "GhostTrader"},
                7,
            ),
        ],
    )

    result = _run(get_message_statistics(admin=SimpleNamespace(), db=fake_db))

    senders = result["most_active_senders"]
    assert len(senders) == 1
    assert senders[0]["nickname"] == "GhostTrader"
    assert senders[0]["nickname"] != ""


def test_missing_sender_row_stays_null_not_fabricated():
    # Outer-join miss (sender has no Player/User row at all) must still
    # surface `nickname: None` — display_name_expr's fallback=None here is
    # what keeps this shape byte-stable relative to pre-WO behavior.
    sender_id = uuid4()
    fake_db = _FakeDB(
        counts=[3, 0, 1, 0],
        sender_seed=[
            (
                sender_id,
                {("players", "nickname"): None, ("users", "username"): None},
                2,
            ),
        ],
    )

    result = _run(get_message_statistics(admin=SimpleNamespace(), db=fake_db))

    senders = result["most_active_senders"]
    assert len(senders) == 1
    assert senders[0]["nickname"] is None


def test_set_nickname_still_wins():
    sender_id = uuid4()
    fake_db = _FakeDB(
        counts=[1, 1, 1, 0],
        sender_seed=[
            (
                sender_id,
                {("players", "nickname"): "Nova", ("users", "username"): "nova_user"},
                4,
            ),
        ],
    )

    result = _run(get_message_statistics(admin=SimpleNamespace(), db=fake_db))

    assert result["most_active_senders"][0]["nickname"] == "Nova"
    assert result["total_messages"] == 1
    assert result["messages_today"] == 1
    assert result["messages_this_week"] == 1
    assert result["flagged_messages"] == 0
