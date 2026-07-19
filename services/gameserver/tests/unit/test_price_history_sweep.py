"""Unit coverage for the PriceHistory time-series sweep + history endpoint
(WO-ECON-MKT-TIMESERIES).

Before this WO, price_history had readers (market_prediction_engine's
PriceHistory-preferred series, economy_analytics._get_price_trends) but ZERO
writers anywhere in the codebase — every prediction/chart ran on a
permanently empty table. This file covers:

  1. sweep_price_history's hourly snapshot — one row per (station, commodity)
     with a MarketPrice row, interval volume from MarketTransaction sums, and
     idempotency within the hour (a second tick never duplicates).
  2. The daily rollup (hourly -> daily on the UTC day boundary) and weekly
     rollup (daily -> weekly on the ISO-week Monday boundary).
  3. Retention pruning (hourly/daily past their window; weekly never pruned).
  4. GET /trading/market/{station_id}/history — empty-not-500 pre-sweep,
     window filtering, ascending order.

All assertions are scoped to a fresh, per-test uuid4() Station id, never a
global PriceHistory/MarketPrice row count — the suite runs against a live,
possibly-seeded DB (per project convention), and sweep_price_history sweeps
the ENTIRE MarketPrice table by design, so a global count would be
meaningless (and other stations' rows are none of this suite's business).
"""
from __future__ import annotations

from datetime import datetime, timedelta
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy.orm import Session

from src.api.routes.trading import get_market_history
from src.models.market_transaction import (
    MarketPrice,
    MarketTransaction,
    PriceHistory,
    TransactionType,
)
from src.models.station import Station, StationClass, StationType
from src.services.npc_scheduler_service import (
    PRICE_HISTORY_DAILY_RETENTION_DAYS,
    PRICE_HISTORY_HOURLY_RETENTION_DAYS,
    sweep_price_history,
)


def _station() -> Station:
    return Station(
        id=uuid4(),
        name="Price History Test Station",
        sector_id=1,
        station_class=StationClass.CLASS_1,
        type=StationType.TRADING,
    )


def _txn(station_id, commodity, quantity, ts) -> MarketTransaction:
    return MarketTransaction(
        station_id=station_id,
        commodity=commodity,
        transaction_type=TransactionType.BUY,
        quantity=quantity,
        unit_price=10,
        total_value=quantity * 10,
        timestamp=ts,
    )


@pytest.mark.unit
class TestHourlySnapshot:
    def test_one_row_per_station_commodity_with_interval_volume(self, db: Session):
        station = _station()
        db.add(station)
        db.add(MarketPrice(
            station_id=station.id, commodity="ore",
            buy_price=10, sell_price=15, quantity=50,
            demand_level=1.2, supply_level=0.8,
        ))
        now = datetime(2026, 7, 1, 14, 30, 0)
        # Inside the trailing hour — counted.
        db.add(_txn(station.id, "ore", 5, now - timedelta(minutes=10)))
        db.add(_txn(station.id, "ore", 3, now - timedelta(minutes=30)))
        # Outside the trailing hour — must NOT be counted.
        db.add(_txn(station.id, "ore", 100, now - timedelta(hours=2)))
        db.commit()

        sweep_price_history(db, now=now)
        db.commit()

        row = (
            db.query(PriceHistory)
            .filter(
                PriceHistory.station_id == station.id,
                PriceHistory.commodity == "ore",
                PriceHistory.snapshot_type == "hourly",
            )
            .one()
        )
        assert row.snapshot_date == datetime(2026, 7, 1, 14, 0, 0)
        assert row.buy_price == 10
        assert row.sell_price == 15
        assert row.quantity == 50
        assert row.daily_volume == 8  # 5 + 3, excludes the 2h-old transaction
        assert row.transactions_count == 2
        assert row.average_transaction_size == 4.0
        assert row.demand_level == 1.2
        assert row.supply_level == 0.8

    def test_second_tick_within_the_hour_does_not_duplicate(self, db: Session):
        station = _station()
        db.add(station)
        db.add(MarketPrice(
            station_id=station.id, commodity="fuel",
            buy_price=5, sell_price=8, quantity=20,
        ))
        db.commit()
        now = datetime(2026, 7, 1, 9, 15, 0)

        sweep_price_history(db, now=now)
        db.commit()
        sweep_price_history(db, now=now + timedelta(minutes=20))  # same hour
        db.commit()

        rows = (
            db.query(PriceHistory)
            .filter(
                PriceHistory.station_id == station.id,
                PriceHistory.commodity == "fuel",
                PriceHistory.snapshot_type == "hourly",
            )
            .all()
        )
        assert len(rows) == 1

    def test_station_with_no_market_price_row_gets_no_snapshot(self, db: Session):
        station = _station()
        db.add(station)
        db.commit()

        sweep_price_history(db, now=datetime(2026, 7, 1, 10, 0, 0))
        db.commit()

        assert (
            db.query(PriceHistory)
            .filter(PriceHistory.station_id == station.id)
            .count()
            == 0
        )


@pytest.mark.unit
class TestDailyRollup:
    def test_rolls_up_the_prior_days_hourly_rows_on_the_day_boundary(self, db: Session):
        station = _station()
        db.add(station)
        yesterday = datetime(2026, 7, 1, 0, 0, 0)  # a Wednesday — not a rollup edge case
        for h, (buy, sell, qty, vol, txns) in enumerate(
            [(10, 15, 40, 5, 1), (12, 16, 42, 7, 2), (11, 14, 38, 3, 1)]
        ):
            db.add(PriceHistory(
                station_id=station.id, commodity="ore",
                buy_price=buy, sell_price=sell, quantity=qty,
                daily_volume=vol, transactions_count=txns,
                average_transaction_size=vol / txns,
                demand_level=1.0, supply_level=1.0,
                snapshot_date=yesterday + timedelta(hours=h),
                snapshot_type="hourly",
            ))
        db.commit()

        # First tick of the new UTC day (2026-07-02 is a Thursday — the
        # weekly rollup's Monday gate stays closed, isolating this test to
        # the daily rollup only).
        sweep_price_history(db, now=datetime(2026, 7, 2, 0, 5, 0))
        db.commit()

        daily_row = (
            db.query(PriceHistory)
            .filter(
                PriceHistory.station_id == station.id,
                PriceHistory.commodity == "ore",
                PriceHistory.snapshot_type == "daily",
            )
            .one()
        )
        assert daily_row.snapshot_date == yesterday
        assert daily_row.buy_price == round((10 + 12 + 11) / 3)
        assert daily_row.sell_price == round((15 + 16 + 14) / 3)
        assert daily_row.quantity == round((40 + 42 + 38) / 3)
        assert daily_row.daily_volume == 5 + 7 + 3
        assert daily_row.transactions_count == 1 + 2 + 1

    def test_does_not_fire_mid_day(self, db: Session):
        station = _station()
        db.add(station)
        db.add(PriceHistory(
            station_id=station.id, commodity="ore",
            buy_price=10, sell_price=15, quantity=40,
            daily_volume=5, transactions_count=1, average_transaction_size=5.0,
            demand_level=1.0, supply_level=1.0,
            snapshot_date=datetime(2026, 7, 1, 3, 0, 0),
            snapshot_type="hourly",
        ))
        db.commit()

        sweep_price_history(db, now=datetime(2026, 7, 1, 14, 0, 0))  # not hour 0
        db.commit()

        assert (
            db.query(PriceHistory)
            .filter(PriceHistory.station_id == station.id, PriceHistory.snapshot_type == "daily")
            .count()
            == 0
        )


@pytest.mark.unit
class TestWeeklyRollup:
    def test_rolls_up_the_prior_weeks_daily_rows_on_monday(self, db: Session):
        station = _station()
        db.add(station)
        last_week_start = datetime(2026, 6, 29, 0, 0, 0)  # a Monday
        for d, (buy, sell, qty, vol, txns) in enumerate(
            [(10, 15, 40, 5, 1), (14, 18, 44, 9, 3)]
        ):
            db.add(PriceHistory(
                station_id=station.id, commodity="ore",
                buy_price=buy, sell_price=sell, quantity=qty,
                daily_volume=vol, transactions_count=txns,
                average_transaction_size=vol / txns,
                demand_level=1.0, supply_level=1.0,
                snapshot_date=last_week_start + timedelta(days=d),
                snapshot_type="daily",
            ))
        db.commit()

        sweep_price_history(db, now=datetime(2026, 7, 6, 0, 5, 0))  # next Monday
        db.commit()

        weekly_row = (
            db.query(PriceHistory)
            .filter(
                PriceHistory.station_id == station.id,
                PriceHistory.commodity == "ore",
                PriceHistory.snapshot_type == "weekly",
            )
            .one()
        )
        assert weekly_row.snapshot_date == last_week_start
        assert weekly_row.buy_price == round((10 + 14) / 2)
        assert weekly_row.daily_volume == 5 + 9
        assert weekly_row.transactions_count == 1 + 3

    def test_does_not_fire_on_a_non_monday_day_boundary(self, db: Session):
        station = _station()
        db.add(station)
        db.add(PriceHistory(
            station_id=station.id, commodity="ore",
            buy_price=10, sell_price=15, quantity=40,
            daily_volume=5, transactions_count=1, average_transaction_size=5.0,
            demand_level=1.0, supply_level=1.0,
            snapshot_date=datetime(2026, 6, 25, 0, 0, 0),
            snapshot_type="daily",
        ))
        db.commit()

        sweep_price_history(db, now=datetime(2026, 7, 2, 0, 5, 0))  # Thursday
        db.commit()

        assert (
            db.query(PriceHistory)
            .filter(PriceHistory.station_id == station.id, PriceHistory.snapshot_type == "weekly")
            .count()
            == 0
        )


@pytest.mark.unit
class TestRetentionPruning:
    def test_prunes_hourly_and_daily_past_their_window_but_never_weekly(self, db: Session):
        station = _station()
        db.add(station)
        now = datetime(2026, 7, 15, 12, 0, 0)

        old_hourly = PriceHistory(
            station_id=station.id, commodity="ore",
            buy_price=1, sell_price=2, quantity=1,
            daily_volume=0, transactions_count=0, average_transaction_size=0.0,
            demand_level=1.0, supply_level=1.0,
            snapshot_date=now - timedelta(days=PRICE_HISTORY_HOURLY_RETENTION_DAYS + 1),
            snapshot_type="hourly",
        )
        recent_hourly = PriceHistory(
            station_id=station.id, commodity="ore",
            buy_price=1, sell_price=2, quantity=1,
            daily_volume=0, transactions_count=0, average_transaction_size=0.0,
            demand_level=1.0, supply_level=1.0,
            snapshot_date=now - timedelta(days=1),
            snapshot_type="hourly",
        )
        old_daily = PriceHistory(
            station_id=station.id, commodity="ore",
            buy_price=1, sell_price=2, quantity=1,
            daily_volume=0, transactions_count=0, average_transaction_size=0.0,
            demand_level=1.0, supply_level=1.0,
            snapshot_date=now - timedelta(days=PRICE_HISTORY_DAILY_RETENTION_DAYS + 1),
            snapshot_type="daily",
        )
        ancient_weekly = PriceHistory(
            station_id=station.id, commodity="ore",
            buy_price=1, sell_price=2, quantity=1,
            daily_volume=0, transactions_count=0, average_transaction_size=0.0,
            demand_level=1.0, supply_level=1.0,
            snapshot_date=now - timedelta(days=400),
            snapshot_type="weekly",
        )
        db.add_all([old_hourly, recent_hourly, old_daily, ancient_weekly])
        db.commit()

        # Pick a `now` that isn't a day/week boundary so this test exercises
        # pruning only, independent of the rollup passes.
        sweep_price_history(db, now=now.replace(hour=12))
        db.commit()

        remaining_types = {
            row.snapshot_type
            for row in db.query(PriceHistory).filter(PriceHistory.station_id == station.id).all()
        }
        assert "hourly" in remaining_types  # recent_hourly survives
        assert "daily" not in remaining_types  # old_daily pruned
        assert "weekly" in remaining_types  # never pruned

        hourly_dates = [
            row.snapshot_date
            for row in db.query(PriceHistory).filter(
                PriceHistory.station_id == station.id, PriceHistory.snapshot_type == "hourly"
            ).all()
        ]
        assert old_hourly.snapshot_date not in hourly_dates
        assert recent_hourly.snapshot_date in hourly_dates


@pytest.mark.unit
class TestMarketHistoryEndpoint:
    async def test_empty_before_any_sweep_not_an_error(self, db: Session):
        station = _station()
        db.add(station)
        db.commit()

        result = await get_market_history(
            station_id=str(station.id), commodity="ore", hours=24,
            db=db, current_user=object(), current_player=object(),
        )

        assert result["history"] == []
        assert result["station_id"] == str(station.id)
        assert result["commodity"] == "ore"

    async def test_window_filters_and_orders_ascending(self, db: Session):
        station = _station()
        db.add(station)
        now = datetime(2026, 7, 10, 12, 0, 0)
        # Outside a 24h window
        db.add(PriceHistory(
            station_id=station.id, commodity="ore", buy_price=1, sell_price=2, quantity=1,
            demand_level=1.0, supply_level=1.0,
            snapshot_date=now - timedelta(hours=48), snapshot_type="hourly",
        ))
        # Inside the window, out of chronological insert order
        db.add(PriceHistory(
            station_id=station.id, commodity="ore", buy_price=20, sell_price=25, quantity=5,
            demand_level=1.0, supply_level=1.0,
            snapshot_date=now - timedelta(hours=2), snapshot_type="hourly",
        ))
        db.add(PriceHistory(
            station_id=station.id, commodity="ore", buy_price=10, sell_price=15, quantity=3,
            demand_level=1.0, supply_level=1.0,
            snapshot_date=now - timedelta(hours=10), snapshot_type="hourly",
        ))
        db.commit()

        result = await get_market_history(
            station_id=str(station.id), commodity="ore", hours=24,
            db=db, current_user=object(), current_player=object(),
        )

        assert len(result["history"]) == 2
        assert result["history"][0]["buy_price"] == 10  # the -10h row, ascending
        assert result["history"][1]["buy_price"] == 20  # the -2h row

    async def test_unknown_station_raises_404(self, db: Session):
        with pytest.raises(HTTPException) as exc_info:
            await get_market_history(
                station_id=str(uuid4()), commodity="ore", hours=24,
                db=db, current_user=object(), current_player=object(),
            )
        assert exc_info.value.status_code == 404
