"""WO-PROG-FL-INTEGRITY -- first-login persistence integrity.

Three defects, three lanes:

1. The ADR-0026 FL1 lifetime +10% trade bonus (`player.settings["trade_bonus"]
   = 0.1`) was an IN-PLACE mutation of a plain (non-Mutable) JSONB column
   (`Player.settings = Column(JSONB, nullable=False, default={})`, no
   `MutableDict.as_mutable()` wrapper) -- invisible to SQLAlchemy's own
   change-tracking and silently dropped at commit. Fixed in
   `first_login_service.py`'s `complete_first_login` via the established
   dict-copy + `flag_modified` pattern (see
   `emergent_reputation_service._store_throttle_bucket` /
   `faction_service.apply_faction_rep_delta`'s history reassignment).

2. `player.reputation = {"faction1": -10}` was a ghost write into a dead
   JSONB store nothing else in the codebase reads -- the real
   `notoriety_penalty` persists on `FirstLoginSession.notoriety_penalty`
   (SYSTEMS/first-login.md:186) and is already surfaced in the completion
   response. The write is DELETED, not replaced.

3. `admin_comprehensive.py`'s `PUT /admin/players/{player_id}`
   `reputation_adjustments` field mutated that same dead `player.reputation`
   JSONB (a complete no-op in practice -- the column defaults to `{}`, so the
   `if faction in player.reputation:` guard was always False). Rewired to the
   canonical `Reputation` table via `FactionService.update_reputation` -- the
   same admin-set surface `admin_factions.py`'s
   `PUT /admin/factions/{faction_id}/reputation` already uses (internal
   commit + level-change WebSocket notify + HONORED medal dispatch).

Falsifiability for (1) -- proven empirically (see PROOF section below) and
re-proven in-suite as a tripwire test: `sqlalchemy.orm.attributes.
get_history(instance, key).has_changes()` is exactly the signal SQLAlchemy's
own unit-of-work flush logic consults to decide whether a column belongs in
the next UPDATE. On a real (transient, unpersisted) `Player()` instance whose
`committed_state` is reset to simulate "freshly loaded from DB":
  - in-place mutation (`player.settings["trade_bonus"] = 0.1`, the OLD bug)
    leaves `get_history(player, "settings").has_changes() is False` --
    SQLAlchemy sees NOTHING dirty, reproducing "silently dropped at commit."
  - dict-copy reassignment (`player.settings = {**old, "trade_bonus": 0.1}`,
    the FIX) leaves `has_changes() is True` regardless of whether
    `flag_modified` is additionally called -- reassignment alone already
    routes through the instrumented attribute's `__set__`. `flag_modified`
    is still applied per the established belt-and-braces pattern (guards
    against ORM edge cases a bare reassignment doesn't cover, e.g. an
    `__eq__`-equal-looking new dict).
Both facts were confirmed with a throwaway script against this exact model
before writing the assertions below; `test_inplace_mutation_is_the_bug_this_
fixture_would_have_missed` re-derives the same proof INSIDE the suite so the
falsifiability claim doesn't rest on an external, unreviewable scratch run.

DB-free: real (transient, unpersisted) `Player()` ORM instances where
`flag_modified` participation must be observed (only a real declarative-
mapped instance carries `_sa_instance_state` --
combat-resolver-deterministic-random-pattern), `types.SimpleNamespace` /
plain model instances everywhere `flag_modified` is never invoked. Admin-
route tests use the "Admin list-route direct-call pattern": the async route
handler is called directly with fake `Depends` args + a fake `Session`,
bypassing `TestClient` and a real DB entirely.
"""
import ast
import inspect as py_inspect
import types
import uuid

import pytest
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.orm.attributes import get_history

from src.services.first_login_service import FirstLoginService
from src.models.first_login import ShipChoice, FirstLoginSession, PlayerFirstLoginState
from src.models.ship import Ship, ShipType, ShipSpecification
from src.models.player import Player
from src.models.user import User
from src.models.faction import Faction, FactionType
from src.models.reputation import Reputation, ReputationLevel
from src.api.routes import admin_comprehensive as admin_mod
from src.api.routes.admin_comprehensive import update_player, PlayerUpdateRequest
from src.auth.admin_scopes import (
    PLAYERS_ADJUST_CREDITS,
    PLAYERS_ADJUST_REP,
    PLAYERS_SUSPEND,
)


# ---------------------------------------------------------------------------
# Section 1 -- trade_bonus persistence pin (Lane A)
# ---------------------------------------------------------------------------

def _session(player_id, negotiation_bonus_flag=False, notoriety_penalty=False,
             awarded_ship=ShipChoice.LIGHT_FREIGHTER):
    return types.SimpleNamespace(
        id=uuid.uuid4(),
        player_id=player_id,
        completed_at=None,
        starting_credits=1000,
        extracted_player_name=None,
        awarded_ship=awarded_ship,
        negotiation_bonus_flag=negotiation_bonus_flag,
        notoriety_penalty=notoriety_penalty,
    )


def _fresh_committed_player(player_id, reputation=None):
    """A real (transient) Player() ORM instance with its `committed_state`
    reset to simulate "freshly loaded from DB, nothing dirty yet" -- the
    baseline `get_history` needs to detect a REAL subsequent change rather
    than trivially reporting the object's own construction as a change."""
    player = Player()
    player.id = player_id
    # `username` is a read-only @property (nickname-or-user.username) -- set
    # the backing `.user` relationship instead, matching real Player usage.
    # The backref event SQLAlchemy fires on assignment needs a real
    # instrumented instance on the other side too, not a SimpleNamespace.
    player.user = User(username="tester")
    player.nickname = None
    player.credits = 0
    player.current_sector_id = 1
    player.current_ship_id = None
    player.settings = {}
    player.reputation = reputation if reputation is not None else {}
    player.first_login = {}
    player.aria_relationship_score = 0
    player.aria_total_interactions = 0
    insp = sa_inspect(player)
    insp.committed_state.clear()
    insp._commit_all(insp.dict)
    return player


def _spec():
    return types.SimpleNamespace(
        type=ShipType.LIGHT_FREIGHTER,
        shield_resistance=0.02,
        armor_rating=0.03,
    )


def _state(player_id):
    return types.SimpleNamespace(
        player_id=player_id,
        has_completed_first_login=False,
        received_resources=False,
        attempts=0,
    )


class _FakeQuery:
    """House pattern (test_first_login_starter_resistances.py /
    test_first_login_completion.py): returns whatever was registered for the
    queried model, ignoring filter/filter_by conditions."""
    def __init__(self, obj):
        self._obj = obj

    def filter(self, *a, **k):
        return self

    def filter_by(self, **k):
        return self

    def order_by(self, *a, **k):
        return self

    def first(self):
        if isinstance(self._obj, list):
            return self._obj[0] if self._obj else None
        return self._obj

    def all(self):
        if self._obj is None:
            return []
        return self._obj if isinstance(self._obj, list) else [self._obj]


class _FakeDB:
    def __init__(self, mapping):
        self._mapping = mapping
        self.added = []
        self.deleted = []
        self.committed = False

    def query(self, model):
        return _FakeQuery(self._mapping.get(model))

    def add(self, obj):
        self.added.append(obj)

    def delete(self, obj):
        self.deleted.append(obj)

    def flush(self):
        pass

    def commit(self):
        self.committed = True

    def refresh(self, obj):
        pass


def _make_service(session, player):
    state = _state(player.id)
    db = _FakeDB({
        FirstLoginSession: session,
        Player: player,
        Ship: [],
        ShipSpecification: _spec(),
        PlayerFirstLoginState: state,
    })
    svc = FirstLoginService(db=db, ai_service=object())
    return svc, db


def test_negotiation_bonus_trade_bonus_is_flush_visible_via_reassignment():
    """The load-bearing pin: after complete_first_login with
    negotiation_bonus_flag=True, `settings["trade_bonus"]` is set to the
    correct value AND SQLAlchemy's own change-tracking sees it -- the exact
    mechanism a real Session's flush() consults to decide whether `settings`
    belongs in the UPDATE. This fails against the reverted in-place-mutation
    code (see the tripwire test below for the in-suite proof)."""
    player_id = uuid.uuid4()
    session = _session(player_id, negotiation_bonus_flag=True)
    player = _fresh_committed_player(player_id)
    original_settings_ref = player.settings
    svc, db = _make_service(session, player)

    result = svc.complete_first_login(session.id, nickname_confirmed=False)

    assert player.settings["trade_bonus"] == 0.1
    assert player.settings is not original_settings_ref, (
        "the fix must reassign a NEW dict, not mutate the original reference "
        "in place"
    )
    assert original_settings_ref.get("trade_bonus") is None, (
        "the original (pre-call) settings dict must be left untouched"
    )
    history = get_history(player, "settings")
    assert history.has_changes() is True, (
        "SQLAlchemy sees no dirty `settings` attribute -- an UPDATE would "
        "silently omit trade_bonus, reproducing the original bug"
    )
    assert db.committed is True


def test_no_negotiation_bonus_leaves_settings_untouched_and_clean():
    """Regression guard: when negotiation_bonus_flag is False, `settings`
    must stay exactly as loaded -- no reassignment, no spurious dirty flag."""
    player_id = uuid.uuid4()
    session = _session(player_id, negotiation_bonus_flag=False)
    player = _fresh_committed_player(player_id)
    svc, _db = _make_service(session, player)

    svc.complete_first_login(session.id, nickname_confirmed=False)

    assert "trade_bonus" not in player.settings
    assert get_history(player, "settings").has_changes() is False


def test_inplace_mutation_is_the_bug_this_fixture_would_have_missed():
    """Falsifiability, re-derived in-suite (not just asserted in a docstring
    from an external scratch run): reproduce the OLD buggy write directly
    against the same fixture helper and show `get_history(...).has_changes()`
    stays False -- i.e. this exact detection mechanism (the one the pin test
    above relies on) WOULD have let the original in-place-mutation bug ship
    silently. This is the demonstration that the pin test is load-bearing,
    not tautological."""
    player_id = uuid.uuid4()
    player = _fresh_committed_player(player_id)

    player.settings["trade_bonus"] = 0.1  # the retired buggy write, verbatim

    assert player.settings["trade_bonus"] == 0.1, "the value IS set in memory"
    assert get_history(player, "settings").has_changes() is False, (
        "in-place mutation of a plain (non-Mutable) JSONB column is "
        "genuinely invisible to SQLAlchemy's change tracking -- this is the "
        "silent-drop-at-commit bug WO-PROG-FL-INTEGRITY fixes"
    )


# ---------------------------------------------------------------------------
# Section 2 -- notoriety_penalty writes nothing to player.reputation (Lane A)
# ---------------------------------------------------------------------------

def test_notoriety_penalty_never_writes_player_reputation():
    """The removed `player.reputation = {"faction1": -10}` ghost write must
    have no replacement -- notoriety_penalty already persists on the
    session (asserted via the response payload) and player.reputation must
    come out of complete_first_login byte-identical to how it went in."""
    player_id = uuid.uuid4()
    session = _session(player_id, notoriety_penalty=True)
    player = _fresh_committed_player(player_id, reputation={"pre_existing": "sentinel"})
    original_reputation_ref = player.reputation
    svc, _db = _make_service(session, player)

    result = svc.complete_first_login(session.id, nickname_confirmed=False)

    assert player.reputation is original_reputation_ref, "player.reputation was reassigned"
    assert player.reputation == {"pre_existing": "sentinel"}, "player.reputation was mutated"
    assert get_history(player, "reputation").has_changes() is False
    assert result["notoriety_penalty"] is True, (
        "the mechanical penalty is the session flag surfaced in the response, "
        "not a player.reputation write"
    )


def test_no_notoriety_penalty_also_leaves_player_reputation_untouched():
    player_id = uuid.uuid4()
    session = _session(player_id, notoriety_penalty=False)
    player = _fresh_committed_player(player_id, reputation={"pre_existing": "sentinel"})
    svc, _db = _make_service(session, player)

    svc.complete_first_login(session.id, nickname_confirmed=False)

    assert player.reputation == {"pre_existing": "sentinel"}


def test_zero_player_reputation_assignment_targets_remain_in_first_login_service():
    """AST-based pin, not a text grep (source-grep-test-self-defeat -- this
    very docstring/comment block mentions `player.reputation` descriptively
    and would false-positive a naive string search). Walk the module's AST
    for any assignment whose target is a `.reputation` attribute access;
    none may remain in this file."""
    import src.services.first_login_service as flm
    tree = ast.parse(open(flm.__file__).read())
    hits = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Attribute) and target.attr == "reputation":
                    hits.append(target.lineno)
    assert hits == [], f"found .reputation assignment target(s) at line(s) {hits}"


# ---------------------------------------------------------------------------
# Section 3 -- admin reputation_adjustments routed to the Reputation table
# (Lane B), route auth untouched
# ---------------------------------------------------------------------------

class _RepListQuery:
    """Reputation query stand-in backed by a mutable list SHARED with
    `_AdminFakeDB.add()` -- a row created mid-call by
    `FactionService.initialize_player_reputations`'s create path is visible
    to the very next `.first()` in the same call, simulating a real DB
    surfacing a just-added row (fake-orm-flush-defaults-gap). `.filter()`
    ignores its condition -- safe because each test scenario seeds exactly
    one player/faction pair."""
    def __init__(self, rows):
        self._rows = rows

    def filter(self, *a, **k):
        return self

    def first(self):
        return self._rows[0] if self._rows else None

    def all(self):
        return list(self._rows)


class _AdminFakeQuery:
    def __init__(self, obj):
        self._obj = obj

    def filter(self, *a, **k):
        return self

    def filter_by(self, **k):
        return self

    def first(self):
        if isinstance(self._obj, list):
            return self._obj[0] if self._obj else None
        return self._obj

    def all(self):
        if self._obj is None:
            return []
        return self._obj if isinstance(self._obj, list) else [self._obj]


class _AdminFakeDB:
    """mapping: {model_class: instance_or_list}. Reputation is special-cased
    onto a mutable list so add()-during-call is visible on re-query (see
    _RepListQuery). `Player.user_id` on the seeded stand-in is left None so
    FactionService.update_reputation's level-change WebSocket branch always
    short-circuits (`if not (recipient and recipient.user_id): return`)
    without needing to depend on the real connection_manager singleton."""
    def __init__(self, mapping, reputation_rows=None):
        self._mapping = mapping
        self._reputation_rows = reputation_rows if reputation_rows is not None else []
        self.added = []
        self.commit_count = 0

    def query(self, model):
        if model is Reputation:
            return _RepListQuery(self._reputation_rows)
        return _AdminFakeQuery(self._mapping.get(model))

    def add(self, obj):
        self.added.append(obj)
        if isinstance(obj, Reputation):
            self._reputation_rows.append(obj)

    def commit(self):
        self.commit_count += 1

    def flush(self):
        pass

    def rollback(self):
        pass


def _admin_player(player_id):
    # `.user_id=None` keeps FactionService.update_reputation's level-change
    # WebSocket branch short-circuited; `.user` backs the route's trailing
    # `player.user.username` audit-log line.
    return types.SimpleNamespace(
        id=player_id, user_id=None, user=types.SimpleNamespace(username="tester"),
    )


def _admin_faction(faction_id, name="Terran Federation", ftype=FactionType.FEDERATION):
    return types.SimpleNamespace(id=faction_id, name=name, faction_type=ftype)


def _admin_user():
    # update_player is RBAC-E5-wrapped (admin_action_attempt), which reads
    # actor.id on log -- a bare username-only fake AttributeErrors on log.
    return types.SimpleNamespace(id=uuid.uuid4(), username="test-admin")


@pytest.mark.asyncio
async def test_admin_reputation_adjustment_clamps_at_negative_800_on_existing_row():
    player_id = uuid.uuid4()
    faction_id = uuid.uuid4()
    player = _admin_player(player_id)
    faction = _admin_faction(faction_id)
    existing_rep = types.SimpleNamespace(
        player_id=player_id, faction_id=faction_id,
        current_value=-750, current_level=ReputationLevel.PUBLIC_ENEMY,
        title="Public Enemy", trade_modifier=0.0, port_access_level=0,
        combat_response="hostile", history=[],
    )
    db = _AdminFakeDB(
        {Player: player, Faction: faction},
        reputation_rows=[existing_rep],
    )
    request = PlayerUpdateRequest(reputation_adjustments={"Terran Federation": -500})

    result = await update_player(
        player_id=str(player_id), update_data=request,
        current_admin=_admin_user(), db=db,
    )

    assert result == {"message": "Player updated successfully"}
    assert existing_rep.current_value == -800, "must clamp at -800, not -1250"
    assert db.commit_count >= 1
    assert not any(isinstance(o, Reputation) for o in db.added), (
        "an existing row must be updated in place, not duplicated"
    )


@pytest.mark.asyncio
async def test_admin_reputation_adjustment_creates_new_reputation_row_when_none_exists():
    player_id = uuid.uuid4()
    faction_id = uuid.uuid4()
    player = _admin_player(player_id)
    faction = _admin_faction(faction_id)
    db = _AdminFakeDB(
        {Player: player, Faction: faction},
        reputation_rows=[],  # nothing pre-seeded -- forces the create path
    )
    request = PlayerUpdateRequest(reputation_adjustments={"Terran Federation": 50})

    result = await update_player(
        player_id=str(player_id), update_data=request,
        current_admin=_admin_user(), db=db,
    )

    assert result == {"message": "Player updated successfully"}
    created = [o for o in db.added if isinstance(o, Reputation)]
    assert len(created) == 1, "initialize_player_reputations must create exactly one row"
    row = created[0]
    assert row.player_id == player_id
    assert row.faction_id == faction_id
    assert row.current_value == 50, "0 (fresh default) + 50 adjustment"


@pytest.mark.asyncio
async def test_admin_reputation_adjustment_unknown_faction_name_is_dropped_not_fatal():
    """A typo'd/unknown faction key must degrade to a logged no-op, not a
    500 -- other keys in the same batch (or the credits/turns/is_active
    fields) must still apply."""
    player_id = uuid.uuid4()
    player = _admin_player(player_id)
    db = _AdminFakeDB({Player: player, Faction: None}, reputation_rows=[])
    request = PlayerUpdateRequest(
        credits=5000, reputation_adjustments={"Not A Real Faction": 100}
    )

    result = await update_player(
        player_id=str(player_id), update_data=request,
        current_admin=_admin_user(), db=db,
    )

    assert result == {"message": "Player updated successfully"}
    assert player.credits == 5000
    # No phantom Reputation row for the dropped unknown-faction adjustment
    # -- db.added legitimately also holds the route's own AdminActionLog
    # audit row (log_admin_action fires on every successful update_player
    # call, unrelated to this WO), so filter by type like the sibling
    # clamp/create-path tests above rather than requiring an empty list.
    assert not any(isinstance(o, Reputation) for o in db.added)


def test_update_player_route_auth_dependency_unchanged():
    """Lane B only touched the reputation_adjustments block -- the route's
    auth gate itself is untouched by THIS WO. That gate has since migrated
    (a separate RBAC-E5 rollout) from the old `get_current_admin`
    (is_admin-only) dependency to `require_all_scopes(PLAYERS_ADJUST_CREDITS,
    PLAYERS_SUSPEND, PLAYERS_ADJUST_REP)` -- pin the scope-gated shape via
    the dependency's own self-describing name (require_all_scopes stamps
    `__name__` with its exact scope list, the same convention
    test_rbac_phase_a2.py pins via `__require_scope__`)."""
    sig = py_inspect.signature(admin_mod.update_player)
    current_admin_param = sig.parameters["current_admin"]
    dependency = current_admin_param.default.dependency
    assert dependency.__name__ == (
        f"require_all_scopes[{PLAYERS_ADJUST_CREDITS},{PLAYERS_SUSPEND},{PLAYERS_ADJUST_REP}]"
    )
