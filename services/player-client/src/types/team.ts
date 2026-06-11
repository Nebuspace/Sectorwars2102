// Team-related type definitions
export interface Team {
  id: string;
  name: string;
  tag: string; // 2-10 character team tag ('' when unset)
  description: string;
  leaderId: string;
  /** Canon gap: backend TeamResponse does not expose the leader's nickname */
  leaderName?: string;
  memberCount: number;
  maxMembers: number;
  /** Canon gap: backend TeamResponse exposes no team reputation field yet */
  reputation?: number;
  founded: string; // ISO timestamp (backend created_at)
  /** Canon gap: backend TeamResponse does not expose is_public */
  isPublic?: boolean;
  recruitmentStatus: 'open' | 'invite-only' | 'closed';
  combatRating: number;
  tradeRating: number;
  totalPlanets: number;
  treasury: {
    credits: number;
    /** Canon gap: backend TeamResponse only exposes treasury_credits today */
    fuel?: number;
    organics?: number;
    equipment?: number;
  };
}

export interface TeamMember {
  id: string;
  playerId: string;
  playerName: string;
  role: 'leader' | 'officer' | 'member' | 'recruit';
  joinedAt: string;
  contributions: {
    credits: number;
    resources: number;
    combatKills: number;
  };
  online: boolean;
  location: {
    sectorId: string;
    sectorName: string;
  };
  shipType: string;
  combatRating: number;
}

// --- Gameserver wire shapes (snake_case) -----------------------------------
// Mirror the pydantic response models in
// services/gameserver/src/api/routes/teams.py. Map these to the camelCase UI
// types above before rendering.

export interface TeamApiResponse {
  id: string;
  name: string;
  description: string | null;
  tag: string | null;
  logo: string | null;
  leader_id: string;
  recruitment_status: string; // TeamRecruitmentStatus: 'OPEN' | 'INVITE_ONLY' | 'CLOSED'
  max_members: number;
  member_count: number;
  total_credits: number;
  total_planets: number;
  combat_rating: number;
  trade_rating: number;
  created_at: string;
  treasury_credits: number;
}

export interface TeamMemberApiResponse {
  player_id: string;
  nickname: string;
  role: string; // TeamRole: 'LEADER' | 'OFFICER' | 'MEMBER' | 'RECRUIT'
  joined_at: string;
  last_active: string | null;
  can_invite: boolean;
  can_kick: boolean;
  can_manage_treasury: boolean;
  can_manage_missions: boolean;
  can_manage_alliances: boolean;
  contribution_credits: Record<string, number> | null;
  current_sector: number | null;
  combat_rating: number;
}

export interface TeamPermissionsApiResponse {
  can_invite: boolean;
  can_kick: boolean;
  can_manage_treasury: boolean;
  can_manage_missions: boolean;
  can_manage_alliances: boolean;
  is_member: boolean;
  role: string | null;
}

export interface TeamInvitation {
  id: string;
  teamId: string;
  teamName: string;
  teamTag: string;
  invitedBy: string;
  invitedAt: string;
  expiresAt: string;
  message?: string;
}

export interface TeamApplication {
  id: string;
  playerId: string;
  playerName: string;
  teamId: string;
  message: string;
  appliedAt: string;
  status: 'pending' | 'accepted' | 'rejected';
}

export interface TeamMessage {
  id: string;
  teamId: string;
  senderId: string;
  senderName: string;
  senderRole: 'leader' | 'officer' | 'member';
  content: string;
  timestamp: string;
  type: 'message' | 'system' | 'alert';
  readBy: string[]; // Array of player IDs who have read the message
}

export interface ResourceTransfer {
  id: string;
  teamId: string;
  fromPlayerId: string;
  fromPlayerName: string;
  toPlayerId: string;
  toPlayerName: string;
  resources: {
    credits?: number;
    fuel?: number;
    organics?: number;
    equipment?: number;
  };
  reason?: string;
  timestamp: string;
  status: 'pending' | 'completed' | 'cancelled';
}

export interface TeamMission {
  id: string;
  teamId: string;
  name: string;
  description: string;
  type: 'combat' | 'trading' | 'exploration' | 'defense' | 'siege';
  status: 'planning' | 'active' | 'completed' | 'failed';
  createdBy: string;
  createdAt: string;
  startTime?: string;
  endTime?: string;
  objectives: MissionObjective[];
  participants: string[]; // Player IDs
  rewards?: {
    credits?: number;
    reputation?: number;
    resources?: Record<string, number>;
  };
}

export interface MissionObjective {
  id: string;
  description: string;
  type: 'destroy' | 'capture' | 'deliver' | 'defend' | 'explore';
  targetId?: string;
  targetType?: 'sector' | 'ship' | 'planet' | 'port';
  requiredAmount?: number;
  currentAmount?: number;
  completed: boolean;
}

export interface Alliance {
  id: string;
  name: string;
  teams: {
    teamId: string;
    teamName: string;
    teamTag: string;
    joinedAt: string;
  }[];
  type: 'mutual-defense' | 'trade' | 'non-aggression';
  createdAt: string;
  expiresAt?: string;
  terms: string[];
}

export interface DiplomaticRelation {
  id: string;
  fromTeamId: string;
  fromTeamName: string;
  toTeamId: string;
  toTeamName: string;
  type: 'ally' | 'neutral' | 'hostile' | 'war';
  establishedAt: string;
  treaty?: {
    type: 'peace' | 'trade' | 'defense' | 'non-aggression';
    terms: string[];
    expiresAt?: string;
  };
}

export interface TeamAnalytics {
  teamId: string;
  period: 'day' | 'week' | 'month' | 'all-time';
  metrics: {
    combatStats: {
      kills: number;
      deaths: number;
      kdRatio: number;
      damageDealt: number;
      damageTaken: number;
    };
    economicStats: {
      creditsEarned: number;
      creditsSpent: number;
      resourcesGathered: number;
      resourcesTraded: number;
      profitMargin: number;
    };
    territoryStats: {
      sectorsControlled: number;
      planetsOwned: number;
      portsVisited: number;
      territoriesLost: number;
      territoriesGained: number;
    };
    memberStats: {
      averageOnlineTime: number;
      activeMembers: number;
      newRecruits: number;
      membersLost: number;
    };
  };
  topPerformers: {
    combat: TeamMember[];
    trading: TeamMember[];
    exploration: TeamMember[];
  };
}

export interface TeamPermissions {
  canInvite: boolean;
  canKick: boolean;
  canPromote: boolean;
  canManageTreasury: boolean;
  canStartMissions: boolean;
  canEditTeamInfo: boolean;
  canManageAlliances: boolean;
  canDeclareWar: boolean;
}