"""LEG-430 — nearest AM-flagged refinery + ore buy_price (mining.md:254)."""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from src.services.mining_service import MiningService


def _station(*, sid, name, sector_id, sector_uuid, faction, refining):
    return SimpleNamespace(
        id=sid,
        name=name,
        sector_id=sector_id,
        sector_uuid=sector_uuid,
        faction_affiliation=faction,
        services={"refining_facility": refining} if refining is not None else {},
        commodities={
            "ore": {"quantity": 50, "capacity": 100, "base_price": 20},
        },
    )


def test_finds_eligible_am_refinery_with_hops_and_price():
    origin_pk = uuid.uuid4()
    near_pk = uuid.uuid4()
    far_pk = uuid.uuid4()
    player_id = uuid.uuid4()
    near_id = uuid.uuid4()
    far_id = uuid.uuid4()

    player = SimpleNamespace(
        id=player_id, current_sector_id=10, current_region_id=None
    )
    origin = SimpleNamespace(id=origin_pk, sector_id=10, region_id=None)
    near = _station(
        sid=near_id,
        name="AM Near",
        sector_id=11,
        sector_uuid=near_pk,
        faction="Astral Mining Consortium",
        refining=True,
    )
    far = _station(
        sid=far_id,
        name="AM Far",
        sector_id=12,
        sector_uuid=far_pk,
        faction="Astral Mining Consortium",
        refining=True,
    )
    non_am = _station(
        sid=uuid.uuid4(),
        name="Fed Port",
        sector_id=13,
        sector_uuid=uuid.uuid4(),
        faction="Terran Federation",
        refining=True,
    )
    am_no_refinery = _station(
        sid=uuid.uuid4(),
        name="AM Hub",
        sector_id=14,
        sector_uuid=uuid.uuid4(),
        faction="Astral Mining Consortium",
        refining=False,
    )

    db = MagicMock()

    def query_side_effect(model):
        q = MagicMock()
        name = getattr(model, "__name__", str(model))
        if name == "Player":
            q.filter.return_value.first.return_value = player
        elif name == "Sector":
            q.filter.return_value.first.return_value = origin
            # region_id.is_(None) chain
            q.filter.return_value.filter.return_value.first.return_value = origin
        elif name == "Station":
            q.filter.return_value.all.return_value = [
                near,
                far,
                non_am,
                am_no_refinery,
            ]
        else:
            q.filter.return_value.first.return_value = None
            q.filter.return_value.all.return_value = []
        return q

    db.query.side_effect = query_side_effect

    adjacency = {
        origin_pk: [near_pk],
        near_pk: [origin_pk, far_pk],
        far_pk: [near_pk],
    }

    with patch(
        "src.services.contract_generator._load_directed_sector_graph",
        return_value=({}, adjacency),
    ), patch(
        "src.services.trading_service.TradingService.calculate_dynamic_price",
        return_value=42,
    ):
        out = MiningService(db).find_nearest_am_refinery(player_id)

    assert out["found"] is True
    assert out["station"]["id"] == str(near_id)
    assert out["station"]["name"] == "AM Near"
    assert out["hop_distance"] == 1
    assert out["ore_buy_price"] == 42
    assert out["reason"] is None


def test_excludes_non_am_and_non_refinery_honest_empty():
    origin_pk = uuid.uuid4()
    player_id = uuid.uuid4()
    player = SimpleNamespace(
        id=player_id, current_sector_id=1, current_region_id=None
    )
    origin = SimpleNamespace(id=origin_pk, sector_id=1, region_id=None)
    only_bad = _station(
        sid=uuid.uuid4(),
        name="No Refine",
        sector_id=2,
        sector_uuid=uuid.uuid4(),
        faction="Astral Mining Consortium",
        refining=False,
    )

    db = MagicMock()

    def query_side_effect(model):
        q = MagicMock()
        name = getattr(model, "__name__", str(model))
        if name == "Player":
            q.filter.return_value.first.return_value = player
        elif name == "Sector":
            q.filter.return_value.filter.return_value.first.return_value = origin
            q.filter.return_value.first.return_value = origin
        elif name == "Station":
            q.filter.return_value.all.return_value = [only_bad]
        return q

    db.query.side_effect = query_side_effect

    with patch(
        "src.services.contract_generator._load_directed_sector_graph",
        return_value=({}, {origin_pk: []}),
    ):
        out = MiningService(db).find_nearest_am_refinery(player_id)

    assert out["found"] is False
    assert out["station"] is None
    assert out["hop_distance"] is None
    assert out["reason"] == "none_reachable"


def test_unreachable_am_refinery_returns_empty():
    origin_pk = uuid.uuid4()
    island_pk = uuid.uuid4()
    player_id = uuid.uuid4()
    player = SimpleNamespace(
        id=player_id, current_sector_id=1, current_region_id=None
    )
    origin = SimpleNamespace(id=origin_pk, sector_id=1, region_id=None)
    island = _station(
        sid=uuid.uuid4(),
        name="Isolated AM",
        sector_id=99,
        sector_uuid=island_pk,
        faction="Astral Mining Consortium",
        refining=True,
    )

    db = MagicMock()

    def query_side_effect(model):
        q = MagicMock()
        name = getattr(model, "__name__", str(model))
        if name == "Player":
            q.filter.return_value.first.return_value = player
        elif name == "Sector":
            q.filter.return_value.filter.return_value.first.return_value = origin
            q.filter.return_value.first.return_value = origin
        elif name == "Station":
            q.filter.return_value.all.return_value = [island]
        return q

    db.query.side_effect = query_side_effect

    # Graph has no edge to island — distances only contain origin.
    with patch(
        "src.services.contract_generator._load_directed_sector_graph",
        return_value=({}, {origin_pk: []}),
    ):
        out = MiningService(db).find_nearest_am_refinery(player_id)

    assert out["found"] is False
    assert out["reason"] == "none_reachable"
