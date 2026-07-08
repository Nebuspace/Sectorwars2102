"""WO-TD-RGF-1 — region-funded TradeDock construction.

construction_service.create_region_funded_construction had zero callers
before this WO; it is now wired to POST /regions/my-region/tradedock-
construction (regional_governance.py). Covers, DB-free, via a hand-built
_FakeQuery/_FakeSession (mirrors test_gate_construction_staging.py's pattern):

  * the service directly — ownership (both the player-id-mismatch and the
    player.user_id-match paths), the >= 500-sector gate, the >= 50M treasury
    gate (both at their exact boundary), the double-POST guard, the
    RegionalTreasuryEntry ledger write (ADR-0059 N-I4 reconciliation
    invariant), the WR14 market-book seed, the FIELD_NEEDED cleanup, and the
    REGION_TRADEDOCK_RESOURCES canon-divergence pin;
  * the route — 404s for a missing/regionless station or player, and the
    _region_construction_status remap of the service's generic 400s to
    409 (sectors) / 402 (treasury), with 403/404/409 passed through as-is;
  * the synthetic ship_type's phase timing (phase_hours/·_progress_phases),
    proving the 90-day build completes and is GAME_TIME_SCALE-scaled, driven
    entirely by an injected `now` rather than wall-clock time;
  * addendum — GET /my-region now carries treasury_balance (the owner panel
    needs it to show "treasury vs 50M"; the FE lane found the field missing).
    That route is async (AsyncSession, unlike the sync routes above), so its
    test uses the direct-call MagicMock/AsyncMock pattern already established
    in test_governance_citizen_api.py rather than the sync _FakeSession.

flag_modified(region, "treasury_balance") requires a REAL mapped ORM instance
(it reads `_sa_instance_state`), so `region` fixtures are real `Region(...)`
objects; `station`/`player` never hit flag_modified and stay SimpleNamespace.
"""
from __future__ import annotations

import inspect
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from src.api.routes import regional_governance as gov
from src.core import game_time
from src.core.market_bootstrap import build_market_prices
from src.models.construction import ConstructionReservation
from src.models.market_transaction import MarketPrice
from src.models.player import Player
from src.models.region import Region, RegionalTreasuryEntry
from src.models.station import Station
from src.services import construction_service as cs
from src.services.construction_service import ConstructionError

FIXED_NOW = datetime(2102, 6, 1, 12, 0, 0, tzinfo=UTC)


# --- shared fakes ------------------------------------------------------------


class _FakeQuery:
    """Stands in for a SQLAlchemy Query. filter()/order_by()/
    populate_existing()/with_for_update() are no-ops returning self — the
    test already controls exactly what's in the fake session, so predicates
    never need real evaluation."""

    def __init__(
        self,
        *,
        first: Any = None,
        count: int = 0,
        all: Optional[List[Any]] = None,
        seq: Optional[List[Any]] = None,
    ) -> None:
        self._first = first
        self._count = count
        self._all = all if all is not None else []
        self._seq = list(seq) if seq is not None else None

    def filter(self, *a: Any, **k: Any) -> "_FakeQuery":
        return self

    def order_by(self, *a: Any, **k: Any) -> "_FakeQuery":
        return self

    def populate_existing(self) -> "_FakeQuery":
        return self

    def with_for_update(self, *a: Any, **k: Any) -> "_FakeQuery":
        return self

    def first(self) -> Any:
        if self._seq is not None:
            return self._seq.pop(0) if self._seq else None
        return self._first

    def count(self) -> int:
        return self._count

    def all(self) -> List[Any]:
        return self._all


class _FakeSession:
    """Maps a model class (or column attribute, e.g. MarketPrice.commodity —
    the exact object construction_service queries on) to the fake query it
    should get. flush() is a no-op; commit()/rollback() are tracked so route
    tests can assert the right one fired."""

    def __init__(self, specs: Dict[Any, _FakeQuery]) -> None:
        self._specs = specs
        self.added: List[Any] = []
        self.deleted: List[Any] = []
        self.committed = False
        self.rolled_back = False

    def query(self, entity: Any) -> _FakeQuery:
        assert entity in self._specs, f"unexpected query for {entity!r}"
        return self._specs[entity]

    def add(self, obj: Any) -> None:
        self.added.append(obj)
        # Fake flush-default backfill: real SQLAlchemy assigns Column(default=)
        # values (like ConstructionReservation.id) at flush against a real
        # engine; this fake never flushes for real, so the ledger's cause_id
        # (read right after add()+flush() inside the service) needs it here.
        if isinstance(obj, ConstructionReservation) and obj.id is None:
            obj.id = uuid.uuid4()

    def delete(self, obj: Any) -> None:
        self.deleted.append(obj)

    def flush(self) -> None:
        pass

    def commit(self) -> None:
        self.committed = True

    def rollback(self) -> None:
        self.rolled_back = True


_DEFAULT_COMMODITIES = {
    "ore": {"quantity": 1000, "base_price": 10, "current_price": 10, "buys": False, "sells": True},
    "organics": {"quantity": 800, "base_price": 10, "current_price": 10, "buys": True, "sells": False},
    "equipment": {"quantity": 500, "base_price": 10, "current_price": 10, "buys": True, "sells": True},
    "fuel": {"quantity": 1500, "base_price": 10, "current_price": 10, "buys": False, "sells": True},
}


def _make_region(
    owner_user_id: Any,
    *,
    total_sectors: int = 500,
    treasury_balance: int = cs.REGION_TRADEDOCK_COST,
    **overrides: Any,
) -> Region:
    defaults = dict(
        id=uuid.uuid4(),
        name=f"region-{uuid.uuid4().hex[:8]}",
        display_name="Test Region",
        owner_id=owner_user_id,
        total_sectors=total_sectors,
        treasury_balance=treasury_balance,
    )
    defaults.update(overrides)
    return Region(**defaults)


def _make_station(
    region_id: Any,
    *,
    tradedock_tier: Optional[str] = "B",
    commodities: Optional[Dict[str, Any]] = None,
    **overrides: Any,
) -> SimpleNamespace:
    defaults = dict(
        id=uuid.uuid4(),
        name="Test TradeDock",
        region_id=region_id,
        tradedock_tier=tradedock_tier,
        treasury_balance=0,
        commodities=commodities if commodities is not None else dict(_DEFAULT_COMMODITIES),
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _make_player(*, player_id: Any = None, user_id: Any = None, **overrides: Any) -> SimpleNamespace:
    defaults = dict(id=player_id or uuid.uuid4(), user_id=user_id or uuid.uuid4())
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _make_session(
    *,
    player: Any,
    station: Any,
    region: Any,
    existing_reservation: Any = None,
    existing_market: Optional[List[Any]] = None,
) -> _FakeSession:
    return _FakeSession({
        Player: _FakeQuery(first=player),
        Station: _FakeQuery(first=station),
        Region: _FakeQuery(first=region),
        ConstructionReservation: _FakeQuery(first=existing_reservation),
        MarketPrice.commodity: _FakeQuery(all=existing_market or []),
    })


# --- service: happy path + ledger reconciliation -----------------------------


@pytest.mark.unit
class TestCreateRegionFundedConstructionService:
    def test_happy_path_deducts_escrows_and_creates_queued_reservation(self) -> None:
        owner_user_id = uuid.uuid4()
        player = _make_player(user_id=owner_user_id)
        region = _make_region(owner_user_id)
        station = _make_station(region.id)
        db = _make_session(player=player, station=station, region=region)

        result = cs.create_region_funded_construction(db, station, player, region.id, now=FIXED_NOW)

        assert region.treasury_balance == 0
        assert station.treasury_balance == cs.REGION_TRADEDOCK_COST

        reservations = [o for o in db.added if isinstance(o, ConstructionReservation)]
        assert len(reservations) == 1
        res = reservations[0]
        assert res.ship_type == "TRADEDOCK_CONSTRUCTION"
        assert res.state == "queued"
        assert res.total_cost == cs.REGION_TRADEDOCK_COST
        assert res.resources_required == cs.REGION_TRADEDOCK_RESOURCES

        assert result["reservation_id"] == str(res.id)
        assert result["station_id"] == str(station.id)
        assert result["region_id"] == str(region.id)
        assert result["total_cost"] == cs.REGION_TRADEDOCK_COST
        assert result["build_days"] == cs.REGION_TRADEDOCK_BUILD_DAYS
        assert result["state"] == "queued"

    def test_ledger_entry_reconciles_sum_deltas_with_balance(self) -> None:
        owner_user_id = uuid.uuid4()
        player = _make_player(user_id=owner_user_id)
        region = _make_region(owner_user_id, treasury_balance=cs.REGION_TRADEDOCK_COST)
        station = _make_station(region.id)
        db = _make_session(player=player, station=station, region=region)

        cs.create_region_funded_construction(db, station, player, region.id, now=FIXED_NOW)

        entries = [o for o in db.added if isinstance(o, RegionalTreasuryEntry)]
        assert len(entries) == 1
        entry = entries[0]
        assert entry.region_id == region.id
        assert entry.delta == -cs.REGION_TRADEDOCK_COST
        assert entry.before_balance == cs.REGION_TRADEDOCK_COST
        assert entry.after_balance == 0
        assert entry.cause_type == RegionalTreasuryEntry.CAUSE_EXPENDITURE
        reservations = [o for o in db.added if isinstance(o, ConstructionReservation)]
        assert entry.cause_id == reservations[0].id
        # ADR-0059 N-I4 TREASURY-RECON invariant: SUM(ledger deltas) == balance.
        assert entry.before_balance + sum(e.delta for e in entries) == region.treasury_balance

    def test_exact_50m_treasury_boundary_is_allowed(self) -> None:
        owner_user_id = uuid.uuid4()
        player = _make_player(user_id=owner_user_id)
        region = _make_region(owner_user_id, treasury_balance=50_000_000)
        station = _make_station(region.id)
        db = _make_session(player=player, station=station, region=region)

        cs.create_region_funded_construction(db, station, player, region.id, now=FIXED_NOW)
        assert region.treasury_balance == 0

    def test_treasury_one_short_is_rejected_with_zero_mutation(self) -> None:
        owner_user_id = uuid.uuid4()
        player = _make_player(user_id=owner_user_id)
        region = _make_region(owner_user_id, treasury_balance=49_999_999)
        station = _make_station(region.id)
        db = _make_session(player=player, station=station, region=region)

        with pytest.raises(ConstructionError) as exc:
            cs.create_region_funded_construction(db, station, player, region.id, now=FIXED_NOW)
        assert exc.value.status_code == 400
        assert "treasury" in exc.value.detail.lower()
        assert region.treasury_balance == 49_999_999
        assert station.treasury_balance == 0
        assert db.added == []

    def test_exact_500_sectors_boundary_is_allowed(self) -> None:
        owner_user_id = uuid.uuid4()
        player = _make_player(user_id=owner_user_id)
        region = _make_region(owner_user_id, total_sectors=500)
        station = _make_station(region.id)
        db = _make_session(player=player, station=station, region=region)

        cs.create_region_funded_construction(db, station, player, region.id, now=FIXED_NOW)
        assert region.treasury_balance == 0

    def test_499_sectors_is_rejected_with_zero_mutation(self) -> None:
        owner_user_id = uuid.uuid4()
        player = _make_player(user_id=owner_user_id)
        region = _make_region(owner_user_id, total_sectors=499)
        station = _make_station(region.id)
        db = _make_session(player=player, station=station, region=region)

        with pytest.raises(ConstructionError) as exc:
            cs.create_region_funded_construction(db, station, player, region.id, now=FIXED_NOW)
        assert exc.value.status_code == 400
        assert "sectors" in exc.value.detail.lower()
        assert region.treasury_balance == cs.REGION_TRADEDOCK_COST
        assert db.added == []

    def test_non_owner_rejected_on_both_player_id_and_user_id_mismatch(self) -> None:
        player = _make_player()  # random id + random user_id, matches nothing
        region = _make_region(uuid.uuid4())  # unrelated owner
        station = _make_station(region.id)
        db = _make_session(player=player, station=station, region=region)

        with pytest.raises(ConstructionError) as exc:
            cs.create_region_funded_construction(db, station, player, region.id, now=FIXED_NOW)
        assert exc.value.status_code == 403
        assert region.treasury_balance == cs.REGION_TRADEDOCK_COST
        assert db.added == []

    def test_owner_match_is_via_player_user_id_not_player_id(self) -> None:
        # AUTH SUBTLETY: Region.owner_id is a users.id, not a players.id — the
        # check must succeed via player.user_id even when player.id is a
        # totally unrelated uuid (proves the wrong field isn't being read).
        owner_user_id = uuid.uuid4()
        player = _make_player(player_id=uuid.uuid4(), user_id=owner_user_id)
        assert player.id != owner_user_id
        region = _make_region(owner_user_id)
        station = _make_station(region.id)
        db = _make_session(player=player, station=station, region=region)

        cs.create_region_funded_construction(db, station, player, region.id, now=FIXED_NOW)
        assert region.treasury_balance == 0

    def test_double_post_guard_rejects_second_reservation_with_zero_mutation(self) -> None:
        owner_user_id = uuid.uuid4()
        player = _make_player(user_id=owner_user_id)
        region = _make_region(owner_user_id)
        station = _make_station(region.id)
        existing = SimpleNamespace(id=uuid.uuid4())  # any non-None = "already in progress"
        db = _make_session(
            player=player, station=station, region=region, existing_reservation=existing
        )

        with pytest.raises(ConstructionError) as exc:
            cs.create_region_funded_construction(db, station, player, region.id, now=FIXED_NOW)
        assert exc.value.status_code == 409
        assert region.treasury_balance == cs.REGION_TRADEDOCK_COST
        assert station.treasury_balance == 0
        assert db.added == []

    def test_field_needed_marker_fully_removed(self) -> None:
        # Scoped to the function itself, not the whole module: construction_
        # service.py carries a SEPARATE, still-valid FIELD_NEEDED flag for an
        # unrelated feature (construction_events/pending_events columns,
        # apply_construction_event) that is out of this WO's scope and must
        # stay put — only the region-funded-construction one was stale.
        assert "FIELD_NEEDED" not in inspect.getsource(cs.create_region_funded_construction)

    def test_region_tradedock_resources_dict_pinned(self) -> None:
        # Byte-identical pin — the canon-"technology"-vs-code-"organics"
        # divergence is Max-gated, not this WO's to resolve.
        assert cs.REGION_TRADEDOCK_RESOURCES == {
            "ore": 500_000, "equipment": 300_000, "organics": 200_000,
        }

    def test_seed_station_market_book_seeds_all_tradeable_commodities(self) -> None:
        owner_user_id = uuid.uuid4()
        player = _make_player(user_id=owner_user_id)
        region = _make_region(owner_user_id)
        station = _make_station(region.id)
        db = _make_session(player=player, station=station, region=region)

        cs.create_region_funded_construction(db, station, player, region.id, now=FIXED_NOW)

        expected = build_market_prices(station.id, station.commodities)
        seeded = [o for o in db.added if isinstance(o, MarketPrice)]
        assert len(seeded) == len(expected) > 0

    def test_seed_station_market_book_skips_already_existing_rows(self) -> None:
        owner_user_id = uuid.uuid4()
        player = _make_player(user_id=owner_user_id)
        region = _make_region(owner_user_id)
        station = _make_station(region.id)
        db = _make_session(
            player=player, station=station, region=region,
            existing_market=[SimpleNamespace(commodity="ore")],
        )

        cs.create_region_funded_construction(db, station, player, region.id, now=FIXED_NOW)

        seeded = [o for o in db.added if isinstance(o, MarketPrice)]
        assert "ore" not in {row.commodity for row in seeded}
        assert len(seeded) == len(_DEFAULT_COMMODITIES) - 1


# --- route: 404s, status remap, zero-mutation --------------------------------


@pytest.mark.unit
class TestCreateRegionFundedTradedockRoute:
    @staticmethod
    def _body(station_id: Any) -> "gov.TradedockConstructionRequest":
        return gov.TradedockConstructionRequest(station_id=station_id)

    def test_no_player_record_is_404(self) -> None:
        current_user = SimpleNamespace(id=uuid.uuid4())
        db = _FakeSession({Player: _FakeQuery(first=None)})
        with pytest.raises(HTTPException) as exc:
            gov.create_region_funded_tradedock(self._body(uuid.uuid4()), current_user, db)
        assert exc.value.status_code == 404

    def test_station_not_found_is_404(self) -> None:
        current_user = SimpleNamespace(id=uuid.uuid4())
        player = _make_player(user_id=current_user.id)
        db = _FakeSession({
            Player: _FakeQuery(first=player),
            Station: _FakeQuery(first=None),
        })
        with pytest.raises(HTTPException) as exc:
            gov.create_region_funded_tradedock(self._body(uuid.uuid4()), current_user, db)
        assert exc.value.status_code == 404

    def test_station_without_region_is_404(self) -> None:
        current_user = SimpleNamespace(id=uuid.uuid4())
        player = _make_player(user_id=current_user.id)
        station = _make_station(region_id=None)
        db = _FakeSession({
            Player: _FakeQuery(first=player),
            Station: _FakeQuery(first=station),
        })
        with pytest.raises(HTTPException) as exc:
            gov.create_region_funded_tradedock(self._body(station.id), current_user, db)
        assert exc.value.status_code == 404

    def test_non_owner_remapped_to_403_with_zero_mutation(self) -> None:
        current_user = SimpleNamespace(id=uuid.uuid4())
        player = _make_player(user_id=current_user.id)
        region = _make_region(uuid.uuid4())  # unrelated owner
        station = _make_station(region.id)
        db = _make_session(player=player, station=station, region=region)

        with pytest.raises(HTTPException) as exc:
            gov.create_region_funded_tradedock(self._body(station.id), current_user, db)
        assert exc.value.status_code == 403
        assert db.rolled_back is True
        assert db.committed is False
        assert region.treasury_balance == cs.REGION_TRADEDOCK_COST
        assert db.added == []

    def test_under_500_sectors_remapped_to_409(self) -> None:
        current_user = SimpleNamespace(id=uuid.uuid4())
        player = _make_player(user_id=current_user.id)
        region = _make_region(current_user.id, total_sectors=499)
        station = _make_station(region.id)
        db = _make_session(player=player, station=station, region=region)

        with pytest.raises(HTTPException) as exc:
            gov.create_region_funded_tradedock(self._body(station.id), current_user, db)
        assert exc.value.status_code == 409
        assert region.treasury_balance == cs.REGION_TRADEDOCK_COST
        assert db.added == []

    def test_insufficient_treasury_remapped_to_402(self) -> None:
        current_user = SimpleNamespace(id=uuid.uuid4())
        player = _make_player(user_id=current_user.id)
        region = _make_region(current_user.id, treasury_balance=49_999_999)
        station = _make_station(region.id)
        db = _make_session(player=player, station=station, region=region)

        with pytest.raises(HTTPException) as exc:
            gov.create_region_funded_tradedock(self._body(station.id), current_user, db)
        assert exc.value.status_code == 402
        assert region.treasury_balance == 49_999_999
        assert db.added == []

    def test_double_post_remapped_to_409(self) -> None:
        current_user = SimpleNamespace(id=uuid.uuid4())
        player = _make_player(user_id=current_user.id)
        region = _make_region(current_user.id)
        station = _make_station(region.id)
        existing = SimpleNamespace(id=uuid.uuid4())
        db = _make_session(
            player=player, station=station, region=region, existing_reservation=existing
        )

        with pytest.raises(HTTPException) as exc:
            gov.create_region_funded_tradedock(self._body(station.id), current_user, db)
        assert exc.value.status_code == 409
        assert region.treasury_balance == cs.REGION_TRADEDOCK_COST
        assert db.added == []

    def test_happy_path_commits_and_returns_payload(self) -> None:
        current_user = SimpleNamespace(id=uuid.uuid4())
        player = _make_player(user_id=current_user.id)
        region = _make_region(current_user.id)
        station = _make_station(region.id)
        db = _make_session(player=player, station=station, region=region)

        result = gov.create_region_funded_tradedock(self._body(station.id), current_user, db)

        assert db.committed is True
        assert db.rolled_back is False
        assert result["region_id"] == str(region.id)
        assert result["station_id"] == str(station.id)
        assert region.treasury_balance == 0
        assert station.treasury_balance == cs.REGION_TRADEDOCK_COST


@pytest.mark.unit
class TestRegionConstructionStatusRemap:
    """_region_construction_status in isolation (the remap table itself)."""

    def test_sectors_400_becomes_409(self) -> None:
        err = ConstructionError(
            400, "Region-funded TradeDock construction requires >= 500 sectors; this region has 10."
        )
        assert gov._region_construction_status(err) == 409

    def test_treasury_400_becomes_402(self) -> None:
        err = ConstructionError(400, "Insufficient region treasury: need 50,000,000 cr, have 0 cr.")
        assert gov._region_construction_status(err) == 402

    def test_unrelated_400_passes_through_unchanged(self) -> None:
        err = ConstructionError(400, "This station is not in the specified region.")
        assert gov._region_construction_status(err) == 400

    @pytest.mark.parametrize("code", [403, 404, 409, 501])
    def test_non_400_codes_pass_through_unchanged(self, code: int) -> None:
        assert gov._region_construction_status(ConstructionError(code, "x")) == code


# --- synthetic ship_type phase timing: 90-day build, GAME_TIME_SCALE-scaled --


@pytest.mark.unit
class TestTradedockConstructionPhaseTiming:
    """TRADEDOCK_CONSTRUCTION is not in SHIP_BUILD_SPECS (deliberately — see
    phase_hours' docstring: adding it there would let create_reservation()
    build one through the ordinary player-credits path). phase_hours() reads
    REGION_TRADEDOCK_BUILD_DAYS instead so the same engine still drives it."""

    @staticmethod
    def _reservation(**overrides: Any) -> SimpleNamespace:
        defaults = dict(
            ship_type="TRADEDOCK_CONSTRUCTION",
            state="deposit_collected",
            total_cost=cs.REGION_TRADEDOCK_COST,
            milestones={"deposit": True, "keel_laid": True, "hull_complete": True, "final": False},
            resources_required=dict(cs.REGION_TRADEDOCK_RESOURCES),
            resources_delivered=dict(cs.REGION_TRADEDOCK_RESOURCES),
            phase_deadline=None,
            updated_at=None,
        )
        defaults.update(overrides)
        return SimpleNamespace(**defaults)

    def test_phase_hours_sum_to_90_canonical_days(self, monkeypatch) -> None:
        monkeypatch.setattr(game_time, "GAME_TIME_SCALE", 1.0)
        total = sum(cs.phase_hours("TRADEDOCK_CONSTRUCTION", p) for p in cs.PHASE_ORDER)
        assert total == pytest.approx(cs.REGION_TRADEDOCK_BUILD_DAYS * 24.0)

    def test_completes_after_exactly_90_canonical_days_via_injected_now(self, monkeypatch) -> None:
        monkeypatch.setattr(game_time, "GAME_TIME_SCALE", 1.0)
        res = self._reservation()
        start = FIXED_NOW
        cs._progress_phases(res, start)
        assert res.state == "frame_assembly"

        ninety_days_later = start + timedelta(days=cs.REGION_TRADEDOCK_BUILD_DAYS)
        cs._progress_phases(res, ninety_days_later)
        assert res.state == "complete"
        assert res.phase_deadline is None
        assert res.claim_expires_at == ninety_days_later + timedelta(hours=cs.CLAIM_WINDOW_HOURS)

    def test_one_day_short_of_90_has_not_completed(self, monkeypatch) -> None:
        monkeypatch.setattr(game_time, "GAME_TIME_SCALE", 1.0)
        res = self._reservation()
        start = FIXED_NOW
        cs._progress_phases(res, start)
        almost = start + timedelta(days=cs.REGION_TRADEDOCK_BUILD_DAYS - 1)
        cs._progress_phases(res, almost)
        assert res.state != "complete"

    def test_game_time_scale_compresses_the_first_phase_deadline(self, monkeypatch) -> None:
        # 90 canonical days at scale 2160 -> 1 wall-clock hour total; frame
        # assembly is 20% of the build -> 12 wall-clock minutes.
        monkeypatch.setattr(game_time, "GAME_TIME_SCALE", 2160.0)
        res = self._reservation()
        cs._progress_phases(res, FIXED_NOW)
        assert res.state == "frame_assembly"
        assert res.phase_deadline == FIXED_NOW + timedelta(minutes=12)


# --- addendum: GET /my-region carries treasury_balance -----------------------


def _fake_full_region(owner_user_id: Any, *, treasury_balance: int = 0, **overrides: Any) -> Region:
    """get_my_region's response dict reads (and float()/·.isoformat()s) every
    field below — unlike _make_region above, a partial Region(**kwargs) here
    leaves the untouched ones as None and crashes float(None)/None.isoformat()
    on real ORM construction (Column defaults never auto-apply without a real
    flush). Scoped to this one test class since the other tests never read
    past what _make_region already sets."""
    now = datetime.now(UTC)
    defaults = dict(
        id=uuid.uuid4(),
        name=f"region-{uuid.uuid4().hex[:8]}",
        display_name="Test Region",
        owner_id=owner_user_id,
        subscription_tier="standard",
        subscription_status="active",
        status="active",
        governance_type="autocracy",
        voting_threshold=0.51,
        election_frequency_days=90,
        constitutional_text=None,
        tax_rate=0.10,
        trade_bonuses={},
        economic_specialization=None,
        starting_credits=1000,
        starting_ship="scout",
        language_pack={},
        aesthetic_theme={},
        traditions={},
        total_sectors=500,
        active_players_30d=0,
        total_trade_volume=0.0,
        treasury_balance=treasury_balance,
        created_at=now,
        updated_at=now,
    )
    defaults.update(overrides)
    return Region(**defaults)


@pytest.mark.unit
class TestGetMyRegionTreasuryBalance:
    """WO-TD-RGF-1 addendum: the owner panel needs treasury_balance to show
    "treasury vs 50M cost" — GET /my-region didn't return it. Owner-gating is
    unchanged (verify_region_owner already 404s a non-owner before the
    response dict is ever built); this only adds one key to that dict."""

    @pytest.mark.asyncio
    async def test_owner_response_carries_treasury_balance(self) -> None:
        owner_user = SimpleNamespace(id=uuid.uuid4())
        region = _fake_full_region(owner_user.id, treasury_balance=12_345_678)
        db = MagicMock()
        db.execute = AsyncMock(
            return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=region))
        )
        result = await gov.get_my_region(current_user=owner_user, db=db)
        assert result["treasury_balance"] == 12_345_678
        assert result["id"] == str(region.id)

    @pytest.mark.asyncio
    async def test_non_owner_gets_404_before_the_response_dict_is_built(self) -> None:
        current_user = SimpleNamespace(id=uuid.uuid4())
        db = MagicMock()
        db.execute = AsyncMock(
            return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=None))
        )
        with pytest.raises(HTTPException) as exc:
            await gov.get_my_region(current_user=current_user, db=db)
        assert exc.value.status_code == 404
