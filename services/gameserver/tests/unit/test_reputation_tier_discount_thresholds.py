"""Unit tests for WO-FIX-REPUTATION-TIER-DISCOUNT-THRESHOLDS.

Before the fix, PersonalReputationService.get_reputation_info() gated the
station discount on score>=250 (one tier too high — Lawful players, score
1-249, got no discount at all) and the faction bonus on score>=500 (one tier
too high — Heroic players, score 250-499, never got the bonus). Canon
(sw2102-docs/FEATURES/gameplay/ranking.md, "Gameplay effects" table):
  [+1, +249]   Lawful     -5% station discount
  [+250, +499] Heroic     -5% station discount, +5% faction standing bonus
  [+500, +1000] Legendary -10% station discount, +5% faction standing bonus

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


def _effects_for(score):
    player = make_player(score)
    service = PersonalReputationService(_FakeSession(player))
    result = service.get_reputation_info(player.id)
    assert result["success"] is True
    return result["effects"]


def test_lawful_floor_gets_discount_only():
    effects = _effects_for(1)
    assert effects["station_price_discount"] == 5
    assert "faction_standing_bonus" not in effects


def test_lawful_ceiling_still_discount_only():
    effects = _effects_for(249)
    assert effects["station_price_discount"] == 5
    assert "faction_standing_bonus" not in effects


def test_heroic_floor_gets_discount_and_faction_bonus():
    effects = _effects_for(250)
    assert effects["station_price_discount"] == 5
    assert effects["faction_standing_bonus"] == 5


def test_heroic_ceiling_still_five_percent_discount():
    effects = _effects_for(499)
    assert effects["station_price_discount"] == 5
    assert effects["faction_standing_bonus"] == 5


def test_legendary_floor_gets_ten_percent_discount():
    effects = _effects_for(500)
    assert effects["station_price_discount"] == 10
    assert effects["faction_standing_bonus"] == 5


def test_neutral_and_below_get_no_positive_effects():
    effects = _effects_for(0)
    assert "station_price_discount" not in effects
    assert "faction_standing_bonus" not in effects
