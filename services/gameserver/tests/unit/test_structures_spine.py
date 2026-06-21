"""Unit tests for the CRT grid spine (WO-K1a): structures.settle/seed + the _via_settle guard.

DB-free: these exercise the guard (I5), the domain-consistent cold-start anchor seed (I8 math),
the anchor round-trip / monotonic gate, and seed idempotency. The behavioral I1 (re-run
byte-identical), I3 (stale-now no-op) and I10 (reproduce-exactly) run as in-process proofs against
a real planet on dev (they need the shipped bodies + a session).
"""
import types
from datetime import datetime, timedelta, UTC

import pytest

import src.services.structures as S


@pytest.fixture(autouse=True)
def _noop_flag_modified(monkeypatch):
    """These DB-free tests use SimpleNamespace planets; sqlalchemy.flag_modified requires a mapped
    instance. The real flag_modified is exercised in the in-process dev proof against a real Planet
    row. Reassigning planet.structures (which the code also does) is what actually marks the column
    dirty on a mapped instance — flag_modified is belt-and-suspenders."""
    monkeypatch.setattr(S, "flag_modified", lambda *a, **k: None)


def _planet(**kw):
    p = types.SimpleNamespace()
    p.structures = kw.get("structures")
    p.last_production = kw.get("last_production")
    p.active_events = kw.get("active_events", {})
    p.under_siege = kw.get("under_siege", False)
    p.siege_started_at = kw.get("siege_started_at")
    p.siege_turns = kw.get("siege_turns", 0)
    p.id = kw.get("id", "test-planet")
    for n in ("size", "citadel_level", "research_level", "factory_level",
              "farm_level", "mine_level", "defense_level", "defense_shields",
              "defense_fighters"):
        setattr(p, n, kw.get(n, 0))
    for n in ("terrain", "temperature", "water_coverage"):
        setattr(p, n, kw.get(n))
    return p


def test_via_settle_guard_trips_loudly_under_strict():
    S.STRICT_VIA_SETTLE = True
    try:
        with pytest.raises(AssertionError):
            S._via_settle_guard("apply_resource_production", False)  # stray direct caller
        S._via_settle_guard("apply_resource_production", True)        # spine call: no raise
    finally:
        S.STRICT_VIA_SETTLE = False


def test_via_settle_guard_warns_but_does_not_raise_by_default():
    S.STRICT_VIA_SETTLE = False
    S._via_settle_guard("apply_resource_production", False)  # WARN-logs a stray; never crashes prod


def test_i4_grep_gate_no_stray_clock_callers():
    """I4 (grep-gate): after the cutover, the clock bodies must have ZERO call-sites outside
    structures.settle() — the sole allowed exception is realize_production's pass-through to
    apply_resource_production."""
    import os
    import re

    src = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "src"))
    bodies = ("apply_resource_production", "advance_siege", "_advance_terraforming",
              "sweep_research_faucet", "realize_production", "process_terraforming_tick")
    allow_lines = {"return self.apply_resource_production(planet, _via_settle=_via_settle)"}
    strays = []
    for root, _dirs, files in os.walk(src):
        for fn in files:
            if not fn.endswith(".py") or fn == "structures.py":
                continue
            path = os.path.join(root, fn)
            with open(path) as f:
                for i, line in enumerate(f, 1):
                    stripped = line.strip()
                    if stripped.startswith(("def ", "#", '"', "'", "*")):
                        continue
                    code = line.split("#", 1)[0]
                    for b in bodies:
                        if re.search(rf"{b}\(", code) and stripped not in allow_lines:
                            strays.append(f"{os.path.relpath(path, src)}:{i}: {stripped}")
    assert not strays, "I4 grep-gate — stray clock-advancing callers outside settle():\n" + "\n".join(strays)


def test_settle_anchor_roundtrip_and_set_under_terraform_meta():
    p = _planet()
    when = datetime(2026, 6, 21, 2, 0, 0, tzinfo=UTC)
    S._set_settle_anchor(p, when)
    assert S._read_settle_anchor(p) == when
    assert p.structures["terraform_meta"]["last_settle_at"] == when.isoformat()


def test_seed_anchor_is_max_of_inner_anchors():
    lp = datetime(2026, 6, 20, 0, 0, 0, tzinfo=UTC)
    tt = datetime(2026, 6, 21, 0, 0, 0, tzinfo=UTC)  # the latest
    p = _planet(last_production=lp,
                active_events={"terraforming": {"last_tick_at": tt.isoformat()}})
    assert S._seed_anchor_value(p) == tt


def test_seed_brand_new_planet_uses_now():
    v = S._seed_anchor_value(_planet())
    assert (datetime.now(UTC) - v).total_seconds() < 5


def test_seed_siege_converts_canonical_turns_to_wall_hours():
    from src.services.planetary_service import SIEGE_TURN_HOURS, SIEGE_TURNS_THRESHOLD
    from src.core.game_time import GAME_TIME_SCALE
    ss = datetime(2026, 6, 20, 0, 0, 0, tzinfo=UTC)
    p = _planet(under_siege=True, siege_started_at=ss,
                siege_turns=SIEGE_TURNS_THRESHOLD + 2, last_production=ss)
    expected = ss + timedelta(hours=(2 * SIEGE_TURN_HOURS) / (GAME_TIME_SCALE or 1.0))
    assert S._seed_anchor_value(p) == expected


def test_seed_is_idempotent():
    p = _planet()
    a1 = S.seed(p)["terraform_meta"]["last_settle_at"]
    a2 = S.seed(p)["terraform_meta"]["last_settle_at"]
    assert a1 == a2  # second seed() never re-stamps


def test_seed_captures_legacy_map_without_touching_derived():
    p = _planet(size=5, citadel_level=2, research_level=3)
    s = S.seed(p)
    assert s["version"] == 1
    assert s["legacy_seed"]["size"] == 5
    assert s["legacy_seed"]["citadel_level"] == 2
    assert p.citadel_level == 2  # derived field untouched


def test_settle_requires_db():
    with pytest.raises(ValueError):
        S.settle(_planet(), datetime.now(UTC), db=None)


def test_settle_gated_returns_noop_without_advancing_anchor(monkeypatch):
    """Spine gate (I3): a stale `now` (<= last_settle_at) is a spine no-op — the anchor does NOT
    advance. DB-free: stub the six steps + the service classes so settle() never touches a real
    body/session, isolating the gate branch."""
    import src.services.planetary_service as PS
    import src.services.terraforming_service as TS

    class _Dummy:
        def __init__(self, db):
            pass

    monkeypatch.setattr(PS, "PlanetaryService", _Dummy)
    monkeypatch.setattr(TS, "TerraformingService", _Dummy)
    for fn in ("_step1_build_queue", "_step2_terraform", "_step3_power_siege",
               "_step4_production", "_step5_research", "_step6_event_roll"):
        monkeypatch.setattr(S, fn, lambda *a, **k: False)

    future = datetime.now(UTC) + timedelta(hours=1)
    p = _planet(structures={"terraform_meta": {"last_settle_at": future.isoformat()}})
    result = S.settle(p, datetime.now(UTC), db=object())  # now < future → GATED
    assert result.changed is False
    assert S._read_settle_anchor(p) == future  # anchor unmoved


def test_settle_advances_anchor_on_forward_now(monkeypatch):
    """Non-gated forward `now` advances the spine anchor to `now` (the single-writer, §1.4)."""
    import src.services.planetary_service as PS
    import src.services.terraforming_service as TS

    class _Dummy:
        def __init__(self, db):
            pass

    monkeypatch.setattr(PS, "PlanetaryService", _Dummy)
    monkeypatch.setattr(TS, "TerraformingService", _Dummy)
    for fn in ("_step1_build_queue", "_step2_terraform", "_step3_power_siege",
               "_step4_production", "_step5_research", "_step6_event_roll"):
        monkeypatch.setattr(S, fn, lambda *a, **k: False)

    past = datetime.now(UTC) - timedelta(hours=1)
    now = datetime.now(UTC)
    p = _planet(structures={"terraform_meta": {"last_settle_at": past.isoformat()}})
    result = S.settle(p, now, db=object())  # now > past → NOT gated
    assert result.changed is True
    assert S._read_settle_anchor(p) == now
