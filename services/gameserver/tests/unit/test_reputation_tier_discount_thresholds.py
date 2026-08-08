"""Unit tests for WO-FIX-REPUTATION-EFFECTS-DISPLAY-STALE-VS-ACTUAL-PRICING.

get_reputation_info()'s displayed `effects` dict now derives its price
percentage from _PERSONAL_REP_TIER_MULTIPLIERS -- the same table
compute_player_price_multiplier() (trading_service.py) actually charges --
instead of a separately hand-maintained score-threshold ladder that had
drifted from real pricing (e.g. Suspicious previously showed "no effect"
while actually being charged +5%; Lawful/Heroic/Legendary/Criminal/Villain
were off by ~1-2pts either direction).

_PERSONAL_REP_TIER_MULTIPLIERS (trading_service.py):
  Legendary 0.90 | Heroic 0.95 | Lawful 0.97 | Neutral 1.00
  Suspicious 1.05 | Outlaw 1.10 | Criminal 1.15 | Villain 1.20

No real DB: a tiny in-memory fake Session/Query keyed by Player.id, mirroring
the pattern in test_bounty_service_nh2.py.
"""
from types import SimpleNamespace
from uuid import uuid4

from src.services.personal_reputation_service import PersonalReputationService


def make_player(rep):
    return SimpleNamespace(
        id=uuid4(),
        personal_reputation=rep,
        reputation_tier="Neutral",
        name_color="#FFFFFF",
    )


class _FakeQuery:
    def __init__(self, players):
        self._players = players
        self._match_id = None

    def filter(self, cond):
        rhs = getattr(cond, "right", None)
        self._match_id = getattr(rhs, "value", None)
        return self

    def first(self):
        return self._players.get(self._match_id)


class _FakeSession:
    def __init__(self, *players):
        self._players = {p.id: p for p in players}

    def query(self, model):
        return _FakeQuery(self._players)


def _info_for(score):
    player = make_player(score)
    service = PersonalReputationService(_FakeSession(player))
    result = service.get_reputation_info(player.id)
    assert result["success"] is True
    return result


def _effects_for(score):
    return _info_for(score)["effects"]


def test_legendary_gets_ten_percent_discount_and_faction_bonus():
    effects = _effects_for(500)
    assert effects["station_price_discount"] == 10
    assert effects["faction_standing_bonus"] == 5
    assert "station_price_increase" not in effects
    assert "bounty_hunter_aggro" not in effects


def test_heroic_gets_five_percent_discount_and_faction_bonus():
    effects = _effects_for(250)
    assert effects["station_price_discount"] == 5
    assert effects["faction_standing_bonus"] == 5


def test_lawful_gets_three_percent_discount_no_faction_bonus():
    effects = _effects_for(1)
    assert effects["station_price_discount"] == 3
    assert "faction_standing_bonus" not in effects


def test_neutral_gets_no_price_effect():
    effects = _effects_for(0)
    assert "station_price_discount" not in effects
    assert "station_price_increase" not in effects
    assert "bounty_hunter_aggro" not in effects
    assert "faction_standing_bonus" not in effects


def test_suspicious_gets_five_percent_increase_not_no_effect():
    # Regression guard: this tier previously showed no effect at all while
    # actually being charged +5% at the pricing layer.
    effects = _effects_for(-1)
    assert effects["station_price_increase"] == 5
    assert "station_price_discount" not in effects
    assert "bounty_hunter_aggro" not in effects


def test_outlaw_gets_ten_percent_increase():
    effects = _effects_for(-250)
    assert effects["station_price_increase"] == 10
    assert "bounty_hunter_aggro" not in effects


def test_criminal_gets_fifteen_percent_increase_and_bounty_aggro():
    effects = _effects_for(-500)
    assert effects["station_price_increase"] == 15
    assert effects["bounty_hunter_aggro"] is True


def test_villain_gets_twenty_percent_increase_and_bounty_aggro():
    effects = _effects_for(-1000)
    assert effects["station_price_increase"] == 20
    assert effects["bounty_hunter_aggro"] is True


def test_effects_tier_matches_the_top_level_tier_field():
    # The effects are keyed off the same `tier` returned alongside them --
    # a caller reconciling the two should never see them disagree.
    info = _info_for(250)
    assert info["tier"] == "Heroic"
