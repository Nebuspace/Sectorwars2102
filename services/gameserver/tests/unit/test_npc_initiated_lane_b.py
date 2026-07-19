"""WO-CMB-NPC-INITIATED-1 lane B — the police trigger (unit tests).

Scoped to the two things this WO actually adds: _maybe_initiate_police_combat
(mocking npc_combat_initiation_service.initiate_npc_combat at its OWN module
boundary — the module's own guard/resolution correctness is proven in
test_npc_initiated_entry.py, not re-proven here) and the scheduler's
npc_combat_initiated routing branch in _broadcast_events. NOT covered: a
full _sweep_one/sweep_pending_engagements integration test — no existing
unit-test harness exists for that function (only a DB-backed integration
test, tests/integration/test_npc_living_system.py); building one from
scratch (faking PendingEngagement rows, NPCStatus transitions, sector-
presence updates) is disproportionate to what this WO adds, which is a
2-line call-site insertion at each ARRIVED-transition branch, identical in
shape to the already-established _place_squad call convention.

Fixture-scoped assertions throughout.
"""
from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import src.services.npc_engagement_service as svc
from src.models.npc_character import NPCArchetype
from src.models.ship import ShipType
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


def _npc(npc_id=None, *, archetype=NPCArchetype.LAW_ENFORCEMENT, display_name="Marshal Vance"):
    npc_id = npc_id or uuid.uuid4()
    ship_id = uuid.uuid4()
    npc = SimpleNamespace(id=npc_id, ship_id=ship_id, display_name=display_name, archetype=archetype)
    ship = SimpleNamespace(id=ship_id, name="Federation Marshal Interdictor", type=ShipType.FAST_COURIER)
    return npc, ship


def _initiate_result(*, combat_result="ATTACKER_VICTORY", npc_ship_destroyed=False):
    return {
        "success": True,
        "combat_result": combat_result,
        "combat_log_id": str(uuid.uuid4()),
        "npc_ship_destroyed": npc_ship_destroyed,
        "defender_ship_destroyed": combat_result == "ATTACKER_VICTORY",
        "dead_npc": None,
        "cargo_stolen": {},
    }


class _FakeSessionForNpcLookup:
    """Serves the two DB reads _maybe_initiate_police_combat_inner issues
    around the initiate_npc_combat call it mocks at the module boundary:
    db.query(NPCCharacter).filter(id==...).first() (squad-member
    selection) and db.query(Ship).filter(id==...).first() (npc_ship, for
    the event build)."""
    def __init__(self, npc=None, npc_ship=None):
        self._npc = npc
        self._npc_ship = npc_ship
        self._model = None

    def query(self, model):
        self._model = model
        return self

    def filter(self, *criteria):
        return self

    def first(self):
        from src.models.npc_character import NPCCharacter
        from src.models.ship import Ship
        if self._model is NPCCharacter:
            return self._npc
        if self._model is Ship:
            return self._npc_ship
        return None


_INITIATE_TARGET = "src.services.npc_combat_initiation_service.initiate_npc_combat"


class TestMaybeInitiatePoliceCombat:
    def test_no_squad_returns_empty_list(self):
        engagement = _engagement(npc_squad_ids=[])
        player = _player()
        result = svc._maybe_initiate_police_combat(
            _FakeSessionForNpcLookup(), engagement, player, _sector()
        )
        assert result == []

    def test_trigger_derived_from_offense_type(self):
        npc, npc_ship = _npc()
        engagement = _engagement(npc_squad_ids=[str(npc.id)], offense_type="attack_innocent")
        player = _player()
        db = _FakeSessionForNpcLookup(npc, npc_ship)
        with patch(_INITIATE_TARGET, return_value=_initiate_result()) as mock_call:
            svc._maybe_initiate_police_combat(db, engagement, player, _sector())
        assert mock_call.call_args.kwargs["trigger"] == "police_attack_innocent"

    def test_initiate_npc_combat_called_with_first_squad_member(self):
        npc1, npc1_ship = _npc()
        npc2_id = uuid.uuid4()
        engagement = _engagement(npc_squad_ids=[str(npc1.id), str(npc2_id)])
        player = _player()
        sector = _sector()
        db = _FakeSessionForNpcLookup(npc1, npc1_ship)
        with patch(_INITIATE_TARGET, return_value=_initiate_result()) as mock_call:
            svc._maybe_initiate_police_combat(db, engagement, player, sector)
        called_db, called_npc, called_defender, called_sector = mock_call.call_args.args
        assert called_npc is npc1  # first squad id wins — Captain-first ordering
        assert called_defender is player
        assert called_sector is sector
        assert mock_call.call_args.kwargs["trigger_context"] == {"engagement_id": str(engagement.id)}

    def test_none_result_yields_no_event_no_rep_hooks(self):
        npc, npc_ship = _npc()
        engagement = _engagement(npc_squad_ids=[str(npc.id)])
        player = _player()
        db = _FakeSessionForNpcLookup(npc, npc_ship)
        with patch(_INITIATE_TARGET, return_value={"success": False, "message": "no"}), \
             patch(
                 "src.services.personal_reputation_service.PersonalReputationService"
             ) as mock_rep:
            result = svc._maybe_initiate_police_combat(db, engagement, player, _sector())
        assert result == []
        mock_rep.assert_not_called()

    def test_defender_fled_applies_evade_arrest_rep(self):
        npc, npc_ship = _npc()
        engagement = _engagement(npc_squad_ids=[str(npc.id)])
        player = _player()
        db = _FakeSessionForNpcLookup(npc, npc_ship)
        with patch(
            _INITIATE_TARGET, return_value=_initiate_result(combat_result="DEFENDER_FLED"),
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
        npc, npc_ship = _npc()
        engagement = _engagement(npc_squad_ids=[str(npc.id)])
        player = _player()
        db = _FakeSessionForNpcLookup(npc, npc_ship)
        with patch(
            _INITIATE_TARGET,
            return_value=_initiate_result(
                combat_result="DEFENDER_VICTORY", npc_ship_destroyed=True
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
        npc, npc_ship = _npc()
        engagement = _engagement(npc_squad_ids=[str(npc.id)])
        player = _player()
        db = _FakeSessionForNpcLookup(npc, npc_ship)
        with patch(
            _INITIATE_TARGET, return_value=_initiate_result(combat_result="ATTACKER_VICTORY"),
        ), patch(
            "src.services.personal_reputation_service.PersonalReputationService"
        ) as mock_rep_cls:
            svc._maybe_initiate_police_combat(db, engagement, player, _sector())
        mock_rep_cls.return_value.adjust_reputation.assert_not_called()

    def test_returns_the_built_event(self):
        npc, npc_ship = _npc()
        engagement = _engagement(npc_squad_ids=[str(npc.id)])
        player = _player()
        db = _FakeSessionForNpcLookup(npc, npc_ship)
        with patch(_INITIATE_TARGET, return_value=_initiate_result()):
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

        npc, _npc_ship = _npc()
        engagement = _engagement(npc_squad_ids=[str(npc.id)])
        player = _player()
        result = svc._maybe_initiate_police_combat(
            _ExplodingSession(), engagement, player, _sector()
        )
        assert result == []

    def test_rep_hook_exception_does_not_block_the_event(self):
        """A rep-adjustment failure is isolated (its own try/except) —
        the heads-up event still builds and returns."""
        npc, npc_ship = _npc()
        engagement = _engagement(npc_squad_ids=[str(npc.id)])
        player = _player()
        db = _FakeSessionForNpcLookup(npc, npc_ship)
        with patch(
            _INITIATE_TARGET, return_value=_initiate_result(combat_result="DEFENDER_FLED"),
        ), patch(
            "src.services.personal_reputation_service.PersonalReputationService",
            side_effect=RuntimeError("rep service down"),
        ):
            result = svc._maybe_initiate_police_combat(db, engagement, player, _sector())
        assert len(result) == 1


class TestSchedulerBroadcastRouting:
    def test_npc_combat_initiated_sends_personal_and_sector(self):
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
