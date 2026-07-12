"""Unit tests for WO-PIRATE-ECO-1 lanes C+D: the HOSTILE_RAIDER kill-log
feeder in ``npc_spawn_service.handle_npc_ship_destroyed`` and the
``GET /api/v1/regions/{region_id}/pirate-ecosystem`` read route.

DB-free, fake-session style (mirrors test_route_history_endpoint.py's
direct-call pattern -- Depends(...) resolution is bypassed, the route/
handler coroutine or function is called directly with plain args).
``db.query(...)`` is a MagicMock dispatched by model identity; no real
database is touched.

The sibling's PirateHolding / PirateKillLog / pirate_ecosystem_service
lanes landed on disk before this file's verification pass, so these tests
exercise the REAL model classes and REAL service module (monkeypatching
only the one entry point -- ``refresh_pirate_ecosystem_snapshot`` -- for
the route's happy-path test, to keep the route test independent of the
service's own scoring math, which is covered by the sibling's tests).
"""
import uuid as uuid_mod
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from src.api.routes import pirate_ecosystem as pe_route
from src.models.npc_character import NPCArchetype, NPCCharacter, NPCStatus
from src.models.pirate_holding import PirateHolding, PirateHoldingTier
from src.models.pirate_kill_log import PirateKillDisposition, PirateKillLog
from src.models.region import Region
from src.models.sector import Sector
from src.services import npc_spawn_service, pirate_ecosystem_service

PIRATE_KEY = npc_spawn_service.PIRATE_PATROL_DEFENSES_KEY


# --------------------------------------------------------------------------- #
# Lane C — kill-log feeder in handle_npc_ship_destroyed
# --------------------------------------------------------------------------- #

def make_npc(*, archetype, current_sector_id=4173, home_region_id=None,
             duty_role=None, bang_roster_ref=None):
    return NPCCharacter(
        id=uuid_mod.uuid4(),
        name="Test Raider",
        title="Captain",
        faction_code="pirates",
        archetype=archetype,
        status=NPCStatus.ON_DUTY,
        current_sector_id=current_sector_id,
        home_region_id=home_region_id,
        ship_id=uuid_mod.uuid4(),
        duty_role=duty_role,
        bang_roster_ref=bang_roster_ref,
    )


def make_sector(*, sector_id, npc_id, holding_id=None, other_ids=None):
    ids = list(other_ids or []) + [str(npc_id)]
    return Sector(
        sector_id=sector_id,
        region_id=uuid_mod.uuid4(),
        defenses={
            PIRATE_KEY: [
                {
                    "patrol_id": str(uuid_mod.uuid4()),
                    "squad_kind": "pirate_captain",
                    "npc_character_ids": ids,
                    "ship_count": len(ids),
                    "holding_id": str(holding_id) if holding_id else None,
                }
            ]
        },
        players_present=[],
    )


def make_db(*, npc, sector=None, holding=None, team_id=None, add_side_effect=None):
    """Sync-Session stand-in. ``db.query(Model)`` dispatches on the model
    identity to the right canned chain; ``db.begin_nested()`` is a no-op
    passthrough context manager (the real SAVEPOINT mechanics aren't what
    these tests are proving -- resilience to a feeder exception is)."""
    db = MagicMock()

    def _query(model, *args, **kwargs):
        chain = MagicMock()
        if model is NPCCharacter:
            chain.filter.return_value.first.return_value = npc
            chain.filter.return_value.order_by.return_value.first.return_value = None
        elif model is Sector:
            chain.filter.return_value.first.return_value = sector
            # WO-NPC-KIA-PRESENCE: handle_npc_ship_destroyed's Sector lock
            # now chains .populate_existing() before .with_for_update() --
            # a pure passthrough stub (returns the same filtered-chain
            # mock) so the existing .with_for_update().first() wiring
            # below still resolves, matching real SQLAlchemy's own
            # Query-returns-Query chaining shape.
            chain.filter.return_value.populate_existing.return_value = chain.filter.return_value
            chain.filter.return_value.with_for_update.return_value.first.return_value = sector
        elif model is PirateHolding:
            chain.filter.return_value.first.return_value = holding
        else:
            # src.models.player.Player.team_id column query
            chain.filter.return_value.scalar.return_value = team_id
        return chain

    db.query.side_effect = _query

    added = []

    def _add(obj):
        if add_side_effect is not None:
            add_side_effect(obj)
        added.append(obj)

    db.add = MagicMock(side_effect=_add)
    db.added = added
    db.flush = MagicMock()

    @contextmanager
    def _begin_nested():
        yield

    db.begin_nested = MagicMock(side_effect=_begin_nested)
    return db


def test_feeder_writes_cleared_row_when_holding_squad_is_emptied():
    """The kill that empties a holding-anchored squad is the "holding
    cleared" event -- exactly one CLEARED PirateKillLog row, with tier/
    weight/region snapshotted from the holding (not the NPC)."""
    ship_id = uuid_mod.uuid4()
    killer_id = uuid_mod.uuid4()
    holding_id = uuid_mod.uuid4()
    region_id = uuid_mod.uuid4()

    npc = make_npc(archetype=NPCArchetype.HOSTILE_RAIDER)
    npc.ship_id = ship_id
    sector = make_sector(
        sector_id=npc.current_sector_id, npc_id=npc.id,
        holding_id=holding_id, other_ids=[],
    )
    holding = PirateHolding(
        id=holding_id, region_id=region_id, sector_id=npc.current_sector_id,
        tier=PirateHoldingTier.STRONGHOLD,
    )
    db = make_db(npc=npc, sector=sector, holding=holding)

    result = npc_spawn_service.handle_npc_ship_destroyed(
        db, ship_id, killed_by_player_id=killer_id,
    )

    kill_logs = [o for o in db.added if isinstance(o, PirateKillLog)]
    assert len(kill_logs) == 1
    row = kill_logs[0]
    assert row.region_id == region_id
    assert row.holding_id == holding_id
    assert row.tier == PirateHoldingTier.STRONGHOLD
    assert row.kill_weight == 10
    assert row.attacker_player_id == killer_id
    assert row.disposition == PirateKillDisposition.CLEARED
    assert result is npc


def test_feeder_writes_nothing_when_squad_not_emptied():
    ship_id = uuid_mod.uuid4()
    holding_id = uuid_mod.uuid4()

    npc = make_npc(archetype=NPCArchetype.HOSTILE_RAIDER)
    npc.ship_id = ship_id
    sector = make_sector(
        sector_id=npc.current_sector_id, npc_id=npc.id,
        holding_id=holding_id, other_ids=["some-other-npc-id"],
    )
    holding = PirateHolding(
        id=holding_id, region_id=uuid_mod.uuid4(), sector_id=npc.current_sector_id,
        tier=PirateHoldingTier.CAMP,
    )
    db = make_db(npc=npc, sector=sector, holding=holding)

    npc_spawn_service.handle_npc_ship_destroyed(db, ship_id)

    assert not [o for o in db.added if isinstance(o, PirateKillLog)]


def test_feeder_writes_nothing_for_roaming_raider_no_holding():
    """A raider whose squad row has no holding_id (today's v1 default --
    no holding-anchored squads exist until ECO-2) never feeds the log,
    even if it's the last NPC in its squad."""
    ship_id = uuid_mod.uuid4()

    npc = make_npc(archetype=NPCArchetype.HOSTILE_RAIDER)
    npc.ship_id = ship_id
    sector = make_sector(
        sector_id=npc.current_sector_id, npc_id=npc.id,
        holding_id=None, other_ids=[],
    )
    db = make_db(npc=npc, sector=sector)

    npc_spawn_service.handle_npc_ship_destroyed(db, ship_id)

    assert not [o for o in db.added if isinstance(o, PirateKillLog)]


def test_feeder_writes_nothing_for_non_pirate_archetype():
    ship_id = uuid_mod.uuid4()

    npc = make_npc(archetype=NPCArchetype.LAW_ENFORCEMENT, home_region_id=uuid_mod.uuid4())
    npc.ship_id = ship_id
    sector = make_sector(sector_id=npc.current_sector_id, npc_id=npc.id, holding_id=uuid_mod.uuid4())
    db = make_db(npc=npc, sector=sector)

    result = npc_spawn_service.handle_npc_ship_destroyed(db, ship_id)

    assert not [o for o in db.added if isinstance(o, PirateKillLog)]
    assert result.status == NPCStatus.KIA  # LAW_ENFORCEMENT is not respawn-permitted


def test_feeder_exception_never_breaks_the_handler():
    """A feeder failure is caught, logged, and never propagates -- the
    handler still completes its normal KIA/respawn processing and still
    writes the (unrelated) NPCDeathLog row."""
    ship_id = uuid_mod.uuid4()
    killer_id = uuid_mod.uuid4()
    holding_id = uuid_mod.uuid4()

    npc = make_npc(archetype=NPCArchetype.HOSTILE_RAIDER)
    npc.ship_id = ship_id
    sector = make_sector(
        sector_id=npc.current_sector_id, npc_id=npc.id,
        holding_id=holding_id, other_ids=[],
    )
    holding = PirateHolding(
        id=holding_id, region_id=uuid_mod.uuid4(), sector_id=npc.current_sector_id,
        tier=PirateHoldingTier.OUTPOST,
    )

    def _boom(obj):
        if isinstance(obj, PirateKillLog):
            raise RuntimeError("simulated feeder failure")

    db = make_db(npc=npc, sector=sector, holding=holding, add_side_effect=_boom)

    result = npc_spawn_service.handle_npc_ship_destroyed(
        db, ship_id, killed_by_player_id=killer_id,
    )

    assert result is npc
    assert result.status == NPCStatus.RESPAWNING  # HOSTILE_RAIDER is respawn-permitted
    assert not [o for o in db.added if isinstance(o, PirateKillLog)]
    # The unrelated death-audit row still lands -- the feeder failure was
    # fully contained to its own savepoint/try-except.
    assert any(type(o).__name__ == "NPCDeathLog" for o in db.added)


def test_handler_return_payload_unchanged():
    """Regression: the feeder must not alter handle_npc_ship_destroyed's
    existing return contract (same NPCCharacter instance, standard KIA
    field mutations)."""
    ship_id = uuid_mod.uuid4()

    npc = make_npc(archetype=NPCArchetype.LAW_ENFORCEMENT, home_region_id=uuid_mod.uuid4())
    npc.ship_id = ship_id
    sector = make_sector(sector_id=npc.current_sector_id, npc_id=npc.id, holding_id=None)
    db = make_db(npc=npc, sector=sector)

    result = npc_spawn_service.handle_npc_ship_destroyed(db, ship_id)

    assert result is npc
    assert result.status == NPCStatus.KIA
    assert result.current_sector_id is None
    assert result.destroyed_at is not None


def test_feeder_no_crash_when_npc_has_no_sector():
    """Edge context: an NPC with no current sector (already off the
    board) must not crash the feeder's sector peek."""
    ship_id = uuid_mod.uuid4()

    npc = make_npc(archetype=NPCArchetype.HOSTILE_RAIDER, current_sector_id=None)
    npc.ship_id = ship_id
    db = make_db(npc=npc, sector=None)

    result = npc_spawn_service.handle_npc_ship_destroyed(db, ship_id)

    assert result is npc
    assert not [o for o in db.added if isinstance(o, PirateKillLog)]


def test_squad_cleared_derives_from_the_single_locked_sector_read():
    """WO-PIRATE-ECO-2 TOCTOU fix pin (structural): squad_cleared /
    squad_holding_id must come from the SAME with_for_update() read used
    for the actual npc_character_ids removal -- not a second, separate,
    unlocked peek. Exactly ONE db.query(Sector) call per handler invocation
    proves there is no such second read.

    The original bug: two near-simultaneous kills from the same squad each
    ran their own UNLOCKED peek before either's locked removal had
    committed, so BOTH computed squad_cleared=False even though the squad
    was, in fact, empty -- silently dropping the "cleared" event."""
    ship_id = uuid_mod.uuid4()
    killer_id = uuid_mod.uuid4()
    holding_id = uuid_mod.uuid4()
    region_id = uuid_mod.uuid4()

    npc = make_npc(archetype=NPCArchetype.HOSTILE_RAIDER)
    npc.ship_id = ship_id
    sector = make_sector(
        sector_id=npc.current_sector_id, npc_id=npc.id,
        holding_id=holding_id, other_ids=[],
    )
    holding = PirateHolding(
        id=holding_id, region_id=region_id, sector_id=npc.current_sector_id,
        tier=PirateHoldingTier.CAMP,
    )
    db = make_db(npc=npc, sector=sector, holding=holding)

    npc_spawn_service.handle_npc_ship_destroyed(db, ship_id, killed_by_player_id=killer_id)

    sector_query_calls = [c for c in db.query.call_args_list if c.args and c.args[0] is Sector]
    assert len(sector_query_calls) == 1, (
        "exactly one db.query(Sector) call is expected per handler "
        "invocation -- a second call would mean squad_cleared is being "
        "read from a separate, unlocked peek again"
    )
    kill_logs = [o for o in db.added if isinstance(o, PirateKillLog)]
    assert len(kill_logs) == 1
    assert kill_logs[0].holding_id == holding_id


def test_squad_cleared_reads_the_locked_chain_not_a_stale_unlocked_one():
    """WO-PIRATE-ECO-2 TOCTOU fix pin (behavioral): the two Sector query
    chains are deliberately seeded with DIFFERENT snapshots -- the plain
    ``.first()`` chain returns a STALE squad (still shows another member,
    not cleared, simulating a peek taken before a concurrent transaction's
    removal committed); the ``.with_for_update().first()`` chain returns
    the CURRENT, correctly-cleared squad (this NPC was the last member,
    already reflecting the serialized removal). A regression back to
    reading squad membership via the plain chain would silently drop the
    cleared event here -- exactly the bug this WO fixed."""
    ship_id = uuid_mod.uuid4()
    holding_id = uuid_mod.uuid4()

    npc = make_npc(archetype=NPCArchetype.HOSTILE_RAIDER)
    npc.ship_id = ship_id

    stale_sector = make_sector(
        sector_id=npc.current_sector_id, npc_id=npc.id,
        holding_id=holding_id, other_ids=["still-here-in-the-stale-read"],
    )
    current_sector = make_sector(
        sector_id=npc.current_sector_id, npc_id=npc.id,
        holding_id=holding_id, other_ids=[],
    )
    holding = PirateHolding(
        id=holding_id, region_id=uuid_mod.uuid4(), sector_id=npc.current_sector_id,
        tier=PirateHoldingTier.CAMP,
    )

    db = MagicMock()

    def _query(model, *args, **kwargs):
        chain = MagicMock()
        if model is NPCCharacter:
            chain.filter.return_value.first.return_value = npc
            chain.filter.return_value.order_by.return_value.first.return_value = None
        elif model is Sector:
            chain.filter.return_value.first.return_value = stale_sector
            # WO-NPC-KIA-PRESENCE passthrough stub -- see make_db() above.
            chain.filter.return_value.populate_existing.return_value = chain.filter.return_value
            chain.filter.return_value.with_for_update.return_value.first.return_value = current_sector
        elif model is PirateHolding:
            chain.filter.return_value.first.return_value = holding
        else:
            chain.filter.return_value.scalar.return_value = None
        return chain

    db.query.side_effect = _query
    added: list = []
    db.add = MagicMock(side_effect=added.append)
    db.added = added
    db.flush = MagicMock()

    @contextmanager
    def _begin_nested():
        yield

    db.begin_nested = MagicMock(side_effect=_begin_nested)

    npc_spawn_service.handle_npc_ship_destroyed(db, ship_id)

    kill_logs = [o for o in db.added if isinstance(o, PirateKillLog)]
    assert len(kill_logs) == 1, (
        "the handler must derive squad_cleared from the LOCKED "
        "with_for_update() read (current_sector, squad now empty), not "
        "the stale plain .first() chain (still shows another member)"
    )


# --------------------------------------------------------------------------- #
# Lane D — GET /regions/{region_id}/pirate-ecosystem
# --------------------------------------------------------------------------- #

def make_route_db(*, region=None, holding_rows=None):
    db = MagicMock()

    def _query(model, *args, **kwargs):
        chain = MagicMock()
        if model is Region:
            chain.filter.return_value.first.return_value = region
        else:
            # holdings-by-tier group-by query: db.query(PirateHolding.tier, func.count(...))
            chain.filter.return_value.group_by.return_value.all.return_value = holding_rows or []
        return chain

    db.query.side_effect = _query
    db.commit = MagicMock()
    return db


def make_region(region_id=None):
    return Region(
        id=region_id or uuid_mod.uuid4(),
        name=f"pirate-eco-test-{uuid_mod.uuid4()}",
        display_name="Pirate Ecosystem Test Region",
        total_sectors=500,
    )


@pytest.mark.asyncio
async def test_route_returns_expected_shape(monkeypatch):
    region_id = uuid_mod.uuid4()
    region = make_region(region_id)
    snapshot_state = {
        "current_population_score": 22,
        "current_target": 28.0,
        "suppression_modifier": 0.8,
        "kill_weight_last_30_days": 4,
        "cleansed_at": None,
        "zero_population_since": None,
    }
    monkeypatch.setattr(
        pirate_ecosystem_service, "refresh_pirate_ecosystem_snapshot",
        lambda db, region, **kw: snapshot_state,
    )
    holding_rows = [
        (PirateHoldingTier.CAMP, 2),
        (PirateHoldingTier.STRONGHOLD, 1),
    ]
    db = make_route_db(region=region, holding_rows=holding_rows)

    response = await pe_route.get_region_pirate_ecosystem(
        str(region_id), db, SimpleNamespace(), SimpleNamespace(),
    )

    assert response["region_id"] == str(region_id)
    assert response["population_score"] == 22
    assert response["target_population"] == 28.0
    assert response["suppression_modifier"] == 0.8
    assert response["kill_weight_last_30_days"] == 4
    assert response["cleansed_at"] is None
    assert response["zero_population_since"] is None
    assert response["holdings_by_tier"] == {"camp": 2, "outpost": 0, "stronghold": 1}
    db.commit.assert_called_once()


@pytest.mark.asyncio
async def test_route_404_for_unknown_region(monkeypatch):
    monkeypatch.setattr(
        pirate_ecosystem_service, "refresh_pirate_ecosystem_snapshot",
        lambda db, region, **kw: {},
    )
    db = make_route_db(region=None)

    with pytest.raises(HTTPException) as exc_info:
        await pe_route.get_region_pirate_ecosystem(
            str(uuid_mod.uuid4()), db, SimpleNamespace(), SimpleNamespace(),
        )
    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_route_404_for_malformed_region_id(monkeypatch):
    monkeypatch.setattr(
        pirate_ecosystem_service, "refresh_pirate_ecosystem_snapshot",
        lambda db, region, **kw: {},
    )
    db = make_route_db(region=None)

    with pytest.raises(HTTPException) as exc_info:
        await pe_route.get_region_pirate_ecosystem(
            "not-a-uuid", db, SimpleNamespace(), SimpleNamespace(),
        )
    assert exc_info.value.status_code == 404


def test_route_requires_player_auth_and_has_no_admin_gate():
    """Signature-level proof (no TestClient/JWT needed): both auth
    dependencies are present, and nothing admin-shaped is wired in."""
    import inspect

    from src.auth.dependencies import get_current_player, get_current_user

    sig = inspect.signature(pe_route.get_region_pirate_ecosystem)
    dependency_funcs = {
        p.default.dependency
        for p in sig.parameters.values()
        if hasattr(p.default, "dependency")
    }

    assert get_current_user in dependency_funcs
    assert get_current_player in dependency_funcs
    assert all("admin" not in fn.__name__.lower() for fn in dependency_funcs)
