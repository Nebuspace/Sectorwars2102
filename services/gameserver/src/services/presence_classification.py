"""Presence rep/law classification — WO-API-PHASE2 Lane B6.

Pure, DB-free port of the player-client's own
``contactClassification.ts`` (``playerRepBucket``/``isHostileNpc``) so the
red/gray/blue reputation bucket and the raider-fair-game hostile flag are
computed ONE way server-side, then consumed by every presence-emitting
path: the REST ``players_present`` enricher
(``intrasystem_movement_service.enrich_presence_with_live_pose``, shared
by ``sectors.py``/``player.py``) and the WS ``sector_players`` broadcast
(``websocket_service.get_sector_players``). Those two paths read from
structurally different sources (a DB-backed JSONB array vs. live
connection-metadata) and are not worth forcibly unifying into one
function; this module is the shared classification kernel they both call
instead ("dual-wire the same helper" — WO-API-PHASE2 map decision).

The client keeps its own copy as a graceful-degrade fallback (legacy
JSONB rows / any client build that predates these fields) — see that
file's own doc-comment. Any change to the bucket vocabulary or the
hostile threshold must be mirrored there by hand.
"""

from typing import Optional

from src.services.npc_spawn_service import LAWFUL_TARGET_THRESHOLD

# Exact vocabulary contactClassification.ts's RED_TIERS/GRAY_TIERS ship —
# personal_reputation_service.py's 8-tier scale (REPUTATION_TIERS), bucketed
# to 3 for the tactical/annunciator display.
_RED_TIERS = frozenset({"Villain", "Criminal", "Outlaw"})
_GRAY_TIERS = frozenset({"Suspicious"})


def player_rep_bucket(reputation_tier: Optional[str]) -> str:
    """Byte-equivalent port of the client's ``playerRepBucket()``: 'red' for
    Villain/Criminal/Outlaw, 'gray' for Suspicious, 'blue' for everything
    else (Neutral/Lawful/Heroic/Legendary, or an unknown/missing tier)."""
    if reputation_tier in _RED_TIERS:
        return "red"
    if reputation_tier in _GRAY_TIERS:
        return "gray"
    return "blue"


def npc_hostile(notoriety: Optional[int], archetype: Optional[str] = None) -> bool:
    """Byte-equivalent port of the client's ``isHostileNpc()`` —
    archetype-FIRST, notoriety-fallback, not notoriety-only:

      - HOSTILE_RAIDER -> always fair game.
      - LAW_ENFORCEMENT -> never fair game.
      - otherwise -> the trader scruples axis: notoriety at/above
        ``LAWFUL_TARGET_THRESHOLD`` (``notoriety_tier()``'s UNSCRUPULOUS-or-
        above gate; attacking a trader below it is the canon
        ``attack_innocent`` reputation penalty).

    ``notoriety`` is EXCLUSIVELY the trader axis (npc_tick_loops.py only
    rolls a value ``if is_trader``) -- pirates and police always spawn with
    ``notoriety=None``, which is exactly why archetype has to lead: a
    notoriety-only read would silently read every HOSTILE_RAIDER as
    non-hostile."""
    arch = (archetype or "").upper()
    if arch == "HOSTILE_RAIDER":
        return True
    if arch == "LAW_ENFORCEMENT":
        return False
    return (notoriety or 0) >= LAWFUL_TARGET_THRESHOLD
