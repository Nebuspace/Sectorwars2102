"""Unit tests for the numerical haggling engine (ADR-0079) + trader personalities.

Pure tests — they exercise the band math, the offer-resolution table, the final
price clamp, the modifier factors, and the trader-personality schema reconcile /
seeding helpers. None require a DB (the DB-touching session/memory paths are
integration-level and proved live).

Each test cites the ADR-0079 decision point it pins.
"""

import pytest

from src.core import trader_personalities as tp
from src.services import haggle_service as h


# ── ADR-0079 point 2: band narrows 20% per round ──────────────────────────────
def test_round_band_narrows_20pct_per_round():
    assert h._round_band_scale(1) == pytest.approx(1.0)
    assert h._round_band_scale(2) == pytest.approx(0.8)
    assert h._round_band_scale(3) == pytest.approx(0.64)
    assert h._round_band_scale(4) == pytest.approx(0.512)
    # clamps out-of-range rounds to [1, MAX_ROUNDS]
    assert h._round_band_scale(0) == pytest.approx(1.0)
    assert h._round_band_scale(99) == pytest.approx(0.512)


# ── ADR-0079 point 5: difficulty 1->0.85 .. 10->1.25 linear ───────────────────
def test_difficulty_band_factor_linear():
    assert h._difficulty_band_factor(1) == pytest.approx(0.85)
    assert h._difficulty_band_factor(10) == pytest.approx(1.25)
    assert h._difficulty_band_factor(5) == pytest.approx(0.85 + (4 / 9) * 0.40)
    # out-of-band difficulty clamps
    assert h._difficulty_band_factor(0) == pytest.approx(0.85)
    assert h._difficulty_band_factor(50) == pytest.approx(1.25)


# ── ADR-0079 point 4: rep-tier multiplier endpoints ───────────────────────────
def test_lerp_endpoints_match_canon():
    # faction: hostile (-1000) x1.05 -> allied (+1000) x0.97
    assert h._lerp_by_value(-1000, 1.05, 0.97) == pytest.approx(1.05)
    assert h._lerp_by_value(1000, 1.05, 0.97) == pytest.approx(0.97)
    assert h._lerp_by_value(0, 1.05, 0.97) == pytest.approx(1.01)
    # personal: disliked x1.05 -> trusted x0.95
    assert h._lerp_by_value(-1000, 1.05, 0.95) == pytest.approx(1.05)
    assert h._lerp_by_value(1000, 1.05, 0.95) == pytest.approx(0.95)


# ── ADR-0079 point 3: rank +1%/tier capped +12% ───────────────────────────────
def test_rank_band_factor_caps_at_12pct():
    class Recruit:
        military_rank = "Recruit"

    class Sergeant:
        military_rank = "Sergeant"  # level 3

    class FleetAdmiral:
        military_rank = "Fleet Admiral"  # level 17 -> capped at 12%

    assert h._rank_band_factor(Recruit()) == pytest.approx(1.0)
    assert h._rank_band_factor(Sergeant()) == pytest.approx(1.03)
    assert h._rank_band_factor(FleetAdmiral()) == pytest.approx(1.12)


# ── ADR-0079 round-1 bands match haggling.md (buy + sell) ──────────────────────
def test_buy_band_round1_neutral():
    fair = 100.0
    band = h._compute_band(fair, "buy", 1, 1.0)
    assert band["accept_threshold"] == pytest.approx(97.0)  # >= fair*0.97
    assert band["reject_threshold"] == pytest.approx(80.0)  # < fair*0.80


def test_sell_band_round1_neutral():
    fair = 100.0
    band = h._compute_band(fair, "sell", 1, 1.0)
    assert band["accept_threshold"] == pytest.approx(103.0)  # <= fair*1.03
    assert band["reject_threshold"] == pytest.approx(120.0)  # > fair*1.20


# ── ADR-0079 point 1: counter = midpoint(offer, fair) ─────────────────────────
def test_buy_offer_resolution_and_counter_midpoint():
    fair = 100.0
    band = h._compute_band(fair, "buy", 1, 1.0)
    # accept
    assert h._resolve_offer(98.0, fair, "buy", band)[0] == "accept"
    # counter at midpoint of (90, 100) = 95
    verdict, counter = h._resolve_offer(90.0, fair, "buy", band)
    assert verdict == "counter"
    assert counter == pytest.approx(95.0)
    # reject
    assert h._resolve_offer(70.0, fair, "buy", band)[0] == "reject"


def test_sell_offer_resolution_and_counter_midpoint():
    fair = 100.0
    band = h._compute_band(fair, "sell", 1, 1.0)
    assert h._resolve_offer(102.0, fair, "sell", band)[0] == "accept"
    verdict, counter = h._resolve_offer(110.0, fair, "sell", band)
    assert verdict == "counter"
    assert counter == pytest.approx(105.0)  # midpoint(110, 100)
    assert h._resolve_offer(130.0, fair, "sell", band)[0] == "reject"


# ── ADR-0079 point 7: final realized price clamped to [0.80, 1.20] x fair ──────
def test_final_price_clamp():
    assert h._clamp_realized(50.0, 100.0) == pytest.approx(80.0)
    assert h._clamp_realized(200.0, 100.0) == pytest.approx(120.0)
    assert h._clamp_realized(95.0, 100.0) == pytest.approx(95.0)


# ── precious_metals-at-floor fix: the desk is bounded by the commodity hard band
# the route enforces, so it never strikes a deal the route's clamp_to_commodity_band
# would silently negate (a struck buy at 78 on a floor=80 commodity was clamped
# back UP to 80 → player charged full price, single-use deal forfeited).
def test_clamp_realized_bounded_by_commodity_floor(monkeypatch):
    # commodity floored at 80 (precious_metals at fair==floor==80).
    monkeypatch.setattr(h, "_commodity_band", lambda c: (80.0, 180.0) if c == "precious_metals" else None)
    # [0.80, 1.20]×80 = [64, 96], but the hard floor 80 raises the realized price.
    assert h._clamp_realized(64.0, 80.0, "precious_metals") == pytest.approx(80.0)
    assert h._clamp_realized(78.0, 80.0, "precious_metals") == pytest.approx(80.0)
    # an offer at/above the floor passes through (still within the [0.80,1.20] window).
    assert h._clamp_realized(90.0, 80.0, "precious_metals") == pytest.approx(90.0)
    # ceiling bound: a sell realized price above the commodity ceiling is capped.
    assert h._clamp_realized(220.0, 180.0, "precious_metals") == pytest.approx(180.0)
    # no commodity → unbounded by the hard band (legacy behaviour preserved).
    assert h._clamp_realized(64.0, 80.0, None) == pytest.approx(64.0)


def test_buy_band_bounded_by_commodity_floor(monkeypatch):
    # fair == floor == 80: no discount is achievable; the lowest acceptable price
    # is the floor itself. Haggling yields no false savings, but the deal is honest.
    monkeypatch.setattr(h, "_commodity_band", lambda c: (80.0, 180.0))
    band = h._compute_band(80.0, "buy", 1, 1.0, "precious_metals")
    # accept_threshold raised to the floor — the only achievable price is 80.
    assert band["accept_threshold"] == pytest.approx(80.0)
    # an offer at the floor accepts; the realized agreed price equals the floor.
    verdict, _ = h._resolve_offer(80.0, 80.0, "buy", band)
    assert verdict == "accept"
    assert h._clamp_realized(80.0, 80.0, "precious_metals") == pytest.approx(80.0)
    # an offer BELOW the floor is NOT a silent full-price strike: it counters
    # (session stays alive) rather than confirming a phantom discount.
    verdict_lo, counter = h._resolve_offer(78.0, 80.0, "buy", band)
    assert verdict_lo == "counter"


def test_buy_band_unfloored_commodity_unchanged(monkeypatch):
    # fair well above the floor: the floor never binds, ordinary discount available.
    monkeypatch.setattr(h, "_commodity_band", lambda c: (80.0, 180.0))
    band = h._compute_band(120.0, "buy", 1, 1.0, "precious_metals")
    # neutral round-1 buy band is fair*0.97 = 116.4, above the 80 floor → untouched.
    assert band["accept_threshold"] == pytest.approx(116.4)
    assert band["reject_threshold"] == pytest.approx(96.0)  # fair*0.80, above floor


def test_sell_band_bounded_by_commodity_ceiling(monkeypatch):
    # fair == ceiling: no premium achievable; highest acceptable price is the ceiling.
    monkeypatch.setattr(h, "_commodity_band", lambda c: (80.0, 180.0))
    band = h._compute_band(180.0, "sell", 1, 1.0, "precious_metals")
    assert band["accept_threshold"] == pytest.approx(180.0)
    verdict, _ = h._resolve_offer(180.0, 180.0, "sell", band)
    assert verdict == "accept"
    assert h._clamp_realized(180.0, 180.0, "precious_metals") == pytest.approx(180.0)


# ── FIX 1 (ADR-0079 point 6): haggle is never worse than not haggling ──────────
# fair_price IS the posted/effective price the route would charge un-haggled
# (trading.md stack baked in). A successful BUY accept must therefore land at or
# BELOW the posted price (the player keeps their rank/rep discount), and a SELL
# accept at or ABOVE it — across every round and every band multiplier.
def test_buy_accept_never_above_posted_price():
    fair = 100.0  # the posted price the player would pay un-haggled
    for band_mult in (0.5, 0.85, 1.0, 1.25, 2.0):
        for rnd in range(1, h.MAX_ROUNDS + 1):
            band = h._compute_band(fair, "buy", rnd, band_mult)
            # The most generous accept-side offer that still ACCEPTS is the
            # accept_threshold itself; anything that accepts is <= it.
            verdict, _ = h._resolve_offer(band["accept_threshold"], fair, "buy", band)
            assert verdict == "accept"
            agreed = h._clamp_realized(band["accept_threshold"], fair)
            assert agreed <= fair + 1e-9  # buy haggle never costs MORE than posted


def test_sell_accept_never_below_posted_price():
    fair = 100.0  # the posted payout the player would receive un-haggled
    for band_mult in (0.5, 0.85, 1.0, 1.25, 2.0):
        for rnd in range(1, h.MAX_ROUNDS + 1):
            band = h._compute_band(fair, "sell", rnd, band_mult)
            verdict, _ = h._resolve_offer(band["accept_threshold"], fair, "sell", band)
            assert verdict == "accept"
            agreed = h._clamp_realized(band["accept_threshold"], fair)
            assert agreed >= fair - 1e-9  # sell haggle never pays LESS than posted


# ── FIX 2: per-player haggle memory prunes at a UNIFORM 90 days ────────────────
# Max ruling (DECISIONS.md haggling-personality-reconciliation, 2026-06-20):
# memory = 90 days uniform, regardless of the per-archetype memory_duration_days.
def test_memory_prune_uniform_90_days_ignores_archetype_window():
    from datetime import timedelta

    now = h._now()
    # Black Market archetype: archetype window is only 7 days, but per-player
    # numerical memory must persist the uniform 90 days.
    personality = {
        "type": "BLACK_MARKET",
        "memory_duration_days": 7,
        "player_memory": {
            "recent": {"last_seen_at": (now - timedelta(days=30)).isoformat()},
            "expired": {"last_seen_at": (now - timedelta(days=91)).isoformat()},
        },
    }
    h._prune_expired_memory(personality)
    # 30 days < 90 → kept even though the archetype window is 7 days.
    assert "recent" in personality["player_memory"]
    # 91 days > 90 → pruned.
    assert "expired" not in personality["player_memory"]
    # The constant is the uniform 90-day horizon.
    assert h.HAGGLE_MEMORY_DAYS == 90


# ── narrowing tightens later rounds; harder multiplier tightens the band ───────
def test_narrowing_and_difficulty_tighten_band():
    fair = 100.0
    b1 = h._compute_band(fair, "buy", 1, 1.0)
    b4 = h._compute_band(fair, "buy", 4, 1.0)
    # later round → accept threshold closer to fair (player must come closer)
    assert b4["accept_threshold"] > b1["accept_threshold"]
    easy = h._compute_band(fair, "buy", 1, 0.85)
    hard = h._compute_band(fair, "buy", 1, 1.25)
    # harder band → accept threshold closer to fair
    assert hard["accept_threshold"] > easy["accept_threshold"]


# ── trader_personalities: archetype-by-class mapping ──────────────────────────
def test_archetype_for_station_class():
    assert tp.archetype_for_station_class(0) == tp.TraderArchetype.FEDERATION
    assert tp.archetype_for_station_class(4) == tp.TraderArchetype.FEDERATION
    assert tp.archetype_for_station_class(5) == tp.TraderArchetype.BORDER
    assert tp.archetype_for_station_class(7) == tp.TraderArchetype.BORDER
    assert tp.archetype_for_station_class(8) == tp.TraderArchetype.BLACK_MARKET
    assert tp.archetype_for_station_class(9) == tp.TraderArchetype.BLACK_MARKET
    assert tp.archetype_for_station_class(10) == tp.TraderArchetype.LUXURY
    assert tp.archetype_for_station_class(11) == tp.TraderArchetype.LUXURY
    assert tp.archetype_for_station_class(999) == tp.TraderArchetype.BORDER


# ── jsonb-schema archetype defaults ───────────────────────────────────────────
def test_archetype_defaults_match_jsonb_schema():
    fed = tp.default_personality(tp.TraderArchetype.FEDERATION)
    assert fed["haggling_difficulty"] == 3
    assert fed["preferred_appeal_types"] == ["procedural", "compliance"]
    assert fed["memory_duration_days"] == 30
    assert fed["trust_level"] == 0  # NOT the legacy 50
    bm = tp.default_personality(tp.TraderArchetype.BLACK_MARKET)
    assert bm["haggling_difficulty"] == 9
    assert bm["memory_duration_days"] == 7


# ── ADR-0079 schema reconcile (legacy → canonical) ────────────────────────────
def test_normalize_legacy_personality():
    legacy = {
        "type": "BORDER",
        "haggling_difficulty": 5,
        "preferred_appeal_types": ["survival", "logical"],  # invalid vocabulary
        "memory_duration": 7,  # legacy key
        "trust_level": 50,  # legacy default → reset to 0
        "quirks": [],
    }
    norm = tp.normalize_personality(legacy)
    assert "memory_duration_days" in norm
    assert "memory_duration" not in norm
    assert norm["trust_level"] == 0
    # invalid appeals filtered → archetype default
    assert norm["preferred_appeal_types"] == ["economic", "personal"]
    assert norm["player_memory"] == {}
    # idempotent
    assert tp.normalize_personality(norm) == norm


def test_normalize_clamps_out_of_band():
    raw = {"type": "FRONTIER", "haggling_difficulty": 99, "memory_duration_days": 9999, "trust_level": 50000, "player_memory": {}}
    norm = tp.normalize_personality(raw)
    assert norm["haggling_difficulty"] == 10
    assert norm["memory_duration_days"] == 90
    assert norm["trust_level"] == 1000


# ── seeding: needs_reseed + reseed preserves memory ───────────────────────────
def test_needs_reseed_detects_no_op_and_legacy():
    legacy = {"type": "BORDER", "memory_duration": 7, "trust_level": 50, "quirks": []}
    assert tp.needs_reseed(legacy, 8) is True  # old shape
    canon_correct = tp.build_personality_for_class(8)
    assert tp.needs_reseed(canon_correct, 8) is False  # already correct + canonical
    # class mismatch with no memory → reseed
    border_on_class8 = tp.build_personality_for_class(5)
    assert tp.needs_reseed(border_on_class8, 8) is True


def test_reseed_preserves_player_memory_and_trust():
    prior = tp.build_personality_for_class(5)  # Border default
    prior["player_memory"] = {"pid-1": {"trust": 40, "session_count": 3}}
    prior["trust_level"] = 40
    reseeded = tp.reseed_personality(prior, 8)  # should become Black Market
    assert reseeded["type"] == "BLACK_MARKET"
    assert reseeded["haggling_difficulty"] == 9
    assert reseeded["player_memory"] == {"pid-1": {"trust": 40, "session_count": 3}}
    assert reseeded["trust_level"] == 40  # non-default trust preserved
