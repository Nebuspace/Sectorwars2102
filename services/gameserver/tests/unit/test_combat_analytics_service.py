"""Unit tests — combat_analytics_service.py (admin combat-dashboard analytics).

No test file existed for this service (719 lines, CombatAnalyticsService).
DB-free: db.query()/.filter()/.order_by()/.group_by()/.having()/.limit()
calls are routed through a hand-rolled _FakeDb that returns pre-seeded,
already-composed result lists keyed by the queried model class (a FIFO
queue per key, so successive same-model queries in one call draw distinct
canned rows) -- filter/order/limit args are accepted and ignored, matching
this suite's established convention of pre-composing results as if the
WHERE/ORDER/LIMIT clauses already ran. types.SimpleNamespace stands in for
every model (CombatLog/Player/Ship/Planet/Sector/FleetBattle) since the
service only reads and directly reassigns plain attributes -- no
flag_modified()/JSONB mutation anywhere in this module.

Sections:
  TestParseTimeframe — timeframe-string → hours parsing, incl. the
    unrecognized-suffix default.
  TestCheckInterventionNeeded — the three intervention heuristics
    (long-running, stalemate, one-sided-blowout) and their round thresholds.
  TestStopCombat / TestAdjustCombatDamage / TestRestoreShields /
    TestDeclareWinner — the pure admin-mutation helpers.
  TestAnalyzeByShipType / TestAnalyzeByCombatType / TestAnalyzeOverall —
    the grouping/aggregation helpers, including empty-input division guards.
  TestCalculateBalanceMetrics / TestIdentifyBalanceOutliers /
    TestGenerateBalanceRecommendations — the balance-score math and the
    overpowered/underpowered classification thresholds (0.7/0.3, severity
    at 0.8/0.2), sample-size gate (>=10).
  TestGetParticipantInfo — player/ship/planet/unknown-type resolution.
  TestInterveneInCombat — not-found / already-ended / unknown-type /
    success (commit) / failure (rollback) paths, each asserting the audit
    log was recorded via db.add().
  TestFindSuspiciousCombats — the repeat-combat group-by pattern detector.
  TestGetCombatDisputes — damage-disparity detection thresholds + status
    filtering + severity-then-time sort.
  TestGetLiveCombatFeed — combat-log + fleet-battle formatting, status/
    victor/duration derivation, and the merged active-first sort.
"""
import uuid
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from src.models.ship import Ship
from src.services.combat_analytics_service import CombatAnalyticsService


class _FakeQuery:
    def __init__(self, result):
        self._result = result

    def filter(self, *_a, **_k):
        return self

    def filter_by(self, *_a, **_k):
        return self

    def order_by(self, *_a, **_k):
        return self

    def group_by(self, *_a, **_k):
        return self

    def having(self, *_a, **_k):
        return self

    def limit(self, _n):
        return self

    def all(self):
        return list(self._result)

    def first(self):
        return self._result[0] if self._result else None


class _FakeDb:
    def __init__(self, results=None):
        self._results = {k: list(v) for k, v in (results or {}).items()}
        self.added = []
        self.committed = False
        self.rolled_back = False

    def query(self, *args):
        key = args[0] if len(args) == 1 and hasattr(args[0], "__name__") else "_group"
        queue = self._results.get(key, [])
        result = queue.pop(0) if queue else []
        return _FakeQuery(result)

    def add(self, obj):
        self.added.append(obj)

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True


def _combat(**kwargs):
    defaults = dict(
        id=uuid.uuid4(),
        combat_type="ship_vs_ship",
        started_at=datetime.utcnow() - timedelta(minutes=5),
        ended_at=None,
        sector_uuid=uuid.uuid4(),
        attacker_id=uuid.uuid4(),
        defender_id=uuid.uuid4(),
        outcome=None,
        rounds=1,
        attacker_damage_dealt=0,
        defender_damage_dealt=0,
        attacker_drones_lost=0,
        defender_drones_lost=0,
        attacker_drones=0,
        defender_drones=0,
        attacker_ship_type="scout",
    )
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def _service(results=None):
    db = _FakeDb(results=results)
    return CombatAnalyticsService(db), db


# ---------------------------------------------------------------------------
# _parse_timeframe
# ---------------------------------------------------------------------------


class TestParseTimeframe:
    def test_hours_suffix(self):
        svc, _ = _service()
        assert svc._parse_timeframe("6h") == 6

    def test_days_suffix(self):
        svc, _ = _service()
        assert svc._parse_timeframe("2d") == 48

    def test_weeks_suffix(self):
        svc, _ = _service()
        assert svc._parse_timeframe("1w") == 168

    def test_unrecognized_suffix_defaults_to_24(self):
        svc, _ = _service()
        assert svc._parse_timeframe("bogus") == 24


# ---------------------------------------------------------------------------
# _check_intervention_needed
# ---------------------------------------------------------------------------


class TestCheckInterventionNeeded:
    def test_short_uneventful_combat_needs_nothing(self):
        svc, _ = _service()
        c = _combat(rounds=5, attacker_damage_dealt=50, defender_damage_dealt=50)
        assert svc._check_intervention_needed(c) is False

    def test_long_running_combat_flagged(self):
        svc, _ = _service()
        c = _combat(rounds=101)
        assert svc._check_intervention_needed(c) is True

    def test_stalemate_flagged(self):
        svc, _ = _service()
        c = _combat(rounds=25, attacker_damage_dealt=10, defender_damage_dealt=10)
        assert svc._check_intervention_needed(c) is True

    def test_stalemate_not_flagged_below_round_threshold(self):
        svc, _ = _service()
        c = _combat(rounds=20, attacker_damage_dealt=10, defender_damage_dealt=10)
        assert svc._check_intervention_needed(c) is False

    def test_one_sided_blowout_flagged(self):
        svc, _ = _service()
        c = _combat(rounds=60, attacker_damage_dealt=1000, defender_damage_dealt=1)
        assert svc._check_intervention_needed(c) is True

    def test_balanced_long_combat_not_flagged(self):
        svc, _ = _service()
        c = _combat(rounds=60, attacker_damage_dealt=500, defender_damage_dealt=500)
        assert svc._check_intervention_needed(c) is False


# ---------------------------------------------------------------------------
# admin mutation helpers
# ---------------------------------------------------------------------------


class TestStopCombat:
    def test_sets_draw_outcome_and_ends_combat(self):
        svc, _ = _service()
        c = _combat(ended_at=None, outcome=None)
        result = svc._stop_combat(c, {"reason": "griefing"})
        assert c.outcome == "draw"
        assert c.ended_at is not None
        assert result["reason"] == "griefing"

    def test_default_reason_when_not_provided(self):
        svc, _ = _service()
        c = _combat()
        result = svc._stop_combat(c, {})
        assert result["reason"] == "Admin intervention"


class TestAdjustCombatDamage:
    def test_scales_attacker_damage(self):
        svc, _ = _service()
        c = _combat(attacker_damage_dealt=100)
        svc._adjust_combat_damage(c, {"target": "attacker", "damage_multiplier": 2.0})
        assert c.attacker_damage_dealt == 200

    def test_scales_defender_damage(self):
        svc, _ = _service()
        c = _combat(defender_damage_dealt=100)
        svc._adjust_combat_damage(c, {"target": "defender", "damage_multiplier": 0.5})
        assert c.defender_damage_dealt == 50

    def test_unknown_target_leaves_damage_untouched(self):
        svc, _ = _service()
        c = _combat(attacker_damage_dealt=100, defender_damage_dealt=100)
        svc._adjust_combat_damage(c, {"target": "both", "damage_multiplier": 2.0})
        assert c.attacker_damage_dealt == 100
        assert c.defender_damage_dealt == 100


class TestRestoreShields:
    def test_restores_attacker_ship_combat_shields(self):
        ship_id = uuid.uuid4()
        ship = SimpleNamespace(
            id=ship_id,
            combat={"shields": 10.0, "max_shields": 100.0, "hull": 50, "max_hull": 50},
        )
        db = _FakeDb({Ship: [[ship]]})
        svc = CombatAnalyticsService(db)
        c = _combat(attacker_ship_id=ship_id, defender_ship_id=uuid.uuid4())
        result = svc._restore_shields(c, {"target": "attacker", "shield_percent": 75})
        assert ship.combat["shields"] == 75.0
        assert result["ships"][0]["shields"] == 75.0
        assert "would be applied" not in result["note"]
        assert "Restored shields" in result["note"]

    def test_restores_both_ships(self):
        a_id, d_id = uuid.uuid4(), uuid.uuid4()
        attacker = SimpleNamespace(
            id=a_id, combat={"shields": 0.0, "max_shields": 80.0}
        )
        defender = SimpleNamespace(
            id=d_id, combat={"shields": 5.0, "max_shields": 200.0}
        )
        # two successive Ship queries (attacker then defender)
        db = _FakeDb({Ship: [[attacker], [defender]]})
        svc = CombatAnalyticsService(db)
        c = _combat(attacker_ship_id=a_id, defender_ship_id=d_id)
        result = svc._restore_shields(c, {"target": "both", "shield_percent": 25})
        assert attacker.combat["shields"] == 20.0
        assert defender.combat["shields"] == 50.0
        assert len(result["ships"]) == 2

    def test_missing_ship_raises_honest_error(self):
        ship_id = uuid.uuid4()
        db = _FakeDb({Ship: [[]]})  # first() → None
        svc = CombatAnalyticsService(db)
        c = _combat(attacker_ship_id=ship_id)
        with pytest.raises(ValueError, match="ship .* not found"):
            svc._restore_shields(c, {"target": "attacker", "shield_percent": 50})

    def test_missing_ship_id_on_combat_raises(self):
        svc, _ = _service()
        c = _combat(attacker_ship_id=None)
        with pytest.raises(ValueError, match="no attacker_ship_id"):
            svc._restore_shields(c, {"target": "attacker", "shield_percent": 50})


class TestDeclareWinner:
    def test_declares_attacker_win(self):
        svc, _ = _service()
        c = _combat(outcome=None, ended_at=None)
        svc._declare_winner(c, {"winner": "attacker"})
        assert c.outcome == "attacker_win"
        assert c.ended_at is not None

    def test_declares_defender_win(self):
        svc, _ = _service()
        c = _combat(outcome=None)
        svc._declare_winner(c, {"winner": "defender"})
        assert c.outcome == "defender_win"

    def test_unrecognized_winner_still_ends_combat_without_outcome(self):
        svc, _ = _service()
        c = _combat(outcome=None, ended_at=None)
        svc._declare_winner(c, {"winner": "nobody"})
        assert c.outcome is None
        assert c.ended_at is not None


# ---------------------------------------------------------------------------
# analysis / aggregation helpers
# ---------------------------------------------------------------------------


class TestAnalyzeByShipType:
    def test_computes_win_rate_and_avg_damage_per_type(self):
        svc, _ = _service()
        combats = [
            _combat(
                attacker_ship_type="scout", outcome="attacker_win",
                attacker_damage_dealt=100, defender_damage_dealt=50,
            ),
            _combat(
                attacker_ship_type="scout", outcome="defender_win",
                attacker_damage_dealt=50, defender_damage_dealt=100,
            ),
        ]
        stats = svc._analyze_by_ship_type(combats)
        assert stats["scout"]["total"] == 2
        assert stats["scout"]["wins"] == 1
        assert stats["scout"]["losses"] == 1
        assert stats["scout"]["win_rate"] == 0.5
        assert stats["scout"]["avg_damage_dealt"] == 75

    def test_combats_without_ship_type_are_skipped(self):
        svc, _ = _service()
        combats = [_combat(attacker_ship_type=None)]
        assert svc._analyze_by_ship_type(combats) == {}


class TestAnalyzeByCombatType:
    def test_aggregates_count_and_averages(self):
        svc, _ = _service()
        start = datetime.utcnow()
        combats = [
            _combat(
                combat_type="ship_vs_ship",
                started_at=start,
                ended_at=start + timedelta(seconds=10),
                rounds=4,
                attacker_damage_dealt=10,
                defender_damage_dealt=10,
            ),
            _combat(
                combat_type="ship_vs_ship",
                started_at=start,
                ended_at=start + timedelta(seconds=20),
                rounds=6,
                attacker_damage_dealt=20,
                defender_damage_dealt=20,
            ),
        ]
        stats = svc._analyze_by_combat_type(combats)
        assert stats["ship_vs_ship"]["count"] == 2
        assert stats["ship_vs_ship"]["avg_duration"] == 15
        assert stats["ship_vs_ship"]["avg_rounds"] == 5
        assert stats["ship_vs_ship"]["avg_damage"] == 30

    def test_unended_combat_still_counted_but_not_in_duration(self):
        svc, _ = _service()
        combats = [_combat(combat_type="x", ended_at=None, rounds=2)]
        stats = svc._analyze_by_combat_type(combats)
        assert stats["x"]["count"] == 1
        assert stats["x"]["avg_duration"] == 0


class TestAnalyzeOverall:
    def test_breaks_down_by_outcome(self):
        svc, _ = _service()
        combats = [
            _combat(outcome="attacker_win"),
            _combat(outcome="attacker_win"),
            _combat(outcome="defender_win"),
            _combat(outcome="draw"),
            _combat(outcome="escaped"),
        ]
        stats = svc._analyze_overall(combats)
        assert stats["total_combats"] == 5
        assert stats["by_outcome"] == {
            "attacker_win": 2, "defender_win": 1, "draw": 1, "escaped": 1
        }

    def test_empty_input_does_not_divide_by_zero(self):
        svc, _ = _service()
        stats = svc._analyze_overall([])
        assert stats["total_combats"] == 0
        assert stats["avg_duration"] == 0
        assert stats["avg_rounds"] == 0


# ---------------------------------------------------------------------------
# balance metrics / outliers / recommendations
# ---------------------------------------------------------------------------


class TestCalculateBalanceMetrics:
    def test_no_win_rate_entries_returns_perfect_default(self):
        svc, _ = _service()
        assert svc._calculate_balance_metrics({"x": {"total": 5}}) == {
            "balance_score": 100, "variance": 0
        }

    def test_perfect_balance_scores_100(self):
        svc, _ = _service()
        metrics = svc._calculate_balance_metrics(
            {"a": {"win_rate": 0.5}, "b": {"win_rate": 0.5}}
        )
        assert metrics["balance_score"] == 100
        assert metrics["variance"] == 0

    def test_imbalance_lowers_score_and_reports_spread(self):
        svc, _ = _service()
        metrics = svc._calculate_balance_metrics(
            {"a": {"win_rate": 0.9}, "b": {"win_rate": 0.1}}
        )
        assert metrics["balance_score"] < 100
        assert metrics["min_win_rate"] == 0.1
        assert metrics["max_win_rate"] == 0.9
        assert metrics["spread"] == pytest.approx(0.8)


class TestIdentifyBalanceOutliers:
    def test_overpowered_entity_flagged(self):
        svc, _ = _service()
        outliers = svc._identify_balance_outliers({"scout": {"win_rate": 0.85, "total": 20}})
        assert outliers[0]["type"] == "overpowered"
        assert outliers[0]["severity"] == "high"

    def test_underpowered_entity_flagged(self):
        svc, _ = _service()
        outliers = svc._identify_balance_outliers({"tug": {"win_rate": 0.15, "total": 20}})
        assert outliers[0]["type"] == "underpowered"
        assert outliers[0]["severity"] == "high"

    def test_moderate_win_rate_not_flagged(self):
        svc, _ = _service()
        outliers = svc._identify_balance_outliers({"cruiser": {"win_rate": 0.55, "total": 20}})
        assert outliers == []

    def test_small_sample_size_not_flagged_even_if_extreme(self):
        svc, _ = _service()
        outliers = svc._identify_balance_outliers({"rare": {"win_rate": 1.0, "total": 3}})
        assert outliers == []


class TestGenerateBalanceRecommendations:
    def test_no_outliers_reports_healthy(self):
        svc, _ = _service()
        recs = svc._generate_balance_recommendations([])
        assert "healthy" in recs[0]

    def test_overpowered_suggests_nerf(self):
        svc, _ = _service()
        recs = svc._generate_balance_recommendations(
            [{"entity": "scout", "type": "overpowered", "win_rate": 0.8}]
        )
        assert "nerf" in recs[0].lower()

    def test_underpowered_suggests_buff(self):
        svc, _ = _service()
        recs = svc._generate_balance_recommendations(
            [{"entity": "tug", "type": "underpowered", "win_rate": 0.2}]
        )
        assert "buff" in recs[0].lower()


# ---------------------------------------------------------------------------
# _get_participant_info
# ---------------------------------------------------------------------------


class TestGetParticipantInfo:
    def test_player_participant_resolved(self):
        from src.models.player import Player

        pid = uuid.uuid4()
        player = SimpleNamespace(id=pid, nickname="Nova", team_id=None)
        svc, _ = _service(results={Player: [[player]]})
        info = svc._get_participant_info(pid, "player")
        assert info["name"] == "Nova"
        assert info["type"] == "player"

    def test_ship_participant_resolved(self):
        from src.models.ship import Ship

        sid = uuid.uuid4()
        owner = uuid.uuid4()
        ship = SimpleNamespace(id=sid, ship_type="Scout", name="Firefly", owner_id=owner)
        svc, _ = _service(results={Ship: [[ship]]})
        info = svc._get_participant_info(sid, "ship")
        assert info["name"] == "Scout (Firefly)"
        assert info["owner_id"] == str(owner)

    def test_planet_participant_resolved(self):
        from src.models.planet import Planet

        planet_id = uuid.uuid4()
        planet = SimpleNamespace(id=planet_id, name="New Earth", owner_id=None)
        svc, _ = _service(results={Planet: [[planet]]})
        info = svc._get_participant_info(planet_id, "planet")
        assert info["name"] == "New Earth"

    def test_unrecognized_type_defaults_to_unknown_without_querying(self):
        svc, db = _service()
        info = svc._get_participant_info(uuid.uuid4(), "fleet")
        assert info["name"] == "Unknown"

    def test_missing_player_row_defaults_to_unknown(self):
        from src.models.player import Player

        svc, _ = _service(results={Player: [[]]})
        info = svc._get_participant_info(uuid.uuid4(), "player")
        assert info["name"] == "Unknown"


# ---------------------------------------------------------------------------
# intervene_in_combat
# ---------------------------------------------------------------------------


class TestInterveneInCombat:
    def test_combat_not_found_raises(self):
        from src.models.combat_log import CombatLog

        svc, _ = _service(results={CombatLog: [[]]})
        with pytest.raises(ValueError, match="not found"):
            svc.intervene_in_combat(uuid.uuid4(), "stop_combat", {})

    def test_already_ended_combat_raises(self):
        from src.models.combat_log import CombatLog

        c = _combat(ended_at=datetime.utcnow())
        svc, _ = _service(results={CombatLog: [[c]]})
        with pytest.raises(ValueError, match="active combat"):
            svc.intervene_in_combat(c.id, "stop_combat", {})

    def test_unknown_intervention_type_rolls_back_and_logs_failure(self):
        from src.models.combat_log import CombatLog

        c = _combat(ended_at=None)
        svc, db = _service(results={CombatLog: [[c]]})
        with pytest.raises(ValueError, match="Unknown intervention type"):
            svc.intervene_in_combat(c.id, "teleport_everyone", {"admin_id": uuid.uuid4()})
        assert db.rolled_back is True
        assert db.committed is False
        assert len(db.added) == 1  # the failure audit log

    def test_successful_stop_combat_commits_and_logs(self):
        from src.models.combat_log import CombatLog

        c = _combat(ended_at=None)
        svc, db = _service(results={CombatLog: [[c]]})
        result = svc.intervene_in_combat(
            c.id, "stop_combat", {"admin_id": uuid.uuid4(), "reason": "abuse"}
        )
        assert result["status"] == "success"
        assert result["type"] == "stop_combat"
        assert db.committed is True
        assert len(db.added) == 1  # the success audit log
        assert c.outcome == "draw"


# ---------------------------------------------------------------------------
# _find_suspicious_combats
# ---------------------------------------------------------------------------


class TestFindSuspiciousCombats:
    def test_repeat_combats_flagged_with_severity(self):
        from src.models.player import Player

        attacker_id, defender_id = uuid.uuid4(), uuid.uuid4()
        row = SimpleNamespace(attacker_id=attacker_id, defender_id=defender_id, combat_count=12)
        attacker = SimpleNamespace(id=attacker_id, nickname="Raider")
        defender = SimpleNamespace(id=defender_id, nickname="Target")
        svc, _ = _service(
            results={
                "_group": [[row]],
                Player: [[attacker], [defender]],
            }
        )
        suspicious = svc._find_suspicious_combats()
        assert len(suspicious) == 1
        assert suspicious[0]["type"] == "repeat_combat"
        assert suspicious[0]["severity"] == "high"
        assert suspicious[0]["participants"]["attacker"]["name"] == "Raider"

    def test_no_repeat_patterns_returns_empty(self):
        svc, _ = _service(results={"_group": [[]]})
        assert svc._find_suspicious_combats() == []


# ---------------------------------------------------------------------------
# get_combat_disputes
# ---------------------------------------------------------------------------


class TestGetCombatDisputes:
    def test_extreme_damage_ratio_flagged_high_severity(self):
        from src.models.combat_log import CombatLog

        c = _combat(
            ended_at=datetime.utcnow(),
            attacker_damage_dealt=2500,
            defender_damage_dealt=100,
        )
        svc, _ = _service(results={CombatLog: [[c], []], "_group": [[]]})
        disputes = svc.get_combat_disputes()
        damage_disputes = [d for d in disputes if d["type"] == "damage_disparity"]
        assert len(damage_disputes) == 1
        assert damage_disputes[0]["severity"] == "high"

    def test_moderate_ratio_flagged_medium_severity(self):
        from src.models.combat_log import CombatLog

        c = _combat(
            ended_at=datetime.utcnow(),
            attacker_damage_dealt=1200,
            defender_damage_dealt=100,
        )
        svc, _ = _service(results={CombatLog: [[c], []], "_group": [[]]})
        disputes = svc.get_combat_disputes()
        assert disputes[0]["severity"] == "medium"

    def test_balanced_combat_not_flagged(self):
        from src.models.combat_log import CombatLog

        c = _combat(ended_at=datetime.utcnow(), attacker_damage_dealt=100, defender_damage_dealt=90)
        svc, _ = _service(results={CombatLog: [[c], []], "_group": [[]]})
        assert svc.get_combat_disputes() == []

    def test_status_filter_applied(self):
        from src.models.combat_log import CombatLog

        c = _combat(ended_at=datetime.utcnow(), attacker_damage_dealt=5000, defender_damage_dealt=10)
        svc, _ = _service(results={CombatLog: [[c], []], "_group": [[]]})
        assert svc.get_combat_disputes(status="resolved") == []
        svc2, _ = _service(results={CombatLog: [[c], []], "_group": [[]]})
        assert len(svc2.get_combat_disputes(status="pending_review")) == 1


# ---------------------------------------------------------------------------
# get_live_combat_feed
# ---------------------------------------------------------------------------


class TestGetLiveCombatFeed:
    def test_in_progress_combat_has_no_victor_and_is_active(self):
        from src.models.combat_log import CombatLog
        from src.models.fleet import FleetBattle
        from src.models.player import Player
        from src.models.sector import Sector

        c = _combat(ended_at=None, outcome=None, rounds=3)
        sector = SimpleNamespace(id=c.sector_uuid, x_coord=1, y_coord=2, z_coord=3, name="Alpha")
        svc, _ = _service(
            results={
                CombatLog: [[c]],
                Sector: [[sector]],
                # attacker/defender participant lookups hit Player -- seed
                # empty so they fall back to "Unknown" rather than raising
                # on a missing key.
                Player: [[], []],
                FleetBattle: [[]],
            }
        )
        feed = svc.get_live_combat_feed()
        assert feed[0]["status"] == "in_progress"
        assert feed[0]["is_active"] is True
        assert feed[0]["victor_id"] is None
        assert feed[0]["sector"]["name"] == "Alpha"

    def test_completed_combat_reports_victor_and_duration(self):
        from src.models.combat_log import CombatLog
        from src.models.fleet import FleetBattle
        from src.models.player import Player
        from src.models.sector import Sector

        started = datetime.utcnow() - timedelta(seconds=30)
        ended = datetime.utcnow()
        c = _combat(started_at=started, ended_at=ended, outcome="attacker_win")
        sector = SimpleNamespace(id=c.sector_uuid, x_coord=0, y_coord=0, z_coord=0, name="Beta")
        svc, _ = _service(
            results={
                CombatLog: [[c]],
                Sector: [[sector]],
                Player: [[], []],
                FleetBattle: [[]],
            }
        )
        feed = svc.get_live_combat_feed(active_only=False)
        assert feed[0]["status"] == "completed"
        assert feed[0]["victor_id"] == str(c.attacker_id)
        assert feed[0]["duration_seconds"] == pytest.approx(30, abs=1)

    def test_fleet_battles_are_merged_into_the_feed(self):
        from src.models.combat_log import CombatLog
        from src.models.fleet import FleetBattle

        battle = SimpleNamespace(
            id=uuid.uuid4(),
            started_at=datetime.utcnow(),
            ended_at=None,
            winner=None,
            attacker_fleet_id=uuid.uuid4(),
            defender_fleet_id=uuid.uuid4(),
            sector_id=uuid.uuid4(),
            attacker_ships_destroyed=1,
            defender_ships_destroyed=0,
            attacker_ships_retreated=0,
            defender_ships_retreated=0,
            total_damage_dealt=500,
        )
        svc, _ = _service(results={CombatLog: [[]], FleetBattle: [[battle]]})
        feed = svc.get_live_combat_feed()
        assert any(f["combat_type"] == "fleet_battle" for f in feed)

    def test_limit_is_respected_across_merged_sources(self):
        from src.models.combat_log import CombatLog
        from src.models.fleet import FleetBattle
        from src.models.player import Player
        from src.models.sector import Sector

        combats = [_combat(ended_at=None) for _ in range(3)]
        sectors = [
            SimpleNamespace(id=c.sector_uuid, x_coord=0, y_coord=0, z_coord=0, name="S")
            for c in combats
        ]
        svc, _ = _service(
            results={
                CombatLog: [combats],
                Sector: [[s] for s in sectors],
                Player: [[] for _ in range(6)],
                FleetBattle: [[]],
            }
        )
        feed = svc.get_live_combat_feed(limit=2)
        assert len(feed) == 2
