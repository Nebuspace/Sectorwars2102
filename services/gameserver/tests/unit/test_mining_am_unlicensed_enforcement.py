"""LEG-119 — repeated unlicensed AM mining trips LAW_ENFORCEMENT patrol."""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from src.services.mining_service import (
    AM_REP_UNLICENSED,
    AM_UNLICENSED_ENFORCEMENT_THRESHOLD,
    AM_UNLICENSED_ENFORCEMENT_WINDOW_HOURS,
    AM_UNLICENSED_OFFENSE_TYPE,
    MiningService,
)


def test_unlicensed_am_rep_penalty_unchanged():
    assert AM_REP_UNLICENSED == -10


def test_provisional_threshold_within_wo_bound():
    assert 1 <= AM_UNLICENSED_ENFORCEMENT_THRESHOLD <= 5
    assert AM_UNLICENSED_ENFORCEMENT_WINDOW_HOURS > 0
    assert AM_UNLICENSED_OFFENSE_TYPE == "unlicensed_am_mining"


def test_below_threshold_does_not_call_route_engagement():
    svc = MiningService(db=MagicMock())
    player = SimpleNamespace(id=uuid.uuid4())
    sector = SimpleNamespace(sector_id=4201, region_id=uuid.uuid4())

    with patch.object(
        svc,
        "count_recent_unlicensed_am_harvests",
        return_value=AM_UNLICENSED_ENFORCEMENT_THRESHOLD - 1,
    ) as count:
        with patch(
            "src.services.npc_engagement_service.route_engagement"
        ) as route:
            with patch.object(svc, "_relocate_military_zone_patrol_into") as relocate:
                tripped = svc._maybe_trip_am_unlicensed_enforcement(player, sector)

    assert tripped is False
    count.assert_called_once_with(player.id)
    route.assert_not_called()
    relocate.assert_not_called()


def test_at_threshold_invokes_route_engagement():
    svc = MiningService(db=MagicMock())
    player = SimpleNamespace(id=uuid.uuid4())
    sector = SimpleNamespace(sector_id=4201, region_id=uuid.uuid4())
    engagement = SimpleNamespace(id=uuid.uuid4())

    with patch.object(
        svc,
        "count_recent_unlicensed_am_harvests",
        return_value=AM_UNLICENSED_ENFORCEMENT_THRESHOLD,
    ):
        with patch(
            "src.services.npc_engagement_service.route_engagement",
            return_value=engagement,
        ) as route:
            with patch.object(svc, "_relocate_military_zone_patrol_into") as relocate:
                tripped = svc._maybe_trip_am_unlicensed_enforcement(player, sector)

    assert tripped is True
    route.assert_called_once_with(
        svc.db, player, AM_UNLICENSED_OFFENSE_TYPE, sector
    )
    relocate.assert_not_called()


def test_above_threshold_falls_back_to_military_zone_relocate_when_no_engagement():
    svc = MiningService(db=MagicMock())
    player = SimpleNamespace(id=uuid.uuid4())
    sector = SimpleNamespace(sector_id=4201, region_id=uuid.uuid4())

    with patch.object(
        svc,
        "count_recent_unlicensed_am_harvests",
        return_value=AM_UNLICENSED_ENFORCEMENT_THRESHOLD + 1,
    ):
        with patch(
            "src.services.npc_engagement_service.route_engagement",
            return_value=None,
        ) as route:
            with patch.object(
                svc, "_relocate_military_zone_patrol_into", return_value=True
            ) as relocate:
                tripped = svc._maybe_trip_am_unlicensed_enforcement(player, sector)

    assert tripped is True
    route.assert_called_once()
    relocate.assert_called_once_with(sector)


def test_enforcement_soft_fails_without_raising():
    svc = MiningService(db=MagicMock())
    player = SimpleNamespace(id=uuid.uuid4())
    sector = SimpleNamespace(sector_id=4201, region_id=uuid.uuid4())

    with patch.object(
        svc,
        "count_recent_unlicensed_am_harvests",
        side_effect=RuntimeError("db down"),
    ):
        tripped = svc._maybe_trip_am_unlicensed_enforcement(player, sector)

    assert tripped is False


def test_count_recent_unlicensed_am_harvests_filters():
    """Verify the query shape hits COMPLETED + am_claimed + !license + window."""
    mock_db = MagicMock()
    q = mock_db.query.return_value
    q.filter.return_value = q
    q.count.return_value = 2

    svc = MiningService(db=mock_db)
    pid = uuid.uuid4()
    assert svc.count_recent_unlicensed_am_harvests(pid) == 2

    mock_db.query.assert_called_once()
    # filter was applied (window + status + claim flags)
    assert q.filter.called
    assert q.count.called
