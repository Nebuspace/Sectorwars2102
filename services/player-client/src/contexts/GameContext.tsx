import React, { createContext, useContext, useState, useEffect, useRef, ReactNode } from 'react';
import axios from 'axios';
import { useAuth } from './AuthContext';

// Types for game state
export interface Ship {
  id: string;
  name: string;
  type: string;
  sector_id: number;
  cargo: Record<string, number>;
  cargo_capacity: number;
  current_speed: number;
  base_speed: number;
  combat: any;
  maintenance: any;
  is_flagship: boolean;
  purchase_value: number;
  current_value: number;
  genesis_devices: number;
  max_genesis_devices: number;
}

export interface Sector {
  id: number;
  sector_id: number;
  sector_number?: number;  // Display number (may differ from sector_id in Central Nexus)
  name: string;
  type: string;
  region_id?: string | null;
  region_name?: string | null;
  hazard_level: number;
  radiation_level: number;
  resources: Record<string, any>;
  players_present: any[];
  special_features?: string[];
  description?: string;
}

export interface Planet {
  id: string;
  name: string;
  type: string;
  status: string;
  sector_id: number;
  owner_id?: string | null;
  owner_name?: string | null;
  owner?: any;
  resources: Record<string, any>;
  population: number;
  max_population: number;
  habitability_score: number;
}

export interface Station {
  id: string;
  name: string;
  type: string;
  status: string;
  sector_id: number;
  owner?: any;
  services: Record<string, any>;
  faction_affiliation?: string;
  station_class?: string | number;
  is_spacedock?: boolean;
}

export interface MoveOption {
  sector_id: number;
  sector_number?: number;  // Display number
  name: string;
  type: string;
  region_id?: string | null;
  region_name?: string | null;
  turn_cost: number;
  can_afford: boolean;
  tunnel_type?: string;
  stability?: number;
}

export interface MarketInfo {
  resources: Record<string, {
    quantity: number;
    buy_price: number;
    sell_price: number;
    station_buys: boolean;
    station_sells: boolean;
    last_updated?: string;
  }>;
  port: {
    id: string;
    name: string;
    type: string;
    faction: string | null;
    tax_rate: number;
    station_class?: string | number;
    is_spacedock?: boolean;
    trade_volume?: number;
    trader_personality_type?: string;
  };
}

export interface PlayerState {
  id: string;
  username: string;
  credits: number;
  turns: number;
  current_sector_id: number;
  is_docked: boolean;
  is_landed: boolean;
  current_port_id?: string;
  current_planet_id?: string;
  defense_drones: number;
  attack_drones: number;
  current_ship_id?: string;
  team_id?: string;

  // Reputation and Ranking
  personal_reputation: number;
  reputation_tier: string;
  name_color: string;
  military_rank: string;
}

interface GameContextType {
  // Player info
  playerState: PlayerState | null;
  refreshPlayerState: () => Promise<void>;
  updatePlayerCredits: (newCredits: number) => void;
  updateShipGenesis: (genesisDevices: number) => void;

  // First login status
  needsFirstLogin: boolean;
  checkFirstLoginStatus: () => Promise<boolean>;
  onFirstLoginComplete: () => Promise<void>;
  
  // Player ships
  ships: Ship[];
  currentShip: Ship | null;
  loadShips: () => Promise<void>;
  setCurrentShip: (shipId: string) => Promise<void>;
  
  // Current location info
  currentSector: Sector | null;
  availableMoves: {
    warps: MoveOption[];
    tunnels: MoveOption[];
  };
  planetsInSector: Planet[];
  stationsInSector: Station[];
  
  // Movement
  moveToSector: (sectorId: number) => Promise<any>;
  getAvailableMoves: () => Promise<void>;
  
  // Station interactions
  dockAtStation: (stationId: string) => Promise<any>;
  undockFromStation: () => Promise<any>;
  marketInfo: MarketInfo | null;
  getMarketInfo: (stationId: string) => Promise<void>;
  buyResource: (stationId: string, resourceType: string, quantity: number) => Promise<any>;
  sellResource: (stationId: string, resourceType: string, quantity: number) => Promise<any>;
  
  // Planet interactions
  claimPlanet: (planetId: string) => Promise<any>;
  landOnPlanet: (planetId: string) => Promise<any>;
  leavePlanet: () => Promise<any>;
  renamePlanet: (planetId: string, newName: string) => Promise<any>;
  getPlanetDetails: (planetId: string) => Promise<any>;
  updatePlanetAllocation: (planetId: string, allocations: { fuel: number; organics: number; equipment: number; ore?: number; terraform?: number }) => Promise<any>;
  updatePlanetDefenses: (planetId: string, defenses: { turrets?: number; shields?: number; fighters?: number }) => Promise<any>;
  upgradePlanetBuilding: (planetId: string, buildingType: string, targetLevel: number) => Promise<any>;
  transferColonists: (planetId: string, action: 'embark' | 'disembark', quantity: number) => Promise<any>;
  depositToSafe: (planetId: string, resourceType: string, amount: number) => Promise<any>;
  withdrawFromSafe: (planetId: string, resourceType: string, amount: number) => Promise<any>;

  // Combat
  attackPlayer: (playerId: string) => Promise<any>;
  attackDrones: () => Promise<any>;
  
  // Loading states
  isLoading: boolean;
  error: string | null;
  
  // General methods
  exploreCurrentLocation: () => Promise<void>;
}

const GameContext = createContext<GameContextType | undefined>(undefined);

export const GameProvider: React.FC<{ children: ReactNode }> = ({ children }) => {
  const { user, isAuthenticated } = useAuth();
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  
  // Player state
  const [playerState, setPlayerState] = useState<PlayerState | null>(null);
  const [needsFirstLogin, setNeedsFirstLogin] = useState<boolean>(false);
  
  // Ships
  const [ships, setShips] = useState<Ship[]>([]);
  const [currentShip, setCurrentShip] = useState<Ship | null>(null);
  
  // Location
  const [currentSector, setCurrentSector] = useState<Sector | null>(null);
  const [availableMoves, setAvailableMoves] = useState<{ warps: MoveOption[], tunnels: MoveOption[] }>({
    warps: [],
    tunnels: []
  });
  const [planetsInSector, setPlanetsInSector] = useState<Planet[]>([]);
  const [stationsInSector, setStationsInSector] = useState<Station[]>([]);
  
  // Market
  const [marketInfo, setMarketInfo] = useState<MarketInfo | null>(null);
  
  // Use Vite proxy for all API requests to avoid CORS issues
  const getApiUrl = () => {
    // If an environment variable is explicitly set, use it
    if (import.meta.env.VITE_API_URL) {
      return import.meta.env.VITE_API_URL;
    }

    // Always use the current origin to leverage Vite proxy in Docker environments
    // This ensures all API calls go through the Vite dev server proxy
    return window.location.origin;  // Use current origin, which will use the proxy
  };

  // Set up axios with authorization header
  const api = axios.create({
    baseURL: getApiUrl(),
  });
  
  // Use token from localStorage directly instead of from context
  api.interceptors.request.use(config => {
    const token = localStorage.getItem('accessToken');
    if (token) {
      config.headers.Authorization = `Bearer ${token}`;
    }
    return config;
  });
  
  // Check first login status
  const checkFirstLoginStatus = async (): Promise<boolean> => {
    if (!user) return false;
    
    try {
      const response = await api.get('/api/v1/first-login/status');
      const needsFirstLogin = (response.data as any).requires_first_login;
      setNeedsFirstLogin(needsFirstLogin);
      return needsFirstLogin;
    } catch (error) {
      console.error('GameContext: Error checking first login status:', error);
      setNeedsFirstLogin(false);
      return false;
    }
  };

  // Track which user we've initialized for to prevent duplicate initialization
  const initializedForUser = useRef<string | null>(null);

  // Initialize game state when user logs in
  useEffect(() => {
    if (user && isAuthenticated) {
      // Prevent duplicate initialization for the same user
      if (initializedForUser.current === user.id) {
        return;
      }

      initializedForUser.current = user.id;

      checkFirstLoginStatus().then((needsFirst) => {
        if (!needsFirst) {
          refreshPlayerState();
          loadShips();
        }
      }).catch(() => {
        // On error, try to load player state anyway (might be a network issue)
        refreshPlayerState();
        loadShips();
      });
    } else {
      initializedForUser.current = null;
      setPlayerState(null);
      setCurrentShip(null);
      setShips([]);
      setNeedsFirstLogin(false);
    }
  }, [user, isAuthenticated]);
  
  // Update current location info when sector changes
  useEffect(() => {
    if (playerState?.current_sector_id) {
      exploreCurrentLocation();
      getAvailableMoves();
    }
  }, [playerState?.current_sector_id]);
  
  // Track if refresh is in progress to prevent duplicate calls
  const refreshInProgress = useRef(false);

  // Refresh player state
  const refreshPlayerState = async () => {
    if (!user) {
      return;
    }

    // Prevent duplicate concurrent calls
    if (refreshInProgress.current) {
      return;
    }

    refreshInProgress.current = true;
    setIsLoading(true);
    setError(null);
    
    try {
      const response = await api.get('/api/v1/player/state');
      setPlayerState(response.data as PlayerState);
      
      // If player has a current ship, load its details
      if ((response.data as any).current_ship_id) {
        try {
          const shipResponse = await api.get('/api/v1/player/current-ship');
          setCurrentShip(shipResponse.data as Ship);
        } catch (shipError) {
          console.warn('GameContext: Failed to load current ship details:', shipError);
          // Don't fail the whole state refresh if just ship loading fails
        }
      }
    } catch (error: any) {
      console.error('GameContext: Error fetching player state:', error);
      
      // Provide more detailed error messages
      if (error.response?.status === 401) {
        setError('Authentication required. Please log in again.');
      } else if (error.response?.status === 404) {
        // Check if this is because first login is needed
        checkFirstLoginStatus().then(needsFirst => {
          if (needsFirst) {
            setError(null); // Clear error since this is expected
          } else {
            setError('Player data not found. You may need to complete the first login process.');
          }
        }).catch(() => {
          setError('Player data not found. You may need to complete the first login process.');
        });
      } else if (error.response?.data?.detail) {
        setError(`Server error: ${error.response.data.detail}`);
      } else if (error.message) {
        setError(`Network error: ${error.message}`);
      } else {
        setError('Failed to load player state');
      }
    } finally {
      setIsLoading(false);
      refreshInProgress.current = false;
    }
  };

  // Load player's ships
  const loadShips = async () => {
    if (!user) return;
    
    setIsLoading(true);
    
    try {
      const response = await api.get('/api/v1/player/ships');
      setShips(response.data || []);
      
      // If there's a current ship, update it
      if (playerState?.current_ship_id) {
        const currentShip = response.data.find((ship: Ship) => ship.id === playerState.current_ship_id);
        if (currentShip) {
          setCurrentShip(currentShip);
        }
      }
    } catch (error: any) {
      console.warn('Failed to load ships:', error);

      // Don't set global error - ships failing shouldn't block the game
      // But do handle auth errors specifically since they affect everything
      if (error.response?.status === 401) {
        setError('Authentication required. Please log in again.');
      }
      setShips([]);
    } finally {
      setIsLoading(false);
    }
  };
  
  // Set current ship
  const setActiveShip = async (shipId: string) => {
    if (!user) return;
    
    setIsLoading(true);
    setError(null);
    
    try {
      await api.post(`/api/v1/ships/${shipId}/set-active`);
      
      // Update player state and ships
      await refreshPlayerState();
      await loadShips();
    } catch (error) {
      console.error('Error setting active ship:', error);
      setError('Failed to set active ship');
    } finally {
      setIsLoading(false);
    }
  };
  
  // Move to another sector
  const moveToSector = async (sectorId: number) => {
    if (!user || !playerState) return;
    
    setIsLoading(true);
    setError(null);
    
    try {
      const response = await api.post(`/api/v1/player/move/${sectorId}`);

      // Update player state after movement
      await refreshPlayerState();

      // Ships move with the player server-side; reload so Hangar
      // doesn't show the pre-move sector
      await loadShips();

      return response.data;
    } catch (error: any) {
      console.error('Error moving to sector:', error);
      setError(error.response?.data?.message || 'Failed to move to sector');
      throw error;
    } finally {
      setIsLoading(false);
    }
  };
  
  // Get available moves from current sector
  const getAvailableMoves = async () => {
    if (!user || !playerState) return;
    
    setIsLoading(true);
    setError(null);
    
    try {
      const response = await api.get('/api/v1/player/available-moves');
      setAvailableMoves(response.data);
    } catch (error) {
      console.error('Error getting available moves:', error);
      setError('Failed to get available moves');
    } finally {
      setIsLoading(false);
    }
  };

  // Explore current location (sector, planets, stations)
  const exploreCurrentLocation = async () => {
    if (!user || !playerState) return;
    
    setIsLoading(true);
    
    try {
      // Get sector info
      try {
        const sectorResponse = await api.get('/api/v1/player/current-sector');
        setCurrentSector(sectorResponse.data);
      } catch (sectorError) {
        console.warn('GameContext: Failed to load current sector:', sectorError);
        setCurrentSector(null);
      }
      
      // Get planets in sector
      try {
        const planetsResponse = await api.get(`/api/v1/sectors/${playerState.current_sector_id}/planets`);
        setPlanetsInSector(planetsResponse.data.planets || []);
      } catch (planetsError) {
        console.warn('GameContext: Failed to load planets:', planetsError);
        setPlanetsInSector([]);
      }
      
      // Get stations in sector
      try {
        const stationsResponse = await api.get(`/api/v1/sectors/${playerState.current_sector_id}/stations`);
        setStationsInSector(stationsResponse.data.stations || []);
      } catch (stationsError) {
        console.warn('GameContext: Failed to load stations:', stationsError);
        setStationsInSector([]);
      }
    } catch (error) {
      console.error('GameContext: Error exploring location:', error);
      // Don't set a general error here as this is not critical for basic UI
    } finally {
      setIsLoading(false);
    }
  };
  
  // Dock at a port
  const dockAtStation = async (stationId: string) => {
    if (!user || !playerState) return;
    
    setIsLoading(true);
    setError(null);
    
    try {
      const response = await api.post('/api/v1/trading/dock', { station_id: stationId });
      
      // Update player state after docking
      await refreshPlayerState();
      
      return response.data;
    } catch (error: any) {
      console.error('Error docking at port:', error);
      setError(error.response?.data?.message || 'Failed to dock at port');
      throw error;
    } finally {
      setIsLoading(false);
    }
  };

  // Undock from current station
  const undockFromStation = async () => {
    if (!user || !playerState) return;

    setIsLoading(true);
    setError(null);

    try {
      const response = await api.post('/api/v1/trading/undock');

      // Update player state after undocking
      await refreshPlayerState();

      return response.data;
    } catch (error: any) {
      console.error('Error undocking from station:', error);
      setError(error.response?.data?.message || 'Failed to undock from station');
      throw error;
    } finally {
      setIsLoading(false);
    }
  };

  // Get market info for a port
  // Note: This intentionally does NOT set global isLoading to avoid re-render cascades
  const getMarketInfo = async (stationId: string) => {
    if (!user) return;

    try {
      const response = await api.get(`/api/v1/trading/market/${stationId}`);
      setMarketInfo(response.data);
    } catch (error) {
      console.error('Error getting market info:', error);
      // Don't set global error state - let the component handle it
    }
  };
  
  // Buy resource from a port
  const buyResource = async (stationId: string, resourceType: string, quantity: number) => {
    if (!user || !playerState) return;
    
    setIsLoading(true);
    setError(null);
    
    try {
      const response = await api.post('/api/v1/trading/buy', {
        station_id: stationId,
        resource_type: resourceType,
        quantity: quantity
      });
      
      // Update player state and market info after purchase
      await refreshPlayerState();
      await getMarketInfo(stationId);
      
      return response.data;
    } catch (error: any) {
      console.error('Error buying resource:', error);
      setError(error.response?.data?.message || 'Failed to buy resource');
      throw error;
    } finally {
      setIsLoading(false);
    }
  };
  
  // Sell resource to a port
  const sellResource = async (stationId: string, resourceType: string, quantity: number) => {
    if (!user || !playerState) return;
    
    setIsLoading(true);
    setError(null);
    
    try {
      const response = await api.post('/api/v1/trading/sell', {
        station_id: stationId,
        resource_type: resourceType,
        quantity: quantity
      });
      
      // Update player state and market info after sale
      await refreshPlayerState();
      await getMarketInfo(stationId);
      
      return response.data;
    } catch (error: any) {
      console.error('Error selling resource:', error);
      setError(error.response?.data?.message || 'Failed to sell resource');
      throw error;
    } finally {
      setIsLoading(false);
    }
  };
  
  // Claim an unclaimed planet (and automatically land on it)
  const claimPlanet = async (planetId: string) => {
    if (!user || !playerState) return;

    setIsLoading(true);
    setError(null);

    try {
      const response = await api.post(`/api/v1/planets/${planetId}/claim`);

      // Update player state after claiming (player is auto-landed).
      // Claiming spends credits and settles colonists from the ship's
      // cargo, and the planet's ownership changes — refresh all three.
      await refreshPlayerState();
      await loadShips();
      await exploreCurrentLocation();

      return response.data;
    } catch (error: any) {
      console.error('Error claiming planet:', error);
      // 400 (requirements not met) and 403 (protected population hub) are
      // in-fiction gameplay refusals that the claim UI surfaces inline;
      // only unexpected failures should raise the global system alert.
      const status = error.response?.status;
      if (status !== 400 && status !== 403) {
        setError(error.response?.data?.detail || error.response?.data?.message || 'Failed to claim planet');
      }
      throw error;
    } finally {
      setIsLoading(false);
    }
  };

  // Land on a planet (only works for owned planets)
  const landOnPlanet = async (planetId: string) => {
    if (!user || !playerState) return;

    setIsLoading(true);
    setError(null);

    try {
      const response = await api.post('/api/v1/planets/land', { planet_id: planetId });

      // Update player state after landing
      await refreshPlayerState();

      return response.data;
    } catch (error: any) {
      console.error('Error landing on planet:', error);
      setError(error.response?.data?.detail || error.response?.data?.message || 'Failed to land on planet');
      throw error;
    } finally {
      setIsLoading(false);
    }
  };

  // Leave a planet
  const leavePlanet = async () => {
    if (!user || !playerState) return;

    setIsLoading(true);
    setError(null);

    try {
      const response = await api.post('/api/v1/planets/leave');

      // Update player state after leaving
      await refreshPlayerState();

      return response.data;
    } catch (error: any) {
      console.error('Error leaving planet:', error);
      setError(error.response?.data?.detail || error.response?.data?.message || 'Failed to leave planet');
      throw error;
    } finally {
      setIsLoading(false);
    }
  };

  // Rename a planet you own
  const renamePlanet = async (planetId: string, newName: string) => {
    if (!user || !playerState) return;

    setIsLoading(true);
    setError(null);

    try {
      const response = await api.put(`/api/v1/planets/${planetId}/rename`, { name: newName });

      // Refresh location data to show updated name
      await exploreCurrentLocation();

      return response.data;
    } catch (error: any) {
      console.error('Error renaming planet:', error);
      setError(error.response?.data?.detail || error.response?.data?.message || 'Failed to rename planet');
      throw error;
    } finally {
      setIsLoading(false);
    }
  };

  // Get planet details
  const getPlanetDetails = async (planetId: string) => {
    if (!user) return;

    try {
      const response = await api.get(`/api/v1/planets/${planetId}`);
      return response.data;
    } catch (error: any) {
      console.error('Error getting planet details:', error);
      throw error;
    }
  };

  // Update planet production allocation
  const updatePlanetAllocation = async (
    planetId: string,
    allocations: { fuel: number; organics: number; equipment: number; ore?: number; terraform?: number }
  ) => {
    if (!user || !playerState) return;

    setIsLoading(true);
    setError(null);

    try {
      const response = await api.put(`/api/v1/planets/${planetId}/allocate`, allocations);
      await refreshPlayerState();
      return response.data;
    } catch (error: any) {
      console.error('Error updating planet allocation:', error);
      setError(error.response?.data?.detail || 'Failed to update allocation');
      throw error;
    } finally {
      setIsLoading(false);
    }
  };

  // Update planet defenses
  const updatePlanetDefenses = async (
    planetId: string,
    defenses: { turrets?: number; shields?: number; fighters?: number }
  ) => {
    if (!user || !playerState) return;

    setIsLoading(true);
    setError(null);

    try {
      const response = await api.put(`/api/v1/planets/${planetId}/defenses`, defenses);
      await refreshPlayerState();
      await exploreCurrentLocation();
      return response.data;
    } catch (error: any) {
      console.error('Error updating planet defenses:', error);
      setError(error.response?.data?.detail || 'Failed to update defenses');
      throw error;
    } finally {
      setIsLoading(false);
    }
  };

  // Upgrade planet building
  const upgradePlanetBuilding = async (planetId: string, buildingType: string, targetLevel: number) => {
    if (!user || !playerState) return;

    setIsLoading(true);
    setError(null);

    try {
      const response = await api.post(`/api/v1/planets/${planetId}/buildings/upgrade`, {
        buildingType,
        targetLevel
      });
      await refreshPlayerState();
      await exploreCurrentLocation();
      return response.data;
    } catch (error: any) {
      console.error('Error upgrading building:', error);
      setError(error.response?.data?.detail || 'Failed to upgrade building');
      throw error;
    } finally {
      setIsLoading(false);
    }
  };

  // Transfer colonists between ship and planet
  const transferColonists = async (planetId: string, action: 'embark' | 'disembark', quantity: number) => {
    if (!user || !playerState) return;

    setIsLoading(true);
    setError(null);

    try {
      const response = await api.post(`/api/v1/planets/${planetId}/colonists/transfer`, {
        action,
        quantity
      });
      await refreshPlayerState();
      await loadShips();
      await exploreCurrentLocation();
      return response.data;
    } catch (error: any) {
      console.error('Error transferring colonists:', error);
      // 400/403 are gameplay refusals (capacity, ownership, quantity) shown
      // inline by the transfer modal; reserve the global alert for the rest.
      const status = error.response?.status;
      if (status !== 400 && status !== 403) {
        setError(error.response?.data?.detail || 'Failed to transfer colonists');
      }
      throw error;
    } finally {
      setIsLoading(false);
    }
  };

  // Deposit resources to citadel safe
  const depositToSafe = async (planetId: string, resourceType: string, amount: number) => {
    if (!user || !playerState) return;

    setIsLoading(true);
    setError(null);

    try {
      const response = await api.post(`/api/v1/planets/${planetId}/safe/deposit`, {
        resource_type: resourceType,
        amount
      });
      await refreshPlayerState();
      await loadShips();
      await exploreCurrentLocation();
      return response.data;
    } catch (error: any) {
      console.error('Error depositing to safe:', error);
      setError(error.response?.data?.detail || 'Failed to deposit to safe');
      throw error;
    } finally {
      setIsLoading(false);
    }
  };

  // Withdraw resources from citadel safe
  const withdrawFromSafe = async (planetId: string, resourceType: string, amount: number) => {
    if (!user || !playerState) return;

    setIsLoading(true);
    setError(null);

    try {
      const response = await api.post(`/api/v1/planets/${planetId}/safe/withdraw`, {
        resource_type: resourceType,
        amount
      });
      await refreshPlayerState();
      await loadShips();
      await exploreCurrentLocation();
      return response.data;
    } catch (error: any) {
      console.error('Error withdrawing from safe:', error);
      setError(error.response?.data?.detail || 'Failed to withdraw from safe');
      throw error;
    } finally {
      setIsLoading(false);
    }
  };

  // Attack another player
  const attackPlayer = async (playerId: string) => {
    if (!user || !playerState) return;
    
    setIsLoading(true);
    setError(null);
    
    try {
      const response = await api.post('/api/v1/combat/attack-player', { defender_id: playerId });
      
      // Update player state after combat
      await refreshPlayerState();
      
      return response.data;
    } catch (error: any) {
      console.error('Error attacking player:', error);
      setError(error.response?.data?.message || 'Failed to attack player');
      throw error;
    } finally {
      setIsLoading(false);
    }
  };
  
  // Attack sector drones
  const attackDrones = async () => {
    if (!user || !playerState) return;
    
    setIsLoading(true);
    setError(null);
    
    try {
      const response = await api.post('/api/v1/combat/attack-drones', { 
        sector_id: playerState.current_sector_id 
      });
      
      // Update player state after combat
      await refreshPlayerState();
      
      return response.data;
    } catch (error: any) {
      console.error('Error attacking drones:', error);
      setError(error.response?.data?.message || 'Failed to attack drones');
      throw error;
    } finally {
      setIsLoading(false);
    }
  };
  
  // Handle first login completion - refresh all game data
  const onFirstLoginComplete = async () => {
    setNeedsFirstLogin(false);
    await Promise.all([
      refreshPlayerState(),
      loadShips()
    ]);
  };

  // Update just the player credits without a full state refresh
  // Used by gambling to update credits instantly without causing re-renders
  const updatePlayerCredits = (newCredits: number) => {
    setPlayerState(prev => prev ? { ...prev, credits: newCredits } : null);
  };

  const updateShipGenesis = (genesisDevices: number) => {
    setCurrentShip(prev => prev ? { ...prev, genesis_devices: genesisDevices } : null);
  };

  const value = {
    // Player info
    playerState,
    refreshPlayerState,
    updatePlayerCredits,
    updateShipGenesis,
    
    // First login status
    needsFirstLogin,
    checkFirstLoginStatus,
    onFirstLoginComplete,
    
    // Player ships
    ships,
    currentShip,
    loadShips,
    setCurrentShip: setActiveShip,
    
    // Current location info
    currentSector,
    availableMoves,
    planetsInSector,
    stationsInSector,
    
    // Movement
    moveToSector,
    getAvailableMoves,
    
    // Station interactions
    dockAtStation,
    undockFromStation,
    marketInfo,
    getMarketInfo,
    buyResource,
    sellResource,
    
    // Planet interactions
    claimPlanet,
    renamePlanet,
    landOnPlanet,
    leavePlanet,
    getPlanetDetails,
    updatePlanetAllocation,
    updatePlanetDefenses,
    upgradePlanetBuilding,
    transferColonists,
    depositToSafe,
    withdrawFromSafe,

    // Combat
    attackPlayer,
    attackDrones,
    
    // Loading states
    isLoading,
    error,
    
    // General methods
    exploreCurrentLocation
  };
  
  return <GameContext.Provider value={value}>{children}</GameContext.Provider>;
};

// Hook for using the game context
export const useGame = () => {
  const context = useContext(GameContext);
  if (context === undefined) {
    throw new Error('useGame must be used within a GameProvider');
  }
  return context;
};