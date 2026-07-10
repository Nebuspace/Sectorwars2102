"""WO-DRIFT-combat-patrol-entry-dispatch — the police movement-side dispatch
wiring (unit tests).

Extends test_patrol_encounters.py's police_patrol_ships leg: that file's own
docstring documents the leg as detection-only ("this leg stops at
detection"), the exact deferral this WO closes. Mock-session style mirrors
test_patrol_encounters.py / test_pirate_encounter_lane_c.py: a MagicMock
stands in for the SQLAlchemy session; npc_engagement_service.route_engagement
is mocked at its OWN module boundary (route_engagement's own dispatch
correctness -- PendingEngagement creation, arrival_turn_threshold = turn+2,
the 5-turn per-offense-type cooldown -- is proven end-to-end against a real
DB in tests/integration/test_npc_living_system.py's TestPoliceEngagement, not
re-proven here). These tests prove MY new orchestration: a matched patrol
calls route_engagement with the right args exactly once (not once per
matched squad), a real dispatch commits, a None/failed dispatch degrades
silently, and this path never reaches for _maybe_initiate_police_combat (the
sweep's own ARRIVED-transition combat trigger -- calling it here would
attack the player through a squad that hasn't been placed in this sector
yet, skipping ADR-0042's 2-turn arrival delay). The pre-existing
"faction_patrol" informational encounter dict is pinned unchanged alongside
the new dispatch side effect.
"""
import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from src.models.sector import Sector
from src.services.movement_service import MovementService

_MODULE = "src.services.npc_engagement_service"


def make_player(player_id=None, is_wanted_at_result=False, is_wanted_at=None):
    return SimpleNamespace(
        id=player_id or uuid.uuid4(),
        is_wanted_at=is_wanted_at or (lambda faction_code, threshold: is_wanted_at_result),
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


def police_squad(faction_code="terran_federation", squad_kind="federation_marshal",
                  wanted_threshold=-500, ship_count=2):
    return {
        "patrol_id": str(uuid.uuid4()),
        "faction_code": faction_code,
        "squad_kind": squad_kind,
        "npc_character_ids": [str(uuid.uuid4()), str(uuid.uuid4())],
        "ship_count": ship_count,
        "wanted_threshold": wanted_threshold,
        "deployed_at": "2026-07-01T00:00:00Z",
    }


def build_service(sector, drones=()):
    """MovementService over a mock session -- mirrors test_patrol_
    encounters.py's build_service, extended to hand the mock db back so
    tests can assert on mock_db.commit."""
    from src.models.drone import Drone

    mock_db = MagicMock()

    def query_side_effect(model):
        if model is Sector:
            q = MagicMock()
            q.filter.return_value.first.return_value = sector
            return q
        if model is Drone:
            q = MagicMock()
            q.filter.return_value.count.return_value = len(drones)
            return q
        raise AssertionError(f"unexpected query target: {model!r}")

    mock_db.query.side_effect = query_side_effect
    return MovementService(mock_db), mock_db


def patrol_encounter(encounters):
    return next((e for e in encounters if e["type"] == "faction_patrol"), None)


class TestPoliceEngagementDispatch:
    def test_matched_patrol_dispatches_wanted_status_engagement(self):
        """The literal WO ask: a wanted player entering a patrolled sector
        calls route_engagement(db, player, "wanted_status", sector) -- the
        SAME offense_type combat_service.py's PvP path already uses
        (combat_service.py's police-engagement-routing block), which is
        also what makes the cooldown-based dedup below work for free."""
        player = make_player(is_wanted_at_result=True)
        sector = make_sector(defenses={"police_patrol_ships": [police_squad()]})
        service, mock_db = build_service(sector)

        with patch(f"{_MODULE}.route_engagement") as mock_route:
            mock_route.return_value = SimpleNamespace(id=uuid.uuid4())
            encounters = service._check_for_encounters(player, sector.sector_id)

        mock_route.assert_called_once_with(mock_db, player, "wanted_status", sector)
        # Detection leg is untouched by the new dispatch side effect --
        # byte-identical to test_patrol_encounters.py's own pin.
        assert patrol_encounter(encounters) == {
            "type": "faction_patrol",
            "faction": "terran_federation",
            "squad": "federation_marshal",
            "ship_count": 2,
            "threat_level": "high",
            "engagement": "pursuit",
        }

    def test_successful_dispatch_commits(self):
        player = make_player(is_wanted_at_result=True)
        sector = make_sector(defenses={"police_patrol_ships": [police_squad()]})
        service, mock_db = build_service(sector)

        with patch(f"{_MODULE}.route_engagement") as mock_route:
            mock_route.return_value = SimpleNamespace(id=uuid.uuid4())
            service._check_for_encounters(player, sector.sector_id)

        mock_db.commit.assert_called_once()

    def test_no_engagement_returned_skips_commit(self):
        """route_engagement returns None on cooldown / out-of-jurisdiction /
        internal failure (it never raises into its caller) -- no
        PendingEngagement means nothing to commit."""
        player = make_player(is_wanted_at_result=True)
        sector = make_sector(defenses={"police_patrol_ships": [police_squad()]})
        service, mock_db = build_service(sector)

        with patch(f"{_MODULE}.route_engagement", return_value=None):
            service._check_for_encounters(player, sector.sector_id)

        mock_db.commit.assert_not_called()

    def test_unmatched_player_never_dispatches(self):
        player = make_player(is_wanted_at_result=False)
        sector = make_sector(defenses={"police_patrol_ships": [police_squad()]})
        service, mock_db = build_service(sector)

        with patch(f"{_MODULE}.route_engagement") as mock_route:
            service._check_for_encounters(player, sector.sector_id)

        mock_route.assert_not_called()
        mock_db.commit.assert_not_called()

    def test_multiple_matched_squads_dispatch_exactly_once(self):
        """Jurisdiction is derived from the SECTOR (route_engagement's own
        jurisdiction_of), not the individual patrol entry -- two matched
        squad rows in one sector (e.g. a Federation entry and a Sentinel
        entry) must not fire the dispatch twice in a single
        _check_for_encounters call. Both informational pings still
        surface; the cooldown would have absorbed a second real dispatch
        anyway, but this pins the correct SHAPE, not just a safe one."""
        player = make_player(is_wanted_at_result=True)
        sector = make_sector(defenses={"police_patrol_ships": [
            police_squad(faction_code="terran_federation", squad_kind="federation_marshal"),
            police_squad(faction_code="galactic_concord", squad_kind="nexus_sentinel"),
        ]})
        service, mock_db = build_service(sector)

        with patch(f"{_MODULE}.route_engagement") as mock_route:
            mock_route.return_value = SimpleNamespace(id=uuid.uuid4())
            encounters = service._check_for_encounters(player, sector.sector_id)

        assert mock_route.call_count == 1
        matched = [e for e in encounters if e["type"] == "faction_patrol"]
        assert len(matched) == 2

    def test_dispatch_failure_never_breaks_the_move(self):
        """route_engagement itself never raises into its caller (its own
        docstring/contract), but the commit() call in
        _maybe_dispatch_police_engagement could still fail -- prove that
        propagates nowhere, matching _maybe_initiate_pirate_combat's
        sibling never-raise contract (the move itself already committed
        by the time this leg runs)."""
        player = make_player(is_wanted_at_result=True)
        sector = make_sector(defenses={"police_patrol_ships": [police_squad()]})
        service, mock_db = build_service(sector)
        mock_db.commit.side_effect = RuntimeError("boom")

        with patch(f"{_MODULE}.route_engagement") as mock_route:
            mock_route.return_value = SimpleNamespace(id=uuid.uuid4())
            # Must complete without raising.
            encounters = service._check_for_encounters(player, sector.sector_id)

        assert patrol_encounter(encounters) is not None

    def test_no_arrived_combat_trigger_invoked(self):
        """Dispatch-only pin: _maybe_initiate_police_combat is the sweep's
        own ARRIVED-transition trigger, never movement's -- calling it here
        would attack the player through a squad still ENGAGED_PENDING_
        ARRIVAL, skipping ADR-0042's 2-turn arrival delay entirely."""
        player = make_player(is_wanted_at_result=True)
        sector = make_sector(defenses={"police_patrol_ships": [police_squad()]})
        service, mock_db = build_service(sector)

        with patch(f"{_MODULE}.route_engagement") as mock_route, \
             patch(f"{_MODULE}._maybe_initiate_police_combat") as mock_combat:
            mock_route.return_value = SimpleNamespace(id=uuid.uuid4())
            service._check_for_encounters(player, sector.sector_id)

        mock_combat.assert_not_called()
