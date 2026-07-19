// @vitest-environment jsdom
/**
 * GameDashboard — WO-UI5-RETIREMENT+GLASS (glass-lane, WO-UI0 part-4),
 * further simplified WO-UI-MAX-BATCH-1 items 1-3 (Max #10/#13/#16).
 *
 * Proves the glass changes on the flight-scene windshield:
 *   1. `.locrow` top-left chips are down to a single region-flavor chip +
 *      conditional ALL STOP — the sector-name chip, the HAZARD chip, and
 *      the NEBULA chip are all gone (sector number/type + the region's full
 *      name already live in the status bar's LocationDropdown, "canon";
 *      HAZARD's OWN open-state was a second, redundant trigger for the
 *      exact same HazardAnalysisCard Annunciator.tsx's HAZARD segment
 *      already opens — that segment is untouched and still owns it).
 *   2. The old hazard/radiation/formations HudChip glass idiom is gone —
 *      the annunciator strip + the new locrow own the glass now (the
 *      landed/docked HudChips — owner/habitability/baystatus — are
 *      untouched, out of this WO's scope, and not exercised by this file's
 *      flight-only fixtures).
 *   3. The SCAN toggle moved off the glass into the SOLAR SYSTEM monitor's
 *      SYSTEM page as a `.act` button, wired to the SAME `scanActive` flag
 *      SolarSystemViewscreen now receives as a controlled prop (real
 *      wreck/formation gating logic covered separately in
 *      SolarSystemViewscreen.livingWindshield.test.tsx — this file proves
 *      the WIRING, via a prop-capturing SSV stub).
 *
 * Mirrors GameDashboard.overlapChipsRetired.test.tsx's proven seam exactly
 * (GameDashboard is the SUT, not mocked; GameLayout and every heavy venue/
 * canvas child are stubbed as irrelevant chrome for this DOM-shape
 * assertion), extended with a prop-capturing SolarSystemViewscreen stub
 * (instead of an inert `<div/>`) so the scanActive wiring is provable, and a
 * mutable autopilotState (instead of a fixed literal) so the ALL STOP
 * chip's in-transit condition can vary per test.
 *
 * WO-HUD-SHIPTYPE (sector-move, Max ruled 2026-07-19) AMENDMENT: the sector
 * NUMBER moved back down here from the status bar's LocationDropdown
 * trigger ("not in both, entirely down") -- a SECOND, UNCONDITIONAL `.loc`
 * chip ("Sector N", via sector_number-or-sector_id) now sits beside the
 * region chip. This partially reverses item 1's "single region-flavor
 * chip" framing above (that text is left as historical record of WHY the
 * sector-name/HAZARD/NEBULA chips are still gone -- only the sector-NUMBER
 * chip is back, not those). The two tests below that pinned "region chip
 * only" are updated accordingly; every other test in this file is
 * unaffected (none of them assert on `.locrow .loc` chip counts/text).
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
// Prop-capturing stub (NOT an inert <div/>) -- exposes the scanActive
// controlled prop as a data attribute so this file can prove GameDashboard
// forwards its lifted state correctly, without needing SSV's real canvas
// draw loop (that's SolarSystemViewscreen.livingWindshield.test.tsx's job).
// Kept even though this file's flight-only fixtures never mount it (still
// owns the 'landed' scene) -- harmless, and future-proof if a fixture adds
// a landed case.
vi.mock('../../tactical/SolarSystemViewscreen', () => ({
  default: (props: { scanActive?: boolean }) => (
    <div data-testid="ssv-stub" data-scan-active={String(!!props.scanActive)} />
  ),
}));
// WO-UI2-WINDSHIELD-TABLEAU: the flight-mode mount is now WindshieldTableau,
// not SolarSystemViewscreen -- same prop-capturing idiom, same scanActive
// controlled prop, so the SCAN wiring assertion below still proves the
// SAME shared state (not a second independent flag).
vi.mock('../../tactical/WindshieldTableau', () => ({
  default: (props: { scanActive?: boolean }) => (
    <div data-testid="windshield-tableau-stub" data-scan-active={String(!!props.scanActive)} />
  ),
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

const SECTOR_HAZARD: any = {
  id: 100, sector_id: 100, sector_number: 100, name: 'Sol', type: 'STANDARD',
  // region_name is the dev-seeded label ("Stage2 Genesis R4 — Terran
  // Space" in real payloads, "The Frontier" here) -- WO-T1D-LANEB: the
  // locrow chip no longer renders it. region_type is the real RegionType
  // enum value (models/region.py) that now drives the chip via
  // formatRegionType (utils/formatters.ts).
  region_id: 'region-1', region_name: 'The Frontier', region_type: 'terran_space',
  hazard_level: 6, radiation_level: 0.2,
  resources: {}, players_present: [], special_features: [],
  special_formations: [{ id: 'f1', is_discovered: true, is_anchor: true, name: 'Whisper Cloud', type: 'NEBULA_CLUSTER' }],
  description: 'A quiet stretch of charted space.',
};

const SECTOR_NEBULA: any = {
  ...SECTOR_HAZARD,
  id: 101, sector_id: 101, name: 'Veil', type: 'NEBULA', hazard_level: 0,
  // region_type: null is what actually suppresses the chip now (the
  // no-region case, e.g. a player between regions); region_name: null
  // kept too since it's realistic for the same no-region state, but the
  // chip's own gate is region_type only.
  region_name: null, region_type: null, special_formations: [],
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
    currentSector: SECTOR_HAZARD,
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

describe('GameDashboard — locrow + HudChip retirement + SCAN relocation (WO-UI5-RETIREMENT+GLASS)', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;
  let errorSpy: ReturnType<typeof vi.spyOn>;

  beforeEach(() => {
    mockSectorWrecks.mockReset();
    mockSectorWrecks.mockResolvedValue([{
      id: 'wreck-1', original_owner_id: null, original_owner_name: null,
      destroyed_ship_type: 'FREIGHTER', cause: 'combat', created_at: '2026-01-01T00:00:00Z',
      age_seconds: 10, cargo: {}, would_flag_suspect: false,
    }]);
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

  const click = async (el: Element) => {
    await act(async () => {
      el.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    });
    await flush();
  };

  it('renders the .locrow region-flavor chip + sector-number chip — no sector-NAME/HAZARD/NEBULA chips', async () => {
    await mount();

    const locrow = container.querySelector('.locrow');
    expect(locrow).toBeTruthy();
    const chips = Array.from(locrow!.querySelectorAll('.loc'));
    const chipText = chips.map((c) => c.textContent);

    // Two surviving informational chips: the region TYPE display-name
    // (WO-T1D-LANEB, "terran_space" -> "Terran Space", never the
    // dev-seeded region_name "The Frontier"), and -- WO-HUD-SHIPTYPE
    // sector-move -- the sector NUMBER ("Sector 100", SECTOR_HAZARD's
    // sector_number), region chip first per Max's ruled order.
    expect(chipText).toEqual(['Terran Space', 'Sector 100']);
    expect(chipText).not.toContain('The Frontier');

    // The sector-NAME chip ("Sol") is still gone -- only the sector NUMBER
    // came back, not a name/type chip.
    expect(chipText).not.toContain('Sol');
    // HAZARD is gone entirely -- Annunciator's own HAZARD segment is the
    // one surviving trigger for HazardAnalysisCard (untouched, out of this
    // file's flight-only fixtures).
    expect(chipText.some((t) => t?.startsWith('HAZARD'))).toBe(false);
    expect(container.querySelector('.locrow button')).toBeNull(); // no HAZARD/ALL STOP button while idle
    // NEBULA + ALL STOP are conditional on nebula type / in-transit -- this
    // fixture is neither, so both are absent.
    expect(chipText).not.toContain('NEBULA');
    expect(chipText.some((t) => t?.includes('ALL STOP'))).toBe(false);

    expect(errorSpy).not.toHaveBeenCalled();
  });

  it('locrow has ONLY the sector-number chip for a NEBULA-type sector with no region (the sector chip is unconditional; region_type gates the region chip alone)', async () => {
    gameState = makeGameState({ currentSector: SECTOR_NEBULA });
    await mount();

    const locrow = container.querySelector('.locrow')!;
    const chipText = Array.from(locrow.querySelectorAll('.loc')).map((c) => c.textContent);
    // region_type is null on this fixture (WO-T1D-LANEB: that's the region
    // chip's gate), so the region chip is absent -- but WO-HUD-SHIPTYPE's
    // sector-number chip has no such gate. SECTOR_NEBULA spreads
    // ...SECTOR_HAZARD and only overrides sector_id (101) -- sector_number
    // is inherited unchanged (100), and the chip reads sector_number first
    // (`?? sector_id`), so it renders "Sector 100" here, not 101.
    expect(chipText).toEqual(['Sector 100']);
    expect(chipText).not.toContain('Veil');
    expect(chipText).not.toContain('NEBULA');
    expect(chipText).not.toContain('The Frontier');
    expect(chipText).not.toContain('Terran Space');
    expect(errorSpy).not.toHaveBeenCalled();
  });

  it('locrow ALL STOP chip renders only while autopilot is engaged, and aborts on click', async () => {
    autopilotState.status = 'engaged';
    await mount();

    const locrow = container.querySelector('.locrow')!;
    const allStop = Array.from(locrow.querySelectorAll('.loc')).find((c) => c.textContent?.includes('ALL STOP')) as HTMLButtonElement;
    expect(allStop).toBeTruthy();
    expect(allStop.tagName).toBe('BUTTON');
    // Pixel a11y gate: visible "🛑 ALL STOP" + title alone aren't enough for
    // a screen reader (title is unreliable) -- aria-label must convey the
    // action.
    expect(allStop.getAttribute('aria-label')).toBe('Abort autopilot and hold position');

    await click(allStop);
    expect(autopilotState.abort).toHaveBeenCalledWith('all stop');
  });

  it('the SOLAR SYSTEM monitor HAZARD tab carries the numeric detail the locrow HAZARD chip used to gate — no floating dialog needed', async () => {
    await mount();

    // No locrow HAZARD button exists to click anymore.
    expect(Array.from(container.querySelectorAll('.locrow .loc')).some((c) => c.textContent?.startsWith('HAZARD'))).toBe(false);

    const solar = container.querySelector('.mon.system-monitor')!;
    const hazardTab = Array.from(solar.querySelectorAll('.deck-tab-btn')).find((b) => b.textContent === 'HAZARD') as HTMLButtonElement;
    expect(hazardTab).toBeTruthy();
    await click(hazardTab);

    expect(solar.textContent).toContain('HAZARD ANALYSIS');
    expect(solar.textContent).toContain('6/10'); // hazard_level
    expect(solar.textContent).toContain('20.0%'); // radiation_level
    // Choosing HAZARD swaps out the sensor list — the SYSTEM page's own
    // content (e.g. its SENSOR SWEEP toggle) is no longer in the DOM.
    expect(solar.querySelector('.system-scan-row')).toBeNull();
  });

  it('the old hazard/radiation/formations HudChip glass idiom is gone', async () => {
    await mount();

    // This fixture has hazard_level>0, radiation_level>0, and a discovered
    // formation -- every retired HudChip's render condition is satisfied,
    // so their absence here is a real proof, not a vacuous one.
    expect(container.querySelector('.hud-overlay.hazard')).toBeNull();
    expect(container.querySelector('.hud-overlay.radiation')).toBeNull();
    expect(container.querySelector('.hud-overlay.formations')).toBeNull();
    // `data-hud-chip` is the marker unique to a real <HudChip> component
    // instance (GameDashboard.tsx's HudChip impl) -- unlike a bare
    // `.hud-overlay` class match, this can't false-positive on the
    // pre-existing, already-dormant (display:none) features/description
    // divs, which carry the class but were never HudChip usages. Flight
    // scene never mounts the landed/docked HudChips (owner/habitability/
    // baystatus) either, so this is zero HudChip instances anywhere on the
    // glass in this mode.
    expect(container.querySelectorAll('[data-hud-chip]').length).toBe(0);
  });

  it('SCAN lives on the SOLAR SYSTEM monitor SYSTEM page, not the glass, and toggles the SAME scanActive prop SSV receives', async () => {
    await mount();

    // Not on the glass.
    const windshield = container.querySelector('.cockpit-windshield')!;
    const glassScanBtn = Array.from(windshield.querySelectorAll('button')).find((b) => b.textContent?.includes('SCAN'));
    expect(glassScanBtn).toBeUndefined();

    // The windshield tableau starts with scanActive=false.
    const ssv = container.querySelector('[data-testid="windshield-tableau-stub"]')!;
    expect(ssv.getAttribute('data-scan-active')).toBe('false');

    // The SOLAR SYSTEM monitor's SYSTEM page owns the button as a `.act`.
    const solar = container.querySelector('.mon.system-monitor')!;
    const scanBtn = Array.from(solar.querySelectorAll('button.act')).find((b) => b.textContent?.includes('SCAN')) as HTMLButtonElement;
    expect(scanBtn).toBeTruthy();
    expect(scanBtn.getAttribute('aria-pressed')).toBe('false');
    // Pixel a11y gate: visible "📡 SCAN" + aria-pressed alone aren't enough
    // for a screen reader to announce WHAT is toggled -- aria-label must
    // convey both state and action (title alone is unreliable).
    expect(scanBtn.getAttribute('aria-label')).toBe('Sensor scan off — wrecks and formations hidden');

    await click(scanBtn);

    // Toggling the monitor's button flips the SAME controlled prop SSV
    // receives -- the shared state, not a second independent flag.
    expect(ssv.getAttribute('data-scan-active')).toBe('true');
    expect(scanBtn.getAttribute('aria-pressed')).toBe('true');
    expect(scanBtn.getAttribute('aria-label')).toBe('Sensor scan active — showing wrecks and formations');
    // Count appended once active: 1 wreck (mockSectorWrecks) + 1 formation
    // (SECTOR_HAZARD.special_formations) = 2.
    expect(scanBtn.textContent).toContain('SCAN — 2');
  });
});
