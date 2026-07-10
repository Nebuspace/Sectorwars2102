// Real API service for gameserver endpoints
import { isAxiosError } from 'axios';
import apiClient from './apiClient';

// Helper function for API requests.
//
// Delegates to the shared apiClient (axios) so every call gets the
// centralized JWT refresh-on-401 behavior. The external contract is
// unchanged: returns the parsed response body, throws
// Error(detail || `API Error: <status>`) on failure.
async function apiRequest(
  endpoint: string,
  options: RequestInit = {}
): Promise<any> {
  try {
    const response = await apiClient.request({
      url: endpoint,
      method: (options.method || 'GET') as string,
      // Call sites pass pre-stringified JSON bodies; forward as-is.
      data: options.body,
      headers: {
        'Content-Type': 'application/json',
        ...(options.headers as Record<string, string>)
      }
    });
    return response.data;
  } catch (error) {
    if (isAxiosError(error) && error.response) {
      const data: any = error.response.data;
      // Surface the server's human message. Native FastAPI HTTPExceptions use
      // `detail` (a string; 422 validation makes it an array — skip those), but
      // this gameserver's global error handler wraps errors as `{message}`.
      // Prefer a string `detail`, fall back to `message`, then a generic code.
      const msg = data && typeof data === 'object'
        ? (typeof data.detail === 'string' ? data.detail : undefined) || data.message
        : undefined;
      throw new Error(msg || `API Error: ${error.response.status}`);
    }
    // Network-level failure (no response) – rethrow like fetch would.
    throw error;
  }
}

// Combat APIs
export const combatAPI = {
  engage: (targetType: 'ship' | 'planet' | 'port', targetId: string) =>
    apiRequest('/api/v1/combat/engage', {
      method: 'POST',
      body: JSON.stringify({ targetType, targetId })
    }),

  getStatus: (combatId: string) =>
    apiRequest(`/api/v1/combat/${combatId}/status`),

  retreat: (combatId: string) =>
    apiRequest(`/api/v1/combat/${combatId}/retreat`, { method: 'POST' }),

  // Drone management
  deployDrones: (sectorId: string, droneCount: number) =>
    apiRequest('/api/v1/drones/deploy', {
      method: 'POST',
      body: JSON.stringify({ sectorId, droneCount })
    }),

  getDeployedDrones: () =>
    apiRequest('/api/v1/drones/deployed'),

  recallDrones: (deploymentId: string) =>
    apiRequest(`/api/v1/drones/${deploymentId}/recall`, {
      method: 'DELETE'
    })
};

// Planetary Management APIs
export const planetaryAPI = {
  getOwnedPlanets: () =>
    apiRequest('/api/v1/planets/owned'),

  getPlanet: (planetId: string) =>
    apiRequest(`/api/v1/planets/${planetId}`),

  allocateColonists: (planetId: string, allocations: { fuel: number, organics: number, equipment: number }) =>
    apiRequest(`/api/v1/planets/${planetId}/allocate`, {
      method: 'PUT',
      body: JSON.stringify(allocations)
    }),

  upgradeBuilding: (planetId: string, buildingType: string, targetLevel: number) =>
    apiRequest(`/api/v1/planets/${planetId}/buildings/upgrade`, {
      method: 'POST',
      body: JSON.stringify({ buildingType, targetLevel })
    }),

  // The wire contract is turrets/shields/fighters (backend DefenseUpdateRequest);
  // Pydantic silently drops unknown keys, so a `drones` field here would be
  // discarded server-side. The UI labels fighters as "Drones" (canon naming).
  updateDefenses: (planetId: string, defenses: { turrets?: number, shields?: number, fighters?: number }) =>
    apiRequest(`/api/v1/planets/${planetId}/defenses`, {
      method: 'PUT',
      body: JSON.stringify(defenses)
    }),

  // planetType is rolled server-side from the device tier (ADR-0014); it is
  // accepted but ignored. tier: basic (1 device), enhanced (3 devices), or
  // advanced (1 device + the Colony Ship is sacrificed for an instant colony).
  // registration controls the new world's visibility on the public registry and
  // its Federation legal status (server is authoritative on the fee charged):
  //   registered (default) — on the charts in your name, no Fed protection
  //   clandestine          — off the registry, no Fed protection
  //   chartered            — Fed legal protection, fee scales down with reputation
  deployGenesis: (
    sectorId: string,
    planetName: string,
    tier: 'basic' | 'enhanced' | 'advanced' = 'basic',
    registration: 'clandestine' | 'registered' | 'chartered' = 'registered'
  ) =>
    apiRequest('/api/v1/planets/genesis/deploy', {
      method: 'POST',
      body: JSON.stringify({ sectorId, planetName, tier, registration })
    }),

  specializePlanet: (planetId: string, specialization: string) =>
    apiRequest(`/api/v1/planets/${planetId}/specialize`, {
      method: 'PUT',
      body: JSON.stringify({ specialization })
    }),

  getSiegeStatus: (planetId: string) =>
    apiRequest(`/api/v1/planets/${planetId}/siege-status`)
};

// Team Management APIs
export const teamAPI = {
  // Team operations
  getTeam: (teamId: string) =>
    apiRequest(`/api/v1/teams/${teamId}`),

  // Body uses the backend CreateTeamRequest field names (teams.py): camelCase
  // keys were silently dropped by pydantic, losing the recruitment choice.
  createTeam: (data: {
    name: string;
    tag?: string;
    description?: string;
    max_members?: number;
    recruitment_status: string;
  }) =>
    apiRequest('/api/v1/teams/create', {
      method: 'POST',
      body: JSON.stringify(data)
    }),

  // Subset of the backend UpdateTeamRequest fields (teams.py)
  updateTeam: (teamId: string, updates: {
    description?: string;
    tag?: string;
    logo?: string;
    recruitment_status?: string;
    max_members?: number;
  }) =>
    apiRequest(`/api/v1/teams/${teamId}`, {
      method: 'PUT',
      body: JSON.stringify(updates)
    }),

  disbandTeam: (teamId: string) =>
    apiRequest(`/api/v1/teams/${teamId}`, {
      method: 'DELETE'
    }),

  // Member management
  getMembers: (teamId: string) =>
    apiRequest(`/api/v1/teams/${teamId}/members`),

  // Backend InvitePlayerRequest resolves the invitee by nickname (teams.py)
  inviteMember: (teamId: string, playerNickname: string) =>
    apiRequest(`/api/v1/teams/${teamId}/invite`, {
      method: 'POST',
      body: JSON.stringify({ player_nickname: playerNickname })
    }),

  kickMember: (teamId: string, memberId: string, reason?: string) =>
    apiRequest(`/api/v1/teams/${teamId}/members/${memberId}`, {
      method: 'DELETE',
      body: JSON.stringify({ reason })
    }),

  // Backend UpdateRoleRequest expects { new_role } with TeamRole enum values
  promoteMember: (teamId: string, memberId: string, newRole: 'OFFICER' | 'MEMBER' | 'RECRUIT') =>
    apiRequest(`/api/v1/teams/${teamId}/members/${memberId}/role`, {
      method: 'PUT',
      body: JSON.stringify({ new_role: newRole })
    }),

  // Server resolves the player's own membership; teamId kept for call-site symmetry
  leaveTeam: (_teamId?: string) =>
    apiRequest('/api/v1/teams/leave', { method: 'POST' }),

  // Team chat
  getMessages: (teamId: string, limit?: number, before?: string) => {
    const params = new URLSearchParams();
    if (limit) params.append('limit', limit.toString());
    if (before) params.append('before', before);
    return apiRequest(`/api/v1/teams/${teamId}/messages?${params}`);
  },

  // Backend SendMessageRequest (teams.py) requires `subject` (str) alongside
  // content; priority defaults to "normal". Chat has no subject concept, so a
  // short slice of the content stands in as the subject.
  sendMessage: (teamId: string, content: string, priority: string = 'normal') =>
    apiRequest(`/api/v1/teams/${teamId}/messages`, {
      method: 'POST',
      body: JSON.stringify({ subject: (content.length > 80 ? content.slice(0, 77) + '…' : content) || 'Team message', content, priority })
    }),

  // Treasury — the backend ops are per-resource-type ({resource_type, amount}),
  // not a multi-resource object (team_service deposit/withdraw/transfer_to_player).
  // Only `credits` and `quantum_crystals` are player-transferable
  // (PLAYER_TRANSFERABLE_RESOURCES whitelist); other columns are server-fed only.
  getTreasuryBalance: (teamId: string) =>
    apiRequest(`/api/v1/teams/${teamId}/treasury`),

  // Newest-first, paginated ledger of every treasury mutation.
  getTreasuryHistory: (teamId: string, skip = 0, limit = 25) =>
    apiRequest(`/api/v1/teams/${teamId}/treasury/history?skip=${skip}&limit=${limit}`),

  depositToTreasury: (teamId: string, resourceType: string, amount: number) =>
    apiRequest(`/api/v1/teams/${teamId}/treasury/deposit`, {
      method: 'POST',
      body: JSON.stringify({ resource_type: resourceType, amount })
    }),

  withdrawFromTreasury: (teamId: string, resourceType: string, amount: number) =>
    apiRequest(`/api/v1/teams/${teamId}/treasury/withdraw`, {
      method: 'POST',
      body: JSON.stringify({ resource_type: resourceType, amount })
    }),

  // Treasury -> member transfer. Backend TransferRequest resolves recipient by
  // nickname; correct path is /treasury/transfer (was /transfer — 404'd).
  transferTreasury: (teamId: string, recipientNickname: string, resourceType: string, amount: number) =>
    apiRequest(`/api/v1/teams/${teamId}/treasury/transfer`, {
      method: 'POST',
      body: JSON.stringify({ recipient_nickname: recipientNickname, resource_type: resourceType, amount })
    }),

  // Mission management
  getMissions: (teamId: string) =>
    apiRequest(`/api/v1/teams/${teamId}/missions`),

  createMission: (teamId: string, mission: any) =>
    apiRequest(`/api/v1/teams/${teamId}/missions`, {
      method: 'POST',
      body: JSON.stringify(mission)
    }),

  updateMission: (teamId: string, missionId: string, updates: any) =>
    apiRequest(`/api/v1/teams/${teamId}/missions/${missionId}`, {
      method: 'PUT',
      body: JSON.stringify(updates)
    }),

  joinMission: (teamId: string, missionId: string) =>
    apiRequest(`/api/v1/teams/${teamId}/missions/${missionId}/join`, {
      method: 'POST'
    }),

  leaveMission: (teamId: string, missionId: string) =>
    apiRequest(`/api/v1/teams/${teamId}/missions/${missionId}/leave`, {
      method: 'DELETE'
    }),

  // Alliance & Diplomacy (Phase 3 - may not be implemented yet)
  getAlliances: (teamId: string) =>
    apiRequest(`/api/v1/teams/${teamId}/alliances`),

  getDiplomaticRelations: (teamId: string) =>
    apiRequest(`/api/v1/teams/${teamId}/relations`),

  proposeAlliance: (teamId: string, data: any) =>
    apiRequest(`/api/v1/teams/${teamId}/alliances/propose`, {
      method: 'POST',
      body: JSON.stringify(data)
    }),

  proposeTreaty: (teamId: string, data: any) =>
    apiRequest(`/api/v1/teams/${teamId}/treaties/propose`, {
      method: 'POST',
      body: JSON.stringify(data)
    }),

  changeDiplomaticRelation: (teamId: string, targetTeamId: string, type: string) =>
    apiRequest(`/api/v1/teams/${teamId}/relations/${targetTeamId}`, {
      method: 'PUT',
      body: JSON.stringify({ type })
    }),

  leaveAlliance: (teamId: string, allianceId: string) =>
    apiRequest(`/api/v1/teams/${teamId}/alliances/${allianceId}`, {
      method: 'DELETE'
    }),

  // Analytics
  getTeamAnalytics: (teamId: string, period: 'day' | 'week' | 'month' | 'all-time') =>
    apiRequest(`/api/v1/teams/${teamId}/analytics?period=${period}`),

  // Permissions
  getPermissions: (teamId: string) =>
    apiRequest(`/api/v1/teams/${teamId}/permissions`),

  // Canon gap: GET /api/v1/teams does not exist on the backend. Still bound
  // because DiplomacyInterface/AllianceManager call it; their fetches fail at
  // runtime today. Remove together with those call sites.
  getAvailableTeams: () =>
    apiRequest('/api/v1/teams')
};

// Fleet Management APIs
export const fleetAPI = {
  createFleet: (name: string, formation?: string, commanderId?: string) =>
    apiRequest('/api/v1/fleets', {
      method: 'POST',
      body: JSON.stringify({ name, formation, commander_id: commanderId })
    }),

  getFleets: () =>
    apiRequest('/api/v1/fleets'),

  getFleet: (fleetId: string) =>
    apiRequest(`/api/v1/fleets/${fleetId}`),

  addShipToFleet: (fleetId: string, shipId: string, role?: string) =>
    apiRequest(`/api/v1/fleets/${fleetId}/add-ship`, {
      method: 'POST',
      body: JSON.stringify({ ship_id: shipId, role })
    }),

  removeShipFromFleet: (fleetId: string, shipId: string) =>
    apiRequest(`/api/v1/fleets/${fleetId}/remove-ship/${shipId}`, {
      method: 'DELETE'
    }),

  updateFormation: (fleetId: string, formation: string) =>
    apiRequest(`/api/v1/fleets/${fleetId}/formation?formation=${formation}`, {
      method: 'PATCH'
    }),

  disbandFleet: (fleetId: string) =>
    apiRequest(`/api/v1/fleets/${fleetId}`, {
      method: 'DELETE'
    }),

  initiateBattle: (fleetId: string, defenderFleetId: string) =>
    apiRequest(`/api/v1/fleets/${fleetId}/initiate-battle`, {
      method: 'POST',
      body: JSON.stringify({ defender_fleet_id: defenderFleetId })
    }),

  simulateBattleRound: (battleId: string) =>
    apiRequest(`/api/v1/fleets/battles/${battleId}/simulate-round`, {
      method: 'POST'
    }),

  getBattles: (activeOnly?: boolean) => {
    const params = activeOnly ? '?active_only=true' : '';
    return apiRequest(`/api/v1/fleets/battles${params}`);
  }
};

// Faction APIs
export const factionAPI = {
  getFactions: () =>
    apiRequest('/api/v1/factions/'),

  getReputation: () =>
    apiRequest('/api/v1/factions/reputation'),

  getFactionReputation: (factionId: string) =>
    apiRequest(`/api/v1/factions/${factionId}/reputation`),

  getTerritory: (factionId: string) =>
    apiRequest(`/api/v1/factions/${factionId}/territory`),

  getPricingModifier: (factionId: string) =>
    apiRequest(`/api/v1/factions/${factionId}/pricing-modifier`)
};

// Message APIs
export const messageAPI = {
  sendMessage: (recipientId: string, content: string, subject?: string) =>
    apiRequest('/api/v1/messages/send', {
      method: 'POST',
      // Backend MessageCreateRequest expects snake_case fields
      body: JSON.stringify({ recipient_id: recipientId, subject, content })
    }),

  getInbox: (page: number = 1, unreadOnly?: boolean) => {
    const params = new URLSearchParams({ page: page.toString() });
    // Backend query param is snake_case: unread_only
    if (unreadOnly) params.append('unread_only', 'true');
    return apiRequest(`/api/v1/messages/inbox?${params}`);
  },

  markAsRead: (messageId: string) =>
    apiRequest(`/api/v1/messages/${messageId}/read`, {
      method: 'PUT'
    }),

  deleteMessage: (messageId: string) =>
    apiRequest(`/api/v1/messages/${messageId}`, {
      method: 'DELETE'
    }),

  getTeamMessages: (teamId: string, page: number = 1) =>
    apiRequest(`/api/v1/messages/team/${teamId}?page=${page}`)
};

// Ship APIs (partial - may need enhancement)
export const shipAPI = {
  getShips: () =>
    apiRequest('/api/v1/ships'), // Endpoint may vary

  getShip: (shipId: string) =>
    apiRequest(`/api/v1/ships/${shipId}`),

  updateShip: (shipId: string, updates: any) =>
    apiRequest(`/api/v1/ships/${shipId}`, {
      method: 'PUT',
      body: JSON.stringify(updates)
    }),

  // Condition + performance band + repair quotes (ships.md maintenance).
  getMaintenanceStatus: (shipId: string) =>
    apiRequest(`/api/v1/ships/${shipId}/maintenance`),

  // Service the hull back to 100% condition at a shipyard. tier: basic|emergency|premium.
  repairMaintenance: (shipId: string, tier: string) =>
    apiRequest(`/api/v1/ships/${shipId}/maintenance/repair`, {
      method: 'POST',
      body: JSON.stringify({ tier })
    }),

  getInsurance: (shipId: string) =>
    apiRequest(`/api/v1/ships/${shipId}/insurance`),

  // Backend expects {tier}: BASIC | STANDARD | PREMIUM (ADR-0081). Premium is
  // paid upfront; upgrades cost the difference. No claims/cancellation (canon).
  purchaseInsurance: (shipId: string, tier: string) =>
    apiRequest(`/api/v1/ships/${shipId}/insurance`, {
      method: 'POST',
      body: JSON.stringify({ tier })
    }),

  // Ship upgrade/equipment calls live on shipUpgradeAPI (below), which matches
  // the real /upgrades, /upgrades/purchase and /equipment/* endpoint shapes.
  // Prefer shipUpgradeAPI for any new upgrade UI; this alias delegates to it so
  // a stale POST-to-the-GET-URL contract is never reintroduced here.
  getUpgrades: (shipId: string) =>
    shipUpgradeAPI.getUpgrades(shipId),

  purchaseUpgrade: (shipId: string, upgradeType: string) =>
    shipUpgradeAPI.purchaseUpgrade(shipId, upgradeType),

  // Back-compat alias: the old name posted to the wrong URL with the wrong body
  // shape. Delegates to the correct purchase endpoint so any lingering caller
  // works instead of 404-ing. `upgradeType` is the UpgradeType enum value.
  installUpgrade: (shipId: string, upgradeType: string) =>
    shipUpgradeAPI.purchaseUpgrade(shipId, upgradeType)
};

// Ranking & Reputation APIs
export const rankingAPI = {
  getRank: () =>
    apiRequest('/api/v1/ranking/rank'),

  getMedals: () =>
    apiRequest('/api/v1/ranking/medals'),

  getDefinitions: () =>
    apiRequest('/api/v1/ranking/definitions'),

  getReputation: () =>
    apiRequest('/api/v1/ranking/reputation'),

  getPublicLeaderboard: (category: string = 'rank_points', limit: number = 20) =>
    apiRequest(`/api/v1/ranking/leaderboard/public?category=${category}&limit=${limit}`),

  getProgress: () =>
    apiRequest('/api/v1/ranking/progress'),
};

// Bounty APIs
export const bountyAPI = {
  place: (targetId: string, amount: number) =>
    apiRequest('/api/v1/ranking/bounties/place', {
      method: 'POST',
      body: JSON.stringify({ target_id: targetId, amount }),
    }),

  getOnTarget: (playerId: string) =>
    apiRequest(`/api/v1/ranking/bounties/target/${playerId}`),

  getAvailable: (limit: number = 20) =>
    apiRequest(`/api/v1/ranking/bounties/available?limit=${limit}`),
};

// Citadel APIs
export const citadelAPI = {
  getInfo: (planetId: string) =>
    apiRequest(`/api/v1/planets/${planetId}/citadel`),

  upgrade: (planetId: string) =>
    apiRequest(`/api/v1/planets/${planetId}/citadel/upgrade`, { method: 'POST' }),

  deposit: (planetId: string, amount: number) =>
    apiRequest(`/api/v1/planets/${planetId}/citadel/deposit`, {
      method: 'POST',
      body: JSON.stringify({ amount }),
    }),

  withdraw: (planetId: string, amount: number) =>
    apiRequest(`/api/v1/planets/${planetId}/citadel/withdraw`, {
      method: 'POST',
      body: JSON.stringify({ amount }),
    }),

  // Toggle "auto-deposit production into safe" (opt-in, default OFF). When ON,
  // each read-path settle sweeps the planet stockpile into the protected safe
  // up to the shared cr-equivalent cap. Owner-only, requires citadel_level >= 1
  // (400 otherwise). Returns { success: true, auto_deposit: bool }.
  setAutoDeposit: (planetId: string, enabled: boolean) =>
    apiRequest(`/api/v1/planets/${planetId}/citadel/auto-deposit`, {
      method: 'POST',
      body: JSON.stringify({ enabled }),
    }),
};

// Planet Grid APIs (CRT-2) — the authoritative citadel grid the player manages.
//
// getGrid → the grid view: cols/rows + plots + placed buildings + derived
//   citadel_level/max_citadel_level (size cap) + the placeable catalog + the
//   owning player's researched-node set (so the UI can render names, costs, and
//   research-gating without a second round-trip). Exact payload shape is owned
//   by the gameserver (GET /grid); GridPanel reads it defensively.
// place → enqueue a building of `kind` on empty plot (x,y); the server charges
//   credits from the player (planet-row→player-row lock order), enforces the
//   research gate (403) and affordability (402); failures surface the server's
//   human message via apiRequest's error mapping.
// decommission → remove a placed building by id; the server credits the 0.25×
//   invested refund back to the player and returns { removed, refund_credits }.
export const gridAPI = {
  getGrid: (planetId: string) =>
    apiRequest(`/api/v1/planets/${planetId}/grid`),

  place: (planetId: string, kind: string, x: number, y: number, level: number = 1) =>
    apiRequest(`/api/v1/planets/${planetId}/grid/place`, {
      method: 'POST',
      body: JSON.stringify({ kind, x, y, level }),
    }),

  decommission: (planetId: string, buildingId: string) =>
    apiRequest(`/api/v1/planets/${planetId}/grid/decommission`, {
      method: 'POST',
      body: JSON.stringify({ building_id: buildingId }),
    }),
};

// Terraforming capstone (CRT grid). The confirm-biome ACTION reclassifies
// planet.type (BARREN -> VOLCANIC, ICE -> DESERT) once the area-weighted grid
// axes have held inside the target biome's band for CAPSTONE_HOLD_TICKS.
// 400 carries a friendly server message (e.g. "biome must hold 24 ticks (held 7)").
export const terraformAPI = {
  confirmBiome: (planetId: string) =>
    apiRequest(`/api/v1/planets/${planetId}/terraforming/confirm-biome`, {
      method: 'POST',
    }),
};

// Citadel Research APIs (CRT-T1.5-9 / CRT-4 — the empire R&D notification cockpit).
//
// Player-facing brand: "Citadel Research" (Max-ruled). These read the now-live
// governed-flywheel economy (the governor + contract sink + faucet copay) and
// surface the generated, perishable Research-Directive OFFERS. The offers are
// PUSHED by the server (contract_offer WS frame) and reacted to here — this is a
// generated, never-browsed pipeline (a done/uncontested world raises no offer).
//
//   getCockpit  → the empire R&D summary + headroom (§5.4/§5.5). One empire-level
//     read: { rpPerDay, rpThroughputPct, banked, spent, contractsActive,
//             worldsFrontier, worldsDone, governorHeadroom, softCap }.
//   getOffers   → the generated, perishable offers (§5.7), NEVER a catalogue:
//     { offers: [{ id, kind, planetId, planetName, rpCost, crCost, magnitude,
//                  expiresAt }] }.
//   startContract → accept an offer / start a kind on a planet (charges the RP
//     gate + cr sink via the existing start_contract). { offerId?, kind?, planetId }.
//   cancelContract → cancel an active/accepted contract (existing cancel_contract;
//     0% cr on active, 0% RP — the anti-arbitrage refund rule). { contractId }.
//   unlockNode → spend banked RP on a tech_tree node (WO-PLN-UNLOCK-1). Response
//     is minimal ({ success, nodeId, bankedRp, unlockedNodes, message }); the
//     caller re-fetches getCockpit() for the refreshed per-node techTree state
//     (an additive field on the cockpit payload above), same as the existing
//     post-startContract refresh pattern.
export const researchCockpitAPI = {
  getCockpit: () =>
    apiRequest('/api/v1/research/cockpit'),

  getOffers: () =>
    apiRequest('/api/v1/research/offers'),

  // Accept a generated offer (offerId) OR start a kind directly (kind + planetId).
  startContract: (params: { offerId?: string; kind?: string; planetId: string }) =>
    apiRequest('/api/v1/research/contracts/start', {
      method: 'POST',
      body: JSON.stringify(params),
    }),

  cancelContract: (contractId: string) =>
    apiRequest('/api/v1/research/contracts/cancel', {
      method: 'POST',
      body: JSON.stringify({ contractId }),
    }),

  unlockNode: (nodeId: string) =>
    apiRequest(`/api/v1/research/tech/${nodeId}/unlock`, {
      method: 'POST',
    }),
};

// Ship Upgrade APIs (real backend endpoints)
export const shipUpgradeAPI = {
  getUpgrades: (shipId: string) =>
    apiRequest(`/api/v1/ships/${shipId}/upgrades`),

  purchaseUpgrade: (shipId: string, upgradeType: string) =>
    apiRequest(`/api/v1/ships/${shipId}/upgrades/purchase`, {
      method: 'POST',
      body: JSON.stringify({ upgrade_type: upgradeType }),
    }),

  installEquipment: (shipId: string, equipmentKey: string) =>
    apiRequest(`/api/v1/ships/${shipId}/equipment/install`, {
      method: 'POST',
      body: JSON.stringify({ equipment_key: equipmentKey }),
    }),

  uninstallEquipment: (shipId: string, equipmentKey: string) =>
    apiRequest(`/api/v1/ships/${shipId}/equipment/uninstall`, {
      method: 'POST',
      body: JSON.stringify({ equipment_key: equipmentKey }),
    }),

  // SHIP-MODS (WO-SM-5): module slot-grid lattice + install/remove.
  //
  // getModules → { ship_id, ship_name, ship_type, module_slots, installed }
  //   module_slots: { v, cols, rows, slots:[{i,x,y,super,class,requires}] } | null
  //   installed:    { "<slot_i>": { class, tier, super_at_install, installed_at } }
  getModules: (shipId: string) =>
    apiRequest(`/api/v1/ships/${shipId}/modules`),

  // installModule → { success, module, supercharged, cost_paid, remaining_credits,
  //                   updated_stats, [consumer_inert] }. The deferred equipment
  //   families (harvester/lander/mining/tractor) return success:false +
  //   consumer_inert:true with a "not yet installable" message — surfaced as
  //   "coming soon" in the catalog so they never reach this call.
  installModule: (shipId: string, slotIndex: number, moduleClass: string, tier: number) =>
    apiRequest(`/api/v1/ships/${shipId}/modules/install`, {
      method: 'POST',
      body: JSON.stringify({ slot_index: slotIndex, module_class: moduleClass, tier }),
    }),

  // removeModule → { success, refund, remaining_credits, updated_stats }. The
  //   refund is SALVAGE_FRACTION (~25%) of the module's tier-scaled cost.
  removeModule: (shipId: string, slotIndex: number) =>
    apiRequest(`/api/v1/ships/${shipId}/modules/remove`, {
      method: 'POST',
      body: JSON.stringify({ slot_index: slotIndex }),
    }),

  // WO-GC-B: Galactic-Citizen L1 cosmetics (zero-stat overlay).
  // getCosmetics → { success, catalog, applied, is_galactic_citizen }
  getCosmetics: (shipId: string) =>
    apiRequest(`/api/v1/ships/${shipId}/cosmetics`),

  // setCosmetic → { success, message, cosmetics }. value=null clears the slot.
  //   403 when the caller lacks an active Galactic Citizen membership.
  setCosmetic: (shipId: string, slot: string, value: string | null) =>
    apiRequest(`/api/v1/ships/${shipId}/cosmetics`, {
      method: 'POST',
      body: JSON.stringify({ slot, value }),
    }),
};

// Planetary Registry APIs (shadow-broker lookup of another player's holdings).
// 403 if the caller's personal_reputation >= 0 (only those off the books may
// query); 404 (no charge) if the name is unknown; an empty planets list (no
// charge) if the target has no non-clandestine worlds; otherwise the server
// charges 50,000 cr. Clandestine worlds never appear in the result.
export const registryAPI = {
  lookup: (playerName: string) =>
    apiRequest('/api/v1/registry/lookup', {
      method: 'POST',
      body: JSON.stringify({ playerName })
    })
};

// Resource Registry — read-only catalog of the 13 canon resources
// (WO-ARCH-RES-1-KERNEL / WO-ARCH-RES-3-FE-CATALOG). Consumed through
// services/resourceCatalog.ts, which fetches + caches this once per session.
export const resourceAPI = {
  list: () => apiRequest('/api/v1/resources'),
};

// Navigation — the cockpit NAV CHART's known-graph surface (WO-PUX-NAVCHART).
// GET /api/v1/nav/chart returns the player's KNOWN sectors (visited ∪
// corp-shared ∪ current — course-plotting.md), the warp/tunnel edges between
// them, and id-only frontier stubs for adjacent-but-unknown sectors. Course
// plotting/engagement itself stays on AutopilotContext's own apiClient.post
// call to POST /api/v1/nav/plot (unchanged by this WO).
export interface NavChartSector {
  sector_id: number;
  name: string;
  type: string;
  x: number;
  y: number;
  z: number;
  visited: boolean;
  current: boolean;
}

export interface NavChartEdge {
  from: number;
  to: number;
  kind: 'warp' | 'tunnel';
}

export interface NavChartResponse {
  sectors: NavChartSector[];
  edges: NavChartEdge[];
  frontier: number[];
}

export const navAPI = {
  getChart: (): Promise<NavChartResponse> => apiRequest('/api/v1/nav/chart'),
};

// Sector contents — existing read-only endpoints (services/gameserver/src/
// api/routes/sectors.py), previously unconsumed by the player client. Scoped
// to the player's CURRENT region server-side (pre-existing constraint,
// unchanged by WO-PUX-NAVCHART) — a known sector in a different region 404s;
// callers should treat that as "contents unknown", not a hard failure.

// WO-CMB-SALVAGE-LOOP-1: one wreck row from GET /sectors/{id}/wrecks.
// Field shape mirrors routes/sectors.py's WreckResponse exactly — no
// damage_type key (the column does not exist on CargoWreck, NO-CANON).
export interface SectorWreck {
  id: string;
  original_owner_id: string | null;
  original_owner_name: string | null;
  destroyed_ship_type: string;
  cause: string;
  created_at: string;
  age_seconds: number;
  cargo: Record<string, number>;
  // Live preview only — can flip true->false while a page is open as the
  // grace window elapses; treat as advisory, not a lock-in (server re-checks
  // at salvage time regardless of what this said when the list loaded).
  would_flag_suspect: boolean;
}

// Mirrors routes/sectors.py's SalvageResponse.
export interface SalvageResult {
  salvaged: Record<string, number>;
  suspect_flagged: boolean;
  wreck_cleared: boolean;
  turns_spent: number;
}

export const sectorAPI = {
  getPlanets: (sectorId: number) => apiRequest(`/api/v1/sectors/${sectorId}/planets`),
  getStations: (sectorId: number) => apiRequest(`/api/v1/sectors/${sectorId}/stations`),

  // List salvageable wrecks in a sector (numeric, cockpit-native sector id —
  // the server resolves it to the sector's UUID internally).
  sectorWrecks: (sectorId: number): Promise<SectorWreck[]> =>
    apiRequest(`/api/v1/sectors/${sectorId}/wrecks`),

  // Salvage a wreck. `quantity` omitted = take as much as fits (server
  // default); a positive int requests a specific amount, further capped
  // server-side by free cargo hold and available turns (whichever is
  // tightest) — 1 turn per 100 units taken, rounded up.
  salvageWreck: (wreckId: string, quantity?: number): Promise<SalvageResult> =>
    apiRequest('/api/v1/sectors/salvage', {
      method: 'POST',
      body: JSON.stringify(
        quantity === undefined ? { wreck_id: wreckId } : { wreck_id: wreckId, quantity }
      ),
    }),
};

// Export all APIs
// Regional governance APIs (member-facing). The owner-scoped /my-region/*
// endpoints live in the admin surface; these are the player-facing reads/writes.
export const governanceAPI = {
  // The calling player's own citizenship status in a region. PATH A: an in-region
  // colony owner is reported as a citizen on the voter roll.
  getMyMembership: (regionId: string) =>
    apiRequest(`/api/v1/regions/${regionId}/membership/me`),

  // Explicit on-ramp: claim citizenship on the strength of owning a colony here.
  claimColonyCitizenship: (regionId: string) =>
    apiRequest(`/api/v1/regions/${regionId}/citizenship/colony-claim`, {
      method: 'POST',
    }),
};

// Region-OWNER-facing APIs (distinct from the member-facing governanceAPI above).
// These are gated server-side to the verified owner of the region. They live in
// the player client because region ownership is a player property — the panel
// probes getMyRegion() on open (200 = owner, 404 = not an owner) and uses the
// returned region id for the invite endpoints.
//
// Invite lifecycle (WO-IL4 → IL3 endpoints in regional_governance.py):
//   POST   /api/v1/regions/{region_id}/invites              — mint (201)
//   GET    /api/v1/regions/{region_id}/invites              — list owner's invites
//   POST   /api/v1/regions/{region_id}/invites/{id}/revoke  — revoke (idempotent)
export const regionOwnerAPI = {
  // Probe region ownership + load the owned region. Throws on 404 (not an owner).
  getMyRegion: () => apiRequest('/api/v1/regions/my-region'),

  // List the caller's invites for a region (newest first), owner-scoped.
  listInvites: (regionId: string) =>
    apiRequest(`/api/v1/regions/${regionId}/invites`),

  // Mint a new invite. Both fields optional: max_uses defaults to 1 (max 10),
  // expiresAt defaults to now + 7 days. expiresAt is an ISO8601 string.
  createInvite: (regionId: string, opts?: { max_uses?: number; expires_at?: string }) => {
    const body: Record<string, unknown> = {};
    if (opts?.max_uses !== undefined) body.max_uses = opts.max_uses;
    if (opts?.expires_at !== undefined) body.expires_at = opts.expires_at;
    return apiRequest(`/api/v1/regions/${regionId}/invites`, {
      method: 'POST',
      body: JSON.stringify(body),
    });
  },

  // Revoke an invite. Idempotent server-side (already-terminal → success).
  revokeInvite: (regionId: string, inviteId: string) =>
    apiRequest(`/api/v1/regions/${regionId}/invites/${inviteId}/revoke`, {
      method: 'POST',
    }),

  // Region-funded TradeDock construction (WO-TD-RGF-1). "my-region" scoped
  // like getMyRegion above — the server derives the region from the
  // authenticated owner, no regionId param. stationId must be an EXISTING
  // TradeDock-tier station inside the caller's region (construction_service
  // ._require_tradedock precondition) — pulls 50,000,000 cr from the region
  // treasury over a 90-day build. There is no dedicated status GET for this
  // route; poll construction_service reservations instead (constructionAPI
  // below) filtering ship_type === 'TRADEDOCK_CONSTRUCTION'.
  initiateTradeDockConstruction: (stationId: string) =>
    apiRequest('/api/v1/regions/my-region/tradedock-construction', {
      method: 'POST',
      body: JSON.stringify({ station_id: stationId }),
    }),
};

// Ship-construction reservation reads (routes/construction.py — the live
// slip-rental pipeline). Ownership-gated server-side to the caller's own
// Player row; a region-funded TradeDock reservation (ship_type
// 'TRADEDOCK_CONSTRUCTION') is owned by the initiating region owner and
// shows up here exactly like a player ship-build reservation does.
export const constructionAPI = {
  getMyReservations: () => apiRequest('/api/v1/construction/reservations/mine'),

  getReservation: (reservationId: string) =>
    apiRequest(`/api/v1/construction/reservations/${reservationId}`),
};

// Haggle APIs (ADR-0079 — numerical price negotiation)
//
// `commodity` MUST be the exact resource_type key the matching buy/sell call
// uses (e.g. 'Ore', 'Tech') — the agreed price is keyed by
// `${station}:${commodity}:${side}` and consumed by POST /trading/{buy|sell}
// when that route forwards the same resource_type. `side` is the PLAYER's
// direction: 'buy' = player buying from the station, 'sell' = player selling.
//
//   POST /api/v1/haggle/open    → opening card { round, band, price_clamp, ... }
//   POST /api/v1/haggle/offer   → round result { verdict, agreed_price?, counter_price?, ... }
//   GET  /api/v1/haggle/status  → { locked, cooldown_remaining_seconds, session }
export const haggleAPI = {
  open: (
    stationId: string,
    commodity: string,
    side: 'buy' | 'sell',
    quantity: number
  ) =>
    apiRequest('/api/v1/haggle/open', {
      method: 'POST',
      body: JSON.stringify({ station_id: stationId, commodity, side, quantity }),
    }),

  offer: (
    stationId: string,
    commodity: string,
    side: 'buy' | 'sell',
    offer: number
  ) =>
    apiRequest('/api/v1/haggle/offer', {
      method: 'POST',
      body: JSON.stringify({ station_id: stationId, commodity, side, offer }),
    }),

  status: (stationId: string, commodity: string, side: 'buy' | 'sell') =>
    apiRequest(
      `/api/v1/haggle/status?station_id=${encodeURIComponent(stationId)}` +
        `&commodity=${encodeURIComponent(commodity)}&side=${encodeURIComponent(side)}`
    ),
};

export const gameAPI = {
  combat: combatAPI,
  planetary: planetaryAPI,
  registry: registryAPI,
  team: teamAPI,
  fleet: fleetAPI,
  faction: factionAPI,
  message: messageAPI,
  ship: shipAPI,
  ranking: rankingAPI,
  bounty: bountyAPI,
  citadel: citadelAPI,
  grid: gridAPI,
  researchCockpit: researchCockpitAPI,
  shipUpgrade: shipUpgradeAPI,
  governance: governanceAPI,
  regionOwner: regionOwnerAPI,
  construction: constructionAPI,
  haggle: haggleAPI,
  resource: resourceAPI,
};