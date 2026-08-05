"""WO-SB-CR2 Lane A — starter-ship combat-resistance parity.

The starter-ship constructor (FirstLoginService.complete_first_login,
first_login_service.py:1642) is one of four Ship() creation paths in the
codebase. The other three (ship_service.py:105-106, npc_spawn_service.py:
424-425, admin_ships.py:467-468) already copy shield_resistance/armor_rating
off the resolved ShipSpecification onto the new Ship row; first_login passed
no resistance kwargs at all, so every starter ship kept the column default
0.0/0.0 regardless of its seeded spec (e.g. LIGHT_FREIGHTER's 0.02/0.03,
ship_specifications_seeder.py:48). This asserts the fix: spec present -> the
values are copied; spec absent -> defensive 0.0/0.0, no exception.

DB-free: SimpleNamespace session/player/spec/state + a tiny fake query layer,
in the house style of test_ship_module_bake.py.
"""
import types
import uuid

import pytest

from src.services.first_login_service import FirstLoginService
from src.models.first_login import ShipChoice
from src.models.ship import Ship, ShipType, ShipSpecification


def _session(player_id, awarded_ship=ShipChoice.LIGHT_FREIGHTER):
    return types.SimpleNamespace(
        id=uuid.uuid4(),
        player_id=player_id,
        completed_at=None,
        starting_credits=1000,
        extracted_player_name=None,
        awarded_ship=awarded_ship,
        negotiation_bonus_flag=False,
        notoriety_penalty=False,
    )


def _player(player_id):
    return types.SimpleNamespace(
        id=player_id,
        username="tester",
        nickname=None,
        credits=0,
        current_sector_id=1,
        current_ship_id=None,
        settings={},
        reputation={},
        first_login={},
        aria_relationship_score=0,
        aria_total_interactions=0,
    )


def _state(player_id):
    """An already-existing PlayerFirstLoginState — avoids exercising
    get_player_first_login_state's create branch (db.add/commit/refresh),
    which is orthogonal to the resistance-copy behavior under test."""
    return types.SimpleNamespace(
        player_id=player_id,
        has_completed_first_login=False,
        received_resources=False,
        attempts=0,
    )


def _spec(shield_resistance=0.02, armor_rating=0.03):
    """LIGHT_FREIGHTER-shaped spec (seeded values: shield_resistance=0.02,
    armor_rating=0.03 per ship_specifications_seeder.py:48)."""
    return types.SimpleNamespace(
        type=ShipType.LIGHT_FREIGHTER,
        shield_resistance=shield_resistance,
        armor_rating=armor_rating,
    )


class _FakeQuery:
    """Minimal SQLAlchemy Query stand-in: returns the object(s) registered
    for the model being queried, ignoring filter/filter_by arguments — the
    house pattern from test_ship_module_bake.py."""
    def __init__(self, obj):
        self._obj = obj

    def filter(self, *a, **k):
        return self

    def filter_by(self, **k):
        return self

    def first(self):
        return self._obj

    def all(self):
        if self._obj is None:
            return []
        return self._obj if isinstance(self._obj, list) else [self._obj]


class _FakeDB:
    """mapping: {model_class: instance_or_list_to_return}"""
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


def _make_service(spec):
    player_id = uuid.uuid4()
    session = _session(player_id)
    player = _player(player_id)
    state = _state(player_id)

    from src.models.player import Player
    from src.models.first_login import FirstLoginSession, PlayerFirstLoginState

    db = _FakeDB({
        FirstLoginSession: session,
        Player: player,
        Ship: [],  # no stale ships to self-heal away
        ShipSpecification: spec,
        PlayerFirstLoginState: state,
    })
    # ai_service truthy sentinel — skips constructing the real
    # AIDialogueService (avoids provider setup unrelated to this test).
    svc = FirstLoginService(db=db, ai_service=object())
    return svc, db, session


def test_starter_ship_copies_spec_resistances_when_spec_present():
    spec = _spec(shield_resistance=0.02, armor_rating=0.03)
    svc, db, session = _make_service(spec)

    svc.complete_first_login(session.id)

    new_ships = [o for o in db.added if isinstance(o, Ship)]
    assert len(new_ships) == 1, db.added
    new_ship = new_ships[0]

    assert new_ship.shield_resistance == 0.02
    assert new_ship.armor_rating == 0.03


def test_starter_ship_defaults_to_zero_when_spec_absent():
    svc, db, session = _make_service(None)

    # Must not raise even with no ShipSpecification row for the resolved type.
    svc.complete_first_login(session.id)

    new_ships = [o for o in db.added if isinstance(o, Ship)]
    assert len(new_ships) == 1, db.added
    new_ship = new_ships[0]

    assert new_ship.shield_resistance == 0.0
    assert new_ship.armor_rating == 0.0
