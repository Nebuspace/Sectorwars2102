"""Nickname validation for first-login callsign capture.

Canon: sw2102-docs/SYSTEMS/first-login.md:249-255 — an AI-extracted dialogue
name is only ever written to Player.nickname after explicit player
confirmation AND passing every rule below (first_login_service.py's
complete_first_login owns the confirmation gate; this module owns the
rules). This retires the prior unconditional
`player.nickname = session.extracted_player_name` write.
"""
import re
import uuid
from typing import Optional, Tuple

from sqlalchemy import func
from sqlalchemy.orm import Session

from src.models.player import Player
from src.models.user import User

NICKNAME_MIN_LEN = 3
NICKNAME_MAX_LEN = 20

# Alphanumeric, underscore, hyphen; at most one internal space; no leading/
# trailing whitespace and no repeated spaces (canon: first-login.md:252-253,
# "3-20-char charset ... letters/digits/limited punctuation"). ASCII-only —
# this also blocks zero-width/RTL characters (a homograph-impersonation
# vector) without a separate script-detection pass.
# NO-CANON: exact charset — proposed to DECISIONS.
NICKNAME_PATTERN = re.compile(r'^[A-Za-z0-9_-]+(?: [A-Za-z0-9_-]+)?$')

# Configurable, module-level per canon (first-login.md:253). Deliberately a
# small starter set bundling profanity with a handful of reserved/impersonation
# terms (admin roles, the game's own AI persona) — matched as a WHOLE-NAME
# case-insensitive equality, not substring containment, to avoid the
# "Scunthorpe problem" (rejecting innocuous names that merely contain a
# blocked substring). NO-CANON: initial wordlist — proposed to DECISIONS.
NICKNAME_BLOCKLIST = {
    "admin", "administrator", "moderator", "gamemaster",
    "system", "support", "aria", "nebuspace",
    "fuck", "shit", "cunt", "bitch", "asshole", "nigger", "faggot",
}


def validate_nickname(
    db: Session,
    name: Optional[str],
    exclude_player_id: Optional[uuid.UUID] = None,
) -> Tuple[bool, Optional[str]]:
    """Validate a candidate player nickname.

    exclude_player_id, when set, excludes that player's own Player.nickname
    row from the collision scan (AC-3 own-name idempotency) — a player
    re-confirming a name their own row already holds must pass, not
    self-reject as "taken". The User.username check is unaffected: a player
    may not claim someone else's *username* as their nickname even if it's
    their own row being compared, so no equivalent exclusion applies there.

    Returns (True, None) when the name passes every rule, else
    (False, reason) where reason is one of:
      - "length"    — outside NICKNAME_MIN_LEN..NICKNAME_MAX_LEN
      - "charset"   — fails NICKNAME_PATTERN (bad characters, leading/
                      trailing whitespace, more than one internal space)
      - "profanity" — case-insensitive whole-name match against
                      NICKNAME_BLOCKLIST
      - "taken"     — case-insensitive collision with an existing
                      Player.nickname (excluding exclude_player_id's own
                      row, if given) or User.username (impersonation +
                      uniqueness, first-login.md:254)
    """
    if name is None or len(name) < NICKNAME_MIN_LEN or len(name) > NICKNAME_MAX_LEN:
        return False, "length"

    if not NICKNAME_PATTERN.match(name):
        return False, "charset"

    lowered = name.lower()
    if lowered in NICKNAME_BLOCKLIST:
        return False, "profanity"

    # Compared against a pre-lowered plain string (not .ilike(name)) —
    # nicknames may contain "_", the ILIKE single-char wildcard, which would
    # otherwise make e.g. candidate "user_1" match an existing "userX1".
    nickname_query = db.query(Player).filter(
        func.lower(Player.nickname) == lowered
    )
    if exclude_player_id is not None:
        nickname_query = nickname_query.filter(Player.id != exclude_player_id)
    nickname_clash = nickname_query.first()
    if nickname_clash:
        return False, "taken"

    username_clash = db.query(User).filter(
        func.lower(User.username) == lowered
    ).first()
    if username_clash:
        return False, "taken"

    return True, None
