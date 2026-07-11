"""Adversarial QA pass over the caps-extraction refactor in armory.py.

Scope: mack (behavioral breakage — refactor-equivalence, boundary/numeric
abuse, lost-update-shaped concurrency from NORMAL use). No source edits.
No git. Companion to the builder's test_armory_catalog_loadout.py, which
this file does NOT modify — it only adds coverage the builder's suite
leaves open:

  1. exact-boundary purchase behavior (AT cap allow / ONE over cap reject)
     across ALL three slots (attack_drone, defense_drone, mines), not just
     attack_drone;
  2. the Drone Bay bonus actually SHIFTING the enforced boundary, not just
     shifting the returned numbers;
  3. shipless / specless purchase 400s, with exact message text pinned;
  4. that POST /purchase computes caps from its OWN locally-loaded ship +
     spec (exactly one query each), never a second fetch;
  5. an empirical, live-SQLAlchemy proof of a identity-map staleness hazard
     on the SAME purchase handler's player-row re-read (pre-existing --
     NOT introduced by this refactor, but load-bearing on the money path
     this refactor sits inside of, so surfaced here).
"""
import types
import uuid

import pytest

from src.api.routes import armory as route
from src.models.player import Player
from src.models.ship import Ship, ShipSpecification
from src.models.station import Station


# --------------------------------------------------------------------------- #
# fakes (self-contained -- not imported from the builder's file, so this file
# has no dependency on their fixture shapes changing under it)
# --------------------------------------------------------------------------- #

class _CountingFakeQuery:
    def __init__(self, result, model, counter):
        self._result = result
        self._model = model
        self._counter = counter

    def filter(self, *a, **k):
        return self

    def with_for_update(self, *a, **k):
        return self

    def first(self):
        self._counter[self._model] = self._counter.get(self._model, 0) + 1
        return self._result


class _CountingFakeSession:
    """Same query-dispatch idiom as the builder's _FakeSession, but counts
    calls per model so tests can assert Ship/ShipSpecification are queried
    exactly once inside POST /purchase (i.e. caps come from the locally
    loaded row, not a second fetch through _current_loadout)."""

    def __init__(self, *, player=None, station=None, ship=None, spec=None):
        self._by_model = {Player: player, Station: station, Ship: ship, ShipSpecification: spec}
        self.committed = False
        self.query_counts: dict = {}

    def query(self, model):
        assert model in self._by_model, f"unexpected query model {model}"
        return _CountingFakeQuery(self._by_model[model], model, self.query_counts)

    def commit(self):
        self.committed = True


def make_player(*, current_ship_id=None, attack_drones=0, defense_drones=0,
                 mines=0, credits=1_000_000, is_docked=True, current_port_id=None):
    return types.SimpleNamespace(
        id=uuid.uuid4(),
        current_ship_id=current_ship_id,
        attack_drones=attack_drones,
        defense_drones=defense_drones,
        mines=mines,
        credits=credits,
        is_docked=is_docked,
        current_port_id=current_port_id,
    )


def make_ship(*, drone_bay_level=0, ship_type="light_freighter"):
    return types.SimpleNamespace(
        id=uuid.uuid4(),
        type=ship_type,
        upgrades={"DRONE_BAY": drone_bay_level} if drone_bay_level else {},
    )


def make_spec(*, ship_type="light_freighter", max_drones=10):
    return types.SimpleNamespace(type=ship_type, max_drones=max_drones)


def make_station(*, is_spacedock=True, services=None):
    return types.SimpleNamespace(id=uuid.uuid4(), is_spacedock=is_spacedock, services=services or {})


async def _purchase(item, quantity, *, ship=None, spec=None, station=None, player=None, db=None):
    request = route.ArmoryPurchaseRequest(item=item, quantity=quantity)
    return await route.purchase_armory_item(request=request, player=player, db=db)


# --------------------------------------------------------------------------- #
# Q1 -- exact-boundary equivalence, all three slots
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_attack_drone_purchase_allowed_exactly_at_cap():
    """cap = 10 (spec 10 + bonus 0); current 9 + qty 1 == 10 -> must ALLOW."""
    ship = make_ship(drone_bay_level=0)
    spec = make_spec(max_drones=10)
    station = make_station()
    player = make_player(current_ship_id=ship.id, attack_drones=9, current_port_id=station.id)
    db = _CountingFakeSession(player=player, station=station, ship=ship, spec=spec)

    result = await _purchase("attack_drone", 1, player=player, db=db)

    assert result["loadout"]["attack_drones"] == 10
    assert db.committed is True


@pytest.mark.asyncio
async def test_attack_drone_purchase_rejected_one_over_cap():
    """current already AT cap (10); buying 1 more must 400, not silently clamp."""
    ship = make_ship(drone_bay_level=0)
    spec = make_spec(max_drones=10)
    station = make_station()
    player = make_player(current_ship_id=ship.id, attack_drones=10, current_port_id=station.id)
    db = _CountingFakeSession(player=player, station=station, ship=ship, spec=spec)

    with pytest.raises(Exception) as exc:
        await _purchase("attack_drone", 1, player=player, db=db)

    assert getattr(exc.value, "status_code", None) == 400
    assert "Capacity exceeded" in getattr(exc.value, "detail", "")
    assert db.committed is False


@pytest.mark.asyncio
async def test_defense_drone_purchase_boundary_matches_attack_drone_boundary():
    """defense_drone shares the SAME cap formula as attack_drone (spec.max_drones
    + bonus) -- prove the shared slot isn't accidentally cross-wired or skipped."""
    ship = make_ship(drone_bay_level=0)
    spec = make_spec(max_drones=6)
    station = make_station()

    # AT cap: allow
    player_ok = make_player(current_ship_id=ship.id, defense_drones=5, current_port_id=station.id)
    db_ok = _CountingFakeSession(player=player_ok, station=station, ship=ship, spec=spec)
    result = await _purchase("defense_drone", 1, player=player_ok, db=db_ok)
    assert result["loadout"]["defense_drones"] == 6

    # ONE over: reject
    player_over = make_player(current_ship_id=ship.id, defense_drones=6, current_port_id=station.id)
    db_over = _CountingFakeSession(player=player_over, station=station, ship=ship, spec=spec)
    with pytest.raises(Exception) as exc:
        await _purchase("defense_drone", 1, player=player_over, db=db_over)
    assert getattr(exc.value, "status_code", None) == 400


@pytest.mark.asyncio
async def test_mines_purchase_boundary_uses_flat_mines_cap_not_spec():
    """mines cap is the flat MINES_CAP (25) regardless of spec.max_drones --
    prove the mines slot didn't get accidentally coupled to the drone formula
    during extraction."""
    ship = make_ship(drone_bay_level=0)
    spec = make_spec(max_drones=999)  # deliberately huge, must NOT affect mines cap
    station = make_station()

    player_ok = make_player(current_ship_id=ship.id, mines=route.MINES_CAP - 1, current_port_id=station.id)
    db_ok = _CountingFakeSession(player=player_ok, station=station, ship=ship, spec=spec)
    result = await _purchase("armored_mine", 1, player=player_ok, db=db_ok)
    assert result["loadout"]["mines"] == route.MINES_CAP

    player_over = make_player(current_ship_id=ship.id, mines=route.MINES_CAP, current_port_id=station.id)
    db_over = _CountingFakeSession(player=player_over, station=station, ship=ship, spec=spec)
    with pytest.raises(Exception) as exc:
        await _purchase("armored_mine", 1, player=player_over, db=db_over)
    assert getattr(exc.value, "status_code", None) == 400


@pytest.mark.parametrize("level", [0, 1, 2, 3, 5, 10])
@pytest.mark.asyncio
async def test_drone_bay_bonus_shifts_the_enforced_boundary_not_just_the_number(level):
    """The Drone Bay bonus must shift the ENFORCED cap, not just the number
    _armory_caps returns. Buy an amount that would overshoot the base spec
    cap but land exactly on the bonus-shifted cap -- must be allowed."""
    ship = make_ship(drone_bay_level=level)
    spec = make_spec(max_drones=10)
    station = make_station()
    bonus = 2 * level
    shifted_cap = 10 + bonus

    # current sits at the BASE (unshifted) cap; buy exactly `bonus` more to
    # land on the shifted cap. If the bonus weren't applied, this would 400.
    player = make_player(current_ship_id=ship.id, attack_drones=10, current_port_id=station.id)
    db = _CountingFakeSession(player=player, station=station, ship=ship, spec=spec)

    if bonus == 0:
        with pytest.raises(Exception) as exc:
            await _purchase("attack_drone", 1, player=player, db=db)
        assert getattr(exc.value, "status_code", None) == 400
    else:
        result = await _purchase("attack_drone", bonus, player=player, db=db)
        assert result["loadout"]["attack_drones"] == shifted_cap
        assert result["loadout"]["caps"]["attack_drones"] == shifted_cap

    # one further unit must always reject, regardless of level
    player2 = make_player(current_ship_id=ship.id, attack_drones=shifted_cap, current_port_id=station.id)
    db2 = _CountingFakeSession(player=player2, station=station, ship=ship, spec=spec)
    with pytest.raises(Exception) as exc2:
        await _purchase("attack_drone", 1, player=player2, db=db2)
    assert getattr(exc2.value, "status_code", None) == 400


# --------------------------------------------------------------------------- #
# Q1 -- shipless / specless purchase parity with pre-refactor behavior
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_purchase_shipless_400_with_expected_message():
    station = make_station()
    player = make_player(current_ship_id=None, current_port_id=station.id)
    db = _CountingFakeSession(player=player, station=station, ship=None, spec=None)

    with pytest.raises(Exception) as exc:
        await _purchase("attack_drone", 1, player=player, db=db)

    assert getattr(exc.value, "status_code", None) == 400
    assert getattr(exc.value, "detail", "") == "You need an active ship to carry armory items"
    assert db.committed is False


@pytest.mark.asyncio
async def test_purchase_specless_400_with_expected_message():
    """Ship resolves but its type has no ShipSpecification row (mirrors the
    catalog-side 'mystery_hull' regression guard, but on the money path)."""
    ship = make_ship(ship_type="mystery_hull")
    station = make_station()
    player = make_player(current_ship_id=ship.id, current_port_id=station.id)
    db = _CountingFakeSession(player=player, station=station, ship=ship, spec=None)

    with pytest.raises(Exception) as exc:
        await _purchase("attack_drone", 1, player=player, db=db)

    assert getattr(exc.value, "status_code", None) == 400
    assert getattr(exc.value, "detail", "") == "No specification available for your current ship"
    assert db.committed is False


# --------------------------------------------------------------------------- #
# Q1(b) -- caps computed from the LOCALLY loaded ship+spec, not a re-fetch
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_purchase_queries_ship_and_spec_exactly_once_each():
    """If the refactor had swapped the inline caps block for a call to
    _current_loadout(player, db) instead of _armory_caps(ship, spec), it
    would issue a SECOND Ship/ShipSpecification query. Assert exactly one
    query per model -- proves caps ride the handler's own already-loaded
    rows, closing the identity-map/re-query risk named in the WO."""
    ship = make_ship(drone_bay_level=1)
    spec = make_spec(max_drones=10)
    station = make_station()
    player = make_player(current_ship_id=ship.id, attack_drones=0, current_port_id=station.id)
    db = _CountingFakeSession(player=player, station=station, ship=ship, spec=spec)

    await _purchase("attack_drone", 1, player=player, db=db)

    assert db.query_counts.get(Ship) == 1, f"Ship queried {db.query_counts.get(Ship)}x, expected exactly 1"
    assert db.query_counts.get(ShipSpecification) == 1, (
        f"ShipSpecification queried {db.query_counts.get(ShipSpecification)}x, expected exactly 1"
    )


# --------------------------------------------------------------------------- #
# Q2 -- shipless / specless catalog crash-avoidance, independent probes
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_current_loadout_is_none_not_a_dict_with_null_caps_shipless():
    player = make_player(current_ship_id=None)
    db = _CountingFakeSession(player=player)
    loadout = route._current_loadout(player, db)
    assert loadout is None
    assert not (isinstance(loadout, dict))  # explicitly rule out {"caps": None, ...}


@pytest.mark.asyncio
async def test_current_loadout_is_none_not_a_dict_with_null_caps_specless():
    ship = make_ship(ship_type="mystery_hull")
    player = make_player(current_ship_id=ship.id)
    db = _CountingFakeSession(player=player, ship=ship, spec=None)
    loadout = route._current_loadout(player, db)
    assert loadout is None
    assert not (isinstance(loadout, dict))


@pytest.mark.asyncio
async def test_catalog_response_frontend_deref_pattern_never_crashes_shipless():
    """Mirror the frontend's actual gate (`if (data.loadout) { ...caps.attack_drones }`)
    against the real route response -- a naive Python transliteration of that
    gate must never raise AttributeError/TypeError for a shipless player."""
    player = make_player(current_ship_id=None)
    db = _CountingFakeSession(player=player)

    response = await route.get_armory_catalog(player=player, db=db)

    loadout = response.get("loadout")
    if loadout:  # mirrors `if (data.loadout)` in the frontend
        _ = loadout["caps"]["attack_drones"]  # would crash if loadout were {"caps": None}
    else:
        assert "loadout" not in response


# --------------------------------------------------------------------------- #
# identity-map staleness on the player row re-read (pre-existing, NOT
# introduced by this refactor -- the caps extraction never touches this
# code path -- but it sits directly on the money path this WO is verifying,
# so it's surfaced here rather than silently passed over. Proven with a
# LIVE SQLAlchemy session, per project convention: FakeSession's flat
# Model -> row map is structurally blind to identity-map semantics and
# cannot prove or disprove this either way.
# --------------------------------------------------------------------------- #

def test_purchase_handler_player_reread_pattern_is_identity_map_stale():
    """armory.py's purchase handler does:

        player: Player = Depends(get_current_player)      # UNLOCKED read,
                                                            # same `db` session
        ...
        player = db.query(Player).filter(Player.id == player.id) \\
                   .with_for_update().first()               # "authoritative"
                                                              # locked re-read,
                                                              # SAME session,
                                                              # SAME PK

    get_current_player() (src/auth/dependencies.py:128) issues its own
    unlocked `db.query(Player).filter(...).first()` on the SAME session
    FastAPI injects into the route body. This test proves, with a live
    SQLAlchemy 2.0 session (not a mock), that the second query -- despite
    genuinely holding `.with_for_update()` -- returns the SAME cached Python
    object with PRE-lock attribute values when another session commits a
    change to that row in between. The row lock is real; the "freshness"
    the comment at armory.py:202-203 promises is not, because nothing calls
    `.populate_existing()`.

    Money-path blast radius: `player.credits` (the credit check, armory.py
    :273) and `player.attack_drones` / `defense_drones` / `mines` (the cap
    check, armory.py :250-254) are ALL read off this same stale object.
    Concretely: two near-simultaneous purchases from the same player (a
    double-click on Buy, or two open tabs) -- request A's dependency
    resolution reads player unlocked, request B fully completes (spends
    credits / adds drones) and commits before A's with_for_update() fires --
    A's "locked, authoritative" check still sees B's PRE-purchase counts,
    can pass a cap/credit check it should fail, and A's subsequent write
    (`player.credits -= total_cost`, `player.attack_drones += quantity`)
    lands on the stale object and overwrites B's update on commit (lost
    update on both money and cap-enforced loadout counts).
    """
    import sqlalchemy as sa
    from sqlalchemy.orm import declarative_base, sessionmaker

    Base = declarative_base()

    class Account(Base):
        __tablename__ = "accounts_mack_repro"
        id = sa.Column(sa.Integer, primary_key=True)
        credits = sa.Column(sa.Integer)
        attack_drones = sa.Column(sa.Integer)

    engine = sa.create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    SessionFactory = sessionmaker(bind=engine, autoflush=False)

    seed = SessionFactory()
    seed.add(Account(id=1, credits=1000, attack_drones=0))
    seed.commit()
    seed.close()

    # Session S == the ONE db session FastAPI injects for the whole request
    # (get_current_player and the route body share it via Depends(get_db)).
    S = SessionFactory()

    # get_current_player()-shaped unlocked pre-read.
    player = S.query(Account).filter(Account.id == 1).first()
    assert player.credits == 1000 and player.attack_drones == 0

    # A concurrent request (separate session) completes a purchase in between.
    concurrent = SessionFactory()
    row = concurrent.query(Account).filter(Account.id == 1).first()
    row.credits = 250        # spent 750 credits
    row.attack_drones = 7    # bought 7 drones
    concurrent.commit()
    concurrent.close()

    # armory.py's "Lock the player row" re-read -- same session, same PK.
    player2 = S.query(Account).filter(Account.id == 1).with_for_update().first()

    assert player2 is player, "identity map returned a different object than expected"
    assert player2.credits == 1000, (
        f"expected STALE credits=1000 (proving the bug); got {player2.credits} -- "
        "if this ever reads 250, the ORM/driver behavior underlying this finding "
        "has changed and the finding should be re-evaluated, not assumed fixed."
    )
    assert player2.attack_drones == 0, (
        f"expected STALE attack_drones=0 (proving the bug); got {player2.attack_drones}"
    )

    S.close()
