"""WO-API-PHASE2 Lane B6: server-side port of the client's rep-bucket /
hostile classification (contactClassification.ts's playerRepBucket /
isHostileNpc). DB-free unit tests against the pure helpers
(presence_classification.py) plus the two presence-emitting paths that
now call them: the REST enricher (intrasystem_movement_service.
enrich_presence_with_live_pose, shared by sectors.py/player.py -- see
test_presence_mirror.py for that function's own pose-focused coverage)
and the WS broadcast (websocket_service.ConnectionManager.
get_sector_players).
"""
from __future__ import annotations

import uuid

from src.services import npc_spawn_service, presence_classification
from src.services.intrasystem_movement_service import enrich_presence_with_live_pose
from src.services.presence_classification import npc_hostile, player_rep_bucket
from src.services.websocket_service import ConnectionManager


class _Row:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class _FakeQuery:
    def __init__(self, rows):
        self._rows = list(rows)

    def filter(self, *a, **kw):
        return self

    def all(self):
        return list(self._rows)


class _FakeSession:
    def __init__(self, *, npcs=None, players=None):
        self._npcs = npcs or []
        self._players = players or []

    def query(self, model):
        name = getattr(model, "__name__", "")
        if name == "NPCCharacter":
            return _FakeQuery(self._npcs)
        if name == "Player":
            return _FakeQuery(self._players)
        return _FakeQuery([])


# --- P1: player_rep_bucket parity table across all 8 canon tiers -----------

def test_p1_player_rep_bucket_matches_client_vocabulary_for_all_8_tiers():
    """personal_reputation_service.REPUTATION_TIERS's 8 tier names, bucketed
    exactly like the client's playerRepBucket() (contactClassification.ts)."""
    assert player_rep_bucket("Villain") == "red"
    assert player_rep_bucket("Criminal") == "red"
    assert player_rep_bucket("Outlaw") == "red"
    assert player_rep_bucket("Suspicious") == "gray"
    assert player_rep_bucket("Neutral") == "blue"
    assert player_rep_bucket("Lawful") == "blue"
    assert player_rep_bucket("Heroic") == "blue"
    assert player_rep_bucket("Legendary") == "blue"
    # Missing/unknown tier defaults CLEAR, same as the client's `tier &&`
    # guard falling through to 'blue'.
    assert player_rep_bucket(None) == "blue"
    assert player_rep_bucket("") == "blue"
    assert player_rep_bucket("SomeFutureTier") == "blue"


# --- P2: npc_hostile is archetype-first, notoriety-fallback ----------------
# (byte-equivalent port of the client's isHostileNpc() -- REVISE: the first
# pass wrongly dropped the archetype short-circuits, which are LOAD-BEARING
# since pirates/police always spawn with notoriety=None, see below.)

def test_p2_npc_hostile_byte_equivalence_table_vs_client_is_hostile_npc():
    """All 4 branches of the client's isHostileNpc(), including the
    regression guard: a HOSTILE_RAIDER with notoriety=None (its actual spawn
    state -- notoriety is exclusively the trader axis) is STILL fair game,
    because archetype is checked before notoriety, not instead of it."""
    assert npc_hostile(None, "HOSTILE_RAIDER") is True    # pirate, no notoriety -- fair game
    assert npc_hostile(80, "LAW_ENFORCEMENT") is False    # cop, even with high notoriety -- never fair game
    assert npc_hostile(80, None) is True                  # trader, notoriety over threshold
    assert npc_hostile(10, None) is False                 # trader, notoriety under threshold
    # Case-insensitive, matching the client's .toUpperCase() -- archetype.name
    # is always the uppercase enum member in practice, but the port itself
    # doesn't assume that.
    assert npc_hostile(None, "hostile_raider") is True
    assert npc_hostile(80, "law_enforcement") is False


def test_p2_npc_hostile_boundary_at_the_threshold():
    threshold = npc_spawn_service.LAWFUL_TARGET_THRESHOLD
    assert npc_hostile(threshold - 1) is False
    assert npc_hostile(threshold) is True
    assert npc_hostile(None) is False  # no notoriety recorded, no archetype -> not fair game


def test_p2_npc_hostile_tracks_the_module_constant_not_a_hardcoded_literal(monkeypatch):
    """Proves the threshold is read from presence_classification's own
    LAWFUL_TARGET_THRESHOLD name (imported from npc_spawn_service) at call
    time, not compiled in as a bare `50` -- shifting the constant shifts
    the boundary npc_hostile enforces."""
    assert presence_classification.LAWFUL_TARGET_THRESHOLD == npc_spawn_service.LAWFUL_TARGET_THRESHOLD == 50
    monkeypatch.setattr(presence_classification, "LAWFUL_TARGET_THRESHOLD", 60)
    assert npc_hostile(55) is False   # below the PATCHED threshold
    assert npc_hostile(60) is True    # at the PATCHED threshold
    # 55 would have been hostile under the original 50-threshold -- if this
    # module had hardcoded 50, the line above's `is False` would fail.


# --- P3: WS sector_players and REST players_present agree ------------------

def test_p3_ws_and_rest_produce_the_same_rep_bucket_for_a_player():
    reputation_tier = "Outlaw"

    # REST path: enrich_presence_with_live_pose (sectors.py/player.py).
    pid = str(uuid.uuid4())
    player_row = _Row(id=uuid.UUID(pid), intrasystem_pose=None, reputation_tier=reputation_tier)
    rest_session = _FakeSession(players=[player_row])
    rest_enriched = enrich_presence_with_live_pose(rest_session, [{"player_id": pid, "username": "someone"}])
    rest_rep_bucket = rest_enriched[0]["rep_bucket"]

    # WS path: ConnectionManager.get_sector_players (websocket_service.py).
    cm = ConnectionManager()
    cm.sector_connections[7] = {"user-1"}
    cm.connection_metadata["user-1"] = {
        "user_data": {"username": "someone", "reputation_tier": reputation_tier},
    }
    ws_players = cm.get_sector_players(7)
    ws_rep_bucket = ws_players[0]["rep_bucket"]

    assert rest_rep_bucket == ws_rep_bucket == "red"


def test_p3_ws_and_rest_agree_across_the_full_tier_set():
    """Same assertion as above, swept across every tier -- both paths must
    move in lockstep since they call the identical presence_classification
    helper, not two independently-maintained copies."""
    for tier, expected in [
        ("Villain", "red"), ("Criminal", "red"), ("Outlaw", "red"),
        ("Suspicious", "gray"),
        ("Neutral", "blue"), ("Lawful", "blue"), ("Heroic", "blue"), ("Legendary", "blue"),
    ]:
        pid = str(uuid.uuid4())
        player_row = _Row(id=uuid.UUID(pid), intrasystem_pose=None, reputation_tier=tier)
        rest_enriched = enrich_presence_with_live_pose(
            _FakeSession(players=[player_row]), [{"player_id": pid, "username": "p"}]
        )

        cm = ConnectionManager()
        cm.sector_connections[1] = {"u"}
        cm.connection_metadata["u"] = {"user_data": {"username": "p", "reputation_tier": tier}}
        ws_players = cm.get_sector_players(1)

        assert rest_enriched[0]["rep_bucket"] == ws_players[0]["rep_bucket"] == expected, tier


def test_p3_rest_npc_hostile_reflects_live_notoriety_not_a_stale_mirror():
    """NPCs never appear in WS's sector_players (it only ever lists live
    socket connections -- humans), so the only presence path emitting
    `hostile` for an NPC is REST's enrich_presence_with_live_pose. Confirms
    it uses the LIVE NPCCharacter.notoriety, not whatever value was mirrored
    into the JSONB entry at spawn time (npc_spawn_service._presence_entry)."""
    nid = str(uuid.uuid4())
    npc_row = _Row(
        id=uuid.UUID(nid),
        current_activity=None,
        daily_schedule=None,
        archetype=None,
        intrasystem_pose=None,
        notoriety=80,
    )
    stale_entry = {"player_id": nid, "is_npc": True, "notoriety": 10, "hostile": False}
    enriched = enrich_presence_with_live_pose(_FakeSession(npcs=[npc_row]), [stale_entry])

    assert enriched[0]["hostile"] is True  # live 80 >= threshold, not the stale mirrored 10/False


def test_p2_npc_spawn_service_presence_entry_write_time_mirror_matches_live_notoriety():
    """_presence_entry's write-time `hostile` mirror (defense-in-depth,
    same idiom as `archetype`) agrees with npc_hostile() for the same
    notoriety -- a spawn-time snapshot that's at least correct at spawn."""
    npc = _Row(
        id=uuid.uuid4(),
        display_name="Captain Test",
        archetype=_Row(name="TRADER"),
        notoriety=65,
        current_activity=None,
        daily_schedule=None,
    )
    ship = _Row(id=uuid.uuid4(), name="Test Runner", type=_Row(name="LIGHT_FREIGHTER"))

    entry = npc_spawn_service._presence_entry(npc, ship)

    assert entry["hostile"] is True
    assert entry["hostile"] == npc_hostile(npc.notoriety, "TRADER")


# --- End-to-end regression guards: a HOSTILE_RAIDER pirate and a
# LAW_ENFORCEMENT cop, both on BOTH presence paths (write-time _presence_entry
# AND read-time enrich_presence_with_live_pose). This is exactly the live
# repro the REVISE flagged: pirates/police spawn with notoriety=None
# (npc_tick_loops.py only rolls a value `if is_trader`), so a notoriety-only
# npc_hostile would silently read every pirate as non-hostile.

def _npc_ship_pair(archetype_name, notoriety):
    npc = _Row(
        id=uuid.uuid4(),
        display_name="Captain Test",
        archetype=_Row(name=archetype_name),
        notoriety=notoriety,
        current_activity=None,
        daily_schedule=None,
        intrasystem_pose=None,
    )
    ship = _Row(id=uuid.uuid4(), name="Test Runner", type=_Row(name="LIGHT_FREIGHTER"))
    return npc, ship


def test_end_to_end_hostile_raider_pirate_with_no_notoriety_is_hostile_on_both_paths():
    """The regression guard: a HOSTILE_RAIDER's actual spawn state is
    notoriety=None (traders only roll one) -- must still read hostile=true
    everywhere, not silently flip to non-hostile."""
    npc, ship = _npc_ship_pair("HOSTILE_RAIDER", None)

    write_time_entry = npc_spawn_service._presence_entry(npc, ship)
    assert write_time_entry["hostile"] is True

    read_time_entry = enrich_presence_with_live_pose(
        _FakeSession(npcs=[npc]),
        [{"player_id": str(npc.id), "is_npc": True}],
    )[0]
    assert read_time_entry["hostile"] is True


def test_end_to_end_law_enforcement_cop_with_high_notoriety_is_never_hostile_on_either_path():
    """A LAW_ENFORCEMENT archetype must never read fair-game, even if it
    somehow carried a high notoriety value -- archetype wins over notoriety."""
    npc, ship = _npc_ship_pair("LAW_ENFORCEMENT", 80)

    write_time_entry = npc_spawn_service._presence_entry(npc, ship)
    assert write_time_entry["hostile"] is False

    read_time_entry = enrich_presence_with_live_pose(
        _FakeSession(npcs=[npc]),
        [{"player_id": str(npc.id), "is_npc": True}],
    )[0]
    assert read_time_entry["hostile"] is False
