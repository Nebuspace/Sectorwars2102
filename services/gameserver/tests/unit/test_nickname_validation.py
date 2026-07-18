"""WO-PUX-FLOGIN-NICKNAME G1 — nickname_validation_service.validate_nickname.

Canon: sw2102-docs/SYSTEMS/first-login.md:249-255. Covers every rule (length,
charset, profanity, impersonation/uniqueness against BOTH Player.nickname and
User.username) plus the pass path.

DB-free: a tiny fake Query that interprets the REAL SQLAlchemy
`func.lower(Column) == <str>` condition the service builds against a literal
candidate list (fake-query-filter-interpreter-pattern) — this proves the
actual case-insensitive comparison, not just a canned return value. Chainable:
a second `.filter(Player.id != exclude_player_id)` (AC-3 own-name exclusion)
is interpreted the same way, by operator, not canned-returned either.
"""
import operator
import types
import uuid

from src.services.nickname_validation_service import validate_nickname, NICKNAME_BLOCKLIST
from src.models.player import Player
from src.models.user import User


class _FakeUniquenessQuery:
    def __init__(self, rows, column_name):
        self._rows = rows
        self._column_name = column_name

    def filter(self, condition):
        # Two condition shapes reach this fake:
        #   func.lower(Model.col) == lowered_literal  (operator.eq) — the
        #     case-insensitive collision scan; condition.right is the bound
        #     literal parameter.
        #   Player.id != exclude_player_id            (operator.ne) — the
        #     own-row exclusion chained onto the Player scan (AC-3).
        target = condition.right.value
        if condition.operator is operator.ne:
            matches = [r for r in self._rows if getattr(r, "id", None) != target]
        else:
            matches = [
                r for r in self._rows
                if (getattr(r, self._column_name) or "").lower() == target
            ]
        return _FakeUniquenessQuery(matches, self._column_name)

    def first(self):
        return self._rows[0] if self._rows else None


class _FakeDB:
    def __init__(self, players=None, users=None):
        self._players = players or []
        self._users = users or []

    def query(self, model):
        if model is Player:
            return _FakeUniquenessQuery(self._players, "nickname")
        if model is User:
            return _FakeUniquenessQuery(self._users, "username")
        raise AssertionError(f"unexpected model queried in validate_nickname: {model}")


def _row(**kw):
    return types.SimpleNamespace(**kw)


def _empty_db():
    return _FakeDB()


# --- pass path -------------------------------------------------------------

def test_valid_name_passes():
    ok, reason = validate_nickname(_empty_db(), "Voidrunner")
    assert (ok, reason) == (True, None)


def test_valid_name_with_underscore_hyphen_and_one_internal_space_passes():
    for candidate in ("Star_Fox", "Neo-Tokyo", "Han Solo"):
        ok, reason = validate_nickname(_empty_db(), candidate)
        assert (ok, reason) == (True, None), candidate


# --- length ------------------------------------------------------------

def test_two_char_name_rejected_for_length():
    ok, reason = validate_nickname(_empty_db(), "ab")
    assert (ok, reason) == (False, "length")


def test_twenty_one_char_name_rejected_for_length():
    ok, reason = validate_nickname(_empty_db(), "a" * 21)
    assert (ok, reason) == (False, "length")


def test_none_name_rejected_for_length():
    ok, reason = validate_nickname(_empty_db(), None)
    assert (ok, reason) == (False, "length")


def test_three_char_name_passes_length_boundary():
    ok, reason = validate_nickname(_empty_db(), "abc")
    assert (ok, reason) == (True, None)


def test_twenty_char_name_passes_length_boundary():
    ok, reason = validate_nickname(_empty_db(), "a" * 20)
    assert (ok, reason) == (True, None)


# --- charset -------------------------------------------------------------

def test_disallowed_punctuation_rejected_for_charset():
    ok, reason = validate_nickname(_empty_db(), "bad!name")
    assert (ok, reason) == (False, "charset")


def test_leading_or_trailing_whitespace_rejected_for_charset():
    ok, reason = validate_nickname(_empty_db(), " Captain")
    assert (ok, reason) == (False, "charset")
    ok, reason = validate_nickname(_empty_db(), "Captain ")
    assert (ok, reason) == (False, "charset")


def test_multiple_internal_spaces_rejected_for_charset():
    ok, reason = validate_nickname(_empty_db(), "Han Solo Jr")
    assert (ok, reason) == (False, "charset")


def test_control_character_rejected_for_charset():
    ok, reason = validate_nickname(_empty_db(), "bad\tname")
    assert (ok, reason) == (False, "charset")
    ok, reason = validate_nickname(_empty_db(), "bad\x00name")
    assert (ok, reason) == (False, "charset")


def test_emoji_rejected_for_charset():
    """ASCII-only NICKNAME_PATTERN also blocks emoji.

    Note (AC-10): the same ASCII-only charset makes Unicode-normalization
    duplicate collisions (e.g. an accented "é" vs its combining-mark form)
    unrepresentable in a nickname at all — non-ASCII characters never pass
    charset in the first place, so there is no normalized form left to
    collide on. AC-10 falls out of this stricter charset rule rather than
    needing a separate NFKC-normalization pass.
    """
    ok, reason = validate_nickname(_empty_db(), "Void\U0001F680runner")
    assert (ok, reason) == (False, "charset")


# --- profanity -------------------------------------------------------------

def test_profane_name_rejected_with_reason_profanity():
    blocked = next(iter(NICKNAME_BLOCKLIST))
    ok, reason = validate_nickname(_empty_db(), blocked)
    assert (ok, reason) == (False, "profanity")


def test_profanity_check_is_case_insensitive():
    blocked = next(iter(NICKNAME_BLOCKLIST))
    ok, reason = validate_nickname(_empty_db(), blocked.upper())
    assert (ok, reason) == (False, "profanity")


def test_profanity_blocklist_is_whole_name_not_substring():
    """Avoids the Scunthorpe problem — a name merely CONTAINING a blocked
    word must not be rejected."""
    assert "admin" in NICKNAME_BLOCKLIST
    ok, reason = validate_nickname(_empty_db(), "AdminValley")
    assert (ok, reason) == (True, None)


# --- impersonation / uniqueness --------------------------------------------

def test_duplicate_of_existing_player_nickname_rejected_case_insensitive():
    db = _FakeDB(players=[_row(nickname="Voidrunner")])
    ok, reason = validate_nickname(db, "VOIDRUNNER")
    assert (ok, reason) == (False, "taken")


def test_duplicate_of_existing_user_username_rejected_case_insensitive():
    db = _FakeDB(users=[_row(username="StarCaptain")])
    ok, reason = validate_nickname(db, "starcaptain")
    assert (ok, reason) == (False, "taken")


def test_no_collision_passes():
    db = _FakeDB(
        players=[_row(nickname="SomeoneElse")],
        users=[_row(username="another_user")],
    )
    ok, reason = validate_nickname(db, "Voidrunner")
    assert (ok, reason) == (True, None)


def test_underscore_candidate_does_not_wildcard_match_a_different_username():
    """Guards the func.lower(...) == plain-string choice over .ilike(name):
    ILIKE treats "_" as a single-char wildcard, so a naive .ilike("user_1")
    would false-positive against "userX1". func.lower equality must not."""
    db = _FakeDB(users=[_row(username="userX1")])
    ok, reason = validate_nickname(db, "user_1")
    assert (ok, reason) == (True, None)


# --- own-name idempotency (AC-3) --------------------------------------------

def test_own_existing_nickname_passes_when_excluded():
    """A player re-confirming a name their own Player row already holds
    must pass, not self-reject as "taken"."""
    player_id = uuid.uuid4()
    db = _FakeDB(players=[_row(id=player_id, nickname="Voidrunner")])
    ok, reason = validate_nickname(db, "Voidrunner", exclude_player_id=player_id)
    assert (ok, reason) == (True, None)


def test_a_different_players_nickname_still_taken_despite_exclusion():
    """exclude_player_id only exempts that ONE row — a different player's
    row with the same nickname must still reject as "taken"."""
    player_id = uuid.uuid4()
    other_player_id = uuid.uuid4()
    db = _FakeDB(players=[_row(id=other_player_id, nickname="Voidrunner")])
    ok, reason = validate_nickname(db, "Voidrunner", exclude_player_id=player_id)
    assert (ok, reason) == (False, "taken")


# --- race window (AC-12, documentation only) --------------------------------

def test_two_sequential_validations_of_same_fresh_name_both_pass():
    """Documents CURRENT behavior, not a guarantee of safety: application-
    level uniqueness has a race window between validate_nickname and the
    caller's commit. Two concurrent first-login completions racing on the
    same fresh name can both pass this check before either has written its
    row. A DB unique index would close the window but is deliberately NOT
    added here (beyond-additive for this WO — it would turn a nickname
    collision into a raw IntegrityError/500 instead of the clean "taken"
    response this module returns); proposed to DECISIONS as a follow-up.
    """
    db = _empty_db()
    first_ok, first_reason = validate_nickname(db, "Freshname")
    second_ok, second_reason = validate_nickname(db, "Freshname")
    assert (first_ok, first_reason) == (True, None)
    assert (second_ok, second_reason) == (True, None)
