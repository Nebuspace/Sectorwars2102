"""Unit tests for WO-NEON-RES-NH3B — persist capped-actual cargo loot in the
combat_log HISTORY row on both the NPC (attack_npc_ship) and PvP
(attack_player) legs, not the pre-cap request.

Technique: ``CombatService._resolve_ship_combat`` is monkeypatched to return
a scripted ATTACKER_VICTORY / defender_ship_destroyed result — the fight
resolution itself (rounds, damage rolls, RNG) is exercised by its own
dedicated tests (test_drone_combat_record.py et al.); this WO only changes
what happens to ``combat_result`` AFTER the resolver returns, so scripting
its output directly is the right isolation boundary (mirrors the
"combat-resolver-deterministic-random-pattern" used elsewhere in this
suite). A lightweight fake Session (pattern: test_drone_combat_record.py's
``_SectorDronesDb`` / test_bounty_service_nh2.py's ``_FakeQuery``) then
drives ``attack_npc_ship`` / ``attack_player`` end-to-end for real. Every
non-cargo-loot side hook they touch (turn regen, ranking, ARIA, medals,
bounty, personal reputation, grey-flag, police engagement, WS emits) is
either already defensively try/except-wrapped in combat_service.py itself,
or resolves to a harmless no-op against the fake rows below — none of them
read or write cargo_looted.

The PvP leg additionally monkeypatches ``CombatService._handle_ship_
destruction`` (escape-pod ejection / insurance / ship_service.destroy_ship)
to a recording no-op: NH3B does not touch ship-destruction mechanics (those
are covered by test_combat_escape.py), and the "transfer stays BEFORE
destruction" ordering constraint is a static code-order fact already
confirmed by direct reading of combat_service.py, not something this
dynamic test needs to re-derive.
"""
import types
import uuid
from contextlib import contextmanager

import pytest

from src.models.cargo_wreck import CargoWreck
from src.models.combat import CombatResult
from src.models.combat_log import CombatLog
from src.models.npc_character import NPCCharacter as NPCCharacterModel
from src.models.player import Player as PlayerModel
from src.models.sector import Sector as SectorModel
from src.models.ship import Ship as ShipModel
from src.models.ship import ShipStatus, ShipType
from src.services.combat_service import CombatService


@pytest.fixture(autouse=True)
def _no_kia_processing(monkeypatch):
    """WO-API-A1: attack_npc_ship's own engage-range backstop needs a REAL
    (non-None) NPCCharacter row now, so this file's single-fixture-per-model
    FakeSession can no longer rely on "NPCCharacter query returns None" to
    keep handle_npc_ship_destroyed's OWN separate internal NPCCharacter
    query (npc_spawn_service.py:1205-1213) a no-op -- that function goes on
    to lock Sector rows, write NPCDeathLog, and mutate squad membership, none
    of which this file's minimal Sector/NPCCharacter fixtures model (out of
    scope -- NH3B is cargo_looted persistence only). Monkeypatches the SAME
    lazy-import target combat_service.py's own ImportError-guarded import
    resolves at call time, mirroring this file's existing _resolve_ship_
    combat / _handle_ship_destruction isolation-boundary convention above."""
    monkeypatch.setattr(
        "src.services.npc_spawn_service.handle_npc_ship_destroyed",
        lambda *a, **k: None,
    )

# WO-API-A1: attack_player/attack_npc_ship now backstop on engage-range --
# every player/NPC fixture in this file shares this IDENTICAL literal pose,
# so attacker/defender/npc are always at the same point regardless of which
# ship id keys their fallback anchor would otherwise use.
_POSE = {
    "x_pct": 50.0, "y_pct": 50.0, "heading_deg": 0.0,
    "phase": "idle", "burning": False, "leg": None,
}


def _make_npc_char(*, ship_id):
    """NPCCharacter double for the engage-range backstop's own query
    (combat_service.attack_npc_ship) -- now REAL (not None), so this same
    query also feeds the pre-existing loot/quantum-drop-faucet code further
    down attack_npc_ship (test_npc_quantum_drops.py's own _make_npc_
    character shape, mirrored here) -- credits=0/archetype=None keeps both
    of those blocks inert (NH3B is scoped to cargo_looted, not credits/
    quantum-shard drops)."""
    return types.SimpleNamespace(
        id=uuid.uuid4(), name="Test NPC", title=None, archetype=None,
        credits=0, notoriety=80, faction_code="independent",
        display_name="Test NPC",
        ship_id=ship_id, current_sector_id=1,
        intrasystem_pose=dict(_POSE),
    )

# --- Shared fixtures ---------------------------------------------------- #

def _cargo(capacity, used, contents):
    return {"capacity": capacity, "used": used, "contents": dict(contents)}


def _make_ship(*, cargo, type_=ShipType.SCOUT_SHIP, sector_id=1, name="Test Hull"):
    ship = ShipModel()
    ship.id = uuid.uuid4()
    ship.type = type_
    ship.name = name
    ship.cargo = cargo
    ship.is_destroyed = False
    ship.is_active = True
    ship.is_npc = False
    ship.current_value = 0
    ship.hangar = None
    ship.tow_state = None
    ship.sector_id = sector_id
    ship.status = ShipStatus.IN_SPACE
    return ship


def _make_player(*, ship, personal_reputation=0, turns=999_999, max_turns=1_000):
    return types.SimpleNamespace(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        username="pilot",
        credits=0,
        turns=turns,
        max_turns=max_turns,
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
        personal_reputation=personal_reputation,
        quantum_shards=0,
        quantum_crystals=0,
        aria_total_interactions=0,
        aria_consciousness_level=1,
        aria_bonus_multiplier=1.0,
        grey_until=None,
        grey_kind=None,
        settings={},
        team_id=None,
        # SUSPECT-LIFE-1 (post-dates this file): attack_player now reads
        # these unconditionally via suspect_service.is_live_suspect for the
        # fed-zone-immunity check. Real Player rows always carry them
        # (migration-backed, default False/NULL); this fake needs the same
        # completeness or attack_player raises AttributeError.
        is_suspect=False,
        suspect_until=None,
        # WO-API-A1 (post-dates this file too): attack_player/attack_npc_ship
        # now backstop on engage-range -- see module-level _POSE.
        intrasystem_pose=dict(_POSE),
    )


def _sector():
    return types.SimpleNamespace(id=uuid.uuid4(), sector_id=1, cluster=None, last_combat=None)


def _victory_result(*, cargo_stolen=None, rounds=1):
    """Scripted ``_resolve_ship_combat`` return — the fight is already
    decided; NH3B only cares what happens to cargo_stolen afterward."""
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
        "cargo_stolen": dict(cargo_stolen or {}),
        "combat_details": [],
    }


class _PlayerQueryStub:
    """Routes a ``Player.id == <literal>`` filter to the matching seeded row
    (pattern: test_bounty_service_nh2.py's ``_FakeQuery``)."""

    def __init__(self, players_by_id):
        self._players = players_by_id
        self._pending_id = None

    def filter(self, cond):
        rhs = getattr(cond, "right", None)
        self._pending_id = getattr(rhs, "value", None)
        return self

    def populate_existing(self, *a, **k):
        # WO-MONEY-REREAD-SERVICES: no-op passthrough, matches real
        # SQLAlchemy Query's chainable-and-returns-self shape.
        return self

    def with_for_update(self, *a, **k):
        return self

    def first(self):
        return self._players.get(self._pending_id)


class _StubQuery:
    """One canned .first()/.all() answer regardless of the filter/order_by/
    with_for_update/populate_existing chain shape actually used — covers
    every real call shape combat_service.py issues against Ship / Sector /
    ShipSpecification / NPCCharacter in the paths exercised here."""

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

    def scalar(self):
        # WO-COMBAT-FRIENDLY-FIRE: attack_player's pre-lock guard does
        # db.query(Player.team_id).filter(...).scalar() -- a column-only
        # scalar read that falls through _FakeCombatDb.query()'s
        # model-is-PlayerModel branch (it's Player.team_id, not Player) to
        # this catch-all. Every player fixture in this file is genuinely
        # teamless (team_id=None, see _make_player above), so None here is
        # faithful -- not a guess. Every other real call shape in this file
        # (Ship/Sector/ShipSpecification/NPCCharacter) never calls
        # .scalar(), so this addition is inert for them.
        return None


class _FakeCombatDb:
    """Minimal synchronous Session double: routes .query(Model) by class,
    records every .add()ed row, and no-ops flush/begin_nested/commit."""

    def __init__(self, *, players, ship_first=None, sector=None, npc_char=None):
        self._players = {p.id: p for p in players}
        self._ship_first = ship_first
        self._sector = sector
        self._npc_char = npc_char
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
            # WO-API-A1: attack_npc_ship's own engage-range backstop AND the
            # pre-existing loot-section query both hit this same model --
            # one shared fixture, self._npc_char has credits=0 so the
            # (unrelated to NH3B) credit-loot block stays inert regardless.
            return _StubQuery(first=self._npc_char, all_=[])
        return _StubQuery(first=None, all_=[])

    def add(self, obj):
        self.added.append(obj)

    def flush(self):
        pass

    def commit(self):
        self.commits += 1

    @contextmanager
    def begin_nested(self):
        yield


def _combat_logs(db):
    return [o for o in db.added if isinstance(o, CombatLog)]


def _wrecks(db):
    return [o for o in db.added if isinstance(o, CargoWreck)]


# ------------------------------------------------------------------------- #
# (1) NPC leg: capped-actual persisted, matches the (already-capped, NH3)
#     response value exactly
# ------------------------------------------------------------------------- #

def test_npc_leg_persists_capped_actual_and_matches_response(monkeypatch):
    """Attacker's free hold (50) is strictly less than the NPC's full cargo
    request (fuel:200 + ore:100 = 300); the history row must carry the
    CAPPED delta {'fuel': 50}, not the pre-cap request, and must equal the
    response's cargo_looted exactly (the NH3 regression guard)."""
    attacker_ship = _make_ship(cargo=_cargo(250, 200, {"ore": 200}))  # 50 free
    npc_ship = _make_ship(cargo=_cargo(1000, 300, {"fuel": 200, "ore": 100}))
    sector = _sector()
    npc_ship.sector = sector  # bypass the lazy relationship load on a transient row
    attacker = _make_player(ship=attacker_ship)

    npc_char = _make_npc_char(ship_id=npc_ship.id)
    db = _FakeCombatDb(players=[attacker], ship_first=npc_ship, sector=sector, npc_char=npc_char)
    cs = CombatService(db)
    monkeypatch.setattr(cs, "_resolve_ship_combat", lambda *a, **k: _victory_result())

    result = cs.attack_npc_ship(attacker_id=attacker.id, ship_id=npc_ship.id)

    assert result["success"] is True
    assert result["cargo_looted"] == {"fuel": 50}  # capped: only 50 free slots

    logs = _combat_logs(db)
    assert len(logs) == 1
    assert logs[0].cargo_looted == {"fuel": 50}
    assert logs[0].cargo_looted == result["cargo_looted"]
    assert db.commits == 1


# ------------------------------------------------------------------------- #
# (2) PvP leg: capped-actual persisted, NOT the resolver's pre-cap request
# ------------------------------------------------------------------------- #

def test_pvp_leg_persists_capped_actual_not_pre_cap_request(monkeypatch):
    """Same capping scenario through attack_player: the resolver hands back
    a pre-cap cargo_stolen request the attacker's hold can't fully accept;
    the persisted CombatLog.cargo_looted must be the capped delta, and the
    transfer must still run before ship-destruction is invoked."""
    attacker_ship = _make_ship(cargo=_cargo(250, 200, {"ore": 200}))  # 50 free
    defender_ship = _make_ship(cargo=_cargo(1000, 300, {"fuel": 200, "ore": 100}))
    sector = _sector()
    attacker = _make_player(ship=attacker_ship)
    defender = _make_player(ship=defender_ship)

    db = _FakeCombatDb(players=[attacker, defender], sector=sector)
    cs = CombatService(db)
    monkeypatch.setattr(
        cs, "_resolve_ship_combat",
        lambda *a, **k: _victory_result(cargo_stolen={"fuel": 200, "ore": 100}),
    )
    # NH3B is scoped to cargo_looted persistence, not ship-destruction/escape-
    # pod mechanics (covered by test_combat_escape.py) — no-op + spy it here,
    # and prove it fires strictly AFTER the transfer has already landed.
    destruction_calls = []

    def _fake_destroy(player, destroyer, cause):
        destruction_calls.append((player, destroyer, cause))

    monkeypatch.setattr(cs, "_handle_ship_destruction", _fake_destroy)

    result = cs.attack_player(attacker_id=attacker.id, defender_id=defender.id)

    assert result["success"] is True
    logs = _combat_logs(db)
    assert len(logs) == 1
    # Pre-cap request was {'fuel': 200, 'ore': 100} (300 total) — the free
    # hold is only 50, so the persisted row must NOT equal the raw request.
    assert logs[0].cargo_looted != {"fuel": 200, "ore": 100}
    assert logs[0].cargo_looted == {"fuel": 50}
    assert db.commits == 1
    # Destruction still fired exactly once, on the defender (the destroyed
    # party), with the attacker as destroyer.
    assert len(destruction_calls) == 1
    assert destruction_calls[0] == (defender, attacker, "combat")
    # By the time destruction ran, the transfer had already moved cargo off
    # the defender's hold onto the attacker's — proof the capped-transfer
    # snapshot idiom executed before the destruction call, not after.
    assert attacker_ship.cargo["contents"] == {"ore": 200, "fuel": 50}


# ------------------------------------------------------------------------- #
# (3) Zero-transfer: a completely full hold persists NULL, not {}
# ------------------------------------------------------------------------- #

def test_npc_leg_zero_transfer_persists_none_not_empty_dict(monkeypatch):
    """Attacker's hold has zero free capacity — nothing can move even though
    the NPC carries lootable cargo. cargo_looted must be NULL/None (the
    existing `or None` column contract), never an empty dict."""
    attacker_ship = _make_ship(cargo=_cargo(200, 200, {"ore": 200}))  # 0 free
    npc_ship = _make_ship(cargo=_cargo(1000, 100, {"fuel": 100}))
    sector = _sector()
    npc_ship.sector = sector
    attacker = _make_player(ship=attacker_ship)

    npc_char = _make_npc_char(ship_id=npc_ship.id)
    db = _FakeCombatDb(players=[attacker], ship_first=npc_ship, sector=sector, npc_char=npc_char)
    cs = CombatService(db)
    monkeypatch.setattr(cs, "_resolve_ship_combat", lambda *a, **k: _victory_result())

    result = cs.attack_npc_ship(attacker_id=attacker.id, ship_id=npc_ship.id)

    assert result["success"] is True
    assert result["cargo_looted"] == {}
    logs = _combat_logs(db)
    assert len(logs) == 1
    assert logs[0].cargo_looted is None  # NOT {}


# ------------------------------------------------------------------------- #
# (4) No-reordering guard: wreck-spawn still fires and the commit still lands
# ------------------------------------------------------------------------- #

def test_npc_leg_wreck_still_spawns_and_transaction_still_commits(monkeypatch):
    """Guards the NO-REORDERING constraint: the new combat_log.cargo_looted
    write-back must not have displaced _spawn_cargo_wreck or the single
    commit. Leftover (uncapped) NPC cargo after the capped transfer is real
    lost cargo, so a CargoWreck must still be added, and exactly one commit
    must still land."""
    attacker_ship = _make_ship(cargo=_cargo(250, 200, {"ore": 200}))  # 50 free
    npc_ship = _make_ship(cargo=_cargo(1000, 300, {"fuel": 200, "ore": 100}))
    sector = _sector()
    npc_ship.sector = sector
    attacker = _make_player(ship=attacker_ship)

    npc_char = _make_npc_char(ship_id=npc_ship.id)
    db = _FakeCombatDb(players=[attacker], ship_first=npc_ship, sector=sector, npc_char=npc_char)
    cs = CombatService(db)
    monkeypatch.setattr(cs, "_resolve_ship_combat", lambda *a, **k: _victory_result())

    result = cs.attack_npc_ship(attacker_id=attacker.id, ship_id=npc_ship.id)

    assert result["success"] is True
    # Only 50 of the 300 requested moved — fuel:150 + ore:100 remain on the
    # dead hull, so _spawn_cargo_wreck must still fire.
    wrecks = _wrecks(db)
    assert len(wrecks) == 1
    assert wrecks[0].cargo == {"fuel": 150, "ore": 100}
    assert db.commits == 1
    # combat_log's capped write-back and the wreck both made it into the
    # SAME single-commit transaction, in the pre-existing add order.
    assert isinstance(db.added[0], CombatLog)
    assert db.added[0].cargo_looted == {"fuel": 50}
