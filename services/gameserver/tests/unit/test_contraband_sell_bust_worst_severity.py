"""WO-FIX-CONTRABAND-BUST-SEVERITY-KEYS-SOLD-LINE-NOT-WORST-HELD.

A black-market bust confiscates the WHOLE illegal hold (``_resolve_bust``
sweeps every ``illegal:*`` key), but before this fix the fine multiplier /
heat flip / rep deltas keyed off ``meta`` for the SOLD line only. A player
selling a LIGHT item (e.g. STOLEN_GOODS) while also carrying SEVERE goods
(e.g. WEAPONS) was fined and heat-flipped as if only the LIGHT item had been
seized, even though the SEVERE item was confiscated too — an
under-penalization relative to what actually happened.

The transit-scan site (``scan_in_transit``) already got this right via
``_worst_held_meta`` (see ``test_contraband_transit_scan.py``); this fix
makes ``sell()`` use the same worst-held selection for severity, while still
reporting the SOLD commodity in the response (what the player was doing).

No real DB: ``sell()``'s DB-backed gates (``_lock_station_player_ship``,
``_is_black_market_venue``, ``_passes_rep_gate``, ``_resolve_sector``) are
monkeypatched to bypass the DB entirely, and ``_resolve_bust`` is stubbed to
capture the ``meta``/``commodity`` it was called with rather than executing
its own DB-backed side effects (heat flip, rep deltas, ledger row) — this
test is scoped to the severity-selection logic in ``sell()``, not
``_resolve_bust`` itself.
"""
from types import SimpleNamespace
from uuid import uuid4

from src.core.illegal_commodities import IllegalCommodity, IllegalSeverity, get_meta
from src.services.contraband_service import ContrabandService
import src.services.contraband_service as contraband_service_module


class _AlwaysDetectRNG:
    @staticmethod
    def random():
        return 0.0  # below any positive p_detect -> always "detected"

    @staticmethod
    def uniform(a, b):
        return 0.0  # neutral haggle roll -- irrelevant to this test's assertions


def _make_ship(player_id, contents):
    return SimpleNamespace(
        id=uuid4(),
        owner_id=player_id,
        cargo={"used": 0, "capacity": 50, "contents": dict(contents)},
    )


def _svc_with_bypassed_gates(monkeypatch, station, player, ship):
    svc = ContrabandService(None)
    monkeypatch.setattr(svc, "_lock_station_player_ship", lambda *a, **k: (station, player, ship, None))
    monkeypatch.setattr(svc, "_is_black_market_venue", lambda s: True)
    monkeypatch.setattr(svc, "_passes_rep_gate", lambda pid: True)
    monkeypatch.setattr(svc, "_resolve_sector", lambda p: None)
    monkeypatch.setattr(contraband_service_module, "_RNG", _AlwaysDetectRNG())
    return svc


def test_selling_light_item_while_holding_severe_item_busts_at_severe(monkeypatch):
    """The discriminating case: sell STOLEN_GOODS (LIGHT) while also carrying
    WEAPONS (SEVERE). The bust must key severity off WEAPONS, not STOLEN_GOODS."""
    player_id = uuid4()
    station_id = uuid4()
    player = SimpleNamespace(
        id=player_id,
        credits=100_000,
        personal_reputation=0,
        is_docked=True,
        current_sector_id=1,
    )
    station = SimpleNamespace(id=station_id, sector_id=1)
    contents = {
        "illegal:STOLEN_GOODS": 5,
        "illegal:WEAPONS": 3,
    }
    ship = _make_ship(player_id, contents)
    svc = _svc_with_bypassed_gates(monkeypatch, station, player, ship)

    captured = {}

    def _fake_resolve_bust(**kwargs):
        captured.update(kwargs)
        return {"success": True, "detected": True, "reason": "detected"}

    monkeypatch.setattr(svc, "_resolve_bust", _fake_resolve_bust)

    result = svc.sell(player, ship, station, IllegalCommodity.STOLEN_GOODS, 5)

    assert result["detected"] is True
    assert captured, "sell() did not reach _resolve_bust"
    assert captured["meta"].severity == IllegalSeverity.SEVERE, (
        f"bust severity was {captured['meta'].severity}, expected SEVERE "
        "(WEAPONS) -- fining/heat-flipping on the sold LIGHT line under-"
        "penalizes a player who was also carrying SEVERE contraband"
    )
    # The reported commodity stays what was actually sold, not the worst held.
    assert captured["commodity"] == IllegalCommodity.STOLEN_GOODS


def test_selling_the_worst_item_itself_still_busts_at_its_own_severity(monkeypatch):
    """Sanity: when the sold line IS the worst-held line, behaviour is unchanged."""
    player_id = uuid4()
    station_id = uuid4()
    player = SimpleNamespace(
        id=player_id,
        credits=100_000,
        personal_reputation=0,
        is_docked=True,
        current_sector_id=1,
    )
    station = SimpleNamespace(id=station_id, sector_id=1)
    contents = {"illegal:WEAPONS": 3}
    ship = _make_ship(player_id, contents)
    svc = _svc_with_bypassed_gates(monkeypatch, station, player, ship)

    captured = {}

    def _fake_resolve_bust(**kwargs):
        captured.update(kwargs)
        return {"success": True, "detected": True, "reason": "detected"}

    monkeypatch.setattr(svc, "_resolve_bust", _fake_resolve_bust)

    svc.sell(player, ship, station, IllegalCommodity.WEAPONS, 3)

    assert captured["meta"].severity == IllegalSeverity.SEVERE
    assert captured["meta"] == get_meta(IllegalCommodity.WEAPONS)
