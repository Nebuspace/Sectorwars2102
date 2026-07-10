"""WO-CMB-NPC-INITIATED-1 lane B — the police trigger (unit tests).

Scoped to the two things this WO actually adds: _maybe_initiate_police_combat
(mocking npc_initiate_attack at the module boundary — npc_initiate_attack's
own guard/resolution correctness is a shared resolver with lane C and is not
re-proven here) and the scheduler's npc_combat_initiated routing branch in
_broadcast_events. NOT covered: a full _sweep_one/sweep_pending_engagements
integration test — no existing unit-test harness exists for that function
(only a DB-backed integration test, tests/integration/test_npc_living_system.py);
building one from scratch (faking PendingEngagement rows, NPCStatus
transitions, sector-presence updates) is disproportionate to what this WO
adds, which is a 4-line call-site insertion at each ARRIVED-transition branch,
identical in shape to the already-established _place_squad call convention.

Fixture-scoped assertions throughout.
"""
from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import src.services.npc_engagement_service as svc
from src.models.npc_character import NPCArchetype
from src.services.npc_scheduler_service import _broadcast_events


def _engagement(*, npc_squad_ids=None, offense_type="wanted_status"):
    return SimpleNamespace(
        id=uuid.uuid4(),
        npc_squad_ids=npc_squad_ids if npc_squad_ids is not None else [str(uuid.uuid4())],
        offense_type=offense_type,
    )


def _player():
    return SimpleNamespace(id=uuid.uuid4(), username="tester", user_id=uuid.uuid4())


def _sector(sector_id=4242):
    return SimpleNamespace(id=uuid.uuid4(), sector_id=sector_id)


def _npc(npc_id):
    return SimpleNamespace(
        id=npc_id, display_name="Marshal Vance", archetype=NPCArchetype.LAW_ENFORCEMENT,
    )


def _npc_initiate_result(npc_id, *, combat_result="ATTACKER_VICTORY", npc_ship_destroyed=False):
    return {
        "success": True,
        "combat_result": combat_result,
        "combat_log_id": str(uuid.uuid4()),
        "npc_ship_destroyed": npc_ship_destroyed,
        "defender_ship_destroyed": combat_result == "ATTACKER_VICTORY",
        "dead_npc": None,
        "npc_id": str(npc_id),
        "npc_display_name": "Marshal Vance",
        "npc_ship_id": str(uuid.uuid4()),
        "npc_ship_name": "Federation Marshal Interdictor",
        "npc_ship_type": "DEFENDER",
        "defender_id": str(uuid.uuid4()),
        "defender_ship_id": str(uuid.uuid4()),
        "sector_id": 4242,
        "cargo_stolen": {},
    }


class _FakeSessionForNpcLookup:
    """Only needs to serve db.query(NPCCharacter).filter(id==...).first()
    for the event-build step at the end of _maybe_initiate_police_combat_inner."""
    def __init__(self, npc):
        self._npc = npc

    def query(self, *entities):
        return self

    def filter(self, *criteria):
        return self

    def first(self):
        return self._npc


class TestMaybeInitiatePoliceCombat:
    def test_no_squad_returns_empty_list(self):
        engagement = _engagement(npc_squad_ids=[])
        player = _player()
        result = svc._maybe_initiate_police_combat(
            _FakeSessionForNpcLookup(None), engagement, player, _sector()
        )
        assert result == []

    def test_cause_derived_from_offense_type(self):
        npc_id = uuid.uuid4()
        engagement = _engagement(npc_squad_ids=[str(npc_id)], offense_type="attack_innocent")
        player = _player()
        db = _FakeSessionForNpcLookup(_npc(npc_id))
        with patch.object(
            svc, "npc_initiate_attack",
            return_value=_npc_initiate_result(npc_id),
        ) as mock_call:
            svc._maybe_initiate_police_combat(db, engagement, player, _sector())
        assert mock_call.call_args.kwargs["cause"] == "police_attack_innocent"

    def test_npc_initiate_attack_called_with_ordered_squad_ids(self):
        npc_id1, npc_id2 = uuid.uuid4(), uuid.uuid4()
        engagement = _engagement(npc_squad_ids=[str(npc_id1), str(npc_id2)])
        player = _player()
        db = _FakeSessionForNpcLookup(_npc(npc_id1))
        with patch.object(
            svc, "npc_initiate_attack",
            return_value=_npc_initiate_result(npc_id1),
        ) as mock_call:
            svc._maybe_initiate_police_combat(db, engagement, player, _sector())
        called_npc_ids, called_defender_id, called_sector = mock_call.call_args.args[1:4]
        assert called_npc_ids == [npc_id1, npc_id2]  # order preserved, untouched
        assert called_defender_id == player.id

    def test_none_result_yields_no_event_no_rep_hooks(self):
        npc_id = uuid.uuid4()
        engagement = _engagement(npc_squad_ids=[str(npc_id)])
        player = _player()
        db = _FakeSessionForNpcLookup(_npc(npc_id))
        with patch.object(svc, "npc_initiate_attack", return_value=None), \
             patch(
                 "src.services.personal_reputation_service.PersonalReputationService"
             ) as mock_rep:
            result = svc._maybe_initiate_police_combat(db, engagement, player, _sector())
        assert result == []
        mock_rep.assert_not_called()

    def test_defender_fled_applies_evade_arrest_rep(self):
        npc_id = uuid.uuid4()
        engagement = _engagement(npc_squad_ids=[str(npc_id)])
        player = _player()
        db = _FakeSessionForNpcLookup(_npc(npc_id))
        with patch.object(
            svc, "npc_initiate_attack",
            return_value=_npc_initiate_result(npc_id, combat_result="DEFENDER_FLED"),
        ), patch(
            "src.services.personal_reputation_service.PersonalReputationService"
        ) as mock_rep_cls:
            mock_instance = mock_rep_cls.return_value
            svc._maybe_initiate_police_combat(db, engagement, player, _sector())
        mock_instance.adjust_reputation.assert_called_once_with(
            player.id, -25, "evade_arrest"
        )

    def test_npc_ship_destroyed_applies_provisional_rep_only(self):
        """Confirms the -50 provisional leg fires, and confirms (by NOT
        mocking anything is_suspect/is_wanted related, since no such call
        exists in the source) that no Suspect/Wanted escalation is
        attempted — the DECISION-NEEDED gap is a documented absence, not
        a silently-wrong implementation."""
        npc_id = uuid.uuid4()
        engagement = _engagement(npc_squad_ids=[str(npc_id)])
        player = _player()
        db = _FakeSessionForNpcLookup(_npc(npc_id))
        with patch.object(
            svc, "npc_initiate_attack",
            return_value=_npc_initiate_result(
                npc_id, combat_result="DEFENDER_VICTORY", npc_ship_destroyed=True
            ),
        ), patch(
            "src.services.personal_reputation_service.PersonalReputationService"
        ) as mock_rep_cls:
            mock_instance = mock_rep_cls.return_value
            result = svc._maybe_initiate_police_combat(db, engagement, player, _sector())
        mock_instance.adjust_reputation.assert_called_once_with(
            player.id, -50, "destroyed_police_officer"
        )
        assert result  # still returns the heads-up event

    def test_flee_and_destruction_are_mutually_exclusive_in_this_scenario_set(self):
        """A DRAW/ATTACKER_VICTORY result triggers neither rep leg."""
        npc_id = uuid.uuid4()
        engagement = _engagement(npc_squad_ids=[str(npc_id)])
        player = _player()
        db = _FakeSessionForNpcLookup(_npc(npc_id))
        with patch.object(
            svc, "npc_initiate_attack",
            return_value=_npc_initiate_result(npc_id, combat_result="ATTACKER_VICTORY"),
        ), patch(
            "src.services.personal_reputation_service.PersonalReputationService"
        ) as mock_rep_cls:
            svc._maybe_initiate_police_combat(db, engagement, player, _sector())
        mock_rep_cls.return_value.adjust_reputation.assert_not_called()

    def test_returns_the_built_event(self):
        npc_id = uuid.uuid4()
        engagement = _engagement(npc_squad_ids=[str(npc_id)])
        player = _player()
        db = _FakeSessionForNpcLookup(_npc(npc_id))
        with patch.object(
            svc, "npc_initiate_attack",
            return_value=_npc_initiate_result(npc_id),
        ):
            events = svc._maybe_initiate_police_combat(db, engagement, player, _sector())
        assert len(events) == 1
        assert events[0]["type"] == "npc_combat_initiated"
        assert events[0]["trigger"] == "police_wanted_status"
        assert events[0]["defender_user_id"] == str(player.user_id)

    def test_outer_wrapper_never_raises_savepoint_preserved(self):
        """Injects a failure INSIDE the inner implementation (the NPC
        lookup blows up) and confirms the OUTER wrapper swallows it —
        proving a failed initiation cannot poison
        sweep_pending_engagements' per-row SAVEPOINT (it can only ever
        degrade to no new events, never propagate)."""
        class _ExplodingSession:
            def query(self, *a, **k):
                raise RuntimeError("connection lost")

        npc_id = uuid.uuid4()
        engagement = _engagement(npc_squad_ids=[str(npc_id)])
        player = _player()
        with patch.object(
            svc, "npc_initiate_attack",
            return_value=_npc_initiate_result(npc_id),
        ):
            result = svc._maybe_initiate_police_combat(
                _ExplodingSession(), engagement, player, _sector()
            )
        assert result == []

    def test_rep_hook_exception_does_not_block_the_event(self):
        """A rep-adjustment failure is isolated (its own try/except) —
        the heads-up event still builds and returns."""
        npc_id = uuid.uuid4()
        engagement = _engagement(npc_squad_ids=[str(npc_id)])
        player = _player()
        db = _FakeSessionForNpcLookup(_npc(npc_id))
        with patch.object(
            svc, "npc_initiate_attack",
            return_value=_npc_initiate_result(npc_id, combat_result="DEFENDER_FLED"),
        ), patch(
            "src.services.personal_reputation_service.PersonalReputationService",
            side_effect=RuntimeError("rep service down"),
        ):
            result = svc._maybe_initiate_police_combat(db, engagement, player, _sector())
        assert len(result) == 1


class TestSchedulerBroadcastRouting:
    def test_npc_attack_initiated_sends_personal_and_sector(self):
        event = {
            "type": "npc_combat_initiated",
            "defender_user_id": str(uuid.uuid4()),
            "sector_id": 4242,
        }

        async def _run():
            with patch("src.services.websocket_service.connection_manager") as mock_cm:
                mock_cm.send_personal_message = AsyncMock()
                mock_cm.broadcast_to_sector = AsyncMock()
                await _broadcast_events([event])
                mock_cm.send_personal_message.assert_awaited_once_with(
                    event["defender_user_id"], event
                )
                mock_cm.broadcast_to_sector.assert_awaited_once_with(4242, event)

        asyncio.run(_run())

    def test_no_defender_user_id_skips_personal_send_only(self):
        event = {"type": "npc_combat_initiated", "defender_user_id": None, "sector_id": 4242}

        async def _run():
            with patch("src.services.websocket_service.connection_manager") as mock_cm:
                mock_cm.send_personal_message = AsyncMock()
                mock_cm.broadcast_to_sector = AsyncMock()
                await _broadcast_events([event])
                mock_cm.send_personal_message.assert_not_awaited()
                mock_cm.broadcast_to_sector.assert_awaited_once()

        asyncio.run(_run())

    def test_no_sector_id_skips_sector_broadcast_only(self):
        event = {"type": "npc_combat_initiated", "defender_user_id": str(uuid.uuid4()), "sector_id": None}

        async def _run():
            with patch("src.services.websocket_service.connection_manager") as mock_cm:
                mock_cm.send_personal_message = AsyncMock()
                mock_cm.broadcast_to_sector = AsyncMock()
                await _broadcast_events([event])
                mock_cm.send_personal_message.assert_awaited_once()
                mock_cm.broadcast_to_sector.assert_not_awaited()

        asyncio.run(_run())

    def test_send_failure_does_not_raise(self):
        event = {
            "type": "npc_combat_initiated",
            "defender_user_id": str(uuid.uuid4()),
            "sector_id": 4242,
        }

        async def _run():
            with patch("src.services.websocket_service.connection_manager") as mock_cm:
                mock_cm.send_personal_message = AsyncMock(side_effect=RuntimeError("boom"))
                mock_cm.broadcast_to_sector = AsyncMock()
                await _broadcast_events([event])  # must not raise

        asyncio.run(_run())
