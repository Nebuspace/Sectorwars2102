// @vitest-environment jsdom
/**
 * GameDashboard — LEG-2731 async harvest poll + in-progress banner.
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { PENDING_HARVEST_STORAGE_KEY } from '../../mining/harvestPoll';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const mockHarvest = vi.fn();
const mockGetHarvestStatus = vi.fn();
const mockSectorWrecks = vi.fn();

vi.mock('../../../services/api', () => ({
  navAPI: {
    getChart: vi.fn().mockResolvedValue({ sectors: [], edges: [], frontier: [] }),
    getThreat: vi.fn().mockResolvedValue([]),
  },
  sectorAPI: {
    sectorWrecks: (...a: unknown[]) => mockSectorWrecks(...a),
    getContents: vi.fn().mockResolvedValue({ star: null, bodies: [] }),
  },
  planetaryAPI: {
    getOwnershipTransfer: vi.fn().mockResolvedValue({ planet_id: '', pending: false, offer: null }),
  },
  miningAPI: {
    harvest: (...a: unknown[]) => mockHarvest(...a),
    getHarvestStatus: (...a: unknown[]) => mockGetHarvestStatus(...a),
    getYieldPreview: vi.fn().mockResolvedValue({}),
    listLicenses: vi.fn().mockResolvedValue([]),
    getNearestAmRefinery: vi.fn().mockResolvedValue(null),
  },
}));

vi.mock('react-router-dom', () => ({ useNavigate: () => vi.fn() }));
vi.mock('../../layouts/GameLayout', () => ({
  default: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
}));
vi.mock('../../trading/TradingInterface', () => ({ default: () => <div /> }));
vi.mock('../../spacedock/SpaceDockInterface', () => ({ default: () => <div /> }));
vi.mock('../../spacedock/PortOfficeVenue', () => ({ default: () => <div /> }));
vi.mock('../../spacedock/ContractBoardVenue', () => ({ default: () => <div /> }));
vi.mock('../../planetary/PopulationCenterInterface', () => ({ default: () => <div /> }));
vi.mock('../../tactical/SolarSystemViewscreen', () => ({ default: () => <div /> }));
vi.mock('../../tactical/WindshieldTableau', () => ({ default: () => <div /> }));
vi.mock('../../mining/NearestAmRefineryOverlay', () => ({ default: () => null }));
vi.mock('../../mining/HarvestYieldPreview', () => ({
  default: ({ onGateChange }: { onGateChange?: (state: { blocked: boolean; message: string | null }) => void }) => {
    React.useEffect(() => {
      onGateChange?.({ blocked: false, message: null });
    }, [onGateChange]);
    return null;
  },
  HARVEST_GATE_COPY: {},
}));
vi.mock('../../tactical/PlanetPortPair', () => ({ default: () => <div data-testid="ppp-stub" /> }));
vi.mock('../../quantum/QuantumDriveConsole', () => ({ default: () => <div /> }));
vi.mock('../../gatewright/GatewrightPanel', () => ({ default: () => <div /> }));
vi.mock('../../cockpit/CockpitColonyManagement', () => ({ default: () => <div /> }));
vi.mock('../../cockpit/SafeVaultPanel', () => ({ default: () => <div /> }));
vi.mock('../../../hooks/useResourceCatalog', () => ({
  useResourceCatalog: () => ({
    catalog: [], loading: false, getLabel: (n: string) => n, getIcon: () => '📦', getColor: () => '#fff',
  }),
}));

const SECTOR_ASTEROID: any = {
  id: 101, sector_id: 101, sector_number: 101, name: 'Belt', type: 'ASTEROID_FIELD',
  region_id: null, region_name: null, hazard_level: 0, radiation_level: 0,
  resources: {}, players_present: [], special_features: [], special_formations: [],
  description: null,
};

function makeGameState(overrides: Record<string, unknown> = {}) {
  return {
    playerState: {
      id: 'player-1', username: 'tester', credits: 1000, turns: 50,
      current_sector_id: 101, is_docked: false, is_landed: false,
      defense_drones: 0, attack_drones: 0, mines: 0,
      personal_reputation: 0, reputation_tier: 'neutral', name_color: '#fff',
      military_rank: 'Cadet',
    },
    currentShip: {
      id: 'ship-1', name: 'Miner', type: 'SCOUT', sector_id: 101,
      cargo: {}, cargo_capacity: 100, current_speed: 1, base_speed: 1,
      combat: {}, maintenance: {}, is_flagship: true, purchase_value: 0,
      current_value: 0, genesis_devices: 0, max_genesis_devices: 0,
    },
    currentSector: SECTOR_ASTEROID,
    planetsInSector: [],
    stationsInSector: [],
    availableMoves: { warps: [], tunnels: [] },
    moveToSector: vi.fn(), dockAtStation: vi.fn(), undockFromStation: vi.fn(),
    claimPlanet: vi.fn(), landOnPlanet: vi.fn(), leavePlanet: vi.fn(), renamePlanet: vi.fn(),
    getPlanetDetails: vi.fn().mockResolvedValue(null),
    transferColonists: vi.fn(), updatePlanetAllocation: vi.fn(),
    getCitadelInfo: vi.fn().mockResolvedValue(null),
    upgradeCitadel: vi.fn(), cancelCitadelUpgrade: vi.fn(),
    getDefenseBuildings: vi.fn().mockResolvedValue({ buildings: [] }),
    buildDefenseBuilding: vi.fn(),
    depositToSafe: vi.fn(), withdrawFromSafe: vi.fn(),
    depositCommodityToSafe: vi.fn(), withdrawCommodityFromSafe: vi.fn(),
    setCitadelAutoDeposit: vi.fn(),
    getPlanetDefenseInfo: vi.fn().mockResolvedValue(null),
    upgradeShields: vi.fn(),
    exploreCurrentLocation: vi.fn().mockResolvedValue(undefined),
    getAvailableMoves: vi.fn().mockResolvedValue(undefined),
    refreshPlayerState: vi.fn().mockResolvedValue(undefined),
    quantumStatus: null, refineQuantumCharge: vi.fn(), error: null,
    ...overrides,
  };
}

let gameState: ReturnType<typeof makeGameState>;
vi.mock('../../../contexts/GameContext', () => ({ useGame: () => gameState }));

let autopilotState: any;
vi.mock('../../../contexts/AutopilotContext', () => ({ useAutopilot: () => autopilotState }));
vi.mock('../../../contexts/FirstLoginContext', () => ({ useFirstLogin: () => ({ requiresFirstLogin: false }) }));
vi.mock('../../../contexts/WebSocketContext', () => ({ useWebSocket: () => ({ sectorPlayers: [] }) }));

import GameDashboard from '../GameDashboard';

describe('GameDashboard harvest poll (LEG-2731)', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;

  beforeEach(() => {
    vi.useFakeTimers();
    sessionStorage.clear();
    mockHarvest.mockReset();
    mockGetHarvestStatus.mockReset();
    mockSectorWrecks.mockResolvedValue([]);
    autopilotState = {
      course: null, lastPlot: null, status: 'idle', pauseReason: null,
      currentHopIndex: 0, plotCourse: vi.fn(), engage: vi.fn(), abort: vi.fn(),
    };
    gameState = makeGameState();
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(async () => {
    vi.useRealTimers();
    sessionStorage.clear();
    await act(async () => { root.unmount(); });
    container.remove();
  });

  const flush = async () => {
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });
  };

  const mount = async () => {
    await act(async () => { root.render(<GameDashboard />); });
    await flush();
  };

  it('POST in_progress then GET COMPLETED shows yield banner (not premature COMPLETE)', async () => {
    mockHarvest.mockResolvedValue({
      status: 'in_progress',
      harvest_id: 'h-1',
      resolves_at: '2099-01-01T00:01:00Z',
      turns_spent: 5,
      remaining_turns: 45,
    });
    mockGetHarvestStatus
      .mockResolvedValueOnce({ status: 'PENDING', resolves_at: '2099-01-01T00:01:00Z' })
      .mockResolvedValueOnce({
        status: 'COMPLETED',
        ore: 42,
        precious_metals: 0,
        quantum_shards: 0,
        turns_spent: 5,
        am_rep_delta: 0,
      });

    await mount();

    const harvestBtn = container.querySelector('.planetary-harvest-btn') as HTMLButtonElement;
    expect(harvestBtn).toBeTruthy();

    await act(async () => {
      harvestBtn.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    });
    await flush();

    expect(sessionStorage.getItem(PENDING_HARVEST_STORAGE_KEY)).toBe('h-1');
    expect(container.textContent).toContain('MINING IN PROGRESS');
    expect(container.textContent).not.toContain('HARVEST COMPLETE');

    await act(async () => {
      vi.advanceTimersByTime(2000);
      await Promise.resolve();
    });
    await flush();

    await act(async () => {
      vi.advanceTimersByTime(2000);
      await Promise.resolve();
    });
    await flush();

    expect(container.textContent).toContain('HARVEST COMPLETE');
    expect(container.textContent).toContain('+42 ORE');
    expect(sessionStorage.getItem(PENDING_HARVEST_STORAGE_KEY)).toBeNull();
  });

  it('GET INTERRUPTED surfaces terminal_reason in failed banner', async () => {
    mockHarvest.mockResolvedValue({
      status: 'in_progress',
      harvest_id: 'h-2',
      resolves_at: '2099-01-01T00:01:00Z',
      turns_spent: 5,
    });
    mockGetHarvestStatus.mockResolvedValue({
      status: 'INTERRUPTED',
      terminal_reason: 'pvp_attack',
      ore: 0,
    });

    await mount();
    const harvestBtn = container.querySelector('.planetary-harvest-btn') as HTMLButtonElement;
    await act(async () => {
      harvestBtn.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    });
    await flush();

    await act(async () => {
      vi.advanceTimersByTime(2000);
      await Promise.resolve();
    });
    await flush();

    expect(container.textContent).toContain('HARVEST FAILED');
    expect(container.textContent).toMatch(/pvp attack/i);
  });
});
