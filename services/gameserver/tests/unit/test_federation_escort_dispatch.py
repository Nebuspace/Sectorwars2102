"""Federation High / Public-Enemy anonymous Defender escorts (LEG-296).

Canon: sw2102-docs/FEATURES/gameplay/police-forces.md:74-96.
Named Marshal counts stay in _federation_squad_size; escorts are extra hulls.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

from src.models.npc_character import NPCStatus
from src.services.npc_engagement_service import (
    FEDERATION_ESCORT_DUTY_ROLE,
    _federation_squad_size,
    _is_federation_escort,
    _release_squad,
    attach_escort_ids_to_police_squad_row,
)
from src.services.npc_spawn_service import POLICE_PATROL_DEFENSES_KEY


class TestFederationEscortCounts:
    def _player(self, rep):
        return SimpleNamespace(personal_reputation=rep)

    def test_low_and_medium_have_zero_escorts(self):
        assert _federation_squad_size(self._player(-1)) == (1, False, 0)
        assert _federation_squad_size(self._player(-249)) == (1, False, 0)
        assert _federation_squad_size(self._player(-250)) == (2, False, 0)
        assert _federation_squad_size(self._player(-499)) == (2, False, 0)

    def test_high_one_escort_no_captain(self):
        named, captain, escorts = _federation_squad_size(self._player(-500))
        assert (named, captain, escorts) == (3, False, 1)
        named, captain, escorts = _federation_squad_size(self._player(-749))
        assert (named, captain, escorts) == (3, False, 1)

    def test_public_enemy_two_escorts_and_captain(self):
        named, captain, escorts = _federation_squad_size(self._player(-750))
        assert (named, captain, escorts) == (3, True, 2)


class TestPoliceSquadRowEscortAttach:
    def test_appends_onto_matching_marshal_row(self):
        marshal = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
        escort = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
        defenses = {
            POLICE_PATROL_DEFENSES_KEY: [
                {
                    "patrol_id": "p1",
                    "squad_kind": "federation_marshal",
                    "npc_character_ids": [marshal],
                    "ship_count": 1,
                }
            ]
        }
        out = attach_escort_ids_to_police_squad_row(defenses, [marshal], [escort])
        row = out[POLICE_PATROL_DEFENSES_KEY][0]
        assert marshal in row["npc_character_ids"]
        assert escort in row["npc_character_ids"]
        assert row["escort_npc_ids"] == [escort]
        assert row["ship_count"] == 2

    def test_dispatch_row_when_no_marshal_row(self):
        marshal = "m1"
        escorts = ["e1", "e2"]
        out = attach_escort_ids_to_police_squad_row({}, [marshal], escorts)
        rows = out[POLICE_PATROL_DEFENSES_KEY]
        assert len(rows) == 1
        assert rows[0]["npc_character_ids"] == [marshal, "e1", "e2"]
        assert rows[0]["escort_npc_ids"] == escorts
        assert rows[0]["ship_count"] == 3

    def test_idempotent_second_attach(self):
        marshal = "m1"
        escort = "e1"
        once = attach_escort_ids_to_police_squad_row({}, [marshal], [escort])
        twice = attach_escort_ids_to_police_squad_row(
            once, [marshal], [escort]
        )
        ids = twice[POLICE_PATROL_DEFENSES_KEY][0]["npc_character_ids"]
        assert ids.count(escort) == 1


class TestReleaseRetiresEscortsNotMarshals:
    def test_escort_retired_marshal_returns_to_duty(self):
        marshal_id = "11111111-1111-1111-1111-111111111111"
        escort_id = "22222222-2222-2222-2222-222222222222"
        marshal = SimpleNamespace(
            duty_role="primary_federation_marshal",
            status=NPCStatus.ENGAGED,
            last_seen_at=None,
        )
        escort = SimpleNamespace(
            duty_role=FEDERATION_ESCORT_DUTY_ROLE,
            status=NPCStatus.ENGAGED,
            last_seen_at=None,
        )
        assert _is_federation_escort(escort)
        assert not _is_federation_escort(marshal)

        db = MagicMock()
        db.query.return_value.filter.return_value.first.side_effect = [
            marshal,
            escort,
        ]
        engagement = SimpleNamespace(npc_squad_ids=[marshal_id, escort_id])
        _release_squad(db, engagement)
        assert marshal.status == NPCStatus.ON_DUTY
        assert escort.status == NPCStatus.RETIRED


class TestSpawnSkipsWithoutDefenderSpec:
    def test_missing_spec_returns_empty(self):
        from src.services.npc_engagement_service import _spawn_federation_escorts

        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = None
        sector = SimpleNamespace(sector_id=1, defenses={})
        assert _spawn_federation_escorts(db, 2, sector, ["m1"]) == []
