"""Unit tests for MovementService._sweep_strategic_analyst_detection (LEG-2757).

Strategic Analyst colonists on a destination-sector planet warn that planet's
owner via the same ``hostile_detected`` WS path as scanner-array detection.
Mock-session style mirrors test_patrol_encounters.py — no live DB required.
"""
import inspect
import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from src.models.colonist_profession import ProfessionType
from src.models.planet import Planet
from src.models.player import Player
from src.models.sector import Sector
from src.services.movement_service import MovementService


def _make_sector(sector_uuid=None, sector_num=1301):
    return SimpleNamespace(
        id=sector_uuid or uuid.uuid4(),
        sector_id=sector_num,
    )


def _make_planet(planet_id=None, sector_uuid=None, owner_id=None):
    return SimpleNamespace(
        id=planet_id or uuid.uuid4(),
        sector_uuid=sector_uuid or uuid.uuid4(),
        owner_id=owner_id,
    )


def _make_player(player_id=None, user_id=None):
    return SimpleNamespace(
        id=player_id or uuid.uuid4(),
        user_id=user_id or uuid.uuid4(),
        current_ship_id=uuid.uuid4(),
    )


def _build_service(sector, planets=(), owner=None):
    mock_db = MagicMock()
    owner = owner or _make_player()

    def query_side_effect(model):
        if model is Sector:
            q = MagicMock()
            q.filter.return_value.first.return_value = sector
            return q
        if model is Planet:
            q = MagicMock()
            q.filter.return_value.all.return_value = list(planets)
            return q
        if model is Player:
            q = MagicMock()
            q.filter.return_value.first.return_value = owner
            return q
        raise AssertionError(f"unexpected query target: {model!r}")

    mock_db.query.side_effect = query_side_effect
    return MovementService(mock_db), owner


class TestStrategicAnalystEarlyWarning:
    def test_hostile_arrival_warns_analyst_planet_owner_once(self):
        sector = _make_sector()
        owner = _make_player()
        mover = _make_player()
        planet_a = _make_planet(sector_uuid=sector.id, owner_id=owner.id)
        planet_b = _make_planet(sector_uuid=sector.id, owner_id=owner.id)
        service, _owner = _build_service(
            sector, planets=[planet_a, planet_b], owner=owner
        )

        analyst_counts = {ProfessionType.STRATEGIC_ANALYSTS: 5}
        dispatch_calls = []

        with patch(
            "src.services.profession_service.profession_counts",
            return_value=analyst_counts,
        ), patch.object(
            service,
            "_is_hostile_to_planet",
            return_value=True,
        ), patch.object(
            service,
            "_dispatch_hostile_detected",
            side_effect=lambda **kw: dispatch_calls.append(kw),
        ):
            service._sweep_strategic_analyst_detection(mover, sector.sector_id)

        assert len(dispatch_calls) == 1
        assert dispatch_calls[0]["owner_user_id"] == owner.user_id
        assert dispatch_calls[0]["sector_id"] == sector.sector_id
        assert dispatch_calls[0]["detection_range"] == 0
        assert dispatch_calls[0]["detected_player_id"] == mover.id

    def test_friendly_mover_skips_dispatch(self):
        sector = _make_sector()
        owner = _make_player()
        mover = _make_player()
        planet = _make_planet(sector_uuid=sector.id, owner_id=owner.id)
        service, _owner = _build_service(sector, planets=[planet], owner=owner)

        with patch(
            "src.services.profession_service.profession_counts",
            return_value={ProfessionType.STRATEGIC_ANALYSTS: 1},
        ), patch.object(
            service,
            "_is_hostile_to_planet",
            return_value=False,
        ), patch.object(
            service,
            "_dispatch_hostile_detected",
        ) as mock_dispatch:
            service._sweep_strategic_analyst_detection(mover, sector.sector_id)

        mock_dispatch.assert_not_called()

    def test_no_analysts_skips_dispatch(self):
        sector = _make_sector()
        owner = _make_player()
        mover = _make_player()
        planet = _make_planet(sector_uuid=sector.id, owner_id=owner.id)
        service, _owner = _build_service(sector, planets=[planet], owner=owner)

        with patch(
            "src.services.profession_service.profession_counts",
            return_value={},
        ), patch.object(
            service,
            "_dispatch_hostile_detected",
        ) as mock_dispatch:
            service._sweep_strategic_analyst_detection(mover, sector.sector_id)

        mock_dispatch.assert_not_called()

    def test_missing_sector_is_noop(self):
        mock_db = MagicMock()
        q = MagicMock()
        q.filter.return_value.first.return_value = None
        mock_db.query.return_value = q
        service = MovementService(mock_db)

        with patch.object(service, "_dispatch_hostile_detected") as mock_dispatch:
            service._sweep_strategic_analyst_detection(_make_player(), 9999)

        mock_dispatch.assert_not_called()

    def test_sweep_errors_are_swallowed(self):
        sector = _make_sector()
        owner = _make_player()
        planet = _make_planet(sector_uuid=sector.id, owner_id=owner.id)
        service, _owner = _build_service(sector, planets=[planet], owner=owner)

        with patch(
            "src.services.profession_service.profession_counts",
            side_effect=RuntimeError("boom"),
        ), patch.object(service, "_dispatch_hostile_detected") as mock_dispatch:
            service._sweep_strategic_analyst_detection(_make_player(), sector.sector_id)

        mock_dispatch.assert_not_called()


class TestStrategicAnalystWiring:
    def test_all_success_paths_call_analyst_sweep(self):
        source = inspect.getsource(MovementService.move_player_to_sector)
        assert source.count("_sweep_strategic_analyst_detection") == 3
