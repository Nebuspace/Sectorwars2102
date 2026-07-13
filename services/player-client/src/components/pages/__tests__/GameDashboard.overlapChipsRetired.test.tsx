// @vitest-environment jsdom
/**
 * GameDashboard — top-left canvas-chip overlap defect retired
 * (WO-UI0-STATUSBAR serial integration).
 *
 * The three `HudChip id="location"`/`"station"`/`"landed"` blocks — the
 * ONLY top-left `HudChip`s (rendered as `.hud-overlay.top-left`)
 * GameDashboard ever mounted, across all three scene branches (flight /
 * docked / landed) — were deleted at the integration step; their
 * location-context readouts (sector, region, CitizenshipBadge, docked/landed
 * target name+type) relocated into StatusBar's LocationDropdown, and their
 * owner-controls (GOVERNANCE/picker/INVITE/TRADEDOCK + both portals)
 * relocated into RegionOwnerControls. This mounts GameDashboard in each of
 * the three scene states and pins `.hud-overlay.top-left` absent in every
 * one (NOT the bare `.top-left` class alone — every scene ALSO renders a
 * purely-decorative `.frame-corner.top-left` windshield-vignette marker,
 * unrelated to the chip-overlap defect and correctly left alone) — the
 * structural, jsdom-provable half of "the overlap is gone" (real
 * pixel-geometry is the Orchestrator's 1440×900 Playwright pass).
 *
 * Mocking mirrors GameDashboard.navMultihop.test.tsx's proven seam exactly
 * (GameDashboard is the SUT, not mocked; GameLayout and every heavy
 * venue/canvas child are stubbed as irrelevant chrome for this DOM-shape
 * assertion) — trimmed to the state/mocks this file's 3 scenes need.
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

vi.mock('../../../services/api', () => ({
  navAPI: {
    getChart: vi.fn().mockResolvedValue({ sectors: [], edges: [], frontier: [] }),
    // WO-UI2-TACTICAL-MONITOR: the flight TACTICAL monitor's rollup fetch --
    // not under test here, resolved empty so the mount doesn't reject.
    getThreat: vi.fn().mockResolvedValue([]),
  },
  // WO-UI2-LIVING-WINDSHIELD: the flight SSV's SCAN-layer wrecks fetch --
  // not under test here, resolved empty so the mount doesn't reject.
  sectorAPI: { sectorWrecks: vi.fn().mockResolvedValue([]) },
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
vi.mock('../../tactical/PlanetPortPair', () => ({ default: () => <div /> }));
vi.mock('../../quantum/QuantumDriveConsole', () => ({ default: () => <div /> }));
vi.mock('../../gatewright/GatewrightPanel', () => ({ default: () => <div /> }));
vi.mock('../../cockpit/CockpitColonyManagement', () => ({ default: () => <div /> }));
vi.mock('../../cockpit/SafeVaultPanel', () => ({ default: () => <div /> }));

vi.mock('../../../hooks/useResourceCatalog', () => ({
  useResourceCatalog: () => ({
    catalog: [], loading: false, getLabel: (n: string) => n, getIcon: () => '📦', getColor: () => '#fff',
  }),
}));

const SECTOR_100: any = {
  id: 100, sector_id: 100, sector_number: 100, name: 'Sol', type: 'STANDARD',
  region_id: null, region_name: null, hazard_level: 0, radiation_level: 0,
  resources: {}, players_present: [], special_features: [], special_formations: [],
};

const STATION_1: any = { id: 'station-1', name: 'Trading Post', type: 'TRADING', sector_id: 100, services: {} };
const PLANET_1: any = { id: 'planet-1', name: 'New Earth', type: 'TERRAN', owner_id: 'player-1', habitability_score: 80 };

function makeGameState(overrides: Record<string, unknown> = {}) {
  return {
    playerState: {
      id: 'player-1', username: 'tester', credits: 1000, turns: 50,
      current_sector_id: 100, is_docked: false, is_landed: false,
      current_port_id: undefined, current_planet_id: undefined,
      defense_drones: 0, attack_drones: 0, mines: 0,
      personal_reputation: 0, reputation_tier: 'neutral', name_color: '#fff',
      military_rank: 'Cadet',
    },
    currentShip: {
      id: 'ship-1', name: 'Tester', type: 'SCOUT', sector_id: 100,
      cargo: {}, cargo_capacity: 100, current_speed: 1, base_speed: 1,
      combat: {}, maintenance: {}, is_flagship: true, purchase_value: 0,
      current_value: 0, genesis_devices: 0, max_genesis_devices: 0,
    },
    currentSector: SECTOR_100,
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
vi.mock('../../../contexts/GameContext', () => ({
  useGame: () => gameState,
}));

let autopilotState: any;
vi.mock('../../../contexts/AutopilotContext', () => ({
  useAutopilot: () => autopilotState,
}));

vi.mock('../../../contexts/FirstLoginContext', () => ({
  useFirstLogin: () => ({ requiresFirstLogin: false }),
}));

vi.mock('../../../contexts/WebSocketContext', () => ({
  useWebSocket: () => ({ sectorPlayers: [] }),
}));

import GameDashboard from '../GameDashboard';

describe('GameDashboard — top-left canvas-chip overlap defect retired', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;
  let errorSpy: ReturnType<typeof vi.spyOn>;

  beforeEach(() => {
    autopilotState = {
      course: null, lastPlot: null, status: 'idle', pauseReason: null,
      currentHopIndex: 0, plotCourse: vi.fn(), engage: vi.fn(), abort: vi.fn(),
    };
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

  it('flight scene: no .top-left chip (retired id="location")', async () => {
    gameState = makeGameState();
    await act(async () => {
      root.render(<GameDashboard />);
    });
    // `.hud-overlay.top-left` (HudChip's own rendered class combo, see
    // GameDashboard.tsx's HudChip impl) — NOT the plain `.top-left` CSS
    // class alone, which also matches the purely-decorative
    // `.frame-corner.top-left` windshield-vignette marker present in every
    // scene (a static border gradient, never part of the overlap defect).
    expect(container.querySelectorAll('.hud-overlay.top-left').length).toBe(0);
    expect(errorSpy).not.toHaveBeenCalled();
  });

  it('docked scene: no .top-left chip (retired id="station")', async () => {
    gameState = makeGameState({
      playerState: {
        ...makeGameState().playerState,
        is_docked: true,
        current_port_id: 'station-1',
      },
      stationsInSector: [STATION_1],
    });
    await act(async () => {
      root.render(<GameDashboard />);
    });
    // `.hud-overlay.top-left` (HudChip's own rendered class combo, see
    // GameDashboard.tsx's HudChip impl) — NOT the plain `.top-left` CSS
    // class alone, which also matches the purely-decorative
    // `.frame-corner.top-left` windshield-vignette marker present in every
    // scene (a static border gradient, never part of the overlap defect).
    expect(container.querySelectorAll('.hud-overlay.top-left').length).toBe(0);
    expect(errorSpy).not.toHaveBeenCalled();
  });

  it('landed scene: no .top-left chip (retired id="landed")', async () => {
    gameState = makeGameState({
      playerState: {
        ...makeGameState().playerState,
        is_landed: true,
        current_planet_id: 'planet-1',
      },
      planetsInSector: [PLANET_1],
    });
    await act(async () => {
      root.render(<GameDashboard />);
    });
    // `.hud-overlay.top-left` (HudChip's own rendered class combo, see
    // GameDashboard.tsx's HudChip impl) — NOT the plain `.top-left` CSS
    // class alone, which also matches the purely-decorative
    // `.frame-corner.top-left` windshield-vignette marker present in every
    // scene (a static border gradient, never part of the overlap defect).
    expect(container.querySelectorAll('.hud-overlay.top-left').length).toBe(0);
    expect(errorSpy).not.toHaveBeenCalled();
  });
});
