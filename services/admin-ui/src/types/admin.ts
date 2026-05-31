/**
 * Comprehensive Admin UI Type Definitions
 * Based on DOCS specifications for full game management
 */

// Core Admin Types
export interface AdminUser {
  id: string;
  username: string;
  email: string;
  is_admin: boolean;
  is_active: boolean;
  created_at: string;
  last_login: string | null;
  permissions: AdminPermission[];
}

export interface AdminPermission {
  id: string;
  name: string;
  category: PermissionCategory;
  description: string;
  granted_at: string;
}

export enum PermissionCategory {
  GALAXY_MANAGEMENT = 'galaxy_management',
  PLAYER_MANAGEMENT = 'player_management',
  UNIVERSE_EDITING = 'universe_editing',
  ECONOMY_CONTROL = 'economy_control',
  COMBAT_MONITORING = 'combat_monitoring',
  SYSTEM_ADMINISTRATION = 'system_administration'
}

// Galaxy Management Types
export interface GalaxyStats {
  total_sectors: number;
  discovered_sectors: number;
  station_count: number;
  planet_count: number;
  player_count: number;
  team_count: number;
  warp_tunnel_count: number;
  sector_warp_count?: number;  // In-region adjacency graph count; back-end may be older
  genesis_count: number;
}

export interface GalaxyConfiguration {
  name: string;
  total_sectors: number;
  zone_distribution: {  // Cosmological zones (Federation/Border/Frontier) - NOT business territories
    federation: number;
    border: number;
    frontier: number;
  };
  density: {
    station_density: number;
    planet_density: number;
    one_way_warp_percentage: number;
  };
  warp_tunnel_config: {
    min_per_region: number;
    max_per_region: number;
    stability_range: { min: number; max: number };
  };
  resource_distribution: {
    federation: { min: number; max: number };
    border: { min: number; max: number };
    frontier: { min: number; max: number };
  };
  hazard_levels: {
    federation: { min: number; max: number };
    border: { min: number; max: number };
    frontier: { min: number; max: number };
  };
}

// Player Management Types
export interface PlayerManagementData {
  id: string;
  username: string;
  email: string;
  credits: number;
  turns: number;
  current_sector_id: number;
  current_ship_id: string | null;
  team_id: string | null;
  reputation: PlayerFactionReputations;
  ships_owned: string[];
  planets_owned: string[];
  ports_owned: string[];
  is_active: boolean;
  last_login: string;
  created_at: string;
  first_login_completed: boolean;
}

export interface PlayerFactionReputations {
  terran_federation: PlayerReputation;
  mercantile_guild: PlayerReputation;
  frontier_coalition: PlayerReputation;
  astral_mining_consortium: PlayerReputation;
  nova_scientific_institute: PlayerReputation;
  fringe_alliance: PlayerReputation;
}

export interface PlayerReputation {
  value: number;
  level: string;
  history: ReputationHistoryEntry[];
}

export interface ReputationHistoryEntry {
  timestamp: string;
  change: number;
  reason: string;
  source: string;
}

// Ship Management Types
export interface ShipManagementData {
  id: string;
  name: string;
  type: ShipType;
  owner_id: string;
  owner_name: string;
  current_sector_id: number;
  maintenance_rating: number;
  cargo_used: number;
  cargo_capacity: number;
  is_active: boolean;
  insurance_active: boolean;
  created_at: string;
}

export enum ShipType {
  LIGHT_FREIGHTER = 'LIGHT_FREIGHTER',
  CARGO_HAULER = 'CARGO_HAULER',
  FAST_COURIER = 'FAST_COURIER',
  SCOUT_SHIP = 'SCOUT_SHIP',
  COLONY_SHIP = 'COLONY_SHIP',
  DEFENDER = 'DEFENDER',
  CARRIER = 'CARRIER',
  WARP_JUMPER = 'WARP_JUMPER'
}

// Universe Structure Types
export interface SectorManagementData {
  id: string;
  sector_id: number;
  name: string;
  type: SectorType;
  cluster_id: string;
  region_name: string;
  x_coord: number;
  y_coord: number;
  z_coord: number;
  hazard_level: number;
  is_discovered: boolean;
  has_port: boolean;
  has_planet: boolean;
  has_warp_tunnel: boolean;
  player_count: number;
  controlling_faction: string | null;
}

export enum SectorType {
  STANDARD = 'STANDARD',
  NEBULA = 'NEBULA',
  ASTEROID_FIELD = 'ASTEROID_FIELD',
  BLACK_HOLE = 'BLACK_HOLE',
  STAR_CLUSTER = 'STAR_CLUSTER',
  VOID = 'VOID',
  INDUSTRIAL = 'INDUSTRIAL',
  AGRICULTURAL = 'AGRICULTURAL',
  FORBIDDEN = 'FORBIDDEN',
  WORMHOLE = 'WORMHOLE'
}

export interface PortManagementData {
  id: string;
  name: string;
  sector_id: number;
  port_class: PortClass;
  type: PortType;
  owner_id: string | null;
  owner_name: string | null;
  faction_affiliation: string | null;
  defense_level: number;
  trade_volume_24h: number;
  tax_rate: number;
  services: PortService[];
  is_operational: boolean;
}

export enum PortClass {
  CLASS_0 = 'CLASS_0',
  CLASS_1 = 'CLASS_1',
  CLASS_2 = 'CLASS_2',
  CLASS_3 = 'CLASS_3',
  CLASS_4 = 'CLASS_4',
  CLASS_5 = 'CLASS_5',
  CLASS_6 = 'CLASS_6',
  CLASS_7 = 'CLASS_7',
  CLASS_8 = 'CLASS_8',
  CLASS_9 = 'CLASS_9',
  CLASS_10 = 'CLASS_10',
  CLASS_11 = 'CLASS_11'
}

export enum PortType {
  TRADING = 'TRADING',
  MILITARY = 'MILITARY',
  INDUSTRIAL = 'INDUSTRIAL',
  MINING = 'MINING',
  SCIENTIFIC = 'SCIENTIFIC',
  SHIPYARD = 'SHIPYARD',
  OUTPOST = 'OUTPOST',
  BLACK_MARKET = 'BLACK_MARKET',
  DIPLOMATIC = 'DIPLOMATIC',
  CORPORATE = 'CORPORATE'
}

export interface PortService {
  name: string;
  available: boolean;
  price_modifier: number;
}

// Planet Management Types
export interface PlanetManagementData {
  id: string;
  name: string;
  sector_id: number;
  type: PlanetType;
  owner_id: string | null;
  owner_name: string | null;
  population: number;
  max_population: number;
  habitability_score: number;
  resource_richness: number;
  production_efficiency: number;
  defense_level: number;
  colonized_at: string | null;
  genesis_created: boolean;
}

export enum PlanetType {
  TERRAN = 'TERRAN',
  DESERT = 'DESERT',
  OCEANIC = 'OCEANIC',
  ICE = 'ICE',
  VOLCANIC = 'VOLCANIC',
  GAS_GIANT = 'GAS_GIANT',
  BARREN = 'BARREN',
  JUNGLE = 'JUNGLE',
  ARCTIC = 'ARCTIC',
  TROPICAL = 'TROPICAL',
  MOUNTAINOUS = 'MOUNTAINOUS',
  ARTIFICIAL = 'ARTIFICIAL'
}

// Combat & Events Types
export interface CombatLogEntry {
  id: string;
  timestamp: string;
  combat_type: CombatType;
  combat_result: CombatResult;
  sector_id: number;
  attacker_id: string;
  attacker_name: string;
  defender_id: string | null;
  defender_name: string | null;
  turns_consumed: number;
  combat_rounds: number;
  attacker_ship_destroyed: boolean;
  defender_ship_destroyed: boolean;
  credits_transferred: number;
  reputation_changes: ReputationChange[];
}

export enum CombatType {
  SHIP_VS_SHIP = 'SHIP_VS_SHIP',
  SHIP_VS_PLANET = 'SHIP_VS_PLANET',
  SHIP_VS_PORT = 'SHIP_VS_PORT',
  SHIP_VS_DRONES = 'SHIP_VS_DRONES',
  PLANET_DEFENSE = 'PLANET_DEFENSE',
  PORT_DEFENSE = 'PORT_DEFENSE',
  SECTOR_DEFENSE = 'SECTOR_DEFENSE'
}

export enum CombatResult {
  ATTACKER_VICTORY = 'ATTACKER_VICTORY',
  DEFENDER_VICTORY = 'DEFENDER_VICTORY',
  DRAW = 'DRAW',
  ATTACKER_FLED = 'ATTACKER_FLED',
  DEFENDER_FLED = 'DEFENDER_FLED',
  MUTUAL_DESTRUCTION = 'MUTUAL_DESTRUCTION',
  ABANDONED = 'ABANDONED'
}

export interface ReputationChange {
  faction: string;
  change: number;
  reason: string;
}

// Team Management Types
export interface TeamManagementData {
  id: string;
  name: string;
  leader_id: string;
  leader_name: string;
  member_count: number;
  total_credits: number;
  average_reputation: number;
  created_at: string;
  is_active: boolean;
  recruitment_open: boolean;
}

export interface Team {
  id: string;
  name: string;
  tag: string;
  leader_id: string;
  leader_name: string;
  member_count: number;
  total_score: number;
  created_at: string;
  description?: string;
}

// Market & Economy Types
export interface MarketData {
  station_id: string;
  port_name: string;
  sector_id: number;
  resources: ResourceMarketData[];
  last_updated: string;
}

export interface ResourceMarketData {
  resource_type: ResourceType;
  buy_price: number;
  sell_price: number;
  quantity_available: number;
  demand_level: DemandLevel;
  price_trend: PriceTrend;
}

export enum ResourceType {
  ORE = 'ORE',
  ORGANICS = 'ORGANICS',
  EQUIPMENT = 'EQUIPMENT',
  LUXURY_GOODS = 'LUXURY_GOODS',
  MEDICAL_SUPPLIES = 'MEDICAL_SUPPLIES',
  TECHNOLOGY = 'TECHNOLOGY',
  FUEL = 'FUEL',
  POPULATION = 'POPULATION'
}

export enum DemandLevel {
  VERY_LOW = 'VERY_LOW',
  LOW = 'LOW',
  MODERATE = 'MODERATE',
  HIGH = 'HIGH',
  VERY_HIGH = 'VERY_HIGH'
}

export enum PriceTrend {
  FALLING = 'FALLING',
  STABLE = 'STABLE',
  RISING = 'RISING'
}

// System Monitoring Types
export interface SystemHealth {
  database_status: HealthStatus;
  api_response_time: number;
  active_players: number;
  active_sessions: number;
  error_rate_1h: number;
  memory_usage: number;
  cpu_usage: number;
  last_checked: string;
}

export enum HealthStatus {
  HEALTHY = 'HEALTHY',
  WARNING = 'WARNING',
  CRITICAL = 'CRITICAL',
  DOWN = 'DOWN'
}

// Event Types
export interface GameEvent {
  id: string;
  type: EventType;
  title: string;
  description: string;
  affected_regions: string[];
  start_time: string;
  end_time: string | null;
  status: EventStatus;
  effects: EventEffect[];
  player_participation: number;
}

export enum EventType {
  WARP_STORM = 'WARP_STORM',
  RESOURCE_BOOM = 'RESOURCE_BOOM',
  FACTION_CONFLICT = 'FACTION_CONFLICT',
  ECONOMIC_SHIFT = 'ECONOMIC_SHIFT',
  SPECIAL_DISCOVERY = 'SPECIAL_DISCOVERY',
  PIRATE_ACTIVITY = 'PIRATE_ACTIVITY',
  PLAGUE_OUTBREAK = 'PLAGUE_OUTBREAK',
  TECHNOLOGICAL_BREAKTHROUGH = 'TECHNOLOGICAL_BREAKTHROUGH'
}

export enum EventStatus {
  SCHEDULED = 'SCHEDULED',
  ACTIVE = 'ACTIVE',
  COMPLETED = 'COMPLETED',
  CANCELLED = 'CANCELLED'
}

export interface EventEffect {
  type: string;
  value: number;
  description: string;
}

// Admin Analytics Types
export interface AnalyticsDashboard {
  player_engagement: PlayerEngagementMetrics;
  economic_health: EconomicHealthMetrics;
  combat_activity: CombatActivityMetrics;
  exploration_progress: ExplorationMetrics;
  server_performance: ServerPerformanceMetrics;
}

export interface PlayerEngagementMetrics {
  daily_active_users: number;
  weekly_active_users: number;
  monthly_active_users: number;
  average_session_duration: number;
  new_registrations_24h: number;
  retention_rate_7d: number;
  retention_rate_30d: number;
}

export interface EconomicHealthMetrics {
  total_credits_in_circulation: number;
  average_player_wealth: number;
  wealth_inequality_gini: number;
  trade_volume_24h: number;
  market_volatility: number;
  resource_scarcity_index: number;
}

export interface CombatActivityMetrics {
  combat_events_24h: number;
  player_vs_player_rate: number;
  average_combat_duration: number;
  ship_destruction_rate: number;
  faction_conflict_intensity: number;
}

export interface ExplorationMetrics {
  sectors_discovered_24h: number;
  total_exploration_percentage: number;
  active_explorers: number;
  new_warp_tunnels_created: number;
  genesis_devices_used: number;
}

export interface ServerPerformanceMetrics {
  api_response_time_avg: number;
  database_query_time_avg: number;
  active_connections: number;
  memory_usage_percentage: number;
  cpu_usage_percentage: number;
  error_rate_24h: number;
}

// Admin Action Types
export interface AdminAction {
  id: string;
  admin_id: string;
  admin_name: string;
  action_type: AdminActionType;
  target_type: string;
  target_id: string;
  description: string;
  timestamp: string;
  success: boolean;
  error_message?: string;
}

export enum AdminActionType {
  PLAYER_BAN = 'PLAYER_BAN',
  PLAYER_UNBAN = 'PLAYER_UNBAN',
  CREDIT_ADJUSTMENT = 'CREDIT_ADJUSTMENT',
  REPUTATION_ADJUSTMENT = 'REPUTATION_ADJUSTMENT',
  SHIP_REPAIR = 'SHIP_REPAIR',
  SECTOR_EDIT = 'SECTOR_EDIT',
  PORT_EDIT = 'PORT_EDIT',
  PLANET_EDIT = 'PLANET_EDIT',
  GALAXY_REGENERATE = 'GALAXY_REGENERATE',
  EVENT_CREATE = 'EVENT_CREATE',
  EVENT_CANCEL = 'EVENT_CANCEL',
  SYSTEM_MAINTENANCE = 'SYSTEM_MAINTENANCE'
}