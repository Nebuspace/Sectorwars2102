// Real API service for gameserver endpoints
import { isAxiosError } from 'axios';
import apiClient from './apiClient';

// Helper function for API requests.
//
// Delegates to the shared apiClient (axios) so every call gets the
// centralized JWT refresh-on-401 behavior. The external contract is
// unchanged: returns the parsed response body, throws
// Error(detail || `API Error: <status>`) on failure.
type ApiRequestOptions = RequestInit & { timeout?: number };

async function apiRequest(
  endpoint: string,
  options: ApiRequestOptions = {}
): Promise<any> {
  const method = ((options.method || 'GET') as string).toUpperCase();
  const headers = {
    'Content-Type': 'application/json',
    ...(options.headers as Record<string, string>),
  };
  try {
    // Prefer verb-specific axios helpers so tests that mock apiClient.get/post
    // (the dominant GameContext harness pattern) still intercept wrapper traffic.
    // Use .request only when a non-default timeout is required (e.g. move).
    let response;
    if (options.timeout != null) {
      response = await apiClient.request({
        url: endpoint,
        method,
        data: options.body,
        timeout: options.timeout,
        headers,
      });
    } else if (method === 'GET') {
      response = await apiClient.get(endpoint, { headers });
    } else if (method === 'POST') {
      response = await apiClient.post(endpoint, options.body, { headers });
    } else if (method === 'PUT') {
      response = await apiClient.put(endpoint, options.body, { headers });
    } else if (method === 'DELETE') {
      response = await apiClient.delete(endpoint, { headers });
    } else {
      response = await apiClient.request({
        url: endpoint,
        method,
        data: options.body,
        headers,
      });
    }
    return response.data;
  } catch (error) {
    if (isAxiosError(error) && error.response) {
      const data: any = error.response.data;
      // Surface the server's human message. Native FastAPI HTTPExceptions use
      // `detail` (a string; 422 validation makes it an array — skip those), but
      // this gameserver's global error handler wraps errors as `{message}`.
      // Prefer a string `detail`, fall back to `message`, then a generic code.
      //
      // Some routes (e.g. POST /regions/{id}/policies validation) reject with
      // a STRUCTURED `detail: {code, errors: string[]}` instead of a plain
      // string. Surface that errors array on the thrown Error too (as
      // `.errors`) so a call site that needs per-field detail can read it,
      // while `.message` still gets a sane joined fallback for every other
      // (non-field-aware) caller.
      //
      // GET /regions/my-region (no region_id) 400s a 2+-region owner with
      // `detail: {code: "ERR_AMBIGUOUS_REGION_OWNER", regions: [...]}`
      // instead of guessing one (WO-DRIFT-admin-gov-multiregion-owner-500).
      // Surface `code`/`regions` the same way, generalized beyond that one
      // shape so any future structured-detail route gets them for free.
      let msg: string | undefined;
      let errors: string[] | undefined;
      let code: string | undefined;
      let regions: Array<{ id: string; name: string; display_name?: string }> | undefined;
      if (data && typeof data === 'object') {
        if (typeof data.detail === 'string') {
          msg = data.detail;
        } else if (data.detail && typeof data.detail === 'object') {
          if (Array.isArray(data.detail.errors)) {
            errors = data.detail.errors;
            msg = errors!.join('; ');
          }
          if (typeof data.detail.code === 'string') {
            code = data.detail.code;
          }
          if (Array.isArray(data.detail.regions)) {
            regions = data.detail.regions;
          }
          msg = msg || data.detail.message;
        }
        msg = msg || data.message;
      }
      const err = new Error(msg || `API Error: ${error.response.status}`);
      // Preserve HTTP status + body for callers that branch on gameplay
      // refusals (400/403/409) or structured detail (tractor lock, etc.).
      (err as any).status = error.response.status;
      (err as any).data = data;
      if (errors) (err as any).errors = errors;
      if (code) (err as any).code = code;
      if (regions) (err as any).regions = regions;
      throw err;
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

// Armory — sector mine laying (open space). Distinct from combatAPI.deployDrones.
export const armoryAPI = {
  deploy: (quantity: number) =>
    apiRequest('/api/v1/armory/deploy', {
      method: 'POST',
      body: JSON.stringify({ quantity }),
    }),
};

// Grey-flag PvP status (WO-BL). Mirrors player_combat.py's
// GreyStatusResponse exactly — a temporary "open season" mark earned by
// aggressing on a lawful target (attacking a player -> 1h, a station -> 1
// day). While grey, qualifying players may attack this player with no
// reputation penalty. Clears at greyUntil or by paying clearFineCredits early.
export interface GreyStatus {
  isGrey: boolean;
  kind: 'player_attack' | 'station_attack' | null;
  greyUntil: string | null;
  remainingSeconds: number;
  clearFineCredits: number | null;
}

// Mirrors player_combat.py's GreyClearFineResponse. success=false (with a
// reason in `message`) covers: not grey, already expired, insufficient
// credits -- credits and grey status are left untouched in every failure case.
export interface GreyClearFineResult {
  success: boolean;
  message: string;
  finePaid: number | null;
  creditsRemaining: number | null;
}

export const greyStatusAPI = {
  getStatus: (): Promise<GreyStatus> => apiRequest('/api/v1/combat/grey-status'),
  clearFine: (): Promise<GreyClearFineResult> =>
    apiRequest('/api/v1/combat/grey-status/clear-fine', { method: 'POST' }),
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

  // Server-authoritative per-unit defense prices (WO-API-PHASE1 B3) -- the
  // SAME defense_unit_price fn the updateDefenses commit path charges
  // (ADR-0076 citadel/planet-type scaling), so the client's cost preview
  // can never drift from what Save will actually charge. Read-only,
  // owner-gated (403 for a planet you don't own).
  // 'shields' is intentionally not part of the pricing response — see
  // WO-FIX-DEFENSE-SHIELDS-CITADEL-PREREQ-BYPASS (DefenseConfiguration.tsx).
  getDefensePricing: (planetId: string): Promise<{ turrets: number; fighters: number }> =>
    apiRequest(`/api/v1/planets/${planetId}/defenses/pricing`),

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

  // Server-authoritative quote for a (tier, registration) pair, priced for
  // the current player's reputation (WO-API-B2). Read-only — makes no
  // credit/reputation/device change. Returns exactly what deployGenesis
  // would charge for the same inputs, since both are sourced from the same
  // server-side cost function.
  getGenesisQuote: (
    tier: 'basic' | 'enhanced' | 'advanced',
    registration: 'clandestine' | 'registered' | 'chartered' = 'registered'
  ) =>
    apiRequest(`/api/v1/genesis/quote?tier=${tier}&registration=${registration}`),

  specializePlanet: (planetId: string, specialization: string) =>
    apiRequest(`/api/v1/planets/${planetId}/specialize`, {
      method: 'PUT',
      body: JSON.stringify({ specialization })
    }),

  // Land on an owned planet / leave the current landed planet.
  land: (planetId: string) =>
    apiRequest('/api/v1/planets/land', {
      method: 'POST',
      body: JSON.stringify({ planet_id: planetId }),
    }),

  leave: () =>
    apiRequest('/api/v1/planets/leave', { method: 'POST' }),

  rename: (planetId: string, name: string) =>
    apiRequest(`/api/v1/planets/${planetId}/rename`, {
      method: 'PUT',
      body: JSON.stringify({ name }),
    }),

  /** Preferred rename path (ADR-0073) — POST /planets/{id}/name. */
  setName: (planetId: string, name: string) =>
    apiRequest(`/api/v1/planets/${planetId}/name`, {
      method: 'POST',
      body: JSON.stringify({ name }),
    }),

  // Embark/disembark colonists between ship cargo and planet population.
  transferColonists: (
    planetId: string,
    action: 'embark' | 'disembark',
    quantity: number,
  ) =>
    apiRequest(`/api/v1/planets/${planetId}/colonists/transfer`, {
      method: 'POST',
      body: JSON.stringify({ action, quantity }),
    }),

  // Defense telemetry — GET /planets/{id}/defenses (scouting-friendly; no
  // ownership required). Distinct from updateDefenses (PUT) / getDefensePricing.
  getDefenses: (planetId: string) =>
    apiRequest(`/api/v1/planets/${planetId}/defenses`),

  // Upgrade the planet's shield generator by one level.
  upgradeShields: (planetId: string) =>
    apiRequest(`/api/v1/planets/${planetId}/shields/upgrade`, { method: 'POST' }),

  getSiegeStatus: (planetId: string) =>
    apiRequest(`/api/v1/planets/${planetId}/siege-status`),

  // Owner-only landing-rights ACL (colonization.md five modes; WO LEG-155).
  // Body matches gameserver LandingRightsRequest — lists always accepted so
  // mode flips stay lossless server-side even when the UI omits list editing.
  setLandingRights: (
    planetId: string,
    body: {
      mode: 'public' | 'team_only' | 'private' | 'whitelist' | 'denylist';
      whitelist?: string[];
      denylist?: string[];
    },
  ): Promise<{
    success: boolean;
    message: string;
    planet_id: string;
    mode: string;
    whitelist: string[];
    denylist: string[];
  }> =>
    apiRequest(`/api/v1/planets/${planetId}/landing-rights`, {
      method: 'PUT',
      body: JSON.stringify({
        mode: body.mode,
        whitelist: body.whitelist ?? [],
        denylist: body.denylist ?? [],
      }),
    }),
};

/** Station-protection tractor lock (Guarantee #2) — player responses. */
export const stationSecurityAPI = {
  getTractorLock: (stationId: string): Promise<{
    locked: boolean;
    reason?: string;
    tractor_strength?: string;
    break_attempts?: number;
    break_attempt_cost?: string;
  }> => apiRequest(`/api/v1/station-security/stations/${stationId}/tractor-lock`),

  breakTractorLock: (stationId: string) =>
    apiRequest(`/api/v1/station-security/stations/${stationId}/tractor-lock/break`, {
      method: 'POST',
    }),

  surrenderTractorLock: (stationId: string) =>
    apiRequest(`/api/v1/station-security/stations/${stationId}/tractor-lock/surrender`, {
      method: 'POST',
    }),
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

  // Analytics
  getTeamAnalytics: (teamId: string, period: 'day' | 'week' | 'month' | 'all-time') =>
    apiRequest(`/api/v1/teams/${teamId}/analytics?period=${period}`),

  // Permissions
  getPermissions: (teamId: string) =>
    apiRequest(`/api/v1/teams/${teamId}/permissions`)
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
  sendMessage: (
    recipientId: string,
    content: string,
    subject?: string,
    replyToId?: string | null,
  ) =>
    apiRequest('/api/v1/messages/send', {
      method: 'POST',
      // Backend MessageCreateRequest expects snake_case fields
      body: JSON.stringify({
        recipient_id: recipientId,
        subject: subject ?? null,
        content,
        reply_to_id: replyToId ?? null,
      }),
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

  /** LEG-412: tip POST /messages/{id}/flag?reason= (10–255 chars). */
  flagMessage: (messageId: string, reason: string) =>
    apiRequest(
      `/api/v1/messages/${encodeURIComponent(messageId)}/flag?reason=${encodeURIComponent(reason)}`,
      { method: 'POST' },
    ),

  getTeamMessages: (teamId: string, page: number = 1) =>
    apiRequest(`/api/v1/messages/team/${teamId}?page=${page}`)
};

// Ship APIs (partial - may need enhancement)
export const shipAPI = {
  getShips: () =>
    apiRequest('/api/v1/ships'), // Endpoint may vary

  /** Current piloted hull (cargo hold, genesis, etc.) — SpaceDock's live source. */
  getCurrentShip: () =>
    apiRequest('/api/v1/player/current-ship'),

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
    shipUpgradeAPI.purchaseUpgrade(shipId, upgradeType),

  // Make `shipId` the player's currently-piloted hull.
  setActive: (shipId: string) =>
    apiRequest(`/api/v1/ships/${shipId}/set-active`, { method: 'POST' }),
};

/** Cockpit player state / navigation (distinct from shipAPI maintenance). */
export const playerAPI = {
  getState: () => apiRequest('/api/v1/player/state'),

  getCurrentSector: () => apiRequest('/api/v1/player/current-sector'),

  getShips: () => apiRequest('/api/v1/player/ships'),

  getAvailableMoves: () => apiRequest('/api/v1/player/available-moves'),

  // Hard ceiling so a stuck FOR UPDATE / wedged gameserver cannot leave
  // the cockpit in "warp bubble forever" with no sector change.
  move: (sectorId: number) =>
    apiRequest(`/api/v1/player/move/${sectorId}`, {
      method: 'POST',
      timeout: 20000,
    }),

  scanLatentTunnels: () =>
    apiRequest('/api/v1/player/scan-latent-tunnels', { method: 'POST' }),

  /** One-time reward for a discovered special formation (WO-UI-ANOMALY). */
  investigateFormation: (formationId: string) =>
    apiRequest(`/api/v1/player/formations/${formationId}/investigate`, {
      method: 'POST',
    }),
};

/** Asteroid-field mining harvest (WO-UI-MINING). */
export const miningAPI = {
  harvest: (shipId: string) =>
    apiRequest('/api/v1/mining/harvest', {
      method: 'POST',
      body: JSON.stringify({ ship_id: shipId }),
    }),
};

/** First-login gate / onboarding session (GameContext + FirstLoginContext). */
export const firstLoginAPI = {
  getStatus: () => apiRequest('/api/v1/first-login/status'),

  startSession: () =>
    apiRequest('/api/v1/first-login/session', { method: 'POST' }),

  claimShip: (payload: { ship_type: string; dialogue_response: string }) =>
    apiRequest('/api/v1/first-login/claim-ship', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),

  submitDialogue: (exchangeId: string, response: string) =>
    apiRequest(`/api/v1/first-login/dialogue/${exchangeId}`, {
      method: 'POST',
      body: JSON.stringify({ response }),
    }),

  /** Omit body for decline-by-default (matches pre-nickname complete). */
  complete: (body?: { nickname_confirmed: boolean; nickname_override: string | null }) =>
    apiRequest('/api/v1/first-login/complete', {
      method: 'POST',
      body: body === undefined ? undefined : JSON.stringify(body),
    }),

  resetSession: () =>
    apiRequest('/api/v1/first-login/session', { method: 'DELETE' }),
};

/** Ship registry behaviors (SYSTEMS/ship-registry.md) — stolen / abandon / claim / transfer. */
export const shipRegistryAPI = {
  reportStolen: (shipId: string, recoveryMode?: 'with_bounty' | 'no_bounty' | null) =>
    apiRequest(`/api/v1/ships/${shipId}/report-stolen`, {
      method: 'POST',
      body: JSON.stringify(
        recoveryMode ? { recovery_mode: recoveryMode } : {}
      ),
    }),

  retractStolenReport: (shipId: string) =>
    apiRequest(`/api/v1/ships/${shipId}/retract-stolen-report`, { method: 'POST' }),

  abandon: (shipId: string, portId: string) =>
    apiRequest(`/api/v1/ships/${shipId}/abandon`, {
      method: 'POST',
      body: JSON.stringify({ port_id: portId }),
    }),

  claim: (shipId: string, portId: string) =>
    apiRequest(`/api/v1/ships/${shipId}/claim`, {
      method: 'POST',
      body: JSON.stringify({ port_id: portId }),
    }),

  fileTransferClaim: (shipId: string, portId: string) =>
    apiRequest(`/api/v1/ships/${shipId}/transfer-claim`, {
      method: 'POST',
      body: JSON.stringify({ port_id: portId }),
    }),

  approveTransferClaim: (shipId: string) =>
    apiRequest(`/api/v1/ships/${shipId}/transfer-claim/approve`, { method: 'POST' }),

  /** Voluntarily eject from the caller's own currently-piloted ship (no
   * ship_id -- always acts on the current ship) into a reused escape pod. */
  eject: () =>
    apiRequest('/api/v1/players/me/eject', { method: 'POST' }),

  /** Board `shipId` -- free/no-pin for the registered owner, otherwise
   * requires the ship's hatch_pin_code. */
  board: (shipId: string, pin?: string | null) =>
    apiRequest(`/api/v1/ships/${shipId}/board`, {
      method: 'POST',
      body: JSON.stringify(pin ? { pin } : {}),
    }),

  /** The current pilot (owner or borrower) changes the pin instantly --
   * caller must be aboard `shipId`. */
  setPin: (shipId: string, pin: string) =>
    apiRequest(`/api/v1/ships/${shipId}/set-pin`, {
      method: 'POST',
      body: JSON.stringify({ pin }),
    }),

  /** Port-gated pin recovery for the registered owner -- 1h delayed
   * take-effect, does not require being aboard. */
  requestPinReset: (shipId: string, portId: string, pin: string) =>
    apiRequest(`/api/v1/ships/${shipId}/request-pin-reset`, {
      method: 'POST',
      body: JSON.stringify({ port_id: portId, pin }),
    }),
};

// Ranking & Reputation APIs
export const rankingAPI = {
  getRank: () =>
    apiRequest('/api/v1/ranking/rank'),

  getDefinitions: () =>
    apiRequest('/api/v1/ranking/definitions'),

  getReputation: () =>
    apiRequest('/api/v1/ranking/reputation'),

  getPublicLeaderboard: (category: string = 'rank_points', limit: number = 20) =>
    apiRequest(`/api/v1/ranking/leaderboard/public?category=${category}&limit=${limit}`),

  getProgress: () =>
    apiRequest('/api/v1/ranking/progress'),
};

/** Player medals (GET /api/v1/medals/me — typed; ranking /medals retired). */
export const medalsAPI = {
  getMe: () => apiRequest('/api/v1/medals/me'),

  /** Clear-on-view offline award queue (GET /api/v1/medals/unviewed). */
  getUnviewed: (): Promise<{ unviewed: string[] }> =>
    apiRequest('/api/v1/medals/unviewed'),
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

// ADR-0094 point-2: defense construct is POST /grid/place, not the retired
// /buildings/construct. Map CitadelService snake_case types → catalog KINDs.
const DEFENSE_TYPE_TO_KIND: Record<string, string> = {
  turret_network: 'TURRET_NETWORK',
  orbital_platform: 'ORBITAL_PLATFORM',
  scanner_array: 'SCANNER_ARRAY',
  rail_gun: 'RAIL_GUN',
  planetary_defense_grid: 'DEFENSE_GRID',
  planet_minefield: 'PLANET_MINEFIELD',
};

function firstClearedEmptyPlot(gridView: {
  plots?: Array<{
    x?: number;
    y?: number;
    cleared?: boolean;
    hazard?: unknown;
    building_id?: string | null;
    buildingId?: string | null;
  }>;
}): { x: number; y: number } | null {
  const plots = Array.isArray(gridView?.plots) ? gridView.plots : [];
  for (const p of plots) {
    const occupied = p.building_id ?? p.buildingId ?? null;
    if (occupied) continue;
    if (p.cleared !== true) continue;
    if (p.hazard != null) continue;
    const x = Number(p.x);
    const y = Number(p.y);
    if (!Number.isFinite(x) || !Number.isFinite(y)) continue;
    return { x, y };
  }
  return null;
}

async function placeDefenseBuildingOnGrid(planetId: string, buildingType: string) {
  const kind = DEFENSE_TYPE_TO_KIND[buildingType] ?? String(buildingType || '').toUpperCase();
  if (!kind) {
    throw new Error('Unknown defense building type');
  }
  const grid = await apiRequest(`/api/v1/planets/${planetId}/grid`);
  const plot = firstClearedEmptyPlot(grid || {});
  if (!plot) {
    throw new Error('No empty cleared grid plot available for defense construction');
  }
  return apiRequest(`/api/v1/planets/${planetId}/grid/place`, {
    method: 'POST',
    body: JSON.stringify({ kind, x: plot.x, y: plot.y, level: 1 }),
  });
}

// Citadel APIs
export const citadelAPI = {
  getInfo: (planetId: string) =>
    apiRequest(`/api/v1/planets/${planetId}/citadel`),

  upgrade: (planetId: string) =>
    apiRequest(`/api/v1/planets/${planetId}/citadel/upgrade`, { method: 'POST' }),

  // Cancel an in-progress citadel upgrade — refunds 50% of credits paid
  // (CitadelService.cancel_upgrade). Owner-only.
  cancelUpgrade: (planetId: string) =>
    apiRequest(`/api/v1/planets/${planetId}/citadel/cancel`, { method: 'POST' }),

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

  // Move commodity planet-stockpile → protected citadel safe.
  depositCommodity: (planetId: string, commodity: string, amount: number) =>
    apiRequest(`/api/v1/planets/${planetId}/citadel/deposit-commodity`, {
      method: 'POST',
      body: JSON.stringify({ commodity, amount }),
    }),

  // Move commodity protected safe → planet stockpile.
  withdrawCommodity: (planetId: string, commodity: string, amount: number) =>
    apiRequest(`/api/v1/planets/${planetId}/citadel/withdraw-commodity`, {
      method: 'POST',
      body: JSON.stringify({ commodity, amount }),
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

  // Defense buildings unlockable at the planet's current citadel level
  // (CitadelService.get_available_buildings). Listing stays on this route;
  // construction migrated to /grid/place (ADR-0094 point 2).
  getAvailableBuildings: (planetId: string) =>
    apiRequest(`/api/v1/planets/${planetId}/buildings/available`),

  // Construct a defense building via the canonical grid place endpoint
  // (ADR-0094). Maps DEFENSE_BUILDINGS snake_case types → catalog KINDs,
  // picks the first cleared empty plot, then POST /grid/place.
  constructBuilding: (planetId: string, buildingType: string) =>
    placeDefenseBuildingOnGrid(planetId, buildingType),
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

// Ground-expedition APIs (ADR-0091 "Planetary Survey & Site Discovery",
// lane2's src/api/routes/expeditions.py — mounted WITHOUT an extra prefix,
// i.e. /api/v1/expeditions/*). Every expedition is a fresh RNG roll
// generated at launch: launch/reroll both return the settled roll
// (status SUCCESS/PARTIAL/FAILURE + result SiteIntel payload, or PENDING
// while EXPEDITION_DELAY_MINUTES has not elapsed).
//
// settle() calls the existing /planets/{id}/claim route, rewritten in
// place by lane3-settle-cas to run the ADR §8 CAS resolver (not a new
// /expeditions/{id}/settle route — corrected post-integration once
// lane3's actual route location was confirmed).
export const expeditionAPI = {
  launch: (planetId: string, shipId?: string | null) =>
    apiRequest('/api/v1/expeditions/launch', {
      method: 'POST',
      body: JSON.stringify({ planet_id: planetId, ship_id: shipId ?? null }),
    }),

  getStatus: (expeditionId: string) =>
    apiRequest(`/api/v1/expeditions/${expeditionId}/status`),

  list: () => apiRequest('/api/v1/expeditions'),

  reroll: (expeditionId: string) =>
    apiRequest(`/api/v1/expeditions/${expeditionId}/reroll`, { method: 'POST' }),

  settle: (planetId: string) =>
    apiRequest(`/api/v1/planets/${planetId}/claim`, { method: 'POST' }),
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
// Player-facing brand: "Citadel Research" (human-ruled). These read the now-live
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

  // Equipment-slot install/uninstall (LEG-109 / LEG-115 / LEG-117 / LEG-120 / LEG-126).
  // Still live for equipment_slots keys (mining_laser, quantum_harvester,
  // planetary_lander, tractor_beam, ecm_suite, stealth_module, …). Distinct from
  // module-grid installModule/removeModule below — deferred lattice families
  // stay out of this path.
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
  //                   updated_stats, [consumer_inert] }. Mining remains deferred
  //   (consumer_inert); lander/tractor are live (WO-WIRE-LANDER-TRACTOR-CATALOG-UNLOCK).
  //   harvester is live (residual 2): install succeeds; passive_income from _baked.
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
// them, and id-only frontier stubs (each linked to the known sector that
// surfaced it, via `from` -- WO-NAV-CHART-FRONTIER-EDGES) for adjacent-but-
// unknown sectors. Course plot: POST /api/v1/nav/plot (navAPI.plot).
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

// A frontier stub carries only the id of the unexplored sector plus the id
// of the ONE known sector that surfaced it (`from`) -- never name/type/
// coordinates (WO-NAV-CHART-FRONTIER-EDGES). `from` exists purely so a
// client can attach the stub to the known graph; it carries no information
// about the frontier sector itself.
export interface NavChartFrontier {
  id: number;
  from: number;
}

export interface NavChartResponse {
  sectors: NavChartSector[];
  edges: NavChartEdge[];
  frontier: NavChartFrontier[];
}

// GET /api/v1/nav/threat — the cockpit TACTICAL deck-monitor's known-graph
// STATIC threat rollup (WO-UI2-TACTICAL-MONITOR). One entry per sector in
// the player's known graph (the exact node-set /nav/chart's `sectors`
// covers — frontier stubs excluded), each carrying a 0-~86 danger score,
// a coarse band, and the scored inputs that produced it. STATIC-only by
// security ruling — this never reflects live remote sector composition
// (that stays client-side, from currentSector.players_present).
export type NavThreatBand = 'CLEAR' | 'CAUTION' | 'HOSTILE' | 'LETHAL';

export interface NavThreatContributor {
  input: 'low_security' | 'hazard' | 'pirate_pressure' | 'recent_combat' | string;
  points: number;
}

export interface NavThreatEntry {
  sector_id: number;
  score: number;
  band: NavThreatBand;
  contributors: NavThreatContributor[];
}

// POST /api/v1/nav/plot — ADR-0072 / AutopilotContext course plot.
export interface CourseHop {
  sector_id: number;
  name: string;
  turn_cost: number;
  visited: boolean;
  safety_rating: number | null;
  via_tunnel: boolean;
}

export interface CourseReachable {
  success: true;
  reachable: true;
  target_sector_id: number;
  hops: CourseHop[];
  total_turns: number;
}

export interface CourseUnreachable {
  success: true;
  reachable: false;
  target_sector_id: number;
  nearest_known: { sector_id: number; name: string } | null;
  /** Present when the sector id does not exist in the galaxy DB. */
  error?: string;
  /**
   * Why the plot refused:
   * - `unknown_sector` — no such sector
   * - `uncharted` — exists but outside the player's known graph
   * - `no_route` — charted, but no directed path from here (one-ways / gaps)
   */
  reason?: 'unknown_sector' | 'uncharted' | 'no_route';
}

export type CoursePlot = CourseReachable | CourseUnreachable;

export const navAPI = {
  // `bounded` (WO-NAV-REACH-BACKEND, default false) opts into the server's
  // scanner-depth-bounded chart (CHART_BOUNDED_DEPTH_CEILING=12) — sectors
  // beyond the bound are demoted to frontier stubs instead of full nodes.
  // Omitted/false preserves today's exact unbounded query string
  // (byte-identical), so any existing caller that doesn't pass it (e.g.
  // GalaxyMap.tsx's standalone fetch) is untouched (WO-NAV-CHART-POLISH
  // sub-part e).
  getChart: (bounded?: boolean): Promise<NavChartResponse> =>
    apiRequest(`/api/v1/nav/chart${bounded ? '?bounded=true' : ''}`),
  getThreat: (): Promise<NavThreatEntry[]> =>
    apiRequest('/api/v1/nav/threat'),
  plot: (
    targetSectorId: number,
    objective: 'min_time' | 'min_risk' = 'min_time',
  ): Promise<CoursePlot> =>
    apiRequest('/api/v1/nav/plot', {
      method: 'POST',
      body: JSON.stringify({
        target_sector_id: targetSectorId,
        objective,
      }),
    }),
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

  /** Live sector contents — ships, planets, ports, etc. */
  getContents: (sectorId: number) =>
    apiRequest(`/api/v1/sectors/${sectorId}/contents`),

  /** Celestial system snapshot (star / decorative bodies) for SolarSystemViewscreen. */
  getSystem: (sectorId: number) =>
    apiRequest(`/api/v1/sectors/${sectorId}/system`),

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

/** Intrasystem helm — windshield pose / burn / halt. */
export const helmAPI = {
  getPose: () => apiRequest('/api/v1/helm/intrasystem/pose'),

  burn: (payload: {
    x_pct: number;
    y_pct: number;
    target_kind: string;
    target_id: string | null;
  }) =>
    apiRequest('/api/v1/helm/intrasystem/burn', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),

  halt: () =>
    apiRequest('/api/v1/helm/intrasystem/halt', { method: 'POST' }),
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

  // Member-scoped discovery (WO-REGOV-CITIZEN-API — any region member, not
  // just the owner, may list). 403 ERR_NOT_A_MEMBER / 404 propagate as thrown
  // Errors via apiRequest.
  listPolicies: (regionId: string) =>
    apiRequest(`/api/v1/regions/${regionId}/policies`),

  listElections: (regionId: string) =>
    apiRequest(`/api/v1/regions/${regionId}/elections`),

  // `terms` is redacted server-side for this member view (owner-only via
  // the separate /my-region/treaties read).
  listTreaties: (regionId: string) =>
    apiRequest(`/api/v1/regions/${regionId}/treaties`),

  // Cast (or reject) a vote in an ACTIVE election. One vote per (election,
  // voter) — a repeat call rejects 409 ERR_ALREADY_VOTED.
  castElectionVote: (regionId: string, electionId: string, candidateId: string) =>
    apiRequest(`/api/v1/regions/${regionId}/elections/${electionId}/vote`, {
      method: 'POST',
      body: JSON.stringify({ candidate_id: candidateId }),
    }),

  // Self-nominate in a PENDING (candidate-registration phase) election.
  // Locks 409 ERR_CANDIDATES_LOCKED once the election advances to ACTIVE.
  registerCandidacy: (regionId: string, electionId: string, platform?: string) =>
    apiRequest(`/api/v1/regions/${regionId}/elections/${electionId}/candidates`, {
      method: 'POST',
      body: JSON.stringify(platform ? { platform } : {}),
    }),

  // Read an election's status + tally (results is populated once COMPLETED).
  getElectionResults: (regionId: string, electionId: string) =>
    apiRequest(`/api/v1/regions/${regionId}/elections/${electionId}/results`),

  // Cast (or reject) a yes/no vote on a VOTING-state policy. One vote per
  // (policy, voter) — a repeat call rejects 409 ERR_ALREADY_VOTED.
  castPolicyVote: (regionId: string, policyId: string, support: boolean) =>
    apiRequest(`/api/v1/regions/${regionId}/policies/${policyId}/vote`, {
      method: 'POST',
      body: JSON.stringify({ support }),
    }),

  // Propose a new policy. 403 ERR_NOT_ELIGIBLE if the caller isn't a voting
  // member; 400 ERR_INVALID_PROPOSED_CHANGES (with a structured `.errors`
  // array on the thrown Error — see apiRequest above) if proposed_changes
  // fails server-side validation.
  proposePolicy: (
    regionId: string,
    data: {
      policy_type: string;
      title: string;
      description?: string;
      proposed_changes: Record<string, unknown>;
      voting_duration_days?: number;
    }
  ) =>
    apiRequest(`/api/v1/regions/${regionId}/policies`, {
      method: 'POST',
      body: JSON.stringify(data),
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
  // Probe region ownership + load the owned region. Throws on 404 (not an
  // owner). Omitting regionId is fine for a 1-region owner (unchanged); a
  // 2+-region owner without it gets a 400 whose thrown Error carries
  // `.code === 'ERR_AMBIGUOUS_REGION_OWNER'` and `.regions` (the pick-list) —
  // never a silent guess. Pass the region_id a caller already knows (e.g. a
  // panel that received it as a prop) to skip the ambiguity entirely.
  getMyRegion: (regionId?: string) =>
    apiRequest(
      regionId
        ? `/api/v1/regions/my-region?region_id=${encodeURIComponent(regionId)}`
        : '/api/v1/regions/my-region'
    ),

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

  // Treaty inbox / lifecycle (WO-ESCALATE-REGIONAL-TREATY-FLOW-PRIORITY).
  // Owner-scoped list includes `terms`; accept/reject are region_b-only;
  // terminate is either party. Optional regionId for multi-region owners.
  listMyTreaties: (regionId?: string) =>
    apiRequest(
      regionId
        ? `/api/v1/regions/my-region/treaties?region_id=${encodeURIComponent(regionId)}`
        : '/api/v1/regions/my-region/treaties',
    ),

  proposeTreaty: (
    body: {
      counterparty_region_id: string;
      treaty_type: string;
      terms?: Record<string, unknown>;
      expires_at?: string | null;
    },
    regionId?: string,
  ) =>
    apiRequest(
      regionId
        ? `/api/v1/regions/my-region/treaties?region_id=${encodeURIComponent(regionId)}`
        : '/api/v1/regions/my-region/treaties',
      { method: 'POST', body: JSON.stringify(body) },
    ),

  acceptTreaty: (treatyId: string) =>
    apiRequest(`/api/v1/regions/treaties/${treatyId}/accept`, { method: 'POST' }),

  rejectTreaty: (treatyId: string) =>
    apiRequest(`/api/v1/regions/treaties/${treatyId}/reject`, { method: 'POST' }),

  terminateTreaty: (treatyId: string) =>
    apiRequest(`/api/v1/regions/treaties/${treatyId}/terminate`, { method: 'POST' }),
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

// Trading price-quote API (WO-API-B1) — server-authoritative READ-ONLY
// preview of what POST /trading/buy or /trading/sell would charge/pay right
// now (unit_price/subtotal/tax_rate/tax/total). If the caller has an
// accepted-but-unconsumed haggle session for this (station, commodity,
// side), the quote peeks that price too (without consuming it) so it stays
// the single source of truth even mid-haggle — see HaggleDesk's "Deal
// struck" total (mack HIGH-2: the desk used to show a tax-FREE total while
// the actual charge applies the station's tax_rate).
export const tradingAPI = {
  quote: (
    stationId: string,
    resourceType: string,
    quantity: number,
    action: 'buy' | 'sell'
  ) =>
    apiRequest('/api/v1/trading/quote', {
      method: 'POST',
      body: JSON.stringify({
        station_id: stationId,
        resource_type: resourceType,
        quantity,
        action,
      }),
    }),

  dock: (stationId: string) =>
    apiRequest('/api/v1/trading/dock', {
      method: 'POST',
      body: JSON.stringify({ station_id: stationId }),
    }),

  undock: () =>
    apiRequest('/api/v1/trading/undock', { method: 'POST' }),

  getSlips: (stationId: string) =>
    apiRequest(`/api/v1/trading/stations/${stationId}/slips`),

  bumpSlip: (stationId: string, occupantPlayerId: string) =>
    apiRequest(`/api/v1/trading/stations/${stationId}/slips/bump`, {
      method: 'POST',
      body: JSON.stringify({ occupant_player_id: occupantPlayerId }),
    }),

  // LEG-438 — long-term mooring (docking-slips.md; tip POST /trading/mooring/long-term).
  // Rate is tip docking_service.LONG_TERM_MOORING_RATE_PER_DAY (200 cr/day) — preview only.
  acquireLongTermMooring: (stationId: string, days: number) =>
    apiRequest('/api/v1/trading/mooring/long-term', {
      method: 'POST',
      body: JSON.stringify({ station_id: stationId, days }),
    }),

  releaseLongTermMooring: () =>
    apiRequest('/api/v1/trading/mooring/long-term/release', {
      method: 'POST',
    }),

  getMarket: (stationId: string) =>
    apiRequest(`/api/v1/trading/market/${stationId}`),

  getMarketHistory: (stationId: string, commodity: string, hours = 24 * 7) =>
    apiRequest(
      `/api/v1/trading/market/${encodeURIComponent(stationId)}/history` +
        `?commodity=${encodeURIComponent(commodity)}&hours=${hours}`,
    ),

  buy: (stationId: string, resourceType: string, quantity: number) =>
    apiRequest('/api/v1/trading/buy', {
      method: 'POST',
      body: JSON.stringify({
        station_id: stationId,
        resource_type: resourceType,
        quantity,
      }),
    }),

  sell: (stationId: string, resourceType: string, quantity: number) =>
    apiRequest('/api/v1/trading/sell', {
      method: 'POST',
      body: JSON.stringify({
        station_id: stationId,
        resource_type: resourceType,
        quantity,
      }),
    }),
};

// Trade Contract APIs (SYSTEMS/contracts.md, gameserver contracts.py) —
// per-station board reads, accept/complete/abandon transitions, and
// player-issued posting/cancel. Callers type-cast the response at the call
// site (matches resourceAPI.list()'s convention above) — see
// src/types/contract.ts for the exact wire shapes.
export const contractsAPI = {
  getBoard: (stationId: string) =>
    apiRequest(`/api/v1/contracts/board?station_id=${encodeURIComponent(stationId)}`),

  getMine: () => apiRequest('/api/v1/contracts/mine'),

  getContract: (contractId: string) =>
    apiRequest(`/api/v1/contracts/${contractId}`),

  accept: (contractId: string) =>
    apiRequest(`/api/v1/contracts/${contractId}/accept`, { method: 'POST' }),

  complete: (contractId: string) =>
    apiRequest(`/api/v1/contracts/${contractId}/complete`, { method: 'POST' }),

  abandon: (contractId: string) =>
    apiRequest(`/api/v1/contracts/${contractId}/abandon`, { method: 'POST' }),

  // body matches PostContractRequest (src/types/contract.ts) — contract_type
  // is restricted server-side to cargo_delivery | bulk_procurement; omit
  // it and the server defaults to cargo_delivery.
  post: (body: {
    destination_station_id: string;
    commodity_type: string;
    quantity: number;
    payment: number;
    deadline: string;
    origin_station_id?: string;
    insurance_pool_reserve?: number;
    contract_type?: 'cargo_delivery' | 'bulk_procurement';
  }) =>
    apiRequest('/api/v1/contracts', {
      method: 'POST',
      body: JSON.stringify(body),
    }),

  cancel: (contractId: string) =>
    apiRequest(`/api/v1/contracts/${contractId}/cancel`, { method: 'POST' }),

  // WO-1a-CORE. tier is one of 'basic' | 'standard' | 'hazard'. Claim-
  // filing (WO-1b-CLAIM-SAFETY) is deferred, design-gated -- not built.
  insure: (contractId: string, tier: string) =>
    apiRequest(`/api/v1/contracts/${contractId}/insure`, {
      method: 'POST',
      body: JSON.stringify({ tier }),
    }),

  // WO-CONTRACT-INSURANCE-ARBITRATION-SCOPE — acceptor files dispute on an
  // expired (failed) contract within the 48h window (contracts.md:390).
  // Server runs Tier-1 sync; unresolvable cases escalate to admin.
  dispute: (contractId: string, body: { reason: string; evidence_snapshot?: string }) =>
    apiRequest(`/api/v1/contracts/${contractId}/dispute`, {
      method: 'POST',
      body: JSON.stringify(body),
    }),
};

// Pioneer Office — migration contracts at a population hub (GameContext
// PioneerOfficeVenue). Separate from trade contractsAPI above.
export const pioneerAPI = {
  getOffice: () => apiRequest('/api/v1/pioneer/office'),

  brokerContract: (cohortTotal: number) =>
    apiRequest('/api/v1/pioneer/contracts', {
      method: 'POST',
      body: JSON.stringify({ cohort_total: cohortTotal }),
    }),

  loadBatch: (contractId: string, quantity: number) =>
    apiRequest(`/api/v1/pioneer/contracts/${contractId}/load`, {
      method: 'POST',
      body: JSON.stringify({ quantity }),
    }),

  listContracts: (includeClosed = false) =>
    apiRequest(
      `/api/v1/pioneer/contracts?include_closed=${includeClosed ? 'true' : 'false'}`,
    ),

  cancelContract: (contractId: string) =>
    apiRequest(`/api/v1/pioneer/contracts/${contractId}/cancel`, {
      method: 'POST',
    }),
};

// Central Nexus Bank — withdraw at Starport Prime (ADR-0050 / monetization.md).
// Balance is readable anywhere; withdraw routes enforce dock + Prime/override.
export const centralBankAPI = {
  getBalance: () => apiRequest('/api/v1/central-bank/balance'),

  withdrawCredits: (amount: number) =>
    apiRequest('/api/v1/central-bank/withdraw/credits', {
      method: 'POST',
      body: JSON.stringify({ amount }),
    }),

  withdrawCommodity: (commodity: string, quantity: number) =>
    apiRequest('/api/v1/central-bank/withdraw/commodity', {
      method: 'POST',
      body: JSON.stringify({ commodity, quantity }),
    }),
};

// Storage lockers — multi-trip contract fulfillment (FEATURES/economy/storage-lockers.md).
// Rent is idempotent per (player, contract); deposit auto-completes when
// accumulated deposits reach the contract quantity.
export const storageAPI = {
  rentLocker: (contractId: string) =>
    apiRequest('/api/v1/storage/lockers', {
      method: 'POST',
      body: JSON.stringify({ contract_id: contractId }),
    }),

  deposit: (lockerId: string, quantity: number) =>
    apiRequest(`/api/v1/storage/lockers/${lockerId}/deposit`, {
      method: 'POST',
      body: JSON.stringify({ quantity }),
    }),

  retrieve: (lockerId: string, quantity?: number) =>
    apiRequest(`/api/v1/storage/lockers/${lockerId}/retrieve`, {
      method: 'POST',
      body: JSON.stringify(quantity != null ? { quantity } : {}),
    }),

  // GET /storage/lockers/claimable (WO-STORE-EXPIRY-CLAIMABLE) — every
  // CLAIMABLE locker the caller owns, across every station, with cargo
  // still retrievable. Bare array, unfiltered by station (a locker's
  // stationId travels on each row) — see types/storage.ts.
  getClaimable: () => apiRequest('/api/v1/storage/lockers/claimable'),
};

/** ADR-0089 player-to-player trade window (thin client). */
export const tradeAPI = {
  initiate: (targetPlayerId: string) =>
    apiRequest('/api/v1/trade/initiate', {
      method: 'POST',
      body: JSON.stringify({ target_player_id: targetPlayerId }),
    }),
  accept: (sessionId: string) =>
    apiRequest(`/api/v1/trade/${sessionId}/accept`, { method: 'POST' }),
  decline: (sessionId: string) =>
    apiRequest(`/api/v1/trade/${sessionId}/decline`, { method: 'POST' }),
  offer: (
    sessionId: string,
    offer: {
      credits?: number;
      commodities?: Record<string, number>;
      ship_id?: string | null;
      ships?: string[];
    }
  ) =>
    apiRequest(`/api/v1/trade/${sessionId}/offer`, {
      method: 'POST',
      body: JSON.stringify({
        credits: offer.credits ?? 0,
        commodities: offer.commodities ?? {},
        ship_id: offer.ship_id ?? null,
        ships: offer.ships ?? [],
      }),
    }),
  confirm: (sessionId: string) =>
    apiRequest(`/api/v1/trade/${sessionId}/confirm`, { method: 'POST' }),
  cancel: (sessionId: string) =>
    apiRequest(`/api/v1/trade/${sessionId}/cancel`, { method: 'POST' }),
  get: (sessionId: string) => apiRequest(`/api/v1/trade/${sessionId}`),
  getOpen: () => apiRequest('/api/v1/trade/open'),
};

// Quantum drive (Warp Jumper) — status / scan / jump / refine / harvest.
export const quantumAPI = {
  getStatus: () => apiRequest('/api/v1/quantum/status'),

  /** Astrogation chart for Warp Jumper (ADR-0030 Phase 1). */
  getMinimap: () => apiRequest('/api/v1/quantum/minimap'),

  scan: (payload: Record<string, unknown>) =>
    apiRequest('/api/v1/quantum/scan', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),

  jump: (payload: Record<string, unknown>) =>
    apiRequest('/api/v1/quantum/jump', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),

  refineCharge: () =>
    apiRequest('/api/v1/quantum/refine-charge', {
      method: 'POST',
      body: JSON.stringify({}),
    }),

  harvest: () =>
    apiRequest('/api/v1/quantum/harvest', {
      method: 'POST',
      body: JSON.stringify({}),
    }),
};

/** Gatewright Guild — warp-gate construction pipeline. */
export const warpGatesAPI = {
  listMine: () => apiRequest('/api/v1/warp-gates/mine'),

  listSector: (sectorId: number) =>
    apiRequest(`/api/v1/warp-gates/sector/${sectorId}`),

  deployBeacon: (destinationSectorId: number) =>
    apiRequest('/api/v1/warp-gates/deploy-beacon', {
      method: 'POST',
      body: JSON.stringify({ destination_sector_id: destinationSectorId }),
    }),

  anchorFocus: (beaconId: string, accessMode: string) =>
    apiRequest('/api/v1/warp-gates/anchor-focus', {
      method: 'POST',
      body: JSON.stringify({ beacon_id: beaconId, access_mode: accessMode }),
    }),

  cancel: (id: string) =>
    apiRequest(`/api/v1/warp-gates/${id}/cancel`, { method: 'POST' }),

  stageMaterials: (siteId: string, materials: Record<string, number>) =>
    apiRequest(`/api/v1/warp-gates/${siteId}/stage-materials`, {
      method: 'POST',
      body: JSON.stringify(materials),
    }),

  advanceConstruction: (siteId: string) =>
    apiRequest(`/api/v1/warp-gates/${siteId}/advance-construction`, {
      method: 'POST',
    }),
};

/** Quantum Crystal / Lumen Crystal refining (DISTINCT from quantum refine-charge). */
export const refiningAPI = {
  /** 5 Shards + 10,000 cr → 1 Quantum Crystal (instant; Class-3+/SpaceDock). */
  refine: () =>
    apiRequest('/api/v1/refining/refine', {
      method: 'POST',
      body: JSON.stringify({}),
    }),

  startLumen: () =>
    apiRequest('/api/v1/refining/refine-lumen/start', {
      method: 'POST',
      body: JSON.stringify({}),
    }),

  lumenStatus: (): Promise<{
    pending: boolean;
    ready_at: string | null;
    collectible: boolean;
  }> => apiRequest('/api/v1/refining/refine-lumen/status'),

  collectLumen: () =>
    apiRequest('/api/v1/refining/refine-lumen/collect', {
      method: 'POST',
      body: JSON.stringify({}),
    }),
};

// Port Office — station ownership, sealed-bid sales, tariffs, takeovers.
export const portOwnershipAPI = {
  getListings: () => apiRequest('/api/v1/port-ownership/listings'),

  getListing: (stationId: string) =>
    apiRequest(`/api/v1/port-ownership/stations/${stationId}/listing`),

  listStation: (stationId: string) =>
    apiRequest(`/api/v1/port-ownership/stations/${stationId}/list`, {
      method: 'POST',
    }),

  placeOffer: (stationId: string, bidAmount: number) =>
    apiRequest(`/api/v1/port-ownership/stations/${stationId}/offer`, {
      method: 'POST',
      body: JSON.stringify({ bid: bidAmount }),
    }),

  getMyStations: () => apiRequest('/api/v1/port-ownership/my-stations'),

  setTax: (stationId: string, taxRate: number) =>
    apiRequest(`/api/v1/port-ownership/stations/${stationId}/tax`, {
      method: 'POST',
      body: JSON.stringify({ rate: taxRate }),
    }),

  withdraw: (stationId: string, amount: number) =>
    apiRequest(`/api/v1/port-ownership/stations/${stationId}/withdraw`, {
      method: 'POST',
      body: JSON.stringify({ amount }),
    }),

  getTakeoverStatus: (stationId: string) =>
    apiRequest(`/api/v1/port-ownership/stations/${stationId}/takeover`),

  launchTakeover: (stationId: string) =>
    apiRequest(`/api/v1/port-ownership/stations/${stationId}/takeover/launch`, {
      method: 'POST',
    }),

  counterTakeover: (stationId: string, action: 'accept' | 'match' | 'dispute') =>
    apiRequest(`/api/v1/port-ownership/stations/${stationId}/takeover/counter`, {
      method: 'POST',
      body: JSON.stringify({ action }),
    }),
};

// Message beacons (message-beacons.md) -- deploy/read/salvage/recharge/
// report kernel is server-shipped (services/gameserver/src/api/routes/
// beacons.py); `mine` lists the calling player's own deployed beacons
// (GET /api/v1/beacons/mine) for the My Beacons management screen.
export interface MyBeacon {
  id: string;
  sector_id: number;
  preview: string;
  deployed_at: string | null;
  charge_expires_at: string | null;
  expiry: string | null;
  state: string;
  read_once: boolean;
  read_count: number;
  flagged: boolean;
}

export const beaconAPI = {
  mine: (page = 1, limit = 20): Promise<{ beacons: MyBeacon[]; total?: number }> =>
    apiRequest(`/api/v1/beacons/mine?page=${page}&limit=${limit}`),

  /** Deploy a message beacon at sector_id (POST /api/v1/beacons/deploy). */
  deploy: (body: { sector_id: number; message: string; read_once?: boolean }) =>
    apiRequest('/api/v1/beacons/deploy', {
      method: 'POST',
      body: JSON.stringify({
        sector_id: body.sector_id,
        message: body.message,
        read_once: body.read_once ?? false,
      }),
    }),

  read: (beaconId: string) => apiRequest(`/api/v1/beacons/${beaconId}/read`),
  salvage: (beaconId: string) => apiRequest(`/api/v1/beacons/${beaconId}/salvage`, { method: 'POST' }),
  recharge: (beaconId: string) => apiRequest(`/api/v1/beacons/${beaconId}/recharge`, { method: 'POST' }),
  report: (beaconId: string) => apiRequest(`/api/v1/beacons/${beaconId}/report`, { method: 'POST' }),
};

export const gameAPI = {
  combat: combatAPI,
  armory: armoryAPI,
  greyStatus: greyStatusAPI,
  planetary: planetaryAPI,
  registry: registryAPI,
  team: teamAPI,
  fleet: fleetAPI,
  faction: factionAPI,
  message: messageAPI,
  ship: shipAPI,
  player: playerAPI,
  ranking: rankingAPI,
  bounty: bountyAPI,
  citadel: citadelAPI,
  grid: gridAPI,
  expedition: expeditionAPI,
  researchCockpit: researchCockpitAPI,
  shipUpgrade: shipUpgradeAPI,
  governance: governanceAPI,
  regionOwner: regionOwnerAPI,
  construction: constructionAPI,
  haggle: haggleAPI,
  trading: tradingAPI,
  resource: resourceAPI,
  contracts: contractsAPI,
  pioneer: pioneerAPI,
  storage: storageAPI,
  trade: tradeAPI,
  quantum: quantumAPI,
  refining: refiningAPI,
  warpGates: warpGatesAPI,
  helm: helmAPI,
  portOwnership: portOwnershipAPI,
  beacon: beaconAPI,
};

/** ADR-0054 X-D3 — GC-lapse 7-day liquidation window self-service. */
export const gcLapseAPI = {
  getStatus: (): Promise<{
    lapsed: boolean;
    gc_lapsed_at: string | null;
    relocation_available: boolean;
    foreign_holdings: Array<{
      asset_type: 'planet' | 'station' | string;
      asset_id: string;
      name: string;
      region_id: string | null;
      sector_id: number;
    }>;
  }> => apiRequest('/api/v1/players/me/gc-lapse-status'),

  emergencyRelocate: (assetType: 'planet' | 'station' | string, assetId: string) =>
    apiRequest('/api/v1/players/me/gc-emergency-relocation', {
      method: 'POST',
      body: JSON.stringify({ asset_type: assetType, asset_id: assetId }),
    }),
};

/** Carrier ship-hangar consent (WO-WIRE-CARRIER-HANGAR-UI / WO-AE). */
export type HangarStatus = {
  hangared_on: { carrier_id: string; carrier_name?: string | null } | null;
  pending_outgoing: {
    carrier_id: string;
    ship_id?: string;
    size_units?: number;
    requested_at?: string;
    request_state?: string;
  } | null;
  owned_carrier: {
    carrier_id: string;
    capacity_units: number;
    used_units: number;
    docked: Array<Record<string, unknown>>;
  } | null;
};

export const hangarAPI = {
  getStatus: (): Promise<HangarStatus> => apiRequest('/api/v1/hangar/status'),
  getHangar: (carrierId: string) => apiRequest(`/api/v1/hangar/${carrierId}`),
  requestDock: (carrierId: string, shipId?: string) =>
    apiRequest(`/api/v1/hangar/${carrierId}/dock-request`, {
      method: 'POST',
      body: JSON.stringify(shipId ? { ship_id: shipId } : {}),
    }),
  accept: (carrierId: string, shipId: string) =>
    apiRequest(`/api/v1/hangar/${carrierId}/accept`, {
      method: 'POST',
      body: JSON.stringify({ ship_id: shipId }),
    }),
  cancel: (carrierId: string, shipId: string) =>
    apiRequest(`/api/v1/hangar/${carrierId}/cancel`, {
      method: 'POST',
      body: JSON.stringify({ ship_id: shipId }),
    }),
  undock: () => apiRequest('/api/v1/hangar/undock', { method: 'POST' }),
  disembark: () => apiRequest('/api/v1/hangar/disembark', { method: 'POST' }),
};

/** Tractor Beam tow consent (WO-WIRE-TOW-CONSENT-UI / ADR-0067). */
export type TowPending = {
  hauler_id: string;
  towed_ship_id?: string | null;
  towed_size?: string | null;
  surcharge_per_move?: number | null;
  requested_at?: string | null;
  request_state?: string;
};

export type TowStatus = {
  towing: Record<string, unknown> | null;
  being_towed_by: { hauler_id: string; surcharge_per_move?: number | null } | null;
  pending_outgoing: TowPending | null;
  pending_incoming: TowPending | null;
};

export const towAPI = {
  getStatus: (): Promise<TowStatus> => apiRequest('/api/v1/tow/status'),
  request: (targetShipId: string) =>
    apiRequest('/api/v1/tow/request', {
      method: 'POST',
      body: JSON.stringify({ target_ship_id: targetShipId }),
    }),
  accept: (haulerId: string) =>
    apiRequest('/api/v1/tow/accept', {
      method: 'POST',
      body: JSON.stringify({ hauler_id: haulerId }),
    }),
  cancel: (haulerId: string) =>
    apiRequest('/api/v1/tow/cancel', {
      method: 'POST',
      body: JSON.stringify({ hauler_id: haulerId }),
    }),
  detach: () => apiRequest('/api/v1/tow/detach', { method: 'POST' }),
};

/** Stranding recovery console (WO-WIRE-RECOVERY-CONSOLE). */
export type RecoveryDistressStatus = {
  available: boolean;
  cooldown_until?: string | null;
  last_used_at?: string | null;
};

export type RecoverySlipdriveStatus = {
  charging: boolean;
  charge_deadline?: string | null;
  ready: boolean;
  cancelled_by_movement?: boolean;
};

export type RecoveryStatus = {
  distress_beacon: RecoveryDistressStatus;
  slipdrive: RecoverySlipdriveStatus;
};

export const recoveryAPI = {
  getStatus: (): Promise<RecoveryStatus> => apiRequest('/api/v1/recovery/status'),
  fireDistressBeacon: () =>
    apiRequest('/api/v1/recovery/distress-beacon', {
      method: 'POST',
      body: JSON.stringify({}),
    }),
  beginSlipdrive: () =>
    apiRequest('/api/v1/recovery/slipdrive/begin', {
      method: 'POST',
      body: JSON.stringify({}),
    }),
  completeSlipdrive: () =>
    apiRequest('/api/v1/recovery/slipdrive/complete', {
      method: 'POST',
      body: JSON.stringify({}),
    }),
  escapePod: () =>
    apiRequest('/api/v1/recovery/escape-pod', {
      method: 'POST',
      body: JSON.stringify({}),
    }),
};

