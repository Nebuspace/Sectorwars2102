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
from src.api.routes.player import router as player_router
from src.api.routes.sectors import router as sectors_router
from src.api.routes.ai import router as ai_router
from src.api.routes.enhanced_ai import router as enhanced_ai_router
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
from src.api.routes.armory import router as armory_router
from src.api.routes.registry import router as registry_router
from src.api.routes.bang_galaxy import router as bang_galaxy_router
from src.api.routes.construction import router as construction_router
from src.api.routes.port_ownership import router as port_ownership_router
from src.api.routes.ranking import router as ranking_router
from src.api.routes.quantum import router as quantum_router
from src.api.routes.warp_gates import router as warp_gates_router
from src.api.routes.nav import router as nav_router
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
api_router.include_router(player_router, tags=["player"])
api_router.include_router(sectors_router, tags=["sectors"])
api_router.include_router(ai_router, tags=["ai-trading"])
api_router.include_router(enhanced_ai_router, tags=["enhanced-ai"])
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
api_router.include_router(armory_router, tags=["armory"])
# Black-market planet registry lookup (router carries its own /registry prefix)
api_router.include_router(registry_router, tags=["registry"])
# TradeDock ship construction (router carries its own /construction prefix)
api_router.include_router(construction_router, tags=["construction"])
# Port ownership: listings/auctions, owner powers, economic takeover
# (router carries its own /port-ownership prefix)
api_router.include_router(port_ownership_router, tags=["port-ownership"])
# Military ranking, medals, reputation, and bounties
# (router carries its own /ranking prefix)
api_router.include_router(ranking_router, tags=["ranking"])
# Quantum drive: shard/crystal/charge inventory, directional scan + jump
# (router carries its own /quantum prefix — no other router claims /quantum,
# so no mount-order shadowing)
api_router.include_router(quantum_router, tags=["quantum"])
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

# Only include test routes in development/test environments
if settings.TESTING or settings.DEVELOPMENT_MODE:
    api_router.include_router(test_router, prefix="/test", tags=["test"])

# Add additional routers here as they are created
# Example:
# api_router.include_router(game_router, prefix="/game", tags=["game"])