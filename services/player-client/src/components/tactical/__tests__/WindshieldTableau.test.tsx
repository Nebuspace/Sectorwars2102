// @vitest-environment jsdom
/**
 * WindshieldTableau — WO-UI2-WINDSHIELD-TABLEAU proof.
 *
 * A REAL DOM component (unlike SolarSystemViewscreen's canvas), so this
 * suite asserts real geometry/structure directly — no reference-coordinate
 * indirection needed. Covers: the static "sliver" composition (off-center
 * sun, decorative rings, belt, fixed stars layer), every demo-idiom object
 * kind rendering from the /contents fetch, the ship marker's click-to-travel
 * glide + heading, moon child-orbit attach points, SCAN gating, and the
 * fetch-failure fallback (never a blank windshield).
 *
 * WO-UI2-FLIGHT-FEEL: this component now consumes the shared
 * WindshieldFlightContext (publishing its own glide state, resolving a
 * row's approach() request, freezing on allStop()) — every mount() below is
 * wrapped in a REAL WindshieldFlightProvider (its only real dependency,
 * useAutopilot, stays the SAME lightweight module mock already established
 * here).
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import type { SectorWreck } from '../../../services/api';
import type { SpecialFormationSummary } from '../../../contexts/GameContext';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const mockGet = vi.fn();
vi.mock('../../../services/apiClient', () => ({
  default: { get: (...args: unknown[]) => mockGet(...args) },
}));

let autopilotStatus: string = 'idle';
vi.mock('../../../contexts/AutopilotContext', () => ({
  useAutopilot: () => ({ status: autopilotStatus, abort: vi.fn() }),
}));

// eslint-disable-next-line import/first
import WindshieldTableau from '../WindshieldTableau';
// eslint-disable-next-line import/first
import { WindshieldFlightProvider, useWindshieldFlight } from '../../../contexts/WindshieldFlightContext';

const SECTOR_ID = 77;

const REAL_PLANET = {
  slot: 0, orbit_au: 0.4, kind: 'TERRAN', size_class: 3,
  palette: { hue: 120, sat: 40 }, rings: false, moons: 2, phase_deg: 45,
  real: true, planet_id: 'planet-real-1', name: 'New Terra', habitability: 62, owned: false,
};

const PROCEDURAL_PLANET = {
  slot: 1, orbit_au: 0.75, kind: 'GAS_GIANT', size_class: 5,
  palette: { hue: 30, sat: 50 }, rings: true, moons: 0, phase_deg: 200,
  real: false,
};

const TEST_STATION = { station_id: 'station-1', name: 'Ring Alpha', type: 'trading_post', orbit_au: 0.55, phase_deg: 120 };

const TEST_SYSTEM = {
  sector_id: SECTOR_ID, sector_type: 'normal',
  star: { kind: 'G_YELLOW', label: 'Sol Prime', color: '#ffdd88' },
  nebula: null, belt: { inner_au: 0.6, outer_au: 0.9 }, debris: null, habitable_zone: null,
  bodies: [REAL_PLANET, PROCEDURAL_PLANET], stations: [TEST_STATION],
};

const TEST_WRECK: SectorWreck = {
  id: 'wreck-1', original_owner_id: null, original_owner_name: null,
  destroyed_ship_type: 'FREIGHTER', cause: 'combat', created_at: '2026-01-01T00:00:00Z',
  age_seconds: 10, cargo: {}, would_flag_suspect: false,
};

const TEST_FORMATION: SpecialFormationSummary = {
  id: 'formation-1', is_discovered: true, is_anchor: false, name: 'Test Anomaly', type: 'NEBULA_CLUSTER',
};

const TEST_SHIP = { player_id: 'p2', ship_id: 'ship-alpha', ship_name: 'Alpha Runner', ship_type: 'SCOUT', is_npc: false, username: 'Rook' };

const flush = () => new Promise((resolve) => setTimeout(resolve, 0));

describe('WindshieldTableau', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;

  beforeEach(() => {
    mockGet.mockReset();
    mockGet.mockResolvedValue({ data: TEST_SYSTEM });
    autopilotStatus = 'idle';
    vi.spyOn(Element.prototype, 'getBoundingClientRect').mockReturnValue({
      width: 800, height: 400, top: 0, left: 0, right: 800, bottom: 400, x: 0, y: 0,
      toJSON() { return {}; },
    } as DOMRect);
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(async () => {
    await act(async () => { root.unmount(); });
    container.remove();
    vi.restoreAllMocks();
  });

  // Captures the shared flight context alongside the real WindshieldTableau
  // mount, so a test can drive approach()/allStop() the same way a SOLAR row
  // or the locrow would, and read isFlying/targetId back out.
  let flightCapture: ReturnType<typeof useWindshieldFlight> | null = null;
  function FlightCapture() {
    flightCapture = useWindshieldFlight();
    return null;
  }

  const mount = async (props: Record<string, unknown> = {}) => {
    flightCapture = null;
    await act(async () => {
      root.render(
        <WindshieldFlightProvider>
          <FlightCapture />
          <WindshieldTableau sectorId={SECTOR_ID} {...props} />
        </WindshieldFlightProvider>
      );
    });
    await flush();
    await flush();
  };

  it('fetches GET /api/v1/sectors/{id}/contents on mount (WO-UI2-INTRASYSTEM-MODEL adoption)', async () => {
    await mount();
    expect(mockGet).toHaveBeenCalledWith(`/api/v1/sectors/${SECTOR_ID}/contents`);
  });

  it('renders the "sliver" composition: fixed stars layer first, off-center sun, 4 decorative orbit rings, and the belt', async () => {
    await mount();
    const scene = container.querySelector('.scene.space')!;
    expect(scene).not.toBeNull();
    expect(scene.firstElementChild?.className).toBe('stars');

    const sun = container.querySelector('.sun') as HTMLElement;
    expect(sun).not.toBeNull();
    const sunLeft = parseFloat(sun.style.left);
    const sunTop = parseFloat(sun.style.top);
    // Off-center-left "sliver" anchor, not a centered orrery.
    expect(sunLeft).toBeGreaterThan(0);
    expect(sunLeft).toBeLessThan(20);
    expect(sunTop).toBeGreaterThan(30);
    expect(sunTop).toBeLessThan(60);

    expect(container.querySelectorAll('.orbit').length).toBe(4);
    expect(container.querySelector('.belt')).not.toBeNull();
  });

  it('places bodies/stations at a STATIC position derived from orbit_au+phase_deg — zero system-level animation at rest (no transition/animation on .pl/.obj)', async () => {
    await mount();
    const planetBtn = container.querySelector('.pl') as HTMLElement;
    expect(planetBtn).not.toBeNull();
    const left1 = planetBtn.style.left;
    const top1 = planetBtn.style.top;
    // Re-render with the same data — the layout fn is a pure function of
    // (sectorId, orbit_au, phase_deg), no wall-clock term, so it's identical.
    await mount();
    const planetBtn2 = container.querySelector('.pl') as HTMLElement;
    expect(planetBtn2.style.left).toBe(left1);
    expect(planetBtn2.style.top).toBe(top1);
  });

  it('renders every demo-idiom object kind: real planet (.pl + .pltag), procedural planet, station (.obj + glyphbox), and (SCAN-gated) wreck + discovered/undiscovered formation', async () => {
    await mount({ scanActive: true, wrecks: [TEST_WRECK], formations: [TEST_FORMATION] });

    const planets = container.querySelectorAll('.pl');
    expect(planets.length).toBe(2); // real + procedural
    const realTag = Array.from(container.querySelectorAll('.pltag')).find((el) => el.textContent === 'New Terra');
    expect(realTag).toBeTruthy();

    const station = container.querySelector('.obj') as HTMLElement;
    expect(station).not.toBeNull();
    expect(station.querySelector('.glyphbox')?.textContent).toBe('🛰');
    expect(station.querySelector('.objtag')?.textContent).toBe('Ring Alpha');

    // Discovered formation -> .obj derelict-beacon idiom.
    const objTags = Array.from(container.querySelectorAll('.objtag')).map((e) => e.textContent);
    expect(objTags).toContain('WRECK — SALVAGE');
    expect(objTags).toContain('TEST ANOMALY');
  });

  // ---- FIX A (Max live-playtest): decorative bodies show their REAL corpus
  // name (celestial_service.py's own name_for_body -- serialized on EVERY
  // body slot, real or decorative), not a fabricated `PROCEDURAL-N-idx`
  // designation that discarded it.
  it('a decorative (non-real) body with a server-provided name shows that REAL name -- never a fabricated PROCEDURAL-<sector>-<idx> designation', async () => {
    const namedDecorative = { ...PROCEDURAL_PLANET, name: 'Kelvara Drift' };
    mockGet.mockResolvedValue({ data: { ...TEST_SYSTEM, bodies: [REAL_PLANET, namedDecorative] } });
    await mount();

    const tags = Array.from(container.querySelectorAll('.pltag')).map((el) => el.textContent);
    expect(tags).toContain('Kelvara Drift');
    expect(tags.some((t) => t?.startsWith('PROCEDURAL-'))).toBe(false);

    const planetBtn = Array.from(container.querySelectorAll('.pl')).find(
      (el) => el.getAttribute('aria-label') === 'Kelvara Drift'
    );
    expect(planetBtn).toBeTruthy();

    // The popup designation matches too -- clicking opens a card titled with
    // the real name, not the fabricated one ('procedural' kind renders its
    // designation verbatim, no .toUpperCase() -- unlike the 'planet'/'star'
    // cases, see renderPopupContent's own switch).
    await act(async () => { (planetBtn as HTMLButtonElement).click(); });
    const title = container.querySelector('.ssv-popup-title');
    expect(title?.textContent).toBe('Kelvara Drift');
  });

  it('SCAN-gates wrecks and undiscovered formations behind scanActive', async () => {
    const undiscovered: SpecialFormationSummary = { ...TEST_FORMATION, id: 'f2', is_discovered: false, name: null, type: null };
    await mount({ scanActive: false, wrecks: [TEST_WRECK], formations: [undiscovered] });
    expect(container.querySelector('[aria-label^="Wreckage"]')).toBeNull();
    expect(container.querySelector('[aria-label="Unresolved signal"]')).toBeNull();

    await mount({ scanActive: true, wrecks: [TEST_WRECK], formations: [undiscovered] });
    expect(container.querySelector('[aria-label^="Wreckage"]')).not.toBeNull();
    const anom = container.querySelector('[aria-label="Unresolved signal"]');
    expect(anom).not.toBeNull();
    expect(anom?.className).toContain('anom');
  });

  it('renders a moon child-orbit layer for a body with moons>0, and none for moons=0 (Max refinement 5a)', async () => {
    await mount();
    const [realPlanetBtn, proceduralPlanetBtn] = Array.from(container.querySelectorAll('.pl'));
    expect(realPlanetBtn.querySelectorAll('.moon-orbit').length).toBe(2);
    expect(realPlanetBtn.querySelectorAll('.moon-dot').length).toBe(2);
    expect(proceduralPlanetBtn.querySelectorAll('.moon-orbit').length).toBe(0);
  });

  it('a planet\'s moon family co-rotates in the real DOM (every .moon-orbit under one planet shares the same .ccw state) and each dot carries its own varied inline size (WO-TABLEAU-TUNE #17)', async () => {
    await mount();
    const realPlanetBtn = container.querySelector('.pl') as HTMLElement;
    const orbitEls = Array.from(realPlanetBtn.querySelectorAll('.moon-orbit'));
    expect(orbitEls.length).toBe(2);
    const ccwStates = new Set(orbitEls.map((el) => el.classList.contains('ccw')));
    expect(ccwStates.size).toBe(1); // whole family: one shared direction

    const dotEls = Array.from(realPlanetBtn.querySelectorAll('.moon-dot')) as HTMLElement[];
    const widths = dotEls.map((el) => parseFloat(el.style.width));
    for (const w of widths) {
      expect(w).toBeGreaterThan(0);
      expect(w).toBeLessThanOrEqual(0.32); // MOON_DOT_MAX_EM
    }
    // Same orbital radius separation guarantee, read straight off the DOM.
    const lefts = dotEls.map((el) => parseFloat(el.style.left)).sort((a, b) => a - b);
    expect(lefts[1] - lefts[0]).toBeGreaterThan(0.32); // > MOON_DOT_MAX_EM
  });

  // ---- T1-A (Max live-playtest): a body clipping off the band's bottom
  // edge, "PROCEDURAL-21-6" hugging the top edge — bodies/stations must stay
  // fully inside the band's [0,100]%x[0,100]% rect. windshieldTableauLayout
  // .test.ts already exhaustively sweeps the pure math; this proves the
  // WIRING — that the real component actually measures its container and
  // threads safeRadii through bodyPosition/stationPosition on every render
  // path, not just that the library function exists unused. Bodies below
  // are deliberately picked at orbit_au near ORBIT_AU_MAX and phase_deg
  // values that overflowed hugely under the pre-T1-A symmetric math (see
  // that test file's own header for the measured -66%..+158% range).
  const EXTREME_BODIES = [
    { slot: 0, orbit_au: 0.95, kind: 'TERRAN', size_class: 10, palette: { hue: 0, sat: 0 }, rings: false, moons: 0, phase_deg: 250, real: true, planet_id: 'edge-1', name: 'Edge One' },
    { slot: 1, orbit_au: 0.95, kind: 'GAS_GIANT', size_class: 10, palette: { hue: 0, sat: 0 }, rings: false, moons: 0, phase_deg: 90, real: false }, // straight "below" the star -- old ry ceiling was 114
    { slot: 2, orbit_au: 0.9, kind: 'ICE', size_class: 10, palette: { hue: 0, sat: 0 }, rings: false, moons: 0, phase_deg: 20, real: true, planet_id: 'edge-3', name: 'Edge Three' },
  ];
  const EXTREME_STATION = { station_id: 'edge-station', name: 'Edge Station', type: 'trading_post', orbit_au: 0.95, phase_deg: 270 }; // straight "above" the star

  function assertEveryObjectInBand(el: HTMLElement, widthPx: number, heightPx: number) {
    // jsdom applies no real CSS cascade under vitest (css:false, see
    // vitest.config.ts) so getComputedStyle(...).fontSize resolves empty ->
    // WindshieldTableau.tsx's own DEFAULT_REM_PX fallback (16) is what's
    // actually in play here, mirrored below. Two separate footprint
    // ceilings, mirroring WindshieldTableau.tsx's own PLANET_FOOTPRINT_EM_MAX
    // / STATION_FOOTPRINT_EM_WIDTH/HEIGHT_MAX split -- a `.obj` station's
    // own name label is a normal-flow flex child (unlike `.pl`'s
    // position:absolute `.pltag`), so it needs a much wider margin (see
    // that constant's own doc-comment for the live-measured derivation).
    const REM_PX = 16;
    const check = (selector: string, emWidth: number, emHeight: number) => {
      const halfXPct = ((emWidth / 2) * REM_PX / widthPx) * 100;
      const halfYPct = ((emHeight / 2) * REM_PX / heightPx) * 100;
      const objects = Array.from(el.querySelectorAll(selector)) as HTMLElement[];
      for (const obj of objects) {
        const left = parseFloat(obj.style.left);
        const top = parseFloat(obj.style.top);
        expect(left - halfXPct).toBeGreaterThanOrEqual(-0.01);
        expect(left + halfXPct).toBeLessThanOrEqual(100.01);
        expect(top - halfYPct).toBeGreaterThanOrEqual(-0.01);
        expect(top + halfYPct).toBeLessThanOrEqual(100.01);
      }
      return objects.length;
    };
    const count = check('.pl', 2.6, 2.6) + check('.obj', 20, 5);
    expect(count).toBeGreaterThan(0);
  }

  it('keeps every body AND station fully inside the measured band rect at 3 seeded sectors with varied body counts, even at orbit_au near the ceiling (T1-A)', async () => {
    for (const [sectorId, bodies] of [
      [21, EXTREME_BODIES],           // the live symptom sector, all 3 extreme bodies
      [104, [EXTREME_BODIES[0]]],     // a different sector, single body
      [205, EXTREME_BODIES.slice(0, 2)], // a third sector, 2 bodies
    ] as const) {
      mockGet.mockResolvedValue({
        data: { ...TEST_SYSTEM, sector_id: sectorId, bodies, stations: [EXTREME_STATION] },
      });
      await mount({ sectorId });
      assertEveryObjectInBand(container, 800, 400); // mocked containerRef rect, this file's own beforeEach
    }
  });

  it('.pl is centered on its own %-anchor via transform:translate(-50%,-50%) — matching .sun/.obj/.other, the demo (RATIFIED.html L1222), and the T1-A fix\'s own margin math', async () => {
    await mount();
    const planetBtn = container.querySelector('.pl') as HTMLElement;
    expect(planetBtn.style.transform).toBe('translate(-50%,-50%)');
  });

  it('the star renders unmistakably larger than every planet — at least 3x the largest planet\'s diameter (WO-TABLEAU-TUNE #18)', async () => {
    await mount();
    const sun = container.querySelector('.sun') as HTMLElement;
    const sunSizeEm = parseFloat(sun.style.width);
    const planetSizesEm = Array.from(container.querySelectorAll('.pl')).map(
      (el) => parseFloat((el as HTMLElement).style.width)
    );
    const largestPlanetEm = Math.max(...planetSizesEm);
    expect(sunSizeEm).toBeGreaterThanOrEqual(largestPlanetEm * 3);
  });

  it('renders other ships as static presence markers (⊳, faction-colored) with aria-label, and clicking opens a popup', async () => {
    const onSelectShip = vi.fn();
    await mount({ ships: [TEST_SHIP], onSelectShip });
    const other = container.querySelector('.other') as HTMLButtonElement;
    expect(other).not.toBeNull();
    expect(other.getAttribute('aria-label')).toBe('Alpha Runner options');
    expect(other.textContent).toContain('⊳');

    await act(async () => { other.click(); });
    const popup = container.querySelector('.ssv-popup');
    expect(popup).not.toBeNull();
    expect(popup?.textContent).toContain('ALPHA RUNNER');
  });

  it('clicking the real planet opens a popup with a LAND action wired to onRequestLand (the demo click→inspect idiom, not the retired orbital-closeup zoom)', async () => {
    const onRequestLand = vi.fn();
    await mount({ onRequestLand });
    const planetBtn = container.querySelector('.pl') as HTMLButtonElement;
    await act(async () => { planetBtn.click(); });

    const landBtn = Array.from(container.querySelectorAll('.ssv-popup-action')).find((b) => b.textContent?.includes('LAND')) as HTMLButtonElement;
    expect(landBtn).toBeTruthy();
    await act(async () => { landBtn.click(); });
    expect(onRequestLand).toHaveBeenCalledWith('planet-real-1');
  });

  it('clicking a station opens a popup with a DOCK action wired to onRequestDock', async () => {
    const onRequestDock = vi.fn();
    await mount({ onRequestDock });
    const stationBtn = container.querySelector('.obj') as HTMLButtonElement;
    await act(async () => { stationBtn.click(); });
    const dockBtn = Array.from(container.querySelectorAll('.ssv-popup-action')).find((b) => b.textContent?.includes('DOCK')) as HTMLButtonElement;
    expect(dockBtn).toBeTruthy();
    await act(async () => { dockBtn.click(); });
    expect(onRequestDock).toHaveBeenCalledWith('station-1');
  });

  it('renders the FULL station name in the popup title even when it is long — no ellipsis clamp (WO-TABLEAU-TUNE #25, Max #25)', async () => {
    const longStationName = 'Trade Hub Capelworks Expansion Complex';
    mockGet.mockResolvedValue({ data: { ...TEST_SYSTEM, stations: [{ ...TEST_STATION, name: longStationName }] } });
    await mount();
    const stationBtn = container.querySelector('.obj') as HTMLButtonElement;
    await act(async () => { stationBtn.click(); });
    const title = container.querySelector('.ssv-popup-title') as HTMLElement;
    expect(title).not.toBeNull();
    expect(title.textContent).toBe(longStationName.toUpperCase());
    expect(title.textContent).not.toContain('…');
    expect(title.textContent?.endsWith('...')).toBe(false);
  });

  it('renders the FULL real-planet name in the popup title even when it is long — no ellipsis clamp (WO-TABLEAU-TUNE #25, Max #25)', async () => {
    const longPlanetName = 'Frostholm Deep Colony Reclamation Site';
    mockGet.mockResolvedValue({ data: { ...TEST_SYSTEM, bodies: [{ ...REAL_PLANET, name: longPlanetName }] } });
    await mount();
    const planetBtn = container.querySelector('.pl') as HTMLButtonElement;
    await act(async () => { planetBtn.click(); });
    const planetTitle = container.querySelector('.ssv-popup-title') as HTMLElement;
    expect(planetTitle.textContent).toBe(longPlanetName.toUpperCase());
    expect(planetTitle.textContent).not.toContain('…');
  });

  it('the ship marker is the ONLY system-level mover: clicking an object glides it there (left/top change, --hdg set) and it briefly burns', async () => {
    await mount();
    const shipBefore = container.querySelector('.shipmk') as HTMLElement;
    expect(shipBefore).not.toBeNull();
    const leftBefore = shipBefore.style.left;
    const topBefore = shipBefore.style.top;

    const planetBtn = container.querySelector('.pl') as HTMLButtonElement;
    await act(async () => { planetBtn.click(); });

    const shipAfter = container.querySelector('.shipmk') as HTMLElement;
    expect(shipAfter.style.left).not.toBe(leftBefore);
    expect(shipAfter.style.top).not.toBe(topBefore);
    expect(shipAfter.className).toContain('burning');
    expect(shipAfter.style.getPropertyValue('--hdg')).toMatch(/deg$/);
  });

  // ---- FIX B (Max live-playtest): ship heading is aspect-corrected to the
  // REAL measured band px dims (this file's own mocked containerRef rect,
  // 800x400 -> bandAspect=0.5), not the raw %-space angle.
  it('ship heading is aspect-corrected to the measured band px dims, not the raw %-space angle', async () => {
    await mount();
    const shipBefore = container.querySelector('.shipmk') as HTMLElement;
    const fromXPct = parseFloat(shipBefore.style.left);
    const fromYPct = parseFloat(shipBefore.style.top);

    const planetBtn = container.querySelector('.pl') as HTMLButtonElement;
    const toXPct = parseFloat(planetBtn.style.left);
    const toYPct = parseFloat(planetBtn.style.top);

    await act(async () => { planetBtn.click(); });

    const shipAfter = container.querySelector('.shipmk') as HTMLElement;
    const hdg = parseFloat(shipAfter.style.getPropertyValue('--hdg'));

    // Mocked containerRef rect in this file's own beforeEach: 800x400 ->
    // bandAspect = heightPx/widthPx = 0.5.
    const dxPct = toXPct - fromXPct;
    const dyPct = toYPct - fromYPct;
    const expectedCorrected = (Math.atan2(dyPct * 0.5, dxPct) * 180) / Math.PI;
    const expectedUncorrected = (Math.atan2(dyPct, dxPct) * 180) / Math.PI;

    // --hdg is toFixed(0) in the component -- tolerate the resulting <=0.5deg rounding.
    expect(Math.abs(hdg - expectedCorrected)).toBeLessThan(0.6);
    // Only meaningfully differ from the uncorrected angle when neither delta
    // is exactly 0 (axis-aligned moves are aspect-independent by construction,
    // per headingDeg's own pure-math tests) -- guards against a vacuous pass.
    if (dxPct !== 0 && dyPct !== 0) {
      expect(Math.abs(hdg - expectedUncorrected)).toBeGreaterThan(0.5);
    }
  });

  it('.shipmk burns while autopilot is engaged (inter-sector transit), independent of any local click', async () => {
    autopilotStatus = 'engaged';
    await mount();
    const ship = container.querySelector('.shipmk') as HTMLElement;
    expect(ship.className).toContain('burning');
  });

  it('seeds the ship at the last-docked station\'s position on a fresh mount (Max refinement 5b: undock emerges at the host)', async () => {
    await mount({ lastDockedStationId: 'station-1' });
    const ship = container.querySelector('.shipmk') as HTMLElement;
    const station = container.querySelector('.obj') as HTMLElement;
    expect(ship.style.left).toBe(station.style.left);
    expect(ship.style.top).toBe(station.style.top);
  });

  it('never goes dark on a fetch failure — renders the static scene chrome + an acquisition-failed message', async () => {
    mockGet.mockReset();
    mockGet.mockRejectedValue(new Error('network down'));
    const errSpy = vi.spyOn(console, 'error').mockImplementation(() => {});
    await mount();
    expect(container.querySelector('.scene.space')).not.toBeNull();
    expect(container.textContent).toContain('SCAN ACQUISITION FAILED');
    errSpy.mockRestore();
  });

  it('does not render an independent ship marker without a resolved system fetch mid-flight (docked-clamp precondition lives at the GameDashboard mount boundary, proven separately)', async () => {
    // WindshieldTableau itself is only ever mounted in the !docked && !landed
    // branch (GameDashboard.tsx) -- the "no independent ship marker while
    // docked" guarantee is structural (this component simply isn't mounted),
    // proven in GameDashboard.dockedStationFace.test.tsx. This test instead
    // proves the LOCAL precondition: no ship glyph renders before the
    // /contents fetch resolves (avoids a flash-of-wrong-position anchor).
    let resolveGet: (v: unknown) => void = () => {};
    mockGet.mockReset();
    mockGet.mockReturnValue(new Promise((resolve) => { resolveGet = resolve; }));
    await act(async () => {
      root.render(
        <WindshieldFlightProvider>
          <WindshieldTableau sectorId={SECTOR_ID} />
        </WindshieldFlightProvider>
      );
    });
    await flush();
    expect(container.querySelector('.shipmk')).toBeNull();
    await act(async () => { resolveGet({ data: TEST_SYSTEM }); });
    await flush();
    await flush();
    expect(container.querySelector('.shipmk')).not.toBeNull();
  });

  // ---- WO-UI2-FLIGHT-FEEL: shared flight-context wiring ------------------

  it('publishes its local glide into the shared flight context — a band click flips flightCapture.isFlying/targetId too, not just the DOM', async () => {
    await mount();
    expect(flightCapture?.isFlying).toBe(false);

    const planetBtn = container.querySelector('.pl') as HTMLButtonElement;
    await act(async () => { planetBtn.click(); });

    expect(flightCapture?.isFlying).toBe(true);
    expect(flightCapture?.targetId).toBe('planet-real-1');
  });

  it('resolves a row\'s flight.approach(planetId) request into the SAME glide a band click performs (position + burning)', async () => {
    await mount();
    const shipBefore = container.querySelector('.shipmk') as HTMLElement;
    const leftBefore = shipBefore.style.left;
    const topBefore = shipBefore.style.top;

    await act(async () => { flightCapture!.approach('planet-real-1'); });

    const shipAfter = container.querySelector('.shipmk') as HTMLElement;
    expect(shipAfter.style.left).not.toBe(leftBefore);
    expect(shipAfter.style.top).not.toBe(topBefore);
    expect(shipAfter.className).toContain('burning');
    expect(flightCapture?.isFlying).toBe(true);
    expect(flightCapture?.targetId).toBe('planet-real-1');
  });

  it('resolves flight.approach(stationId) against stations the same way', async () => {
    await mount();
    const station = container.querySelector('.obj') as HTMLElement;

    await act(async () => { flightCapture!.approach('station-1'); });

    const ship = container.querySelector('.shipmk') as HTMLElement;
    expect(ship.style.left).toBe(station.style.left);
    expect(ship.style.top).toBe(station.style.top);
    expect(flightCapture?.targetId).toBe('station-1');
  });

  it('flight.approach() with an unresolvable id is a no-op — no crash, ship stays put', async () => {
    await mount();
    const shipBefore = container.querySelector('.shipmk') as HTMLElement;
    const leftBefore = shipBefore.style.left;
    const topBefore = shipBefore.style.top;

    await act(async () => { flightCapture!.approach('does-not-exist'); });

    const shipAfter = container.querySelector('.shipmk') as HTMLElement;
    expect(shipAfter.style.left).toBe(leftBefore);
    expect(shipAfter.style.top).toBe(topBefore);
    expect(flightCapture?.isFlying).toBe(false);
  });

  it('flight.allStop() freezes an in-progress glide — clears burning, and isFlying drops back to false', async () => {
    await mount();
    const planetBtn = container.querySelector('.pl') as HTMLButtonElement;
    await act(async () => { planetBtn.click(); });
    expect(flightCapture?.isFlying).toBe(true);

    await act(async () => { flightCapture!.allStop(); });

    const ship = container.querySelector('.shipmk') as HTMLElement;
    expect(ship.className).not.toContain('burning');
    expect(flightCapture?.isFlying).toBe(false);
    expect(flightCapture?.targetId).toBeNull();
  });
});

// ---- WO-TABLEAU-TUNE (Max #25): source-level guard against the ellipsis
// clamp regressing. The DOM textContent assertions above prove there is no
// JS-level string truncation, but they can't see a CSS text-overflow clamp
// (jsdom doesn't apply the imported stylesheet's computed style) — so this
// reads the real stylesheet text and asserts the SPECIFIC `.ssv-popup-title`
// rule block no longer declares white-space:nowrap/overflow:hidden/
// text-overflow:ellipsis together (the combination that silently cut real
// station/planet names like "TRADE HUB CAPELWORKS" off mid-word in the
// fixed-232px popup card). Scoped to just that block (not a whole-file grep)
// so it can't false-fail on `.ssv-tableau .pltag`'s legitimate, demo-verbatim
// `white-space: nowrap` (that class has no width clamp, so nowrap there
// never truncates).
describe('solar-system-viewscreen.css — .ssv-popup-title has no ellipsis clamp', () => {
  it('the .ssv-popup-title rule block does not declare text-overflow:ellipsis', async () => {
    const fs = await import('node:fs');
    const path = await import('node:path');
    const cssPath = path.resolve(__dirname, '../solar-system-viewscreen.css');
    const css = fs.readFileSync(cssPath, 'utf8');
    const match = css.match(/\.ssv-popup-title\s*\{([^}]*)\}/);
    expect(match).not.toBeNull();
    const block = match![1];
    expect(block).not.toMatch(/text-overflow\s*:\s*ellipsis/);
  });
});
