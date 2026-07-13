// @vitest-environment jsdom
/**
 * GameDashboard — SOLAR SYSTEM[SYSTEM] sensor-list additions
 * (WO-UI-MAX-BATCH-1 items 8-9, Max #10/#13/#16, revised by Max #21):
 *
 *   9. STAR + decorative-body rows (dim, no action) fetched from the real
 *      GET /sectors/{id}/contents endpoint WindshieldTableau.tsx already
 *      consumes for the flight scene — this is a SECOND reader of the same
 *      cheap, deterministic, per-sector snapshot. Real (`real:true`) bodies
 *      are excluded — those are the DB planets planetsInSector/PlanetPortPair
 *      already render, proven elsewhere (GameDashboard.solarRowStateMachine
 *      .test.tsx).
 *   9. Formation + wreck sensor rows, gated on the SAME `scanActive` flag
 *      the windshield's own glyphs use (SURVEY reuses the real
 *      handleInvestigateFormation call; wreck APPROACH jumps to SALVAGE).
 *   8. Hazards render as terse, un-numbered rows on SYSTEM — named (sector
 *      TYPE match) or the generic fallback — never a %-block or a bare
 *      hazard_level/radiation_level number (that detail lives on the new
 *      HAZARD tab, proven in GameDashboard.deckReconcile.test.tsx).
 *
 * Mirrors GameDashboard.solarRowStateMachine.test.tsx's seam: PlanetPortPair
 * stubbed (its own row machinery is proven in PlanetPortPair
 * .rowStateMachine.test.tsx + GameDashboard.solarRowStateMachine.test.tsx),
 * WindshieldTableau stubbed inert, apiClient mocked so the /contents fetch
 * this file adds is deterministic (not a real network reject).
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

const mockApiGet = vi.fn();
const mockApiPost = vi.fn();
vi.mock('../../../services/apiClient', () => ({
  default: {
    get: (...a: unknown[]) => mockApiGet(...a),
    post: (...a: unknown[]) => mockApiPost(...a),
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

const SECTOR_STANDARD: any = {
  id: 100, sector_id: 100, sector_number: 100, name: 'Sol', type: 'STANDARD',
  region_id: 'region-1', region_name: 'The Frontier', hazard_level: 6, radiation_level: 0.2,
  resources: {}, players_present: [], special_features: [],
  special_formations: [
    { id: 'f-known', is_discovered: true, is_anchor: true, name: 'Whisper Cloud', type: 'NEBULA_CLUSTER' },
    { id: 'f-unknown', is_discovered: false, is_anchor: false, name: null, type: null },
  ],
  description: 'A quiet stretch of charted space.',
};

const SECTOR_CLEAN: any = {
  ...SECTOR_STANDARD, id: 101, sector_id: 101, hazard_level: 0, radiation_level: 0,
  special_formations: [],
};

const SECTOR_NEBULA_TYPE: any = {
  ...SECTOR_STANDARD, id: 102, sector_id: 102, type: 'NEBULA', hazard_level: 0, radiation_level: 0,
  special_formations: [],
};

const CONTENTS_RESPONSE = {
  star: { kind: 'K_ORANGE', label: 'K-class Orange Dwarf', color: '#ffa94d' },
  bodies: [
    { slot: 1, kind: 'BARREN', real: false },
    { slot: 2, kind: 'GAS_GIANT', real: false },
    { slot: 3, kind: 'TERRAN', real: true, planet_id: 'planet-a' }, // real -- must NOT get a decorative row
  ],
  stations: [],
};

const WRECK = {
  id: 'wreck-1', original_owner_id: null, original_owner_name: null,
  destroyed_ship_type: 'LIGHT_FREIGHTER', cause: 'combat', created_at: '2026-01-01T00:00:00Z',
  age_seconds: 10, cargo: {}, would_flag_suspect: false,
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
    currentSector: SECTOR_STANDARD,
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

describe('GameDashboard — SOLAR SYSTEM[SYSTEM] sensor-list rows (WO-UI-MAX-BATCH-1 items 8-9)', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;
  let errorSpy: ReturnType<typeof vi.spyOn>;

  beforeEach(() => {
    mockSectorWrecks.mockReset();
    mockSectorWrecks.mockResolvedValue([WRECK]);
    mockApiGet.mockReset();
    mockApiGet.mockResolvedValue({ data: CONTENTS_RESPONSE });
    mockApiPost.mockReset();
    mockApiPost.mockResolvedValue({ data: { success: true } });
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
      await Promise.resolve();
    });
  };

  const mount = async () => {
    await act(async () => {
      root.render(<GameDashboard />);
    });
    await flush();
  };

  const click = async (el: Element) => {
    await act(async () => {
      el.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    });
    await flush();
  };

  it('STAR + decorative-body rows render dim, no action — real bodies are excluded (PlanetPortPair owns those)', async () => {
    await mount();

    expect(mockApiGet).toHaveBeenCalledWith('/api/v1/sectors/100/contents');
    const solar = container.querySelector('.mon.system-monitor')!;
    const rows = Array.from(solar.querySelectorAll('.mbody > .row'));

    const starRow = rows.find((r) => r.textContent?.includes('K-CLASS ORANGE DWARF'));
    expect(starRow).toBeTruthy();
    expect(starRow!.querySelector('button')).toBeNull(); // dim, no action
    expect(starRow!.textContent).toContain('primary');

    const barrenRow = rows.find((r) => r.textContent?.startsWith('🌑 BARREN') || r.textContent?.includes('BARREN'));
    expect(barrenRow).toBeTruthy();
    expect(barrenRow!.querySelector('button')).toBeNull();
    expect(barrenRow!.textContent).toContain('uninhabitable');

    const gasGiantRow = rows.find((r) => r.textContent?.includes('GAS GIANT'));
    expect(gasGiantRow).toBeTruthy();
    expect(gasGiantRow!.querySelector('button')).toBeNull();

    // The real (`real:true`) TERRAN body must NOT get its own decorative
    // row — planetsInSector/PlanetPortPair is its one renderer.
    expect(solar.textContent).not.toMatch(/TERRAN(?! WORLD)/);

    expect(errorSpy).not.toHaveBeenCalled();
  });

  it('uninhabitable-body filter (T1-B) defaults OFF — decorative bodies visible, STAR row unaffected by the toggle', async () => {
    await mount();
    const solar = container.querySelector('.mon.system-monitor')!;

    const filterBtn = solar.querySelector('.system-filter-toggle') as HTMLButtonElement;
    expect(filterBtn).toBeTruthy();
    expect(filterBtn.getAttribute('aria-pressed')).toBe('false');
    expect(filterBtn.getAttribute('aria-label')).toBe('Hide uninhabitable bodies');

    // DEFAULT OFF: both decorative bodies from CONTENTS_RESPONSE render.
    expect(solar.textContent).toContain('BARREN');
    expect(solar.textContent).toContain('GAS GIANT');
    expect(solar.textContent).toContain('K-CLASS ORANGE DWARF'); // the star, unrelated to the filter

    expect(errorSpy).not.toHaveBeenCalled();
  });

  it('uninhabitable-body filter (T1-B) toggling ON drops the decorative rows (count assertion), toggling back OFF restores them', async () => {
    await mount();
    const solar = container.querySelector('.mon.system-monitor')!;
    const rowsBefore = solar.querySelectorAll('.mbody > .row').length;

    const filterBtn = solar.querySelector('.system-filter-toggle') as HTMLButtonElement;
    await click(filterBtn);

    expect(filterBtn.getAttribute('aria-pressed')).toBe('true');
    // Label-in-Name (WCAG 2.5.3): aria-label stays the STABLE literal while
    // aria-pressed (asserted above) is the sole state carrier — a dynamic
    // per-state label would drop the visible "HIDE UNINHABITABLE" text from
    // the accessible name in the ON state.
    expect(filterBtn.getAttribute('aria-label')).toBe('Hide uninhabitable bodies');
    // The 2 decorative CONTENTS_RESPONSE bodies (BARREN, GAS_GIANT) are gone.
    const rowsAfter = solar.querySelectorAll('.mbody > .row').length;
    expect(rowsAfter).toBe(rowsBefore - 2);
    expect(solar.textContent).not.toContain('BARREN');
    expect(solar.textContent).not.toContain('GAS GIANT');
    // The STAR row is NOT a decorative body — the filter never touches it.
    expect(solar.textContent).toContain('K-CLASS ORANGE DWARF');

    await click(filterBtn);
    expect(filterBtn.getAttribute('aria-pressed')).toBe('false');
    expect(solar.querySelectorAll('.mbody > .row').length).toBe(rowsBefore);
    expect(solar.textContent).toContain('BARREN');
  });

  it('a long decorative-body list renders every row uncapped — the panel scrolls internally instead of truncating (Max ruling, T1-B)', async () => {
    const manyBodies = Array.from({ length: 40 }, (_, i) => ({
      slot: i, kind: i % 2 === 0 ? 'BARREN' : 'ICE', real: false,
    }));
    mockApiGet.mockResolvedValue({
      data: { star: CONTENTS_RESPONSE.star, bodies: manyBodies, stations: [] },
    });
    await mount();
    const solar = container.querySelector('.mon.system-monitor')!;
    const rows = solar.querySelectorAll('.mbody > .row');

    // 40 decorative-body rows + the STAR row, at minimum (plus SENSOR SWEEP
    // and this fixture's generic hazard row) — no slice/count cap anywhere.
    expect(rows.length).toBeGreaterThanOrEqual(41);
    expect(errorSpy).not.toHaveBeenCalled();
  });

  it('a failed /contents fetch silently degrades to zero STAR/barren rows — no console.error, no crash', async () => {
    mockApiGet.mockRejectedValue(new Error('network'));
    await mount();

    const solar = container.querySelector('.mon.system-monitor')!;
    expect(solar.textContent).not.toContain('primary');
    expect(errorSpy).not.toHaveBeenCalled();
  });

  it('generic hazard fallback row on SYSTEM for a STANDARD sector with hazard_level>0 — no numbers, no %-block', async () => {
    await mount();
    const solar = container.querySelector('.mon.system-monitor')!;

    expect(solar.textContent).toContain('HAZARD DETECTED');
    expect(solar.textContent).not.toContain('6/10');
    expect(solar.textContent).not.toContain('20.0%');
    expect(solar.querySelector('.hud-bar')).toBeNull();
  });

  it('named hazard row for a NEBULA-type sector, and NO row at all for a clean sector', async () => {
    gameState = makeGameState({ currentSector: SECTOR_NEBULA_TYPE });
    await mount();
    let solar = container.querySelector('.mon.system-monitor')!;
    expect(solar.textContent).toContain('NEBULA');
    expect(solar.textContent).toContain('affects sensors and combat');

    await act(async () => { root.unmount(); });
    container.remove();
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
    gameState = makeGameState({ currentSector: SECTOR_CLEAN });
    await mount();
    solar = container.querySelector('.mon.system-monitor')!;
    expect(solar.textContent).not.toContain('HAZARD DETECTED');
  });

  it('formation + wreck sensor rows are gated on scanActive — absent by default, appear once SCAN is toggled on', async () => {
    await mount();
    const solar = container.querySelector('.mon.system-monitor')!;

    expect(solar.textContent).not.toContain('WHISPER CLOUD');
    expect(solar.textContent).not.toContain('UNKNOWN ANOMALY');
    expect(solar.textContent).not.toContain('LIGHT FREIGHTER');

    const scanBtn = Array.from(solar.querySelectorAll('button.act')).find((b) => b.textContent?.includes('SCAN')) as HTMLButtonElement;
    await click(scanBtn);

    // Discovered formation: named + SURVEY action.
    expect(solar.textContent).toContain('WHISPER CLOUD');
    const surveyBtn = Array.from(solar.querySelectorAll('button')).find((b) => b.textContent?.includes('SURVEY')) as HTMLButtonElement;
    expect(surveyBtn).toBeTruthy();
    await click(surveyBtn);
    expect(mockApiPost).toHaveBeenCalledWith('/api/v1/player/formations/f-known/investigate');

    // Undiscovered formation: masked, no action button next to it.
    expect(solar.textContent).toContain('UNKNOWN ANOMALY');

    // Wreck: named + APPROACH jumps to the SALVAGE tab.
    expect(solar.textContent).toContain('LIGHT FREIGHTER');
    const approachBtn = Array.from(solar.querySelectorAll('button')).find((b) => b.textContent?.includes('APPROACH')) as HTMLButtonElement;
    expect(approachBtn).toBeTruthy();
    await click(approachBtn);
    const tabs = Array.from(solar.querySelectorAll('.deck-tab-btn'));
    const salvageTab = tabs.find((b) => b.textContent === 'SALVAGE');
    expect(salvageTab?.className).toContain('active');
  });
});

// Static CSS-source proof (T1-B): jsdom doesn't apply this component's
// imported stylesheet, and a real layout-driven scrollHeight>clientHeight
// assertion is unreachable in jsdom's stub layout engine (it reports 0 for
// both regardless of content/CSS) — mirrors WindshieldTableau.test.tsx's own
// `.ssv-popup-title` static-source precedent (readFileSync + a scoped
// rule-block regex, not a whole-file grep) for exactly this reason. Proves
// the rule exists, is scoped to `.system-monitor .mbody` (not global/
// document-level), and carries the Scroll-Law-exception marker a future
// audit needs to find before "fixing" it. The real geometry (panel actually
// scrolls, document does not) still owes a live/Playwright eyeball.
describe('solar-system-viewscreen.css — SOLAR SYSTEM Scroll-Law exception (T1-B)', () => {
  it('.system-monitor .mbody declares overflow-y:auto, scoped (not global), with the Scroll-Law-exception marker present', async () => {
    const fs = await import('node:fs');
    const path = await import('node:path');
    const cssPath = path.resolve(__dirname, '../../tactical/solar-system-viewscreen.css');
    const css = fs.readFileSync(cssPath, 'utf8');

    const match = css.match(/\.system-monitor \.mbody\s*\{([^}]*)\}/);
    expect(match).not.toBeNull();
    expect(match![1]).toMatch(/overflow-y\s*:\s*auto/);

    expect(css).toMatch(/SCROLL-LAW EXCEPTION/i);
    expect(css).toMatch(/Max ruling/);
  });
});
