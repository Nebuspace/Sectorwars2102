"""LEG-97 — Frontier Coalition +5 mining hook (FRONTIER unclaimed only)."""

from src.models.zone import ZoneType
from src.services.mining_service import (
    FC_FACTION_NAME,
    FC_REP_UNCLAIMED_FRONTIER,
    MiningService,
)


def test_fc_rep_frontier_unclaimed_is_plus_five():
    assert (
        MiningService.frontier_coalition_rep_for_harvest(
            am_claimed=False, is_frontier=True
        )
        == FC_REP_UNCLAIMED_FRONTIER
        == 5
    )


def test_fc_rep_skipped_when_am_claimed_even_on_frontier():
    assert (
        MiningService.frontier_coalition_rep_for_harvest(
            am_claimed=True, is_frontier=True
        )
        == 0
    )


def test_fc_rep_skipped_on_non_frontier_unclaimed():
    assert (
        MiningService.frontier_coalition_rep_for_harvest(
            am_claimed=False, is_frontier=False
        )
        == 0
    )


def test_fc_constants_match_canon_name_and_magnitude():
    assert FC_FACTION_NAME == "Frontier Coalition"
    assert FC_REP_UNCLAIMED_FRONTIER == 5


def test_sector_is_frontier_uses_loaded_zone():
    class _Zone:
        zone_type = ZoneType.FRONTIER

    class _Sector:
        zone = _Zone()
        zone_id = None

    svc = MiningService(db=None)
    assert svc._sector_is_frontier(_Sector()) is True


def test_sector_is_frontier_false_when_no_zone():
    class _Sector:
        zone = None
        zone_id = None

    svc = MiningService(db=None)
    assert svc._sector_is_frontier(_Sector()) is False


def test_sector_is_frontier_loads_zone_by_id():
    class _Zone:
        zone_type = ZoneType.BORDER

    class _Q:
        def filter(self, *_a, **_k):
            return self

        def first(self):
            return _Zone()

    class _Db:
        def query(self, _model):
            return _Q()

    class _Sector:
        zone = None
        zone_id = "z1"

    svc = MiningService(db=_Db())
    assert svc._sector_is_frontier(_Sector()) is False
