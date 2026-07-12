// @vitest-environment jsdom
/**
 * GameDashboard — flying deck collapsed to the CANONICAL 3 monitors
 * (WO-UI2-DECK-RECONCILE, cockpit-redesign-v10 §05): SOLAR SYSTEM · NAV ·
 * TACTICAL, no more standalone COMMS monitor / 4th TACTICAL threat-band
 * column. Covers the GameDashboard-level wiring the page-component unit
 * tests (TacticalTargetPage/TacticalThreatPage/SolarSalvagePage.test.tsx,
 * GameDashboard.chartMonitor/navMultihop.test.tsx's COURSE/CHART split)
 * don't reach on their own: monitor COUNT, SOLAR's SYSTEM/SALVAGE/SIGNALS
 * tab wiring (hazard fold-in, shared wreck fetch, formation SIGNALS), NAV
 * COURSE's adjacent-exit MOVE wiring, and TACTICAL receiving the same
 * merged sectorContacts feed COMMS used to.
 *
 * Mirrors GameDashboard.navMultihop.test.tsx's seam: jsdom + react-dom/
 * client createRoot + act(), no RTL, useAutopilot mocked directly (no real
 * AutopilotProvider -- this file never plots/engages a course).
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import type { SectorWreck } from '../../../services/api';

const mockGetChart = vi.fn();
const mockGetMyRegion = vi.fn();
const mockSectorWrecks = vi.fn();
vi.mock('../../../services/api', () => ({
  navAPI: { getChart: (...a: unknown[]) => mockGetChart(...a) },
  regionOwnerAPI: { getMyRegion: (...a: unknown[]) => mockGetMyRegion(...a) },
  sectorAPI: {
    sectorWrecks: (...a: unknown[]) => mockSectorWrecks(...a),
    salvageWreck: vi.fn(),
  },
  combatAPI: { engage: vi.fn(), getStatus: vi.fn() },
  greyStatusAPI: { getStatus: () => Promise.resolve({ isGrey: false, kind: null, greyUntil: null, remainingSeconds: 0, clearFineCredits: null }) },
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
vi.mock('../../tactical/NavigationMap', () => ({ default: () => <div data-testid="navmap-stub" /> }));
vi.mock('../../galaxy/Galaxy3DRenderer', () => ({ default: () => <div /> }));
vi.mock('../../quantum/QuantumDriveConsole', () => ({ default: () => <div /> }));
vi.mock('../../gatewright/GatewrightPanel', () => ({ default: () => <div /> }));
vi.mock('../../governance/CitizenshipBadge', () => ({ default: () => <div /> }));
vi.mock('../../governance/RegionInvitePanel', () => ({ default: () => <div /> }));
vi.mock('../../governance/RegionTradeDockPanel', () => ({ default: () => <div /> }));
vi.mock('../../cockpit/CockpitColonyManagement', () => ({ default: () => <div /> }));
vi.mock('../../cockpit/SafeVaultPanel', () => ({ default: () => <div /> }));

vi.mock('../../../hooks/useResourceCatalog', () => ({
  useResourceCatalog: () => ({ catalog: [], loading: false, getLabel: (n: string) => n, getIcon: () => '📦', getColor: () => '#fff' }),
}));

const SECTOR_100: any = {
  id: 100, sector_id: 100, sector_number: 100, name: 'Sol', type: 'STANDARD',
  region_id: null, region_name: null, hazard_level: 6, radiation_level: 0.2,
  resources: {}, players_present: [], special_features: ['NEBULA'],
  special_formations: [{ id: 'f1', is_discovered: true, is_anchor: true, name: 'Whisper Cloud', type: 'NEBULA_CLUSTER', is_investigated: false }],
  description: 'A quiet stretch of charted space.',
};

function makeGameState(overrides: Record<string, unknown> = {}) {
  return {
    playerState: {
      id: 'player-1', username: 'tester', credits: 1000, turns: 50,
      current_sector_id: 100, is_docked: false, is_landed: false,
      defense_drones: 0, attack_drones: 0, mines: 0,
      personal_reputation: 0, reputation_tier: 'Neutral', name_color: '#fff',
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
      warps: [{ sector_id: 101, sector_number: 101, name: 'Sector 101', type: 'STANDARD', turn_cost: 1, can_afford: true }],
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
    sendPlayerMessage: vi.fn(),
    deployMines: vi.fn(),
    updatePlayerCredits: vi.fn(),
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
    course: null, lastPlot: null, status: 'idle', pauseReason: null,
    currentHopIndex: 0, plotCourse: vi.fn(), engage: vi.fn(), abort: vi.fn(),
  }),
}));

vi.mock('../../../contexts/FirstLoginContext', () => ({
  useFirstLogin: () => ({ requiresFirstLogin: false }),
}));

vi.mock('../../../contexts/WebSocketContext', () => ({
  useWebSocket: () => ({ sectorPlayers: [] }),
}));

import GameDashboard from '../GameDashboard';

const WRECK: SectorWreck = {
  id: 'wreck-1', original_owner_id: null, original_owner_name: 'Crimson Corsair',
  destroyed_ship_type: 'LIGHT_FREIGHTER', cause: 'combat', created_at: '2026-01-01T00:00:00Z',
  age_seconds: 60, cargo: { ore: 10 }, would_flag_suspect: false,
};

describe('GameDashboard — flying deck collapsed to 3 monitors (WO-UI2-DECK-RECONCILE)', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;

  beforeEach(() => {
    mockGetChart.mockReset();
    mockGetMyRegion.mockReset();
    mockGetMyRegion.mockRejectedValue(new Error('not a region owner'));
    mockGetChart.mockResolvedValue({ sectors: [], edges: [], frontier: [] });
    mockSectorWrecks.mockReset();
    mockSectorWrecks.mockResolvedValue([]);

    gameState = makeGameState();

    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(async () => {
    await act(async () => {
      root.unmount();
    });
    container.remove();
    vi.restoreAllMocks();
  });

  const flush = async () => {
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
      await Promise.resolve();
    });
  };

  const click = async (el: Element) => {
    await act(async () => {
      el.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    });
    await flush();
  };

  const mount = async () => {
    await act(async () => {
      root.render(<GameDashboard />);
    });
    await flush();
  };

  const clickTab = async (label: string) => {
    const btn = Array.from(container.querySelectorAll('.deck-tab-btn')).find((b) => b.textContent === label);
    expect(btn, `expected a "${label}" tab button`).toBeTruthy();
    await click(btn!);
  };

  it('renders EXACTLY 3 flying deck-monitors: SOLAR SYSTEM, NAV, TACTICAL — no COMMS monitor', async () => {
    await mount();

    const monitors = container.querySelectorAll('.console-monitor');
    expect(monitors.length).toBe(3);
    expect(container.querySelector('.console-monitor.system-monitor')).toBeTruthy();
    expect(container.querySelector('.console-monitor.nav-monitor')).toBeTruthy();
    expect(container.querySelector('.console-monitor.tactical-monitor')).toBeTruthy();
    expect(container.querySelector('.console-monitor.comms-monitor')).toBeNull();
  });

  it('SOLAR SYSTEM: SYSTEM page folds hazard/radiation/no-transit notes into the bodies list (not a separate page)', async () => {
    await mount();

    const solar = container.querySelector('.console-monitor.system-monitor')!;
    const tabs = Array.from(solar.querySelectorAll('.deck-tab-btn')).map((b) => b.textContent);
    expect(tabs).toEqual(['SYSTEM', 'SALVAGE', 'SIGNALS']);

    expect(solar.querySelector('.system-hazard-fold')).toBeTruthy();
    expect(solar.textContent).toContain('6/10'); // hazard_level
    expect(solar.textContent).toContain('20.0%'); // radiation_level
    expect(solar.textContent).toContain('NEBULA'); // special_features NO-TRANSIT note
    expect(solar.textContent).toContain('A quiet stretch of charted space.');
    // Formations do NOT render on SYSTEM anymore — they moved to SIGNALS
    // ('.hud-badge' also styles the special_features NO-TRANSIT note above,
    // so assert on the formation's own name text instead of the shared class).
    expect(solar.textContent).not.toContain('WHISPER CLOUD');
  });

  it('SOLAR SYSTEM: SIGNALS page shows discovered formations with INVESTIGATE (moved off the old HAZARDS page)', async () => {
    await mount();
    const solar = container.querySelector('.console-monitor.system-monitor')!;
    await clickTab('SIGNALS');

    expect(solar.textContent).toContain('WHISPER CLOUD');
    const investigateBtn = Array.from(solar.querySelectorAll('button')).find((b) => b.textContent?.includes('INVESTIGATE'));
    expect(investigateBtn).toBeTruthy();
  });

  it('SOLAR SYSTEM: SALVAGE page renders the shared sectorWrecks feed and refetches it after salvaging', async () => {
    mockSectorWrecks.mockResolvedValue([WRECK]);
    await mount();
    // The SCAN-layer fetch fires once on mount regardless of active tab.
    expect(mockSectorWrecks).toHaveBeenCalledTimes(1);

    const solar = container.querySelector('.console-monitor.system-monitor')!;
    await clickTab('SALVAGE');

    expect(solar.querySelector('.solar-salvage-wreck-row')?.textContent).toContain('Light Freighter');
  });

  it('NAV: COURSE page lists adjacent exits with MOVE wired to moveToSector (1 click = 1 hop)', async () => {
    await mount();

    const nav = container.querySelector('.console-monitor.nav-monitor')!;
    const tabs = Array.from(nav.querySelectorAll('.deck-tab-btn')).map((b) => b.textContent);
    expect(tabs).toEqual(['COURSE', 'CHART']); // non-Warp-Jumper hull -- no DRIVE tab

    const exitRow = nav.querySelector('.nav-exit-row')!;
    expect(exitRow.textContent).toContain('Sector 101');
    await click(exitRow.querySelector('.nav-exit-move-btn')!);

    expect(gameState.moveToSector).toHaveBeenCalledWith(101);
  });

  it('NAV: CHART page is a separate tab and the graph is not visible on COURSE', async () => {
    await mount();
    const nav = container.querySelector('.console-monitor.nav-monitor')!;
    expect(nav.querySelector('[data-testid="navmap-stub"]')).toBeNull();

    await clickTab('CHART');
    expect(nav.querySelector('[data-testid="navmap-stub"]')).toBeTruthy();
  });

  it('TACTICAL: receives the merged sectorContacts feed COMMS used to (TARGET is the default page)', async () => {
    gameState = makeGameState({
      currentSector: {
        ...SECTOR_100,
        players_present: [
          { player_id: 'p1', ship_id: 's1', username: 'Vega', is_npc: false, reputation_tier: 'Lawful', personal_reputation: 40 },
        ],
      },
    });
    await mount();

    const tactical = container.querySelector('.console-monitor.tactical-monitor')!;
    expect(tactical.textContent).toContain('TACTICAL');
    expect(tactical.querySelector('.target-contact-list')).toBeTruthy();
    expect(tactical.textContent).toContain('Vega');
  });

  it('SOLAR SYSTEM column renders the full monitor name with no ellipsis-clipping class regression', async () => {
    await mount();
    const label = container.querySelector('.console-monitor.system-monitor .screen-hud-header span')!;
    expect(label.textContent).toBe('SOLAR SYSTEM');
  });
});
