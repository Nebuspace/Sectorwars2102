// @vitest-environment jsdom
/**
 * GameDashboard — SOLAR SYSTEM page row state machine wiring
 * (WO-UI2-WINDSHIELD-TABLEAU item 3, cockpit-redesign-v10 §05 L1349-1352).
 *
 * PlanetPortPair.rowStateMachine.test.tsx proves the row LABEL/onClick
 * machinery in isolation; this file proves GameDashboard computes and
 * forwards the right inputs to it:
 *   - flying = autopilot.status === 'engaged' (same signal the locrow's
 *     🛑 ALL STOP chip already reads — GameDashboard.locrowGlassRetirement.
 *     test.tsx), threaded to every PlanetPortPair + the asteroid HARVEST row.
 *   - onHalt calls autopilot.abort('all stop') (same reason string the
 *     locrow chip uses).
 *   - isLanded/isDocked are a per-body id match (playerState.current_
 *     planet_id/current_port_id), NOT the old sector-wide broadcast that
 *     marked every row in a multi-planet sector landed/docked at once.
 *
 * Mirrors GameDashboard.locrowGlassRetirement.test.tsx's proven seam: real
 * GameDashboard, prop-capturing PlanetPortPair stub (not an inert <div/>),
 * mutable autopilotState.
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
vi.mock('../../tactical/SolarSystemViewscreen', () => ({ default: () => <div /> }));
// WO-UI2-WINDSHIELD-TABLEAU: flight-mode mount is now WindshieldTableau —
// inert stub, this file's row-state-machine assertions are all on
// PlanetPortPair below, not the windshield scene itself.
vi.mock('../../tactical/WindshieldTableau', () => ({ default: () => <div /> }));

// Prop-capturing stub — records every render's props keyed by planet/station
// id so this file can assert the per-row flying/here wiring GameDashboard
// computes, without needing PlanetPortPair's real confirm-dialog machinery
// (that's PlanetPortPair.rowStateMachine.test.tsx's job).
const pppCalls: Array<{ planetId: string | null; stationId: string | null; isLanded: boolean; isDocked: boolean; flying: boolean; hasOnHalt: boolean }> = [];
let lastOnHalt: (() => void) | null = null;
vi.mock('../../tactical/PlanetPortPair', () => ({
  default: (props: any) => {
    pppCalls.push({
      planetId: props.planet?.id ?? null,
      stationId: props.station?.id ?? null,
      isLanded: !!props.isLanded,
      isDocked: !!props.isDocked,
      flying: !!props.flying,
      hasOnHalt: typeof props.onHalt === 'function',
    });
    lastOnHalt = props.onHalt ?? null;
    return (
      <div
        data-testid="ppp-stub"
        data-planet-id={props.planet?.id ?? ''}
        data-station-id={props.station?.id ?? ''}
        data-flying={String(!!props.flying)}
      />
    );
  },
}));

vi.mock('../../quantum/QuantumDriveConsole', () => ({ default: () => <div /> }));
vi.mock('../../gatewright/GatewrightPanel', () => ({ default: () => <div /> }));
vi.mock('../../cockpit/CockpitColonyManagement', () => ({ default: () => <div /> }));
vi.mock('../../cockpit/SafeVaultPanel', () => ({ default: () => <div /> }));

vi.mock('../../../hooks/useResourceCatalog', () => ({
  useResourceCatalog: () => ({
    catalog: [], loading: false, getLabel: (n: string) => n, getIcon: () => '📦', getColor: () => '#fff',
  }),
}));

const SECTOR_TWO_PLANETS: any = {
  id: 100, sector_id: 100, sector_number: 100, name: 'Sol', type: 'STANDARD',
  region_id: null, region_name: null, hazard_level: 0, radiation_level: 0,
  resources: {}, players_present: [], special_features: [], special_formations: [],
  description: null,
};

const SECTOR_ASTEROID: any = {
  ...SECTOR_TWO_PLANETS, id: 101, sector_id: 101, type: 'ASTEROID_FIELD',
};

const PLANET_A = { id: 'planet-a', name: 'Alpha', type: 'terran', status: 'active', sector_id: 100, owner_id: null, owner_name: null, population: 0, max_population: 0, habitability_score: 50 };
const PLANET_B = { id: 'planet-b', name: 'Beta', type: 'ice', status: 'active', sector_id: 100, owner_id: null, owner_name: null, population: 0, max_population: 0, habitability_score: 20 };
const STATION_A = { id: 'station-a', name: 'Ring A', type: 'trading_post', status: 'operational', sector_id: 100, owner_id: null, owner_name: null };

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
    currentSector: SECTOR_TWO_PLANETS,
    planetsInSector: [PLANET_A, PLANET_B],
    stationsInSector: [STATION_A],
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

describe('GameDashboard — SOLAR SYSTEM row state machine wiring (WO-UI2-WINDSHIELD-TABLEAU item 3)', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;
  let errorSpy: ReturnType<typeof vi.spyOn>;

  beforeEach(() => {
    pppCalls.length = 0;
    mockSectorWrecks.mockReset();
    mockSectorWrecks.mockResolvedValue([]);
    autopilotState = {
      course: null, lastPlot: null, status: 'idle', pauseReason: null,
      currentHopIndex: 0, plotCourse: vi.fn(), engage: vi.fn(), abort: vi.fn(),
    };
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

  it('idle (not engaged): every row gets flying=false and a real onHalt callback', async () => {
    await mount();

    expect(pppCalls.length).toBeGreaterThan(0);
    for (const call of pppCalls) {
      expect(call.flying).toBe(false);
      expect(call.hasOnHalt).toBe(true);
    }
  });

  it('engaged: every row gets flying=true (same signal as the locrow ALL STOP chip)', async () => {
    autopilotState.status = 'engaged';
    await mount();

    expect(pppCalls.length).toBeGreaterThan(0);
    for (const call of pppCalls) {
      expect(call.flying).toBe(true);
    }
    const stubs = Array.from(container.querySelectorAll('[data-testid="ppp-stub"]'));
    expect(stubs.length).toBeGreaterThan(0);
    for (const s of stubs) {
      expect(s.getAttribute('data-flying')).toBe('true');
    }
  });

  it('the onHalt passed to PlanetPortPair calls autopilot.abort("all stop") — same reason the locrow chip uses', async () => {
    autopilotState.status = 'engaged';
    await mount();

    expect(lastOnHalt).toBeTruthy();
    lastOnHalt!();
    expect(autopilotState.abort).toHaveBeenCalledWith('all stop');
  });

  it('multi-planet sector: isLanded is a PER-BODY id match, not a sector-wide broadcast', async () => {
    // NOTE: is_landed/is_docked stay FALSE here on purpose — flipping either
    // true switches GameDashboard's whole console to the surface-face-
    // workspace/station-face-workspace branch, which UNMOUNTS the SOLAR
    // SYSTEM monitor (and every PlanetPortPair row) entirely; verified live
    // by this test failing with zero pppCalls when is_landed was set true.
    // That means today current_planet_id/current_port_id can never be set
    // while this monitor is mounted — the "here" branch this fix targets is
    // architecturally unreachable in the current app, a correctness/future-
    // proofing fix rather than an active-bug fix. This test proves the pure
    // id-match FORMULA GameDashboard now uses (current_planet_id===planet.id
    // per row, not playerState.is_landed broadcast to every row) is correct
    // in isolation from that unrelated render gate.
    gameState = makeGameState({
      playerState: {
        ...makeGameState().playerState,
        current_planet_id: 'planet-b',
      },
    });
    await mount();

    const forA = pppCalls.find((c) => c.planetId === 'planet-a');
    const forB = pppCalls.find((c) => c.planetId === 'planet-b');
    expect(forA?.isLanded).toBe(false);
    expect(forB?.isLanded).toBe(true);
  });

  it('multi-station sector: isDocked is a PER-BODY id match, not a sector-wide broadcast', async () => {
    // Same rationale as above — is_docked stays false; current_port_id
    // alone drives the per-row match.
    const STATION_B = { ...STATION_A, id: 'station-b', name: 'Ring B' };
    gameState = makeGameState({
      planetsInSector: [PLANET_A],
      stationsInSector: [STATION_A, STATION_B],
      playerState: {
        ...makeGameState().playerState,
        current_port_id: 'station-b',
      },
    });
    await mount();

    const forStationA = pppCalls.find((c) => c.stationId === 'station-a');
    const forStationB = pppCalls.find((c) => c.stationId === 'station-b');
    expect(forStationA?.isDocked).toBe(false);
    expect(forStationB?.isDocked).toBe(true);
  });

  it('asteroid field, idle: HARVEST button renders (not HALT)', async () => {
    gameState = makeGameState({
      currentSector: SECTOR_ASTEROID,
      planetsInSector: [],
      stationsInSector: [],
    });
    await mount();

    const solar = container.querySelector('.mon.system-monitor')!;
    expect(solar.querySelector('.planetary-harvest-btn')).toBeTruthy();
    expect(Array.from(solar.querySelectorAll('button')).some((b) => b.textContent?.includes('HALT'))).toBe(false);
  });

  it('asteroid field, engaged (flying): row shows 🛑 HALT ▸ (.act.armed) instead of HARVEST, and halts on click', async () => {
    autopilotState.status = 'engaged';
    gameState = makeGameState({
      currentSector: SECTOR_ASTEROID,
      planetsInSector: [],
      stationsInSector: [],
    });
    await mount();

    const solar = container.querySelector('.mon.system-monitor')!;
    expect(solar.querySelector('.planetary-harvest-btn')).toBeNull();
    const haltBtn = Array.from(solar.querySelectorAll('button.act.armed')).find((b) => b.textContent === '🛑 HALT ▸') as HTMLButtonElement;
    expect(haltBtn).toBeTruthy();

    await act(async () => {
      haltBtn.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    });
    await flush();
    expect(autopilotState.abort).toHaveBeenCalledWith('all stop');
  });
});
