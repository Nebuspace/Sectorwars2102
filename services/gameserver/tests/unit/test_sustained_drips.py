"""Unit coverage for the sustained-reputation-drip mechanic
(factions-and-teams.md:229-230, WO-PROG-SUSTAINED-DRIPS).

Of the six "ongoing-state drip mechanics" rows in that canon table, only
these two are buildable today (personal_reputation is a live Player column
with no unbuilt dependency): a player sustaining Heroic+ personal_reputation
(>= +250) for 7+ canonical days drips -5/day Fringe Alliance
(FactionType.OUTLAWS); a player sustaining Outlaw+ (<= -250) for 7+
canonical days drips -2/day Mercantile Guild (FactionType.MERCHANTS). The
other four rows (ship-skin wearing, contraband-per-sector-hop, Wanted-status-
docked-at-Fringe-port) are BLOCKED on unbuilt systems and are NOT covered
here (nothing to test -- no code exists for them).

This file exercises ``apply_sustained_reputation_drip`` -- the PURE,
session-injectable per-player state-machine step -- directly, never the
SessionLocal-owning ``_run_sustained_reputation_drip_sweep_sync`` wrapper
(which needs a live advisory-lock-capable Postgres session and, per this
codebase's convention, is integration-only / not unit-tested; see
test_route_runs_retention.py's identical split between
``prune_route_optimization_runs`` (unit-tested) and its ``_run_..._sync``
wrapper (not)).

No live DB is used. ``apply_faction_rep_delta`` is REPLACED with a spy (never
exercised for real, never a direct Reputation write) -- per the codebase's
mock-only unit-test convention this proves the SUT calls the SYNC, flush-
only faction-rep primitive with the exact (player_id, FactionType, delta,
reason) canon requires, without needing to fake Faction/Reputation queries
at all. ``apply_sustained_reputation_drip`` calls ``flag_modified(player,
"settings")`` on a real write, which needs genuine SQLAlchemy instance-state
(``_sa_instance_state``) -- a bare stub raises AttributeError (test_owner_
controls.py / test_first_login_persistence.py's identical reasoning), so
every player fixture here is a REAL (transient) ``Player()`` ORM instance
with ``committed_state`` reset to simulate "freshly loaded from DB, nothing
dirty yet" -- ``get_history(player, "settings").has_changes()`` then detects
a REAL subsequent write, not the object's own construction.

Acceptance-criteria map (WO-PROG-SUSTAINED-DRIPS):
  entering Heroic starts the clock        -> TestEnteringBandStartsClock
  dropping below +250 clears it           -> TestDroppingOutClearsTracker
  day 6 -> no drip                        -> TestNotYetSustained
  day 7+ -> -5 Fringe daily                -> TestDripApplied::test_heroic_...
  Outlaw side -> -2 Mercantile             -> TestDripApplied::test_outlaw_...
  same-day re-run no-op                   -> TestSameDayIdempotent
  JSONB persistence (get_history/flag_modified pin)
                                            -> TestJSONBPersistenceProof
  band flip (heroic->outlaw) resets clock -> TestBandFlipResetsClock
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.orm.attributes import get_history

from src.services.scheduler import reputation_team_sweeps
from src.models.faction import FactionType
from src.models.player import Player
from src.services.npc_scheduler_service import (
    SUSTAINED_DRIP_DAYS_REQUIRED,
    SUSTAINED_HEROIC_DRIP_DELTA,
    SUSTAINED_HEROIC_THRESHOLD,
    SUSTAINED_OUTLAW_DRIP_DELTA,
    SUSTAINED_OUTLAW_THRESHOLD,
    _SUSTAINED_TIER_SETTINGS_KEY,
    apply_sustained_reputation_drip,
)

TODAY = 1000  # arbitrary canonical-day index; only relative offsets matter


# --------------------------------------------------------------------------- #
# Fixtures / fakes
# --------------------------------------------------------------------------- #

def _fresh_committed_player(*, personal_reputation=0, settings=None, is_active=True):
    """A real (transient) Player() ORM instance with committed_state reset to
    simulate 'freshly loaded from DB, nothing dirty yet' -- get_history needs
    this baseline to detect a REAL subsequent change rather than trivially
    reporting the object's own construction as a change (test_owner_
    controls.py's _fresh_committed_station, same reasoning)."""
    player = Player()
    player.id = uuid.uuid4()
    player.personal_reputation = personal_reputation
    player.settings = settings if settings is not None else {}
    player.is_active = is_active
    insp = sa_inspect(player)
    insp.committed_state.clear()
    insp._commit_all(insp.dict)
    return player


def _tracker(band, since_day, last_drip_day=None):
    return {
        _SUSTAINED_TIER_SETTINGS_KEY: {
            "band": band, "since_day": since_day, "last_drip_day": last_drip_day,
        }
    }


class _RepDeltaSpy:
    """Stand-in for apply_faction_rep_delta -- records every call instead of
    touching Faction/Reputation at all. Returns None, mirroring the real
    primitive's documented no-op-on-missing-faction return path (the SUT
    must not depend on a truthy return)."""

    def __init__(self):
        self.calls = []

    def __call__(self, db, player_id, faction_type, delta, reason):
        self.calls.append(
            {"db": db, "player_id": player_id, "faction_type": faction_type,
             "delta": delta, "reason": reason}
        )
        return None


@pytest.fixture
def rep_spy(monkeypatch):
    spy = _RepDeltaSpy()
    monkeypatch.setattr(reputation_team_sweeps, "apply_faction_rep_delta", spy)
    return spy


# --------------------------------------------------------------------------- #
# Entering a sustained band starts the clock -- no drip yet
# --------------------------------------------------------------------------- #

class TestEnteringBandStartsClock:
    def test_fresh_heroic_entry_starts_clock_no_drip(self, rep_spy):
        player = _fresh_committed_player(personal_reputation=SUSTAINED_HEROIC_THRESHOLD)

        result = apply_sustained_reputation_drip(db=None, player=player, today=TODAY)

        assert result is None
        assert rep_spy.calls == []
        tracker = player.settings[_SUSTAINED_TIER_SETTINGS_KEY]
        assert tracker == {"band": "heroic", "since_day": TODAY, "last_drip_day": None}

    def test_fresh_outlaw_entry_starts_clock_no_drip(self, rep_spy):
        player = _fresh_committed_player(personal_reputation=SUSTAINED_OUTLAW_THRESHOLD)

        result = apply_sustained_reputation_drip(db=None, player=player, today=TODAY)

        assert result is None
        assert rep_spy.calls == []
        tracker = player.settings[_SUSTAINED_TIER_SETTINGS_KEY]
        assert tracker == {"band": "outlaw", "since_day": TODAY, "last_drip_day": None}

    def test_middle_reputation_is_never_a_candidate_band(self, rep_spy):
        """A player strictly between the thresholds resolves to no band at
        all and, with no existing tracker, is a clean no-op."""
        player = _fresh_committed_player(personal_reputation=0)

        result = apply_sustained_reputation_drip(db=None, player=player, today=TODAY)

        assert result is None
        assert rep_spy.calls == []
        assert _SUSTAINED_TIER_SETTINGS_KEY not in player.settings


# --------------------------------------------------------------------------- #
# Dropping out of the sustained range clears the tracker
# --------------------------------------------------------------------------- #

class TestDroppingOutClearsTracker:
    def test_dropping_below_heroic_threshold_clears_tracker(self, rep_spy):
        player = _fresh_committed_player(
            personal_reputation=SUSTAINED_HEROIC_THRESHOLD - 1,  # 249: no longer Heroic+
            settings=_tracker("heroic", since_day=TODAY - 3),
        )

        result = apply_sustained_reputation_drip(db=None, player=player, today=TODAY)

        assert result is None
        assert rep_spy.calls == []
        assert _SUSTAINED_TIER_SETTINGS_KEY not in player.settings

    def test_rising_above_outlaw_threshold_clears_tracker(self, rep_spy):
        player = _fresh_committed_player(
            personal_reputation=SUSTAINED_OUTLAW_THRESHOLD + 1,  # -249: no longer Outlaw+
            settings=_tracker("outlaw", since_day=TODAY - 10, last_drip_day=TODAY - 1),
        )

        result = apply_sustained_reputation_drip(db=None, player=player, today=TODAY)

        assert result is None
        assert rep_spy.calls == []
        assert _SUSTAINED_TIER_SETTINGS_KEY not in player.settings

    def test_no_tracker_and_no_band_is_a_true_no_op(self, rep_spy):
        """Nothing to clear -> settings dict identity is untouched (proven
        via get_history, not just value equality)."""
        player = _fresh_committed_player(personal_reputation=0, settings={})

        apply_sustained_reputation_drip(db=None, player=player, today=TODAY)

        assert get_history(player, "settings").has_changes() is False


# --------------------------------------------------------------------------- #
# Not yet sustained (< 7 canonical days)
# --------------------------------------------------------------------------- #

class TestNotYetSustained:
    def test_day_6_no_drip(self, rep_spy):
        """6 elapsed canonical days (< SUSTAINED_DRIP_DAYS_REQUIRED=7) is not
        yet sustained -- no drip, tracker untouched."""
        since_day = TODAY - (SUSTAINED_DRIP_DAYS_REQUIRED - 1)
        player = _fresh_committed_player(
            personal_reputation=SUSTAINED_HEROIC_THRESHOLD,
            settings=_tracker("heroic", since_day=since_day),
        )

        result = apply_sustained_reputation_drip(db=None, player=player, today=TODAY)

        assert result is None
        assert rep_spy.calls == []
        assert get_history(player, "settings").has_changes() is False
        assert player.settings[_SUSTAINED_TIER_SETTINGS_KEY]["since_day"] == since_day

    def test_day_0_fresh_entry_this_call_no_drip(self, rep_spy):
        since_day = TODAY
        player = _fresh_committed_player(
            personal_reputation=SUSTAINED_OUTLAW_THRESHOLD,
            settings=_tracker("outlaw", since_day=since_day),
        )

        result = apply_sustained_reputation_drip(db=None, player=player, today=TODAY)

        assert result is None
        assert rep_spy.calls == []


# --------------------------------------------------------------------------- #
# Drip applied at day 7+
# --------------------------------------------------------------------------- #

class TestDripApplied:
    def test_heroic_day_7_drips_minus_5_fringe_alliance(self, rep_spy):
        since_day = TODAY - SUSTAINED_DRIP_DAYS_REQUIRED  # exactly 7 elapsed
        player = _fresh_committed_player(
            personal_reputation=SUSTAINED_HEROIC_THRESHOLD,
            settings=_tracker("heroic", since_day=since_day),
        )

        result = apply_sustained_reputation_drip(db="fake-db", player=player, today=TODAY)

        assert result == "heroic"
        assert len(rep_spy.calls) == 1
        call = rep_spy.calls[0]
        assert call["db"] == "fake-db"
        assert call["player_id"] == player.id
        assert call["faction_type"] == FactionType.OUTLAWS  # Fringe Alliance
        assert call["delta"] == SUSTAINED_HEROIC_DRIP_DELTA == -5
        assert "Heroic" in call["reason"]

        tracker = player.settings[_SUSTAINED_TIER_SETTINGS_KEY]
        assert tracker == {"band": "heroic", "since_day": since_day, "last_drip_day": TODAY}

    def test_outlaw_day_7_drips_minus_2_mercantile_guild(self, rep_spy):
        since_day = TODAY - SUSTAINED_DRIP_DAYS_REQUIRED
        player = _fresh_committed_player(
            personal_reputation=SUSTAINED_OUTLAW_THRESHOLD,
            settings=_tracker("outlaw", since_day=since_day),
        )

        result = apply_sustained_reputation_drip(db="fake-db", player=player, today=TODAY)

        assert result == "outlaw"
        assert len(rep_spy.calls) == 1
        call = rep_spy.calls[0]
        assert call["faction_type"] == FactionType.MERCHANTS  # Mercantile Guild
        assert call["delta"] == SUSTAINED_OUTLAW_DRIP_DELTA == -2
        assert "Outlaw" in call["reason"]

        tracker = player.settings[_SUSTAINED_TIER_SETTINGS_KEY]
        assert tracker == {"band": "outlaw", "since_day": since_day, "last_drip_day": TODAY}

    def test_drip_continues_daily_past_the_7_day_threshold(self, rep_spy):
        """A player who stays Heroic well past day 7 keeps dripping on each
        NEW canonical day -- since_day is preserved (never reset) while
        last_drip_day advances."""
        since_day = TODAY - 23  # 23 days sustained, last dripped yesterday
        player = _fresh_committed_player(
            personal_reputation=SUSTAINED_HEROIC_THRESHOLD,
            settings=_tracker("heroic", since_day=since_day, last_drip_day=TODAY - 1),
        )

        result = apply_sustained_reputation_drip(db=None, player=player, today=TODAY)

        assert result == "heroic"
        tracker = player.settings[_SUSTAINED_TIER_SETTINGS_KEY]
        assert tracker["since_day"] == since_day  # unchanged -- clock keeps running
        assert tracker["last_drip_day"] == TODAY


# --------------------------------------------------------------------------- #
# Same-day re-run is idempotent
# --------------------------------------------------------------------------- #

class TestSameDayIdempotent:
    def test_second_call_same_day_does_not_double_drip(self, rep_spy):
        since_day = TODAY - SUSTAINED_DRIP_DAYS_REQUIRED
        player = _fresh_committed_player(
            personal_reputation=SUSTAINED_HEROIC_THRESHOLD,
            settings=_tracker("heroic", since_day=since_day),
        )

        first = apply_sustained_reputation_drip(db=None, player=player, today=TODAY)
        assert first == "heroic"
        assert len(rep_spy.calls) == 1

        second = apply_sustained_reputation_drip(db=None, player=player, today=TODAY)
        assert second is None
        assert len(rep_spy.calls) == 1  # NOT called a second time

    def test_already_dripped_today_is_a_clean_settings_no_op(self, rep_spy):
        """A row already anchored to today (e.g. a restart mid-day re-reads
        the anchor) makes zero writes -- proven via get_history, not just
        the spy call count."""
        player = _fresh_committed_player(
            personal_reputation=SUSTAINED_OUTLAW_THRESHOLD,
            settings=_tracker("outlaw", since_day=TODAY - 30, last_drip_day=TODAY),
        )

        result = apply_sustained_reputation_drip(db=None, player=player, today=TODAY)

        assert result is None
        assert rep_spy.calls == []
        assert get_history(player, "settings").has_changes() is False


# --------------------------------------------------------------------------- #
# JSONB persistence -- flag_modified fires on every real write
# --------------------------------------------------------------------------- #

class TestJSONBPersistenceProof:
    """get_history(...).has_changes() is exactly the signal SQLAlchemy's own
    unit-of-work flush logic consults to decide whether a column belongs in
    the next UPDATE -- proving each write below would survive a real
    flush/commit, not just an in-memory dict mutation invisible to the ORM."""

    def test_fresh_entry_registers_as_dirty(self, rep_spy):
        player = _fresh_committed_player(personal_reputation=SUSTAINED_HEROIC_THRESHOLD)
        assert get_history(player, "settings").has_changes() is False

        apply_sustained_reputation_drip(db=None, player=player, today=TODAY)

        assert get_history(player, "settings").has_changes() is True

    def test_clear_registers_as_dirty(self, rep_spy):
        player = _fresh_committed_player(
            personal_reputation=0,  # dropped out of band
            settings=_tracker("heroic", since_day=TODAY - 3),
        )
        assert get_history(player, "settings").has_changes() is False

        apply_sustained_reputation_drip(db=None, player=player, today=TODAY)

        assert get_history(player, "settings").has_changes() is True

    def test_drip_registers_as_dirty(self, rep_spy):
        since_day = TODAY - SUSTAINED_DRIP_DAYS_REQUIRED
        player = _fresh_committed_player(
            personal_reputation=SUSTAINED_HEROIC_THRESHOLD,
            settings=_tracker("heroic", since_day=since_day),
        )
        assert get_history(player, "settings").has_changes() is False

        apply_sustained_reputation_drip(db=None, player=player, today=TODAY)

        assert get_history(player, "settings").has_changes() is True

    def test_not_yet_sustained_registers_as_clean(self, rep_spy):
        """The negative case: no write at all means get_history sees no
        change -- proves the SUT doesn't reassign settings unconditionally
        on every call."""
        since_day = TODAY - (SUSTAINED_DRIP_DAYS_REQUIRED - 1)
        player = _fresh_committed_player(
            personal_reputation=SUSTAINED_HEROIC_THRESHOLD,
            settings=_tracker("heroic", since_day=since_day),
        )
        assert get_history(player, "settings").has_changes() is False

        apply_sustained_reputation_drip(db=None, player=player, today=TODAY)

        assert get_history(player, "settings").has_changes() is False


# --------------------------------------------------------------------------- #
# Band flip resets the clock
# --------------------------------------------------------------------------- #

class TestBandFlipResetsClock:
    def test_heroic_to_outlaw_flip_resets_clock_no_drip(self, rep_spy):
        """A tracker already past the 7-day heroic threshold (and even
        already dripped once) does NOT carry over when the player's
        reputation flips straight to the Outlaw band -- the new band starts
        its own clock at zero and this call does not drip."""
        player = _fresh_committed_player(
            personal_reputation=SUSTAINED_OUTLAW_THRESHOLD,  # now Outlaw+
            settings=_tracker("heroic", since_day=TODAY - 40, last_drip_day=TODAY - 1),
        )

        result = apply_sustained_reputation_drip(db=None, player=player, today=TODAY)

        assert result is None
        assert rep_spy.calls == []
        tracker = player.settings[_SUSTAINED_TIER_SETTINGS_KEY]
        assert tracker == {"band": "outlaw", "since_day": TODAY, "last_drip_day": None}

    def test_outlaw_to_heroic_flip_resets_clock_no_drip(self, rep_spy):
        player = _fresh_committed_player(
            personal_reputation=SUSTAINED_HEROIC_THRESHOLD,  # now Heroic+
            settings=_tracker("outlaw", since_day=TODAY - 40, last_drip_day=TODAY - 1),
        )

        result = apply_sustained_reputation_drip(db=None, player=player, today=TODAY)

        assert result is None
        assert rep_spy.calls == []
        tracker = player.settings[_SUSTAINED_TIER_SETTINGS_KEY]
        assert tracker == {"band": "heroic", "since_day": TODAY, "last_drip_day": None}


# --------------------------------------------------------------------------- #
# Defensive parsing -- corrupted/future anchors reset rather than crash
# --------------------------------------------------------------------------- #

class TestDefensiveAnchorParsing:
    def test_unparsable_since_day_resets_the_clock(self, rep_spy):
        player = _fresh_committed_player(
            personal_reputation=SUSTAINED_HEROIC_THRESHOLD,
            settings={_SUSTAINED_TIER_SETTINGS_KEY: {
                "band": "heroic", "since_day": "not-a-number", "last_drip_day": None,
            }},
        )

        result = apply_sustained_reputation_drip(db=None, player=player, today=TODAY)

        assert result is None
        assert rep_spy.calls == []
        tracker = player.settings[_SUSTAINED_TIER_SETTINGS_KEY]
        assert tracker == {"band": "heroic", "since_day": TODAY, "last_drip_day": None}

    def test_future_since_day_resets_the_clock(self, rep_spy):
        player = _fresh_committed_player(
            personal_reputation=SUSTAINED_OUTLAW_THRESHOLD,
            settings=_tracker("outlaw", since_day=TODAY + 5),
        )

        result = apply_sustained_reputation_drip(db=None, player=player, today=TODAY)

        assert result is None
        assert rep_spy.calls == []
        tracker = player.settings[_SUSTAINED_TIER_SETTINGS_KEY]
        assert tracker == {"band": "outlaw", "since_day": TODAY, "last_drip_day": None}


# --------------------------------------------------------------------------- #
# Falsifiability -- proves the 7-day gate genuinely reads the named constant
# --------------------------------------------------------------------------- #

class TestFalsifiability:
    def test_shrinking_the_required_days_makes_a_previously_protected_row_drip(
        self, rep_spy, monkeypatch
    ):
        """A tracker only 3 canonical days old does not drip at the fixture's
        default 7-day requirement. Shrink SUSTAINED_DRIP_DAYS_REQUIRED to 3
        (the SAME live module global apply_sustained_reputation_drip reads)
        and the SAME tracker drips -- proving the elapsed-days check
        genuinely gates on the named constant rather than being dead code or
        a hardcoded literal."""
        since_day = TODAY - 3
        player = _fresh_committed_player(
            personal_reputation=SUSTAINED_HEROIC_THRESHOLD,
            settings=_tracker("heroic", since_day=since_day),
        )

        not_yet = apply_sustained_reputation_drip(db=None, player=player, today=TODAY)
        assert not_yet is None
        assert rep_spy.calls == []

        monkeypatch.setattr(reputation_team_sweeps, "SUSTAINED_DRIP_DAYS_REQUIRED", 3)

        now_dripped = apply_sustained_reputation_drip(db=None, player=player, today=TODAY)
        assert now_dripped == "heroic"
        assert len(rep_spy.calls) == 1
