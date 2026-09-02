"""Unit tests for player–NPC co-presence encounter recording (LEG-3961)."""

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from src.models.npc_character import NPCArchetype, NPCStatus
from src.services.movement_service import MovementService
from src.services.player_npc_encounter_service import (
    list_player_npc_encounters,
    record_npc_copresence_for_sector,
)


def _make_npc(npc_id=None, sector_id=1301, name="Vance", title="Marshal"):
    return SimpleNamespace(
        id=npc_id or uuid.uuid4(),
        current_sector_id=sector_id,
        status=NPCStatus.ON_DUTY,
        name=name,
        title=title,
        archetype=NPCArchetype.LAW_ENFORCEMENT,
    )


class TestRecordNpcCopresenceForSector:
    def test_creates_row_for_on_duty_npc_in_sector(self):
        player_id = uuid.uuid4()
        npc = _make_npc()
        db = MagicMock()

        db.query.return_value.filter.return_value.all.return_value = [npc]
        db.query.return_value.filter.return_value.first.return_value = None

        touched = record_npc_copresence_for_sector(db, player_id, 1301)

        assert touched == 1
        db.add.assert_called_once()
        added = db.add.call_args[0][0]
        assert added.player_id == player_id
        assert added.npc_character_id == npc.id
        assert added.count == 1
        assert added.last_sector_id == 1301
        db.commit.assert_called_once()

    def test_increments_existing_row(self):
        player_id = uuid.uuid4()
        npc = _make_npc()
        existing = SimpleNamespace(
            player_id=player_id,
            npc_character_id=npc.id,
            count=11,
            last_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            last_sector_id=1200,
        )
        db = MagicMock()

        query_npc = MagicMock()
        query_npc.filter.return_value.all.return_value = [npc]
        query_row = MagicMock()
        query_row.filter.return_value.first.return_value = existing
        db.query.side_effect = [query_npc, query_row]

        touched = record_npc_copresence_for_sector(db, player_id, 1301)

        assert touched == 1
        assert existing.count == 12
        assert existing.last_sector_id == 1301
        db.add.assert_not_called()
        db.commit.assert_called_once()

    def test_no_npcs_returns_zero_without_commit(self):
        db = MagicMock()
        db.query.return_value.filter.return_value.all.return_value = []

        touched = record_npc_copresence_for_sector(db, uuid.uuid4(), 1301)

        assert touched == 0
        db.commit.assert_not_called()


class TestListPlayerNpcEncounters:
    def test_formats_npc_name_with_title(self):
        player_id = uuid.uuid4()
        npc_id = uuid.uuid4()
        encounter = SimpleNamespace(
            npc_character_id=npc_id,
            count=3,
            last_at=datetime(2026, 9, 2, 10, 0, tzinfo=timezone.utc),
            last_sector_id=1301,
        )
        npc = SimpleNamespace(name="Vance", title="Marshal")
        db = MagicMock()
        db.query.return_value.join.return_value.filter.return_value.order_by.return_value.all.return_value = [
            (encounter, npc)
        ]

        rows = list_player_npc_encounters(db, player_id)

        assert rows == [
            {
                "npc_character_id": str(npc_id),
                "npc_name": "Marshal Vance",
                "count": 3,
                "last_at": "2026-09-02T10:00:00+00:00",
                "last_sector_id": 1301,
            }
        ]


class TestMovementServiceHook:
    def test_check_for_encounters_records_copresence(self):
        sector = SimpleNamespace(
            id=uuid.uuid4(),
            sector_id=1301,
            players_present=[],
            type=SimpleNamespace(name="STANDARD"),
            hazard_level=0,
            defenses={},
        )
        player = SimpleNamespace(id=uuid.uuid4(), current_ship=None)
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = sector
        db.query.return_value.filter.return_value.count.return_value = 0

        service = MovementService(db)
        with patch.object(
            service,
            "_maybe_record_npc_copresence",
        ) as record_mock:
            encounters = service._check_for_encounters(player, 1301)

        assert encounters == []
        record_mock.assert_called_once_with(player, 1301)

    def test_maybe_record_npc_copresence_swallows_errors(self):
        db = MagicMock()
        service = MovementService(db)
        player = SimpleNamespace(id=uuid.uuid4())

        with patch(
            "src.services.player_npc_encounter_service.record_npc_copresence_for_sector",
            side_effect=RuntimeError("boom"),
        ):
            service._maybe_record_npc_copresence(player, 1301)
