import React, { createContext, useContext, useState, useEffect, useRef, ReactNode } from 'react';
import { useAuth } from './AuthContext';
import apiClient from '../services/apiClient';

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
  is_population_hub?: boolean;
}

// A pioneer migration contract brokered at a capital population hub.
export interface MigrationContract {
  id: string;
  source_planet_id: string;
  source_planet_name?: string | null;
  source_sector_id: number;
  cohort_total: number;
  loaded: number;
  delivered: number;
  remaining_to_load: number;
  fee_per_pioneer_locked: number;
  status: 'BROKERED' | 'IN_PROGRESS' | 'FULFILLED' | 'VOID';
}

export interface PioneerOffice {
  planet_id: string;
  planet_name: string;
  fee_per_pioneer: number;
  cargo_colonists: number;
  cargo_free: number;
  contracts: MigrationContract[];
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

export interface StationSlips {
  capacity: number;
  occupied: number;
  free: number;
  fee: number;
  bump_cost: number;
  queue_length: number;
  my_queue_position: number | null;
  occupants_bumpable_count: number;
}

// --- Quantum drive (Warp Jumper) ---
export interface QuantumStatus {
  quantum_shards: number;
  quantum_crystals: number;
  quantum_charges: number;
  jump_cooldown_until: string | null;
  scan_cooldown_until: string | null;
  can_jump: boolean;
  is_warp_jumper: boolean;
  sensor_level: number;
}

export interface QuantumBearing {
  yaw_deg: number;
  pitch_deg: number;
  range_band: 'near' | 'mid' | 'far' | 'extended';
}

export interface QuantumScanResult {
  resonance: 'bright' | 'steady' | 'faint' | 'silent';
  texture: 'hollow' | 'mineral' | 'chromatic' | 'heavy' | 'hot' | 'turbulent';
  echo: 'silent' | 'faint motion';
  expires_at: string;
  scan_cooldown_until: string | null;
  turns_remaining: number;
}

// A paid echo scan, tagged with the sector it was fired from. Lifted into
// context so flipping the NAV monitor mode (which unmounts the console)
// doesn't destroy telemetry the pilot spent turns/shards to obtain.
export interface QuantumScanTelemetry {
  origin_sector_id: number;
  result: QuantumScanResult;
}

export interface QuantumJumpResult {
  outcome: 'jump' | 'misfire';
  destination_sector_id: number;
  destination_name: string;
  distance_jumped: number;
  hull_damage_pct: number;
  jump_cooldown_until: string | null;
  turns_remaining: number;
}

// One inbox entry, exactly as Message.to_dict() serializes it on the
// gameserver (GET /api/v1/messages/inbox → {messages: [...], unread_count,
// total, page, limit, pages}).
export interface PlayerMessage {
  id: string;
  sender_id: string;
  recipient_id: string | null;
  team_id: string | null;
  subject: string | null;
  content: string;
  sent_at: string | null;
  read_at: string | null;
  message_type: string;
  priority: string;
  thread_id: string | null;
  reply_to_id: string | null;
  flagged: boolean;
  is_read: boolean;
  sender_name?: string;
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
  getStationSlips: (stationId: string) => Promise<StationSlips | null>;
  bumpDockOccupant: (stationId: string, occupantPlayerId: string) => Promise<any>;
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
  // Allocations are colonist HEADCOUNTS, not percentages — the backend
  // (PlanetResourceAllocation + PlanetaryService.allocate_colonists)
  // accepts exactly {fuel, organics, equipment} and validates that the
  // sum does not exceed planet.colonists.
  updatePlanetAllocation: (planetId: string, allocations: { fuel: number; organics: number; equipment: number }) => Promise<any>;
  updatePlanetDefenses: (planetId: string, defenses: { turrets?: number; shields?: number; fighters?: number }) => Promise<any>;
  upgradePlanetBuilding: (planetId: string, buildingType: string, targetLevel: number) => Promise<any>;
  transferColonists: (planetId: string, action: 'embark' | 'disembark', quantity: number) => Promise<any>;
  // Pioneer Office (population-hub migration contracts)
  getPioneerOffice: () => Promise<PioneerOffice>;
  brokerMigrationContract: (cohortTotal: number) => Promise<MigrationContract>;
  loadPioneerBatch: (contractId: string, quantity: number) => Promise<MigrationContract>;
  listMigrationContracts: (includeClosed?: boolean) => Promise<MigrationContract[]>;
  cancelMigrationContract: (contractId: string) => Promise<MigrationContract>;
  // Citadel (5-level) — info, upgrades, and CREDITS-ONLY safe storage.
  // CitadelService.deposit_to_safe/withdraw_from_safe move credits between
  // the player balance and planet.citadel_safe_credits; there is no
  // commodity storage in the citadel safe.
  getCitadelInfo: (planetId: string) => Promise<any>;
  upgradeCitadel: (planetId: string) => Promise<any>;
  cancelCitadelUpgrade: (planetId: string) => Promise<any>;
  getDefenseBuildings: (planetId: string) => Promise<any>;
  buildDefenseBuilding: (planetId: string, buildingType: string) => Promise<any>;
  depositToSafe: (planetId: string, amount: number) => Promise<any>;
  withdrawFromSafe: (planetId: string, amount: number) => Promise<any>;
  depositCommodityToSafe: (planetId: string, commodity: string, amount: number) => Promise<any>;
  withdrawCommodityFromSafe: (planetId: string, commodity: string, amount: number) => Promise<any>;
  // Planetary defenses — shield generator status/upgrade
  getPlanetDefenseInfo: (planetId: string) => Promise<any>;
  upgradeShields: (planetId: string) => Promise<any>;

  // Port Office — station ownership, sealed-bid sales, tariffs, takeovers
  // (backend: /api/v1/port-ownership/*). Payload shapes are normalized
  // defensively in the Port Office venue, so these return `unknown`.
  getPortListings: () => Promise<unknown>;
  getListing: (stationId: string) => Promise<unknown>;
  listStation: (stationId: string) => Promise<unknown>;
  placeOffer: (stationId: string, bidAmount: number) => Promise<unknown>;
  getMyStations: () => Promise<unknown>;
  setStationTax: (stationId: string, taxRate: number) => Promise<unknown>;
  withdrawTreasury: (stationId: string, amount: number) => Promise<unknown>;
  getTakeoverStatus: (stationId: string) => Promise<unknown>;
  launchTakeover: (stationId: string) => Promise<unknown>;
  counterTakeover: (stationId: string, action: 'accept' | 'match' | 'dispute') => Promise<unknown>;

  // Player-to-player hails (COMMS mailbox) — bound to /api/v1/messages/*.
  // Follows the Port Office mold: no global isLoading/error churn, the
  // COMMS monitor surfaces failures inline.
  inboxMessages: PlayerMessage[];
  unreadMessageCount: number;
  refreshInbox: () => Promise<void>;
  sendPlayerMessage: (
    recipientId: string,
    content: string,
    subject?: string | null,
    replyToId?: string | null
  ) => Promise<{ message_id: string; sent_at: string }>;
  markMessageRead: (messageId: string) => Promise<void>;

  // Quantum drive (Warp Jumper) — status is auto-refreshed alongside player
  // state whenever the active ship is a WARP_JUMPER, null otherwise
  quantumStatus: QuantumStatus | null;
  refreshQuantumStatus: () => Promise<void>;
  quantumScan: (payload: QuantumBearing) => Promise<QuantumScanResult>;
  quantumJump: (payload: QuantumBearing) => Promise<QuantumJumpResult>;
  refineQuantumCharge: () => Promise<{ quantum_charges: number; quantum_shards: number }>;
  // Last paid echo scan, preserved across NAV mode flips and cleared on
  // sector change (telemetry from a prior sector is meaningless here).
  quantumScanResult: QuantumScanTelemetry | null;
  setQuantumScanResult: (telemetry: QuantumScanTelemetry | null) => void;


  // Loading states
  // isLoading is TRUE ONLY during initial hydration — the first
  // refreshPlayerState while playerState is still null. Background
  // refreshes and mutations never touch it, so consumers can gate
  // first-load placeholders on it without flicker/remount churn.
  isLoading: boolean;
  // isRefreshing flips during background refreshPlayerState runs (after
  // hydration) for any consumer that wants a lightweight activity signal.
  isRefreshing: boolean;
  error: string | null;
  
  // General methods
  exploreCurrentLocation: () => Promise<void>;
}

const GameContext = createContext<GameContextType | undefined>(undefined);

export const GameProvider: React.FC<{ children: ReactNode }> = ({ children }) => {
  const { user, isAuthenticated } = useAuth();
  const [isLoading, setIsLoading] = useState(false);
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Latches true once the first player-state hydration succeeds. Refs (not
  // derived from the playerState closure) so a stale function reference held
  // by a consumer can never misclassify a background refresh as initial load.
  const hasHydrated = useRef(false);
  
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

  // Player-to-player hails (COMMS mailbox)
  const [inboxMessages, setInboxMessages] = useState<PlayerMessage[]>([]);
  const [unreadMessageCount, setUnreadMessageCount] = useState(0);

  // Quantum drive (Warp Jumper only)
  const [quantumStatus, setQuantumStatus] = useState<QuantumStatus | null>(null);
  // Paid echo scan telemetry, lifted out of the console so NAV mode flips
  // don't destroy it. Cleared whenever the player's sector changes.
  const [quantumScanResult, setQuantumScanResult] = useState<QuantumScanTelemetry | null>(null);

  // Shared axios instance: attaches the access token from localStorage and
  // transparently refreshes it on 401 (see services/apiClient.ts). Its
  // baseURL resolves to VITE_API_URL or window.location.origin, matching the
  // Vite-proxy semantics this context previously set up itself.
  const api = apiClient;


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
      hasHydrated.current = false;
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

  // Keep the sector snapshot live. NPC ships (patrolling marshals, raiders,
  // merchant captains) and other pilots move through the sector continuously,
  // but the snapshot — including players_present, which carries NPC entries
  // the COMMS contacts list renders — was only fetched on arrival, so the
  // crowd appeared frozen no matter how much the galaxy moved underneath it.
  // Poll current-sector on an interval so contacts visibly arrive and depart.
  // The gameserver documents polled players_present as the authoritative
  // visibility path (its websocket sector routing is best-effort), so this is
  // the intended sync mechanism, not a workaround. Skipped while the tab is
  // backgrounded; cleared on sector change / unmount.
  useEffect(() => {
    if (!playerState?.current_sector_id) return;
    const SECTOR_PRESENCE_POLL_MS = 5000;
    const id = window.setInterval(async () => {
      if (typeof document !== 'undefined' && document.hidden) return;
      try {
        const res = await api.get('/api/v1/player/current-sector');
        setCurrentSector(res.data);
      } catch {
        // Transient — the next tick retries.
      }
    }, SECTOR_PRESENCE_POLL_MS);
    return () => window.clearInterval(id);
  }, [playerState?.current_sector_id]);

  // A paid echo scan is only meaningful from the sector it was fired in —
  // discard it the moment the player relocates.
  useEffect(() => {
    setQuantumScanResult(null);
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
    // Global isLoading is reserved for the true initial hydration (we have
    // never successfully loaded player state). Every later run is a
    // background refresh and only flips the lightweight isRefreshing flag —
    // toggling the global flag on every scan/jump/move/dock is what caused
    // the app-wide spinner/remount plague.
    const isInitialHydration = !hasHydrated.current;
    if (isInitialHydration) {
      setIsLoading(true);
    } else {
      setIsRefreshing(true);
    }
    setError(null);

    try {
      const response = await api.get('/api/v1/player/state');
      setPlayerState(response.data as PlayerState);
      hasHydrated.current = true;

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
      if (isInitialHydration) {
        setIsLoading(false);
      } else {
        setIsRefreshing(false);
      }
      refreshInProgress.current = false;
    }
  };

  // Load player's ships
  const loadShips = async () => {
    if (!user) return;

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
        setShips([]);
      }
      // On transient (non-auth) errors keep the previously loaded ships
      // rather than blanking the fleet list
    }
  };
  
  // Set current ship
  const setActiveShip = async (shipId: string) => {
    if (!user) return;
    
    setError(null);
    
    try {
      await api.post(`/api/v1/ships/${shipId}/set-active`);
      
      // Update player state and ships
      await refreshPlayerState();
      await loadShips();
    } catch (error) {
      console.error('Error setting active ship:', error);
      setError('Failed to set active ship');
    }
  };
  
  // Move to another sector
  const moveToSector = async (sectorId: number) => {
    if (!user || !playerState) return;
    
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
    }
  };
  
  // Get available moves from current sector
  const getAvailableMoves = async () => {
    if (!user || !playerState) return;
    
    setError(null);
    
    try {
      const response = await api.get('/api/v1/player/available-moves');
      setAvailableMoves(response.data);
    } catch (error) {
      console.error('Error getting available moves:', error);
      setError('Failed to get available moves');
    }
  };

  // Explore current location (sector, planets, stations)
  const exploreCurrentLocation = async () => {
    if (!user || !playerState) return;

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
    }
  };
  
  // Dock at a port
  const dockAtStation = async (stationId: string) => {
    if (!user || !playerState) return;
    
    setError(null);
    
    try {
      const response = await api.post('/api/v1/trading/dock', { station_id: stationId });
      
      // Update player state after docking
      await refreshPlayerState();
      
      return response.data;
    } catch (error: any) {
      // 409 = every transient slip is taken; the server auto-enqueued us and
      // returned slip/queue/bump details. Surface that payload to callers
      // instead of throwing so the UI can offer the queue/bump flow inline.
      if (error.response?.status === 409 && error.response?.data) {
        return { full: true, ...error.response.data };
      }
      console.error('Error docking at port:', error);
      setError(error.response?.data?.message || 'Failed to dock at port');
      throw error;
    }
  };

  // Get transient slip availability for a station
  // Note: lightweight read used by dock lists and gauges — intentionally does
  // NOT set global isLoading/error to avoid re-render cascades
  const getStationSlips = async (stationId: string): Promise<StationSlips | null> => {
    if (!user) return null;

    try {
      const response = await api.get(`/api/v1/trading/stations/${stationId}/slips`);
      return response.data as StationSlips;
    } catch (error) {
      console.warn('GameContext: Failed to load station slips:', error);
      return null;
    }
  };

  // Pay the bump cost to evict a long-tenured occupant and take their slip.
  // Errors are surfaced inline by the dock-full panel, so no global setError.
  const bumpDockOccupant = async (stationId: string, occupantPlayerId: string) => {
    if (!user || !playerState) {
      // Callers treat a return as success — never fall through silently
      throw new Error('Not ready to dock — please try again');
    }

    let response;
    try {
      response = await api.post(`/api/v1/trading/stations/${stationId}/slips/bump`, {
        occupant_player_id: occupantPlayerId
      });
    } catch (error: any) {
      console.error('Error bumping slip occupant:', error);
      throw error;
    }

    // The bump succeeded server-side; a failed refresh must not read as a
    // failed bump (the player IS docked and WAS charged)
    try {
      await refreshPlayerState();
    } catch (refreshError) {
      console.warn('Post-bump state refresh failed:', refreshError);
    }

    return response.data;
  };

  // Undock from current station
  const undockFromStation = async () => {
    if (!user || !playerState) return;

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
    }
  };
  
  // Sell resource to a port
  const sellResource = async (stationId: string, resourceType: string, quantity: number) => {
    if (!user || !playerState) return;
    
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
    }
  };
  
  // Claim an unclaimed planet (and automatically land on it)
  const claimPlanet = async (planetId: string) => {
    if (!user || !playerState) return;

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
    }
  };

  // Land on a planet (only works for owned planets)
  const landOnPlanet = async (planetId: string) => {
    if (!user || !playerState) return;

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
    }
  };

  // Leave a planet
  const leavePlanet = async () => {
    if (!user || !playerState) return;

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
    }
  };

  // Rename a planet you own
  const renamePlanet = async (planetId: string, newName: string) => {
    if (!user || !playerState) return;

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

  // Update planet production allocation (colonist headcounts).
  // PUT /allocate returns {success, allocations: {fuel, organics, equipment,
  // unused}, productionRates: {fuel, organics, equipment, colonists}}.
  // No global isLoading/error churn: the allocation sliders persist on a
  // debounce and surface failures inline with an optimistic revert, and the
  // endpoint touches no player-level state (no credits/turns), so there is
  // nothing to refresh globally.
  const updatePlanetAllocation = async (
    planetId: string,
    allocations: { fuel: number; organics: number; equipment: number }
  ) => {
    if (!user) throw new Error('Not authenticated');

    try {
      const response = await api.put(`/api/v1/planets/${planetId}/allocate`, allocations);
      return response.data;
    } catch (error: any) {
      console.error('Error updating planet allocation:', error);
      throw error;
    }
  };

  // Update planet defenses
  const updatePlanetDefenses = async (
    planetId: string,
    defenses: { turrets?: number; shields?: number; fighters?: number }
  ) => {
    if (!user || !playerState) return;

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
    }
  };

  // Upgrade planet building
  const upgradePlanetBuilding = async (planetId: string, buildingType: string, targetLevel: number) => {
    if (!user || !playerState) return;

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
    }
  };

  // Transfer colonists between ship and planet
  const transferColonists = async (planetId: string, action: 'embark' | 'disembark', quantity: number) => {
    if (!user || !playerState) return;

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
    }
  };

  // --- Pioneer Office: migration contracts at a population hub ---
  // Follow the Port Office mold: no global isLoading/error churn — the venue
  // surfaces 400/403 refusals inline. Mutations that move credits or cargo
  // refresh player + ship state so the cockpit stays authoritative.
  const getPioneerOffice = async (): Promise<PioneerOffice> => {
    const response = await api.get('/api/v1/pioneer/office');
    return response.data;
  };

  const brokerMigrationContract = async (cohortTotal: number): Promise<MigrationContract> => {
    const response = await api.post('/api/v1/pioneer/contracts', { cohort_total: cohortTotal });
    return response.data;
  };

  const loadPioneerBatch = async (contractId: string, quantity: number): Promise<MigrationContract> => {
    const response = await api.post(`/api/v1/pioneer/contracts/${contractId}/load`, { quantity });
    await refreshPlayerState();
    await loadShips();
    return response.data;
  };

  const listMigrationContracts = async (includeClosed = false): Promise<MigrationContract[]> => {
    const response = await api.get('/api/v1/pioneer/contracts', {
      params: { include_closed: includeClosed },
    });
    return response.data;
  };

  const cancelMigrationContract = async (contractId: string): Promise<MigrationContract> => {
    const response = await api.post(`/api/v1/pioneer/contracts/${contractId}/cancel`);
    return response.data;
  };

  // --- Citadel: info, upgrades, and the credits-only safe ---
  // These follow the Port Office mold: no global isLoading/error churn — the
  // planetary ops console surfaces failures inline. Mutations that move
  // credits refresh player state so the header credits stay authoritative.

  // Citadel info — GET /planets/{id}/citadel (owner-only; 400 otherwise).
  // Returns {citadel_level, citadel_name, max_population, safe_storage,
  // safe_credits, drone_capacity, is_upgrading, upgrade_remaining_seconds?,
  // next_level: {level, name, upgrade_cost, upgrade_hours, resource_cost, ...} | null}
  const getCitadelInfo = async (planetId: string) => {
    if (!user) throw new Error('Not authenticated');

    try {
      const response = await api.get(`/api/v1/planets/${planetId}/citadel`);
      return response.data;
    } catch (error: any) {
      console.error('Error getting citadel info:', error);
      throw error;
    }
  };

  // Start a citadel upgrade — POST /planets/{id}/citadel/upgrade.
  // Level 0→1 (Outpost) is free and instant; higher levels deduct credits
  // and planet resources and run on a timer (CitadelService.start_upgrade).
  const upgradeCitadel = async (planetId: string) => {
    if (!user || !playerState) throw new Error('Not authenticated');

    try {
      const response = await api.post(`/api/v1/planets/${planetId}/citadel/upgrade`);
      // Upgrades from level 1+ deduct player credits
      await refreshPlayerState();
      return response.data;
    } catch (error: any) {
      console.error('Error upgrading citadel:', error);
      throw error;
    }
  };

  // Cancel an in-progress citadel upgrade — POST /planets/{id}/citadel/cancel.
  // Refunds 50% of the credits paid (CitadelService.cancel_upgrade).
  const cancelCitadelUpgrade = async (planetId: string) => {
    if (!user || !playerState) throw new Error('Not authenticated');
    try {
      const response = await api.post(`/api/v1/planets/${planetId}/citadel/cancel`);
      await refreshPlayerState();
      return response.data;
    } catch (error: any) {
      console.error('Error cancelling citadel upgrade:', error);
      throw error;
    }
  };

  // Defense buildings a planet's citadel level unlocks — GET
  // /planets/{id}/buildings/available (CitadelService.get_available_buildings).
  const getDefenseBuildings = async (planetId: string) => {
    try {
      const response = await api.get(`/api/v1/planets/${planetId}/buildings/available`);
      return response.data;
    } catch (error: any) {
      console.error('Error getting defense buildings:', error);
      return null;
    }
  };

  // Construct a defense building — POST /planets/{id}/buildings/construct.
  const buildDefenseBuilding = async (planetId: string, buildingType: string) => {
    if (!user || !playerState) throw new Error('Not authenticated');
    try {
      const response = await api.post(`/api/v1/planets/${planetId}/buildings/construct`, { buildingType });
      await refreshPlayerState();
      return response.data;
    } catch (error: any) {
      console.error('Error constructing defense building:', error);
      throw error;
    }
  };

  // Deposit credits into the citadel safe — POST /planets/{id}/citadel/deposit
  // {amount}. Server gating (CitadelService.deposit_to_safe): planet must be
  // owned, citadel_level >= 1, player must hold the credits, and the safe
  // balance may not exceed CITADEL_LEVELS[level].safe_storage. Returns
  // {credits_deposited, safe_balance, safe_capacity, player_credits, message}.
  const depositToSafe = async (planetId: string, amount: number) => {
    if (!user || !playerState) throw new Error('Not authenticated');

    try {
      const response = await api.post(`/api/v1/planets/${planetId}/citadel/deposit`, { amount });
      // Deposit debits the player's credit balance
      await refreshPlayerState();
      return response.data;
    } catch (error: any) {
      console.error('Error depositing to citadel safe:', error);
      throw error;
    }
  };

  // Withdraw credits from the citadel safe — POST /planets/{id}/citadel/withdraw
  // {amount}. Returns {credits_withdrawn, safe_balance, player_credits, message}.
  const withdrawFromSafe = async (planetId: string, amount: number) => {
    if (!user || !playerState) throw new Error('Not authenticated');

    try {
      const response = await api.post(`/api/v1/planets/${planetId}/citadel/withdraw`, { amount });
      // Withdrawal credits the player's balance
      await refreshPlayerState();
      return response.data;
    } catch (error: any) {
      console.error('Error withdrawing from citadel safe:', error);
      throw error;
    }
  };

  // Move a commodity planet-stockpile -> protected citadel safe.
  // POST /planets/{id}/citadel/deposit-commodity {commodity, amount}.
  const depositCommodityToSafe = async (planetId: string, commodity: string, amount: number) => {
    if (!user || !playerState) throw new Error('Not authenticated');
    try {
      const response = await api.post(`/api/v1/planets/${planetId}/citadel/deposit-commodity`, { commodity, amount });
      return response.data;
    } catch (error: any) {
      console.error('Error depositing commodity to citadel safe:', error);
      throw error;
    }
  };

  // Move a commodity safe -> planet stockpile.
  // POST /planets/{id}/citadel/withdraw-commodity {commodity, amount}.
  const withdrawCommodityFromSafe = async (planetId: string, commodity: string, amount: number) => {
    if (!user || !playerState) throw new Error('Not authenticated');
    try {
      const response = await api.post(`/api/v1/planets/${planetId}/citadel/withdraw-commodity`, { commodity, amount });
      return response.data;
    } catch (error: any) {
      console.error('Error withdrawing commodity from citadel safe:', error);
      throw error;
    }
  };

  // Defense telemetry — GET /planets/{id}/defenses (no ownership required;
  // useful for scouting). Returns {shieldGenerator: {level, maxLevel, name,
  // strength, currentShields, regenPerHour, nextUpgrade: {level, name,
  // strength, regenPerHour, cost} | null}, defenseLevel, damageReduction,
  // turrets, fighters}.
  const getPlanetDefenseInfo = async (planetId: string) => {
    if (!user) throw new Error('Not authenticated');

    try {
      const response = await api.get(`/api/v1/planets/${planetId}/defenses`);
      return response.data;
    } catch (error: any) {
      console.error('Error getting planet defense info:', error);
      throw error;
    }
  };

  // Upgrade the planet's shield generator by one level — POST
  // /planets/{id}/shields/upgrade. Returns {shieldGenerator: {level, name,
  // strength, regenPerHour, maxLevel}, creditsCost, creditsRemaining,
  // nextUpgradeCost}; errors arrive as 400 detail strings.
  const upgradeShields = async (planetId: string) => {
    if (!user || !playerState) throw new Error('Not authenticated');

    try {
      const response = await api.post(`/api/v1/planets/${planetId}/shields/upgrade`);
      // Upgrade deducts player credits
      await refreshPlayerState();
      return response.data;
    } catch (error: any) {
      console.error('Error upgrading shields:', error);
      throw error;
    }
  };

  // --- Port Office: station ownership, sealed-bid sales, tariffs, takeovers ---
  // These follow the getPlanetDetails mold: no global isLoading/error churn —
  // the Port Office venue surfaces failures inline. Mutations that move
  // credits (escrowed offers, treasury withdrawals, forced sales) refresh
  // player state so the header credits stay authoritative.

  // Registry board — every station currently listed for sale in scope
  const getPortListings = async (): Promise<unknown> => {
    if (!user) throw new Error('Not authenticated');

    try {
      const response = await api.get('/api/v1/port-ownership/listings');
      return response.data;
    } catch (error: any) {
      console.error('Error getting port listings:', error);
      throw error;
    }
  };

  // Ownership/listing status for one station. Reading this also lets the
  // server lazily resolve expired grace windows (sealed-bid auctions resolve
  // on first read past expiry — no scheduler exists).
  const getListing = async (stationId: string): Promise<unknown> => {
    if (!user) throw new Error('Not authenticated');

    try {
      const response = await api.get(`/api/v1/port-ownership/stations/${stationId}/listing`);
      return response.data;
    } catch (error: any) {
      console.error('Error getting station listing:', error);
      throw error;
    }
  };

  // Put a station on the sale board (price is formula-set server-side)
  const listStation = async (stationId: string): Promise<unknown> => {
    if (!user || !playerState) throw new Error('Not authenticated');

    try {
      const response = await api.post(`/api/v1/port-ownership/stations/${stationId}/list`);
      return response.data;
    } catch (error: any) {
      console.error('Error listing station for sale:', error);
      throw error;
    }
  };

  // Sealed-bid offer — funds are escrowed (debited) immediately
  const placeOffer = async (stationId: string, bidAmount: number): Promise<unknown> => {
    if (!user || !playerState) throw new Error('Not authenticated');

    try {
      const response = await api.post(`/api/v1/port-ownership/stations/${stationId}/offer`, {
        bid: bidAmount
      });
      // Escrow debits credits at offer time
      await refreshPlayerState();
      return response.data;
    } catch (error: any) {
      console.error('Error placing station offer:', error);
      throw error;
    }
  };

  // Stations I own (with treasury / tax / revenue detail)
  const getMyStations = async (): Promise<unknown> => {
    if (!user) throw new Error('Not authenticated');

    try {
      const response = await api.get('/api/v1/port-ownership/my-stations');
      return response.data;
    } catch (error: any) {
      console.error('Error getting my stations:', error);
      throw error;
    }
  };

  // Owner lever: set the trade tariff within [0.0, 0.25]
  const setStationTax = async (stationId: string, taxRate: number): Promise<unknown> => {
    if (!user || !playerState) throw new Error('Not authenticated');

    try {
      const response = await api.post(`/api/v1/port-ownership/stations/${stationId}/tax`, {
        rate: taxRate
      });
      return response.data;
    } catch (error: any) {
      console.error('Error setting station tax:', error);
      throw error;
    }
  };

  // Owner lever: withdraw from the station treasury (solo owner only)
  const withdrawTreasury = async (stationId: string, amount: number): Promise<unknown> => {
    if (!user || !playerState) throw new Error('Not authenticated');

    try {
      const response = await api.post(`/api/v1/port-ownership/stations/${stationId}/withdraw`, {
        amount
      });
      // Withdrawal credits the player
      await refreshPlayerState();
      return response.data;
    } catch (error: any) {
      console.error('Error withdrawing station treasury:', error);
      throw error;
    }
  };

  // Economic-takeover campaign status. Reading this also lets the server
  // lazily evaluate monthly volume shares and counter-window expiry.
  const getTakeoverStatus = async (stationId: string): Promise<unknown> => {
    if (!user) throw new Error('Not authenticated');

    try {
      const response = await api.get(`/api/v1/port-ownership/stations/${stationId}/takeover`);
      return response.data;
    } catch (error: any) {
      console.error('Error getting takeover status:', error);
      throw error;
    }
  };

  // Challenger: open an economic-takeover campaign against this station
  const launchTakeover = async (stationId: string): Promise<unknown> => {
    if (!user || !playerState) throw new Error('Not authenticated');

    try {
      const response = await api.post(`/api/v1/port-ownership/stations/${stationId}/takeover/launch`);
      return response.data;
    } catch (error: any) {
      console.error('Error launching takeover campaign:', error);
      throw error;
    }
  };

  // Owner counter during the 7-canonical-day window: accept (forced sale),
  // match (volume contest resets the clock), or dispute (auto-arbitration)
  const counterTakeover = async (
    stationId: string,
    action: 'accept' | 'match' | 'dispute'
  ): Promise<unknown> => {
    if (!user || !playerState) throw new Error('Not authenticated');

    try {
      const response = await api.post(`/api/v1/port-ownership/stations/${stationId}/takeover/counter`, {
        action
      });
      // 'accept' transfers ownership + sale proceeds atomically
      await refreshPlayerState();
      return response.data;
    } catch (error: any) {
      console.error('Error countering takeover:', error);
      throw error;
    }
  };

  // --- Player-to-player hails: the COMMS mailbox ---
  // These follow the getStationSlips/Port Office mold: no global
  // isLoading/error churn — the COMMS monitor surfaces failures inline.

  // Pull the inbox (first page covers the cockpit mailbox; the backend
  // serves 50 per page). Sets both the message list and the unread badge
  // count from the same authoritative response.
  const refreshInbox = async () => {
    if (!user) return;

    try {
      const response = await api.get('/api/v1/messages/inbox');
      const data = response.data as { messages: PlayerMessage[]; unread_count: number };
      setInboxMessages(data.messages || []);
      setUnreadMessageCount(data.unread_count || 0);
      // Server count is authoritative again — drop the local decrement guard
      locallyReadIds.current.clear();
    } catch (error) {
      console.warn('GameContext: Failed to load message inbox:', error);
      // Keep the previously loaded inbox on transient failures
    }
  };

  // Send a hail to another player — POST /api/v1/messages/send
  // {recipient_id, subject?, content, reply_to_id?} (snake_case per
  // MessageCreateRequest). Returns {message_id, sent_at}.
  const sendPlayerMessage = async (
    recipientId: string,
    content: string,
    subject?: string | null,
    replyToId?: string | null
  ): Promise<{ message_id: string; sent_at: string }> => {
    if (!user || !playerState) throw new Error('Not authenticated');

    try {
      const response = await api.post('/api/v1/messages/send', {
        recipient_id: recipientId,
        subject: subject || null,
        content,
        reply_to_id: replyToId || null
      });
      return response.data as { message_id: string; sent_at: string };
    } catch (error: any) {
      console.error('Error sending player message:', error);
      throw error;
    }
  };

  // Mark one hail read — PUT /api/v1/messages/{id}/read — then update the
  // local list and badge in place (no refetch needed for a single flag).
  // The ref guard makes the badge decrement idempotent per message id even
  // under stale closures / rapid double-expands; refreshInbox resets it
  // because a fresh server count re-baselines everything.
  const locallyReadIds = useRef<Set<string>>(new Set());
  const markMessageRead = async (messageId: string): Promise<void> => {
    if (!user) throw new Error('Not authenticated');

    const wasUnread =
      !locallyReadIds.current.has(messageId) &&
      inboxMessages.some(msg => msg.id === messageId && !msg.is_read);

    try {
      await api.put(`/api/v1/messages/${messageId}/read`);
      setInboxMessages(prev => prev.map(msg =>
        msg.id === messageId && !msg.is_read
          ? { ...msg, is_read: true, read_at: new Date().toISOString() }
          : msg
      ));
      if (wasUnread) {
        locallyReadIds.current.add(messageId);
        setUnreadMessageCount(prev => Math.max(0, prev - 1));
      }
    } catch (error: any) {
      console.error('Error marking message read:', error);
      throw error;
    }
  };

  // --- Quantum drive (Warp Jumper): scan / jump / charge refinement ---
  // These follow the Port Office mold: no global isLoading/error churn — the
  // Quantum Drive console surfaces failures inline. Status is a lightweight
  // read; actions that spend turns/shards/charges refresh the affected state.

  const refreshQuantumStatus = async () => {
    if (!user) return;

    try {
      const response = await api.get('/api/v1/quantum/status');
      setQuantumStatus(response.data as QuantumStatus);
    } catch (error) {
      console.warn('GameContext: Failed to load quantum status:', error);
      setQuantumStatus(null);
    }
  };

  // Keep quantum status in lockstep with player state while piloting a
  // Warp Jumper; clear it the moment the active ship is anything else.
  useEffect(() => {
    if (currentShip?.type === 'WARP_JUMPER') {
      refreshQuantumStatus();
    } else {
      setQuantumStatus(null);
    }
  }, [currentShip?.id, currentShip?.type, playerState?.turns, playerState?.current_sector_id]);

  // Hyperspace echo scan along a bearing (spends turns; far band spends a shard)
  const quantumScan = async (payload: QuantumBearing): Promise<QuantumScanResult> => {
    if (!user || !playerState) throw new Error('Not authenticated');

    try {
      const response = await api.post('/api/v1/quantum/scan', payload);
      // Scan spends turns (and a shard on the far band) — keep the header
      // turns counter and the console's cooldowns authoritative.
      await Promise.allSettled([refreshPlayerState(), refreshQuantumStatus()]);
      return response.data as QuantumScanResult;
    } catch (error: any) {
      console.error('Error running quantum scan:', error);
      throw error;
    }
  };

  // Commit the jump along a bearing (1 quantum charge + turns; may misfire)
  const quantumJump = async (payload: QuantumBearing): Promise<QuantumJumpResult> => {
    if (!user || !playerState) throw new Error('Not authenticated');

    let response;
    try {
      response = await api.post('/api/v1/quantum/jump', payload);
    } catch (error: any) {
      console.error('Error committing quantum jump:', error);
      throw error;
    }

    // The jump succeeded server-side (even a misfire MOVED the ship) — a
    // failed refresh must not read as a failed jump.
    try {
      await refreshPlayerState();
      await loadShips();
      await refreshQuantumStatus();
    } catch (refreshError) {
      console.warn('Post-jump state refresh failed:', refreshError);
    }

    return response.data as QuantumJumpResult;
  };

  // Refine 1 quantum shard into 1 charge on the current Warp Jumper
  // (server enforces docked-at-Class-3+/SpaceDock and charge capacity)
  const refineQuantumCharge = async (): Promise<{ quantum_charges: number; quantum_shards: number }> => {
    if (!user || !playerState) throw new Error('Not authenticated');

    try {
      const response = await api.post('/api/v1/quantum/refine-charge', {});
      await refreshQuantumStatus();
      return response.data as { quantum_charges: number; quantum_shards: number };
    } catch (error: any) {
      console.error('Error refining quantum charge:', error);
      throw error;
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
    getStationSlips,
    bumpDockOccupant,
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
    getPioneerOffice,
    brokerMigrationContract,
    loadPioneerBatch,
    listMigrationContracts,
    cancelMigrationContract,
    getCitadelInfo,
    upgradeCitadel,
    cancelCitadelUpgrade,
    getDefenseBuildings,
    buildDefenseBuilding,
    depositToSafe,
    withdrawFromSafe,
    depositCommodityToSafe,
    withdrawCommodityFromSafe,
    getPlanetDefenseInfo,
    upgradeShields,

    // Port Office — station ownership
    getPortListings,
    getListing,
    listStation,
    placeOffer,
    getMyStations,
    setStationTax,
    withdrawTreasury,
    getTakeoverStatus,
    launchTakeover,
    counterTakeover,

    // Player-to-player hails (COMMS mailbox)
    inboxMessages,
    unreadMessageCount,
    refreshInbox,
    sendPlayerMessage,
    markMessageRead,

    // Quantum drive (Warp Jumper)
    quantumStatus,
    refreshQuantumStatus,
    quantumScan,
    quantumJump,
    refineQuantumCharge,
    quantumScanResult,
    setQuantumScanResult,

    // Loading states
    isLoading,
    isRefreshing,
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