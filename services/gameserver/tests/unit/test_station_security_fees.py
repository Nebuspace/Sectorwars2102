"""Unit tests for the canon station-security docking-fee matrix.

Canon: FEATURES/economy/station-protection.md §Docking fee economics,
lines 117-136 -- a base fee by ship SIZE (Tiny/Small/Medium/Large/Capital)
multiplied by the station's security TIER (none/basic/standard/premium).
DB-free: docking_fee_for/ship_size_for/_realize_fee are pure or take a
lightweight stand-in in place of a real Session.
"""
from types import SimpleNamespace

import pytest

from src.models.ship import ShipSize
from src.services import docking_service


def make_station(security_level="basic", price_modifiers=None, treasury_balance=0):
    """Lightweight stand-in carrying the attributes docking_fee_for /
    _realize_fee read. security_level mirrors the real Station.security_level
    computed property's output (a lowercased tier string)."""
    return SimpleNamespace(
        id="station-1",
        security_level=security_level,
        price_modifiers=price_modifiers or {},
        treasury_balance=treasury_balance,
    )


# The full canon matrix, station-protection.md:117-136.
CANON_MATRIX = {
    (ShipSize.TINY, "none"): 0,
    (ShipSize.TINY, "basic"): 0,
    (ShipSize.TINY, "standard"): 0,
    (ShipSize.TINY, "premium"): 0,
    (ShipSize.SMALL, "none"): 0,
    (ShipSize.SMALL, "basic"): 100,
    (ShipSize.SMALL, "standard"): 150,
    (ShipSize.SMALL, "premium"): 300,
    (ShipSize.MEDIUM, "none"): 0,
    (ShipSize.MEDIUM, "basic"): 250,
    (ShipSize.MEDIUM, "standard"): 375,
    (ShipSize.MEDIUM, "premium"): 750,
    (ShipSize.LARGE, "none"): 0,
    (ShipSize.LARGE, "basic"): 500,
    (ShipSize.LARGE, "standard"): 750,
    (ShipSize.LARGE, "premium"): 1500,
    (ShipSize.CAPITAL, "none"): 0,
    (ShipSize.CAPITAL, "basic"): 1000,
    (ShipSize.CAPITAL, "standard"): 1500,
    (ShipSize.CAPITAL, "premium"): 3000,
}


class TestCanonMatrix:
    """All 20 size x tier cells, canon numbers exact."""

    @pytest.mark.parametrize("size,tier,expected", [
        (size, tier, fee) for (size, tier), fee in CANON_MATRIX.items()
    ])
    def test_matrix_cell(self, size, tier, expected):
        station = make_station(security_level=tier)
        assert docking_service.docking_fee_for(station, size) == expected

    def test_worked_example_cargo_hauler_large_premium(self):
        """station-protection.md:136 worked example: a Cargo Hauler (Large)
        docking at a Premium-tier Capital station pays 1,500cr."""
        station = make_station(security_level="premium")
        assert docking_service.docking_fee_for(station, ShipSize.LARGE) == 1500


class TestEscapePodAlwaysFree:
    @pytest.mark.parametrize("tier", ["none", "basic", "standard", "premium"])
    def test_tiny_is_free_at_every_tier(self, tier):
        station = make_station(security_level=tier)
        assert docking_service.docking_fee_for(station, ShipSize.TINY) == 0


class TestNoneTierIsFreeAtEverySize:
    """0x multiplier applies uniformly -- a "none"-tier (frontier/undefended)
    station is a fee-free dock regardless of ship size. This is a deliberate
    LIVE-ECONOMY change from the old flat per-class fee table (every station
    used to charge >=25cr regardless of security posture); flagged for the
    Max digest as the docking-fee faucet shrinking at undefended stations."""

    @pytest.mark.parametrize("size", [ShipSize.SMALL, ShipSize.MEDIUM, ShipSize.LARGE, ShipSize.CAPITAL])
    def test_free_docking_at_none_tier_station(self, size):
        station = make_station(security_level="none")
        assert docking_service.docking_fee_for(station, size) == 0


class TestUnknownSizeFallback:
    """NO-CANON: an unresolved/NULL ship size (NPC-only Interdictor hulls,
    or a spec-lookup miss) falls back to Medium."""

    def test_none_ship_size_falls_back_to_medium(self):
        station = make_station(security_level="premium")
        assert docking_service.docking_fee_for(station, None) == 750  # Medium(250) x 3.0

    def test_omitted_ship_size_arg_falls_back_to_medium(self):
        station = make_station(security_level="basic")
        assert docking_service.docking_fee_for(station) == 250  # Medium(250) x 1.0

    def test_unrecognized_size_value_falls_back_to_medium(self):
        """Defensive: a value outside the ShipSize enum (should never happen
        given the type hint, but the .get() default guards it anyway)."""
        station = make_station(security_level="standard")
        assert docking_service.docking_fee_for(station, "not-a-real-size") == 375  # Medium(250) x 1.5


class TestOwnerOverride:
    """_owner_docking_fee_override (~:314) + its canon 50-500cr clamp are
    unchanged; this pins that the override still short-circuits the whole
    size/tier matrix lookup, exactly as it short-circuited the old flat
    class table."""

    def test_override_wins_regardless_of_size_and_tier(self):
        station = make_station(
            security_level="premium",
            price_modifiers={"docking_fee_enabled": True, "docking_fee": 300},
        )
        assert docking_service.docking_fee_for(station, ShipSize.CAPITAL) == 300

    def test_override_wins_at_none_tier_too(self):
        """The override replaces the whole matrix lookup, not just the tier
        factor -- so it still charges even at a "none"-tier station where
        the matrix itself would otherwise be free."""
        station = make_station(
            security_level="none",
            price_modifiers={"docking_fee_enabled": True, "docking_fee": 200},
        )
        assert docking_service.docking_fee_for(station, ShipSize.SMALL) == 200

    def test_override_wins_over_escape_pod_too(self):
        """Same precedence rule applied to the Tiny/Escape-Pod cell: an
        active override is checked first, unconditionally, exactly as in the
        pre-existing code -- so it also overrides what would otherwise be a
        free Escape-Pod dock."""
        station = make_station(
            security_level="basic",
            price_modifiers={"docking_fee_enabled": True, "docking_fee": 75},
        )
        assert docking_service.docking_fee_for(station, ShipSize.TINY) == 75

    def test_override_disabled_falls_through_to_matrix(self):
        station = make_station(
            security_level="standard",
            price_modifiers={"docking_fee_enabled": False, "docking_fee": 999},
        )
        assert docking_service.docking_fee_for(station, ShipSize.SMALL) == 150

    def test_override_unset_falls_through_to_matrix(self):
        station = make_station(security_level="standard", price_modifiers={})
        assert docking_service.docking_fee_for(station, ShipSize.SMALL) == 150

    def test_override_clamp_ceiling_regression(self):
        station = make_station(
            security_level="premium",
            price_modifiers={"docking_fee_enabled": True, "docking_fee": 5000},
        )
        assert docking_service.docking_fee_for(station, ShipSize.CAPITAL) == 500

    def test_override_clamp_floor_regression(self):
        station = make_station(
            security_level="premium",
            price_modifiers={"docking_fee_enabled": True, "docking_fee": 5},
        )
        assert docking_service.docking_fee_for(station, ShipSize.TINY) == 50


class _FakeSpecQuery:
    def __init__(self, spec):
        self._spec = spec

    def filter(self, *args, **kwargs):
        return self

    def first(self):
        return self._spec


class _FakeSpecDB:
    """Scripted stand-in for db.query(ShipSpecification).filter(...).first()
    -- the only query ship_size_for() issues."""

    def __init__(self, spec):
        self._spec = spec

    def query(self, model):
        return _FakeSpecQuery(self._spec)


class TestShipSizeFor:
    def test_none_ship_returns_none(self):
        assert docking_service.ship_size_for(_FakeSpecDB(None), None) is None

    def test_missing_spec_returns_none(self):
        ship = SimpleNamespace(type="CARGO_HAULER")
        assert docking_service.ship_size_for(_FakeSpecDB(None), ship) is None

    def test_null_size_spec_returns_none(self):
        """NPC-only Interdictor hulls: a matched spec row with ship_size=None."""
        ship = SimpleNamespace(type="NPC_MARSHAL_INTERDICTOR")
        spec = SimpleNamespace(ship_size=None)
        assert docking_service.ship_size_for(_FakeSpecDB(spec), ship) is None

    def test_resolves_matched_spec_size(self):
        ship = SimpleNamespace(type="CARGO_HAULER")
        spec = SimpleNamespace(ship_size=ShipSize.LARGE)
        assert docking_service.ship_size_for(_FakeSpecDB(spec), ship) == ShipSize.LARGE


class TestQuoteChargeAgreement:
    """The /dock charge, the /slips quote, and bump()'s internal charge all
    resolve size via the same two functions (ship_size_for then
    docking_fee_for) in the same order. This pins that, for the same
    station+ship, three independent call sites can never diverge -- they are
    structurally the same computation, not three copies that could drift."""

    def test_same_station_and_ship_yield_identical_fee_across_sites(self):
        spec = SimpleNamespace(ship_size=ShipSize.LARGE)
        fake_db = _FakeSpecDB(spec)
        ship = SimpleNamespace(type="CARGO_HAULER")
        station = make_station(security_level="premium")

        # Site A: dock route (trading.py docking_ship_size + docking_fee)
        dock_charge = docking_service.docking_fee_for(
            station, docking_service.ship_size_for(fake_db, ship)
        )
        # Site B: /slips quote route (trading.py quote_ship_size + fee)
        slips_quote = docking_service.docking_fee_for(
            station, docking_service.ship_size_for(fake_db, ship)
        )
        # Site C: bump() internal charge (docking_service.py bump_ship_size + fee)
        bump_charge = docking_service.docking_fee_for(
            station, docking_service.ship_size_for(fake_db, ship)
        )

        assert dock_charge == slips_quote == bump_charge == 1500


class TestRealizeFeeFallbackRegression:
    """_realize_fee (~:68) is untouched by this change; this re-pins its OWN
    observable contract (delegate to realize_port_revenue, or fall back to a
    100%-to-treasury credit if that hook is unavailable/raises) now that the
    surrounding fee-computation code changed. It does NOT re-verify
    port_ownership_service's actual 40/30/30 percentage math -- that
    function lives in a different lane and is out of this WO's scope."""

    def test_falls_back_to_full_treasury_credit_when_realize_hook_unavailable(self):
        # A db stand-in with no .query attribute forces realize_port_revenue's
        # _lock_station() to raise AttributeError, tripping the except-fallback.
        fake_db = object()
        station = make_station(security_level="premium", treasury_balance=1000)
        docking_service._realize_fee(fake_db, station, 1500)
        assert station.treasury_balance == 2500
