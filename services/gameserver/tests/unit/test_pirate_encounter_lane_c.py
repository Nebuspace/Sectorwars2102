"""WO-CMB-NPC-INITIATED-1 lane C — the pirate trigger (unit tests).

Extends MovementService._check_for_encounters' pirate_patrol_ships leg
(the sibling of test_patrol_encounters.py's police_patrol_ships leg, which
that file's own docstring documents as the EXACT deferral this WO closes
for pirates specifically). Mock-session style mirrors test_patrol_
encounters.py: a MagicMock stands in for the SQLAlchemy session; no live
DB required. npc_combat_initiation_service.initiate_npc_combat /
emit_npc_combat_initiated / build_npc_combat_initiated_event are mocked at
the MODULE's own boundary (lane A/B's own resolution correctness is
proven elsewhere, not re-proven here) — these tests prove MY new
orchestration: the flee-threshold gate, the commit-then-emit sequencing,
and the never-raises contract.
"""
import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from src.models.npc_character import NPCCharacter
from src.models.sector import Sector
from src.models.ship import Ship, ShipSpecification, ShipType
from src.services.movement_service import MovementService

_MODULE = "src.services.npc_combat_initiation_service"


def make_player(player_id=None, ship=None, has_ship=True):
    return SimpleNamespace(
        id=player_id or uuid.uuid4(),
        is_wanted_at=lambda faction_code, threshold: False,
        current_ship=(ship if has_ship else None) or SimpleNamespace(type=ShipType.LIGHT_FREIGHTER),
    )


def make_sector(sector_uuid=None, sector_num=1301, defenses=None):
    return SimpleNamespace(
        id=sector_uuid or uuid.uuid4(),
        sector_id=sector_num,
        players_present=[],
        type=SimpleNamespace(name="STANDARD"),
        hazard_level=0,
        defenses=defenses,
    )


def _pirate_patrol(npc_ids):
    return {
        "patrol_id": str(uuid.uuid4()),
        "faction_code": "pirates",
        "squad_kind": "pirate_captain",
        "npc_character_ids": [str(i) for i in npc_ids],
        "ship_count": len(npc_ids),
    }


def build_service(sector, *, npc=None, npc_ship=None, player_spec_cost=100, pirate_spec_cost=100,
                   drones=()):
    from src.models.drone import Drone

    mock_db = MagicMock()
    # OUTSIDE query_side_effect so it persists across BOTH separate
    # db.query(ShipSpecification) calls _maybe_initiate_pirate_combat
    # issues (a counter re-initialized INSIDE the side-effect function
    # would reset to 0 on each call, always returning player_spec_cost).
    spec_calls = {"n": 0}

    def query_side_effect(model):
        if model is Sector:
            q = MagicMock()
            q.filter.return_value.first.return_value = sector
            return q
        if model is Drone:
            q = MagicMock()
            q.filter.return_value.count.return_value = len(drones)
            return q
        if model is NPCCharacter:
            q = MagicMock()
            q.filter.return_value.first.return_value = npc
            return q
        if model is Ship:
            q = MagicMock()
            q.filter.return_value.first.return_value = npc_ship
            return q
        if model is ShipSpecification:
            q = MagicMock()

            def _first():
                # First call = player's spec, second = pirate's — mirrors
                # the two sequential queries _maybe_initiate_pirate_combat
                # issues in that order.
                spec_calls["n"] += 1
                cost = player_spec_cost if spec_calls["n"] == 1 else pirate_spec_cost
                return SimpleNamespace(base_cost=cost)

            q.filter.return_value.first.side_effect = _first
            return q
        raise AssertionError(f"unexpected query target: {model!r}")

    mock_db.query.side_effect = query_side_effect
    return MovementService(mock_db), mock_db


def pirate_encounter(encounters):
    return next((e for e in encounters if e["type"] == "pirate_aggression"), None)


def _npc_result(npc_id, defender_id, **overrides):
    base = {
        "success": True,
        "combat_result": "ATTACKER_VICTORY",
        "combat_log_id": str(uuid.uuid4()),
        "npc_ship_destroyed": False,
        "defender_ship_destroyed": True,
        "dead_npc": None,
        "npc_id": str(npc_id),
        "npc_display_name": "Captain Blackwood",
        "npc_ship_id": str(uuid.uuid4()),
        "npc_ship_name": "The Marauder",
        "npc_ship_type": "LIGHT_FREIGHTER",
        "defender_id": str(defender_id),
        "defender_ship_id": str(uuid.uuid4()),
        "sector_id": 1301,
        "cargo_stolen": {},
    }
    base.update(overrides)
    return base


class TestPirateEncounterLeg:
    def test_no_pirate_patrol_key_no_encounter(self):
        sector = make_sector(defenses={})
        service, _db = build_service(sector)
        encounters = service._check_for_encounters(make_player(), sector.sector_id)
        assert pirate_encounter(encounters) is None

    def test_patrol_with_no_npc_ids_skipped(self):
        sector = make_sector(defenses={"pirate_patrol_ships": [{"faction_code": "pirates"}]})
        service, _db = build_service(sector)
        with patch(f"{_MODULE}.initiate_npc_combat") as mock_call:
            encounters = service._check_for_encounters(make_player(), sector.sector_id)
        assert pirate_encounter(encounters) is None
        mock_call.assert_not_called()

    def test_player_with_no_current_ship_skips_pirate_check_entirely(self):
        npc_id = uuid.uuid4()
        sector = make_sector(defenses={"pirate_patrol_ships": [_pirate_patrol([npc_id])]})
        service, _db = build_service(sector)
        player = make_player(has_ship=False)
        with patch(f"{_MODULE}.initiate_npc_combat") as mock_call:
            service._check_for_encounters(player, sector.sector_id)
        mock_call.assert_not_called()

    def test_pirate_flees_when_player_at_least_2x_tougher(self):
        npc_id = uuid.uuid4()
        npc = SimpleNamespace(id=npc_id, ship_id=uuid.uuid4())
        npc_ship = SimpleNamespace(id=npc.ship_id, is_destroyed=False, type=ShipType.LIGHT_FREIGHTER)
        sector = make_sector(defenses={"pirate_patrol_ships": [_pirate_patrol([npc_id])]})
        service, db = build_service(
            sector, npc=npc, npc_ship=npc_ship, player_spec_cost=200, pirate_spec_cost=100,
        )
        with patch(f"{_MODULE}.initiate_npc_combat") as mock_call:
            encounters = service._check_for_encounters(make_player(), sector.sector_id)
        mock_call.assert_not_called()
        assert pirate_encounter(encounters) is None
        db.commit.assert_not_called()

    def test_pirate_attacks_when_not_overwhelmingly_outmatched(self):
        npc_id = uuid.uuid4()
        player = make_player()
        npc = SimpleNamespace(id=npc_id, ship_id=uuid.uuid4())
        npc_ship = SimpleNamespace(id=npc.ship_id, is_destroyed=False, type=ShipType.LIGHT_FREIGHTER)
        sector = make_sector(defenses={"pirate_patrol_ships": [_pirate_patrol([npc_id])]})
        service, db = build_service(
            sector, npc=npc, npc_ship=npc_ship, player_spec_cost=100, pirate_spec_cost=100,
        )
        result = _npc_result(npc_id, player.id)
        with patch(
            f"{_MODULE}.initiate_npc_combat", return_value=result,
        ) as mock_initiate, patch(
            f"{_MODULE}.emit_npc_combat_initiated",
        ) as mock_emit, patch(
            f"{_MODULE}.build_npc_combat_initiated_event",
            return_value={"type": "npc_combat_initiated", "combat_id": result["combat_log_id"]},
        ):
            encounters = service._check_for_encounters(player, sector.sector_id)

        mock_initiate.assert_called_once()
        call_args = mock_initiate.call_args.args
        assert call_args[1] is npc  # the single selected combatant, not an id list
        assert call_args[2] is player
        assert mock_initiate.call_args.kwargs["trigger"] == "pirate_aggression"

        db.commit.assert_called_once()
        mock_emit.assert_called_once()

        encounter = pirate_encounter(encounters)
        assert encounter is not None
        assert encounter["combat_result"] == "ATTACKER_VICTORY"
        assert encounter["engagement"] == "fight"

    def test_only_first_pirate_patrol_processed(self):
        npc_id1, npc_id2 = uuid.uuid4(), uuid.uuid4()
        player = make_player()
        npc = SimpleNamespace(id=npc_id1, ship_id=uuid.uuid4())
        npc_ship = SimpleNamespace(id=npc.ship_id, is_destroyed=False, type=ShipType.LIGHT_FREIGHTER)
        sector = make_sector(defenses={
            "pirate_patrol_ships": [_pirate_patrol([npc_id1]), _pirate_patrol([npc_id2])]
        })
        service, db = build_service(
            sector, npc=npc, npc_ship=npc_ship, player_spec_cost=100, pirate_spec_cost=100,
        )
        result = _npc_result(npc_id1, player.id)
        with patch(
            f"{_MODULE}.initiate_npc_combat", return_value=result,
        ) as mock_initiate, patch(
            f"{_MODULE}.emit_npc_combat_initiated",
        ), patch(
            f"{_MODULE}.build_npc_combat_initiated_event",
            return_value={"type": "npc_combat_initiated"},
        ):
            service._check_for_encounters(player, sector.sector_id)
        mock_initiate.assert_called_once()  # not called twice for the second patrol

    def test_none_result_no_commit_no_encounter(self):
        npc_id = uuid.uuid4()
        player = make_player()
        npc = SimpleNamespace(id=npc_id, ship_id=uuid.uuid4())
        npc_ship = SimpleNamespace(id=npc.ship_id, is_destroyed=False, type=ShipType.LIGHT_FREIGHTER)
        sector = make_sector(defenses={"pirate_patrol_ships": [_pirate_patrol([npc_id])]})
        service, db = build_service(
            sector, npc=npc, npc_ship=npc_ship, player_spec_cost=100, pirate_spec_cost=100,
        )
        with patch(
            f"{_MODULE}.initiate_npc_combat", return_value={"success": False, "message": "no"},
        ):
            encounters = service._check_for_encounters(player, sector.sector_id)
        assert pirate_encounter(encounters) is None
        db.commit.assert_not_called()

    def test_never_raises_on_unexpected_error(self):
        npc_id = uuid.uuid4()
        player = make_player()
        sector = make_sector(defenses={"pirate_patrol_ships": [_pirate_patrol([npc_id])]})
        mock_db = MagicMock()
        mock_db.query.side_effect = RuntimeError("connection lost")
        service = MovementService(mock_db)
        # _check_for_encounters itself has other legs that would also hit
        # this exploding session; call the pirate helper directly to
        # isolate its own never-raises contract.
        result = service._maybe_initiate_pirate_combat(player, sector, [npc_id])
        assert result is None
