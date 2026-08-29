"""NPC trade route tariff demand_factor elasticity (port-ownership.md § Tariff impact)."""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from src.services import npc_trading_service


@pytest.mark.parametrize(
    "tax_rate, expected",
    [
        (0.0, 1.0),
        (0.05, 0.75),
        (0.08, 0.60),
        (0.10, 0.50),
        (0.15, 0.25),
        (0.18, 0.10),
        (0.25, 0.10),
        (None, 1.0),
    ],
)
def test_compute_tariff_demand_factor_canon_table(tax_rate, expected):
    assert npc_trading_service.compute_tariff_demand_factor(tax_rate) == pytest.approx(
        expected
    )


@pytest.mark.parametrize(
    "standing, expected_score",
    [
        (800, 1.0),
        (400, 0.5),
        (0, 0.0),
        (-400, -0.5),
        (-800, -1.0),
        (1200, 1.0),
        (-1200, -1.0),
    ],
)
def test_normalize_standing_to_reputation_score(standing, expected_score):
    assert npc_trading_service.normalize_standing_to_reputation_score(
        standing
    ) == pytest.approx(expected_score)


@pytest.mark.parametrize(
    "reputation_score, expected_multiplier",
    [
        (1.0, 1.10),
        (0.0, 1.0),
        (-1.0, 0.90),
    ],
)
def test_compose_reputation_traffic_multiplier(reputation_score, expected_multiplier):
    assert npc_trading_service.compose_reputation_traffic_multiplier(
        reputation_score
    ) == pytest.approx(expected_multiplier)


def test_compose_npc_traffic_weight_after_demand_factor():
    """+1 reputation_score adds 10% on top of demand_factor."""
    weight = npc_trading_service.compose_npc_traffic_weight(0.05, 1.0)
    demand_only = npc_trading_service.compute_tariff_demand_factor(0.05)
    assert weight == pytest.approx(demand_only * 1.10)


def test_resolve_station_reputation_score_unowned_returns_zero():
    station = SimpleNamespace(
        owner_id=None,
        faction_affiliation="terran_federation",
    )
    assert npc_trading_service.resolve_station_reputation_score(MagicMock(), station) == 0.0


def test_resolve_station_reputation_score_owner_standing():
    owner_id = uuid.uuid4()
    faction_id = uuid.uuid4()
    station = SimpleNamespace(
        owner_id=owner_id,
        faction_affiliation="terran_federation",
    )
    owner = SimpleNamespace(id=owner_id, team_id=None)
    faction = SimpleNamespace(id=faction_id, name="terran_federation")
    db = MagicMock()

    faction_query = MagicMock()
    faction_query.filter.return_value.first.return_value = faction
    player_query = MagicMock()
    player_query.filter.return_value.first.return_value = owner

    def query_router(model):
        model_str = str(model)
        if "Faction" in model_str:
            return faction_query
        if "Player" in model_str:
            return player_query
        return MagicMock()

    db.query.side_effect = query_router

    with patch(
        "src.services.faction_service.resolve_effective_faction_standing_value",
        return_value=(800, "personal"),
    ):
        score = npc_trading_service.resolve_station_reputation_score(db, station)

    assert score == pytest.approx(1.0)


def test_compose_region_tax_rate_quarter_scales_traffic_by_three_quarters():
    """port-ownership.md:190 — region.tax_rate=0.25 → traffic ×0.75."""
    base = 1.0
    assert npc_trading_service.compose_region_tax_on_traffic(base, 0.25) == pytest.approx(
        0.75
    )
    assert npc_trading_service.compose_region_tax_on_traffic(base, 0.0) == pytest.approx(
        1.0
    )


def test_compute_npc_route_traffic_weight_applies_region_compose():
    """Same station tariff; 25% region tax → 0.75× route weight vs 0%."""
    station_tax = 0.05  # demand_factor 0.75
    at_zero = npc_trading_service.compute_npc_route_traffic_weight(station_tax, 0.0)
    at_quarter = npc_trading_service.compute_npc_route_traffic_weight(station_tax, 0.25)
    assert at_quarter == pytest.approx(at_zero * 0.75)


def test_compute_npc_route_traffic_weight_reputation_then_region():
    """Canon stack: demand × (1+0.10×rep) × (1−region_tax)."""
    station_tax = 0.05
    rep = 1.0
    with_rep = npc_trading_service.compose_npc_traffic_weight(station_tax, rep)
    final = npc_trading_service.compute_npc_route_traffic_weight(
        station_tax, 0.25, reputation_score=rep
    )
    assert final == pytest.approx(with_rep * 0.75)


def _station(station_id, sector_id, *, tax_rate, supplies, wants):
    commodities = {}
    for name in supplies:
        commodities[name] = {
            "sells": True,
            "buys": False,
            "quantity": 80,
            "capacity": 100,
        }
    for name in wants:
        commodities[name] = {
            "sells": False,
            "buys": True,
            "quantity": 10,
            "capacity": 100,
        }
    return SimpleNamespace(
        id=station_id,
        sector_id=sector_id,
        tax_rate=tax_rate,
        owner_id=None,
        faction_affiliation=None,
        commodities=commodities,
    )


def test_high_tariff_station_selected_less_often():
    """25% tariff (df=0.10) vs 5% floor (df=0.75) — ~13% relative pick rate."""
    region_id = uuid.uuid4()
    home_sector = 1
    low_tax = _station(uuid.uuid4(), 2, tax_rate=0.05, supplies=["ore"], wants=[])
    high_tax = _station(uuid.uuid4(), 3, tax_rate=0.25, supplies=["ore"], wants=["fuel"])
    buyer = _station(uuid.uuid4(), 4, tax_rate=0.05, supplies=[], wants=["ore"])

    stations = [low_tax, high_tax, buyer]

    def fake_hop_distances(_db, sector_id, _budget):
        return {2: 1, 3: 1, 4: 1, sector_id: 0}

    db = MagicMock()
    db.query.return_value.filter.return_value.all.return_value = [(2,), (3,), (4,)]

    with patch(
        "src.services.npc_engagement_service._hop_distances",
        side_effect=fake_hop_distances,
    ), patch.object(db, "query") as mock_query:
        station_query = MagicMock()
        station_query.all.return_value = stations
        sector_query = MagicMock()
        sector_query.filter.return_value.all.return_value = [(2,), (3,), (4,)]

        region_query = MagicMock()
        region_query.filter.return_value.first.return_value = SimpleNamespace(tax_rate=0.0)

        def query_router(model):
            model_str = str(model)
            if "Sector" in model_str:
                return sector_query
            if "Region" in model_str:
                return region_query
            return station_query

        mock_query.side_effect = query_router

        high_tax_first = 0
        trials = 2000
        for _ in range(trials):
            route = npc_trading_service.generate_trade_route(db, region_id, home_sector)
            assert route is not None
            if route[0]["station_id"] == str(high_tax.id):
                high_tax_first += 1

    ratio = high_tax_first / trials
    expected_ratio = 0.10 / (0.10 + 0.75)
    assert ratio == pytest.approx(expected_ratio, rel=0.25)
