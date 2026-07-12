// @vitest-environment jsdom
/**
 * GameDashboard — NAV multi-hop known-graph feed (WO-NAV-MULTIHOP-FEED,
 * sub-part b).
 *
 * Sub-part (a) built chartToNavSectors (navChartTransform.ts, own unit
 * tests) -- a pure BFS/cap transform over the player's full known-space
 * graph from GET /nav/chart. This pins the WIRING into the cockpit: the
 * deep graph is MERGED (never replaces) the existing 1-hop `navSectors`
 * feed built from currentSector + availableMoves, so a fresh player's
 * unvisited-but-adjacent destinations (server-classified as frontier,
 * but still directly warpable) never vanish, while a player who has
 * explored further sees rings beyond direct adjacency. Frontier stubs
 * with NO existing 1-hop entry now render too (WO-NAV-CHART-POLISH),
 * as a distinct glyph -- never as a full `circle.node-circle` -- see
 * "renders a frontier-only id as a distinct glyph" below.
 *
 * Mirrors GalaxyMap.chart.test.tsx's seam: jsdom + react-dom/client
 * createRoot + act(), no RTL, no new deps -- every child component not
 * under test (station/planetary/comms/governance venues, the vista
 * viewport, GameLayout chrome) is stubbed; NavigationMap and
 * navChartTransform stay REAL so the assertions exercise the actual
 * render pipeline, not a mock of it. The force-directed layout's rAF
 * loop is drained via a queueing polyfill (a truly-synchronous invoke
 * would blow the call stack recursing through simulate()'s own tail
 * call), one QUEUED CALLBACK AT A TIME, each inside its own act() --
 * see drainRaf's own comment below for why a batch drain (fine for
 * NavigationMap.courseOverlay.test.tsx, which never asserts a frame
 * count) understates settling here, where the assertions do.
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import type { NavChartResponse, NavChartSector, NavChartEdge, NavChartFrontier } from '../../../services/api';

// ---------------------------------------------------------------------------
// services/api -- navAPI.getChart is the fetch under test. regionOwnerAPI's
// probe RELOCATED off GameDashboard to
// components/governance/RegionOwnerControls.tsx at the WO-UI0-STATUSBAR
// integration step (it now mounts inside StatusBar's LocationDropdown, an
// ancestor of GameDashboard) -- kept mocked here defensively even though
// GameDashboard no longer imports it, since nothing under THIS test's tree
// mounts RegionOwnerControls either.
// ---------------------------------------------------------------------------
const mockGetChart = vi.fn();
const mockGetMyRegion = vi.fn();
vi.mock('../../../services/api', () => ({
  navAPI: {
    getChart: (...a: unknown[]) => mockGetChart(...a),
    // WO-UI2-TACTICAL-MONITOR: the flight TACTICAL monitor's rollup fetch --
    // not under test here, resolved empty so the mount doesn't reject.
    getThreat: () => Promise.resolve([]),
  },
  regionOwnerAPI: { getMyRegion: (...a: unknown[]) => mockGetMyRegion(...a) },
  // WO-UI2-LIVING-WINDSHIELD: the flight SSV's SCAN-layer wrecks fetch --
  // not under test here, resolved empty so the mount doesn't reject.
  sectorAPI: { sectorWrecks: () => Promise.resolve([]) },
}));

// react-router-dom: GameDashboard calls useNavigate() unconditionally at the
// top of the component; no <Router> is mounted here since nothing under
// test performs navigation.
vi.mock('react-router-dom', () => ({
  useNavigate: () => vi.fn(),
}));

// GameLayout is page chrome, out of scope (mirrors GalaxyMap.chart.test /
// Dashboard.icons.test's own UserProfile stub).
vi.mock('../../layouts/GameLayout', () => ({
  default: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
}));

// Every other console/venue child GameDashboard mounts is irrelevant to the
// NAV chart feed and stubbed to a bare div -- keeps the render tree focused
// and avoids pulling in the vista canvas engine (SolarSystemViewscreen) or
// other heavy subtrees this WO never touches.
vi.mock('../../trading/TradingInterface', () => ({ default: () => <div /> }));
vi.mock('../../spacedock/SpaceDockInterface', () => ({ default: () => <div /> }));
vi.mock('../../spacedock/PortOfficeVenue', () => ({ default: () => <div /> }));
vi.mock('../../spacedock/ContractBoardVenue', () => ({ default: () => <div /> }));
vi.mock('../../planetary/PopulationCenterInterface', () => ({ default: () => <div /> }));
vi.mock('../../tactical/SolarSystemViewscreen', () => ({ default: () => <div /> }));
vi.mock('../../tactical/PlanetPortPair', () => ({ default: () => <div /> }));
vi.mock('../../quantum/QuantumDriveConsole', () => ({ default: () => <div /> }));
vi.mock('../../gatewright/GatewrightPanel', () => ({ default: () => <div /> }));
vi.mock('../../comms/CommsMailbox', () => ({ default: () => <div /> }));
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
// GameContext -- mutable module-scope object (mirrors GalaxyMap.chart.test's
// `let availableMoves` / "Mutable mock hook state across rerenders": a
// vi.mock'd hook reads a let-bound object, and a test reassigns + re-renders
// to simulate a sector change without remounting).
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

// ---------------------------------------------------------------------------
// Chart fixtures
// ---------------------------------------------------------------------------
function sector(id: number, overrides: Partial<NavChartSector> = {}): NavChartSector {
  return { sector_id: id, name: `Sector ${id}`, type: 'normal', x: id, y: 0, z: 0, visited: true, current: false, ...overrides };
}
function chart(sectors: NavChartSector[], edges: NavChartEdge[], frontier: NavChartFrontier[] = []): NavChartResponse {
  return { sectors, edges, frontier };
}

// current(100) -- 1 hop --> 101 -- 1 hop --> 102 -- 1 hop --> 103, plus a
// frontier stub (999) that must never render as a full node.
const CHART_DEEP: NavChartResponse = chart(
  [
    sector(100, { name: 'Sol', current: true }),
    sector(101, { name: 'Adjacent Reach' }),
    sector(102, { name: 'Two Hop Deep' }),
    sector(103, { name: 'Three Hop Deep' }),
  ],
  [
    { from: 100, to: 101, kind: 'warp' },
    { from: 101, to: 102, kind: 'warp' },
    { from: 102, to: 103, kind: 'warp' },
  ],
  [{ id: 999, from: 103 }],
);

// Fresh player: only the current sector is known; sector 101 -- the SAME id
// availableMoves lists as an unvisited adjacent warp destination -- is
// classified frontier by the server/util. This is the exact collision the
// MERGE (not replace) design exists for.
const CHART_FRESH: NavChartResponse = chart(
  [sector(100, { name: 'Sol', current: true })],
  [],
  [{ id: 101, from: 100 }],
);

describe('GameDashboard — NAV multi-hop known-graph feed', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;
  let rafQueue: FrameRequestCallback[] = [];
  let rafOrig: unknown;
  let cafOrig: unknown;

  beforeEach(() => {
    mockGetChart.mockReset();
    mockGetMyRegion.mockReset();
    mockGetMyRegion.mockRejectedValue(new Error('not a region owner'));
    mockGetChart.mockResolvedValue(CHART_DEEP);

    gameState = makeGameState();
    autopilotState = {
      course: null, lastPlot: null, status: 'idle', pauseReason: null,
      currentHopIndex: 0, plotCourse: vi.fn(), engage: vi.fn(), abort: vi.fn(),
    };

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

  // Iteratively drains queued rAF callbacks one at a time -- each callback
  // is awaited inside its OWN act() so NavigationMap's `settled`/
  // `frameCount` closure state (mutated inside a setNodes(prevNodes => ...)
  // updater, which React applies asynchronously, not at the setNodes call
  // site) is actually committed before the NEXT callback runs. Draining a
  // whole queued batch synchronously in one tight loop (no yield back to
  // React in between) leaves `settled` permanently stale at its pre-frame-1
  // value for every callback in that pass, so `if (!settled)` never stops
  // re-scheduling and frameCount free-runs well past NavigationMap's
  // internal 120-frame cap -- this is what a first attempt at this drain
  // (a synchronous batch-drain, matching NavigationMap.courseOverlay.test's
  // own polyfill) actually measured. That existing course-overlay test
  // never asserts a frame COUNT so the staleness was invisible there; this
  // file does, so the drain needs the per-frame commit. Returns the number
  // of frames actually run, so a caller can assert settling happened
  // within the internal budget rather than merely hitting the guard.
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

  // Lets a settled cycle's trailing `setTimeout(() => setIsSimulating(false),
  // 0)` (a REAL timer, unrelated to node positions -- see NavigationMap.tsx
  // and NavigationMap.courseOverlay.test.tsx's own identical `flush`) fire
  // inside an act() boundary instead of leaking past the test.
  const flushTimers = async () => {
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 10));
    });
  };

  // NAV's DeckPageTabs rail (WO-UI2-DECK-RECONCILE, §05: [COURSE · CHART ·
  // DRIVE]) defaults to COURSE -- NavigationMap only mounts on CHART now
  // (the plot row that used to sit in the shared header, always visible
  // alongside the graph, moved into COURSE's own page content). Every test
  // in this file exercises the graph/force-layout, so `mount()` switches to
  // CHART immediately after the initial render, BEFORE the first flush --
  // this preserves the original mount ordering the "caps rendered nodes"
  // test's frame-budget comment relies on (NavigationMap's own first
  // commit still lands on the small pre-chart-fetch graph, exactly as it
  // did when it was unconditionally visible; the deep chart's data lands
  // via a later re-render either way).
  const clickChartTab = async () => {
    const btn = Array.from(container.querySelectorAll('button')).find(
      (b) => b.textContent?.trim() === 'CHART'
    );
    expect(btn, 'expected a CHART tab button').toBeTruthy();
    await act(async () => {
      btn!.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    });
  };

  const mount = async () => {
    await act(async () => {
      root.render(<GameDashboard />);
    });
    await clickChartTab();
    await flush();
    await drainRaf();
    await flush();
    await flushTimers();
  };

  const nodeTitles = (): string[] =>
    Array.from(container.querySelectorAll('circle.node-circle title')).map((t) => t.textContent || '');

  const nodeCount = (): number => container.querySelectorAll('circle.node-circle').length;

  it('renders beyond-adjacency nodes (2-hop, 3-hop) while the 1-hop entries keep their existing name/type', async () => {
    await mount();

    expect(mockGetChart).toHaveBeenCalled();
    const titles = nodeTitles();
    // 1-hop entries (current + the availableMoves warp target) are sourced
    // from the EXISTING navSectors feed, byte-for-byte -- "Sector 100" /
    // "Sector 101", not the deep chart's "Sol" / "Adjacent Reach".
    expect(titles).toContain('Sector 100');
    expect(titles).toContain('Sector 101');
    // Beyond-adjacency nodes exist ONLY in the deep graph -- their presence
    // is proof the multi-hop feed reached the render, not just the 1-hop ring.
    expect(titles).toContain('Two Hop Deep');
    expect(titles).toContain('Three Hop Deep');
    expect(nodeCount()).toBe(4);
  });

  it('never renders a frontier-only id as a full node (circle.node-circle) -- it gets the distinct frontier glyph instead', async () => {
    await mount();

    const titles = nodeTitles();
    expect(titles).not.toContain('Sector 999');
    expect(titles.some((t) => t.includes('999'))).toBe(false);
  });

  it('renders a frontier-only id as a distinct glyph (WO-NAV-CHART-POLISH) -- linked to its source sector', async () => {
    await mount();

    // CHART_DEEP's frontier stub {id: 999, from: 103} has no colliding
    // 1-hop entry (only 100/101 are directly warpable) -- it renders as
    // the dedicated frontier glyph, not a circle.node-circle.
    const glyph = container.querySelector('[data-testid="frontier-node-999"]');
    expect(glyph).toBeTruthy();
    expect(glyph?.tagName.toLowerCase()).toBe('rect');
    expect(glyph?.classList.contains('frontier-glyph')).toBe(true);
  });

  it('preserves the fresh-player 1-hop view unchanged when the deep chart only knows the current sector (MERGE, not replace)', async () => {
    mockGetChart.mockResolvedValue(CHART_FRESH);

    await mount();

    expect(mockGetChart).toHaveBeenCalled();
    const titles = nodeTitles();
    // Sector 101 is simultaneously (a) an unvisited availableMoves warp
    // target -- so it MUST still render and be clickable -- and (b) the
    // chart's own frontier id for the same sector. A literal replace would
    // drop it; the merge keeps it via the untouched 1-hop navSectors feed.
    expect(titles.sort()).toEqual(['Sector 100', 'Sector 101']);
    expect(nodeCount()).toBe(2);
  });

  it('click-to-move works on an adjacent rendered node fed by the untouched 1-hop feed', async () => {
    mockGetChart.mockResolvedValue(CHART_FRESH);
    await mount();

    const hitTarget = Array.from(container.querySelectorAll('circle[fill="transparent"]'))
      .find((el) => el.querySelector('title')?.textContent === 'Sector 101');
    expect(hitTarget).toBeTruthy();

    await act(async () => {
      hitTarget!.dispatchEvent(new Event('pointerdown', { bubbles: true }));
    });

    expect(gameState.moveToSector).toHaveBeenCalledWith(101);
  });

  it('refetches the known-graph chart when the current sector changes', async () => {
    await mount();
    expect(mockGetChart).toHaveBeenCalledTimes(1);

    gameState = makeGameState({
      currentSector: {
        ...SECTOR_100, id: 200, sector_id: 200, sector_number: 200, name: 'Nova',
      },
      playerState: { ...makeGameState().playerState, current_sector_id: 200 },
    });
    await act(async () => {
      root.render(<GameDashboard />);
    });
    await flush();
    await clickChartTab();
    await flush();
    await drainRaf();
    await flush();
    await flushTimers();

    expect(mockGetChart).toHaveBeenCalledTimes(2);
  });

  // Draining up to ~120 real frames, each committed inside its own act()
  // (see drainRaf above for why per-frame commits are required), is slow
  // under full-suite parallel CPU contention -- well past vitest's 5s
  // default. Generous explicit timeout below; this is real physics work,
  // not a hang.
  it('caps rendered nodes at the util ceiling (150) and settles the force layout within its ~120-frame budget for a 300-known-sector graph', async () => {
    // Star graph: current sector 100 directly connected to 299 neighbors,
    // all at depth 1 -- well within the default depth cap, so only the
    // node ceiling truncates (mirrors navChartTransform.test.ts's own
    // 150-cap fixture).
    const NEIGHBOR_COUNT = 299;
    const sectors: NavChartSector[] = [sector(100, { name: 'Sol', current: true })];
    const edges: NavChartEdge[] = [];
    for (let i = 101; i <= 100 + NEIGHBOR_COUNT; i++) {
      sectors.push(sector(i));
      edges.push({ from: 100, to: i, kind: 'warp' });
    }
    const bigChart = chart(sectors, edges);
    vi.spyOn(console, 'info').mockImplementation(() => {});
    mockGetChart.mockResolvedValue(bigChart);

    await act(async () => {
      root.render(<GameDashboard />);
    });
    await clickChartTab();
    await flush();
    const frames = await drainRaf();
    await flush();
    await flushTimers();

    expect(nodeCount()).toBeLessThanOrEqual(150);
    expect(nodeCount()).toBeGreaterThan(0);
    // NavigationMap's simulation caps at 120 frames internally; settling
    // strictly within that (not the drain loop's much larger guard) proves
    // the merged 150-node graph doesn't blow the existing perf budget. The
    // total includes exactly one extra frame from the transient 1-hop-only
    // graph NavigationMap renders on the very first commit -- before the
    // deep chart arrives, mergedNavSectors is just the small existing
    // navSectors feed, which settles trivially (well under the velocity
    // threshold) in a single frame before the 150-node cycle's own topology
    // takes over -- so 120 (the big cycle's own internal cap) + 1 (the
    // small warm-up cycle) is the true, deterministic ceiling here, not a
    // fudge factor.
    expect(frames).toBeLessThanOrEqual(121);
  }, 20000);
});
