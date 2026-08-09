"""Unit tests — price_alert_service.py (ops/admin price-alert evaluator).

No test file existed for this service (found during a gameserver
test-coverage sweep). DB-free: the module's own helpers duck-type via
``getattr`` (canon field name first, real column second), so plain
``types.SimpleNamespace`` stands in for a ``PriceAlert`` row without needing
a real model instance or a database.

Sections:
  TestResolveThreshold / TestResolveOp / TestResolveCooldownSeconds /
  TestInCooldown / TestCrosses — the pure per-alert helpers in isolation,
    including the canon-name-vs-real-column fallback order each documents.
  TestEvaluatePriceAlerts — the query-driving function, via a hand-rolled
    ``_SeqFakeSession`` (matching this suite's convention). Covers a real
    crossing firing + stamping ``last_triggered_at`` when the model carries
    it, a non-crossing not firing, a cooldown-suppressed crossing not
    firing, and the CURRENT real ``PriceAlert`` model's actual behavior:
    since it has no ``last_triggered_at`` column, cooldown is structurally
    inert today (documented, not a bug being introduced by this test).
  TestSweepPriceAlerts — ``evaluate_price_alerts`` monkeypatched to a
    scripted stub (it's independently covered above; this function's own
    logic is the price_lookup wiring, exception handling, and cross-alert
    de-dup), covering: lookup invoked with (station_id, commodity), a
    None result skips, a raising lookup is caught and skipped, alerts
    missing ``commodity`` are skipped without ever calling the lookup, and
    duplicate alert ids across two inner evaluate calls are de-duped.
"""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from src.models.market_transaction import PriceAlert as RealPriceAlert
from src.services import price_alert_service as pas_module
from src.services.price_alert_service import (
    DEFAULT_COOLDOWN_SECONDS,
    _crosses,
    _in_cooldown,
    _resolve_cooldown_seconds,
    _resolve_op,
    _resolve_threshold,
    evaluate_price_alerts,
    sweep_price_alerts,
)


class _SeqFakeSession:
    def __init__(self, results):
        self._results = list(results)
        self._i = 0

    def query(self, _model):
        return self

    def filter(self, *_args, **_kwargs):
        return self

    def all(self):
        r = self._results[self._i]
        self._i += 1
        return r


# ---------------------------------------------------------------------------
# _resolve_threshold
# ---------------------------------------------------------------------------


class TestResolveThreshold:
    def test_prefers_canon_threshold_attr(self):
        alert = SimpleNamespace(threshold=12.5, threshold_value=99.0)
        assert _resolve_threshold(alert) == 12.5

    def test_falls_back_to_threshold_value(self):
        alert = SimpleNamespace(threshold_value=42.0)
        assert _resolve_threshold(alert) == 42.0

    def test_missing_both_returns_none(self):
        alert = SimpleNamespace()
        assert _resolve_threshold(alert) is None

    def test_non_numeric_returns_none(self):
        alert = SimpleNamespace(threshold_value="not-a-number")
        assert _resolve_threshold(alert) is None

    def test_coerces_string_number(self):
        alert = SimpleNamespace(threshold_value="15.5")
        assert _resolve_threshold(alert) == 15.5


# ---------------------------------------------------------------------------
# _resolve_op
# ---------------------------------------------------------------------------


class TestResolveOp:
    def test_explicit_comparison_attr_wins(self):
        alert = SimpleNamespace(comparison="below", alert_type="price_spike")
        assert _resolve_op(alert) == "<="

    def test_explicit_direction_attr_recognized(self):
        alert = SimpleNamespace(direction="up")
        assert _resolve_op(alert) == ">="

    def test_alert_type_drop_heuristic(self):
        alert = SimpleNamespace(alert_type="price_drop")
        assert _resolve_op(alert) == "<="

    def test_alert_type_spike_heuristic(self):
        alert = SimpleNamespace(alert_type="price_spike")
        assert _resolve_op(alert) == ">="

    def test_alert_type_low_supply_maps_to_lte(self):
        alert = SimpleNamespace(alert_type="low_supply")
        assert _resolve_op(alert) == "<="

    def test_unrecognized_alert_type_defaults_gte(self):
        alert = SimpleNamespace(alert_type="high_volume")
        assert _resolve_op(alert) == ">="

    def test_no_hints_at_all_defaults_gte(self):
        alert = SimpleNamespace()
        assert _resolve_op(alert) == ">="


# ---------------------------------------------------------------------------
# _resolve_cooldown_seconds
# ---------------------------------------------------------------------------


class TestResolveCooldownSeconds:
    def test_missing_column_returns_default(self):
        alert = SimpleNamespace()
        assert _resolve_cooldown_seconds(alert) == DEFAULT_COOLDOWN_SECONDS

    def test_explicit_value_used(self):
        alert = SimpleNamespace(cooldown_seconds=60)
        assert _resolve_cooldown_seconds(alert) == 60

    def test_negative_value_falls_back_to_default(self):
        alert = SimpleNamespace(cooldown_seconds=-5)
        assert _resolve_cooldown_seconds(alert) == DEFAULT_COOLDOWN_SECONDS

    def test_non_numeric_falls_back_to_default(self):
        alert = SimpleNamespace(cooldown_seconds="soon")
        assert _resolve_cooldown_seconds(alert) == DEFAULT_COOLDOWN_SECONDS

    def test_zero_is_a_valid_explicit_value(self):
        alert = SimpleNamespace(cooldown_seconds=0)
        assert _resolve_cooldown_seconds(alert) == 0


# ---------------------------------------------------------------------------
# _in_cooldown
# ---------------------------------------------------------------------------


class TestInCooldown:
    def test_never_fired_is_not_in_cooldown(self):
        alert = SimpleNamespace(last_triggered_at=None)
        assert _in_cooldown(alert, datetime.now(timezone.utc)) is False

    def test_fired_recently_is_in_cooldown(self):
        now = datetime.now(timezone.utc)
        alert = SimpleNamespace(
            last_triggered_at=now - timedelta(seconds=10), cooldown_seconds=300
        )
        assert _in_cooldown(alert, now) is True

    def test_fired_outside_window_is_not_in_cooldown(self):
        now = datetime.now(timezone.utc)
        alert = SimpleNamespace(
            last_triggered_at=now - timedelta(seconds=400), cooldown_seconds=300
        )
        assert _in_cooldown(alert, now) is False

    def test_naive_last_triggered_at_is_coerced_to_aware(self):
        now = datetime.now(timezone.utc)
        naive_recent = (now - timedelta(seconds=5)).replace(tzinfo=None)
        alert = SimpleNamespace(last_triggered_at=naive_recent, cooldown_seconds=300)
        # Would raise (naive - aware) if _aware() weren't applied internally.
        assert _in_cooldown(alert, now) is True

    def test_zero_cooldown_never_suppresses(self):
        now = datetime.now(timezone.utc)
        alert = SimpleNamespace(last_triggered_at=now, cooldown_seconds=0)
        assert _in_cooldown(alert, now) is False


# ---------------------------------------------------------------------------
# _crosses
# ---------------------------------------------------------------------------


class TestCrosses:
    def test_gte_rises_to_threshold(self):
        assert _crosses(">=", 100.0, 100.0) is True

    def test_gte_below_threshold_does_not_cross(self):
        assert _crosses(">=", 99.0, 100.0) is False

    def test_lte_drops_to_threshold(self):
        assert _crosses("<=", 50.0, 50.0) is True

    def test_lte_above_threshold_does_not_cross(self):
        assert _crosses("<=", 51.0, 50.0) is False


# ---------------------------------------------------------------------------
# evaluate_price_alerts
# ---------------------------------------------------------------------------


def _alert(**kwargs):
    defaults = dict(id="a1", commodity="ore", station_id="s1")
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


class TestEvaluatePriceAlerts:
    def test_non_numeric_price_returns_empty_without_querying(self):
        db = _SeqFakeSession([])  # would IndexError if a query were attempted
        result = evaluate_price_alerts(db, "s1", "ore", "not-a-price")
        assert result == []

    def test_no_matching_alerts_returns_empty(self):
        db = _SeqFakeSession([[]])
        assert evaluate_price_alerts(db, "s1", "ore", 100.0) == []

    def test_alert_without_a_resolvable_threshold_is_skipped(self):
        db = _SeqFakeSession([[_alert(threshold_value=None)]])
        assert evaluate_price_alerts(db, "s1", "ore", 100.0) == []

    def test_crossing_fires_and_stamps_last_triggered_at_when_carried(self):
        now = datetime(2026, 8, 9, 12, 0, 0, tzinfo=timezone.utc)
        alert = _alert(alert_type="price_spike", threshold_value=100.0, last_triggered_at=None)
        db = _SeqFakeSession([[alert]])
        fired = evaluate_price_alerts(db, "s1", "ore", 150.0, now=now)
        assert fired == [alert]
        assert alert.last_triggered_at == now

    def test_non_crossing_does_not_fire(self):
        alert = _alert(alert_type="price_spike", threshold_value=100.0)
        db = _SeqFakeSession([[alert]])
        assert evaluate_price_alerts(db, "s1", "ore", 50.0) == []

    def test_cooldown_suppresses_a_real_crossing(self):
        now = datetime(2026, 8, 9, 12, 0, 0, tzinfo=timezone.utc)
        alert = _alert(
            alert_type="price_spike",
            threshold_value=100.0,
            last_triggered_at=now - timedelta(seconds=10),
            cooldown_seconds=300,
        )
        db = _SeqFakeSession([[alert]])
        assert evaluate_price_alerts(db, "s1", "ore", 150.0, now=now) == []

    def test_multiple_alerts_only_crossing_ones_returned(self):
        hit = _alert(id="hit", alert_type="price_spike", threshold_value=100.0)
        miss = _alert(id="miss", alert_type="price_spike", threshold_value=999.0)
        db = _SeqFakeSession([[hit, miss]])
        fired = evaluate_price_alerts(db, "s1", "ore", 150.0)
        assert fired == [hit]

    def test_real_price_alert_model_shape_never_suppresses_via_cooldown(self):
        # The live PriceAlert model has no last_triggered_at column, so
        # _resolve_last_triggered always returns None and _in_cooldown
        # always returns False -- cooldown is structurally inert against
        # today's schema (documented in the module's own docstring). This
        # pins that real, current behavior rather than the aspirational
        # canon-field behavior tested above.
        real_alert = RealPriceAlert()
        real_alert.id = "real-1"
        real_alert.commodity = "ore"
        real_alert.station_id = "s1"
        real_alert.alert_type = "price_spike"
        real_alert.threshold_value = 100.0
        real_alert.is_active = True
        db = _SeqFakeSession([[real_alert]])
        fired = evaluate_price_alerts(db, "s1", "ore", 150.0)
        assert fired == [real_alert]
        # And a second call "fires" again immediately -- no cooldown ever
        # applies while last_triggered_at can't be persisted on this model.
        db2 = _SeqFakeSession([[real_alert]])
        assert evaluate_price_alerts(db2, "s1", "ore", 150.0) == [real_alert]


# ---------------------------------------------------------------------------
# sweep_price_alerts
# ---------------------------------------------------------------------------


class TestSweepPriceAlerts:
    def test_calls_price_lookup_with_station_and_commodity(self, monkeypatch):
        calls = []

        def fake_evaluate(_db, station_id, commodity, _price, _now):
            calls.append((station_id, commodity))
            return []

        monkeypatch.setattr(pas_module, "evaluate_price_alerts", fake_evaluate)
        alert = _alert(commodity="fuel_ore", station_id="s9")
        db = _SeqFakeSession([[alert]])
        sweep_price_alerts(db, price_lookup=lambda sid, c: 42.0)
        assert calls == [("s9", "fuel_ore")]

    def test_missing_commodity_skips_without_calling_lookup(self, monkeypatch):
        lookup_calls = []

        def fake_evaluate(*_a, **_k):
            return []

        monkeypatch.setattr(pas_module, "evaluate_price_alerts", fake_evaluate)
        alert = _alert(commodity=None)
        db = _SeqFakeSession([[alert]])
        sweep_price_alerts(
            db, price_lookup=lambda sid, c: lookup_calls.append((sid, c)) or 1.0
        )
        assert lookup_calls == []

    def test_none_price_lookup_result_skips_the_alert(self, monkeypatch):
        evaluate_calls = []

        def fake_evaluate(*_a, **_k):
            evaluate_calls.append(1)
            return []

        monkeypatch.setattr(pas_module, "evaluate_price_alerts", fake_evaluate)
        alert = _alert()
        db = _SeqFakeSession([[alert]])
        sweep_price_alerts(db, price_lookup=lambda sid, c: None)
        assert evaluate_calls == []

    def test_price_lookup_exception_is_caught_and_skips(self, monkeypatch):
        def fake_evaluate(*_a, **_k):
            raise AssertionError("should never be called")

        monkeypatch.setattr(pas_module, "evaluate_price_alerts", fake_evaluate)

        def bad_lookup(_sid, _c):
            raise RuntimeError("boom")

        alert = _alert()
        db = _SeqFakeSession([[alert]])
        result = sweep_price_alerts(db, price_lookup=bad_lookup)
        assert result == []

    def test_dedupes_fired_alerts_across_inner_evaluate_calls(self, monkeypatch):
        shared_fired = _alert(id="dup")

        def fake_evaluate(*_a, **_k):
            return [shared_fired]

        monkeypatch.setattr(pas_module, "evaluate_price_alerts", fake_evaluate)
        alert_1 = _alert(id="src1", commodity="ore", station_id="s1")
        alert_2 = _alert(id="src2", commodity="ore", station_id="s2")
        db = _SeqFakeSession([[alert_1, alert_2]])
        fired = sweep_price_alerts(db, price_lookup=lambda sid, c: 100.0)
        # Both source rows resolve to the SAME underlying fired alert id --
        # the sweep must not double-report it.
        assert fired == [shared_fired]

    def test_returns_union_of_distinct_fired_alerts(self, monkeypatch):
        fired_a = _alert(id="fa")
        fired_b = _alert(id="fb")
        results = iter([[fired_a], [fired_b]])

        def fake_evaluate(*_a, **_k):
            return next(results)

        monkeypatch.setattr(pas_module, "evaluate_price_alerts", fake_evaluate)
        alert_1 = _alert(id="src1")
        alert_2 = _alert(id="src2")
        db = _SeqFakeSession([[alert_1, alert_2]])
        fired = sweep_price_alerts(db, price_lookup=lambda sid, c: 100.0)
        assert fired == [fired_a, fired_b]
