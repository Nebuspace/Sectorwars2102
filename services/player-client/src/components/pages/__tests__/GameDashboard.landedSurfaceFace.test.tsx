// @vitest-environment jsdom
/**
 * GameDashboard — landed keeps the windshield vista but bezel-swaps the deck
 * to the surface face (WO-UI4-SURFACE-MODE).
 *
 * Unlike DOCKED (WO-UI3-STATION-MODE, which killed the 3D bay canvas
 * outright), the WINDSHIELD RULING (orchestrator-LOCKED) keeps landed's
 * `SolarSystemViewscreen scene="landed"` mounted — it is real type/
 * habitability/citadel-aware content, plus the bespoke `.landed-expanded`
 * 65%-band landmass enrichment. This suite pins that down: the vista +
 * `.windshield-frame` chrome anchor stay mounted landed, while the deck's
 * flight-monitor bezel chrome (`.console-monitor.planetary-ops-monitor.
 * full-width` + `.monitor-bezel`'s 4 rivet-corner decorations, and the same
 * chrome PopulationCenterInterface owns internally) is gone, replaced by
 * `.surface-face-workspace` (game-layout.css, scoped under `.game-container.
 * mode-surface`) — for BOTH landed deck branches (owned-colony and
 * population-hub) — and proves FLIGHT is untouched (regression guard: same
 * mock, same component, same assertions inverted).
 *
 * `mode-surface`'s CSS class + the MFD rail's persistence live one level up
 * in GameLayout (mocked out here as irrelevant chrome, matching
 * GameDashboard.dockedStationFace.test.tsx's proven seam) — see
 * GameLayout.modeStationPersistentRail.test.tsx for that half (its "landed:
 * mode-surface, not mode-station" case already covers the class + rail).
 *
 * Mocking mirrors GameDashboard.dockedStationFace.test.tsx's proven seam
 * exactly (GameDashboard is the SUT, not mocked; GameLayout and every heavy
 * venue/canvas child are stubbed as irrelevant chrome). PopulationCenterInterface
 * is made trackable (unlike the plain stub docked uses) — the whole point of
 * the hub half of this suite is proving GameDashboard wraps it in
 * `.surface-face-workspace` with the right planet prop.
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
// Trackable (unlike dockedStationFace's plain stub) — this suite proves
// GameDashboard wraps it in `.surface-face-workspace` with the right prop.
const populationCenterMock = vi.fn((_props?: unknown) => <div data-testid="pc-mock" />);
vi.mock('../../planetary/PopulationCenterInterface', () => ({
  default: (props: unknown) => populationCenterMock(props),
}));
// Trackable (unlike dockedStationFace's plain stub) — the whole point of
// this suite is proving landed NEVER stops mounting this (WINDSHIELD RULING).
const solarSystemViewscreenMock = vi.fn((_props?: unknown) => <div data-testid="ssv-mock" />);
vi.mock('../../tactical/SolarSystemViewscreen', () => ({
  default: (props: unknown) => solarSystemViewscreenMock(props),
}));
// WO-UI2-WINDSHIELD-TABLEAU: flight-mode mount is now WindshieldTableau, not
// SolarSystemViewscreen — trackable (same idiom as solarSystemViewscreenMock
// above) so the "flight still mounts something, landed doesn't" regression
// guard below can assert the RIGHT component for its mode.
const windshieldTableauMock = vi.fn((_props?: unknown) => <div data-testid="windshield-tableau-mock" />);
vi.mock('../../tactical/WindshieldTableau', () => ({
  default: (props: unknown) => windshieldTableauMock(props),
}));
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

const PLANET_UNCLAIMED: any = {
  id: 'planet-1', name: 'Ceti Alpha', type: 'TERRAN', sector_id: 100,
  habitability_score: 42, is_population_hub: false, owner_id: null, owner_name: null,
};

const PLANET_HUB: any = {
  id: 'planet-2', name: 'New Earth', type: 'TERRAN', sector_id: 100,
  habitability_score: 95, is_population_hub: true, owner_id: null, owner_name: null,
};

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

describe('GameDashboard — landed keeps the vista, bezel-swaps the deck (WO-UI4-SURFACE-MODE)', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;
  let errorSpy: ReturnType<typeof vi.spyOn>;

  beforeEach(() => {
    autopilotState = {
      course: null, lastPlot: null, status: 'idle', pauseReason: null,
      currentHopIndex: 0, plotCourse: vi.fn(), engage: vi.fn(), abort: vi.fn(),
    };
    solarSystemViewscreenMock.mockClear();
    populationCenterMock.mockClear();
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

  it('landed, owned-colony (not a population hub): vista mounted, deck bezel-swapped', async () => {
    gameState = makeGameState({
      playerState: {
        ...makeGameState().playerState,
        is_landed: true,
        current_planet_id: 'planet-1',
      },
      planetsInSector: [PLANET_UNCLAIMED],
    });
    await act(async () => {
      root.render(<GameDashboard />);
    });

    // WINDSHIELD RULING — the vista + its chrome anchor are PRESERVED.
    expect(solarSystemViewscreenMock).toHaveBeenCalled();
    const ssvCall = solarSystemViewscreenMock.mock.calls[0][0] as any;
    expect(ssvCall.scene).toBe('landed');
    expect(container.querySelector('[data-testid="ssv-mock"]')).not.toBeNull();
    expect(container.querySelector('.windshield-frame')).not.toBeNull();

    // Deck — the flight-monitor bezel wrapper is gone.
    expect(container.querySelector('.monitor-bezel')).toBeNull();
    expect(container.querySelector('.console-monitor')).toBeNull();
    expect(container.querySelector('.planetary-ops-monitor')).toBeNull();

    // The surface face replaces it; the salvaged content inside is untouched.
    expect(container.querySelector('.surface-face-workspace')).not.toBeNull();
    expect(container.textContent).toContain('PLANETARY OPERATIONS COMMAND');

    // a11y parity with the docked station header (WO-UI4-SURFACE-MODE REVISE):
    // region landmark on the header container, heading role on the title.
    const hudHeader = container.querySelector('.screen-hud-header');
    expect(hudHeader?.getAttribute('role')).toBe('region');
    expect(hudHeader?.getAttribute('aria-label')).toBe('Planetary Operations');
    const hudHeading = hudHeader?.querySelector('[role="heading"]');
    expect(hudHeading).not.toBeNull();
    expect(hudHeading?.getAttribute('aria-level')).toBe('2');
    expect(hudHeading?.textContent).toBe('PLANETARY OPERATIONS COMMAND');

    // Correct branch — not the population-hub interface.
    expect(populationCenterMock).not.toHaveBeenCalled();
    expect(container.querySelector('[data-testid="pc-mock"]')).toBeNull();

    expect(errorSpy).not.toHaveBeenCalled();
  });

  it('landed on a population hub: vista mounted, PopulationCenterInterface wrapped in the surface face', async () => {
    gameState = makeGameState({
      playerState: {
        ...makeGameState().playerState,
        is_landed: true,
        current_planet_id: 'planet-2',
      },
      planetsInSector: [PLANET_HUB],
    });
    await act(async () => {
      root.render(<GameDashboard />);
    });

    // WINDSHIELD RULING holds on the hub branch too — same vista either way.
    expect(solarSystemViewscreenMock).toHaveBeenCalled();
    const ssvCall = solarSystemViewscreenMock.mock.calls[0][0] as any;
    expect(ssvCall.scene).toBe('landed');
    expect(container.querySelector('.windshield-frame')).not.toBeNull();

    // Correct branch — the hub interface, not the generic owned-colony console.
    expect(populationCenterMock).toHaveBeenCalled();
    const pcCall = populationCenterMock.mock.calls[0][0] as any;
    expect(pcCall.planet).toEqual(PLANET_HUB);
    expect(container.querySelector('[data-testid="pc-mock"]')).not.toBeNull();
    expect(container.textContent).not.toContain('PLANETARY OPERATIONS COMMAND');

    // Wrapped in the same surface face workspace as the owned-colony branch.
    const workspace = container.querySelector('.surface-face-workspace');
    expect(workspace).not.toBeNull();
    expect(workspace?.querySelector('[data-testid="pc-mock"]')).not.toBeNull();

    expect(errorSpy).not.toHaveBeenCalled();
  });

  it('flight (not docked, not landed): WindshieldTableau still mounts — no regression (WO-UI2-WINDSHIELD-TABLEAU: was SolarSystemViewscreen)', async () => {
    gameState = makeGameState();
    await act(async () => {
      root.render(<GameDashboard />);
    });

    expect(windshieldTableauMock).toHaveBeenCalled();
    expect(container.querySelector('[data-testid="windshield-tableau-mock"]')).not.toBeNull();
    expect(container.querySelector('.surface-face-workspace')).toBeNull();

    expect(errorSpy).not.toHaveBeenCalled();
  });
});
