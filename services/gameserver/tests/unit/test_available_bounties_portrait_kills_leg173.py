"""LEG-173 — available-bounties contract gains portrait_url + recent_kills.

Pins:
- Existing keys unchanged when a target has an active bounty.
- New keys always present (portrait_url may be null; recent_kills may be []).
- recent_kills populated from CombatLog PvP wins when rows exist.
"""
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

from src.models.combat_log import CombatLog
from src.models.player import Player
from src.services.bounty_service import BountyService


class _FakeQuery:
    def __init__(self, results):
        self._results = results
        self._filters = []

    def filter(self, *args):
        self._filters.extend(args)
        return self

    def order_by(self, *args):
        return self

    def limit(self, n):
        self._limit = n
        return self

    def all(self):
        rows = self._results
        if hasattr(self, "_limit"):
            rows = rows[: self._limit]
        return list(rows)


class _FakeDB:
    """Routes query(Model) to pre-seeded result lists."""

    def __init__(self, by_model):
        self._by_model = by_model

    def query(self, model):
        return _FakeQuery(self._by_model.get(model, []))


def test_available_bounties_includes_portrait_and_recent_kills_keys():
    target = SimpleNamespace(
        id=uuid4(),
        nickname="WantedOne",
        reputation_tier="Villain",
        personal_reputation=-800,
        current_sector_id=42,
        is_active=True,
        settings={
            "bounties": [
                {
                    "id": "b1",
                    "placed_by": str(uuid4()),
                    "placed_by_name": "Hunter",
                    "amount": 5000,
                    "type": "player",
                }
            ],
            "system_bounty_pot": 0,
        },
    )

    db = _FakeDB({Player: [target], CombatLog: []})
    svc = BountyService(db)
    # _get_system_bounties / pot helpers may query Player again — keep SimpleNamespace
    # compatible by stubbing pot read via settings only.
    result = svc.get_available_bounties(limit=20)

    assert result["success"] is True
    assert result["total_targets"] == 1
    row = result["bounties"][0]
    assert row["player_id"] == str(target.id)
    assert row["player_name"] == "WantedOne"
    assert row["reputation_tier"] == "Villain"
    assert row["total_bounty"] == 5000
    assert row["bounty_count"] == 1
    assert row["current_sector"] == 42
    assert "portrait_url" in row
    assert row["portrait_url"] is None
    assert "recent_kills" in row
    assert row["recent_kills"] == []
    assert "_player_id" not in row


def test_available_bounties_recent_kills_from_combat_log():
    target_id = uuid4()
    victim_id = uuid4()
    combat_id = uuid4()
    target = SimpleNamespace(
        id=target_id,
        nickname="Villain",
        reputation_tier="Villain",
        personal_reputation=-900,
        current_sector_id=7,
        is_active=True,
        settings={
            "bounties": [
                {
                    "id": "b2",
                    "placed_by": str(uuid4()),
                    "placed_by_name": "Fed",
                    "amount": 9000,
                    "type": "player",
                }
            ],
            "system_bounty_pot": 0,
        },
    )
    victim = SimpleNamespace(
        id=victim_id,
        nickname="Victim",
        username="Victim",
    )
    ts = datetime(2026, 8, 16, 12, 0, tzinfo=timezone.utc)
    log = SimpleNamespace(
        id=combat_id,
        attacker_id=target_id,
        defender_id=victim_id,
        outcome="attacker_win",
        sector_id=99,
        timestamp=ts,
        ended_at=None,
        started_at=ts,
    )

    # First Player query = active bounty scan; CombatLog query; then victim Player.in_
    class _RoutingDB:
        def __init__(self):
            self._player_calls = 0

        def query(self, model):
            if model is Player:
                self._player_calls += 1
                if self._player_calls == 1:
                    return _FakeQuery([target])
                return _FakeQuery([victim])
            if model is CombatLog:
                return _FakeQuery([log])
            return _FakeQuery([])

    svc = BountyService(_RoutingDB())
    result = svc.get_available_bounties(limit=20)
    row = result["bounties"][0]
    assert row["portrait_url"] is None
    assert len(row["recent_kills"]) == 1
    kill = row["recent_kills"][0]
    assert kill["combat_id"] == str(combat_id)
    assert kill["victim_id"] == str(victim_id)
    assert kill["victim_name"] == "Victim"
    assert kill["sector_id"] == 99
    assert kill["timestamp"] == ts.isoformat()
