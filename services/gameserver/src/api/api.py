from fastapi import APIRouter

from src.api.routes.auth import router as auth_router
from src.api.routes.users import router as users_router
from src.api.routes.test import router as test_router
from src.api.routes.status import router as status_router
from src.api.routes.first_login import router as first_login_router
from src.api.routes.admin import router as admin_router
from src.api.routes.admin_first_login import router as admin_first_login_router
from src.api.routes.admin_enhanced import router as admin_enhanced_router
from src.api.routes.admin_comprehensive import router as admin_comprehensive_router
from src.api.routes.player_combat import router as player_combat_router
from src.api.routes.events import router as events_router
from src.api.routes.websocket import router as websocket_router
from src.api.routes.trading import router as trading_router
from src.api.routes.mining import router as mining_router
from src.api.routes.player import router as player_router
from src.api.routes.sectors import router as sectors_router
from src.api.routes.ai import router as ai_router
from src.api.routes.enhanced_ai import router as enhanced_ai_router
from src.api.routes.route_optimizer import router as route_optimizer_router  # WO-RO1
from src.api.routes.market_prediction import router as market_prediction_router  # WO-MP1
from src.api.routes.audit import router as audit_router
from src.api.routes.messages import router as messages_router
from src.api.routes.admin_messages import router as admin_messages_router
from src.api.routes.factions import router as factions_router
from src.api.routes.admin_factions import router as admin_factions_router
from src.api.routes.drones import router as drones_router
from src.api.routes.admin_drones import router as admin_drones_router
from src.api.routes.fleets import router as fleets_router
from src.api.routes.admin_fleets import router as admin_fleets_router
from src.api.routes.planets import router as planets_router
from src.api.routes.planet_grid import router as planet_grid_router
from src.api.routes.pioneer import router as pioneer_router
from src.api.routes.teams import router as teams_router
from src.api.routes.admin_economy import router as admin_economy_router
from src.api.routes.admin_combat import router as admin_combat_router
from src.api.routes.admin_ships import router as admin_ships_router
from src.api.routes.admin_colonization import router as admin_colonization_router
from src.api.routes.mfa import router as mfa_router
from src.api.routes.paypal import router as paypal_router
from src.api.routes.nexus import router as nexus_router
from src.api.routes.regional_governance import router as regional_governance_router
from src.api.routes.translation import router as translation_router
from src.api.routes.enhanced_websocket import router as enhanced_websocket_router
from src.api.routes.debug import router as debug_router
from src.api.routes.gambling import router as gambling_router
from src.api.routes.genesis import router as genesis_router
from src.api.routes.ship_upgrades import router as ship_upgrades_router
from src.api.routes.hangar import router as hangar_router
from src.api.routes.tow import router as tow_router
from src.api.routes.armory import router as armory_router
from src.api.routes.registry import router as registry_router
from src.api.routes.bang_galaxy import router as bang_galaxy_router
from src.api.routes.construction import router as construction_router
from src.api.routes.port_ownership import router as port_ownership_router
from src.api.routes.station_security import router as station_security_router
from src.api.routes.ranking import router as ranking_router
from src.api.routes.quantum import router as quantum_router
from src.api.routes.refining import router as refining_router
from src.api.routes.recovery import router as recovery_router
from src.api.routes.warp_gates import router as warp_gates_router
from src.api.routes.nav import router as nav_router
from src.api.routes.medals import router as medals_router
from src.api.routes.haggle import router as haggle_router
from src.api.routes.research_cockpit import router as research_cockpit_router
from src.api.routes.black_market import router as black_market_router
from src.api.routes.resources import router as resources_router  # WO-ARCH-RES-1-KERNEL (router carries /resources prefix)
from src.api.routes.pirate_ecosystem import router as pirate_ecosystem_router  # WO-PIRATE-ECO-1
from src.api.routes.contracts import router as contracts_router  # WO-ECON-CONTRACT-1-KERNEL
from src.api.routes.admin_contract_disputes import router as admin_contract_disputes_router  # WO-CONTRACT-6
from src.api.routes.beacons import router as beacons_router  # WO-P4-play-beacon-kernel
from src.api.routes.storage import router as storage_router  # WO-STORE-DEPOSIT-FLOW
from src.api.routes.intrasystem import router as intrasystem_router  # WO-ISP
from src.api.routes.admin_reports import router as admin_reports_router  # WO-PADMIN-analytics
from src.core.config import settings

# Main API router - note that the version is now in the main API_V1_STR prefix
# so we don't need to add 'v1' in the router prefixes here
api_router = APIRouter()

# Include status router first - these endpoints don't require authentication
api_router.include_router(status_router, prefix="/status", tags=["status"])

# Include all authenticated route modules here
api_router.include_router(auth_router, prefix="/auth", tags=["auth"])
api_router.include_router(users_router, prefix="/users", tags=["users"])
api_router.include_router(first_login_router, prefix="/first-login", tags=["first-login"])
api_router.include_router(admin_router, prefix="/admin", tags=["admin"])
api_router.include_router(admin_first_login_router, tags=["admin-first-login"])
api_router.include_router(admin_enhanced_router, prefix="/admin", tags=["admin-enhanced"])
api_router.include_router(admin_comprehensive_router, prefix="/admin", tags=["admin-comprehensive"])
# NOTE: the legacy combat.py / economy.py routers were deleted — they were
# mounted before admin_combat.py / admin_economy.py and shadowed the working
# /admin/combat/* and /admin/economy/* implementations with broken handlers.
# Their still-working endpoints (/stats, /{id}/resolve, /metrics fallback,
# /create-alert, DELETE /alerts/{id}) were ported into the admin_* routers.
api_router.include_router(player_combat_router, tags=["player-combat"])
api_router.include_router(events_router, tags=["events"])
api_router.include_router(websocket_router, tags=["websocket"])
api_router.include_router(trading_router, tags=["trading"])
api_router.include_router(mining_router, tags=["mining"])  # WO-MINING (router carries /mining prefix)
api_router.include_router(haggle_router, tags=["haggle"])  # ADR-0079 numerical haggling (router carries /haggle prefix)
api_router.include_router(player_router, tags=["player"])
api_router.include_router(sectors_router, tags=["sectors"])
api_router.include_router(ai_router, tags=["ai-trading"])
api_router.include_router(enhanced_ai_router, tags=["enhanced-ai"])
api_router.include_router(route_optimizer_router, tags=["routes"])  # WO-RO1 (router carries /routes prefix)
api_router.include_router(market_prediction_router, tags=["market"])  # WO-MP1 (router carries /market-prediction prefix)
api_router.include_router(audit_router, tags=["audit"])
api_router.include_router(messages_router, tags=["messages"])
api_router.include_router(admin_messages_router, tags=["admin-messages"])
api_router.include_router(factions_router, tags=["factions"])
api_router.include_router(admin_factions_router, tags=["admin-factions"])
api_router.include_router(drones_router, tags=["drones"])
api_router.include_router(admin_drones_router, tags=["admin-drones"])
api_router.include_router(fleets_router, tags=["fleets"])
api_router.include_router(admin_fleets_router, tags=["admin-fleets"])
api_router.include_router(planets_router, tags=["planets"])
api_router.include_router(planet_grid_router, tags=["planet-grid"])
api_router.include_router(pioneer_router, tags=["pioneer"])
api_router.include_router(teams_router, tags=["teams"])
api_router.include_router(admin_economy_router, tags=["admin-economy"])
api_router.include_router(admin_combat_router, tags=["admin-combat"])
api_router.include_router(admin_ships_router, tags=["admin-ships"])
api_router.include_router(admin_colonization_router, prefix="/admin", tags=["admin-colonization"])
api_router.include_router(mfa_router, tags=["mfa"])
api_router.include_router(paypal_router, tags=["paypal"])
api_router.include_router(nexus_router, tags=["nexus"])
api_router.include_router(regional_governance_router, tags=["regional-governance"])
api_router.include_router(translation_router, tags=["translation"])
api_router.include_router(enhanced_websocket_router, tags=["websocket", "real-time"])
api_router.include_router(debug_router, tags=["debug"])
api_router.include_router(gambling_router, tags=["gambling"])
api_router.include_router(genesis_router, tags=["genesis"])
api_router.include_router(ship_upgrades_router, tags=["ship-upgrades"])
# Carrier ship-hangar (WO-AE): dock-request/accept/undock/disembark consent flow
# (router carries its own /hangar prefix)
api_router.include_router(hangar_router, tags=["hangar"])
# Tractor Beam tow (WO-AF): request/accept/cancel/detach consent flow
# (router carries its own /tow prefix; the per-move surcharge rides the normal
# movement / quantum endpoints via MovementService / quantum_service)
api_router.include_router(tow_router, tags=["tow"])
api_router.include_router(armory_router, tags=["armory"])
# Black-market planet registry lookup (router carries its own /registry prefix)
api_router.include_router(registry_router, tags=["registry"])
# TradeDock ship construction (router carries its own /construction prefix)
api_router.include_router(construction_router, tags=["construction"])
# Port ownership: listings/auctions, owner powers, economic takeover
# (router carries its own /port-ownership prefix)
api_router.include_router(port_ownership_router, tags=["port-ownership"])
# Station-protection security-tier ladder: upgrade/downgrade/status
# (WO-STN-SEC-1; router carries its own /station-security prefix)
api_router.include_router(station_security_router, tags=["station-security"])
# Military ranking, medals, reputation, and bounties
# (router carries its own /ranking prefix)
api_router.include_router(ranking_router, tags=["ranking"])
api_router.include_router(medals_router, tags=["medals"])  # ADR-0028 relational medals (router carries /medals prefix)
# Citadel Research notification cockpit (CRT-T1.5-9 / CRT-4): empire R&D summary +
# generated perishable directive offers + start/cancel (router carries /research prefix)
api_router.include_router(research_cockpit_router, tags=["research-cockpit"])
# Quantum drive: shard/crystal/charge inventory, directional scan + jump
# (router carries its own /quantum prefix — no other router claims /quantum,
# so no mount-order shadowing)
api_router.include_router(quantum_router, tags=["quantum"])
# Shard → Crystal refining (5 Shards + 10,000 cr → 1 Quantum Crystal): the
# ONLY player-driven source of Quantum Crystals (router carries its own
# /refining prefix; distinct from the 1:1 /quantum/refine-charge jump charge)
api_router.include_router(refining_router, tags=["refining"])
# One-way-stranding recovery: Federation distress beacon (any hull, -10 rep,
# 24h cooldown) + Warp Jumper Slipdrive (quantum_jump_capable hulls, charge +
# fuel-scaled escape) (router carries its own /recovery prefix)
api_router.include_router(recovery_router, tags=["recovery"])
# Player warp gates: three-phase construction ritual (ADR-0029) + sector
# structure listing (router carries its own /warp-gates prefix; traversal
# itself rides the normal /player/move endpoints via MovementService)
api_router.include_router(warp_gates_router, tags=["warp-gates"])
# ADR-0072 Phase 1 — course plotting through the player's known graph
# (router carries its own /nav prefix)
api_router.include_router(nav_router, tags=["nav"])
# Bang galaxy generator admin endpoints (Phase 1C of bang-integration plan).
# The legacy `/admin/galaxy/generate` route in admin.py stays intact; Phase 4
# removes it in favour of the new job-based flow defined here.
api_router.include_router(bang_galaxy_router, prefix="/admin", tags=["admin-bang"])
# Black-market contraband trading (WO-BLACKMARKET kernel): gated catalog + buy +
# sell with detection roll (router carries its own /trading prefix → endpoints at
# /trading/black-market/...)
api_router.include_router(black_market_router, tags=["black-market"])
# Resource registry catalog (WO-ARCH-RES-1-KERNEL): read-only seeded canon
# resource list (router carries its own /resources prefix → GET /resources).
api_router.include_router(resources_router, tags=["resources"])
# Pirate ecosystem read API (WO-PIRATE-ECO-1): region-scoped population/
# target/cleansed-state snapshot (router carries its own /regions prefix →
# GET /regions/{region_id}/pirate-ecosystem).
api_router.include_router(pirate_ecosystem_router, tags=["pirate-ecosystem"])
# Trade contracts (WO-ECON-CONTRACT-1-KERNEL): board/mine/{id} reads +
# accept/complete/abandon on NPC-issued cargo_delivery contracts (router
# carries its own /contracts prefix). Player-issued posting, insurance,
# bulk-partial deliver, cancel, and disputes are later build steps.
api_router.include_router(contracts_router, tags=["contracts"])
api_router.include_router(admin_contract_disputes_router, tags=["admin-contract-disputes"])
# Message beacons (WO-P4-play-beacon-kernel): deploy/read/salvage a
# physical "message in a bottle" in a sector (router carries its own
# /beacons prefix).
api_router.include_router(beacons_router, tags=["beacons"])
# Storage lockers (WO-STORE-DEPOSIT-FLOW): rent a locker at a contract's
# destination station, deposit cargo in installments, auto-complete on
# full quantity (router carries its own /storage prefix).
api_router.include_router(storage_router, tags=["storage"])
# Intra-system helm (WO-ISP): authoritative burn/halt/pose (router carries
# /helm/intrasystem prefix).
api_router.include_router(intrasystem_router, tags=["intrasystem"])
api_router.include_router(admin_reports_router, tags=["admin-reports"])  # WO-PADMIN-analytics

# Only include test routes in development/test environments
if settings.TESTING or settings.DEVELOPMENT_MODE:
    api_router.include_router(test_router, prefix="/test", tags=["test"])

# Add additional routers here as they are created
# Example:
# api_router.include_router(game_router, prefix="/game", tags=["game"])