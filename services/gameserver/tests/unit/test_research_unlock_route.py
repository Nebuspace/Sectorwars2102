"""Route-level tests for POST /research/tech/{node_id}/unlock (WO-PLN-UNLOCK-1).

Calls the async route handlers directly (no TestClient / no real DB — the Mac
has no live Postgres) with a fake Session mirroring research_service.unlock_node's
own ``db.query(Player).filter(...).with_for_update().first()`` shape, the exact
idiom already established in test_research_service.py's ``_FakeSession``. The
route adds NO economy logic of its own; these tests prove the HTTP-facing
wrapper (status-code mapping, zero-deduction on every failure branch, the
response shape) on top of the already-unit-tested unlock_node.

``_tech_tree_state`` (the additive GET /cockpit field the client polls after a
successful unlock) is exercised directly as a pure function of the player's
ledger — it takes no db argument, so no Planet/soft-cap mocking is needed to
prove the locked/affordable/unlocked bookkeeping the Accept criteria checks for.
"""
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from src.services import research_service as rs
from src.services import tech_tree
from src.api.routes import research_cockpit as route


@pytest.fixture(autouse=True)
def _noop_flag_modified(monkeypatch):
    """SimpleNamespace player fakes aren't SQLAlchemy-mapped; no-op flag_modified
    exactly as test_research_service.py does for the same reason (the code also
    reassigns the attribute, so marking the JSONB column dirty is irrelevant to
    the logic under test)."""
    monkeypatch.setattr(rs, "flag_modified", lambda *a, **k: None)


def make_player(player_id="p1", ledger=None):
    return SimpleNamespace(id=player_id, research_ledger=ledger)


class _FakeQuery:
    def __init__(self, single=None):
        self._single = single

    def filter(self, *a, **k):
        return self

    def populate_existing(self, *a, **k):
        # WO-MONEY-REREAD-SERVICES: no-op passthrough, matches real
        # SQLAlchemy Query's chainable-and-returns-self shape.
        return self

    def with_for_update(self, *a, **k):
        return self

    def first(self):
        return self._single


class _FakeSession:
    """Routes every query() to the fixed player row (the route/unlock_node only
    ever queries Player here); records flush/commit so a failure path's
    zero-deduction claim is checkable against BOTH the ledger and the commit."""

    def __init__(self, player):
        self._player = player
        self.flushed = False
        self.committed = False

    def query(self, model):
        return _FakeQuery(single=self._player)

    def flush(self):
        self.flushed = True

    def commit(self):
        self.committed = True

    def rollback(self):
        pass


async def _unlock(db, player, node_id):
    return await route.unlock_tech_node_endpoint(node_id, current_player=player, db=db)


# --------------------------------------------------------------------------- #
# happy path
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_unlock_happy_path_deducts_and_appends():
    p = make_player(ledger={"rp": 100, "unlocked": [tech_tree.FREE_ROOT_ID]})
    db = _FakeSession(p)

    result = await _unlock(db, p, "t.defense.railgun.1")

    assert result["success"] is True
    assert result["nodeId"] == "t.defense.railgun.1"
    assert result["bankedRp"] == 50  # 100 - 50 cost
    assert "t.defense.railgun.1" in result["unlockedNodes"]
    assert "t.defense.railgun.1" in p.research_ledger["unlocked"]
    assert p.research_ledger["rp"] == 50
    assert db.committed is True


# --------------------------------------------------------------------------- #
# insufficient RP -> 4xx, zero deduction, zero commit
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_unlock_insufficient_rp_no_deduct():
    p = make_player(ledger={"rp": 10, "unlocked": [tech_tree.FREE_ROOT_ID]})
    db = _FakeSession(p)

    with pytest.raises(HTTPException) as exc_info:
        await _unlock(db, p, "t.defense.railgun.1")  # costs 50

    assert exc_info.value.status_code == 402
    assert "insufficient" in exc_info.value.detail.lower()
    # Zero deduction: ledger untouched, no commit reached.
    assert p.research_ledger["rp"] == 10
    assert db.committed is False


# --------------------------------------------------------------------------- #
# unknown node -> 404
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_unlock_unknown_node_404():
    p = make_player(ledger={"rp": 1000, "unlocked": [tech_tree.FREE_ROOT_ID]})
    db = _FakeSession(p)

    with pytest.raises(HTTPException) as exc_info:
        await _unlock(db, p, "t.not.a.real.node")

    assert exc_info.value.status_code == 404
    assert p.research_ledger["rp"] == 1000
    assert db.committed is False


# --------------------------------------------------------------------------- #
# double-unlock -> 4xx, no double charge
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_unlock_double_unlock_no_double_charge():
    p = make_player(ledger={
        "rp": 100,
        "unlocked": [tech_tree.FREE_ROOT_ID, "t.defense.railgun.1"],
    })
    db = _FakeSession(p)

    with pytest.raises(HTTPException) as exc_info:
        await _unlock(db, p, "t.defense.railgun.1")

    assert exc_info.value.status_code == 400
    assert "already" in exc_info.value.detail.lower()
    # No second deduction — the 100 RP banked before the (already-applied) first
    # unlock is untouched by this second, rejected attempt.
    assert p.research_ledger["rp"] == 100
    assert db.committed is False


# --------------------------------------------------------------------------- #
# missing prereq -> 4xx, zero deduction (the 4th unlock_node failure branch)
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_unlock_missing_prereq_no_deduct():
    p = make_player(ledger={"rp": 1000, "unlocked": [tech_tree.FREE_ROOT_ID]})
    db = _FakeSession(p)

    with pytest.raises(HTTPException) as exc_info:
        await _unlock(db, p, "t.defense.grid.1")  # requires railgun first

    assert exc_info.value.status_code == 400
    assert "prereq" in exc_info.value.detail.lower()
    assert p.research_ledger["rp"] == 1000
    assert db.committed is False


# --------------------------------------------------------------------------- #
# post-unlock: the SAME predicate citadel_service.py:1522 gates
# build_defense_building on (research_service.player_has_tech) now passes.
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_unlock_railgun_then_player_has_tech_gate_passes():
    p = make_player(ledger={"rp": 100, "unlocked": [tech_tree.FREE_ROOT_ID]})
    db = _FakeSession(p)

    assert rs.player_has_tech(p, "t.defense.railgun.1") is False  # gate closed before

    result = await _unlock(db, p, "t.defense.railgun.1")

    assert result["success"] is True
    assert rs.player_has_tech(p, "t.defense.railgun.1") is True  # gate open after


# --------------------------------------------------------------------------- #
# _tech_tree_state (the GET /cockpit additive field the client polls) — proves
# the Accept criterion "GET /cockpit shows the node in the unlocked set" at the
# logic level, without needing to mock Planet/soft-cap queries the rest of
# get_cockpit performs.
# --------------------------------------------------------------------------- #

def test_tech_tree_state_reflects_unlocked_and_affordability():
    p = make_player(ledger={"rp": 40, "unlocked": [tech_tree.FREE_ROOT_ID, "t.defense.railgun.1"]})

    nodes = {n["id"]: n for n in route._tech_tree_state(p)}

    railgun = nodes["t.defense.railgun.1"]
    assert railgun["unlocked"] is True
    assert railgun["affordable"] is False  # no re-purchase path once unlocked

    grid = nodes["t.defense.grid.1"]  # prereq (railgun) now met, but 40 RP < 120 cost
    assert grid["unlocked"] is False
    assert grid["prereqsMet"] is True
    assert grid["affordable"] is False

    hazard_clear = nodes["t.terraforming.hazard_clear.1"]  # prereq (root) met, 40 RP >= 60? no
    assert hazard_clear["prereqsMet"] is True
    assert hazard_clear["affordable"] is False  # costs 60, only 40 banked

    intensity = nodes["t.terraforming.intensity.1"]  # prereq is plot_clear, not unlocked
    assert intensity["prereqsMet"] is False
    assert intensity["affordable"] is False
