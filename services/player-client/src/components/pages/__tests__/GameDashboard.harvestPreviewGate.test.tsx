// @vitest-environment jsdom
/**
 * LEG-2687 — HARVEST pre-click grey-out via yield-preview gate (mining.md:251).
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const mockPreview = vi.fn();
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
    getYieldPreview: (...a: unknown[]) => mockPreview(...a),
    harvest: vi.fn(),
  },
}));

vi.mock('react-router-dom', () => ({
  useNavigate: () => vi.fn(),
}));

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
vi.mock('../../tactical/PlanetPortPair', () => ({ default: () => null }));
vi.mock('../../quantum/QuantumDriveConsole', () => ({ default: () => <div /> }));
vi.mock('../../gatewright/GatewrightPanel', () => ({ default: () => <div /> }));
vi.mock('../../cockpit/CockpitColonyManagement', () => ({ default: () => <div /> }));
vi.mock('../../cockpit/SafeVaultPanel', () => ({ default: () => <div /> }));

vi.mock('../../../hooks/useResourceCatalog', () => ({
  useResourceCatalog: () => ({
    catalog: [],
    loading: false,
    getLabel: (n: string) => n,
    getIcon: () => '📦',
    getColor: () => '#fff',
  }),
}));

const SECTOR_ASTEROID: any = {
  id: 101,
  sector_id: 101,
  sector_number: 101,
  name: 'Belt',
  type: 'ASTEROID_FIELD',
  region_id: null,
  region_name: null,
  hazard_level: 0,
  radiation_level: 0,
  resources: {},
  players_present: [],
  special_features: [],
  special_formations: [],
  description: null,
};

function makeGameState(overrides: Record<string, unknown> = {}) {
  return {
    playerState: {
      id: 'player-1',
      username: 'tester',
      credits: 1000,
      turns: 50,
      current_sector_id: 101,
      is_docked: false,
      is_landed: false,
      current_port_id: undefined,
      current_planet_id: undefined,
      defense_drones: 0,
      attack_drones: 0,
      mines: 0,
      personal_reputation: 0,
      reputation_tier: 'neutral',
      name_color: '#fff',
      military_rank: 'Cadet',
    },
    currentShip: {
      id: 'ship-1',
      name: 'Tester',
      type: 'SCOUT',
      sector_id: 101,
      cargo: {},
      cargo_capacity: 100,
      current_speed: 1,
      base_speed: 1,
      combat: {},
      maintenance: {},
      is_flagship: true,
      purchase_value: 0,
      current_value: 0,
      genesis_devices: 0,
      max_genesis_devices: 0,
    },
    currentSector: SECTOR_ASTEROID,
    planetsInSector: [],
    stationsInSector: [],
    availableMoves: { warps: [], tunnels: [] },
    moveToSector: vi.fn(),
    dockAtStation: vi.fn(),
    undockFromStation: vi.fn(),
    claimPlanet: vi.fn(),
    landOnPlanet: vi.fn(),
    leavePlanet: vi.fn(),
    renamePlanet: vi.fn(),
    getPlanetDetails: vi.fn().mockResolvedValue(null),
    transferColonists: vi.fn(),
    updatePlanetAllocation: vi.fn(),
    getCitadelInfo: vi.fn().mockResolvedValue(null),
    upgradeCitadel: vi.fn(),
    cancelCitadelUpgrade: vi.fn(),
    getDefenseBuildings: vi.fn().mockResolvedValue({ buildings: [] }),
    buildDefenseBuilding: vi.fn(),
    depositToSafe: vi.fn(),
    withdrawFromSafe: vi.fn(),
    depositCommodityToSafe: vi.fn(),
    withdrawCommodityFromSafe: vi.fn(),
    setCitadelAutoDeposit: vi.fn(),
    getPlanetDefenseInfo: vi.fn().mockResolvedValue(null),
    upgradeShields: vi.fn(),
    exploreCurrentLocation: vi.fn().mockResolvedValue(undefined),
    getAvailableMoves: vi.fn().mockResolvedValue(undefined),
    refreshPlayerState: vi.fn().mockResolvedValue(undefined),
    quantumStatus: null,
    refineQuantumCharge: vi.fn(),
    error: null,
    ...overrides,
  };
}

let gameState: ReturnType<typeof makeGameState>;
vi.mock('../../../contexts/GameContext', () => ({
  useGame: () => gameState,
}));

vi.mock('../../../contexts/AutopilotContext', () => ({
  useAutopilot: () => ({
    course: null,
    lastPlot: null,
    status: 'idle',
    pauseReason: null,
    currentHopIndex: 0,
    plotCourse: vi.fn(),
    engage: vi.fn(),
    abort: vi.fn(),
  }),
}));

vi.mock('../../../contexts/FirstLoginContext', () => ({
  useFirstLogin: () => ({ requiresFirstLogin: false }),
}));

vi.mock('../../../contexts/WebSocketContext', () => ({
  useWebSocket: () => ({ sectorPlayers: [] }),
}));

import GameDashboard from '../GameDashboard';

describe('GameDashboard — HARVEST yield-preview pre-click gate (LEG-2687)', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;
  let errorSpy: ReturnType<typeof vi.spyOn>;

  beforeEach(() => {
    mockPreview.mockReset();
    mockSectorWrecks.mockReset();
    mockSectorWrecks.mockResolvedValue([]);
    gameState = makeGameState();
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
    errorSpy = vi.spyOn(console, 'error').mockImplementation(() => {});
  });

  afterEach(async () => {
    await act(async () => {
      root.unmount();
    });
    container.remove();
    errorSpy.mockRestore();
  });

  const flush = async () => {
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
      await Promise.resolve();
    });
  };

  const mount = async () => {
    await act(async () => {
      root.render(<GameDashboard />);
    });
    await flush();
  };

  const harvestBtn = () =>
    container.querySelector('.planetary-harvest-btn') as HTMLButtonElement | null;

  it('disables HARVEST and shows gate copy when preview reports no_mining_laser', async () => {
    mockPreview.mockRejectedValue(new Error('no_mining_laser'));
    await mount();

    const btn = harvestBtn();
    expect(btn).toBeTruthy();
    expect(btn!.disabled).toBe(true);
    expect(btn!.getAttribute('aria-disabled')).toBe('true');
    expect(container.querySelector('[data-testid="harvest-gate-reason"]')?.textContent).toContain(
      'No mining laser equipped',
    );
  });

  it('leaves HARVEST enabled when preview succeeds', async () => {
    mockPreview.mockResolvedValue({
      success: true,
      reason: null,
      ore_lo: 8,
      ore_hi: 12,
      richness_tier: 3,
      laser_level: 2,
      turns_cost: 5,
    });
    await mount();

    const btn = harvestBtn();
    expect(btn).toBeTruthy();
    expect(btn!.disabled).toBe(false);
    expect(container.querySelector('[data-testid="harvest-gate-reason"]')).toBeNull();
  });
});
