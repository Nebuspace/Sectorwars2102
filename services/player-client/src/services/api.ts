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
  deployGenesis: (sectorId: string, planetName: string, tier: 'basic' | 'enhanced' | 'advanced' = 'basic') =>
    apiRequest('/api/v1/planets/genesis/deploy', {
      method: 'POST',
      body: JSON.stringify({ sectorId, planetName, tier })
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

  getMissions: (factionId?: string) => {
    const params = factionId ? `?faction_id=${factionId}` : '';
    return apiRequest(`/api/v1/factions/missions${params}`);
  },

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

  getUpgrades: (shipId: string) =>
    apiRequest(`/api/v1/ships/${shipId}/upgrades`),

  installUpgrade: (shipId: string, upgradeId: string) =>
    apiRequest(`/api/v1/ships/${shipId}/upgrades`, {
      method: 'POST',
      body: JSON.stringify({ upgradeId })
    })
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
};

// Export all APIs
export const gameAPI = {
  combat: combatAPI,
  planetary: planetaryAPI,
  team: teamAPI,
  fleet: fleetAPI,
  faction: factionAPI,
  message: messageAPI,
  ship: shipAPI,
  ranking: rankingAPI,
  bounty: bountyAPI,
  citadel: citadelAPI,
  shipUpgrade: shipUpgradeAPI,
};