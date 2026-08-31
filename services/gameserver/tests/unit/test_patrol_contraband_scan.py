"""LEG-3148 — patrol contraband scan dispatches contraband_scan_hit offense.

Canon: police-forces.md:40,:179 — passing patrol scans hot cargo on sector
entry; P(detected) = 0.3 / max(1, evasion/10); hit routes ADR-0042 engagement.
"""
import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from src.models.sector import Sector
from src.services.movement_service import MovementService

_MODULE = "src.services.npc_engagement_service"
_CONTRABAND_CLS = "src.services.contraband_service.ContrabandService"


def make_player(*, player_id=None, current_ship=None, is_wanted_at_result=False):
    return SimpleNamespace(
        id=player_id or uuid.uuid4(),
        current_ship=current_ship,
        is_wanted_at=lambda faction_code, threshold: is_wanted_at_result,
    )


def make_ship(*, evasion=10):
    return SimpleNamespace(id=uuid.uuid4(), evasion=evasion)


def make_sector(defenses=None):
    return SimpleNamespace(
        id=uuid.uuid4(),
        sector_id=1301,
        players_present=[],
        type=SimpleNamespace(name="STANDARD"),
        hazard_level=0,
        defenses=defenses,
    )


def police_squad():
    return {
        "patrol_id": str(uuid.uuid4()),
        "faction_code": "terran_federation",
        "squad_kind": "federation_marshal",
        "npc_character_ids": [str(uuid.uuid4())],
        "ship_count": 2,
        "wanted_threshold": -500,
        "deployed_at": "2026-07-01T00:00:00Z",
    }


def build_service(sector, drones=()):
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


class TestPatrolContrabandScanDispatch:
    def test_detected_contraband_dispatches_contraband_scan_hit(self):
        ship = make_ship(evasion=10)
        player = make_player(current_ship=ship, is_wanted_at_result=False)
        sector = make_sector(defenses={"police_patrol_ships": [police_squad()]})
        service, mock_db = build_service(sector)

        with patch(_CONTRABAND_CLS) as mock_cls, \
             patch(f"{_MODULE}.route_engagement") as mock_route, \
             patch(f"{_MODULE}.dispatch_police_en_route_event"):
            mock_cls.return_value.roll_patrol_contraband_scan.return_value = {
                "scanned": True, "detected": True, "probability": 0.3
            }
            mock_route.return_value = SimpleNamespace(id=uuid.uuid4())
            service._check_for_encounters(player, sector.sector_id)

        mock_cls.return_value.roll_patrol_contraband_scan.assert_called_once_with(player, ship)
        mock_route.assert_called_once_with(
            mock_db, player, "contraband_scan_hit", sector
        )

    def test_clean_cargo_skips_contraband_dispatch(self):
        ship = make_ship()
        player = make_player(current_ship=ship, is_wanted_at_result=False)
        sector = make_sector(defenses={"police_patrol_ships": [police_squad()]})
        service, _mock_db = build_service(sector)

        with patch(_CONTRABAND_CLS) as mock_cls, \
             patch(f"{_MODULE}.route_engagement") as mock_route:
            mock_cls.return_value.roll_patrol_contraband_scan.return_value = {
                "scanned": False, "detected": False, "reason": "no_contraband"
            }
            service._check_for_encounters(player, sector.sector_id)

        mock_cls.return_value.roll_patrol_contraband_scan.assert_called_once()
        mock_route.assert_not_called()

    def test_no_patrol_presence_skips_scan(self):
        player = make_player(current_ship=make_ship())
        sector = make_sector(defenses={"police_patrol_ships": []})
        service, _mock_db = build_service(sector)

        with patch(_CONTRABAND_CLS) as mock_cls:
            service._check_for_encounters(player, sector.sector_id)

        mock_cls.assert_not_called()


class TestPatrolContrabandScanProbability:
    def test_evasion_reduces_detection_probability(self):
        from src.services.contraband_service import ContrabandService

        svc = ContrabandService(MagicMock())
        ship = SimpleNamespace(
            id=uuid.uuid4(),
            evasion=30,
            cargo={"contents": {"illegal:hazardous_transport": {"units": 5}}},
        )
        player = SimpleNamespace(id=uuid.uuid4())

        with patch.object(svc, "_worst_held_meta", return_value=("x", "y")), \
             patch("src.services.contraband_service._RNG") as mock_rng:
            mock_rng.random.return_value = 0.5
            outcome = svc.roll_patrol_contraband_scan(player, ship)

        assert outcome["scanned"] is True
        assert outcome["probability"] == 0.1  # 0.3 / (30/10)

    def test_clean_hold_not_scanned(self):
        from src.services.contraband_service import ContrabandService

        svc = ContrabandService(MagicMock())
        ship = SimpleNamespace(id=uuid.uuid4(), evasion=10, cargo={"contents": {}})
        player = SimpleNamespace(id=uuid.uuid4())

        outcome = svc.roll_patrol_contraband_scan(player, ship)
        assert outcome == {
            "scanned": False,
            "detected": False,
            "reason": "no_contraband",
        }
