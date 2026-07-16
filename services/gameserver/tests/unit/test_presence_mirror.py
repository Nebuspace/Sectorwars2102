"""P0-FIX-PRESENCE-MIRROR (Max two-seat repro, 2026-07-16): a player's
authoritative pose can exist (Player.intrasystem_pose) while their OWN
players_present entry has no pose keys at all -- ensure_player_pose's lazy
create-on-GET (GET /helm/intrasystem/pose) never mirrors, only burn/halt do.
Every OTHER player's client rendered them "porting" as a result.

enrich_presence_with_live_pose (intrasystem_movement_service.py) closes this
by re-deriving EVERY entry's pose (human AND NPC) from the authoritative row
at READ time, extending the pre-existing NPC-only enrichment idiom already
used by sectors.py/player.py -- these tests exercise that shared function
directly, DB-free.
"""
from __future__ import annotations

import uuid

from src.services.intrasystem_movement_service import enrich_presence_with_live_pose

POSE = {
    "x_pct": 40.0, "y_pct": 60.0, "heading_deg": 90.0,
    "phase": "idle", "burning": False, "leg": None,
}


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


def _human_entry(player_id, **extra):
    e = {"player_id": player_id, "username": "someone"}
    e.update(extra)
    return e


def _npc_entry(npc_id, **extra):
    e = {"player_id": npc_id, "is_npc": True}
    e.update(extra)
    return e


def test_empty_present_list_short_circuits_no_query_no_crash():
    session = _FakeSession()  # would raise on any query() call it doesn't recognize
    assert enrich_presence_with_live_pose(session, []) == []


def test_human_entry_with_no_pose_key_gets_one_from_the_authoritative_player_row():
    """The exact live repro: ensure_player_pose wrote Player.intrasystem_pose
    but never touched the presence entry -- ZERO pose keys pre-fix."""
    pid = str(uuid.uuid4())
    present = [_human_entry(pid)]  # no "pose" key at all
    player = _Row(id=uuid.UUID(pid), intrasystem_pose=dict(POSE))
    session = _FakeSession(players=[player])

    enriched = enrich_presence_with_live_pose(session, present)

    assert len(enriched) == 1
    assert enriched[0]["pose"]["x_pct"] == 40.0
    assert enriched[0]["pose"]["phase"] == "idle"
    assert enriched[0]["username"] == "someone"  # other fields preserved


def test_human_entry_pose_is_overwritten_not_merely_filled_in_if_stale():
    """Re-derives from the authoritative row EVERY read, not just when
    missing -- a stale mirrored pose must be replaced, not trusted."""
    pid = str(uuid.uuid4())
    stale_pose = {"x_pct": 1.0, "y_pct": 1.0, "heading_deg": 0.0, "phase": "idle", "burning": False, "leg": None}
    present = [_human_entry(pid, pose=stale_pose)]
    player = _Row(id=uuid.UUID(pid), intrasystem_pose=dict(POSE))
    session = _FakeSession(players=[player])

    enriched = enrich_presence_with_live_pose(session, present)

    assert enriched[0]["pose"]["x_pct"] == 40.0  # the FRESH value, not 1.0


def test_human_with_no_pose_on_the_player_row_either_is_left_without_a_pose_key():
    """A player who has genuinely never established a pose at all (no
    intrasystem_pose on the Player row) must not crash and must not get a
    fabricated pose -- leaves the entry as-is."""
    pid = str(uuid.uuid4())
    present = [_human_entry(pid)]
    player = _Row(id=uuid.UUID(pid), intrasystem_pose=None)
    session = _FakeSession(players=[player])

    enriched = enrich_presence_with_live_pose(session, present)

    assert "pose" not in enriched[0]


def test_npc_enrichment_is_preserved_unchanged_activity_mission_archetype_and_pose():
    """Regression guard: this function REPLACES sectors.py's/player.py's own
    inline NPC-only block verbatim -- must not lose any of its fields."""
    nid = str(uuid.uuid4())
    present = [_npc_entry(nid)]
    npc = _Row(
        id=uuid.UUID(nid),
        current_activity=_Row(name="PATROL"),
        daily_schedule={"mission": "commerce"},
        archetype=_Row(name="TRADER"),
        intrasystem_pose=dict(POSE),
    )
    session = _FakeSession(npcs=[npc])

    enriched = enrich_presence_with_live_pose(session, present)

    assert enriched[0]["activity"] == "PATROL"
    assert enriched[0]["mission"] == "commerce"
    assert enriched[0]["archetype"] == "TRADER"
    assert enriched[0]["pose"]["x_pct"] == 40.0
    assert enriched[0]["is_npc"] is True


def test_npc_missing_daily_schedule_mission_defaults_to_commerce():
    nid = str(uuid.uuid4())
    present = [_npc_entry(nid)]
    npc = _Row(
        id=uuid.UUID(nid),
        current_activity=None,
        daily_schedule=None,
        archetype=None,
        intrasystem_pose=None,
    )
    session = _FakeSession(npcs=[npc])

    enriched = enrich_presence_with_live_pose(session, present)

    assert enriched[0]["mission"] == "commerce"
    assert enriched[0]["activity"] is None
    assert "pose" not in enriched[0]


def test_mixed_human_and_npc_entries_both_enriched_independently():
    pid, nid = str(uuid.uuid4()), str(uuid.uuid4())
    present = [_human_entry(pid), _npc_entry(nid)]
    player = _Row(id=uuid.UUID(pid), intrasystem_pose={**POSE, "x_pct": 11.0})
    npc = _Row(
        id=uuid.UUID(nid),
        current_activity=_Row(name="WORK_STATION"),
        daily_schedule={},
        archetype=_Row(name="MERCHANT"),
        intrasystem_pose={**POSE, "x_pct": 22.0},
    )
    session = _FakeSession(players=[player], npcs=[npc])

    enriched = enrich_presence_with_live_pose(session, present)

    by_pid = {e["player_id"]: e for e in enriched}
    assert by_pid[pid]["pose"]["x_pct"] == 11.0
    assert "activity" not in by_pid[pid]  # NPC-only fields never added to a human entry
    assert by_pid[nid]["pose"]["x_pct"] == 22.0
    assert by_pid[nid]["activity"] == "WORK_STATION"


def test_entry_for_a_player_row_that_no_longer_exists_is_left_unchanged():
    """An orphaned presence entry (player deleted, or id mismatch) must not
    crash the whole enrichment pass -- matches the pre-existing NPC
    not-found behavior."""
    pid = str(uuid.uuid4())
    present = [_human_entry(pid)]
    session = _FakeSession(players=[])  # no matching Player row

    enriched = enrich_presence_with_live_pose(session, present)

    assert enriched == present


def test_non_dict_entries_pass_through_untouched():
    present = [None, "garbage", 42]
    session = _FakeSession()
    assert enrich_presence_with_live_pose(session, present) == present
