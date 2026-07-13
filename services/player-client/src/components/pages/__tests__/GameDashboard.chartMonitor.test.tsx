// @vitest-environment jsdom
/**
 * GameDashboard — NAV deck-monitor /game/map parity (WO-UI2-CHART-MONITOR).
 *
 * Most of the Accept criteria this WO targets (glyph legend, plotted-course
 * overlay + total-turns, unreachable nearest-known feedback, engage/abort
 * wiring) were ALREADY shipped by an earlier WO-NAV-COURSE-OVERLAY pass --
 * this file's job is to pin that existing wiring with real coverage (none
 * existed at the GameDashboard level, and AutopilotContext itself had ZERO
 * test coverage anywhere in the repo before this file), plus the one actual
 * gap this WO adds: the 2D/3D chart-view toggle.
 *
 * Unlike GameDashboard.navMultihop.test.tsx (which mocks useAutopilot
 * entirely), this file wraps GameDashboard in the REAL AutopilotProvider --
 * apiClient.post is the only mocked seam for the plot round-trip, so the
 * plot -> overlay/total-turns, plot -> unreachable-feedback, and (uniquely)
 * engage -> one-hop-at-a-time-via-moveToSector paths are exercised through
 * genuine AutopilotContext state machine transitions, not a stand-in. This
 * is the one place engage()'s setTimeout hop-chain gets proven at all.
 *
 * Mirrors GalaxyMap.chart.test.tsx / GameDashboard.navMultihop.test.tsx's
 * seam: jsdom + react-dom/client createRoot + act(), no RTL, no new deps.
 * Every child component not under test is stubbed (same suppression list
 * navMultihop.test.tsx already validated against this exact component);
 * NavigationMap, DeckPageTabs, and the real AutopilotContext stay REAL.
 * Galaxy3DRenderer is additionally stubbed here -- its R3F/Canvas tree
 * cannot render under jsdom (no WebGL context), same reason GalaxyMap.
 * chart.test.tsx stubs it for its own 3D-mode coverage.
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import type { NavChartResponse, NavChartSector } from '../../../services/api';

// ---------------------------------------------------------------------------
// services/api -- navAPI.getChart is the only fetch under test here; the
// windshield SCAN layer's wreck feed and the (GameDashboard no-longer-
// mounted, kept defensively) region-owner probe are resolved inert.
// ---------------------------------------------------------------------------
const mockGetChart = vi.fn();
const mockGetMyRegion = vi.fn();
vi.mock('../../../services/api', () => ({
  navAPI: {
    getChart: (...a: unknown[]) => mockGetChart(...a),
  },
  regionOwnerAPI: { getMyRegion: (...a: unknown[]) => mockGetMyRegion(...a) },
  sectorAPI: { sectorWrecks: () => Promise.resolve([]) },
  // TACTICAL[TARGET]/[THREAT] (WO-UI2-DECK-RECONCILE) only call these from
  // click handlers / the THREAT page (never mounted -- TACTICAL defaults to
  // TARGET, not under test here) -- undefined stand-ins are never invoked.
  combatAPI: undefined,
  greyStatusAPI: undefined,
}));

// apiClient -- AutopilotContext's OWN plot POST target (used by the real,
// unmocked AutopilotProvider wrapping GameDashboard below). GameDashboard's
// own two direct apiClient.post call sites (mining harvest, formation
// investigate) are never exercised by these tests.
const mockPost = vi.fn();
vi.mock('../../../services/apiClient', () => ({
  default: { post: (...a: unknown[]) => mockPost(...a) },
}));

vi.mock('../../layouts/GameLayout', () => ({
  default: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
}));

// Galaxy3DRenderer -- R3F/Canvas needs a real WebGL context; stubbed to a
// bare marker div (mirrors GalaxyMap.chart.test.tsx's identical stub).
vi.mock('../../galaxy/Galaxy3DRenderer', () => ({
  default: () => <div data-testid="galaxy-3d-stub" />,
}));

// Every other console/venue child GameDashboard mounts is irrelevant to the
// NAV chart monitor and stubbed to a bare div (same list navMultihop.test.tsx
// already validated against this exact component).
vi.mock('../../trading/TradingInterface', () => ({ default: () => <div /> }));
vi.mock('../../spacedock/SpaceDockInterface', () => ({ default: () => <div /> }));
vi.mock('../../spacedock/PortOfficeVenue', () => ({ default: () => <div /> }));
vi.mock('../../spacedock/ContractBoardVenue', () => ({ default: () => <div /> }));
vi.mock('../../planetary/PopulationCenterInterface', () => ({ default: () => <div /> }));
vi.mock('../../tactical/SolarSystemViewscreen', () => ({ default: () => <div /> }));
vi.mock('../../tactical/PlanetPortPair', () => ({ default: () => <div /> }));
vi.mock('../../quantum/QuantumDriveConsole', () => ({ default: () => <div /> }));
vi.mock('../../gatewright/GatewrightPanel', () => ({ default: () => <div /> }));
vi.mock('../../governance/CitizenshipBadge', () => ({ default: () => <div /> }));
vi.mock('../../governance/RegionInvitePanel', () => ({ default: () => <div /> }));
vi.mock('../../governance/RegionTradeDockPanel', () => ({ default: () => <div /> }));
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

// ---------------------------------------------------------------------------
// GameContext -- mutable module-scope object (mirrors navMultihop.test.tsx's
// "Mutable mock hook state across rerenders" seam). AutopilotContext's real
// provider reads moveToSector/playerState from this SAME mocked module.
// ---------------------------------------------------------------------------
const SECTOR_100: any = {
  id: 100, sector_id: 100, sector_number: 100, name: 'Sol', type: 'STANDARD',
  region_id: null, region_name: null, hazard_level: 0, radiation_level: 0,
  resources: {}, players_present: [], special_features: [], special_formations: [],
};

function makeGameState(overrides: Record<string, unknown> = {}) {
  return {
    playerState: {
      id: 'player-1', username: 'tester', credits: 1000, turns: 50,
      current_sector_id: 100, is_docked: false, is_landed: false,
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
    availableMoves: {
      warps: [{ sector_id: 101, sector_number: 101, name: 'Adjacent', type: 'STANDARD', turn_cost: 1, can_afford: true }],
      tunnels: [],
    },
    moveToSector: vi.fn().mockResolvedValue({}),
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

vi.mock('../../../contexts/FirstLoginContext', () => ({
  useFirstLogin: () => ({ requiresFirstLogin: false }),
}));

vi.mock('../../../contexts/WebSocketContext', () => ({
  useWebSocket: () => ({ sectorPlayers: [] }),
}));

import GameDashboard from '../GameDashboard';
import { AutopilotProvider } from '../../../contexts/AutopilotContext';

function sector(id: number, overrides: Partial<NavChartSector> = {}): NavChartSector {
  return { sector_id: id, name: `Sector ${id}`, type: 'normal', x: id, y: 0, z: 0, visited: true, current: false, ...overrides };
}
const DEFAULT_CHART: NavChartResponse = {
  sectors: [sector(100, { name: 'Sol', current: true })],
  edges: [],
  frontier: [],
};

// A 2-hop reachable course to sector 103 (neither hop pre-known in
// DEFAULT_CHART -- NavigationMap's course-chain-injection synthesizes both
// nodes from the course itself, proving the overlay doesn't depend on the
// deep chart already knowing the route).
const REACHABLE_PLOT = {
  success: true,
  reachable: true,
  target_sector_id: 103,
  hops: [
    { sector_id: 101, name: 'Adjacent', turn_cost: 1, visited: false, safety_rating: null, via_tunnel: false },
    { sector_id: 103, name: 'Three Hop Deep', turn_cost: 2, visited: false, safety_rating: null, via_tunnel: false },
  ],
  total_turns: 3,
};

const UNREACHABLE_PLOT = {
  success: true,
  reachable: false,
  target_sector_id: 999999,
  nearest_known: { sector_id: 103, name: 'Three Hop Deep' },
};

describe('GameDashboard — NAV chart monitor (/game/map parity, WO-UI2-CHART-MONITOR)', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;
  let rafQueue: FrameRequestCallback[] = [];
  let rafOrig: unknown;
  let cafOrig: unknown;

  beforeEach(() => {
    mockGetChart.mockReset();
    mockGetMyRegion.mockReset();
    mockGetMyRegion.mockRejectedValue(new Error('not a region owner'));
    mockGetChart.mockResolvedValue(DEFAULT_CHART);
    mockPost.mockReset();
    mockPost.mockResolvedValue({ data: {} });

    gameState = makeGameState();

    rafOrig = (global as any).requestAnimationFrame;
    cafOrig = (global as any).cancelAnimationFrame;
    rafQueue = [];
    (global as any).requestAnimationFrame = (cb: FrameRequestCallback): number => {
      rafQueue.push(cb);
      return rafQueue.length;
    };
    (global as any).cancelAnimationFrame = () => {};

    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(async () => {
    await act(async () => {
      root.unmount();
    });
    container.remove();
    (global as any).requestAnimationFrame = rafOrig;
    (global as any).cancelAnimationFrame = cafOrig;
    vi.restoreAllMocks();
  });

  // Same per-frame drain as navMultihop.test.tsx -- see that file's own
  // comment for why a synchronous batch-drain leaves `settled` stale.
  const drainRaf = async (guardCap = 200): Promise<number> => {
    let passes = 0;
    while (rafQueue.length > 0 && passes < guardCap) {
      const cb = rafQueue.shift()!;
      // eslint-disable-next-line no-await-in-loop
      await act(async () => {
        cb(0);
      });
      passes++;
    }
    return passes;
  };

  const flush = async () => {
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
      await Promise.resolve();
    });
  };

  const flushTimers = async () => {
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 10));
    });
  };

  // Full settle: lets any state-driven topology change (e.g. a freshly
  // plotted course chain-injecting new nodes) run its force-simulation to
  // completion before assertions read node/overlay geometry.
  const settle = async () => {
    await flush();
    await drainRaf();
    await flush();
    await flushTimers();
  };

  const mount = async () => {
    await act(async () => {
      root.render(
        <AutopilotProvider>
          <GameDashboard />
        </AutopilotProvider>
      );
    });
    await settle();
  };

  const findButton = (text: string): HTMLButtonElement | undefined =>
    Array.from(container.querySelectorAll('button')).find((b) => b.textContent?.includes(text)) as
      | HTMLButtonElement
      | undefined;

  const clickButton = async (text: string) => {
    const btn = findButton(text);
    expect(btn, `expected a button containing "${text}"`).toBeTruthy();
    await act(async () => {
      btn!.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    });
  };

  const setInputValue = async (selector: string, value: string) => {
    const el = container.querySelector(selector) as HTMLInputElement;
    expect(el, `expected an input matching ${selector}`).toBeTruthy();
    await act(async () => {
      const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value')!.set!;
      setter.call(el, value);
      el.dispatchEvent(new Event('input', { bubbles: true }));
    });
  };

  const plot = async (targetSectorId: number) => {
    await setInputValue('.nav-plot-input', String(targetSectorId));
    await clickButton('PLOT');
    await settle();
  };

  // -- (a) reach glyphs ------------------------------------------------------

  it('renders the HERE / IN-RANGE / OUT-OF-RANGE / FRONTIER reach legend', async () => {
    await mount();
    // NAV[COURSE] is the default page (WO-UI2-DECK-RECONCILE) -- the legend
    // is NavigationMap's own markup, which only mounts on NAV[CHART].
    await clickButton('CHART');
    await settle();

    const legend = container.querySelector('.navigation-instructions')?.textContent || '';
    expect(legend).toContain('Here');
    expect(legend).toContain('In Range');
    expect(legend).toContain('Out of Range');
    expect(legend).toContain('Frontier');
  });

  // -- (b) plot -> overlay + total_turns -------------------------------------

  it('plotting a reachable multi-hop course draws the overlay polyline and shows total turns', async () => {
    mockPost.mockImplementation((url: string) =>
      url === '/api/v1/nav/plot'
        ? Promise.resolve({ data: REACHABLE_PLOT })
        : Promise.resolve({ data: {} })
    );

    await mount();
    await plot(103);

    expect(mockPost).toHaveBeenCalledWith('/api/v1/nav/plot', { target_sector_id: 103, objective: 'min_time' });
    // The course summary (.nav-course-meta) lives on NAV[COURSE] itself
    // (WO-UI2-DECK-RECONCILE, §05: "plotted course + ENGAGE" is COURSE's own
    // content) -- read it before switching pages.
    expect(container.querySelector('.nav-course-meta')?.textContent).toContain('3');

    // The polyline overlay + hop waypoint markers are NavigationMap's own
    // markup, which only mounts on NAV[CHART] -- switch pages to read them.
    await clickButton('CHART');
    await settle();

    expect(container.querySelector('[data-testid="course-polyline"]')).toBeTruthy();
    // Both hop waypoint markers rendered, including the chain-injected one
    // (103) with no prior entry in DEFAULT_CHART.
    expect(container.querySelector('[data-testid="course-hop-marker-101"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="course-hop-marker-103"]')).toBeTruthy();
  });

  // -- (c) engage -> hops execute one-at-a-time ------------------------------

  it('engaging autopilot executes hops one-at-a-time via moveToSector, in course order', async () => {
    mockPost.mockImplementation((url: string) =>
      url === '/api/v1/nav/plot'
        ? Promise.resolve({ data: REACHABLE_PLOT })
        : Promise.resolve({ data: {} })
    );
    gameState.moveToSector = vi.fn((id: number) => Promise.resolve({ success: true, new_sector_id: id }));

    await mount();
    await plot(103);

    await clickButton('ENGAGE');
    await settle();

    // First hop only -- the second must NOT have fired yet (proves
    // one-at-a-time, not both hops dispatched together on engage()).
    expect(gameState.moveToSector).toHaveBeenCalledTimes(1);
    expect(gameState.moveToSector).toHaveBeenNthCalledWith(1, 101);

    // engage()'s hop chain schedules the next hop ~800ms later via a real
    // setTimeout -- wait past it with a real timer (this file, like
    // navMultihop.test.tsx, keeps requestAnimationFrame the only faked
    // primitive; a real 900ms wait is simpler and less fragile here than
    // juggling vi.useFakeTimers() around the rAF polyfill above).
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 900));
    });
    await settle();

    expect(gameState.moveToSector).toHaveBeenCalledTimes(2);
    expect(gameState.moveToSector).toHaveBeenNthCalledWith(2, 103);
  }, 15000);

  // -- (d) unreachable -> nearest-known feedback, not a crash ----------------

  it('an unreachable target renders nearest-known feedback instead of crashing', async () => {
    mockPost.mockImplementation((url: string) =>
      url === '/api/v1/nav/plot'
        ? Promise.resolve({ data: UNREACHABLE_PLOT })
        : Promise.resolve({ data: {} })
    );

    await mount();
    await plot(999999);

    const strip = container.querySelector('.nav-course-unreachable');
    expect(strip).toBeTruthy();
    expect(strip?.textContent).toContain('BEYOND CHARTED SPACE');
    expect(strip?.textContent).toContain('103');
    // No engage control for an unreachable plot (no course was ever set).
    expect(findButton('ENGAGE')).toBeFalsy();
  });

  // -- (e) 2D/3D toggle preserves the monitor mount --------------------------

  it('toggling 2D/3D swaps the chart renderer without unmounting the NAV monitor scaffold', async () => {
    await mount();
    // The 2D/3D toolbar is NAV[CHART]-only content now (WO-UI2-DECK-
    // RECONCILE moved it out of the shared header into CHART's own page).
    await clickButton('CHART');
    await settle();

    const monitorBefore = container.querySelector('.mon.nav-monitor');
    expect(monitorBefore).toBeTruthy();
    expect(container.querySelector('.navigation-map-wrapper')).toBeTruthy();
    expect(container.querySelector('[data-testid="galaxy-3d-stub"]')).toBeFalsy();

    await clickButton('3D');
    await flush();

    expect(container.querySelector('.mon.nav-monitor')).toBe(monitorBefore);
    expect(container.querySelector('[data-testid="galaxy-3d-stub"]')).toBeTruthy();
    expect(container.querySelector('.navigation-map-wrapper')).toBeFalsy();

    await clickButton('2D');
    await settle();

    expect(container.querySelector('.mon.nav-monitor')).toBe(monitorBefore);
    expect(container.querySelector('.navigation-map-wrapper')).toBeTruthy();
    expect(container.querySelector('[data-testid="galaxy-3d-stub"]')).toBeFalsy();
  });
});
