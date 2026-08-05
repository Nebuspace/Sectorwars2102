"""Unit tests for WO-TD-NEXGEN-1 (Central Nexus TradeDock seeding parity).

A Nexus built through the LIVE ``generate_central_nexus`` route had ZERO
TradeDocks (zero writers of ``Station.tradedock_tier`` anywhere in
``nexus_generation_service.py`` before this WO), making the Warp Jumper
unbuildable in any galaxy generated that way even though the bang-import
path already seeds the canon quota (tradedock-shipyard.md
#galaxy-generation-seeding). These tests are DB-free throughout — every
target under test is a pure function or a classmethod/staticmethod that
takes plain Python data, mirroring the DB-free style already established in
tests/unit/test_station_security_seeding.py and
tests/unit/test_nexus_trade_patterns.py.

Four layers:

1. ``TestDeriveTradedockPlacements`` — the pure, DB-free placement rule
   (cluster/connectivity filtering, starter/anchor exclusion, NO-CANON
   fallback ladder).
2. ``TestTradedockStationRow`` / ``TestThreeTradedocksTogether`` — the pure
   row-builder (commodities via apply_class_pattern, security tier via the
   shared WO-STN-SEC-1 helper, tradedock_tier/services/defenses shape).
3. ``TestStarportPrimeSecurityWiring`` — the Nexus CLASS_0 anchor's tier
   wiring at its construction site (_generate_port_for_sector), called
   directly so it's exercised even though this WO's report documents that
   branch as currently dead in the live route (Nexus sector numbering
   starts at 301, never 1).
4. ``TestSeedNexusTradedocksWiring`` / ``TestArchRes2ePathUntouched`` —
   source-position/AST pins (no DB, no line numbers) that
   _seed_nexus_tradedocks is actually wired into generate_central_nexus in
   the right order, and that the WO-ARCH-RES-2E market-price code
   (_create_market_prices_for_nexus_stations) this WO must not touch is
   untouched.
"""
from __future__ import annotations

import ast
import inspect
import random
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Dict, List

import pytest

from src.core.station_security_tiers import _derive_station_security_tier
from src.models.cluster import ClusterType
from src.models.station import SECURITY_TIER_RANK, StationClass, StationStatus, StationType
from src.services.nexus_generation_service import NexusGenerationService

SERVICE = NexusGenerationService

# ---------------------------------------------------------------------------
# Shared fixture: the ratified 20-cluster Central Nexus table (LOCKED in
# tests/unit/test_central_nexus.py::NEXUS_CLUSTER_TABLE), transcribed here in
# type-only form since _derive_tradedock_placements only reads .id/.type.
# Index 0 = "Commerce Central Hub" (TRADE_HUB, starter). Index 9 = "Gateway
# Plaza" (STANDARD, the commented Capital-anchor reservation). Non-starter
# TRADE_HUB (candidate commerce) clusters sit at indices 2, 6, 13.
# ---------------------------------------------------------------------------
_CLUSTER_TYPES = [
    ClusterType.TRADE_HUB,          # 0 starter — excluded
    ClusterType.POPULATION_CENTER,  # 1
    ClusterType.TRADE_HUB,          # 2 candidate
    ClusterType.RESOURCE_RICH,      # 3
    ClusterType.FRONTIER_OUTPOST,   # 4
    ClusterType.FRONTIER_OUTPOST,   # 5
    ClusterType.TRADE_HUB,          # 6 candidate
    ClusterType.RESOURCE_RICH,      # 7
    ClusterType.STANDARD,           # 8
    ClusterType.STANDARD,           # 9 Gateway Plaza — Capital-anchor reserved
    ClusterType.POPULATION_CENTER,  # 10
    ClusterType.STANDARD,           # 11
    ClusterType.RESOURCE_RICH,      # 12
    ClusterType.TRADE_HUB,          # 13 candidate
    ClusterType.FRONTIER_OUTPOST,   # 14
    ClusterType.STANDARD,           # 15
    ClusterType.FRONTIER_OUTPOST,   # 16
    ClusterType.STANDARD,           # 17
    ClusterType.STANDARD,           # 18
    ClusterType.FRONTIER_OUTPOST,   # 19
]


def _make_clusters() -> List[SimpleNamespace]:
    return [SimpleNamespace(id=uuid.uuid4(), type=t) for t in _CLUSTER_TYPES]


def _sectors_for_cluster(cluster_index: int, base: int, count: int = 5) -> List[int]:
    return [base + i for i in range(1, count + 1)]


def _happy_path_fixture():
    """3 candidate TRADE_HUB clusters (2, 6, 13), each with 5 sectors and a
    mix of inbound-warp counts straddling the >=2 canon floor, no sector
    pre-occupied. Qualifying (>=2 inbound) sectors: 22, 24, 61, 64, 133, 135.
    """
    clusters = _make_clusters()
    cluster_id_by_sector: Dict[int, uuid.UUID] = {}
    inbound_warp_count: Dict[int, int] = {}

    layout = {
        2: {21: 1, 22: 3, 23: 0, 24: 2, 25: 1},
        6: {61: 2, 62: 0, 63: 1, 64: 3, 65: 0},
        13: {131: 0, 132: 1, 133: 2, 134: 0, 135: 5},
    }
    for cluster_index, sector_counts in layout.items():
        for sector_id, inbound in sector_counts.items():
            cluster_id_by_sector[sector_id] = clusters[cluster_index].id
            inbound_warp_count[sector_id] = inbound

    # A handful of sectors in non-candidate clusters too, so the pool isn't
    # trivially "every sector in the fixture qualifies".
    for cluster_index in (1, 3, 8, 11):
        for offset, sector_id in enumerate(_sectors_for_cluster(cluster_index, cluster_index * 100)):
            cluster_id_by_sector[sector_id] = clusters[cluster_index].id
            inbound_warp_count[sector_id] = offset  # 0..4, mostly <2

    return clusters, cluster_id_by_sector, inbound_warp_count


QUALIFYING_HAPPY_PATH_SECTORS = {22, 24, 61, 64, 133, 135}


class TestDeriveTradedockPlacements:
    """The pure placement rule — tests/unit style mirrors
    test_station_security_seeding.py's pure-function-first layering."""

    def test_happy_path_returns_three_placements_no_warnings(self):
        clusters, cluster_id_by_sector, inbound = _happy_path_fixture()
        placements, warnings = SERVICE._derive_tradedock_placements(
            clusters, cluster_id_by_sector, inbound, set(), ("A", "B", "B"),
            random.Random(42),
        )
        assert len(placements) == 3
        assert warnings == []

    def test_happy_path_only_ever_picks_qualifying_connectivity_sectors(self):
        """Repeat across many RNG seeds: every chosen sector must be one of
        the six sectors with >=2 inbound warps in a non-starter TRADE_HUB
        cluster — proves the <2-inbound-warp sectors are truly excluded,
        not just usually avoided."""
        clusters, cluster_id_by_sector, inbound = _happy_path_fixture()
        for seed in range(200):
            placements, warnings = SERVICE._derive_tradedock_placements(
                clusters, cluster_id_by_sector, inbound, set(), ("A", "B", "B"),
                random.Random(seed),
            )
            assert warnings == []
            chosen = {p["sector_id"] for p in placements}
            assert chosen <= QUALIFYING_HAPPY_PATH_SECTORS
            assert len(chosen) == 3  # no duplicate sector across the 3 docks

    def test_exactly_one_tier_a_and_two_tier_b_in_order(self):
        clusters, cluster_id_by_sector, inbound = _happy_path_fixture()
        placements, _ = SERVICE._derive_tradedock_placements(
            clusters, cluster_id_by_sector, inbound, set(), ("A", "B", "B"),
            random.Random(7),
        )
        tiers = [p["tier"] for p in placements]
        assert tiers == ["A", "B", "B"]

    def test_never_places_in_starter_cluster(self):
        clusters, cluster_id_by_sector, inbound = _happy_path_fixture()
        starter_id = clusters[0].id
        for seed in range(50):
            placements, _ = SERVICE._derive_tradedock_placements(
                clusters, cluster_id_by_sector, inbound, set(), ("A", "B", "B"),
                random.Random(seed),
            )
            for p in placements:
                assert cluster_id_by_sector[p["sector_id"]] != starter_id

    def test_never_places_in_gateway_plaza_capital_anchor_cluster(self):
        """Index 9 ("Gateway Plaza") is STANDARD-typed, so the primary
        TRADE_HUB filter would already exclude it — this test forces the
        point by giving it high-connectivity sectors that WOULD otherwise
        look attractive, and confirming they're still never chosen."""
        clusters, cluster_id_by_sector, inbound = _happy_path_fixture()
        gateway_plaza_id = clusters[9].id
        for offset, sector_id in enumerate((901, 902, 903)):
            cluster_id_by_sector[sector_id] = gateway_plaza_id
            inbound[sector_id] = 10  # maximally attractive connectivity

        for seed in range(50):
            placements, _ = SERVICE._derive_tradedock_placements(
                clusters, cluster_id_by_sector, inbound, set(), ("A", "B", "B"),
                random.Random(seed),
            )
            for p in placements:
                assert cluster_id_by_sector[p["sector_id"]] != gateway_plaza_id

    def test_occupied_sectors_are_never_reused(self):
        clusters, cluster_id_by_sector, inbound = _happy_path_fixture()
        occupied = {22, 24, 61}  # pre-occupy 3 of the 6 qualifying sectors
        for seed in range(50):
            placements, _ = SERVICE._derive_tradedock_placements(
                clusters, cluster_id_by_sector, inbound, occupied, ("A", "B", "B"),
                random.Random(seed),
            )
            chosen = {p["sector_id"] for p in placements}
            assert chosen.isdisjoint(occupied)
            assert chosen <= {64, 133, 135}

    def test_fallback_used_when_no_qualifying_commerce_cluster_has_connectivity(self):
        """Construct a Nexus fixture with no qualifying cluster (WO Lane 2
        requirement): every non-starter TRADE_HUB sector has <2 inbound
        warps, forcing the NO-CANON fallback to the best-connectivity
        non-starter/non-FRONTIER cluster."""
        clusters, cluster_id_by_sector, inbound = _happy_path_fixture()
        # Starve every candidate commerce sector below the connectivity floor.
        for sector_id in QUALIFYING_HAPPY_PATH_SECTORS:
            inbound[sector_id] = 1
        # Give one STANDARD (non-FRONTIER, non-starter, non-anchor) cluster's
        # sector strong connectivity to be the "best" fallback pick.
        best_fallback_sector = 1101
        cluster_id_by_sector[best_fallback_sector] = clusters[11].id
        inbound[best_fallback_sector] = 9

        placements, warnings = SERVICE._derive_tradedock_placements(
            clusters, cluster_id_by_sector, inbound, set(), ("A", "B", "B"),
            random.Random(3),
        )
        assert len(placements) == 3
        assert any("[NO-CANON]" in w for w in warnings)
        starter_id = clusters[0].id
        gateway_plaza_id = clusters[9].id
        for p in placements:
            cid = cluster_id_by_sector[p["sector_id"]]
            assert cid not in (starter_id, gateway_plaza_id)
            assert p["cluster_type"] != ClusterType.FRONTIER_OUTPOST
        # The best-connectivity sector should have been the first one taken.
        assert best_fallback_sector in {p["sector_id"] for p in placements}

    def test_last_resort_will_even_use_frontier_before_shipping_zero(self):
        """Extreme fixture: the ONLY non-starter cluster is FRONTIER_OUTPOST
        (no TRADE_HUB candidate, no benign fallback cluster at all). The
        broadened fallback (which excludes FRONTIER_OUTPOST) is empty too,
        so the function must fall through to the absolute last resort
        rather than silently returning fewer than 3 placements."""
        clusters = [
            SimpleNamespace(id=uuid.uuid4(), type=ClusterType.TRADE_HUB),  # 0 starter
            SimpleNamespace(id=uuid.uuid4(), type=ClusterType.FRONTIER_OUTPOST),  # 1 only option
        ]
        cluster_id_by_sector = {}
        inbound = {}
        for sector_id in range(201, 206):
            cluster_id_by_sector[sector_id] = clusters[1].id
            inbound[sector_id] = 0

        placements, warnings = SERVICE._derive_tradedock_placements(
            clusters, cluster_id_by_sector, inbound, set(), ("A", "B", "B"),
            random.Random(11),
        )
        assert len(placements) == 3
        assert any("exhausted the broadened fallback pool" in w for w in warnings)
        for p in placements:
            assert cluster_id_by_sector[p["sector_id"]] == clusters[1].id

    def test_gracefully_degrades_when_truly_no_sector_is_free(self):
        """If even the last resort has nothing free, the function must
        return fewer than 3 placements with an explicit warning per missing
        tier -- never raise, never silently invent a sector."""
        clusters = [
            SimpleNamespace(id=uuid.uuid4(), type=ClusterType.TRADE_HUB),  # 0 starter
            SimpleNamespace(id=uuid.uuid4(), type=ClusterType.STANDARD),   # 1
        ]
        cluster_id_by_sector = {301: clusters[1].id}
        inbound = {301: 0}
        occupied = {301}  # the one non-starter sector is already taken

        placements, warnings = SERVICE._derive_tradedock_placements(
            clusters, cluster_id_by_sector, inbound, occupied, ("A", "B", "B"),
            random.Random(5),
        )
        assert len(placements) == 0
        assert len(warnings) == 3
        assert all("no free sector left" in w for w in warnings)

    def test_deterministic_given_same_rng_seed(self):
        clusters, cluster_id_by_sector, inbound = _happy_path_fixture()
        r1, w1 = SERVICE._derive_tradedock_placements(
            clusters, cluster_id_by_sector, inbound, set(), ("A", "B", "B"), random.Random(99)
        )
        r2, w2 = SERVICE._derive_tradedock_placements(
            clusters, cluster_id_by_sector, inbound, set(), ("A", "B", "B"), random.Random(99)
        )
        assert r1 == r2
        assert w1 == w2


class TestTradedockStationRow:
    """The pure row-builder — no DB, no session."""

    def _row(self, tier: str, cluster_type=ClusterType.TRADE_HUB):
        return SERVICE._build_tradedock_station_row(
            sector_id=4242,
            tier=tier,
            name=f"Test TradeDock {tier}",
            cluster_type=cluster_type,
            region_id=str(uuid.uuid4()),
            rng=random.Random(123),
        )

    def test_station_class_and_type(self):
        row = self._row("A")
        assert row["station_class"] == StationClass.CLASS_11
        assert row["type"] == StationType.SHIPYARD
        assert row["status"] == StationStatus.OPERATIONAL
        assert row["is_spacedock"] is False

    def test_tradedock_tier_field(self):
        assert self._row("A")["tradedock_tier"] == "A"
        assert self._row("B")["tradedock_tier"] == "B"

    def test_market_book_non_empty_for_exotic_tech_and_luxury_goods(self):
        """WO Lane 2: 'each new TradeDock has a non-empty market book' — the
        Station.commodities JSONB (the bang-shape 'book') must carry
        buys=sells=True with positive quantity for CLASS_11's two
        commodities, not the fully-inert (buys=sells=False, quantity=0)
        baseline."""
        row = self._row("A")
        commodities = row["commodities"]
        assert set(commodities.keys()) == {
            "ore", "organics", "equipment", "fuel", "luxury_goods",
            "gourmet_food", "exotic_technology", "colonists", "precious_metals",
        }
        for key in ("exotic_technology", "luxury_goods"):
            assert commodities[key]["buys"] is True
            assert commodities[key]["sells"] is True
            assert commodities[key]["quantity"] > 0
        # Everything else stays untraded for a Class-11 station.
        for key in ("ore", "organics", "equipment", "fuel", "gourmet_food", "colonists", "precious_metals"):
            assert commodities[key]["buys"] is False
            assert commodities[key]["sells"] is False

    def test_defenses_present_and_class_scaled(self):
        row = self._row("A")
        assert row["defenses"]["defense_drones"] > 0

    def test_luxury_amenities_only_on_tier_a(self):
        assert self._row("A")["services"]["luxury_amenities"] is True
        assert self._row("B")["services"]["luxury_amenities"] is False

    def test_tier_a_security_tier_is_standard(self):
        """Tier-A is one of canon's three literal Standard/Premium anchors
        ('Terran Space hub stations') per _derive_station_security_tier."""
        assert self._row("A")["security"]["tier"] == "standard"

    def test_tier_b_security_tier_is_standard_same_as_tier_a(self):
        """WO-TD-NEXGEN-1 REVISE ruling: canon is silent on TradeDock tiers
        under either reading of station-protection.md:28-33's "Terran Space
        hub stations", so the WO's own stated acceptance criterion ("the 3
        TradeDocks read standard") governs. Both Tier-A and Tier-B seed
        'standard' — Tier-B TradeDocks hold expensive shipyard construction
        reservations and shouldn't sit behind weaker protection than their
        Tier-A sibling."""
        assert self._row("B")["security"]["tier"] == "standard"

    def test_tier_b_security_tier_unconditional_even_in_a_lawless_cluster(self):
        """WO-TD-NEXGEN-1 REVISE ruling: unlike the shared
        _derive_station_security_tier helper (which downgrades a
        non-anchor station to 'none' in a lawless cluster), this
        generator's TradeDock row-builder does not consult cluster_type for
        security tier at all — matching how Tier-A was already unconditional
        (the helper returns 'standard' for tradedock_tier=='A' before it
        ever reaches its own lawless-cluster check). Unreachable via live
        placement today (FRONTIER_OUTPOST/CONTESTED clusters are excluded
        at placement time) but pins the row-builder's real, direct
        behavior."""
        row = self._row("B", cluster_type=ClusterType.FRONTIER_OUTPOST)
        assert row["security"]["tier"] == "standard"

    def test_security_tier_is_always_a_valid_rank_key(self):
        for tier in ("A", "B"):
            assert self._row(tier)["security"]["tier"] in SECURITY_TIER_RANK

    def test_description_mentions_tier(self):
        assert "Tier-A" in self._row("A")["description"]
        assert "Tier-B" in self._row("B")["description"]


class TestThreeTradedocksTogether:
    """End-to-end (still DB-free): compose _derive_tradedock_placements +
    _build_tradedock_station_row exactly as _seed_nexus_tradedocks does, and
    assert the WO's own post-generation invariants on the resulting rows."""

    def _build_rows(self, seed: int = 42):
        clusters, cluster_id_by_sector, inbound = _happy_path_fixture()
        placements, warnings = SERVICE._derive_tradedock_placements(
            clusters, cluster_id_by_sector, inbound, set(), ("A", "B", "B"),
            random.Random(seed),
        )
        rows = [
            SERVICE._build_tradedock_station_row(
                sector_id=p["sector_id"],
                tier=p["tier"],
                name=f"Fixture TradeDock {i}",
                cluster_type=p["cluster_type"],
                region_id="fixture-region",
                rng=random.Random(f"fixture:{p['sector_id']}"),
            )
            for i, p in enumerate(placements)
        ]
        return rows, warnings, clusters, cluster_id_by_sector

    def test_exactly_three_stations_with_tradedock_tier_not_null(self):
        rows, _, _, _ = self._build_rows()
        assert len(rows) == 3
        assert all(r["tradedock_tier"] is not None for r in rows)

    def test_exactly_one_tier_a(self):
        rows, _, _, _ = self._build_rows()
        assert sum(1 for r in rows if r["tradedock_tier"] == "A") == 1
        assert sum(1 for r in rows if r["tradedock_tier"] == "B") == 2

    def test_no_seeded_tradedock_in_starter_or_capital_anchor_cluster(self):
        rows, _, clusters, cluster_id_by_sector = self._build_rows()
        starter_id = clusters[0].id
        gateway_plaza_id = clusters[9].id
        for r in rows:
            cid = cluster_id_by_sector[r["sector_id"]]
            assert cid not in (starter_id, gateway_plaza_id)

    def test_every_row_has_a_non_empty_market_book(self):
        rows, _, _, _ = self._build_rows()
        for r in rows:
            assert any(c["buys"] or c["sells"] for c in r["commodities"].values())


class TestStarportPrimeSecurityWiring:
    """The Nexus CLASS_0 anchor's security-tier wiring at its construction
    site. Called directly (bypassing generate_central_nexus's sector-1
    special-case entirely) because — per this WO's report — that branch is
    currently DEAD in the live route: Central Nexus sector numbering starts
    at 301 (generate_central_nexus's current_sector_num), so
    _generate_port_for_sector(sector_num=1, ...) is never actually reached
    by the live generate-route call chain today. This test still proves the
    WIRING ITSELF is correct so it's ready the moment that separate,
    pre-existing numbering gap is fixed."""

    def test_starport_prime_row_carries_premium_tier(self):
        service = NexusGenerationService()
        row = service._generate_port_for_sector(1, str(uuid.uuid4()))
        assert row["is_starport_prime"] is True
        assert row["security"]["tier"] == "premium"

    def test_matches_the_shared_helper_directly(self):
        assert (
            _derive_station_security_tier(
                region_type="central_nexus",
                cluster_type=None,
                station_class=StationClass.CLASS_0,
                is_spacedock=False,
                tradedock_tier=None,
            )
            == "premium"
        )

    def test_ordinary_port_rows_are_unaffected(self):
        """Scope guard: this WO deliberately does NOT wire security tiers
        for ordinary (non-anchor, non-TradeDock) Nexus ports — see the WO
        report's Concerns. An ordinary port row must NOT carry a 'security'
        key."""
        service = NexusGenerationService()
        row = service._generate_port_for_sector(2, str(uuid.uuid4()))
        assert "security" not in row


# ---------------------------------------------------------------------------
# Wiring / regression pins — source position and AST, no DB, no line numbers.
# ---------------------------------------------------------------------------

_SERVICE_PATH = Path(inspect.getfile(NexusGenerationService))
_SERVICE_SOURCE = _SERVICE_PATH.read_text()


class TestSeedNexusTradedocksWiring:
    def test_generate_central_nexus_calls_seed_after_warps_before_market_prices(self):
        source = inspect.getsource(NexusGenerationService.generate_central_nexus)
        warp_idx = source.index("self._generate_warp_tunnels(")
        seed_idx = source.index("self._seed_nexus_tradedocks(")
        market_idx = source.index("self._create_market_prices_for_nexus_stations(")
        assert warp_idx < seed_idx < market_idx, (
            "TradeDock seeding must run after warp tunnels exist (placement "
            "needs inbound-warp counts) and before the market-price sweep "
            "(so the new stations are priced automatically)"
        )

    def test_seed_nexus_tradedocks_delegates_to_the_pure_helpers(self):
        source = inspect.getsource(NexusGenerationService._seed_nexus_tradedocks)
        assert "self._derive_tradedock_placements(" in source
        assert "self._build_tradedock_station_row(" in source
        assert "insert(Station)" in source

    def test_generation_stats_surface_tradedock_count_and_warnings(self):
        source = inspect.getsource(NexusGenerationService.generate_central_nexus)
        assert 'generation_stats["tradedocks_created"]' in source
        assert 'generation_stats["tradedock_placement_warnings"]' in source


class TestArchRes2ePathUntouched:
    """Complementary to the exhaustive AST pins already in
    tests/unit/test_nexus_trade_patterns.py (which this WO's diff does not
    modify and which must still pass unchanged) — this class scopes down to
    exactly what THIS diff could plausibly have disturbed: that
    _create_market_prices_for_nexus_stations itself is untouched, regardless
    of line-number drift from the new methods inserted above it."""

    def _method_source(self) -> str:
        return inspect.getsource(NexusGenerationService._create_market_prices_for_nexus_stations)

    def test_spread_multipliers_unchanged(self):
        source = self._method_source()
        for literal in ("0.85", "1.15", "1.1", "1.5", "0.5", "0.9"):
            assert literal in source

    def test_still_reads_class_pattern_from_the_declared_sot(self):
        source = self._method_source()
        assert "get_class_pattern(station.station_class)" in source
        assert "from src.core.station_class_map import get_class_pattern" in source

    def test_base_commodities_key_set_unchanged_no_precious_metals(self):
        tree = ast.parse(_SERVICE_SOURCE, filename=str(_SERVICE_PATH))
        matches = [
            node for node in ast.walk(tree)
            if isinstance(node, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == "base_commodities" for t in node.targets)
        ]
        assert len(matches) == 1
        keys = {k.value for k in matches[0].value.keys}
        assert keys == {
            "ore", "organics", "equipment", "fuel", "luxury_goods",
            "gourmet_food", "exotic_technology", "colonists",
        }
        assert "precious_metals" not in keys
