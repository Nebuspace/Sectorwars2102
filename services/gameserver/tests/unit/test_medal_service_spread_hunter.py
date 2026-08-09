"""Unit tests for economic.spread_hunter (WO-ESCALATE-SPREAD-HUNTER-ROUND-TRIP-DEFINITION).

Ruled definition (DECISIONS.md `spread-hunter-round-trip-definition`): one
completed BUY MarketTransaction paired with a later completed SELL of the
same commodity, same player, a DIFFERENT station, sell unit_price > buy
unit_price. Counted once per pair, no physical return-to-origin required.

Pure / mocked -- no DB fixture required, mirrors test_first_citizen_medal.py's
MagicMock + patch.object convention.
"""
from __future__ import annotations

import uuid
from unittest.mock import MagicMock, patch

from src.models.market_transaction import TransactionType
from src.services import medal_service
from src.services.medal_catalog import MEDAL_CATALOG, medals_for_trigger


def test_profitable_round_trips_owns_spread_hunter():
    matches = medals_for_trigger("profitable_round_trips")
    assert [m["id"] for m in matches] == ["economic.spread_hunter"]
    entry = MEDAL_CATALOG["economic.spread_hunter"]
    assert entry["criteria"]["threshold"] == 50


def _fake_db(rows):
    db = MagicMock()
    db.query.return_value.filter.return_value.order_by.return_value.all.return_value = rows
    return db


def _row(txn_type, commodity, station_id, unit_price):
    return (txn_type, commodity, station_id, unit_price)


def test_buy_then_sell_different_station_higher_price_counts_one():
    station_a, station_b = uuid.uuid4(), uuid.uuid4()
    rows = [
        _row(TransactionType.BUY, "Ore", station_a, 10),
        _row(TransactionType.SELL, "Ore", station_b, 20),
    ]
    db = _fake_db(rows)
    assert medal_service._profitable_round_trips_count(db, uuid.uuid4()) == 1


def test_same_station_buy_sell_does_not_count():
    station_a = uuid.uuid4()
    rows = [
        _row(TransactionType.BUY, "Ore", station_a, 10),
        _row(TransactionType.SELL, "Ore", station_a, 20),
    ]
    db = _fake_db(rows)
    assert medal_service._profitable_round_trips_count(db, uuid.uuid4()) == 0


def test_sell_price_not_higher_than_buy_does_not_count():
    station_a, station_b = uuid.uuid4(), uuid.uuid4()
    rows = [
        _row(TransactionType.BUY, "Ore", station_a, 20),
        _row(TransactionType.SELL, "Ore", station_b, 20),
    ]
    db = _fake_db(rows)
    assert medal_service._profitable_round_trips_count(db, uuid.uuid4()) == 0


def test_each_buy_and_sell_consumed_at_most_once():
    """Two profitable pairs -> exactly 2 round trips, not 4 (no double-consuming
    a single BUY or SELL row across multiple pairs)."""
    station_a, station_b = uuid.uuid4(), uuid.uuid4()
    rows = [
        _row(TransactionType.BUY, "Ore", station_a, 10),
        _row(TransactionType.BUY, "Ore", station_a, 12),
        _row(TransactionType.SELL, "Ore", station_b, 20),
        _row(TransactionType.SELL, "Ore", station_b, 22),
    ]
    db = _fake_db(rows)
    assert medal_service._profitable_round_trips_count(db, uuid.uuid4()) == 2


def test_unmatched_sell_with_no_prior_buy_is_skipped():
    station_b = uuid.uuid4()
    rows = [_row(TransactionType.SELL, "Ore", station_b, 20)]
    db = _fake_db(rows)
    assert medal_service._profitable_round_trips_count(db, uuid.uuid4()) == 0


def test_different_commodities_never_pair():
    station_a, station_b = uuid.uuid4(), uuid.uuid4()
    rows = [
        _row(TransactionType.BUY, "Ore", station_a, 10),
        _row(TransactionType.SELL, "Tech", station_b, 20),
    ]
    db = _fake_db(rows)
    assert medal_service._profitable_round_trips_count(db, uuid.uuid4()) == 0


def test_check_and_award_trade_medals_evaluates_profitable_round_trips():
    """The dispatcher computes the round-trip count unconditionally (mirrors
    combat's unconditional _combat_victory_count call) and feeds it into
    _evaluate_and_award under the profitable_round_trips trigger."""
    player = MagicMock()
    player.id = uuid.uuid4()
    player.credits = 0
    db = MagicMock()

    with patch.object(
        medal_service, "_profitable_round_trips_count", return_value=50
    ) as counter, patch.object(
        medal_service, "_evaluate_and_award", return_value=[]
    ) as evaluate:
        medal_service.check_and_award_trade_medals(db, player, {})

    counter.assert_called_once_with(db, player.id)
    calls = [c for c in evaluate.call_args_list if c.args[2] == "profitable_round_trips"]
    assert len(calls) == 1
    args, kwargs = calls[0]
    assert args[0] is db
    assert args[1] == player.id
    assert args[3] == 50
    assert kwargs["source_event_key"] == "trade.sell"
    assert kwargs["awarded_via"] == "trade"
