"""Unit tests: hull/commodity appraisal fallback pricing (WO-FIX-UNPRICED-HULL-APPRAISAL-HARDCODED).

Covers PlayerTradeService._unit_price / _hull_base_cost — the fallback path used
when no PlayerTradeablePrice row exists for an asset_key. Must derive from the
real shipyard base_cost (ShipSpecification) / canonical commodity base price
(commodity_economy.base_price), never a flat magic number.
"""

from src.core import commodity_economy
from src.models.player_trade import PlayerTradeablePrice
from src.models.ship import ShipSpecification, ShipType
from src.services.player_trade_service import PlayerTradeService


class _FakeQuery:
    def __init__(self, rows):
        self._rows = rows

    def filter(self, *args, **kwargs):
        return self

    def first(self):
        return self._rows[0] if self._rows else None


class _FakeSession:
    """Routes .query(Model) to a preloaded row list per model class."""

    def __init__(self, rows_by_model):
        self._rows_by_model = rows_by_model

    def query(self, model):
        return _FakeQuery(self._rows_by_model.get(model, []))


def _spec(ship_type: ShipType, base_cost: int) -> ShipSpecification:
    spec = ShipSpecification()
    spec.type = ship_type
    spec.base_cost = base_cost
    return spec


def test_unpriced_carrier_hull_uses_real_shipyard_base_cost_not_flat_5000():
    db = _FakeSession(
        {
            PlayerTradeablePrice: [],
            ShipSpecification: [_spec(ShipType.CARRIER, 500000)],
        }
    )
    svc = PlayerTradeService(db)

    price = svc._unit_price(f"ship:{ShipType.CARRIER.value}")

    assert price == 500000
    assert price != 5000


def test_unpriced_scout_hull_matches_its_own_class_price_not_carrier():
    db = _FakeSession(
        {
            PlayerTradeablePrice: [],
            ShipSpecification: [_spec(ShipType.SCOUT_SHIP, 30000)],
        }
    )
    svc = PlayerTradeService(db)

    assert svc._unit_price(f"ship:{ShipType.SCOUT_SHIP.value}") == 30000


def test_priced_hull_with_playertradeableprice_row_is_unaffected():
    """Regression: explicit admin-maintained price rows still win over the fallback."""
    row = PlayerTradeablePrice()
    row.asset_key = f"ship:{ShipType.CARRIER.value}"
    row.unit_value_cr = 777000

    db = _FakeSession(
        {
            PlayerTradeablePrice: [row],
            ShipSpecification: [_spec(ShipType.CARRIER, 500000)],
        }
    )
    svc = PlayerTradeService(db)

    assert svc._unit_price(f"ship:{ShipType.CARRIER.value}") == 777000


def test_unpriced_commodity_derives_from_canonical_commodity_economy_table():
    db = _FakeSession({PlayerTradeablePrice: [], ShipSpecification: []})
    svc = PlayerTradeService(db)

    assert svc._unit_price("precious_metals") == commodity_economy.base_price("precious_metals")
    assert svc._unit_price("ore") == commodity_economy.base_price("ore")
    # Historical hardcoded defaults matched this table already for these keys;
    # confirms the single-source refactor is behaviour-preserving here.
    assert svc._unit_price("ore") == 15
    assert svc._unit_price("equipment") == 35


def test_unpriced_credits_key_still_one_credit_per_unit():
    db = _FakeSession({PlayerTradeablePrice: [], ShipSpecification: []})
    svc = PlayerTradeService(db)
    assert svc._unit_price("credits") == 1


def test_unknown_ship_type_string_never_crashes_and_returns_zero_not_flat_5000():
    db = _FakeSession({PlayerTradeablePrice: [], ShipSpecification: []})
    svc = PlayerTradeService(db)
    assert svc._unit_price("ship:NOT_A_REAL_TYPE") == 0


def test_hull_with_no_shipspecification_row_returns_zero_not_flat_5000():
    db = _FakeSession({PlayerTradeablePrice: [], ShipSpecification: []})
    svc = PlayerTradeService(db)
    assert svc._unit_price(f"ship:{ShipType.CARRIER.value}") == 0


def test_carrier_hull_trade_binds_anti_rmt_counterparty_cap_when_realistically_priced():
    """A realistically-priced Carrier (500k) must actually bind the anti-RMT
    single-counterparty cap (250,000/30d) — the bug this WO fixes let every
    anti-RMT cap sit inert because appraisal was stuck at a flat 5,000
    (100 Carriers would have been required to trip any cap at all).
    """
    from src.services.player_trade_antirmt import (
        COUNTERPARTY_CAP_30D,
        WindowTotals,
        check_caps,
    )

    db = _FakeSession(
        {
            PlayerTradeablePrice: [],
            ShipSpecification: [_spec(ShipType.CARRIER, 500000)],
        }
    )
    svc = PlayerTradeService(db)
    carrier_price = svc._unit_price(f"ship:{ShipType.CARRIER.value}")

    reason = check_caps(
        prior_7d=WindowTotals(sent=0, received=0),
        send_this=0,
        receive_this=carrier_price,
        is_new_low_rep=False,
        prior_cp_flow_30d=0,
        cp_flow_this=carrier_price,
    )

    assert reason == "counterparty_cap_exceeded"
    assert carrier_price > COUNTERPARTY_CAP_30D
    # At the old flat-5000 fallback this single trade would never have bound
    # the cap (5,000 < 250,000) — the bug this WO closes.
    assert 5000 < COUNTERPARTY_CAP_30D
