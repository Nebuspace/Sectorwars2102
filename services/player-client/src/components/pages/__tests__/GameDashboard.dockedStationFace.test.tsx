// @vitest-environment jsdom
/**
 * GameDashboard — docked renders a station FACE, not a cockpit scene
 * (WO-UI3-STATION-MODE).
 *
 * Docked used to mount `SolarSystemViewscreen scene="docked"` (a live 3D
 * bay canvas) behind a `.windshield-frame` cockpit vignette, and wrap the
 * venue content in the flight-monitor bezel chrome
 * (`.console-monitor.trading-monitor.full-width` + `.monitor-bezel`'s 4
 * rivet-corner decorations) — "cockpit + a trading monitor" per the
 * confirm-pass. This suite pins the DOCKED branch down to zero scene canvas
 * + zero bezel chrome, replaced by `.station-face-bay-band` /
 * `.station-face-workspace` (game-layout.css, scoped under
 * `.game-container.mode-station`) — and proves FLIGHT is untouched (the
 * regression guard: same mock, same component, same assertions inverted).
 *
 * `mode-station`'s CSS class + the MFD rail's persistence live one level up
 * in GameLayout (mocked out here as irrelevant chrome, matching
 * GameDashboard.overlapChipsRetired.test.tsx's proven seam) — see
 * GameLayout.modeStationPersistentRail.test.tsx for that half.
 * SpaceDockInterface's OWN persistent-undock-across-venues behavior is
 * covered by SpaceDockInterface.persistentUndock.test.tsx (it is mocked
 * here too — this file only proves GameDashboard's own DOCKED branch shape,
 * not what's inside the venue workspace).
 *
 * Mocking mirrors GameDashboard.overlapChipsRetired.test.tsx's proven seam
 * exactly (GameDashboard is the SUT, not mocked; GameLayout and every heavy
 * venue/canvas child are stubbed as irrelevant chrome).
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
// Trackable (unlike overlapChipsRetired's plain stub) — the whole point of
// this suite is proving docked NEVER invokes this component.
const solarSystemViewscreenMock = vi.fn((_props?: unknown) => <div data-testid="ssv-mock" />);
vi.mock('../../tactical/SolarSystemViewscreen', () => ({
  default: (props: unknown) => solarSystemViewscreenMock(props),
}));
vi.mock('../../tactical/PlanetPortPair', () => ({ default: () => <div /> }));
vi.mock('../../quantum/QuantumDriveConsole', () => ({ default: () => <div /> }));
vi.mock('../../gatewright/GatewrightPanel', () => ({ default: () => <div /> }));
vi.mock('../../comms/CommsMailbox', () => ({ default: () => <div /> }));
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

describe('GameDashboard — docked renders a station face, not the cockpit scene (WO-UI3-STATION-MODE)', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;
  let errorSpy: ReturnType<typeof vi.spyOn>;

  beforeEach(() => {
    autopilotState = {
      course: null, lastPlot: null, status: 'idle', pauseReason: null,
      currentHopIndex: 0, plotCourse: vi.fn(), engage: vi.fn(), abort: vi.fn(),
    };
    solarSystemViewscreenMock.mockClear();
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

  it('docked: zero SolarSystemViewscreen mounts, zero deck-monitor bezel chrome', async () => {
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

    // "Windshield" — the 3D bay scene never mounts while docked.
    expect(solarSystemViewscreenMock).not.toHaveBeenCalled();
    expect(container.querySelector('[data-testid="ssv-mock"]')).toBeNull();
    expect(container.querySelector('.windshield-frame')).toBeNull();

    // "Deck" — the flight-monitor bezel wrapper is gone.
    expect(container.querySelector('.monitor-bezel')).toBeNull();
    expect(container.querySelector('.console-monitor')).toBeNull();
    expect(container.querySelector('.trading-monitor')).toBeNull();

    // The station face replaces them.
    const bayBand = container.querySelector('.station-face-bay-band');
    expect(bayBand).not.toBeNull();
    expect(container.querySelector('.station-face-workspace')).not.toBeNull();

    // Pixel a11y REVISE — the identity strip is a landmark region, and its
    // name is discoverable as a heading (was a bare, unannounced <span>).
    expect(bayBand?.getAttribute('role')).toBe('region');
    expect(bayBand?.getAttribute('aria-label')).toBe('Docked station');
    const bayBandName = container.querySelector('.station-face-bay-band-name');
    expect(bayBandName?.getAttribute('role')).toBe('heading');
    expect(bayBandName?.getAttribute('aria-level')).toBe('2');

    // Salvaged: the CLAMPED chip.
    expect(container.textContent).toContain('CLAMPS ENGAGED');

    // Non-SpaceDock docked keeps its own persistent undock (unchanged by
    // this WO — it already sat in the always-rendered venue header, not
    // gated per-tab).
    expect(container.querySelector('.station-undock-btn')).not.toBeNull();

    expect(errorSpy).not.toHaveBeenCalled();
  });

  it('flight (not docked, not landed): SolarSystemViewscreen still mounts — no regression', async () => {
    gameState = makeGameState();
    await act(async () => {
      root.render(<GameDashboard />);
    });

    expect(solarSystemViewscreenMock).toHaveBeenCalled();
    expect(container.querySelector('[data-testid="ssv-mock"]')).not.toBeNull();
    expect(container.querySelector('.station-face-bay-band')).toBeNull();
    expect(container.querySelector('.station-face-workspace')).toBeNull();

    expect(errorSpy).not.toHaveBeenCalled();
  });
});
