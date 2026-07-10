"""WO-QA-HEADLESS-PLAYTHROUGH phase 1 — API-only core-loop playthrough.

Regression guard for ADR-0094 Proposed ("the entire game is playable via
API, zero browser required"). ONE ordered playthrough, real DB fixture (the
``db``/``client`` fixtures from ``tests/conftest.py`` — same real-DB-
transaction-per-test harness ``tests/integration/test_npc_living_system.py``
uses), REAL routes only — every request below is discovered by reading the
actual route file, never mocked. The point is proving the API surface, not
the response shapes in isolation, so each step asserts a genuine server-side
MUTATION (a follow-up read, a changed DB row, a non-trivial response field),
not just a 200.

Discovered route map (file:line as of this WO):
    POST /auth/register            api/routes/auth.py:464  (setup, not one
                                    of the 9 named steps -- the only way to
                                    get a real User+Player pair via the API;
                                    register itself returns no tokens)
    POST /auth/login/json          api/routes/auth.py:203  == "auth JSON-login"
    GET  /first-login/status       api/routes/first_login.py:96
    POST /first-login/session      api/routes/first_login.py:139
    POST /first-login/claim-ship   api/routes/first_login.py:231
    POST /first-login/complete     api/routes/first_login.py:493
                                    == "first-login-to-playable"
    POST /player/move/{sector_id}  api/routes/player.py:608        == "move"
    POST /trading/dock             api/routes/trading.py:1331      == "dock"
    POST /trading/buy              api/routes/trading.py:484       == "trade"
    POST /haggle/open              api/routes/haggle.py:65
    POST /haggle/offer             api/routes/haggle.py:92         == "haggle"
    POST /contracts/{id}/accept    api/routes/contracts.py:160     == "contract"
    POST /trading/undock           api/routes/trading.py:1544      == "undock"
    POST /combat/engage            api/routes/player_combat.py:162
    GET  /combat/{combatId}/status api/routes/player_combat.py:218
                                    == "combat-poll"

TWO FINDINGS surfaced building this (both reported, neither silently worked
around):

1. STEP-ORDER CONTRADICTS REAL GAME-STATE PRECONDITIONS. The WO's literal
   flow lists combat-poll BEFORE undock, but ``CombatService.attack_npc_ship``
   hard-rejects a docked attacker ("Cannot attack while docked at a port or
   landed on a planet" — combat_service.py:1162-1163) and ``move_player_to_
   sector`` hard-rejects a docked mover ("You must undock before moving to
   another sector" — movement_service.py:795-796). There is no real
   sequencing in which "dock -> trade -> haggle -> contract -> combat-poll ->
   undock" is literally executable in that order: the player must undock
   BEFORE combat can be engaged. This test runs undock immediately after the
   contract step and combat-poll last -- the same nine steps, reordered to
   match what the server actually allows. Every step the WO named is still
   exercised exactly once.

2. AI-DIALOGUE SAFETY GUARD REQUIRED, NOT OPTIONAL. ``POST /first-login/
   session`` calls ``FirstLoginService.generate_initial_prompt``, which tries
   a REAL AI provider first if one ``is_available()`` (ai_provider_service.py
   :654) -- and this shell has previously made live, paid OpenAI/Anthropic
   calls in a test that only relied on "no key present" (see this repo's own
   monk-memory note on the subject). ``is_available()`` checks
   ``os.getenv("OPENAI_API_KEY"/"ANTHROPIC_API_KEY")`` at call time, not at
   test-authorship time, so relying on env absence is not a safe assumption
   for THIS test file, which may run in CI environments with real AI
   secrets wired (per this WO's own phase-2 "activates the real
   ARIA_ENCRYPTION_KEY secret" framing). This test explicitly monkeypatches
   BOTH provider classes' ``is_available()`` to False for its whole run,
   forcing the documented-safe template-fallback path
   (``first_login_service.py:475-479``) regardless of what keys are present
   in whatever environment eventually executes it. The ESCAPE_POD claim-ship
   choice was picked specifically because it is the ONLY ship choice that
   bypasses the rest of the AI-touching interrogation entirely (claim_ship's
   auto_approve_escape_pod branch, first_login.py:281-298 / first_login_
   service.py:1394-1423, is pure DB writes -- no further AI call sites are
   reachable after this one guard).

PROOF BOUNDARY (per this WO's own instruction): the Mac has no Postgres, so
this file's collection is what's proven locally (see the report for the
exact ``pytest --collect-only`` output) -- the live green run is the
orchestrator's window / this WO's phase-2 CI lane. Every route above is
grep-confirmed to exist with the shown method+path; every request/response
shape is read directly from the route + service source, not guessed.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from src.core.config import settings
from src.models.cluster import Cluster, ClusterType
from src.models.contract import Contract, ContractIssuerType, ContractStatus, ContractType
from src.models.region import Region, RegionType
from src.models.sector import Sector
from src.models.ship import Ship, ShipStatus, ShipType
from src.models.station import Station, StationClass, StationType
from src.models.warp_tunnel import WarpTunnel, WarpTunnelType
from src.services import ai_provider_service as ai_provider_module

API = settings.API_V1_STR


@pytest.fixture
def client(app_fixture: FastAPI, db: Session) -> TestClient:
    """Shadows ``tests/conftest.py``'s own ``client`` fixture for THIS FILE
    ONLY (pytest resolves same-named fixtures file-local-first — zero effect
    on any other test file) — the shared fixture's ``TestClient(app_fixture)``
    sends Starlette's default ``Host: testserver``, which
    ``TrustedHostMiddleware`` (main.py:326-328) rejects with a 400 whenever
    ``settings.DEVELOPMENT_MODE`` is false at app-construction time (the
    live stage run hit exactly this — DEVELOPMENT_MODE is false there).

    ``base_url="http://localhost"`` is env-independent BY CONSTRUCTION, not
    by reading DEVELOPMENT_MODE and branching: main.py's allowed_hosts is
    ``["*"]`` when DEVELOPMENT_MODE else
    ``["localhost", "*.app.github.dev", "*.repl.co"]`` — "localhost" is a
    member of BOTH branches (the wildcard trivially allows it; the
    restrictive list names it explicitly), so this Host header passes
    regardless of which branch a given environment (stage's DEVELOPMENT_
    MODE=false pin, a permissive CI, or a local run) actually took. Never
    hardcodes a stage/tailnet-specific host."""
    return TestClient(app_fixture, base_url="http://localhost")


def _commodity(quantity: int, capacity: int, base_price: int, *, buys: bool, sells: bool) -> dict:
    return {
        "quantity": quantity, "capacity": capacity,
        "base_price": base_price, "current_price": base_price,
        "production_rate": 0, "price_variance": 20,
        "buys": buys, "sells": sells,
    }


class PlaythroughWorld:
    """Bag of fixture rows shared by the ordered playthrough."""


@pytest.fixture
def playthrough_world(db: Session) -> PlaythroughWorld:
    """Builds the DESTINATION half of the playthrough (a station + a hostile
    NPC ship + one acceptable contract) in a fresh, combat-legal
    ``PLAYER_OWNED`` region, warp-connected to a Terran Space starting
    sector -- REUSED if this DB already seeds one (stage), SELF-SEEDED if
    not (a bare fresh-migrated DB, e.g. phase-2 CI: ``alembic upgrade head``
    with no data seed at all).

    Reuse, not duplicate, when one already exists: ``POST /auth/register``
    (auth.py:584-599) always queries the DB's existing Terran Space region +
    its lowest-``sector_id`` sector and places the new player there -- a
    second, competing Terran Space region would not necessarily be the one
    register picks, silently breaking the warp connection this fixture
    builds. Self-seeding a MINIMAL one only when none exists avoids that
    collision by construction (there is nothing to compete with).

    ``valid_region_type_sector_count`` CHECK (models/region.py:194-199,
    verified against the CURRENT constraint text -- b4d2f7e9a1c6 widened
    player_owned's cap but left terran_space untouched): terran_space
    requires ``total_sectors = 300`` EXACTLY, not a floor/range like
    player_owned's [100, 1500] -- the created region uses exactly 300. Only
    ONE real Sector row is actually created (register only ever needs the
    lowest-``sector_id`` one to exist, not all 300) -- ``total_sectors`` is
    a declared-capacity column read by the CHECK constraint and canon
    displays, not a row-count invariant enforced anywhere else in this path.
    """
    w = PlaythroughWorld()

    terran_region = db.query(Region).filter(
        Region.region_type == RegionType.TERRAN_SPACE.value
    ).first()
    if terran_region is None:
        terran_region = Region(
            name="Terran Space",
            display_name="Terran Space",
            region_type=RegionType.TERRAN_SPACE.value,
            total_sectors=300,  # valid_region_type_sector_count: EXACT for terran_space
        )
        db.add(terran_region)
        db.flush()

    w.start_sector = (
        db.query(Sector)
        .filter(Sector.region_id == terran_region.id)
        .order_by(Sector.sector_id.asc())
        .first()
    )
    if w.start_sector is None:
        terran_cluster = Cluster(
            name="Terran Space Cluster",
            region_id=terran_region.id,
            type=ClusterType.STANDARD,
        )
        db.add(terran_cluster)
        db.flush()
        # sector_id is UNIQUE galaxy-wide (Sector.sector_id, not just within
        # this new region) — a low fixed value like 1 would be safe on a
        # genuinely blank DB but is NOT provably safe against some other
        # partially-seeded scenario that already has other regions/sectors.
        # Random high offset (same convention as `base` below, distinct
        # range so the two can never collide with each other) sidesteps
        # that regardless of what else is in this DB. Register()'s own
        # ORDER BY sector_id ASC still picks it trivially — it's the ONLY
        # sector in this fresh region either way.
        terran_sector_id = uuid.uuid4().int % 100_000 + 400_000
        w.start_sector = Sector(
            sector_id=terran_sector_id,
            name="Terran Space Sector 1",
            region_id=terran_region.id,
            cluster_id=terran_cluster.id,
            x_coord=0, y_coord=0, z_coord=0,
        )
        db.add(w.start_sector)
        db.flush()

    base = uuid.uuid4().int % 100_000 + 500_000  # avoid colliding with baseline sector_id ranges

    w.region = Region(
        name=f"playthrough-{base}",
        display_name="Playthrough Space",
        region_type=RegionType.PLAYER_OWNED.value,
        # valid_region_type_sector_count DB constraint: player_owned requires
        # total_sectors in [100, 1500] (central_nexus=5000, terran_space=300).
        # 10 was never valid — this fixture only ever populates one real
        # sector, so 100 (the floor) is correct, not just "big enough".
        total_sectors=100,
    )
    db.add(w.region)
    db.flush()

    w.cluster = Cluster(
        name="Playthrough Cluster",
        region_id=w.region.id,
        type=ClusterType.STANDARD,
    )
    db.add(w.cluster)
    db.flush()

    # Combat is disallowed in TERRAN_SPACE regions (CombatService.
    # _is_combat_allowed, combat_service.py:2407-2408) — PLAYER_OWNED is the
    # cheapest fixture-side way to get a real, combat-legal destination.
    w.sector_dest = Sector(
        sector_id=base,
        name=f"Playthrough Sector {base}",
        region_id=w.region.id,
        cluster_id=w.cluster.id,
        x_coord=1, y_coord=0, z_coord=0,
    )
    db.add(w.sector_dest)
    db.flush()

    w.tunnel = WarpTunnel(
        name="Playthrough Conduit",
        origin_sector_id=w.start_sector.id,
        destination_sector_id=w.sector_dest.id,
        type=WarpTunnelType.NATURAL,
        is_bidirectional=True,
        turn_cost=1,
    )
    db.add(w.tunnel)

    w.station = Station(
        name="Playthrough Depot",
        sector_id=w.sector_dest.sector_id,
        sector_uuid=w.sector_dest.id,
        station_class=StationClass.CLASS_4,
        type=StationType.TRADING,
        commodities={
            "ore": _commodity(5000, 8000, 15, buys=False, sells=True),
        },
    )
    db.add(w.station)
    db.flush()

    # A hostile NPC-owned ship in the same sector — the combat-engage target.
    # owner_id=None + is_npc=True is the exact "NPC ship" shape combat_
    # service.attack_npc_ship's is_npc_ship guard checks
    # (player_combat.py:189).
    w.npc_ship = Ship(
        name="Derelict Raider",
        type=ShipType.LIGHT_FREIGHTER,
        owner_id=None,
        is_npc=True,
        sector_id=w.sector_dest.sector_id,
        base_speed=1.0,
        current_speed=1.0,
        turn_cost=1,
        warp_capable=False,
        is_active=True,
        status=ShipStatus.IN_SPACE,
        maintenance={"condition": 100.0},
        cargo={"capacity": 50, "used": 0, "contents": {}},
        combat={"hull": 20, "max_hull": 20, "shields": 0, "max_shields": 0},
        attack_turn_cost=1,
        genesis_devices=0, max_genesis_devices=0,
        mines=0, max_mines=0,
        is_destroyed=False, is_flagship=False,
        purchase_value=0, current_value=0,
        upgrades={}, equipment_slots={}, insurance=None,
    )
    db.add(w.npc_ship)
    db.flush()

    # One NPC-issued, currently-POSTED cargo_delivery contract at the
    # destination station — issuer_id = the station's own id, matching
    # cargo_delivery's NPC-issuer convention (contract.py's issuer_id
    # docstring; GET /contracts/board filters on exactly this).
    w.contract = Contract(
        id=uuid.uuid4(),
        issuer_type=ContractIssuerType.NPC,
        issuer_id=w.station.id,
        contract_type=ContractType.CARGO_DELIVERY,
        status=ContractStatus.POSTED,
        destination_station_id=w.station.id,
        commodity_type="ore",
        quantity=10,
        payment=Decimal("500.00"),
        penalty=Decimal("500.00"),
        acceptance_fee_pct=Decimal("2.0"),
        deadline=datetime.now(UTC) + timedelta(hours=4),
        posting_stations=[],
    )
    db.add(w.contract)
    db.flush()

    return w


@pytest.mark.integration
def test_core_loop_playthrough(
    client: TestClient, db: Session, playthrough_world: PlaythroughWorld, monkeypatch: pytest.MonkeyPatch,
) -> None:
    w = playthrough_world

    # SAFETY (finding 2, see module docstring): force both AI providers
    # unavailable regardless of what keys exist in the environment. Class-
    # level patch — get_ai_provider_service() is a module-global singleton
    # (ai_provider_service.py:854-857) constructed once per process, so a
    # patch on the already-constructed instance would not be reliable
    # across test ordering; patching the CLASS method is.
    monkeypatch.setattr(ai_provider_module.OpenAIProvider, "is_available", lambda self: False)
    monkeypatch.setattr(ai_provider_module.AnthropicProvider, "is_available", lambda self: False)

    # --- setup: register (not one of the 9 named steps, but the only real
    # API path to a User+Player pair) ---
    username = f"playthrough_{uuid.uuid4().hex[:10]}"
    password = "PlaythroughPass123!"
    register_resp = client.post(
        f"{API}/auth/register",
        json={"username": username, "email": f"{username}@playthrough.test", "password": password},
    )
    assert register_resp.status_code == 200, register_resp.text

    # --- step 1: auth JSON-login ---
    login_resp = client.post(
        f"{API}/auth/login/json", json={"username": username, "password": password},
    )
    assert login_resp.status_code == 200, login_resp.text
    tokens = login_resp.json()
    assert tokens["access_token"]
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}

    # --- step 2: first-login-to-playable ---
    status_resp = client.get(f"{API}/first-login/status", headers=headers)
    assert status_resp.status_code == 200, status_resp.text
    assert status_resp.json()["requires_first_login"] is True

    session_resp = client.post(f"{API}/first-login/session", headers=headers)
    assert session_resp.status_code == 200, session_resp.text
    session_body = session_resp.json()
    assert session_body["current_step"] == "ship_selection"
    assert "ESCAPE_POD" in session_body["available_ships"]

    claim_resp = client.post(
        f"{API}/first-login/claim-ship", headers=headers,
        json={"ship_type": "ESCAPE_POD", "dialogue_response": "I'll take the escape pod, no questions asked."},
    )
    assert claim_resp.status_code == 200, claim_resp.text
    claim_body = claim_resp.json()
    # ESCAPE_POD auto-approves — no interrogation round is offered.
    assert claim_body["current_step"] == "completion"

    complete_resp = client.post(f"{API}/first-login/complete", headers=headers, json={})
    assert complete_resp.status_code == 200, complete_resp.text
    complete_body = complete_resp.json()
    assert complete_body["credits"] == 1000  # auto_approve_escape_pod's fixed grant
    assert complete_body["ship"]

    # Mutation check: the player now has a real ship and is standing in the
    # Terran Space starting sector register() placed them in.
    state_resp = client.get(f"{API}/player/state", headers=headers)
    assert state_resp.status_code == 200, state_resp.text
    state_body = state_resp.json()
    assert state_body["current_ship_id"]
    assert state_body["current_sector_id"] == w.start_sector.sector_id
    assert state_body["credits"] == 1000

    # --- step 3: move ---
    move_resp = client.post(f"{API}/player/move/{w.sector_dest.sector_id}", headers=headers)
    assert move_resp.status_code == 200, move_resp.text
    assert move_resp.json()["new_sector_id"] == w.sector_dest.sector_id
    # Mutation check: a fresh state poll reflects the new sector server-side,
    # not just the move response.
    state_after_move = client.get(f"{API}/player/state", headers=headers).json()
    assert state_after_move["current_sector_id"] == w.sector_dest.sector_id

    # --- step 4: dock ---
    dock_resp = client.post(
        f"{API}/trading/dock", headers=headers, json={"station_id": str(w.station.id)},
    )
    assert dock_resp.status_code == 200, dock_resp.text
    assert client.get(f"{API}/player/state", headers=headers).json()["is_docked"] is True

    # --- step 5: trade ---
    buy_resp = client.post(
        f"{API}/trading/buy", headers=headers,
        json={"station_id": str(w.station.id), "resource_type": "ore", "quantity": 10},
    )
    assert buy_resp.status_code == 200, buy_resp.text
    # Mutation check: cargo actually holds the purchased ore.
    ship_resp = client.get(f"{API}/player/current-ship", headers=headers)
    assert ship_resp.status_code == 200, ship_resp.text
    cargo_contents = ship_resp.json()["cargo"].get("contents", {})
    assert cargo_contents.get("ore", 0) >= 10

    # --- step 6: haggle ---
    open_resp = client.post(
        f"{API}/haggle/open", headers=headers,
        json={"station_id": str(w.station.id), "commodity": "ore", "side": "buy", "quantity": 5},
    )
    assert open_resp.status_code == 200, open_resp.text
    fair_price = open_resp.json()["band"]["fair_price"]

    # An offer AT the fair price sits inside every accept band regardless of
    # personality/round-narrowing multipliers (_compute_band's BUY accept
    # rule: offer >= fair * (1 - accept_half), and accept_half < 1 always) —
    # deterministic without needing to reproduce the RNG-free band math here.
    offer_resp = client.post(
        f"{API}/haggle/offer", headers=headers,
        json={"station_id": str(w.station.id), "commodity": "ore", "side": "buy", "offer": fair_price},
    )
    assert offer_resp.status_code == 200, offer_resp.text
    offer_body = offer_resp.json()
    assert offer_body["verdict"] == "accept"
    assert offer_body["status"] == "accepted"
    assert offer_body["agreed_price"] is not None

    # --- step 7: contract ---
    accept_resp = client.post(f"{API}/contracts/{w.contract.id}/accept", headers=headers)
    assert accept_resp.status_code == 200, accept_resp.text
    accept_body = accept_resp.json()
    assert accept_body["status"] == "accepted"
    # Mutation check: the contract board / "mine" listing reflects the accept.
    mine_resp = client.get(f"{API}/contracts/mine", headers=headers)
    assert mine_resp.status_code == 200, mine_resp.text
    accepted_ids = {c["id"] for c in mine_resp.json()["accepted"]}
    assert str(w.contract.id) in accepted_ids

    # --- step 8: undock (moved ahead of combat-poll — see finding 1) ---
    undock_resp = client.post(f"{API}/trading/undock", headers=headers)
    assert undock_resp.status_code == 200, undock_resp.text
    assert client.get(f"{API}/player/state", headers=headers).json()["is_docked"] is False

    # --- step 9: combat-poll ---
    engage_resp = client.post(
        f"{API}/combat/engage", headers=headers,
        json={"targetType": "ship", "targetId": str(w.npc_ship.id)},
    )
    assert engage_resp.status_code == 200, engage_resp.text
    engage_body = engage_resp.json()
    assert engage_body["status"] == "initiated", engage_body
    combat_id = engage_body["combatId"]
    assert combat_id

    poll_resp = client.get(f"{API}/combat/{combat_id}/status", headers=headers)
    assert poll_resp.status_code == 200, poll_resp.text
    poll_body = poll_resp.json()
    # Combat resolves synchronously inside /engage (player_combat.py's own
    # module docstring) — the poll always reports the already-resolved
    # state, never a pending one.
    assert poll_body["status"] == "completed"
    assert poll_body["outcome"] in ("attacker_win", "defender_win", "draw", "escaped")
    assert isinstance(poll_body["rounds"], list) and len(poll_body["rounds"]) > 0
