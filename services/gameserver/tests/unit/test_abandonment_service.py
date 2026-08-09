"""Unit tests — abandonment_service.py (PL4b planet abandonment/reclaim, single-writer).

No test file existed for this service. DB-free: raw-SQL / db.execute() calls
are routed through a hand-rolled ``_FakeDb`` that dispatches a canned
``_FakeResult`` by matching a distinguishing substring of the executed SQL
text (robust to call-order, and lets the same fake serve both the ``text()``
raw-SQL path and the ``player_planets`` Core delete/insert path). Real
(unattached) ``Planet`` / ``Player`` / ``Ship`` model instances are used
where the module mutates ORM attributes directly or calls
``flag_modified()`` (which requires a real mapped instance, not a
SimpleNamespace) -- notably ``ship.cargo``.

Sections:
  TestSunkCostFor / TestCompensationFor — the pure money-math (I2/I10):
    the citadel-ladder sunk cost and the 0.4-haircut, capped/floored.
  TestSiegeBlocksAbandon / TestSiegeBlocksReclaim — the siege gates (I8),
    including the stale-siege relief window and the no-start-stamp
    conservative-block branch.
  TestRevertToUnowned — the shared reversion helper (I7): ownership/access
    reset, structures/citadel/etc. left untouched, abandoned_at stamped,
    reclaimable_at cleared, player_planets association deleted.
  TestAbandonPlanet — voluntary path (I3): not-owner/under-siege rejection,
    landed-owner lifted, always zero compensation.
  TestReclaimPlanet — involuntary path (I2/I4/I5/I6/I7/I8): every
    precondition rejection, the all-or-nothing price validation (credits
    then cargo), the tenure-gated haircut paid to the displaced owner,
    cargo debit math (incl. the legacy fuel_ore alias drop), and the
    re-founding under the reclaimer.
  TestFlagInactivePlanets / TestClearFlagForPlayer / TestTenureDays —
    the raw-SQL query-driven helpers, via canned fetchall/first rows.
"""
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from src.models.planet import Planet, PlanetStatus
from src.models.player import Player
from src.models.ship import Ship
from src.services.abandonment_service import (
    CLAIM_CREDIT_COST,
    COMPENSATION_FRACTION,
    RECLAIM_CREDIT_COST,
    RECLAIM_GRACE_DAYS,
    RECLAIM_RESOURCE_COST,
    SIEGE_RECLAIM_RELIEF_DAYS,
    TENURE_FLOOR_DAYS,
    _revert_to_unowned,
    _siege_blocks_abandon,
    _siege_blocks_reclaim,
    _tenure_days,
    abandon_planet,
    clear_flag_for_player,
    compensation_for,
    flag_inactive_planets,
    reclaim_planet,
    sunk_cost_for,
)
from src.services.citadel_service import CITADEL_LEVELS


class _FakeResult:
    def __init__(self, first_val=None, fetchall_val=None):
        self._first = first_val
        self._fetchall = fetchall_val if fetchall_val is not None else []

    def first(self):
        return self._first

    def fetchall(self):
        return self._fetchall


class _FakeDb:
    """Dispatches db.execute() results by substring-matching the SQL text
    against registered (match, result) pairs, consumed in FIFO order per
    match so repeated identical statements draw distinct canned rows."""

    def __init__(self, execute_results=None, query_first_results=None):
        self._execute_results = list(execute_results or [])
        self.executed = []
        self._query_first_queue = list(query_first_results or [])

    def execute(self, stmt, params=None):
        sql = str(stmt)
        self.executed.append((sql, params))
        for i, (match, result) in enumerate(self._execute_results):
            if match in sql:
                del self._execute_results[i]
                return result
        return _FakeResult()

    def query(self, _model):
        return self

    def filter(self, *_args, **_kwargs):
        return self

    def with_for_update(self):
        return self

    def first(self):
        return self._query_first_queue.pop(0)


def _planet(**kwargs):
    p = Planet()
    p.id = kwargs.pop("id", uuid4())
    p.owner_id = kwargs.pop("owner_id", None)
    p.status = kwargs.pop("status", PlanetStatus.COLONIZED)
    p.landing_rights = kwargs.pop("landing_rights", None)
    p.under_siege = kwargs.pop("under_siege", False)
    p.siege_started_at = kwargs.pop("siege_started_at", None)
    p.citadel_level = kwargs.pop("citadel_level", 0)
    for k, v in kwargs.items():
        setattr(p, k, v)
    return p


def _player(**kwargs):
    pl = Player()
    pl.id = kwargs.pop("id", uuid4())
    pl.credits = kwargs.pop("credits", 100_000)
    pl.is_landed = kwargs.pop("is_landed", False)
    pl.current_planet_id = kwargs.pop("current_planet_id", None)
    for k, v in kwargs.items():
        setattr(pl, k, v)
    return pl


def _ship(**kwargs):
    s = Ship()
    s.cargo = kwargs.pop(
        "cargo",
        {"used": 0, "capacity": 50, "contents": {}},
    )
    for k, v in kwargs.items():
        setattr(s, k, v)
    return s


# ---------------------------------------------------------------------------
# sunk_cost_for / compensation_for
# ---------------------------------------------------------------------------


class TestSunkCostFor:
    def test_level_0_is_just_the_claim_fee(self):
        assert sunk_cost_for(0) == CLAIM_CREDIT_COST

    def test_level_1_is_still_just_the_claim_fee(self):
        assert sunk_cost_for(1) == CLAIM_CREDIT_COST

    def test_level_2_adds_the_l2_upgrade_cost(self):
        expected = CLAIM_CREDIT_COST + CITADEL_LEVELS[2]["upgrade_cost"]
        assert sunk_cost_for(2) == expected

    def test_level_5_sums_the_whole_ladder(self):
        expected = CLAIM_CREDIT_COST + sum(
            CITADEL_LEVELS[n]["upgrade_cost"] for n in range(2, 6)
        )
        assert sunk_cost_for(5) == expected

    def test_none_treated_as_zero(self):
        assert sunk_cost_for(None) == CLAIM_CREDIT_COST


class TestCompensationFor:
    def test_level_0_comp_is_a_guaranteed_loss_vs_claim_fee(self):
        comp = compensation_for(0)
        assert comp == int(round(COMPENSATION_FRACTION * CLAIM_CREDIT_COST))
        assert comp < CLAIM_CREDIT_COST

    def test_comp_is_never_more_than_sunk_cost(self):
        for level in range(0, 6):
            assert compensation_for(level) <= sunk_cost_for(level)

    def test_comp_is_never_negative(self):
        assert compensation_for(0) >= 0

    def test_comp_scales_with_citadel_level(self):
        assert compensation_for(4) > compensation_for(2) > compensation_for(0)


# ---------------------------------------------------------------------------
# siege gates
# ---------------------------------------------------------------------------


class TestSiegeBlocksAbandon:
    def test_not_sieged_does_not_block(self):
        assert _siege_blocks_abandon(_planet(under_siege=False)) is False

    def test_sieged_blocks(self):
        assert _siege_blocks_abandon(_planet(under_siege=True)) is True


class TestSiegeBlocksReclaim:
    def test_not_sieged_does_not_block(self):
        assert _siege_blocks_reclaim(_planet(under_siege=False)) is False

    def test_sieged_with_no_start_stamp_conservatively_blocks(self):
        p = _planet(under_siege=True, siege_started_at=None)
        assert _siege_blocks_reclaim(p) is True

    def test_recent_siege_blocks(self):
        started = datetime.now(UTC) - timedelta(days=1)
        p = _planet(under_siege=True, siege_started_at=started)
        assert _siege_blocks_reclaim(p) is True

    def test_stale_siege_no_longer_blocks(self):
        started = datetime.now(UTC) - timedelta(days=SIEGE_RECLAIM_RELIEF_DAYS + 1)
        p = _planet(under_siege=True, siege_started_at=started)
        assert _siege_blocks_reclaim(p) is False

    def test_naive_siege_started_at_is_coerced_to_aware(self):
        started = (datetime.now(UTC) - timedelta(days=1)).replace(tzinfo=None)
        p = _planet(under_siege=True, siege_started_at=started)
        # Would raise (naive - aware) if _siege_blocks_reclaim didn't coerce.
        assert _siege_blocks_reclaim(p) is True


# ---------------------------------------------------------------------------
# _revert_to_unowned
# ---------------------------------------------------------------------------


class TestRevertToUnowned:
    def test_resets_ownership_and_access_fields(self):
        p = _planet(
            owner_id=uuid4(),
            status=PlanetStatus.COLONIZED,
            landing_rights={"mode": "allowlist"},
        )
        db = _FakeDb()
        _revert_to_unowned(db, p)
        assert p.owner_id is None
        assert p.status == PlanetStatus.HABITABLE
        assert p.landing_rights is None

    def test_deletes_the_player_planets_association(self):
        p = _planet()
        db = _FakeDb()
        _revert_to_unowned(db, p)
        assert any("DELETE FROM player_planets" in sql for sql, _ in db.executed)

    def test_stamps_abandoned_at_and_clears_reclaimable_at(self):
        p = _planet()
        db = _FakeDb()
        _revert_to_unowned(db, p)
        abandoned_calls = [
            params for sql, params in db.executed
            if "UPDATE planets SET abandoned_at" in sql
        ]
        reclaimable_calls = [
            params for sql, params in db.executed
            if "UPDATE planets SET reclaimable_at" in sql
        ]
        assert len(abandoned_calls) == 1
        assert abandoned_calls[0]["val"] is not None
        assert len(reclaimable_calls) == 1
        assert reclaimable_calls[0]["val"] is None

    def test_leaves_developed_asset_fields_untouched(self):
        # _revert_to_unowned must not touch citadel_level/under_siege -- those
        # are the reclaim premium the next claimant inherits.
        p = _planet(citadel_level=4, under_siege=True)
        db = _FakeDb()
        _revert_to_unowned(db, p)
        assert p.citadel_level == 4
        assert p.under_siege is True


# ---------------------------------------------------------------------------
# abandon_planet
# ---------------------------------------------------------------------------


class TestAbandonPlanet:
    def test_not_owner_raises(self):
        owner = _player()
        other_owner_id = uuid4()
        p = _planet(owner_id=other_owner_id)
        db = _FakeDb()
        with pytest.raises(ValueError, match="not_owner"):
            abandon_planet(db, p, owner)

    def test_unowned_planet_raises_not_owner(self):
        owner = _player()
        p = _planet(owner_id=None)
        db = _FakeDb()
        with pytest.raises(ValueError, match="not_owner"):
            abandon_planet(db, p, owner)

    def test_under_siege_raises(self):
        owner = _player()
        p = _planet(owner_id=owner.id, under_siege=True)
        db = _FakeDb()
        with pytest.raises(ValueError, match="under_siege"):
            abandon_planet(db, p, owner)

    def test_lifts_owner_if_landed_on_the_abandoned_planet(self):
        owner = _player()
        p = _planet(owner_id=owner.id)
        owner.is_landed = True
        owner.current_planet_id = p.id
        db = _FakeDb()
        abandon_planet(db, p, owner)
        assert owner.is_landed is False
        assert owner.current_planet_id is None

    def test_does_not_disturb_owner_landed_elsewhere(self):
        owner = _player()
        p = _planet(owner_id=owner.id)
        elsewhere = uuid4()
        owner.is_landed = True
        owner.current_planet_id = elsewhere
        db = _FakeDb()
        abandon_planet(db, p, owner)
        assert owner.is_landed is True
        assert owner.current_planet_id == elsewhere

    def test_always_pays_zero_compensation(self):
        owner = _player()
        p = _planet(owner_id=owner.id, citadel_level=5)
        db = _FakeDb()
        result = abandon_planet(db, p, owner)
        assert result["compensation"] == 0
        assert result["path"] == "voluntary"

    def test_reverts_the_planet_to_unowned(self):
        owner = _player()
        p = _planet(owner_id=owner.id)
        db = _FakeDb()
        abandon_planet(db, p, owner)
        assert p.owner_id is None
        assert p.status == PlanetStatus.HABITABLE


# ---------------------------------------------------------------------------
# reclaim_planet
# ---------------------------------------------------------------------------


def _reclaimable_row(flagged_days_ago):
    ts = datetime.now(UTC) - timedelta(days=flagged_days_ago)
    return (None, ts, None)  # (tax_rate, reclaimable_at, abandoned_at)


class TestReclaimPlanet:
    def test_unowned_planet_raises_not_owned(self):
        p = _planet(owner_id=None)
        reclaimer = _player()
        db = _FakeDb()
        with pytest.raises(ValueError, match="not_owned"):
            reclaim_planet(db, p, reclaimer)

    def test_reclaimer_is_current_owner_raises_already_owner(self):
        reclaimer = _player()
        p = _planet(owner_id=reclaimer.id)
        db = _FakeDb()
        with pytest.raises(ValueError, match="already_owner"):
            reclaim_planet(db, p, reclaimer)

    def test_not_flagged_raises(self):
        p = _planet(owner_id=uuid4())
        reclaimer = _player()
        db = _FakeDb(
            execute_results=[("SELECT tax_rate", _FakeResult(first_val=(None, None, None)))]
        )
        with pytest.raises(ValueError, match="not_flagged"):
            reclaim_planet(db, p, reclaimer)

    def test_within_grace_window_raises(self):
        p = _planet(owner_id=uuid4())
        reclaimer = _player()
        db = _FakeDb(
            execute_results=[
                ("SELECT tax_rate", _FakeResult(first_val=_reclaimable_row(1)))
            ]
        )
        with pytest.raises(ValueError, match="within_grace"):
            reclaim_planet(db, p, reclaimer)

    def test_past_grace_and_under_recent_siege_raises(self):
        p = _planet(
            owner_id=uuid4(),
            under_siege=True,
            siege_started_at=datetime.now(UTC) - timedelta(days=1),
        )
        reclaimer = _player()
        db = _FakeDb(
            execute_results=[
                (
                    "SELECT tax_rate",
                    _FakeResult(first_val=_reclaimable_row(RECLAIM_GRACE_DAYS + 1)),
                )
            ]
        )
        with pytest.raises(ValueError, match="under_siege"):
            reclaim_planet(db, p, reclaimer)

    def test_insufficient_credits_raises(self):
        p = _planet(owner_id=uuid4())
        reclaimer = _player(credits=RECLAIM_CREDIT_COST - 1)
        db = _FakeDb(
            execute_results=[
                (
                    "SELECT tax_rate",
                    _FakeResult(first_val=_reclaimable_row(RECLAIM_GRACE_DAYS + 1)),
                )
            ]
        )
        with pytest.raises(ValueError, match="insufficient_credits"):
            reclaim_planet(db, p, reclaimer)

    def test_no_ship_raises(self):
        p = _planet(owner_id=uuid4())
        reclaimer = _player(credits=RECLAIM_CREDIT_COST)
        db = _FakeDb(
            execute_results=[
                (
                    "SELECT tax_rate",
                    _FakeResult(first_val=_reclaimable_row(RECLAIM_GRACE_DAYS + 1)),
                )
            ]
        )
        with pytest.raises(ValueError, match="no_ship"):
            reclaim_planet(db, p, reclaimer, ship=None)

    def test_insufficient_resources_raises(self):
        p = _planet(owner_id=uuid4())
        reclaimer = _player(credits=RECLAIM_CREDIT_COST)
        ship = _ship(
            cargo={
                "used": 0,
                "capacity": 50,
                "contents": {"ore": RECLAIM_RESOURCE_COST - 1, "organics": 9999, "equipment": 9999},
            }
        )
        db = _FakeDb(
            execute_results=[
                (
                    "SELECT tax_rate",
                    _FakeResult(first_val=_reclaimable_row(RECLAIM_GRACE_DAYS + 1)),
                )
            ]
        )
        with pytest.raises(ValueError, match="insufficient_resources"):
            reclaim_planet(db, p, reclaimer, ship=ship)

    def test_legacy_fuel_ore_alias_counts_toward_ore(self):
        p = _planet(owner_id=uuid4(), citadel_level=0)
        reclaimer = _player(credits=RECLAIM_CREDIT_COST)
        ship = _ship(
            cargo={
                "used": 0,
                "capacity": 50,
                "contents": {
                    "fuel_ore": RECLAIM_RESOURCE_COST,
                    "organics": RECLAIM_RESOURCE_COST,
                    "equipment": RECLAIM_RESOURCE_COST,
                },
            }
        )
        db = _FakeDb(
            execute_results=[
                (
                    "SELECT tax_rate",
                    _FakeResult(first_val=_reclaimable_row(RECLAIM_GRACE_DAYS + 1)),
                ),
                (
                    "SELECT acquired_at",
                    _FakeResult(first_val=None),  # no tenure row -> ineligible
                ),
            ]
        )
        result = reclaim_planet(db, p, reclaimer, ship=ship)
        assert result["displaced_compensation"] == 0
        assert ship.cargo["contents"]["ore"] == 0
        assert "fuel_ore" not in ship.cargo["contents"]

    def test_charges_credits_and_cargo_and_flips_ownership(self):
        old_owner_id = uuid4()
        p = _planet(owner_id=old_owner_id, citadel_level=0)
        reclaimer = _player(credits=200_000)
        ship = _ship(
            cargo={
                "used": 300,
                "capacity": 500,
                "contents": {
                    "ore": RECLAIM_RESOURCE_COST + 10,
                    "organics": RECLAIM_RESOURCE_COST + 20,
                    "equipment": RECLAIM_RESOURCE_COST + 30,
                },
            }
        )
        db = _FakeDb(
            execute_results=[
                (
                    "SELECT tax_rate",
                    _FakeResult(first_val=_reclaimable_row(RECLAIM_GRACE_DAYS + 1)),
                ),
                ("SELECT acquired_at", _FakeResult(first_val=None)),
            ]
        )
        result = reclaim_planet(db, p, reclaimer, ship=ship)
        assert reclaimer.credits == 200_000 - RECLAIM_CREDIT_COST
        assert ship.cargo["contents"]["ore"] == 10
        assert ship.cargo["contents"]["organics"] == 20
        assert ship.cargo["contents"]["equipment"] == 30
        assert p.owner_id == reclaimer.id
        assert p.status == PlanetStatus.COLONIZED
        assert result["reclaim_credits_charged"] == RECLAIM_CREDIT_COST
        assert any("INSERT INTO player_planets" in sql for sql, _ in db.executed)

    def test_tenure_eligible_displaced_owner_is_paid_the_haircut(self):
        old_owner_id = uuid4()
        p = _planet(owner_id=old_owner_id, citadel_level=2)
        reclaimer = _player(credits=200_000)
        ship = _ship(
            cargo={
                "used": 0,
                "capacity": 500,
                "contents": {
                    "ore": RECLAIM_RESOURCE_COST,
                    "organics": RECLAIM_RESOURCE_COST,
                    "equipment": RECLAIM_RESOURCE_COST,
                },
            }
        )
        acquired = datetime.now(UTC) - timedelta(days=TENURE_FLOOR_DAYS + 1)
        displaced = _player(id=old_owner_id, credits=1_000)
        db = _FakeDb(
            execute_results=[
                (
                    "SELECT tax_rate",
                    _FakeResult(first_val=_reclaimable_row(RECLAIM_GRACE_DAYS + 1)),
                ),
                ("SELECT acquired_at", _FakeResult(first_val=(acquired,))),
            ],
            query_first_results=[displaced],
        )
        expected_comp = compensation_for(2)
        result = reclaim_planet(db, p, reclaimer, ship=ship)
        assert result["tenure_eligible"] is True
        assert result["displaced_compensation"] == expected_comp
        assert displaced.credits == 1_000 + expected_comp

    def test_tenure_below_floor_pays_no_compensation(self):
        old_owner_id = uuid4()
        p = _planet(owner_id=old_owner_id, citadel_level=3)
        reclaimer = _player(credits=200_000)
        ship = _ship(
            cargo={
                "used": 0,
                "capacity": 500,
                "contents": {
                    "ore": RECLAIM_RESOURCE_COST,
                    "organics": RECLAIM_RESOURCE_COST,
                    "equipment": RECLAIM_RESOURCE_COST,
                },
            }
        )
        acquired = datetime.now(UTC) - timedelta(days=TENURE_FLOOR_DAYS - 1)
        db = _FakeDb(
            execute_results=[
                (
                    "SELECT tax_rate",
                    _FakeResult(first_val=_reclaimable_row(RECLAIM_GRACE_DAYS + 1)),
                ),
                ("SELECT acquired_at", _FakeResult(first_val=(acquired,))),
            ]
        )
        result = reclaim_planet(db, p, reclaimer, ship=ship)
        assert result["tenure_eligible"] is False
        assert result["displaced_compensation"] == 0


# ---------------------------------------------------------------------------
# flag_inactive_planets / clear_flag_for_player / _tenure_days
# ---------------------------------------------------------------------------


class TestFlagInactivePlanets:
    def test_counts_flagged_and_cleared_rows(self):
        flag_id, clear_id = uuid4(), uuid4()
        db = _FakeDb(
            execute_results=[
                ("JOIN players pl ON pl.id = p.owner_id", _FakeResult(fetchall_val=[(flag_id,)])),
                ("LEFT JOIN players pl ON pl.id = p.owner_id", _FakeResult(fetchall_val=[(clear_id,)])),
            ]
        )
        result = flag_inactive_planets(db)
        assert result == {"flagged": 1, "cleared": 1}

    def test_no_candidates_is_a_no_op(self):
        db = _FakeDb(
            execute_results=[
                ("JOIN players pl ON pl.id = p.owner_id", _FakeResult(fetchall_val=[])),
                ("LEFT JOIN players pl ON pl.id = p.owner_id", _FakeResult(fetchall_val=[])),
            ]
        )
        assert flag_inactive_planets(db) == {"flagged": 0, "cleared": 0}


class TestClearFlagForPlayer:
    def test_clears_every_flagged_row_for_the_player(self):
        p1, p2 = uuid4(), uuid4()
        db = _FakeDb(
            execute_results=[
                ("SELECT id FROM planets", _FakeResult(fetchall_val=[(p1,), (p2,)])),
            ]
        )
        count = clear_flag_for_player(db, uuid4())
        assert count == 2
        assert any("UPDATE planets SET reclaimable_at" in sql for sql, _ in db.executed)

    def test_no_flagged_rows_returns_zero(self):
        db = _FakeDb(
            execute_results=[("SELECT id FROM planets", _FakeResult(fetchall_val=[]))]
        )
        assert clear_flag_for_player(db, uuid4()) == 0


class TestTenureDays:
    def test_no_association_row_returns_none(self):
        db = _FakeDb(execute_results=[("SELECT acquired_at", _FakeResult(first_val=None))])
        assert _tenure_days(db, uuid4(), uuid4()) is None

    def test_computes_days_since_acquisition(self):
        acquired = datetime.now(UTC) - timedelta(days=10)
        db = _FakeDb(
            execute_results=[("SELECT acquired_at", _FakeResult(first_val=(acquired,)))]
        )
        days = _tenure_days(db, uuid4(), uuid4())
        assert 9.9 < days < 10.1

    def test_naive_acquired_at_is_coerced_to_aware(self):
        acquired = (datetime.now(UTC) - timedelta(days=5)).replace(tzinfo=None)
        db = _FakeDb(
            execute_results=[("SELECT acquired_at", _FakeResult(first_val=(acquired,)))]
        )
        days = _tenure_days(db, uuid4(), uuid4())
        assert 4.9 < days < 5.1
