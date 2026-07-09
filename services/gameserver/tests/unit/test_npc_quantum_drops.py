"""Unit tests for WO-CMB-QDROP-NPC-1 -- the canon NPC quantum-shard drop
table on ``attack_npc_ship`` victories (FEATURES/galaxy/quantum-resources.md
S3 "Combat salvage"): Quantum-Smuggler kill -> 5% chance of 1-2 shards;
Rogue-Scientist kill -> 15% chance of 1-3 shards. Only the PvP 100% wallet
transfer (``_transfer_quantum_wallet``) existed before this WO; the NPC leg
carried an explicit deferral comment.

MAPPING (NO-CANON, flagged in combat_service.py): canon names two NPC kinds
that do not exist as spawn kinds. A Smuggler/Black-Marketeer-TITLED TRADER
kill maps to the 5%/1-2 row; a RESEARCHER-archetype kill (declared but never
spawned by npc_spawn_service.py today) maps to the 15%/1-3 row; every other
NPC never drops.

Sections:
  (1) TestRollNpcQuantumDrop -- the shared roll helper
      ``_roll_npc_quantum_drop`` in complete isolation. DB-free:
      SimpleNamespace(archetype, title) stand-ins, scripted
      random.random()/random.randint() (mirrors the
      combat-resolver-deterministic-random-pattern used elsewhere in this
      suite). Covers both mapped rows, the chance-threshold boundary on
      both sides, magnitude-range boundaries (measured over many draws,
      not a single sample), and every non-mapped archetype/title
      combination.
  (2) TestAttackNpcShipQuantumDrop -- end-to-end through
      ``CombatService.attack_npc_ship`` via a scripted ``_resolve_ship_
      combat`` result (pattern: test_combat_loot_history_nh3b.py's
      ``_FakeCombatDb`` / ``_resolve_ship_combat`` monkeypatch idiom),
      extended to route NPCCharacter queries to a seeded row.
      ``handle_npc_ship_destroyed`` (KIA/squad/death-log processing) is
      monkeypatched to a no-op stub: the quantum-drop code reads the
      EARLIER ``looted_npc`` query already present in ``attack_npc_ship``,
      not that helper's own internal query, so stubbing it out is the
      correct isolation boundary and avoids standing up unrelated KIA/squad
      machinery. Covers: credit lands on Player.quantum_shards (additive,
      not overwritten), the result payload carries ``quantum_shards_
      dropped`` on a hit and omits the key on a miss / non-mapped NPC, and
      a non-ATTACKER_VICTORY result never rolls (losses/draws never drop).
  (3) TestPvpQuantumWalletUntouched -- a direct-call regression pin that
      ``_transfer_quantum_wallet`` (the PvP 100% flat transfer) is
      byte-unchanged by this WO. No dedicated PvP quantum-combat test
      existed before this WO (grepped); this is DB-free since the method
      never touches ``self.db``.
"""
import types
import uuid

import pytest

import src.services.combat_service as combat_service_module
import src.services.npc_spawn_service as npc_spawn_service_module
from src.models.combat import CombatResult
from src.models.npc_character import NPCArchetype
from src.models.npc_character import NPCCharacter as NPCCharacterModel
from src.models.player import Player as PlayerModel
from src.models.sector import Sector as SectorModel
from src.models.ship import Ship as ShipModel
from src.models.ship import ShipStatus, ShipType
from src.services.combat_service import (
    NPC_QUANTUM_DROP_RESEARCHER_CHANCE,
    NPC_QUANTUM_DROP_RESEARCHER_MAX,
    NPC_QUANTUM_DROP_RESEARCHER_MIN,
    NPC_QUANTUM_DROP_SMUGGLER_CHANCE,
    NPC_QUANTUM_DROP_SMUGGLER_MAX,
    NPC_QUANTUM_DROP_SMUGGLER_MIN,
    CombatService,
    _roll_npc_quantum_drop,
)

# =========================================================================
# (1) The shared roll helper, in isolation
# =========================================================================


def _npc(*, archetype, title=None):
    return types.SimpleNamespace(archetype=archetype, title=title)


class TestRollNpcQuantumDrop:
    def test_none_looted_npc_never_drops(self):
        assert _roll_npc_quantum_drop(None) == 0

    def test_smuggler_titled_trader_hits_within_smuggler_range(self, monkeypatch):
        monkeypatch.setattr(combat_service_module.random, "random", lambda: 0.0)
        monkeypatch.setattr(combat_service_module.random, "randint", lambda lo, hi: hi)
        npc = _npc(archetype=NPCArchetype.TRADER, title="Smuggler")
        assert _roll_npc_quantum_drop(npc) == NPC_QUANTUM_DROP_SMUGGLER_MAX

    def test_black_marketeer_titled_trader_also_maps_to_smuggler_row(self, monkeypatch):
        monkeypatch.setattr(combat_service_module.random, "random", lambda: 0.0)
        monkeypatch.setattr(combat_service_module.random, "randint", lambda lo, hi: lo)
        npc = _npc(archetype=NPCArchetype.TRADER, title="Black Marketeer")
        assert _roll_npc_quantum_drop(npc) == NPC_QUANTUM_DROP_SMUGGLER_MIN

    def test_researcher_archetype_hits_within_researcher_range(self, monkeypatch):
        monkeypatch.setattr(combat_service_module.random, "random", lambda: 0.0)
        monkeypatch.setattr(combat_service_module.random, "randint", lambda lo, hi: hi)
        npc = _npc(archetype=NPCArchetype.RESEARCHER, title=None)
        assert _roll_npc_quantum_drop(npc) == NPC_QUANTUM_DROP_RESEARCHER_MAX

    def test_smuggler_chance_gate_boundary(self, monkeypatch):
        """random() strictly < chance is a hit; == chance is a miss (the
        module-wide `random.random() < x` idiom, e.g. combat_service.py's
        crit-roll gates)."""
        npc = _npc(archetype=NPCArchetype.TRADER, title="Smuggler")
        monkeypatch.setattr(
            combat_service_module.random, "random",
            lambda: NPC_QUANTUM_DROP_SMUGGLER_CHANCE,
        )
        assert _roll_npc_quantum_drop(npc) == 0  # == chance -> miss

        monkeypatch.setattr(
            combat_service_module.random, "random",
            lambda: NPC_QUANTUM_DROP_SMUGGLER_CHANCE - 0.0001,
        )
        monkeypatch.setattr(combat_service_module.random, "randint", lambda lo, hi: lo)
        assert _roll_npc_quantum_drop(npc) == NPC_QUANTUM_DROP_SMUGGLER_MIN  # just below -> hit

    def test_researcher_chance_gate_boundary(self, monkeypatch):
        npc = _npc(archetype=NPCArchetype.RESEARCHER)
        monkeypatch.setattr(
            combat_service_module.random, "random",
            lambda: NPC_QUANTUM_DROP_RESEARCHER_CHANCE,
        )
        assert _roll_npc_quantum_drop(npc) == 0  # == chance -> miss

        monkeypatch.setattr(
            combat_service_module.random, "random",
            lambda: NPC_QUANTUM_DROP_RESEARCHER_CHANCE - 0.0001,
        )
        monkeypatch.setattr(combat_service_module.random, "randint", lambda lo, hi: hi)
        assert _roll_npc_quantum_drop(npc) == NPC_QUANTUM_DROP_RESEARCHER_MAX  # just below -> hit

    def test_smuggler_magnitude_never_zero_never_above_max(self, monkeypatch):
        """Force the gate to always hit; leave random.randint() REAL so the
        actual distribution is exercised (not a single scripted sample --
        measure-distribution-before-fixing-edge-case). Both boundaries must
        actually appear across enough draws to prove the range is
        inclusive on both ends, not just bounded."""
        monkeypatch.setattr(combat_service_module.random, "random", lambda: 0.0)
        npc = _npc(archetype=NPCArchetype.TRADER, title="Smuggler")
        draws = [_roll_npc_quantum_drop(npc) for _ in range(300)]
        assert all(
            NPC_QUANTUM_DROP_SMUGGLER_MIN <= d <= NPC_QUANTUM_DROP_SMUGGLER_MAX
            for d in draws
        )
        assert min(draws) == NPC_QUANTUM_DROP_SMUGGLER_MIN
        assert max(draws) == NPC_QUANTUM_DROP_SMUGGLER_MAX

    def test_researcher_magnitude_never_zero_never_above_max(self, monkeypatch):
        monkeypatch.setattr(combat_service_module.random, "random", lambda: 0.0)
        npc = _npc(archetype=NPCArchetype.RESEARCHER)
        draws = [_roll_npc_quantum_drop(npc) for _ in range(300)]
        assert all(
            NPC_QUANTUM_DROP_RESEARCHER_MIN <= d <= NPC_QUANTUM_DROP_RESEARCHER_MAX
            for d in draws
        )
        assert min(draws) == NPC_QUANTUM_DROP_RESEARCHER_MIN
        assert max(draws) == NPC_QUANTUM_DROP_RESEARCHER_MAX

    @pytest.mark.parametrize(
        "archetype,title",
        [
            (NPCArchetype.HOSTILE_RAIDER, "Pirate Captain"),
            (NPCArchetype.LAW_ENFORCEMENT, "Marshal"),
            (NPCArchetype.CIVILIAN, None),
            (NPCArchetype.MISSION_GIVER, None),
            (NPCArchetype.STATION_OFFICIAL, None),
            (NPCArchetype.STATION_SECURITY, None),
            (NPCArchetype.FACTION_PATROL, None),
            (NPCArchetype.FACTION_LEADER, None),
            # TRADER, but NOT a Smuggler/Black-Marketeer title
            (NPCArchetype.TRADER, "Trader"),
            (NPCArchetype.TRADER, "Merchant Prince"),
            (NPCArchetype.TRADER, "Cargo Runner"),
            (NPCArchetype.TRADER, "Trade Baron"),
            (NPCArchetype.TRADER, None),
        ],
    )
    def test_non_mapped_npc_never_drops_even_on_a_forced_hit(
        self, monkeypatch, archetype, title
    ):
        # Force the gate to always hit (0.0 < any chance) -- a non-mapped
        # NPC must never even reach the roll, let alone drop.
        monkeypatch.setattr(combat_service_module.random, "random", lambda: 0.0)
        npc = _npc(archetype=archetype, title=title)
        assert _roll_npc_quantum_drop(npc) == 0


# =========================================================================
# (2) End-to-end through CombatService.attack_npc_ship
# =========================================================================


def _cargo(capacity=100, used=0, contents=None):
    return {"capacity": capacity, "used": used, "contents": dict(contents or {})}


def _make_ship(*, cargo=None, type_=ShipType.SCOUT_SHIP, sector_id=1, name="Test Hull"):
    ship = ShipModel()
    ship.id = uuid.uuid4()
    ship.type = type_
    ship.name = name
    ship.cargo = cargo or _cargo()
    ship.is_destroyed = False
    ship.is_active = True
    ship.is_npc = True
    ship.current_value = 0
    ship.hangar = None
    ship.tow_state = None
    ship.sector_id = sector_id
    ship.status = ShipStatus.IN_SPACE
    return ship


def _make_player(*, ship, quantum_shards=0):
    return types.SimpleNamespace(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        username="pilot",
        credits=0,
        turns=999_999,
        max_turns=1_000,
        last_turn_regeneration=None,
        lifetime_turns_spent=0,
        current_ship=ship,
        current_ship_id=ship.id,
        current_sector_id=1,
        is_docked=False,
        is_landed=False,
        current_port_id=None,
        attack_drones=0,
        defense_drones=0,
        military_rank="__no_such_rank__",  # forces try/except-guarded fallbacks
        personal_reputation=0,
        quantum_shards=quantum_shards,
        quantum_crystals=0,
        aria_total_interactions=0,
        aria_consciousness_level=1,
        aria_bonus_multiplier=1.0,
        grey_until=None,
        grey_kind=None,
        settings={},
        team_id=None,
    )


def _sector():
    return types.SimpleNamespace(id=uuid.uuid4(), sector_id=1, cluster=None, last_combat=None)


def _make_npc_character(*, archetype, title=None, notoriety=80, name="Test NPC"):
    return types.SimpleNamespace(
        id=uuid.uuid4(),
        name=name,
        title=title,
        archetype=archetype,
        credits=0,
        notoriety=notoriety,
        faction_code="independent",
        display_name=f"{title + ' ' if title else ''}{name}".strip(),
    )


def _victory_result(*, rounds=1):
    return {
        "result": CombatResult.ATTACKER_VICTORY,
        "message": "attacker wins",
        "rounds": rounds,
        "attacker_drones_lost": 0,
        "defender_drones_lost": 0,
        "attacker_damage_dealt": 10,
        "defender_damage_dealt": 0,
        "attacker_ship_destroyed": False,
        "defender_ship_destroyed": True,
        "cargo_stolen": {},
        "combat_details": [],
    }


def _draw_result(*, rounds=1):
    """No one destroyed -- the ATTACKER_VICTORY loot block never runs at
    all, so the quantum-drop roll can never fire (losses/draws never
    drop)."""
    return {
        "result": CombatResult.DRAW,
        "message": "draw",
        "rounds": rounds,
        "attacker_drones_lost": 0,
        "defender_drones_lost": 0,
        "attacker_damage_dealt": 0,
        "defender_damage_dealt": 0,
        "attacker_ship_destroyed": False,
        "defender_ship_destroyed": False,
        "cargo_stolen": {},
        "combat_details": [],
    }


class _PlayerQueryStub:
    def __init__(self, players_by_id):
        self._players = players_by_id
        self._pending_id = None

    def filter(self, cond):
        rhs = getattr(cond, "right", None)
        self._pending_id = getattr(rhs, "value", None)
        return self

    def with_for_update(self, *a, **k):
        return self

    def first(self):
        return self._players.get(self._pending_id)


class _StubQuery:
    def __init__(self, first=None, all_=None):
        self._first = first
        self._all = all_ if all_ is not None else []

    def filter(self, *a, **k):
        return self

    def with_for_update(self, *a, **k):
        return self

    def order_by(self, *a, **k):
        return self

    def populate_existing(self, *a, **k):
        return self

    def first(self):
        return self._first

    def all(self):
        return self._all


class _FakeCombatDb:
    """Minimal synchronous Session double (pattern:
    test_combat_loot_history_nh3b.py's ``_FakeCombatDb``), extended to
    route ``NPCCharacter`` queries to a seeded row so the quantum-drop
    mapping can read a real archetype/title."""

    def __init__(self, *, players, ship_first=None, sector=None, npc_first=None):
        self._players = {p.id: p for p in players}
        self._ship_first = ship_first
        self._sector = sector
        self._npc_first = npc_first
        self.added = []
        self.commits = 0

    def query(self, model):
        if model is PlayerModel:
            return _PlayerQueryStub(self._players)
        if model is ShipModel:
            return _StubQuery(first=self._ship_first, all_=[])
        if model is SectorModel:
            return _StubQuery(first=self._sector, all_=[])
        if model is NPCCharacterModel:
            return _StubQuery(first=self._npc_first, all_=[])
        return _StubQuery(first=None, all_=[])

    def add(self, obj):
        self.added.append(obj)

    def flush(self):
        pass

    def commit(self):
        self.commits += 1

    def begin_nested(self):
        from contextlib import contextmanager

        @contextmanager
        def _cm():
            yield

        return _cm()


def _run_attack(
    monkeypatch, *, npc_archetype, npc_title, quantum_starting=0, victory=True
):
    npc_ship = _make_ship(cargo=_cargo())
    sector = _sector()
    npc_ship.sector = sector
    attacker = _make_player(ship=_make_ship(cargo=_cargo(200, 0, {})), quantum_shards=quantum_starting)
    npc_row = _make_npc_character(archetype=npc_archetype, title=npc_title)

    db = _FakeCombatDb(players=[attacker], ship_first=npc_ship, sector=sector, npc_first=npc_row)
    cs = CombatService(db)
    result_fn = _victory_result if victory else _draw_result
    monkeypatch.setattr(cs, "_resolve_ship_combat", lambda *a, **k: result_fn())
    # KIA/squad/death-log processing is unrelated to the quantum-drop code
    # (which reads the EARLIER looted_npc query) -- stub it to a harmless
    # no-op so this test doesn't have to stand up that machinery.
    monkeypatch.setattr(npc_spawn_service_module, "handle_npc_ship_destroyed", lambda *a, **k: None)

    result = cs.attack_npc_ship(attacker_id=attacker.id, ship_id=npc_ship.id)
    return result, attacker, db


class TestAttackNpcShipQuantumDrop:
    def test_smuggler_kill_credits_shards_and_payload_carries_it(self, monkeypatch):
        monkeypatch.setattr(combat_service_module.random, "random", lambda: 0.0)
        monkeypatch.setattr(combat_service_module.random, "randint", lambda lo, hi: 2)

        result, attacker, db = _run_attack(
            monkeypatch, npc_archetype=NPCArchetype.TRADER, npc_title="Smuggler"
        )

        assert result["success"] is True
        assert attacker.quantum_shards == 2
        assert result["quantum_shards_dropped"] == 2
        assert db.commits == 1

    def test_researcher_kill_credits_shards_and_payload_carries_it(self, monkeypatch):
        monkeypatch.setattr(combat_service_module.random, "random", lambda: 0.0)
        monkeypatch.setattr(combat_service_module.random, "randint", lambda lo, hi: 3)

        result, attacker, db = _run_attack(
            monkeypatch, npc_archetype=NPCArchetype.RESEARCHER, npc_title=None
        )

        assert result["success"] is True
        assert attacker.quantum_shards == 3
        assert result["quantum_shards_dropped"] == 3

    def test_shards_accumulate_on_existing_balance(self, monkeypatch):
        monkeypatch.setattr(combat_service_module.random, "random", lambda: 0.0)
        monkeypatch.setattr(combat_service_module.random, "randint", lambda lo, hi: 1)

        result, attacker, db = _run_attack(
            monkeypatch,
            npc_archetype=NPCArchetype.TRADER,
            npc_title="Black Marketeer",
            quantum_starting=5,
        )

        assert attacker.quantum_shards == 6  # additive, not overwritten
        assert result["quantum_shards_dropped"] == 1

    def test_miss_never_credits_and_payload_omits_key(self, monkeypatch):
        # Force a guaranteed miss (>= chance).
        monkeypatch.setattr(combat_service_module.random, "random", lambda: 0.999)

        result, attacker, db = _run_attack(
            monkeypatch, npc_archetype=NPCArchetype.TRADER, npc_title="Smuggler"
        )

        assert attacker.quantum_shards == 0
        assert "quantum_shards_dropped" not in result

    def test_non_mapped_npc_never_credits_even_on_a_forced_hit(self, monkeypatch):
        # Force the gate to always hit -- a non-mapped NPC must still never
        # drop (the mapping excludes it before the roll is even attempted).
        monkeypatch.setattr(combat_service_module.random, "random", lambda: 0.0)
        monkeypatch.setattr(combat_service_module.random, "randint", lambda lo, hi: hi)

        result, attacker, db = _run_attack(
            monkeypatch, npc_archetype=NPCArchetype.HOSTILE_RAIDER, npc_title="Pirate Captain"
        )

        assert attacker.quantum_shards == 0
        assert "quantum_shards_dropped" not in result

    def test_non_ship_destroying_result_never_rolls(self, monkeypatch):
        """A DRAW result never enters the ATTACKER_VICTORY loot block at
        all -- losses/draws never drop, even with a forced-hit RNG."""
        monkeypatch.setattr(combat_service_module.random, "random", lambda: 0.0)
        monkeypatch.setattr(combat_service_module.random, "randint", lambda lo, hi: hi)

        result, attacker, db = _run_attack(
            monkeypatch,
            npc_archetype=NPCArchetype.TRADER,
            npc_title="Smuggler",
            victory=False,
        )

        assert attacker.quantum_shards == 0
        assert "quantum_shards_dropped" not in result


# =========================================================================
# (3) PvP wallet-transfer regression pin -- byte-untouched by this WO
# =========================================================================


class TestPvpQuantumWalletUntouched:
    def test_pvp_transfer_stays_a_flat_100pct_no_drop_fraction(self):
        """DB-free: _transfer_quantum_wallet never touches self.db. Proves
        the PvP leg still moves the victim's FULL wallet -- no 5%/15%
        drop-fraction gate was introduced by this WO."""
        cs = CombatService(db=None)
        victor = types.SimpleNamespace(id=uuid.uuid4(), quantum_shards=0, quantum_crystals=0)
        victim = types.SimpleNamespace(id=uuid.uuid4(), quantum_shards=7, quantum_crystals=3)

        cs._transfer_quantum_wallet(victor=victor, victim=victim)

        assert victor.quantum_shards == 7
        assert victor.quantum_crystals == 3
        assert victim.quantum_shards == 0
        assert victim.quantum_crystals == 0

    def test_pvp_transfer_is_a_noop_on_a_zero_wallet(self):
        cs = CombatService(db=None)
        victor = types.SimpleNamespace(id=uuid.uuid4(), quantum_shards=0, quantum_crystals=0)
        victim = types.SimpleNamespace(id=uuid.uuid4(), quantum_shards=0, quantum_crystals=0)

        cs._transfer_quantum_wallet(victor=victor, victim=victim)

        assert victor.quantum_shards == 0
        assert victor.quantum_crystals == 0
