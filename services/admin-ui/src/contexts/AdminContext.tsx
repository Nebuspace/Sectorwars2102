import React, { createContext, useCallback, useContext, useState, useEffect, ReactNode } from 'react';
import { useAuth } from './AuthContext';
import { api } from '../utils/auth';
import type {
  BangConfig,
  BangJobListItem,
  BangJobResponse,
} from '../components/universe/bang/types';
import {
  createBangJob,
  listBangJobs,
  wipeBangGalaxy,
} from '../services/bangGalaxyApi';
import { formatAdminApiError } from '../utils/adminApiError';
import { adminHttpErrorMessage } from '../utils/adminHttpError';

// Types for admin context
export interface AdminStats {
  totalUsers: number;
  activePlayers: number;
  totalSectors: number;
  totalPlanets: number;
  totalPorts: number;
  totalShips: number;
  playerSessions: number;
}

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

export interface SectorGenerationConfig {
  region_id?: string;
  cluster_id?: string;
  sector_type?: 'normal' | 'nebula' | 'black_hole' | 'asteroid_field';
  resource_richness?: 'poor' | 'average' | 'rich' | 'abundant';
}

export interface GalaxyState {
  id: string;
  name: string;
  created_at: string;
  statistics: GalaxyStats;
  state: {
    age_in_days: number;
    economic_health: number;
    exploration_percentage: number;
  };
}

export interface Region {
  id: string;
  display_name: string;
  region_type: 'central_nexus' | 'terran_space' | 'player_owned';
  total_sectors: number;
  created_at: string;
  statistics?: {
    total_sectors: number;
    discovered_sectors: number;
    station_count: number;
    planet_count: number;
  };
}

export interface Zone {
  id: string;
  region_id: string;
  name: string;
  zone_type: string;  // EXPANSE, FEDERATION, BORDER, FRONTIER
  start_sector: number;
  end_sector: number;
  sector_count: number;
  policing_level: number;
  danger_rating: number;
  created_at: string;
  actual_sector_count?: number;
  avg_security_level?: number;
}

export interface Cluster {
  id: string;
  name: string;
  type: string;
  sector_count: number;
  region_id: string;
}

export interface UserAccount {
  id: string;
  username: string;
  email: string | null;
  is_active: boolean;
  is_admin: boolean;
  created_at: string;
  last_login: string | null;
  verified: boolean;
}

export interface PlayerAccount {
  id: string;
  user_id: string;
  username: string;
  email: string;
  credits: number;
  turns: number;
  last_game_login: string | null;
  current_sector_id: number;
  current_ship_id: string | null;
  ships_count: number;
  planets_count: number;
  stations_count: number;
  team_id: string | null;
  is_active: boolean;
  status: string;
  created_at: string | null;
  last_login: string | null;
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
  aria: any | null;  // Full ARIA type defined in playerManagement.ts
}

export interface SectorData {
  id: string;
  sector_id: number;
  name: string;
  type: string;
  cluster_id: string;
  region_name: string;
  zone_id: string | null;
  zone_name: string | null;
  zone_type: string | null;
  x_coord: number;
  y_coord: number;
  z_coord: number;
  hazard_level: number;
  is_discovered: boolean;
  has_port: boolean;
  has_planet: boolean;
  has_warp_tunnel: boolean;
  resource_richness: string;
  controlling_faction: string | null;
}

interface AdminContextType {
  // Stats and overview
  adminStats: AdminStats | null;
  loadAdminStats: () => Promise<void>;

  // Galaxy management
  galaxyState: GalaxyState | null;
  regions: Region[];
  zones: Zone[];
  clusters: Cluster[];
  loadGalaxyInfo: () => Promise<void>;
  loadRegions: () => Promise<void>;
  loadRegionZones: (regionId: string) => Promise<void>;
  loadClusters: (regionId?: string) => Promise<void>;
  addSectors: (galaxyId: string, numSectors: number, config?: SectorGenerationConfig) => Promise<void>;
  createWarpTunnel: (sourceSectorId: number, targetSectorId: number, stability?: number) => Promise<void>;
  clearGalaxyData: () => Promise<void>;

  // Sector data for visualization
  sectors: SectorData[];
  loadSectors: (regionId?: string, zoneId?: string, clusterId?: string, limit?: number, offset?: number) => Promise<void>;

  // User management
  users: UserAccount[];
  players: PlayerAccount[];
  loadUsers: () => Promise<void>;
  loadPlayers: () => Promise<void>;
  activateUser: (userId: string) => Promise<void>;
  deactivateUser: (userId: string) => Promise<void>;

  // sw2102-bang generation (Phase 3 — admin UI integration)
  bangHistory: BangJobListItem[];
  bangHistoryTotal: number;
  bangGalaxy: (config: BangConfig, galaxyName?: string) => Promise<BangJobResponse | null>;
  loadBangHistory: (page?: number, pageSize?: number) => Promise<void>;
  wipeGalaxy: (galaxyId: string, confirmName: string) => Promise<void>;

  // Loading and error state
  isLoading: boolean;
  error: string | null;
}

const AdminContext = createContext<AdminContextType | undefined>(undefined);

export const AdminProvider: React.FC<{ children: ReactNode }> = ({ children }) => {
  const { user, token } = useAuth();
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  
  // Stats and overview
  const [adminStats, setAdminStats] = useState<AdminStats | null>(null);

  // Galaxy management
  const [galaxyState, setGalaxyState] = useState<GalaxyState | null>(null);
  const [regions, setRegions] = useState<Region[]>([]);
  const [zones, setZones] = useState<Zone[]>([]);
  const [clusters, setClusters] = useState<Cluster[]>([]);
  const [sectors, setSectors] = useState<SectorData[]>([]);
  
  // User management
  const [users, setUsers] = useState<UserAccount[]>([]);
  const [players, setPlayers] = useState<PlayerAccount[]>([]);

  // sw2102-bang state
  const [bangHistory, setBangHistory] = useState<BangJobListItem[]>([]);
  const [bangHistoryTotal, setBangHistoryTotal] = useState<number>(0);
  
  // Load admin stats
  const loadAdminStats = async () => {
    if (!user || !user.is_admin) return;

    setIsLoading(true);
    setError(null);

    try {
      // Backend returns snake_case, we need to map to camelCase for TypeScript interface
      const response = await api.get<any>('/api/v1/admin/stats');

      // Map snake_case API response to camelCase interface
      const mappedStats: AdminStats = {
        totalUsers: response.data.total_users || 0,
        activePlayers: response.data.total_players || 0,
        totalSectors: response.data.total_sectors || 0,
        totalPlanets: response.data.total_planets || 0,
        totalPorts: response.data.total_ports || 0,
        totalShips: response.data.total_ships || 0,
        playerSessions: response.data.active_sessions || 0
      };

      setAdminStats(mappedStats);
    } catch (error) {
      console.error('Error loading admin stats:', error);
      setError(adminHttpErrorMessage(error, 'Failed to load admin statistics', 'PLAYERS_VIEW'));
      setAdminStats(null);
    } finally {
      setIsLoading(false);
    }
  };
  
  // Load galaxy info — memoized so callers passing it as a useEffect dep
  // do not trigger an infinite render-and-fetch loop. Without useCallback
  // every AdminProvider render creates a new function identity, which fires
  // every dependent useEffect, which calls setGalaxyState, which re-renders
  // the provider, which... (rate limiter just catches the loop).
  const loadGalaxyInfo = useCallback(async () => {
    if (!user || !user.is_admin) {
      return;
    }

    setIsLoading(true);
    setError(null);

    try {
      const response = await api.get<GalaxyState | {galaxy: null}>('/api/v1/admin/galaxy');

      if (response.data && 'galaxy' in response.data && response.data.galaxy === null) {
        setGalaxyState(null);
      } else if (response.data && 'id' in response.data) {
        setGalaxyState(response.data as GalaxyState);
      } else {
        console.warn('Unexpected galaxy API response format:', response.data);
        setGalaxyState(null);
      }
    } catch (error) {
      console.error('Error loading galaxy info:', error);
      setError(adminHttpErrorMessage(error, 'Failed to load galaxy information', 'admin.galaxy.manage'));
      setGalaxyState(null);
    } finally {
      setIsLoading(false);
    }
  }, [user]);
  
  // Load regions
  const loadRegions = async () => {
    if (!user || !user.is_admin) return;

    setIsLoading(true);
    setError(null);

    try {
      const response = await api.get<{regions: Region[]}>('/api/v1/admin/regions');
      setRegions(response.data.regions || []);
    } catch (error) {
      console.error('Error loading regions:', error);
      setError(adminHttpErrorMessage(error, 'Failed to load regions', 'admin.galaxy.manage'));
    } finally {
      setIsLoading(false);
    }
  };

  // Load zones for a specific region
  const loadRegionZones = async (regionId: string) => {
    if (!user || !user.is_admin) return;

    setIsLoading(true);
    setError(null);

    try {
      const response = await api.get<{zones: Zone[]}>(`/api/v1/admin/regions/${regionId}/zones`);
      setZones(response.data.zones || []);
    } catch (error) {
      console.error('Error loading region zones:', error);
      setError(adminHttpErrorMessage(error, 'Failed to load region zones', 'admin.galaxy.manage'));
    } finally {
      setIsLoading(false);
    }
  };
  
  // Load clusters for a region (non-blocking: does not set global error state)
  const loadClusters = async (regionId?: string) => {
    if (!user || !user.is_admin) return;

    try {
      let url = '/api/v1/admin/clusters';
      if (regionId) {
        url = `/api/v1/admin/regions/${regionId}/clusters`;
      }

      const response = await api.get<{clusters: Cluster[]}>(url);
      setClusters(response.data.clusters || []);
    } catch (error) {
      console.error('Error loading clusters:', error);
      // Clusters are optional/non-critical - don't set global error state
      setClusters([]);
    }
  };
  
  // Legacy generateGalaxy was removed in the bang cutover (Phase 4A).
  // Galaxy generation is now handled via the bang flow:
  //   AdminContext.bangGalaxy → POST /api/v1/admin/galaxy/jobs
  // See services/admin-ui/src/components/universe/bang/ for the new form +
  // history + log + wipe UI mounted at /universe/bang.

  // Add sectors to an existing galaxy
  const addSectors = async (galaxyId: string, numSectors: number, config?: SectorGenerationConfig) => {
    if (!user || !user.is_admin) return;

    setIsLoading(true);
    setError(null);

    try {
      await api.post(`/api/v1/admin/galaxy/${galaxyId}/sectors/add`, {
        num_sectors: numSectors,
        config
      });

      // After adding sectors, reload galaxy info
      await loadGalaxyInfo();
      await loadRegions();

      // If a region was specified, reload its zones and clusters
      if (config?.region_id) {
        await loadRegionZones(config.region_id);
        await loadClusters(config.region_id);
      }
    } catch (error) {
      console.error('Error adding sectors:', error);
      setError(adminHttpErrorMessage(error, 'Failed to add sectors to galaxy', 'admin.galaxy.manage'));
      throw error; // Re-throw to allow component to handle it
    } finally {
      setIsLoading(false);
    }
  };

  // Clear all galaxy data
  const clearGalaxyData = async () => {
    if (!user || !user.is_admin) return;

    setIsLoading(true);
    setError(null);

    try {
      await api.delete('/api/v1/admin/galaxy/clear');

      // After clearing, reset all state
      setGalaxyState(null);
      setRegions([]);
      setZones([]);
      setClusters([]);
      setSectors([]);

    } catch (error) {
      console.error('Error clearing galaxy data:', error);
      setError(adminHttpErrorMessage(error, 'Failed to clear galaxy data', 'admin.galaxy.manage'));
      throw error;
    } finally {
      setIsLoading(false);
    }
  };

  // Create a warp tunnel between two sectors
  const createWarpTunnel = async (sourceSectorId: number, targetSectorId: number, stability?: number) => {
    if (!user || !user.is_admin) return;
    
    setIsLoading(true);
    setError(null);
    
    try {
      await api.post('/api/v1/admin/warp-tunnels/create', {
        source_sector_id: sourceSectorId,
        target_sector_id: targetSectorId,
        stability: stability ?? 0.75 // Default to 75% stability if not specified
      });
      
      // After creating tunnel, reload galaxy info
      await loadGalaxyInfo();
    } catch (error) {
      console.error('Error creating warp tunnel:', error);
      setError(adminHttpErrorMessage(error, 'Failed to create warp tunnel', 'admin.galaxy.manage'));
      throw error; // Re-throw to allow component to handle it
    } finally {
      setIsLoading(false);
    }
  };
  
  // Load sectors for visualization
  const loadSectors = async (): Promise<void> => {
    if (!user || !user.is_admin || !galaxyState) {
      return;
    }
    
    setIsLoading(true);
    setError(null);
    
    try {
      const response = await api.get<{sectors: SectorData[]}>('/api/v1/admin/sectors');
      setSectors(response.data.sectors || []);
    } catch (error) {
      console.error('Error loading sectors:', error);
      setError(adminHttpErrorMessage(error, 'Failed to load sectors', 'admin.galaxy.manage'));
    } finally {
      setIsLoading(false);
    }
  };
  
  // Load user accounts
  const loadUsers = async () => {
    if (!user || !user.is_admin) return;
    
    setIsLoading(true);
    setError(null);
    
    try {
      const response = await api.get<{users: UserAccount[]}>('/api/v1/admin/users');
      setUsers(response.data.users || []);
    } catch (error) {
      console.error('Error loading users:', error);
      setError(adminHttpErrorMessage(error, 'Failed to load user accounts', 'PLAYERS_VIEW'));
    } finally {
      setIsLoading(false);
    }
  };
  
  // Load player accounts
  const loadPlayers = async () => {
    if (!user || !user.is_admin) return;
    
    setIsLoading(true);
    setError(null);
    
    try {
      const response = await api.get<{players: PlayerAccount[]}>('/api/v1/admin/players');
      setPlayers(response.data.players || []);
    } catch (error) {
      console.error('Error loading players:', error);
      setError(adminHttpErrorMessage(error, 'Failed to load player accounts', 'PLAYERS_VIEW'));
    } finally {
      setIsLoading(false);
    }
  };
  
  // Activate a user account.
  // There are no dedicated activate/deactivate endpoints — the real user CRUD
  // lives at /api/v1/users/* (users.py) and PUT /users/{id} accepts a partial
  // update with an is_active flag.
  const activateUser = async (userId: string) => {
    if (!user || !user.is_admin) return;

    setIsLoading(true);
    setError(null);

    try {
      await api.put(`/api/v1/users/${userId}`, { is_active: true });

      // Update local state
      setUsers(users.map(u =>
        u.id === userId ? { ...u, is_active: true } : u
      ));
    } catch (error) {
      console.error('Error activating user:', error);
      setError(adminHttpErrorMessage(error, 'Failed to activate user account', 'PLAYERS_VIEW'));
    } finally {
      setIsLoading(false);
    }
  };

  // Deactivate a user account (PUT /users/{id} — see activateUser)
  const deactivateUser = async (userId: string) => {
    if (!user || !user.is_admin) return;

    setIsLoading(true);
    setError(null);

    try {
      await api.put(`/api/v1/users/${userId}`, { is_active: false });

      // Update local state
      setUsers(users.map(u =>
        u.id === userId ? { ...u, is_active: false } : u
      ));
    } catch (error) {
      console.error('Error deactivating user:', error);
      setError(adminHttpErrorMessage(error, 'Failed to deactivate user account', 'PLAYERS_VIEW'));
    } finally {
      setIsLoading(false);
    }
  };
  
  // ---------------------------------------------------------------------
  // sw2102-bang integration (Phase 3)
  //
  // These wrap the bang-galaxy API helpers and mirror the existing
  // setIsLoading / setError pattern so the rest of the app can opt in
  // without bespoke state. The actual HTTP plumbing lives in
  // `services/bangGalaxyApi.ts`; the live SSE log is consumed by the
  // dedicated `useBangGenerationStream` hook (not by this context).
  // ---------------------------------------------------------------------

  const bangGalaxy = useCallback(
    async (config: BangConfig, galaxyName?: string): Promise<BangJobResponse | null> => {
      if (!user || !user.is_admin) return null;
      setIsLoading(true);
      setError(null);
      try {
        const job = await createBangJob({ config, galaxy_name: galaxyName }, token);
        return job;
      } catch (err) {
        console.error('Error starting bang generation job:', err);
        setError(adminHttpErrorMessage(err, 'Failed to start bang generation job', 'BANG_REGENERATE'));
        throw err;
      } finally {
        setIsLoading(false);
      }
    },
    [user, token],
  );

  const loadBangHistory = useCallback(
    async (page: number = 0, pageSize: number = 20): Promise<void> => {
      if (!user || !user.is_admin) return;
      setIsLoading(true);
      setError(null);
      try {
        const result = await listBangJobs(page, pageSize, token);
        setBangHistory(result.items ?? []);
        setBangHistoryTotal(result.total ?? 0);
      } catch (err) {
        // Endpoint is live (GET /admin/galaxy/jobs); surface real failures.
        console.error('Error loading bang history:', err);
        setBangHistory([]);
        setBangHistoryTotal(0);
        setError(adminHttpErrorMessage(err, 'Failed to load bang generation history', 'BANG_REGENERATE'));
        throw err;
      } finally {
        setIsLoading(false);
      }
    },
    [user, token],
  );

  const wipeGalaxy = useCallback(
    async (galaxyId: string, confirmName: string): Promise<void> => {
      if (!user || !user.is_admin) return;
      setIsLoading(true);
      setError(null);
      try {
        await wipeBangGalaxy(galaxyId, confirmName, token);
        // After a wipe the canonical galaxy is gone; reset all derived state.
        setGalaxyState(null);
        setRegions([]);
        setZones([]);
        setClusters([]);
        setSectors([]);
      } catch (err) {
        console.error('Error wiping galaxy:', err);
        // LEG-1315: context-layer wipe must distinguish RBAC/rate-limit from bare Failed
        setError(
          formatAdminApiError(err, {
            fallback: 'Failed to wipe galaxy',
            scopeHint: 'admin.universe.manage',
          }),
        );
        throw err;
      } finally {
        setIsLoading(false);
      }
    },
    [user, token],
  );

  // Load initial data when user logs in
  useEffect(() => {
    if (user && user.is_admin) {
      loadAdminStats();
      loadGalaxyInfo();
      loadUsers();
      loadPlayers();
    }
  }, [user]);

  // Load regions when galaxy is loaded
  useEffect(() => {
    if (galaxyState) {
      loadRegions();
    }
  }, [galaxyState]);
  
  const value = {
    // Stats and overview
    adminStats,
    loadAdminStats,

    // Galaxy management
    galaxyState,
    regions,
    zones,
    clusters,
    loadGalaxyInfo,
    loadRegions,
    loadRegionZones,
    loadClusters,
    addSectors,
    createWarpTunnel,
    clearGalaxyData,

    // Sector data for visualization
    sectors,
    loadSectors,

    // User management
    users,
    players,
    loadUsers,
    loadPlayers,
    activateUser,
    deactivateUser,

    // sw2102-bang generation
    bangHistory,
    bangHistoryTotal,
    bangGalaxy,
    loadBangHistory,
    wipeGalaxy,

    // Loading and error state
    isLoading,
    error
  };
  
  return <AdminContext.Provider value={value}>{children}</AdminContext.Provider>;
};

// Hook for using the admin context
export const useAdmin = () => {
  const context = useContext(AdminContext);
  if (context === undefined) {
    throw new Error('useAdmin must be used within an AdminProvider');
  }
  return context;
};