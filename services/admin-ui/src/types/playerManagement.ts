// Enhanced Player Management Types
// Created: May 23, 2025
// Purpose: Comprehensive types for enhanced player analytics and management

// ARIA Personal Intelligence Summary
export interface ARIASummary {
  trust_level: number;  // 0-100
  recommendations_accepted: number;
  recommendations_total: number;
  data_points: number;
  model_status: string;  // "training" | "trained" | "inactive"
  trading_style: string;
  last_interaction: string | null;
  ai_generated_profits_7d: number;
  behavioral_classification: string;
  most_used_features: Array<{
    feature_name: string;
    usage_count: number;
  }>;
}

// Base model matching the actual API response
export interface PlayerModel {
  // Core identification (from API)
  id: string;
  username: string;
  email: string;

  // Game state (from API)
  credits: number;
  turns: number;
  current_sector_id: number | null;
  current_region_id: string | null;
  current_ship_id: string | null;
  team_id: string | null;
  is_active: boolean;
  last_login: string | null;
  created_at: string;

  // Asset counts (from API)
  ships_count: number;
  planets_count: number;
  stations_count: number;

  // Computed/derived fields for UI
  status: 'active' | 'inactive' | 'banned';
  assets: {
    ships_count: number;
    planets_count: number;
    stations_count: number;
    total_value: number;
  };
  activity: {
    last_login: string | null;
    session_count_today: number;
    actions_today: number;
    total_trade_volume: number;
    combat_rating: number;
    suspicious_activity: boolean;
  };

  // ARIA Personal Intelligence (null if no data collected yet)
  aria: ARIASummary | null;
}

export interface FactionReputations {
  terran_federation: FactionReputation;
  mercantile_guild: FactionReputation;
  frontier_coalition: FactionReputation;
  astral_mining_consortium: FactionReputation;
  nova_scientific_institute: FactionReputation;
  fringe_alliance: FactionReputation;
}

export interface FactionReputation {
  value: number;
  level: string;
  level_numeric: number;
}

export interface ShipSummary {
  id: string;
  name: string;
  type: string;
  sector_id: number;
  maintenance: number;
  value: number;
}

export interface PlanetSummary {
  id: string;
  name: string;
  type: string;
  sector_id: number;
  colonists: number;
  value: number;
}

export interface PortSummary {
  id: string;
  name: string;
  class: number;
  sector_id: number;
  daily_income: number;
  value: number;
}

export interface PlayerFilters {
  search: string;
  status: 'all' | 'active' | 'inactive' | 'banned';
  team: string | null;
  minCredits: number | null;
  maxCredits: number | null;
  lastLoginAfter: Date | null;
  lastLoginBefore: Date | null;
  reputationFilter: ReputationFilter | null;
  hasShips: boolean | null;
  hasPlanets: boolean | null;
  hasPorts: boolean | null;
  onlineOnly: boolean;
  suspiciousActivity: boolean;
}

export interface ReputationFilter {
  faction: string;
  minLevel: number;
  maxLevel: number;
}

export interface PlayerUpdates {
  credits?: number;
  turns?: number;
  current_sector_id?: number;
  home_sector_id?: number;
  is_active?: boolean;
  reputation?: Partial<FactionReputations>;
  admin_notes?: string;
  team_id?: string | null;
  attack_drones?: number;
  defense_drones?: number;
  mines?: number;
  reason: string;
}

export interface AssetTransfer {
  from_player_id: string;
  to_player_id: string;
  assets: {
    ship_ids?: string[];
    planet_ids?: string[];
    port_ids?: string[];
    credits?: number;
    items?: {
      attack_drones?: number;
      defense_drones?: number;
      mines?: number;
    };
  };
  reason: string;
  notify_players: boolean;
}

export interface EmergencyAction {
  type: 'RESCUE' | 'TELEPORT' | 'RESTORE' | 'COMPENSATE';
  parameters: {
    target_sector_id?: number;
    restore_to_date?: string;
    compensation_amount?: number;
    reason: string;
  };
}

export interface BulkOperation {
  player_ids: string[];
  operation: 'CREDIT_ADJUST' | 'TURN_GRANT' | 'STATUS_CHANGE' | 'REPUTATION_ADJUST';
  parameters: {
    amount?: number;
    new_status?: string;
    reputation_changes?: ReputationChange[];
    reason: string;
  };
}

export interface ReputationChange {
  faction: string;
  new_value: number;
}

export interface ActivityLogEntry {
  id: string;
  timestamp: string;
  action_type: string;
  details: string;
  sector_id?: number;
  target_id?: string;
  result?: string;
}

export interface PlayerMetrics {
  total_active_players: number;
  total_credits_circulation: number;
  average_session_time: number;
  new_players_today: number;
  player_retention_rate: number;
  players_online_now: number;
  total_players: number;
  banned_players: number;
  suspicious_activity_alerts: number;
}

export interface ValidationError {
  field: string;
  message: string;
  value?: any;
}

export interface PlayerListResponse {
  players: PlayerModel[];
  total: number;
  page: number;
  total_pages: number;
  metrics?: PlayerMetrics;
}

export interface PlayerAnalyticsState {
  // Data
  players: PlayerModel[];
  selectedPlayer: PlayerModel | null;
  totalCount: number;
  currentPage: number;
  metrics: PlayerMetrics | null;
  
  // UI State
  editMode: boolean;
  unsavedChanges: boolean;
  loading: boolean;
  errors: ValidationError[];
  
  // Filtering & Search
  filters: PlayerFilters;
  
  // Sorting & Pagination
  sortBy: 'username' | 'credits' | 'turns' | 'created_at' | 'last_login';
  sortOrder: 'asc' | 'desc';
  pageSize: number;
  
  // Real-time features
  realTimeUpdates: boolean;
  selectedPlayers: string[];
  
  // Modal state
  showBulkOperations: boolean;
  showEmergencyOps: boolean;
  showAssetManager: boolean;
  showActivityMonitor: boolean;
}

// API Response types
export interface ApiResponse<T> {
  success: boolean;
  data?: T;
  error?: string;
  message?: string;
}

export interface OperationResult {
  success: boolean;
  message: string;
  affected_players?: number;
  details?: any;
}