"""LEG-298: bounty_hunter_aggro / personal_reputation ≤ −500 spawn scale.

Canon: SYSTEMS/bounty-and-reputation.md:148 (rate increased at ≤ −500);
FEATURES/gameplay/ranking.md Villain 1.20 is the only numeric
encounter-adjacent magnitude. No invented bands beyond that threshold.

DB-free: helpers + a tiny fake Session for sector occupancy.
"""
from types import SimpleNamespace
from uuid import uuid4

from src.services.personal_reputation_service import (
    BOUNTY_HUNTER_AGGRO_THRESHOLD,
    BOUNTY_HUNTER_ENCOUNTER_MULTIPLIER,
    bounty_hunter_encounter_multiplier,
    bounty_hunter_spawn_count,
    lowest_personal_reputation_in_sector,
)


def test_threshold_matches_canon_minus_500():
    assert BOUNTY_HUNTER_AGGRO_THRESHOLD == -500


def test_multiplier_neutral_just_above_threshold():
    assert bounty_hunter_encounter_multiplier(-499) == 1.0
    assert bounty_hunter_encounter_multiplier(0) == 1.0
    assert bounty_hunter_encounter_multiplier(None) == 1.0


def test_multiplier_increases_at_criminal_floor():
    assert bounty_hunter_encounter_multiplier(-500) == BOUNTY_HUNTER_ENCOUNTER_MULTIPLIER
    assert bounty_hunter_encounter_multiplier(-750) == BOUNTY_HUNTER_ENCOUNTER_MULTIPLIER
    assert bounty_hunter_encounter_multiplier(-1000) == BOUNTY_HUNTER_ENCOUNTER_MULTIPLIER


def test_spawn_count_measurable_increase_at_threshold():
    assert bounty_hunter_spawn_count(1, -499) == 1
    assert bounty_hunter_spawn_count(1, -500) == 2
    assert bounty_hunter_spawn_count(1, -750) == 2
    assert bounty_hunter_spawn_count(0, -500) == 0
    assert bounty_hunter_spawn_count(2, -500) == 3  # ceil(2 * 1.20) = 3


class _FakeQuery:
    def __init__(self, rows):
        self._rows = list(rows)
        self._ordered = False

    def filter(self, *_a, **_k):
        return self

    def order_by(self, *_a, **_k):
        self._ordered = True
        self._rows.sort(key=lambda r: r[0])
        return self

    def first(self):
        return self._rows[0] if self._rows else None


class _FakeSession:
    def __init__(self, reputations):
        self._rows = [(r,) for r in reputations]

    def query(self, *_cols):
        return _FakeQuery(self._rows)


def test_lowest_rep_empty_sector_is_none():
    assert lowest_personal_reputation_in_sector(_FakeSession([]), 12) is None


def test_lowest_rep_picks_most_negative_player():
    db = _FakeSession([-100, -500, -200])
    assert lowest_personal_reputation_in_sector(db, 12) == -500


def test_info_effects_flag_aligns_with_spawn_threshold():
    """Criminal/Villain display flag is the same ≤ −500 consumer."""
    from src.services.personal_reputation_service import PersonalReputationService

    class _PQuery:
        def __init__(self, player):
            self._player = player

        def filter(self, *_a, **_k):
            return self

        def first(self):
            return self._player

    class _Sess:
        def __init__(self, player):
            self._player = player

        def query(self, *_a):
            return _PQuery(self._player)

    player = SimpleNamespace(
        id=uuid4(),
        personal_reputation=-500,
        reputation_tier="Criminal",
        name_color="#FF4400",
    )
    info = PersonalReputationService(_Sess(player)).get_reputation_info(player.id)
    assert info["effects"]["bounty_hunter_aggro"] is True
    assert bounty_hunter_encounter_multiplier(-500) > bounty_hunter_encounter_multiplier(-499)
