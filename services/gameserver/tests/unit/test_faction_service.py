"""Unit tests — faction_service.py (faction reputation, rivalry caps, decay,
territory influence, and the SectorFactionInfluence read/write pair).

No test file existed for this service. DB-free: a `_FakeDb` keyed per-model-
class (a FIFO queue popped on each `.query(Model)` call, mirroring calls in
the order the source code issues them) stands in for the session. Real
(unattached) Faction/Reputation/SectorFactionInfluence/Player model instances
are used throughout for consistency with the rest of the suite, even though
this service does not call flag_modified() anywhere (every JSONB write here
is a plain reassignment, which SQLAlchemy's instance-level change tracking
detects on its own). `manager` (the websocket connection_manager singleton)
is monkeypatched to a recording stand-in — it's owned by websocket_service.py,
not this service, and a real send would try to publish to the cross-worker
bus / Redis, which is unavailable in a DB-free unit test.

Sections:
  TestDispatchFactionMedals — the defensive getattr/try-except medal hook.
  TestApplyFactionRepDelta — the sync in-transaction rep-delta helper (used
    by combat_service; no faction row / clamp / new-vs-existing reputation).
  TestAdjustSectorInfluence — the UPSERT write half of SectorFactionInfluence,
    including the IntegrityError-race SAVEPOINT recovery path.
  TestGetSectorInfluence — the ordered read, ties broken by faction_id.
  TestSectorTerritoryTier — the four-tier taxonomy classifier.
  TestSectorSpawnBias — the patrol/pirate multiplier derivation.
  TestDominantReputationFactionId — best-standing lookup, positive-only.
  TestFactionServiceReads — the thin query wrappers (get_all_factions, etc).
  TestInitializePlayerReputations — get-or-create across every faction.
  TestUpdateReputation — the full orchestration: rivalry cap, clamp, level
    transition, medal dispatch, WebSocket notification.
  TestApplyRivalryCap — the standalone cap-enforcement helper.
  TestApplyReputationDecay — the 30-day-inactive decay sweep.
  TestGetTradeModifier / TestCalculateTradeModifier — the two independent
    trade-modifier formulas (lookup-table vs linear-scale).
  TestCalculateReputationLevel / TestGetReputationTitle /
  TestCalculatePortAccessLevel / TestCalculateCombatResponse — the pure
    value -> classification ladders.
  TestGetFactionPricingModifier — delegates to Faction.get_pricing_modifier.
  TestCheckTerritoryAccess — sector-control lookup + access check.
  TestUpdateFactionTerritory — territory reassignment + broadcast.
"""

from datetime import datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy.exc import IntegrityError

import src.services.medal_service as medal_service_module
from src.models.faction import Faction, FactionType
from src.models.player import Player
from src.models.reputation import Reputation, ReputationLevel
from src.models.sector_faction_influence import SectorFactionInfluence
from src.models.team import Team
from src.services import faction_service
from src.services.faction_service import (
    PIRATE_SPAWN_FLOOR,
    TERRITORY_CONTESTED_MAX,
    TERRITORY_CONTESTED_MIN,
    TERRITORY_CONTROLLED_MIN,
    TERRITORY_CORE_MIN,
    TERRITORY_SECONDARY_PRESENCE_MIN,
    TRADE_MODIFIER_PUBLIC_ENEMY,
    TRADE_MODIFIERS,
    EffectiveFactionStanding,
    FactionService,
    _dispatch_faction_medals,
    adjust_sector_influence,
    apply_faction_rep_delta,
    build_effective_faction_standing,
    dominant_reputation_faction_id,
    get_sector_influence,
    resolve_effective_faction_standing_value,
    sector_spawn_bias,
    sector_territory_tier,
    trade_modifier_from_standing_value,
)


class _FakeQuery:
    def __init__(self, value):
        self._value = value

    def filter(self, *_args, **_kwargs):
        return self

    def filter_by(self, *_args, **_kwargs):
        return self

    def order_by(self, *_args, **_kwargs):
        return self

    def first(self):
        return self._value

    def all(self):
        return self._value if self._value is not None else []


class _NestedCtx:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False  # never suppress -- mirrors real begin_nested()'s propagation


class _FakeDb:
    def __init__(self, results=None):
        self._queues = {k: list(v) for k, v in (results or {}).items()}
        self.added = []
        self.committed = False
        self.refreshed = []
        self.flush_calls = 0
        self.raise_integrity_once = False

    def query(self, model):
        queue = self._queues.get(model, [])
        value = queue.pop(0) if queue else None
        return _FakeQuery(value)

    def add(self, obj):
        self.added.append(obj)

    def commit(self):
        self.committed = True

    def refresh(self, obj):
        self.refreshed.append(obj)

    def flush(self):
        self.flush_calls += 1
        if self.raise_integrity_once:
            self.raise_integrity_once = False
            raise IntegrityError("insert", {}, Exception("duplicate key"))

    def begin_nested(self):
        return _NestedCtx()


class _FakeManager:
    def __init__(self):
        self.personal_messages = []
        self.broadcasts = []

    async def send_personal_message(self, user_id, message):
        self.personal_messages.append((user_id, message))

    async def broadcast_global(self, message, exclude_user=None):
        self.broadcasts.append((message, exclude_user))


@pytest.fixture(autouse=True)
def _stub_manager(monkeypatch):
    fake = _FakeManager()
    monkeypatch.setattr(faction_service, "manager", fake)
    return fake


def _faction(**kwargs):
    f = Faction()
    f.id = kwargs.pop("id", uuid4())
    f.name = kwargs.pop("name", "Terran Federation")
    f.faction_type = kwargs.pop("faction_type", FactionType.FEDERATION)
    f.territory_sectors = kwargs.pop("territory_sectors", [])
    f.base_pricing_modifier = kwargs.pop("base_pricing_modifier", 1.0)
    for k, v in kwargs.items():
        setattr(f, k, v)
    return f


def _reputation(**kwargs):
    r = Reputation()
    r.id = kwargs.pop("id", uuid4())
    r.player_id = kwargs.pop("player_id", uuid4())
    r.faction_id = kwargs.pop("faction_id", uuid4())
    r.current_value = kwargs.pop("current_value", 0)
    r.current_level = kwargs.pop("current_level", ReputationLevel.NEUTRAL)
    r.title = kwargs.pop("title", "Neutral")
    r.last_updated = kwargs.pop("last_updated", datetime.utcnow())
    r.decay_paused = kwargs.pop("decay_paused", False)
    r.is_locked = kwargs.pop("is_locked", False)
    r.history = kwargs.pop("history", [])
    r.trade_modifier = kwargs.pop("trade_modifier", 0.0)
    r.port_access_level = kwargs.pop("port_access_level", 0)
    r.combat_response = kwargs.pop("combat_response", "neutral")
    for k, v in kwargs.items():
        setattr(r, k, v)
    return r


def _influence(**kwargs):
    i = SectorFactionInfluence()
    i.id = kwargs.pop("id", uuid4())
    i.sector_id = kwargs.pop("sector_id", uuid4())
    i.faction_id = kwargs.pop("faction_id", uuid4())
    i.influence_percentage = kwargs.pop("influence_percentage", 0.0)
    for k, v in kwargs.items():
        setattr(i, k, v)
    return i


def _player(**kwargs):
    p = Player()
    p.id = kwargs.pop("id", uuid4())
    p.user_id = kwargs.pop("user_id", uuid4())
    for k, v in kwargs.items():
        setattr(p, k, v)
    return p


# ---------------------------------------------------------------------------
# _dispatch_faction_medals
# ---------------------------------------------------------------------------


class TestDispatchFactionMedals:
    def test_calls_the_medal_hook_when_present(self, monkeypatch):
        calls = []
        monkeypatch.setattr(
            medal_service_module,
            "check_and_award_faction_medals",
            lambda db, player_id: calls.append((db, player_id)),
        )
        db, pid = object(), uuid4()
        _dispatch_faction_medals(db, pid)
        assert calls == [(db, pid)]

    def test_missing_hook_is_a_silent_noop(self, monkeypatch):
        monkeypatch.delattr(
            medal_service_module, "check_and_award_faction_medals", raising=False
        )
        _dispatch_faction_medals(object(), uuid4())  # must not raise

    def test_hook_exception_is_logged_and_swallowed(self, monkeypatch):
        def boom(db, player_id):
            raise RuntimeError("medal db down")

        monkeypatch.setattr(
            medal_service_module, "check_and_award_faction_medals", boom
        )
        _dispatch_faction_medals(object(), uuid4())  # must not raise


# ---------------------------------------------------------------------------
# apply_faction_rep_delta
# ---------------------------------------------------------------------------


class TestApplyFactionRepDelta:
    def test_missing_faction_row_returns_none(self):
        db = _FakeDb(results={Faction: [None]})
        result = apply_faction_rep_delta(
            db, uuid4(), FactionType.FEDERATION, 50, "test"
        )
        assert result is None
        assert db.flush_calls == 0

    def test_creates_a_new_reputation_when_none_exists(self):
        faction = _faction(id=uuid4())
        db = _FakeDb(results={Faction: [faction], Reputation: [None]})
        pid = uuid4()
        result = apply_faction_rep_delta(db, pid, FactionType.FEDERATION, 100, "combat")
        assert result is not None
        assert result.current_value == 100
        assert result in db.added
        assert db.flush_calls == 1

    def test_updates_an_existing_reputation_and_appends_history(self):
        faction = _faction(id=uuid4())
        existing = _reputation(faction_id=faction.id, current_value=200, history=[{"x": 1}])
        db = _FakeDb(results={Faction: [faction], Reputation: [existing]})
        result = apply_faction_rep_delta(db, uuid4(), FactionType.FEDERATION, -50, "penalty")
        assert result is existing
        assert result.current_value == 150
        assert len(result.history) == 2
        assert result.history[-1]["reason"] == "penalty"
        assert result.history[-1]["change"] == -50

    def test_clamps_to_the_800_ceiling(self):
        faction = _faction(id=uuid4())
        existing = _reputation(faction_id=faction.id, current_value=780)
        db = _FakeDb(results={Faction: [faction], Reputation: [existing]})
        result = apply_faction_rep_delta(db, uuid4(), FactionType.FEDERATION, 100, "big win")
        assert result.current_value == 800

    def test_clamps_to_the_negative_800_floor(self):
        faction = _faction(id=uuid4())
        existing = _reputation(faction_id=faction.id, current_value=-780)
        db = _FakeDb(results={Faction: [faction], Reputation: [existing]})
        result = apply_faction_rep_delta(db, uuid4(), FactionType.FEDERATION, -100, "atrocity")
        assert result.current_value == -800

    def test_does_not_commit_only_flushes(self):
        faction = _faction(id=uuid4())
        db = _FakeDb(results={Faction: [faction], Reputation: [None]})
        apply_faction_rep_delta(db, uuid4(), FactionType.FEDERATION, 10, "x")
        assert db.committed is False
        assert db.flush_calls == 1

    def test_derived_fields_are_recomputed_via_a_real_faction_service(self):
        faction = _faction(id=uuid4())
        db = _FakeDb(results={Faction: [faction], Reputation: [None]})
        result = apply_faction_rep_delta(db, uuid4(), FactionType.FEDERATION, 650, "grind")
        assert result.current_level == ReputationLevel.REVERED  # 650 falls in the [600,700) band
        assert result.combat_response == "friendly"
        assert result.port_access_level == 3

    def test_faction_name_scopes_to_named_row(self):
        """LEG-97: optional faction_name must credit that roster row's id."""
        fc = _faction(
            id=uuid4(),
            name="Frontier Coalition",
            faction_type=FactionType.INDEPENDENTS,
        )
        db = _FakeDb(results={Faction: [fc], Reputation: [None]})
        result = apply_faction_rep_delta(
            db,
            uuid4(),
            FactionType.INDEPENDENTS,
            5,
            "mining_harvest_frontier_unclaimed",
            faction_name="Frontier Coalition",
        )
        assert result is not None
        assert result.faction_id == fc.id
        assert result.current_value == 5

    def test_faction_name_miss_returns_none_without_fallback(self):
        """Named miss must not silently apply to another Independents row.

        FakeDb ignores SQL predicates, so a miss is simulated by queuing
        None for the Faction lookup — the production path adds
        ``Faction.name == faction_name`` before ``.first()``.
        """
        db = _FakeDb(results={Faction: [None]})
        result = apply_faction_rep_delta(
            db,
            uuid4(),
            FactionType.INDEPENDENTS,
            5,
            "mining_harvest_frontier_unclaimed",
            faction_name="Frontier Coalition",
        )
        assert result is None
        assert db.added == []
        assert db.flush_calls == 0

    def test_faction_name_filter_is_applied_in_query_chain(self):
        """Prove the name predicate is attached (FakeDb cannot eval SQL)."""
        recorded = []
        fc = _faction(
            id=uuid4(),
            name="Frontier Coalition",
            faction_type=FactionType.INDEPENDENTS,
        )

        class _RecordingQuery(_FakeQuery):
            def filter(self, *args, **kwargs):
                recorded.extend(args)
                return self

        class _RecordingDb(_FakeDb):
            def query(self, model):
                queue = self._queues.get(model, [])
                value = queue.pop(0) if queue else None
                return _RecordingQuery(value)

        db = _RecordingDb(results={Faction: [fc], Reputation: [None]})
        apply_faction_rep_delta(
            db,
            uuid4(),
            FactionType.INDEPENDENTS,
            5,
            "test",
            faction_name="Frontier Coalition",
        )
        assert len(recorded) >= 2
        # Second filter clause is the name equality (ColumnElement).
        name_clause = recorded[1]
        assert getattr(name_clause.left, "key", None) == "name" or "name" in str(
            name_clause
        )
        assert "Frontier Coalition" in str(name_clause.right.value if hasattr(name_clause, "right") else name_clause)


# ---------------------------------------------------------------------------
# adjust_sector_influence
# ---------------------------------------------------------------------------


class TestAdjustSectorInfluence:
    def test_none_faction_id_is_a_noop(self):
        db = _FakeDb()
        assert adjust_sector_influence(db, uuid4(), None, 10.0) is None

    def test_none_sector_id_is_a_noop(self):
        db = _FakeDb()
        assert adjust_sector_influence(db, None, uuid4(), 10.0) is None

    def test_adds_delta_to_an_existing_row(self):
        row = _influence(influence_percentage=40.0)
        db = _FakeDb(results={SectorFactionInfluence: [row]})
        result = adjust_sector_influence(db, row.sector_id, row.faction_id, 15.0)
        assert result is row
        assert result.influence_percentage == 55.0
        assert db.flush_calls == 1

    def test_clamps_to_100_ceiling(self):
        row = _influence(influence_percentage=90.0)
        db = _FakeDb(results={SectorFactionInfluence: [row]})
        result = adjust_sector_influence(db, row.sector_id, row.faction_id, 50.0)
        assert result.influence_percentage == 100.0

    def test_clamps_to_0_floor(self):
        row = _influence(influence_percentage=10.0)
        db = _FakeDb(results={SectorFactionInfluence: [row]})
        result = adjust_sector_influence(db, row.sector_id, row.faction_id, -50.0)
        assert result.influence_percentage == 0.0

    def test_creates_a_new_row_when_none_exists(self):
        db = _FakeDb(results={SectorFactionInfluence: [None]})
        sector_id, faction_id = uuid4(), uuid4()
        result = adjust_sector_influence(db, sector_id, faction_id, 20.0)
        assert result.influence_percentage == 20.0
        assert result in db.added
        # one flush inside the begin_nested() insert, one after applying the delta.
        assert db.flush_calls == 2

    def test_race_loser_recovers_via_savepoint_and_applies_delta_to_winner(self):
        winner_row = _influence(influence_percentage=30.0)
        db = _FakeDb(
            results={SectorFactionInfluence: [None, winner_row]}
        )
        db.raise_integrity_once = True
        result = adjust_sector_influence(db, winner_row.sector_id, winner_row.faction_id, 10.0)
        assert result is winner_row
        assert result.influence_percentage == 40.0

    def test_race_loser_still_missing_after_recovery_degrades_to_none(self):
        db = _FakeDb(results={SectorFactionInfluence: [None, None]})
        db.raise_integrity_once = True
        result = adjust_sector_influence(db, uuid4(), uuid4(), 10.0)
        assert result is None


# ---------------------------------------------------------------------------
# get_sector_influence
# ---------------------------------------------------------------------------


class TestGetSectorInfluence:
    def test_none_sector_id_returns_empty_list(self):
        db = _FakeDb()
        assert get_sector_influence(db, None) == []

    def test_returns_rows_from_the_query(self):
        rows = [_influence(influence_percentage=80.0), _influence(influence_percentage=20.0)]
        db = _FakeDb(results={SectorFactionInfluence: [rows]})
        assert get_sector_influence(db, uuid4()) == rows


# ---------------------------------------------------------------------------
# sector_territory_tier
# ---------------------------------------------------------------------------


class TestSectorTerritoryTier:
    def test_empty_rows_is_uncontrolled(self):
        assert sector_territory_tier([]) == "uncontrolled"

    def test_zero_top_influence_is_uncontrolled(self):
        assert sector_territory_tier([_influence(influence_percentage=0.0)]) == "uncontrolled"

    def test_100_percent_is_core(self):
        assert sector_territory_tier([_influence(influence_percentage=TERRITORY_CORE_MIN)]) == "core"

    def test_at_controlled_threshold_is_controlled(self):
        rows = [_influence(influence_percentage=TERRITORY_CONTROLLED_MIN)]
        assert sector_territory_tier(rows) == "controlled"

    def test_contested_band_with_a_material_secondary_is_contested(self):
        rows = [
            _influence(influence_percentage=(TERRITORY_CONTESTED_MIN + TERRITORY_CONTESTED_MAX) / 2),
            _influence(influence_percentage=TERRITORY_SECONDARY_PRESENCE_MIN),
        ]
        assert sector_territory_tier(rows) == "contested"

    def test_contested_band_without_a_material_secondary_is_uncontrolled(self):
        rows = [
            _influence(influence_percentage=(TERRITORY_CONTESTED_MIN + TERRITORY_CONTESTED_MAX) / 2),
            _influence(influence_percentage=TERRITORY_SECONDARY_PRESENCE_MIN - 1),
        ]
        assert sector_territory_tier(rows) == "uncontrolled"

    def test_single_weak_holder_below_controlled_is_uncontrolled(self):
        rows = [_influence(influence_percentage=TERRITORY_CONTROLLED_MIN - 1)]
        assert sector_territory_tier(rows) == "uncontrolled"


# ---------------------------------------------------------------------------
# sector_spawn_bias
# ---------------------------------------------------------------------------


class TestSectorSpawnBias:
    def test_no_rows_is_reproduce_exactly_neutral(self):
        db = _FakeDb(results={SectorFactionInfluence: [[]]})
        result = sector_spawn_bias(db, uuid4())
        assert result == {
            "tier": "uncontrolled",
            "dominant_faction_id": None,
            "dominant_influence": 0.0,
            "patrol_multiplier": 1.0,
            "pirate_multiplier": 1.0,
        }

    def test_full_core_influence_maxes_patrol_and_floors_pirate(self):
        row = _influence(influence_percentage=100.0)
        db = _FakeDb(results={SectorFactionInfluence: [[row]]})
        result = sector_spawn_bias(db, row.sector_id)
        assert result["tier"] == "core"
        assert result["dominant_faction_id"] == row.faction_id
        assert result["patrol_multiplier"] == 2.0
        assert result["pirate_multiplier"] == PIRATE_SPAWN_FLOOR

    def test_half_influence_splits_the_difference(self):
        row = _influence(influence_percentage=50.0)
        db = _FakeDb(results={SectorFactionInfluence: [[row]]})
        result = sector_spawn_bias(db, row.sector_id)
        assert result["patrol_multiplier"] == 1.5
        assert result["pirate_multiplier"] == 0.5


# ---------------------------------------------------------------------------
# dominant_reputation_faction_id
# ---------------------------------------------------------------------------


class TestDominantReputationFactionId:
    def test_none_player_id_returns_none(self):
        db = _FakeDb()
        assert dominant_reputation_faction_id(db, None) is None

    def test_no_reputation_rows_returns_none(self):
        db = _FakeDb(results={Reputation: [None]})
        assert dominant_reputation_faction_id(db, uuid4()) is None

    def test_best_standing_at_or_below_zero_returns_none(self):
        rep = _reputation(current_value=0)
        db = _FakeDb(results={Reputation: [rep]})
        assert dominant_reputation_faction_id(db, uuid4()) is None

    def test_positive_best_standing_returns_its_faction_id(self):
        rep = _reputation(current_value=250, faction_id=uuid4())
        db = _FakeDb(results={Reputation: [rep]})
        assert dominant_reputation_faction_id(db, uuid4()) == rep.faction_id


# ---------------------------------------------------------------------------
# FactionService — thin read wrappers
# ---------------------------------------------------------------------------


class TestFactionServiceReads:
    @pytest.mark.asyncio
    async def test_get_all_factions(self):
        factions = [_faction(), _faction()]
        db = _FakeDb(results={Faction: [factions]})
        svc = FactionService(db)
        assert await svc.get_all_factions() == factions

    @pytest.mark.asyncio
    async def test_get_faction_by_id_found(self):
        f = _faction()
        db = _FakeDb(results={Faction: [f]})
        svc = FactionService(db)
        assert await svc.get_faction_by_id(f.id) is f

    @pytest.mark.asyncio
    async def test_get_faction_by_type_not_found(self):
        db = _FakeDb(results={Faction: [None]})
        svc = FactionService(db)
        assert await svc.get_faction_by_type(FactionType.PIRATES) is None

    @pytest.mark.asyncio
    async def test_get_player_reputation(self):
        rep = _reputation()
        db = _FakeDb(results={Reputation: [rep]})
        svc = FactionService(db)
        assert await svc.get_player_reputation(rep.player_id, rep.faction_id) is rep

    @pytest.mark.asyncio
    async def test_get_all_player_reputations(self):
        reps = [_reputation(), _reputation()]
        db = _FakeDb(results={Reputation: [reps]})
        svc = FactionService(db)
        assert await svc.get_all_player_reputations(uuid4()) == reps


# ---------------------------------------------------------------------------
# initialize_player_reputations
# ---------------------------------------------------------------------------


class TestInitializePlayerReputations:
    @pytest.mark.asyncio
    async def test_creates_a_reputation_for_every_faction_missing_one(self):
        f1, f2 = _faction(), _faction()
        pid = uuid4()
        db = _FakeDb(results={Faction: [[f1, f2]], Reputation: [None, None]})
        svc = FactionService(db)
        result = await svc.initialize_player_reputations(pid)
        assert len(result) == 2
        assert len(db.added) == 2
        assert all(r.current_value == 0 for r in result)
        assert db.committed is True

    @pytest.mark.asyncio
    async def test_reuses_an_existing_reputation_without_creating_a_duplicate(self):
        f1 = _faction()
        existing = _reputation(faction_id=f1.id)
        pid = uuid4()
        db = _FakeDb(results={Faction: [[f1]], Reputation: [existing]})
        svc = FactionService(db)
        result = await svc.initialize_player_reputations(pid)
        assert result == [existing]
        assert db.added == []


# ---------------------------------------------------------------------------
# update_reputation
# ---------------------------------------------------------------------------


class TestUpdateReputation:
    @pytest.mark.asyncio
    async def test_positive_change_updates_value_and_derived_fields(self):
        # 0 -> 50 crosses NEUTRAL -> RECOGNIZED, so the method's level-
        # transition branch runs and needs a real linked Player + faction.
        faction_id = uuid4()
        faction = _faction(id=faction_id)
        rep = _reputation(faction_id=faction_id, current_value=0)
        rep.faction = faction
        player = _player(id=rep.player_id)
        db = _FakeDb(results={Reputation: [rep], Player: [player]})
        svc = FactionService(db)
        result = await svc.update_reputation(rep.player_id, faction_id, 50, reason="trade run")
        assert result.current_value == 50
        assert result.history[-1]["reason"] == "trade run"
        assert db.committed is True

    @pytest.mark.asyncio
    async def test_negative_change_skips_the_rivalry_cap_entirely(self):
        # 150 -> 120 stays within the ACKNOWLEDGED band [100,199] -- no level
        # transition, so the Player/faction-dependent notification branch
        # never runs and this test can stay scoped to the rivalry-cap skip.
        faction_id = uuid4()
        rep = _reputation(
            faction_id=faction_id,
            current_value=150,
            current_level=ReputationLevel.ACKNOWLEDGED,
        )
        db = _FakeDb(results={Reputation: [rep]})
        svc = FactionService(db)
        result = await svc.update_reputation(rep.player_id, faction_id, -30, reason="crime")
        assert result.current_value == 120
        # no Faction queries should have been issued for a negative change
        assert Faction not in db._queues or db._queues[Faction] == []

    @pytest.mark.asyncio
    async def test_level_transition_sends_a_websocket_notification(self, _stub_manager):
        faction_id = uuid4()
        faction = _faction(id=faction_id, name="Terran Federation")
        # NEUTRAL (0) -> RECOGNIZED (>=50) is a level transition.
        rep = _reputation(faction_id=faction_id, current_value=0, current_level=ReputationLevel.NEUTRAL)
        rep.faction = faction
        player = _player(id=rep.player_id)
        db = _FakeDb(results={Reputation: [rep], Player: [player]})
        svc = FactionService(db)
        await svc.update_reputation(rep.player_id, faction_id, 60, reason="mission")
        assert len(_stub_manager.personal_messages) == 1
        user_id, message = _stub_manager.personal_messages[0]
        assert user_id == str(player.user_id)
        assert message["type"] == "reputation_changed"
        assert message["faction_name"] == "Terran Federation"

    @pytest.mark.asyncio
    async def test_level_transition_with_no_linked_user_sends_nothing(self, _stub_manager):
        faction_id = uuid4()
        rep = _reputation(faction_id=faction_id, current_value=0, current_level=ReputationLevel.NEUTRAL)
        db = _FakeDb(results={Reputation: [rep], Player: [None]})
        svc = FactionService(db)
        await svc.update_reputation(rep.player_id, faction_id, 60, reason="mission")
        assert _stub_manager.personal_messages == []

    @pytest.mark.asyncio
    async def test_same_level_change_sends_no_notification(self, _stub_manager):
        faction_id = uuid4()
        rep = _reputation(faction_id=faction_id, current_value=10, current_level=ReputationLevel.NEUTRAL)
        db = _FakeDb(results={Reputation: [rep]})
        svc = FactionService(db)
        await svc.update_reputation(rep.player_id, faction_id, 5, reason="small")
        assert _stub_manager.personal_messages == []

    @pytest.mark.asyncio
    async def test_reaching_honored_dispatches_the_medal_hook(self, monkeypatch):
        calls = []
        monkeypatch.setattr(
            medal_service_module,
            "check_and_award_faction_medals",
            lambda db, player_id: calls.append(player_id),
        )
        faction_id = uuid4()
        # RESPECTED (300) -> HONORED (500) on +200.
        rep = _reputation(
            faction_id=faction_id, current_value=300, current_level=ReputationLevel.RESPECTED
        )
        db = _FakeDb(results={Reputation: [rep], Player: [None]})
        svc = FactionService(db)
        await svc.update_reputation(rep.player_id, faction_id, 200, reason="grind")
        assert calls == [rep.player_id]

    @pytest.mark.asyncio
    async def test_missing_reputation_initializes_before_updating(self):
        faction_id = uuid4()
        pid = uuid4()
        f1 = _faction(id=faction_id)
        created = _reputation(player_id=pid, faction_id=faction_id, current_value=0)
        # 1st get_player_reputation -> None: triggers initialize_player_reputations
        #   -> get_all_factions -> [f1]; get_player_reputation(f1) -> None (creates)
        # 2nd get_player_reputation (post-init) -> the created row.
        db = _FakeDb(
            results={
                Reputation: [None, None, created],
                Faction: [[f1]],
            }
        )
        svc = FactionService(db)
        result = await svc.update_reputation(pid, faction_id, 20, reason="first contact")
        assert result.current_value == 20


# ---------------------------------------------------------------------------
# _apply_rivalry_cap
# ---------------------------------------------------------------------------


class TestApplyRivalryCap:
    def test_unknown_target_faction_returns_change_unchanged(self):
        db = _FakeDb(results={Faction: [None]})
        svc = FactionService(db)
        assert svc._apply_rivalry_cap(uuid4(), uuid4(), 0, 100) == 100

    def test_faction_with_no_rivalry_entry_returns_change_unchanged(self):
        faction = _faction(name="Independent Traders")
        db = _FakeDb(results={Faction: [faction]})
        svc = FactionService(db)
        assert svc._apply_rivalry_cap(uuid4(), faction.id, 0, 100) == 100

    def test_rival_faction_row_missing_returns_change_unchanged(self):
        faction = _faction(name="Terran Federation")
        db = _FakeDb(results={Faction: [faction, None]})
        svc = FactionService(db)
        assert svc._apply_rivalry_cap(uuid4(), faction.id, 0, 100) == 100

    def test_rival_at_or_below_zero_applies_no_cap(self):
        faction = _faction(name="Terran Federation")
        rival = _faction(name="Fringe Alliance")
        db = _FakeDb(results={Faction: [faction, rival], Reputation: [None]})
        svc = FactionService(db)
        assert svc._apply_rivalry_cap(uuid4(), faction.id, 700, 100) == 100

    def test_positive_rival_reputation_caps_the_combined_total(self):
        faction = _faction(name="Terran Federation")
        rival = _faction(name="Fringe Alliance")
        rival_rep = _reputation(current_value=500)
        db = _FakeDb(results={Faction: [faction, rival], Reputation: [rival_rep]})
        svc = FactionService(db)
        # cap = 800; current 400 + change 200 + rival 500 = 1100 > 800
        # allowed = max(0, 800 - 500 - 400) = 0
        allowed = svc._apply_rivalry_cap(uuid4(), faction.id, 400, 200)
        assert allowed == 0

    def test_projected_total_within_cap_is_unclamped(self):
        faction = _faction(name="Terran Federation")
        rival = _faction(name="Fringe Alliance")
        rival_rep = _reputation(current_value=100)
        db = _FakeDb(results={Faction: [faction, rival], Reputation: [rival_rep]})
        svc = FactionService(db)
        # 200 + 50 + 100 = 350 <= 800 -- no cap needed
        assert svc._apply_rivalry_cap(uuid4(), faction.id, 200, 50) == 50


# ---------------------------------------------------------------------------
# apply_reputation_decay
# ---------------------------------------------------------------------------


class TestApplyReputationDecay:
    @pytest.mark.asyncio
    async def test_within_neutral_band_is_skipped(self):
        rep = _reputation(current_value=50, last_updated=datetime.utcnow() - timedelta(days=60))
        db = _FakeDb(results={Reputation: [[rep]]})
        svc = FactionService(db)
        result = await svc.apply_reputation_decay(uuid4())
        assert result == []
        assert rep.current_value == 50

    @pytest.mark.asyncio
    async def test_locked_reputation_is_skipped(self):
        rep = _reputation(current_value=300, is_locked=True, last_updated=datetime.utcnow() - timedelta(days=60))
        db = _FakeDb(results={Reputation: [[rep]]})
        svc = FactionService(db)
        result = await svc.apply_reputation_decay(uuid4())
        assert result == []

    @pytest.mark.asyncio
    async def test_decay_paused_reputation_is_skipped(self):
        rep = _reputation(current_value=300, decay_paused=True, last_updated=datetime.utcnow() - timedelta(days=60))
        db = _FakeDb(results={Reputation: [[rep]]})
        svc = FactionService(db)
        result = await svc.apply_reputation_decay(uuid4())
        assert result == []

    @pytest.mark.asyncio
    async def test_within_30_day_window_is_skipped(self):
        rep = _reputation(current_value=300, last_updated=datetime.utcnow() - timedelta(days=10))
        db = _FakeDb(results={Reputation: [[rep]]})
        svc = FactionService(db)
        result = await svc.apply_reputation_decay(uuid4())
        assert result == []

    @pytest.mark.asyncio
    async def test_positive_reputation_decays_toward_but_not_below_100(self):
        rep = _reputation(current_value=300, last_updated=datetime.utcnow() - timedelta(days=45))
        db = _FakeDb(results={Reputation: [[rep]]})
        svc = FactionService(db)
        result = await svc.apply_reputation_decay(uuid4())
        assert len(result) == 1
        assert rep.current_value == max(100, 300 - 15)
        assert db.committed is True

    @pytest.mark.asyncio
    async def test_negative_reputation_decays_toward_but_not_above_negative_100(self):
        rep = _reputation(current_value=-300, last_updated=datetime.utcnow() - timedelta(days=45))
        db = _FakeDb(results={Reputation: [[rep]]})
        svc = FactionService(db)
        await svc.apply_reputation_decay(uuid4())
        assert rep.current_value == min(-100, -300 + 15)

    @pytest.mark.asyncio
    async def test_decay_is_capped_at_50_points_per_call(self):
        rep = _reputation(current_value=800, last_updated=datetime.utcnow() - timedelta(days=200))
        db = _FakeDb(results={Reputation: [[rep]]})
        svc = FactionService(db)
        await svc.apply_reputation_decay(uuid4())
        assert rep.current_value == 750  # 800 - min(170, 50)

    @pytest.mark.asyncio
    async def test_no_decayed_rows_does_not_commit(self):
        rep = _reputation(current_value=0, last_updated=datetime.utcnow() - timedelta(days=100))
        db = _FakeDb(results={Reputation: [[rep]]})
        svc = FactionService(db)
        await svc.apply_reputation_decay(uuid4())
        assert db.committed is False


# ---------------------------------------------------------------------------
# get_trade_modifier (lookup-table variant)
# ---------------------------------------------------------------------------


class TestGetTradeModifier:
    @pytest.mark.asyncio
    async def test_no_reputation_record_returns_neutral(self):
        db = _FakeDb(results={Player: [None], Reputation: [None]})
        svc = FactionService(db)
        assert await svc.get_trade_modifier(uuid4(), uuid4()) == 1.0

    @pytest.mark.parametrize(
        "value,expected",
        [
            (700, 0.85),
            (500, 0.90),
            (300, 0.95),
            (100, 0.97),
            (0, 1.00),
            (-99, 1.00),
            (-299, 1.05),
            (-499, 1.15),
            (-699, 1.30),
            (-800, TRADE_MODIFIER_PUBLIC_ENEMY),
        ],
    )
    @pytest.mark.asyncio
    async def test_threshold_ladder(self, value, expected):
        rep = _reputation(current_value=value)
        db = _FakeDb(results={Player: [None], Reputation: [rep]})
        svc = FactionService(db)
        assert await svc.get_trade_modifier(uuid4(), uuid4()) == expected

    def test_thresholds_are_defined_high_to_low(self):
        values = [t for t, _ in TRADE_MODIFIERS]
        assert values == sorted(values, reverse=True)


# ---------------------------------------------------------------------------
# _calculate_reputation_level
# ---------------------------------------------------------------------------


class TestCalculateReputationLevel:
    @pytest.mark.parametrize(
        "value,expected",
        [
            (700, ReputationLevel.EXALTED),
            (600, ReputationLevel.REVERED),
            (500, ReputationLevel.HONORED),
            (400, ReputationLevel.VALUED),
            (300, ReputationLevel.RESPECTED),
            (200, ReputationLevel.TRUSTED),
            (100, ReputationLevel.ACKNOWLEDGED),
            (50, ReputationLevel.RECOGNIZED),
            (0, ReputationLevel.NEUTRAL),
            (-50, ReputationLevel.NEUTRAL),
            (-100, ReputationLevel.QUESTIONABLE),
            (-200, ReputationLevel.SUSPICIOUS),
            (-300, ReputationLevel.UNTRUSTWORTHY),
            (-400, ReputationLevel.SMUGGLER),
            (-500, ReputationLevel.PIRATE),
            (-600, ReputationLevel.OUTLAW),
            (-700, ReputationLevel.CRIMINAL),
            (-800, ReputationLevel.PUBLIC_ENEMY),
        ],
    )
    def test_ladder(self, value, expected):
        svc = FactionService(_FakeDb())
        assert svc._calculate_reputation_level(value) == expected


# ---------------------------------------------------------------------------
# _get_reputation_title
# ---------------------------------------------------------------------------


class TestGetReputationTitle:
    def test_known_level_returns_its_title(self):
        svc = FactionService(_FakeDb())
        assert svc._get_reputation_title(ReputationLevel.HONORED) == "Honored"

    def test_every_level_has_a_title(self):
        svc = FactionService(_FakeDb())
        for level in ReputationLevel:
            assert svc._get_reputation_title(level) != "Unknown"


# ---------------------------------------------------------------------------
# _calculate_trade_modifier (linear-scale variant, distinct from get_trade_modifier)
# ---------------------------------------------------------------------------


class TestCalculateTradeModifier:
    def test_zero_is_zero(self):
        svc = FactionService(_FakeDb())
        assert svc._calculate_trade_modifier(0) == 0.0

    def test_max_positive_is_030(self):
        svc = FactionService(_FakeDb())
        assert svc._calculate_trade_modifier(800) == 0.3

    def test_max_negative_is_negative_030(self):
        svc = FactionService(_FakeDb())
        assert svc._calculate_trade_modifier(-800) == -0.3


# ---------------------------------------------------------------------------
# _calculate_port_access_level
# ---------------------------------------------------------------------------


class TestCalculatePortAccessLevel:
    @pytest.mark.parametrize(
        "value,expected",
        [(600, 3), (599, 2), (200, 2), (199, 1), (-200, 1), (-201, 0)],
    )
    def test_ladder(self, value, expected):
        svc = FactionService(_FakeDb())
        assert svc._calculate_port_access_level(value) == expected


# ---------------------------------------------------------------------------
# _calculate_combat_response
# ---------------------------------------------------------------------------


class TestCalculateCombatResponse:
    @pytest.mark.parametrize(
        "value,expected",
        [(400, "friendly"), (399, "neutral"), (-200, "neutral"), (-201, "hostile")],
    )
    def test_ladder(self, value, expected):
        svc = FactionService(_FakeDb())
        assert svc._calculate_combat_response(value) == expected


# ---------------------------------------------------------------------------
# get_faction_pricing_modifier
# ---------------------------------------------------------------------------


class TestGetFactionPricingModifier:
    @pytest.mark.asyncio
    async def test_unknown_faction_returns_neutral(self):
        db = _FakeDb(results={Faction: [None]})
        svc = FactionService(db)
        assert await svc.get_faction_pricing_modifier(uuid4(), uuid4()) == 1.0

    @pytest.mark.asyncio
    async def test_no_reputation_uses_the_factions_base_modifier(self):
        faction = _faction(base_pricing_modifier=1.1)
        db = _FakeDb(results={Faction: [faction], Reputation: [None]})
        svc = FactionService(db)
        result = await svc.get_faction_pricing_modifier(uuid4(), faction.id)
        assert result == 1.1

    @pytest.mark.asyncio
    async def test_delegates_to_the_factions_get_pricing_modifier(self):
        faction = _faction(base_pricing_modifier=1.0)
        rep = _reputation(current_value=650)
        db = _FakeDb(results={Faction: [faction], Reputation: [rep]})
        svc = FactionService(db)
        result = await svc.get_faction_pricing_modifier(uuid4(), faction.id)
        assert result == faction.get_pricing_modifier(650)


# ---------------------------------------------------------------------------
# check_territory_access
# ---------------------------------------------------------------------------


class TestCheckTerritoryAccess:
    @pytest.mark.asyncio
    async def test_uncontrolled_sector_is_always_allowed(self):
        sector_id = uuid4()
        faction = _faction(territory_sectors=[])
        db = _FakeDb(results={Faction: [[faction]]})
        svc = FactionService(db)
        result = await svc.check_territory_access(uuid4(), sector_id)
        assert result["allowed"] is True
        assert result["reason"] == "Neutral territory"

    @pytest.mark.asyncio
    async def test_controlled_sector_with_no_reputation_is_denied(self):
        sector_id = uuid4()
        faction = _faction(name="Pirates United", territory_sectors=[sector_id])
        db = _FakeDb(results={Faction: [[faction]], Reputation: [None]})
        svc = FactionService(db)
        result = await svc.check_territory_access(uuid4(), sector_id)
        assert result["allowed"] is False
        assert "No standing" in result["reason"]

    @pytest.mark.asyncio
    async def test_controlled_sector_with_sufficient_reputation_is_allowed(self):
        sector_id = uuid4()
        faction = _faction(faction_type=FactionType.MERCHANTS, territory_sectors=[sector_id])
        rep = _reputation(current_value=0)
        db = _FakeDb(results={Faction: [[faction]], Reputation: [rep]})
        svc = FactionService(db)
        result = await svc.check_territory_access(uuid4(), sector_id)
        assert result["allowed"] is True

    @pytest.mark.asyncio
    async def test_controlled_sector_with_insufficient_reputation_is_denied(self):
        sector_id = uuid4()
        faction = _faction(faction_type=FactionType.PIRATES, territory_sectors=[sector_id])
        rep = _reputation(current_value=-250)
        db = _FakeDb(results={Faction: [[faction]], Reputation: [rep]})
        svc = FactionService(db)
        result = await svc.check_territory_access(uuid4(), sector_id)
        assert result["allowed"] is False
        assert "Insufficient reputation" in result["reason"]


# ---------------------------------------------------------------------------
# update_faction_territory
# ---------------------------------------------------------------------------


class TestUpdateFactionTerritory:
    @pytest.mark.asyncio
    async def test_unknown_faction_raises(self):
        db = _FakeDb(results={Faction: [None]})
        svc = FactionService(db)
        with pytest.raises(ValueError):
            await svc.update_faction_territory(uuid4(), [uuid4()])

    @pytest.mark.asyncio
    async def test_updates_territory_commits_and_broadcasts(self, _stub_manager):
        faction = _faction(territory_sectors=[])
        new_sectors = [uuid4(), uuid4()]
        db = _FakeDb(results={Faction: [faction]})
        svc = FactionService(db)
        result = await svc.update_faction_territory(faction.id, new_sectors)
        assert result.territory_sectors == new_sectors
        assert db.committed is True
        assert faction in db.refreshed
        assert len(_stub_manager.broadcasts) == 1
        message, _exclude = _stub_manager.broadcasts[0]
        assert message["type"] == "faction_territory_changed"
        assert message["sectors"] == [str(sid) for sid in new_sectors]


# ---------------------------------------------------------------------------
# effective_faction_standing / team aggregate consumers (LEG-800)
# ---------------------------------------------------------------------------


class TestEffectiveFactionStanding:
    def test_solo_player_uses_personal_reputation(self):
        player_id = uuid4()
        faction_id = uuid4()
        rep = _reputation(player_id=player_id, faction_id=faction_id, current_value=100)
        db = _FakeDb(results={Player: [None], Reputation: [rep]})
        value, source = resolve_effective_faction_standing_value(
            db, player_id, faction_id
        )
        assert source == "personal"
        assert value == 100

    def test_team_player_uses_team_aggregate(self, monkeypatch):
        player_id = uuid4()
        faction_id = uuid4()
        team_id = uuid4()
        player = Player(id=player_id, team_id=team_id)
        team = Team(id=team_id, name="Test Team", leader_id=player_id)

        def _fake_get_team_reputation(_db, _team, *, now=None):
            assert _team.id == team_id
            return {
                "standings": {
                    str(faction_id): {
                        "faction_id": str(faction_id),
                        "value": 700,
                        "level": ReputationLevel.EXALTED.value,
                    }
                }
            }

        monkeypatch.setattr(
            "src.services.team_reputation_service.get_team_reputation",
            _fake_get_team_reputation,
        )

        db = _FakeDb(results={Player: [player], Team: [team]})
        value, source = resolve_effective_faction_standing_value(
            db, player_id, faction_id
        )
        assert source == "team"
        assert value == 700

    @pytest.mark.asyncio
    async def test_get_trade_modifier_team_average_changes_pricing(self, monkeypatch):
        player_id = uuid4()
        faction_id = uuid4()
        team_id = uuid4()
        player = Player(id=player_id, team_id=team_id)
        team = Team(id=team_id, name="Avg Team", leader_id=player_id)
        personal_rep = _reputation(
            player_id=player_id, faction_id=faction_id, current_value=0
        )

        def _fake_get_team_reputation(_db, _team, *, now=None):
            return {
                "standings": {
                    str(faction_id): {
                        "faction_id": str(faction_id),
                        "value": 700,
                        "level": ReputationLevel.EXALTED.value,
                    }
                }
            }

        monkeypatch.setattr(
            "src.services.team_reputation_service.get_team_reputation",
            _fake_get_team_reputation,
        )

        db = _FakeDb(results={Player: [player], Team: [team]})
        svc = FactionService(db)
        assert await svc.get_trade_modifier(player_id, faction_id) == 0.85
        assert trade_modifier_from_standing_value(0) == 1.0

    @pytest.mark.asyncio
    async def test_get_trade_modifier_solo_unchanged(self):
        player_id = uuid4()
        faction_id = uuid4()
        rep = _reputation(player_id=player_id, faction_id=faction_id, current_value=0)
        db = _FakeDb(results={Player: [None], Reputation: [rep]})
        svc = FactionService(db)
        assert await svc.get_trade_modifier(player_id, faction_id) == 1.0

    def test_build_effective_maps_helpers(self):
        player_id = uuid4()
        faction_id = uuid4()
        rep = _reputation(player_id=player_id, faction_id=faction_id, current_value=600)
        db = _FakeDb(results={Player: [None], Reputation: [rep]})
        svc = FactionService(db)
        standing = build_effective_faction_standing(db, player_id, faction_id, svc=svc)
        assert isinstance(standing, EffectiveFactionStanding)
        assert standing.source == "personal"
        assert standing.value == 600
        assert standing.port_access_level == svc._calculate_port_access_level(600)
        assert standing.combat_response == svc._calculate_combat_response(600)
        assert standing.trade_modifier == svc._calculate_trade_modifier(600)
