"""WO-QTI-DISPLAY-NAME-EXPR — Player.display_name_expr precedence pins.

Player.display_name_expr() is the SQL twin of the Player.username @property
(src/models/player.py) — three call sites (regional_governance_service,
admin_messages, admin_comprehensive) had independently re-implemented this
fallback rule and drifted (one leaked '' nicknames instead of falling
through). This proves the helper's REAL SQLAlchemy clause tree — not a
hand-written parallel reimplementation that could itself silently drift —
matches the @property byte-for-byte across the (nickname, username) matrix:
a set nickname, an empty-string nickname ('' must be treated as unset, same
as the property's truthiness check), a NULL nickname, and no linked
User/Player row at all.

DB-free: `_eval_expr` walks the actual `coalesce(nullif(...), ..., ...)`
clause tree the helper builds (fake-query-interpreter-pattern) and evaluates
it against a plain dict standing in for joined row values, mirroring SQL
NULL/COALESCE/NULLIF semantics — no engine, no session.
"""
from types import SimpleNamespace

from sqlalchemy.sql.elements import BindParameter, Label
from sqlalchemy.sql.functions import Function

from src.models.player import Player


def _eval_expr(node, values):
    """Evaluate a display_name_expr() clause tree against `values`, a dict
    keyed by (table_name, column_name) -> python value. Interprets the real
    coalesce/nullif/Column/BindParameter nodes SQLAlchemy actually built."""
    if isinstance(node, Label):
        return _eval_expr(node.element, values)
    if isinstance(node, BindParameter):
        return node.value
    if isinstance(node, Function):
        args = list(node.clause_expr.element.clauses)
        if node.name == "coalesce":
            for arg in args:
                value = _eval_expr(arg, values)
                if value is not None:
                    return value
            return None
        if node.name == "nullif":
            a, b = (_eval_expr(arg, values) for arg in args)
            return None if a == b else a
        raise AssertionError(f"unhandled SQL function in display_name_expr tree: {node.name}")
    # a mapped Column (Player.nickname / User.username)
    return values.get((node.table.name, node.name))


def _property_display_name(nickname, username):
    """Invoke the REAL Player.username @property against a duck-typed fake
    (no ORM/DB needed for a plain property read) — the ground truth the SQL
    expression must match byte-for-byte."""
    fake_user = SimpleNamespace(username=username) if username is not None else None
    fake_player = SimpleNamespace(nickname=nickname, user=fake_user)
    return Player.username.fget(fake_player)


MATRIX = [
    ("nickname_set", "Nova", "nova_user"),
    ("empty_nickname_falls_through", "", "nova_user"),
    ("null_nickname_falls_through", None, "nova_user"),
    ("no_user_row_at_all", None, None),
]


def test_default_fallback_matches_username_property_byte_for_byte():
    for case_id, nickname, username in MATRIX:
        values = {("players", "nickname"): nickname, ("users", "username"): username}
        expr_result = _eval_expr(Player.display_name_expr(), values)
        property_result = _property_display_name(nickname, username)
        assert expr_result == property_result, (
            f"{case_id}: display_name_expr()={expr_result!r} != "
            f"Player.username={property_result!r}"
        )


def test_empty_string_nickname_falls_through_not_leaked():
    # The bug this WO fixes: '' must resolve to the joined username, not ''.
    values = {("players", "nickname"): "", ("users", "username"): "nova_user"}
    assert _eval_expr(Player.display_name_expr(), values) == "nova_user"


def test_default_fallback_literal_on_no_match():
    values = {("players", "nickname"): None, ("users", "username"): None}
    assert _eval_expr(Player.display_name_expr(), values) == "Unknown Player"


def test_fallback_none_omits_terminal_literal_on_no_match():
    # admin_messages opts out of the terminal literal to preserve its
    # pre-existing `nickname: null` shape when a sender's Player/User row is
    # missing entirely (outer-join miss) — must NOT become "Unknown Player".
    values = {("players", "nickname"): None, ("users", "username"): None}
    expr = Player.display_name_expr(label="nickname", fallback=None)
    assert _eval_expr(expr, values) is None


def test_fallback_none_still_fixes_empty_string_case():
    # fallback=None only removes the terminal literal — the nullif('') fix
    # for the empty-string bug still applies.
    values = {("players", "nickname"): "", ("users", "username"): "nova_user"}
    expr = Player.display_name_expr(label="nickname", fallback=None)
    assert _eval_expr(expr, values) == "nova_user"


def test_custom_label_is_applied():
    expr = Player.display_name_expr(label="nickname")
    assert expr.name == "nickname"
    assert Player.display_name_expr().name == "username"
