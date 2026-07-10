"""Regression pin for WO-RT-LOCK-ACTIVATE.

Before this, every sweep in npc_scheduler_service.py except the citizen
re-bake and presence sweeps (which already carried their own hand-picked
keys, WO-GC-C / WO-PRESWEEP) shared the single global ``_ADVISORY_LOCK_KEY``
with the main NPC tick (``_run_due_ticks_sync``). A tick running Loop A/B/C
across the whole galaxy could hold that key for minutes, and every other
sweep sharing it would skip (``pg_try_advisory_xact_lock``) rather than run.
Each sweep now gets its own derived key (module-level, mnemonic-packed —
see ``_mnemonic_lock_key``) so it only ever serializes against another
instance of ITSELF; ``bootstrap_region_sync`` — the one lock site with a
``region_id`` in scope — switched to ``region_lock_key(region_id)`` instead,
its first production call site.

Fully DB-free and import-only: no SessionLocal, no live Postgres. The
site -> key map below is a REGRESSION PIN, not a design doc — if it drifts,
either the map or the code is wrong and this test says which lines to look
at.
"""
from __future__ import annotations

import ast
import uuid
from pathlib import Path

import pytest

from src.services import npc_scheduler_service as sched
from src.services import economy_faucet_service as faucet

_MODULE_PATH = Path(sched.__file__)
_SOURCE = _MODULE_PATH.read_text(encoding="utf-8")

_FAUCET_MODULE_PATH = Path(faucet.__file__)
_FAUCET_SOURCE = _FAUCET_MODULE_PATH.read_text(encoding="utf-8")


def _parse_module() -> ast.Module:
    return ast.parse(_SOURCE, filename=str(_MODULE_PATH))


# --------------------------------------------------------------------------- #
# Every static (module-level constant) lock key in the file.
# --------------------------------------------------------------------------- #

ALL_STATIC_KEYS = {
    # Global — kept EXCLUSIVELY by the main tick after this change.
    "_ADVISORY_LOCK_KEY": sched._ADVISORY_LOCK_KEY,
    # Pre-existing, hand-picked (WO-GC-C / WO-PRESWEEP) — untouched.
    "_CITIZEN_REBAKE_LOCK_KEY": sched._CITIZEN_REBAKE_LOCK_KEY,
    "_PRESENCE_SWEEP_LOCK_KEY": sched._PRESENCE_SWEEP_LOCK_KEY,
    # New (WO-RT-LOCK-ACTIVATE) — one per sweep-type, mnemonic-derived.
    "_WEEKLY_DECAY_LOCK_KEY": sched._WEEKLY_DECAY_LOCK_KEY,
    "_GENESIS_COMPLETION_LOCK_KEY": sched._GENESIS_COMPLETION_LOCK_KEY,
    "_PLANETARY_ADVANCE_LOCK_KEY": sched._PLANETARY_ADVANCE_LOCK_KEY,
    "_GOVERNANCE_SWEEP_LOCK_KEY": sched._GOVERNANCE_SWEEP_LOCK_KEY,
    "_CONSTRUCTION_ADVANCE_LOCK_KEY": sched._CONSTRUCTION_ADVANCE_LOCK_KEY,
    "_ECONOMIC_METRICS_LOCK_KEY": sched._ECONOMIC_METRICS_LOCK_KEY,
    "_IDLE_INCOME_LOCK_KEY": sched._IDLE_INCOME_LOCK_KEY,
    "_DAILY_STIPEND_LOCK_KEY": sched._DAILY_STIPEND_LOCK_KEY,
    "_BOUNTY_ACCRUAL_LOCK_KEY": sched._BOUNTY_ACCRUAL_LOCK_KEY,
    "_SUSTAINED_DRIP_LOCK_KEY": sched._SUSTAINED_DRIP_LOCK_KEY,
    "_PORT_OPERATING_COSTS_LOCK_KEY": sched._PORT_OPERATING_COSTS_LOCK_KEY,
    "_STATION_RECOVERY_LOCK_KEY": sched._STATION_RECOVERY_LOCK_KEY,
    "_RECLAIM_FLAG_LOCK_KEY": sched._RECLAIM_FLAG_LOCK_KEY,
    "_PRICE_HISTORY_LOCK_KEY": sched._PRICE_HISTORY_LOCK_KEY,
    "_ROUTE_RUNS_RETENTION_LOCK_KEY": sched._ROUTE_RUNS_RETENTION_LOCK_KEY,
    "_ORPHAN_SCHEDULE_REPAIR_LOCK_KEY": sched._ORPHAN_SCHEDULE_REPAIR_LOCK_KEY,
    "_SEED_TRADER_ROSTERS_LOCK_KEY": sched._SEED_TRADER_ROSTERS_LOCK_KEY,
    "_LAW_PATROL_DISPERSAL_LOCK_KEY": sched._LAW_PATROL_DISPERSAL_LOCK_KEY,
    "_STRANDED_RELOCATE_LOCK_KEY": sched._STRANDED_RELOCATE_LOCK_KEY,
    "_TRADER_NOTORIETY_LOCK_KEY": sched._TRADER_NOTORIETY_LOCK_KEY,
    "_TRADER_MISSION_LOCK_KEY": sched._TRADER_MISSION_LOCK_KEY,
    "_BULK_FILL_TRADERS_LOCK_KEY": sched._BULK_FILL_TRADERS_LOCK_KEY,
    "_RETENTION_SWEEP_LOCK_KEY": sched._RETENTION_SWEEP_LOCK_KEY,
    # WO-CMB-SUSPECT-LIFE-1 / WO-PIRATE-ECO-2 held-sweep wiring — two more
    # own-key sweeps. (WO-RT-TEAM-REP's TEAM_REPUTATION_SWEEP_LOCK_KEY is
    # imported from team_reputation_service.py, not module-level HERE, so
    # it is a name-site below but deliberately absent from this dict.)
    "_SUSPECT_CLEAR_LOCK_KEY": sched._SUSPECT_CLEAR_LOCK_KEY,
    "_PIRATE_ECOSYSTEM_TICK_LOCK_KEY": sched._PIRATE_ECOSYSTEM_TICK_LOCK_KEY,
    # WO-SCHED-LOOP-WEDGE refinement — contract generation's write phase now
    # takes its own lock (the wrapper previously took none at all), so two
    # gameserver instances can't double-generate.
    "_CONTRACT_GENERATION_LOCK_KEY": sched._CONTRACT_GENERATION_LOCK_KEY,
}

# Every {"key": <bare Name>} lock-acquisition site, keyed by its enclosing
# function. bootstrap_region_sync is deliberately absent -- its site binds
# `region_lock_key(region_id)`, a Call, not a bare Name; it is pinned
# separately below.
EXPECTED_NAME_SITE_MAP = {
    "_run_weekly_decay_sync": "_WEEKLY_DECAY_LOCK_KEY",
    "_run_genesis_completion_sync": "_GENESIS_COMPLETION_LOCK_KEY",
    "_run_planetary_advance_sync": "_PLANETARY_ADVANCE_LOCK_KEY",
    "_run_governance_sweep_sync": "_GOVERNANCE_SWEEP_LOCK_KEY",
    "_run_construction_advance_sync": "_CONSTRUCTION_ADVANCE_LOCK_KEY",
    "_run_economic_metrics_snapshot_sync": "_ECONOMIC_METRICS_LOCK_KEY",
    "_run_idle_income_sweep_sync": "_IDLE_INCOME_LOCK_KEY",
    "_run_daily_stipend_sweep_sync": "_DAILY_STIPEND_LOCK_KEY",
    "_run_bounty_accrual_sweep_sync": "_BOUNTY_ACCRUAL_LOCK_KEY",
    "_run_sustained_reputation_drip_sweep_sync": "_SUSTAINED_DRIP_LOCK_KEY",
    "_run_port_operating_costs_sync": "_PORT_OPERATING_COSTS_LOCK_KEY",
    "_run_station_recovery_sync": "_STATION_RECOVERY_LOCK_KEY",
    "_run_reclaim_flag_sweep_sync": "_RECLAIM_FLAG_LOCK_KEY",
    "_run_price_history_sweep_sync": "_PRICE_HISTORY_LOCK_KEY",
    "_run_route_runs_retention_sync": "_ROUTE_RUNS_RETENTION_LOCK_KEY",
    "_run_due_ticks_sync": "_ADVISORY_LOCK_KEY",  # the main tick -- KEEPS the global key
    "_repair_orphan_schedules_sync": "_ORPHAN_SCHEDULE_REPAIR_LOCK_KEY",
    "_seed_trader_rosters_sync": "_SEED_TRADER_ROSTERS_LOCK_KEY",
    "_disperse_law_patrols_sync": "_LAW_PATROL_DISPERSAL_LOCK_KEY",
    "_relocate_stranded_npcs_sync": "_STRANDED_RELOCATE_LOCK_KEY",
    "_assign_trader_notoriety_sync": "_TRADER_NOTORIETY_LOCK_KEY",
    "_assign_trader_missions_sync": "_TRADER_MISSION_LOCK_KEY",
    "_bulk_fill_traders_sync": "_BULK_FILL_TRADERS_LOCK_KEY",
    "_run_retention_sweep_sync": "_RETENTION_SWEEP_LOCK_KEY",
    "_run_citizen_rebake_sweep_sync": "_CITIZEN_REBAKE_LOCK_KEY",
    "_run_presence_sweep_sync": "_PRESENCE_SWEEP_LOCK_KEY",
    # WO-CMB-SUSPECT-LIFE-1 / WO-RT-TEAM-REP / WO-PIRATE-ECO-2 held-sweep
    # wiring — three previously-built cores, wired into the loop dispatch
    # this pass. TEAM_REPUTATION_SWEEP_LOCK_KEY is imported from
    # team_reputation_service.py (its own pre-declared 'TREP' mnemonic),
    # not redeclared here — the bare-Name binding site still shows up in
    # this AST scan regardless of where the name is defined.
    "_run_suspect_clear_sweep_sync": "_SUSPECT_CLEAR_LOCK_KEY",
    "_run_team_reputation_sweep_sync": "TEAM_REPUTATION_SWEEP_LOCK_KEY",
    "_run_pirate_ecosystem_tick_sync": "_PIRATE_ECOSYSTEM_TICK_LOCK_KEY",
    # WO-SCHED-LOOP-WEDGE: generation's write phase, own advisory lock.
    "_run_contract_generation_sync": "_CONTRACT_GENERATION_LOCK_KEY",
}

# 27 bare-Name sites + 1 Call-form site (bootstrap_region_sync) = the true
# lock-site count (superseding any stale figure quoted anywhere else --
# this file is the enumeration of record going forward).
EXPECTED_TOTAL_LOCK_SITES = len(EXPECTED_NAME_SITE_MAP) + 1


def _lock_key_bindings(tree: ast.Module):
    """Yield (enclosing_function_name, value_repr, lineno) for every literal
    ``{"key": <expr>}`` dict in the module -- the exact binding pattern
    every ``pg_try_advisory_xact_lock`` / ``pg_advisory_xact_lock`` call
    site in this file uses. ``value_repr`` is the bare Name id for a
    constant reference, or ``"<func>(...)"`` for a call expression (e.g.
    ``region_lock_key(region_id)``)."""
    results = []

    def visit(node, enclosing):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            enclosing = node.name
        if (
            isinstance(node, ast.Dict)
            and len(node.keys) == 1
            and isinstance(node.keys[0], ast.Constant)
            and node.keys[0].value == "key"
        ):
            value = node.values[0]
            if isinstance(value, ast.Name):
                value_repr = value.id
            elif isinstance(value, ast.Call) and isinstance(value.func, ast.Name):
                value_repr = f"{value.func.id}(...)"
            else:
                value_repr = ast.dump(value)
            results.append((enclosing, value_repr, node.lineno))
        for child in ast.iter_child_nodes(node):
            visit(child, enclosing)

    visit(tree, None)
    return results


def test_true_lock_site_count():
    """Enumerates every pg_try_advisory_xact_lock / pg_advisory_xact_lock
    binding site in the module. This is the count to trust over any
    hand-maintained comment or work-order figure."""
    bindings = _lock_key_bindings(_parse_module())
    assert len(bindings) == EXPECTED_TOTAL_LOCK_SITES, (
        f"expected {EXPECTED_TOTAL_LOCK_SITES} lock-key binding sites, "
        f"found {len(bindings)}: {bindings}"
    )


def test_site_to_key_map_pinned():
    """Pins the exact function -> key-constant map (Pass 1 + main tick)."""
    bindings = _lock_key_bindings(_parse_module())
    name_sites = {fn: val for fn, val, _ in bindings if not val.endswith("(...)")}
    assert name_sites == EXPECTED_NAME_SITE_MAP


def test_bootstrap_region_sync_uses_region_lock_key():
    """Pins the one Pass-2 split: the only lock site with region_id already
    in scope switched to region_lock_key(region_id)."""
    bindings = _lock_key_bindings(_parse_module())
    call_sites = {fn: val for fn, val, _ in bindings if val.endswith("(...)")}
    assert call_sites == {"bootstrap_region_sync": "region_lock_key(...)"}


def test_advisory_lock_key_referenced_only_by_main_tick_and_region_lock_key():
    """Guards the whole point of this work order: no function other than
    the main tick itself (which keeps the global key) and region_lock_key
    (whose None-fallback and XOR-fold legitimately read the global key as
    their base) may reference `_ADVISORY_LOCK_KEY` in code -- AST-based, so
    a docstring/comment mentioning the literal can't produce a false
    positive (and can't mask a real regression either)."""
    allowed = {"region_lock_key", "_run_due_ticks_sync"}
    tree = _parse_module()
    violations = []

    def visit(node, enclosing):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            enclosing = node.name
            if enclosing not in allowed and enclosing is not None:
                for inner in ast.walk(node):
                    if isinstance(inner, ast.Name) and inner.id == "_ADVISORY_LOCK_KEY":
                        violations.append(f"{enclosing}:{inner.lineno}")
                return  # don't descend further -- already fully scanned above
        for child in ast.iter_child_nodes(node):
            visit(child, enclosing)

    visit(tree, None)
    assert not violations, (
        f"_ADVISORY_LOCK_KEY referenced outside the main tick / "
        f"region_lock_key: {violations}"
    )


def test_region_lock_key_has_a_production_call_site():
    """Dormancy over: region_lock_key(...) is actually called from
    production code (bootstrap_region_sync), not just defined."""
    tree = _parse_module()
    call_sites = []

    def visit(node, enclosing):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            enclosing = node.name
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "region_lock_key"
            and enclosing != "region_lock_key"
        ):
            call_sites.append(f"{enclosing}:{node.lineno}")
        for child in ast.iter_child_nodes(node):
            visit(child, enclosing)

    visit(tree, None)
    assert call_sites, "region_lock_key has zero production call sites"
    assert all(site.startswith("bootstrap_region_sync:") for site in call_sites), call_sites


# --------------------------------------------------------------------------- #
# Key-value properties: distinctness + Postgres bigint safety.
# --------------------------------------------------------------------------- #

def test_all_static_keys_pairwise_distinct():
    values = list(ALL_STATIC_KEYS.values())
    seen = {}
    dupes = []
    for name, value in ALL_STATIC_KEYS.items():
        if value in seen:
            dupes.append((seen[value], name, value))
        seen[value] = name
    assert not dupes, f"colliding lock keys: {dupes}"
    assert len(values) == len(set(values))
    assert len(ALL_STATIC_KEYS) == 29  # 1 global + 2 legacy + 26 new sweep-type keys


def test_all_static_keys_nonnegative_and_63bit_safe():
    for name, key in ALL_STATIC_KEYS.items():
        assert isinstance(key, int) and not isinstance(key, bool), name
        assert 0 <= key < 2 ** 63, f"{name}={key} out of Postgres bigint-safe range"


def test_mnemonic_lock_key_matches_hand_picked_legacy_constants():
    """The new helper, applied to the pre-existing hand-picked mnemonics,
    reproduces those exact literals -- proof the new keys extend the same
    idiom rather than inventing a new one."""
    assert sched._mnemonic_lock_key("GCRB") == sched._CITIZEN_REBAKE_LOCK_KEY == 0x47435242
    assert sched._mnemonic_lock_key("PRSW") == sched._PRESENCE_SWEEP_LOCK_KEY == 0x50525357


@pytest.mark.parametrize("bad_code", ["ABC", "ABCDE", "", "TOOLONG"])
def test_mnemonic_lock_key_rejects_wrong_length(bad_code):
    with pytest.raises(ValueError):
        sched._mnemonic_lock_key(bad_code)


# --------------------------------------------------------------------------- #
# region_lock_key: determinism + distinctness (WO-GWQ-ish properties reused
# for the newly-activated call site).
# --------------------------------------------------------------------------- #

def test_region_lock_key_none_falls_back_to_global():
    assert sched.region_lock_key(None) == sched._ADVISORY_LOCK_KEY


def test_region_lock_key_deterministic_across_repeated_calls():
    region_id = uuid.uuid4()
    assert sched.region_lock_key(region_id) == sched.region_lock_key(region_id)


def test_region_lock_key_deterministic_across_uuid_and_str_forms():
    """Keyed off str(region_id) -- a caller passing the UUID object or its
    string form must land on the SAME key, or the lock silently desyncs."""
    region_id = uuid.uuid4()
    assert sched.region_lock_key(region_id) == sched.region_lock_key(str(region_id))


def test_region_lock_key_masked_to_63_bits():
    for _ in range(200):
        key = sched.region_lock_key(uuid.uuid4())
        assert 0 <= key < 2 ** 63
        assert key & sched._LOCK_KEY_MASK_63 == key


def test_region_lock_key_pairwise_distinct_across_regions():
    region_ids = [uuid.uuid4() for _ in range(200)]
    keys = [sched.region_lock_key(rid) for rid in region_ids]
    assert len(set(keys)) == len(keys), "region_lock_key collision across distinct regions"


def test_region_lock_key_distinct_from_every_static_key():
    static_values = set(ALL_STATIC_KEYS.values())
    region_ids = [uuid.uuid4() for _ in range(200)]
    for rid in region_ids:
        key = sched.region_lock_key(rid)
        assert key not in static_values, f"region_lock_key({rid}) collides with a static sweep key"


# --------------------------------------------------------------------------- #
# Weekly-faucet lock key (WO-RT-LOCK-ACTIVATE follow-up): economy_faucet_
# service.py's run_weekly_faucet_sync used to reuse npc_scheduler_service's
# global _ADVISORY_LOCK_KEY literal verbatim ("the value MUST match"), which
# meant it still contended with the main NPC tick after every OTHER sweep was
# de-globalized onto its own key. It now derives its own key via the same
# _mnemonic_lock_key idiom, imported from npc_scheduler_service (verified
# import-graph-safe: npc_scheduler_service's only references to
# economy_faucet_service are function-scoped lazy imports, never module-level,
# so no cycle).
# --------------------------------------------------------------------------- #

def test_weekly_faucet_lock_key_matches_mnemonic_pack():
    """Proof the faucet key extends the same idiom as every other per-sweep
    key rather than reintroducing a hand-picked literal."""
    assert faucet._WEEKLY_FAUCET_LOCK_KEY == sched._mnemonic_lock_key("WFCT")


def test_weekly_faucet_lock_key_distinct_from_all_static_scheduler_keys():
    """The whole point of this follow-up: the faucet no longer contends with
    the main NPC tick, or any other sweep, for the same advisory-lock key."""
    assert faucet._WEEKLY_FAUCET_LOCK_KEY not in ALL_STATIC_KEYS.values()


def test_weekly_faucet_lock_key_distinct_from_region_lock_key_samples():
    """Sampled region_lock_key() outputs must never collide with the
    faucet's static key either."""
    region_ids = [uuid.uuid4() for _ in range(200)]
    for rid in region_ids:
        key = sched.region_lock_key(rid)
        assert key != faucet._WEEKLY_FAUCET_LOCK_KEY, (
            f"region_lock_key({rid}) collides with the weekly-faucet key"
        )


def test_weekly_faucet_lock_key_is_63_bit_safe():
    key = faucet._WEEKLY_FAUCET_LOCK_KEY
    assert isinstance(key, int) and not isinstance(key, bool)
    assert 0 <= key < 2 ** 63


def test_economy_faucet_service_has_no_shared_literal_or_stale_name():
    """Source pin: the old shared-literal constant (0x53573231 / 'SW21') and
    its name (_ADVISORY_LOCK_KEY) must be fully gone from
    economy_faucet_service.py's CODE -- AST-based (not a raw string search),
    so a comment/docstring that legitimately narrates the pre-de-globalization
    history (mentioning the old name in prose) can't produce a false
    positive."""
    tree = ast.parse(_FAUCET_SOURCE, filename=str(_FAUCET_MODULE_PATH))
    stale_literal = 0x53573231
    violations = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id == "_ADVISORY_LOCK_KEY":
            violations.append(f"Name:{node.lineno}")
        if isinstance(node, ast.Constant) and node.value == stale_literal:
            violations.append(f"Constant:{node.lineno}")
    assert not violations, (
        f"stale shared lock-key literal/name still present in "
        f"economy_faucet_service.py: {violations}"
    )
