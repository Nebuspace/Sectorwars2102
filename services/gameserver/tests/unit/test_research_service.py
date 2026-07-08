"""Unit tests for the CRT research kernel (WO-K0): tech_tree + research_service.

Covers (CRT-MASTER §K0 acceptance):
  * DAG reachability of the static catalog (assert_dag_reachable).
  * Lazy-seed of a NULL ledger and the point-of-use readers.
  * unlock_node: spend RP, prereq gating, double-spend protection.
  * The faucet sweep: A.4 one-time WIPE+REFUND, steady-state drain, and
    idempotency (a re-run / zero-faucet planet does NOT double-refund).

No real DB: a tiny in-memory fake Session returns pre-seeded player rows for the
``with_for_update`` lookups the service performs, exactly like the lightweight
SimpleNamespace stand-ins the other unit tests use.
"""
from types import SimpleNamespace

import pytest

from src.services import tech_tree
from src.services import research_service as rs


@pytest.fixture(autouse=True)
def _noop_flag_modified(monkeypatch):
    """WO-CL5: the SimpleNamespace fakes below aren't SQLAlchemy-mapped, so
    ``sqlalchemy.orm.attributes.flag_modified`` raises ``'SimpleNamespace' has no attribute
    '_sa_instance_state'``. The real flag_modified is exercised by the in-process dev proofs
    against real Planet/Player rows (WO-K0 + the K1a refund proof); these unit tests assert the
    pure faucet/unlock LOGIC, for which marking the JSONB column dirty is irrelevant (the code also
    reassigns the attribute). No-op it so the logic tests run."""
    monkeypatch.setattr(rs, "flag_modified", lambda *a, **k: None)


# --------------------------------------------------------------------------- #
# K0-1: catalog / DAG reachability
# --------------------------------------------------------------------------- #

def test_assert_dag_reachable_passes_on_kernel():
    # The shipped kernel must be a valid DAG reachable from the free root.
    rs_ok = tech_tree.assert_dag_reachable()
    assert rs_ok is None  # raises on failure; returns None on success


def test_free_root_is_free_and_only_root():
    assert tech_tree.TECH_NODES[tech_tree.FREE_ROOT_ID]["cost"]["rp"] == 0
    roots = [nid for nid, n in tech_tree.TECH_NODES.items() if not n["prereqs"]]
    assert roots == [tech_tree.FREE_ROOT_ID]


def test_content_unlock_index_maps_buildings():
    # K0-3 relies on these two content keys resolving to their gate nodes.
    # FIX-2 rename: defense_grid -> planetary_defense_grid (avoids collision with
    # the unrelated Station.defense_grid boolean).
    assert tech_tree.node_id_for_content("rail_gun") == "t.defense.railgun.1"
    assert tech_tree.node_id_for_content("planetary_defense_grid") == "t.defense.grid.1"
    # The OLD key must no longer resolve (the rename is total).
    assert tech_tree.node_id_for_content("defense_grid") is None
    assert tech_tree.node_id_for_content("not_a_thing") is None


def test_assert_dag_reachable_detects_cycle(monkeypatch):
    # Inject a cycle and confirm the assertion fires.
    bad = dict(tech_tree.TECH_NODES)
    bad["t.cycle.a"] = {"id": "t.cycle.a", "branch": "production", "tier": 1,
                        "name": "A", "cost": {"rp": 1}, "prereqs": ["t.cycle.b"],
                        "effect": {"kind": "tool", "key": "x"}}
    bad["t.cycle.b"] = {"id": "t.cycle.b", "branch": "production", "tier": 1,
                        "name": "B", "cost": {"rp": 1}, "prereqs": ["t.cycle.a"],
                        "effect": {"kind": "tool", "key": "y"}}
    monkeypatch.setattr(tech_tree, "TECH_NODES", bad)
    with pytest.raises(AssertionError):
        tech_tree.assert_dag_reachable()


def test_assert_dag_reachable_detects_orphan(monkeypatch):
    # A node whose prereq is itself missing → unreachable / missing-ref.
    bad = dict(tech_tree.TECH_NODES)
    bad["t.orphan.1"] = {"id": "t.orphan.1", "branch": "production", "tier": 1,
                         "name": "Orphan", "cost": {"rp": 1},
                         "prereqs": ["t.does.not.exist"],
                         "effect": {"kind": "tool", "key": "z"}}
    monkeypatch.setattr(tech_tree, "TECH_NODES", bad)
    with pytest.raises(AssertionError):
        tech_tree.assert_dag_reachable()


# --------------------------------------------------------------------------- #
# K0-2: readers + lazy seed
# --------------------------------------------------------------------------- #

def make_player(player_id="p1", credits=10000, ledger=None):
    return SimpleNamespace(id=player_id, credits=credits, research_ledger=ledger)


def test_null_ledger_lazy_seeds_free_root():
    p = make_player(ledger=None)
    led = rs.ledger_of(p)
    assert led["rp"] == 0
    assert led["unlocked"] == [tech_tree.FREE_ROOT_ID]
    # Pure read: does NOT persist the seed onto the column.
    assert p.research_ledger is None


def test_player_has_tech_reads_unlocked():
    p = make_player(ledger=None)
    assert rs.player_has_tech(p, tech_tree.FREE_ROOT_ID) is True
    assert rs.player_has_tech(p, "t.defense.railgun.1") is False
    p2 = make_player(ledger={"rp": 0, "unlocked": [tech_tree.FREE_ROOT_ID, "t.defense.railgun.1"]})
    assert rs.player_has_tech(p2, "t.defense.railgun.1") is True


def test_inert_readers_default_when_unwired():
    p = make_player(ledger=None)
    assert rs.has_tool(p, "hazard_clear") is False
    assert rs.gate_value(p, "terraform_intensity", floor=1) == 1
    assert rs.tech_modifier(p, "production_rate", base=0.0) == 0.0


def test_gate_and_modifier_read_unlocked_placeholders():
    p = make_player(ledger={"rp": 0, "unlocked": [
        tech_tree.FREE_ROOT_ID,
        "t.terraforming.plot_clear.1",
        "t.terraforming.intensity.1",
        "t.production.yield.1",
    ]})
    assert rs.has_tool(p, "plot_clear") is True
    assert rs.gate_value(p, "terraform_intensity", floor=1) == 2
    assert rs.tech_modifier(p, "production_rate", base=0.0) == pytest.approx(0.05)


# --------------------------------------------------------------------------- #
# fake session for the locking lookups
# --------------------------------------------------------------------------- #

class _FakeQuery:
    def __init__(self, single=None, rows=None, peek=None):
        # single: the row .first() returns for a Player lookup
        # rows:   the list .all() returns for an owned-planets aggregate
        # peek:   the Row .first() returns for the Player.research_ledger column
        self._single = single
        self._rows = rows if rows is not None else []
        self._peek = peek

    def filter(self, *a, **k):
        return self

    def order_by(self, *a, **k):
        return self

    def with_for_update(self, *a, **k):
        return self

    def first(self):
        # Column-peek query returns a 1-tuple Row (research_ledger,); the
        # Player-row query returns the player object.
        if self._peek is not None:
            return self._peek
        return self._single

    def all(self):
        return self._rows


class _FakeSession:
    """Routes Player vs Planet queries to the right fixtures; records rollback.

    Discriminates by what's passed to ``query()``:
      * ``query(Player)``           -> the locked player row (.first()).
      * ``query(Player.research_ledger)`` -> the unlocked ledger peek Row.
      * ``query(Planet)``           -> the owner's owned-planets list (.all()).
    """
    def __init__(self, player, owned_planets=None):
        self._player = player
        self._owned = owned_planets if owned_planets is not None else []
        self.flushed = False
        self.committed = False
        self.rolled_back = False

    def query(self, model):
        from src.models.player import Player as _P
        from src.models.planet import Planet as _Planet
        # Column expression (Player.research_ledger) -> peek Row.
        if model is getattr(_P, "research_ledger", None):
            ledger = self._player.research_ledger if self._player else None
            peek = None if self._player is None else (ledger,)
            return _FakeQuery(peek=peek)
        if model is _Planet:
            return _FakeQuery(rows=self._owned)
        # Default: the Player row lookup.
        return _FakeQuery(single=self._player)

    def flush(self):
        self.flushed = True

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True


# --------------------------------------------------------------------------- #
# K0-2: unlock pipeline
# --------------------------------------------------------------------------- #

def test_unlock_node_spends_rp():
    p = make_player(ledger={"rp": 100, "insight": 0, "doctrine": 0,
                            "unlocked": [tech_tree.FREE_ROOT_ID]})
    db = _FakeSession(p)
    res = rs.unlock_node(db, p.id, "t.defense.railgun.1")
    assert res["success"] is True
    assert res["rp_remaining"] == 50  # 100 - 50 cost
    assert "t.defense.railgun.1" in p.research_ledger["unlocked"]
    assert db.flushed is True


def test_unlock_node_blocks_on_prereq():
    # defense_grid requires railgun first.
    p = make_player(ledger={"rp": 1000, "unlocked": [tech_tree.FREE_ROOT_ID]})
    db = _FakeSession(p)
    res = rs.unlock_node(db, p.id, "t.defense.grid.1")
    assert res["success"] is False
    assert "prereq" in res["message"].lower()


def test_unlock_node_blocks_on_insufficient_rp():
    p = make_player(ledger={"rp": 10, "unlocked": [tech_tree.FREE_ROOT_ID]})
    db = _FakeSession(p)
    res = rs.unlock_node(db, p.id, "t.defense.railgun.1")  # costs 50
    assert res["success"] is False
    assert "insufficient" in res["message"].lower()


def test_unlock_node_blocks_double_unlock():
    p = make_player(ledger={"rp": 100, "unlocked": [tech_tree.FREE_ROOT_ID, "t.defense.railgun.1"]})
    db = _FakeSession(p)
    res = rs.unlock_node(db, p.id, "t.defense.railgun.1")
    assert res["success"] is False
    assert "already" in res["message"].lower()


# --------------------------------------------------------------------------- #
# K0-2: the faucet sweep — A.4 wipe+refund, steady drain, idempotency
# --------------------------------------------------------------------------- #

def make_planet(planet_id="pl1", owner_id="p1", rp_banked=0):
    events = {}
    if rp_banked:
        events["research_points"] = rp_banked
    return SimpleNamespace(id=planet_id, owner_id=owner_id, active_events=events)


def test_sweep_first_contact_wipes_and_refunds():
    p = make_player(player_id="p1", credits=5000, ledger=None)  # never swept
    planet = make_planet(owner_id="p1", rp_banked=300)
    # FIX-1: the aggregate scans the owner's owned planets; here just the one.
    db = _FakeSession(p, owned_planets=[planet])

    changed = rs.sweep_research_faucet(db, planet)

    assert changed is True
    # A.4: wiped to ledger (NOT added to rp) + refunded credits at the rate.
    assert p.research_ledger["rp"] == 0
    assert p.credits == 5000 + 300 * rs.RP_TO_CREDIT_RATE
    assert "swept_at" in p.research_ledger
    # Faucet drained to zero so it can't be counted twice.
    assert planet.active_events["research_points"] == 0


def test_sweep_first_contact_aggregates_all_owned_planets():
    # FIX-1 CORRECTNESS: on first sweep the refund is the player's TOTAL banked
    # RP across ALL owned planets, not just the first-swept one. Without the
    # aggregate, planet B's pre-kernel RP would later drain as spendable ledger
    # (the windfall the ruling closes).
    p = make_player(player_id="p1", credits=5000, ledger=None)
    planet_a = make_planet(planet_id="pl_a", owner_id="p1", rp_banked=300)
    planet_b = make_planet(planet_id="pl_b", owner_id="p1", rp_banked=200)
    planet_c = make_planet(planet_id="pl_c", owner_id="p1", rp_banked=0)
    db = _FakeSession(p, owned_planets=[planet_a, planet_b, planet_c])

    # Sweep arrives on planet A first.
    changed = rs.sweep_research_faucet(db, planet_a)

    assert changed is True
    # Refund is the SUM across all owned planets (300 + 200 + 0).
    assert p.credits == 5000 + 500 * rs.RP_TO_CREDIT_RATE
    assert p.research_ledger["rp"] == 0
    assert "swept_at" in p.research_ledger
    # EVERY owned planet's faucet is zeroed in the one atomic pass.
    assert planet_a.active_events.get("research_points", 0) == 0
    assert planet_b.active_events.get("research_points", 0) == 0
    assert planet_c.active_events.get("research_points", 0) == 0

    # And a later sweep that reaches planet B is now a pure no-op (already
    # drained + swept_at present) — no second refund, no windfall ledger RP.
    changed_b = rs.sweep_research_faucet(db, planet_b)
    assert changed_b is False
    assert p.credits == 5000 + 500 * rs.RP_TO_CREDIT_RATE
    assert p.research_ledger["rp"] == 0


def test_sweep_is_idempotent_no_double_refund():
    p = make_player(player_id="p1", credits=5000, ledger=None)
    planet = make_planet(owner_id="p1", rp_banked=300)
    db = _FakeSession(p, owned_planets=[planet])

    rs.sweep_research_faucet(db, planet)  # first contact
    credits_after_first = p.credits

    # Re-run on the now-zeroed faucet: pure no-op, no further refund.
    changed_again = rs.sweep_research_faucet(db, planet)
    assert changed_again is False
    assert p.credits == credits_after_first


def test_sweep_steady_state_drains_rp_pays_faucet_copay():
    # A player who has already been swept (swept_at present) gets RP credited,
    # not a *refund* — but the T1.5-1 faucet copay (Max-RULED, WO-COPAY/#9)
    # DOES debit credits on every governed-RP crediting, steady state included
    # (research_service.py:889 calls _apply_faucet_copay unconditionally).
    # Derive the expected debit from the module constants (never a hardcoded
    # literal) so a future re-ruling of FAUCET_CREDIT_COPAY can't re-stale this.
    p = make_player(player_id="p1", credits=5000,
                    ledger={"rp": 10, "insight": 0, "doctrine": 0,
                            "unlocked": [tech_tree.FREE_ROOT_ID],
                            "swept_at": "2026-06-20T00:00:00+00:00"})
    planet = make_planet(owner_id="p1", rp_banked=120)
    db = _FakeSession(p, owned_planets=[planet])

    changed = rs.sweep_research_faucet(db, planet)

    assert changed is True
    assert p.research_ledger["rp"] == 130  # 10 + 120, becomes spendable
    # 120 banked RP is far under the empire soft cap, so governed == raw == 120.
    assert p.credits == 5000 - rs.faucet_copay(120)  # copay debited, no windfall refund
    assert planet.active_events["research_points"] == 0


def test_sweep_steady_state_per_planet_does_not_touch_others():
    # FIX-1 steady state stays PER-PLANET: once swept_at is present, a sweep on
    # planet A drains ONLY planet A — planet B's faucet is untouched (it drains on
    # its own sweep). The aggregate is a one-time first-contact event only.
    p = make_player(player_id="p1", credits=5000,
                    ledger={"rp": 0, "insight": 0, "doctrine": 0,
                            "unlocked": [tech_tree.FREE_ROOT_ID],
                            "swept_at": "2026-06-20T00:00:00+00:00"})
    planet_a = make_planet(planet_id="pl_a", owner_id="p1", rp_banked=50)
    planet_b = make_planet(planet_id="pl_b", owner_id="p1", rp_banked=70)
    db = _FakeSession(p, owned_planets=[planet_a, planet_b])

    changed = rs.sweep_research_faucet(db, planet_a)
    assert changed is True
    assert p.research_ledger["rp"] == 50  # only A drained
    assert planet_a.active_events["research_points"] == 0
    assert planet_b.active_events["research_points"] == 70  # untouched


def test_sweep_zero_faucet_is_noop():
    p = make_player(player_id="p1", credits=5000,
                    ledger={"rp": 0, "insight": 0, "doctrine": 0,
                            "unlocked": [tech_tree.FREE_ROOT_ID],
                            "swept_at": "2026-06-20T00:00:00+00:00"})
    planet = make_planet(owner_id="p1", rp_banked=0)
    db = _FakeSession(p, owned_planets=[planet])
    # Already-swept player, empty faucet -> fast no-op, no player lock taken.
    assert rs.sweep_research_faucet(db, planet) is False
    assert p.credits == 5000
