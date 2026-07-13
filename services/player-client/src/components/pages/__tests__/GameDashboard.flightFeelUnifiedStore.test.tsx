// @vitest-environment jsdom
/**
 * GameDashboard — WO-UI2-FLIGHT-FEEL seam fix, the acceptance integration
 * proof.
 *
 * Every other GameDashboard.*.test.tsx stubs WindshieldTableau out to an
 * inert `<div/>` (its real /contents fetch + DOM-geometry glide aren't
 * needed for those files' own assertions). THIS file is the one place that
 * mounts the REAL WindshieldTableau alongside the REAL SOLAR SYSTEM row
 * (PlanetPortPair, also unstubbed) and the REAL locrow, all under the SAME
 * WindshieldFlightProvider GameDashboard's own outer wrapper now mounts —
 * proving the exact gap unit tests (which stub one side or the other) can't
 * see: a SOLAR row's "🧭 APPROACH ▸" click actually moves the windshield
 * ship marker, flips the row to HALT, and surfaces the locrow's ALL STOP
 * chip, then HALT/ALL-STOP both actually stop it.
 *
 * Mirrors GameDashboard.solarRowStateMachine.test.tsx's mock harness
 * (services/api, react-router-dom, GameLayout, every OTHER heavy child) —
 * WindshieldTableau and PlanetPortPair are simply left off that stub list,
 * and apiClient additionally gets a `.get` mock (WindshieldTableau's own
 * /contents fetch — GameDashboard itself only ever calls apiClient.post,
 * per its own two direct call sites, both irrelevant here).
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const mockSectorWrecks = vi.fn();
vi.mock('../../../services/api', () => ({
  navAPI: {
    getChart: vi.fn().mockResolvedValue({ sectors: [], edges: [], frontier: [] }),
    getThreat: vi.fn().mockResolvedValue([]),
  },
  sectorAPI: { sectorWrecks: (...a: unknown[]) => mockSectorWrecks(...a) },
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
// Only the default (canvas orrery, 'docked'/'landed' scenes) is stubbed —
// WindshieldTableau.tsx (real, unstubbed, below) imports NAMED exports
// (STAR_RADIUS_FACTOR, shipFaction) from this SAME module, so the real ones
// must survive the mock.
vi.mock('../../tactical/SolarSystemViewscreen', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../tactical/SolarSystemViewscreen')>();
  return { ...actual, default: () => <div /> };
});
vi.mock('../../quantum/QuantumDriveConsole', () => ({ default: () => <div /> }));
vi.mock('../../gatewright/GatewrightPanel', () => ({ default: () => <div /> }));
vi.mock('../../cockpit/CockpitColonyManagement', () => ({ default: () => <div /> }));
vi.mock('../../cockpit/SafeVaultPanel', () => ({ default: () => <div /> }));
// WindshieldTableau and PlanetPortPair are DELIBERATELY left real — this
// file's entire point is proving their live handshake through the shared
// WindshieldFlightContext.

vi.mock('../../../hooks/useResourceCatalog', () => ({
  useResourceCatalog: () => ({
    catalog: [], loading: false, getLabel: (n: string) => n, getIcon: () => '📦', getColor: () => '#fff',
  }),
}));

// WindshieldTableau's /contents GET — the ONLY apiClient consumer this file
// exercises for real (GameDashboard itself only ever POSTs, elsewhere).
const mockApiGet = vi.fn();
vi.mock('../../../services/apiClient', () => ({
  default: {
    get: (...a: unknown[]) => mockApiGet(...a),
    post: vi.fn().mockResolvedValue({ data: {} }),
  },
}));

const SECTOR: any = {
  id: 100, sector_id: 100, sector_number: 100, name: 'Sol', type: 'STANDARD',
  region_id: null, region_name: null, hazard_level: 0, radiation_level: 0,
  resources: {}, players_present: [], special_features: [], special_formations: [],
  description: null,
};

const PLANET_ALPHA = {
  id: 'planet-a', name: 'Alpha', type: 'terran', status: 'active', sector_id: 100,
  owner_id: 'owner-x', owner_name: 'Someone Else', population: 4200, habitability_score: 62,
};

// WindshieldTableau's own /contents contract (SectorContentsResponse subset
// — mirrors WindshieldTableau.test.tsx's TEST_SYSTEM fixture) — SAME
// 'planet-a' id as PLANET_ALPHA above, so flight.approach('planet-a')
// resolves to a real position.
const CONTENTS_SYSTEM = {
  sector_id: 100, sector_type: 'normal',
  star: { kind: 'G_YELLOW', label: 'Sol', color: '#ffdd88' },
  nebula: null, belt: null, debris: null, habitable_zone: null,
  bodies: [{
    slot: 0, orbit_au: 0.5, kind: 'TERRAN', size_class: 3,
    palette: { hue: 120, sat: 40 }, rings: false, moons: 0, phase_deg: 90,
    real: true, planet_id: 'planet-a', name: 'Alpha', habitability: 62, owned: true,
  }],
  stations: [],
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
    currentSector: SECTOR,
    planetsInSector: [PLANET_ALPHA],
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

describe('GameDashboard — WO-UI2-FLIGHT-FEEL unified flight store (row APPROACH -> ship moves -> HALT -> ALL STOP)', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;
  let errorSpy: ReturnType<typeof vi.spyOn>;

  beforeEach(() => {
    mockSectorWrecks.mockReset();
    mockSectorWrecks.mockResolvedValue([]);
    mockApiGet.mockReset();
    mockApiGet.mockResolvedValue({ data: CONTENTS_SYSTEM });
    autopilotState = {
      course: null, lastPlot: null, status: 'idle', pauseReason: null,
      currentHopIndex: 0, plotCourse: vi.fn(), engage: vi.fn(), abort: vi.fn(),
    };
    gameState = makeGameState();
    // WindshieldTableau's popup-position math + this file's freeze-on-halt
    // assertions both need a real-looking rect (WindshieldTableau.test.tsx's
    // own established fixture).
    vi.spyOn(Element.prototype, 'getBoundingClientRect').mockReturnValue({
      width: 800, height: 400, top: 0, left: 0, right: 800, bottom: 400, x: 0, y: 0,
      toJSON() { return {}; },
    } as DOMRect);
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
    await flush(); // WindshieldTableau's own /contents fetch resolves one microtask later
  };

  const click = async (el: Element) => {
    await act(async () => {
      el.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    });
    await flush();
  };

  it('mounts the real windshield + real SOLAR row for Alpha, fed by the SAME /contents fetch', async () => {
    await mount();
    expect(mockApiGet).toHaveBeenCalledWith('/api/v1/sectors/100/contents');
    expect(container.querySelector('.ssv-tableau .shipmk')).not.toBeNull();
    const approachBtn = container.querySelector('[aria-label="Approach Alpha"]') as HTMLButtonElement;
    expect(approachBtn).toBeTruthy();
    expect(approachBtn.textContent).toBe('🧭 APPROACH ▸');
  });

  it('THE ACCEPTANCE CASE: clicking the row\'s APPROACH ▸ moves the ship marker, flips the row to HALT, and surfaces the locrow ALL STOP chip', async () => {
    await mount();

    const ship = container.querySelector('.ssv-tableau .shipmk') as HTMLElement;
    const leftBefore = ship.style.left;
    const topBefore = ship.style.top;
    expect(ship.className).not.toContain('burning');
    expect(container.querySelector('.locrow')?.textContent).not.toContain('ALL STOP');

    const approachBtn = container.querySelector('[aria-label="Approach Alpha"]') as HTMLButtonElement;
    await click(approachBtn);

    // 1. The ship marker's position changed toward the target.
    const shipAfter = container.querySelector('.ssv-tableau .shipmk') as HTMLElement;
    expect(shipAfter.style.left).not.toBe(leftBefore);
    expect(shipAfter.style.top).not.toBe(topBefore);
    expect(shipAfter.className).toContain('burning');

    // 2. The row flipped to HALT (same body, same section).
    const haltBtn = container.querySelector('[aria-label="Halt — abort autopilot and hold position"]') as HTMLButtonElement;
    expect(haltBtn).toBeTruthy();
    expect(haltBtn.textContent).toBe('🛑 HALT ▸');
    expect(container.querySelector('[aria-label="Approach Alpha"]')).toBeNull();

    // 3. The locrow shows the ALL STOP chip.
    const locrow = container.querySelector('.locrow')!;
    const allStopChip = Array.from(locrow.querySelectorAll('.loc')).find((c) => c.textContent?.includes('ALL STOP'));
    expect(allStopChip).toBeTruthy();

    // 4. HALT stops it: burning clears, the row reverts to APPROACH, and the
    //    locrow ALL STOP chip disappears.
    await click(haltBtn);
    const shipStopped = container.querySelector('.ssv-tableau .shipmk') as HTMLElement;
    expect(shipStopped.className).not.toContain('burning');
    expect(container.querySelector('[aria-label="Approach Alpha"]')).toBeTruthy();
    const locrowAfterHalt = container.querySelector('.locrow')!;
    expect(Array.from(locrowAfterHalt.querySelectorAll('.loc')).some((c) => c.textContent?.includes('ALL STOP'))).toBe(false);
  });

  it('the locrow\'s own ALL STOP chip click also halts the row\'s glide (same shared store, either control stops it)', async () => {
    await mount();

    const approachBtn = container.querySelector('[aria-label="Approach Alpha"]') as HTMLButtonElement;
    await click(approachBtn);
    expect((container.querySelector('.ssv-tableau .shipmk') as HTMLElement).className).toContain('burning');

    const locrow = container.querySelector('.locrow')!;
    const allStopChip = Array.from(locrow.querySelectorAll('.loc')).find((c) => c.textContent?.includes('ALL STOP')) as HTMLButtonElement;
    expect(allStopChip).toBeTruthy();
    await click(allStopChip);

    expect((container.querySelector('.ssv-tableau .shipmk') as HTMLElement).className).not.toContain('burning');
    expect(container.querySelector('[aria-label="Approach Alpha"]')).toBeTruthy();
    expect(autopilotState.abort).toHaveBeenCalledWith('all stop');
  });
});
