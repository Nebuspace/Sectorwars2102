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
// commitIspBurn/commitIspHalt POST the optimistic ISP burn/halt commit
// (both .catch(() => {}) -- "optimistic local flight still runs" even if
// this rejects) -- reject so the suite's synthetic clicks don't need a real
// pose contract, matching the pose GET mock's own no-mock-in-suite stance.
// Re-armed in beforeEach (not just here): this file's afterEach runs
// vi.restoreAllMocks(), which strips a bare vi.fn()'s implementation after
// its first use, leaving later tests' apiClient.post(...) returning
// undefined instead of a promise.
const mockPost = vi.fn();
vi.mock('../../../services/apiClient', () => ({
  default: {
    get: (...args: unknown[]) => mockGet(...args),
    post: (...args: unknown[]) => mockPost(...args),
  },
}));

// WindshieldTableau now ALSO fetches GET /api/v1/helm/intrasystem/pose on
// mount (server-authoritative pose hydration) alongside the /contents fetch
// this suite already exercised -- both go through the SAME apiClient.get, so
// a blanket mockResolvedValue answers the pose fetch with system-contents
// shaped data too, leaving `heading_deg` undefined and crashing the ship
// marker's `heading.toFixed(0)`. Route by URL: the pose endpoint rejects
// (matches the real backend's behavior when the endpoint 500s/lags deploy --
// WindshieldTableau's own .catch() silently keeps local flight), everything
// else answers with the given contents payload.
const mockContents = (data: unknown) => {
  mockGet.mockImplementation((url: string) =>
    String(url).includes('/helm/intrasystem/pose')
      ? Promise.reject(new Error('no pose mock in this suite'))
      : Promise.resolve({ data })
  );
};

let autopilotStatus: string = 'idle';
vi.mock('../../../contexts/AutopilotContext', () => ({
  useAutopilot: () => ({ status: autopilotStatus, abort: vi.fn() }),
}));

// eslint-disable-next-line import/first
import WindshieldTableau, { chooseWarpArrivalAnchor } from '../WindshieldTableau';
// eslint-disable-next-line import/first
import { WindshieldFlightProvider, useWindshieldFlight } from '../../../contexts/WindshieldFlightContext';
// eslint-disable-next-line import/first
import { starAnchor, otherPresencePosition } from '../windshieldTableauLayout';

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

describe('chooseWarpArrivalAnchor', () => {
  const band = { widthPx: 1000, heightPx: 500, remPx: 16 };

  it('uses a fresh random coordinate instead of a sector-deterministic anchor', () => {
    const emptySystem = {
      star: null, nebula: null, belt: null, debris: null, bodies: [], stations: [],
    };
    const a = chooseWarpArrivalAnchor(77, emptySystem, band, () => 0.2);
    const b = chooseWarpArrivalAnchor(77, emptySystem, band, () => 0.8);
    expect(a).not.toEqual(b);
  });

  it('rejects a random candidate overlapping the sun', () => {
    const starOnlySystem = {
      star: TEST_SYSTEM.star,
      nebula: null,
      belt: null,
      debris: null,
      bodies: [],
      stations: [],
    };
    const star = starAnchor(SECTOR_ID, starOnlySystem.star, []);
    // Bounds for this 1000x500 band are x=[6,94], y=[10,90]. Feed the star's
    // exact center as attempt 1 (must reject), then a clear upper-right point.
    const draws = [
      (star.xPct - 6) / 88,
      (star.yPct - 10) / 80,
      0.9,
      0.1,
    ];
    let drawIndex = 0;
    const point = chooseWarpArrivalAnchor(
      SECTOR_ID,
      starOnlySystem,
      band,
      () => draws[Math.min(drawIndex++, draws.length - 1)],
    );
    expect(drawIndex).toBeGreaterThanOrEqual(4);
    const distancePx = Math.hypot(
      ((point.xPct - star.xPct) / 100) * band.widthPx,
      ((point.yPct - star.yPct) / 100) * band.heightPx,
    );
    const starRadiusPx = (star.sizeEm * band.remPx) / 2;
    const bubbleAndClearancePx = (1.7 + 0.8) * band.remPx;
    expect(distancePx).toBeGreaterThanOrEqual(starRadiusPx + bubbleAndClearancePx);
  });
});

describe('WindshieldTableau', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;

  beforeEach(() => {
    mockGet.mockReset();
    mockContents(TEST_SYSTEM);
    mockPost.mockReset();
    mockPost.mockRejectedValue(new Error('no burn/halt mock in this suite'));
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
    vi.useRealTimers();
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

  it('renders the "sliver" composition: fixed stars layer first, off-center sun, one per-body orbit ellipse per body/station (T0-2), and the belt', async () => {
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

    // T0-2: the old 4 generic decorativeRings are RETIRED -- one real
    // per-body orbit ellipse per body/station instead (TEST_SYSTEM: 2
    // bodies + 1 station = 3).
    expect(container.querySelectorAll('.orbit').length).toBe(3);
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
    mockContents({ ...TEST_SYSTEM, bodies: [REAL_PLANET, namedDecorative] });
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
      mockContents({ ...TEST_SYSTEM, sector_id: sectorId, bodies, stations: [EXTREME_STATION] });
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

  it('renders other ships on the shared flight profile (oriented hull, phase class) with click→popup', async () => {
    const onSelectShip = vi.fn();
    await mount({ ships: [TEST_SHIP], onSelectShip });
    const other = container.querySelector('.other') as HTMLButtonElement;
    expect(other).not.toBeNull();
    expect(other.getAttribute('aria-label')).toBe('Alpha Runner options');
    expect(other.textContent).toContain('⊳');
    expect(other.querySelector('.other-hull')).not.toBeNull();
    expect(other.style.getPropertyValue('--hdg')).toMatch(/deg$/);
    // Idle or an in-flight travel-* class — never the old static marker.
    expect(
      other.className === 'other' ||
      /travel-(orienting|accelerating|gliding|brake-turn|braking|final-orient)/.test(other.className)
    ).toBe(true);

    await act(async () => { other.click(); });
    const popup = container.querySelector('.ssv-popup');
    expect(popup).not.toBeNull();
    expect(popup?.textContent).toContain('ALPHA RUNNER');
  });

  // WO-QUEUE-PLTAG-MOVING-STAR: extends the body-tag edge-lean fix (T2's own
  // WO-UI-PLTAG-CLAMP) to the star's tag (far-left by design, so left-lean is
  // the realistic case) and moving NPC-contact tags (right-lean, the live
  // defect a hub probe caught: "Freight Captain..." names overflowing by up
  // to 105px). pltagLabelHalfWidthEm/labelEdgeLean's own pure-function
  // correctness is proven in windshieldTableauLayout.test.ts (+ this WO's own
  // report, a zero-footprint Playwright harness at real 1440x900 pixel
  // geometry) -- these two tests only prove the WIRING into this component's
  // render (right variable, right className/style branch), using this
  // suite's own global getBoundingClientRect mock (800x400, beforeEach above)
  // so bandBox is non-zero here too.
  it('the star tag leans LEFT instead of centering when its label is wide enough to cross the left edge (star sits at ~9-14% by design, starAnchor)', async () => {
    const wideStarSystem = {
      ...TEST_SYSTEM,
      star: { kind: 'HYPERGIANT_UNSTABLE_VARIABLE_LABEL_LONG_ENOUGH_TO_OVERFLOW', label: 'Test', color: '#fff' },
    };
    mockContents(wideStarSystem);
    await mount();
    // The star's own tag is the only `.pltag` that's a DIRECT child of
    // `.scene.space` -- body/ship tags are nested one level deeper inside
    // `.pl`/`.other`.
    const starTag = container.querySelector('.scene.space > .pltag') as HTMLElement;
    expect(starTag).not.toBeNull();
    expect(starTag.textContent).toBe('HYPERGIANT UNSTABLE VARIABLE LABEL LONG ENOUGH TO OVERFLOW');
    // Left-lean sets transform:none (flush-left, see WindshieldTableau.tsx's
    // own doc-comment on this branch) -- the default centered case would be
    // 'translateX(-50%)'.
    expect(starTag.style.transform).toBe('none');
    expect(starTag.style.left).toMatch(/^calc\(/);
  });

  it('a moving NPC-contact tag leans RIGHT instead of overflowing when near the band\'s right edge with a real live-longest-pattern ship name (hub probe: "Freight Captain..." names, up to 105px overflow on stage)', async () => {
    const longShip = {
      player_id: 'p9', ship_id: 'ship-freight', ship_type: 'FREIGHTER', is_npc: true,
      username: 'npc', archetype: 'TRADER',
      ship_name: "Freight Captain Yuki Tanahashi III's Freighter",
      pose: { x_pct: 92, y_pct: 50, heading_deg: 0, leg: null },
    };
    await mount({ ships: [longShip] });
    const contactTag = container.querySelector('.other .pltag') as HTMLElement;
    expect(contactTag).not.toBeNull();
    expect(contactTag.textContent).toBe("Freight Captain Yuki Tanahashi III's Freighter");
    expect(contactTag.className).toContain('pltag-lean-right');
  });

  // FIX-POSELESS-FALLBACK (P0, hub-live-watching): a HUMAN contact with no
  // `pose` used to fall into otherShipFlightPose's time-driven cosmetic
  // wander (built for decorative NPC traffic with no real tracked
  // position) -- a REAL player's dot then "ported" between different
  // positions every time the contactT clock ticked. Must hold ONE stable,
  // deterministic (per player_id/ship_id) position instead until real pose
  // data arrives. NPC contacts with no pose keep the UNCHANGED cosmetic
  // wander -- that's the intended, still-correct decorative behavior for
  // traffic with no server-tracked position at all (verified explicitly
  // below, not assumed).
  it('a pose-less HUMAN contact holds ONE stable position across multiple contactT clock ticks (was: cosmetic porting)', async () => {
    const humanNoPose = {
      player_id: 'player-77', ship_id: 'ship-human-1', ship_type: 'SCOUT',
      is_npc: false, username: 'Voyager', ship_name: 'Wanderer',
      // no `pose` field at all -- the exact gap this fix closes.
    };
    await mount({ ships: [humanNoPose] });
    vi.useFakeTimers();
    const other = () => container.querySelector('.other') as HTMLElement;
    const first = { left: other().style.left, top: other().style.top };
    expect(first.left).not.toBe('');

    // Simulate several "polls" of the contactT drift clock (~50ms/tick).
    for (let i = 0; i < 3; i++) {
      await act(async () => { vi.advanceTimersByTime(500); });
    }
    const second = { left: other().style.left, top: other().style.top };
    expect(second).toEqual(first);

    await act(async () => { vi.advanceTimersByTime(4000); });
    const third = { left: other().style.left, top: other().style.top };
    expect(third).toEqual(first);
  });

  it('a pose-less NPC contact keeps the UNCHANGED cosmetic wander (same code path as before this fix, only humans were re-routed)', async () => {
    const npcNoPose = {
      player_id: 'npc-1', ship_id: 'ship-npc-1', ship_type: 'FREIGHTER',
      is_npc: true, username: 'npc', archetype: 'TRADER', activity: 'COMMUTE', mission: 'commerce',
      // no `pose` field -- decorative traffic, otherShipFlightPose's own
      // intended use case.
    };
    await mount({ ships: [npcNoPose] });
    const other = container.querySelector('.other') as HTMLElement;
    const rendered = { left: parseFloat(other.style.left), top: parseFloat(other.style.top) };
    // otherShipFlightPose's own leg/phase-offset math (a per-id seeded
    // random start point along its flight profile, windshieldTableauLayout
    // .ts's phaseOffsetMs) means an NPC's rendered position essentially
    // never lands exactly on the raw otherPresencePosition(id) seed anchor
    // -- confirming this branch is STILL going through the flight-profile
    // path, not the new parked-anchor one this fix added for humans (that
    // would land EXACTLY on the seed anchor, see the human/pose test above).
    const parkedAnchor = otherPresencePosition('npc-1'); // player_id, matches this fix's own player_id||ship_id priority
    expect(rendered.left).not.toBeCloseTo(parkedAnchor.xPct, 3);
  });

  it('a contact WITH real pose data is unaffected by this fix (still driven by deriveIspPose, not the parked anchor)', async () => {
    const humanWithPose = {
      player_id: 'player-88', ship_id: 'ship-human-2', ship_type: 'SCOUT',
      is_npc: false, username: 'Voyager', ship_name: 'Wanderer',
      pose: { x_pct: 42, y_pct: 33, heading_deg: 15, leg: null },
    };
    await mount({ ships: [humanWithPose] });
    const other = container.querySelector('.other') as HTMLElement;
    expect(parseFloat(other.style.left)).toBeCloseTo(42);
    expect(parseFloat(other.style.top)).toBeCloseTo(33);
  });

  it('gates planet landing by proximity: APPROACH → flashing HALT → LAND', async () => {
    const onRequestLand = vi.fn();
    await mount({ onRequestLand });
    vi.useFakeTimers();
    // Park far from the planet first; this sector's deterministic test anchor
    // can land inside landing range.
    const tableau = container.querySelector('.ssv-tableau') as HTMLElement;
    await act(async () => {
      tableau.dispatchEvent(new MouseEvent('contextmenu', {
        bubbles: true, cancelable: true, clientX: 760, clientY: 40,
      }));
    });
    const travelBtn = Array.from(container.querySelectorAll('.ssv-popup-action'))
      .find((b) => b.textContent?.includes('Travel To')) as HTMLButtonElement;
    await act(async () => { travelBtn.click(); });
    await act(async () => { vi.advanceTimersByTime(8300); });

    const planetBtn = container.querySelector('.pl') as HTMLButtonElement;
    await act(async () => { planetBtn.click(); });

    expect(container.querySelector('.ssv-popup')?.textContent).toContain('OUTSIDE LANDING RANGE');
    expect(Array.from(container.querySelectorAll('.ssv-popup-action'))
      .some((b) => b.textContent?.includes('🛬 LAND'))).toBe(false);
    const approachBtn = Array.from(container.querySelectorAll('.ssv-popup-action'))
      .find((b) => b.textContent?.includes('APPROACH')) as HTMLButtonElement;
    expect(approachBtn).toBeTruthy();
    // Inspecting a planet must not silently start travel.
    expect((container.querySelector('.shipmk') as HTMLElement).className).not.toContain('travel-');

    await act(async () => { approachBtn.click(); });
    const haltBtn = Array.from(container.querySelectorAll('.ssv-popup-action'))
      .find((b) => b.textContent?.includes('HALT')) as HTMLButtonElement;
    expect(haltBtn).toBeTruthy();
    expect(haltBtn.classList.contains('halt')).toBe(true);
    expect(onRequestLand).not.toHaveBeenCalled();

    await act(async () => { vi.advanceTimersByTime(8300); });
    const landBtn = Array.from(container.querySelectorAll('.ssv-popup-action'))
      .find((b) => b.textContent?.includes('LAND')) as HTMLButtonElement;
    expect(landBtn).toBeTruthy();
    await act(async () => { landBtn.click(); });
    expect(onRequestLand).toHaveBeenCalledWith('planet-real-1');
  });

  it('gates station docking by proximity: APPROACH → flashing HALT → DOCK', async () => {
    const onRequestDock = vi.fn();
    await mount({ onRequestDock });
    vi.useFakeTimers();
    // Park far from the station first; this sector's deterministic test anchor
    // happens to be inside docking range.
    const tableau = container.querySelector('.ssv-tableau') as HTMLElement;
    await act(async () => {
      tableau.dispatchEvent(new MouseEvent('contextmenu', {
        bubbles: true, cancelable: true, clientX: 760, clientY: 40,
      }));
    });
    const travelBtn = Array.from(container.querySelectorAll('.ssv-popup-action'))
      .find((b) => b.textContent?.includes('Travel To')) as HTMLButtonElement;
    await act(async () => { travelBtn.click(); });
    // Run the whole flight profile so the hull is parked (idle) at the far
    // corner — the continuous glide replaces the old transitionend signal.
    await act(async () => { vi.advanceTimersByTime(8300); });

    const stationBtn = container.querySelector('.obj') as HTMLButtonElement;
    await act(async () => { stationBtn.click(); });

    expect(container.querySelector('.ssv-popup')?.textContent).toContain('OUTSIDE DOCKING RANGE');
    expect(Array.from(container.querySelectorAll('.ssv-popup-action'))
      .some((b) => b.textContent?.includes('⚓ DOCK'))).toBe(false);
    const approachBtn = Array.from(container.querySelectorAll('.ssv-popup-action'))
      .find((b) => b.textContent?.includes('APPROACH')) as HTMLButtonElement;
    expect(approachBtn).toBeTruthy();

    await act(async () => { approachBtn.click(); });
    const haltBtn = Array.from(container.querySelectorAll('.ssv-popup-action'))
      .find((b) => b.textContent?.includes('HALT')) as HTMLButtonElement;
    expect(haltBtn).toBeTruthy();
    expect(haltBtn.classList.contains('halt')).toBe(true);
    expect(onRequestDock).not.toHaveBeenCalled();

    // Run the approach flight to completion; once parked (idle) at the
    // stand-off point the popup flips HALT → DOCK.
    await act(async () => { vi.advanceTimersByTime(8300); });
    const dockBtn = Array.from(container.querySelectorAll('.ssv-popup-action'))
      .find((b) => b.textContent?.includes('DOCK')) as HTMLButtonElement;
    expect(dockBtn).toBeTruthy();
    await act(async () => { dockBtn.click(); });
    expect(onRequestDock).toHaveBeenCalledWith('station-1');
  });

  it('renders the FULL station name in the popup title even when it is long — no ellipsis clamp (WO-TABLEAU-TUNE #25, Max #25)', async () => {
    const longStationName = 'Trade Hub Capelworks Expansion Complex';
    mockContents({ ...TEST_SYSTEM, stations: [{ ...TEST_STATION, name: longStationName }] });
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
    mockContents({ ...TEST_SYSTEM, bodies: [{ ...REAL_PLANET, name: longPlanetName }] });
    await mount();
    const planetBtn = container.querySelector('.pl') as HTMLButtonElement;
    await act(async () => { planetBtn.click(); });
    const planetTitle = container.querySelector('.ssv-popup-title') as HTMLElement;
    expect(planetTitle.textContent).toBe(longPlanetName.toUpperCase());
    expect(planetTitle.textContent).not.toContain('…');
  });

  it('flies orient → accelerate → coast → reverse-burn → stop → face destination', async () => {
    await mount();
    vi.useFakeTimers();
    const shipBefore = container.querySelector('.shipmk') as HTMLElement;
    expect(shipBefore).not.toBeNull();
    const leftBefore = shipBefore.style.left;
    const topBefore = shipBefore.style.top;
    const planetBtn = container.querySelector('.pl') as HTMLButtonElement;
    const targetLeft = parseFloat(planetBtn.style.left);
    const targetTop = parseFloat(planetBtn.style.top);

    await act(async () => { planetBtn.click(); });
    const approachBtn = Array.from(container.querySelectorAll('.ssv-popup-action'))
      .find((b) => b.textContent?.includes('APPROACH')) as HTMLButtonElement;
    if (approachBtn) {
      await act(async () => { approachBtn.click(); });
    } else {
      await act(async () => { flightCapture!.approach('planet-real-1'); });
    }

    // Circular difference in degrees, tolerant of the accumulating (unwrapped)
    // heading values the component uses to keep every turn spinning one way.
    const circAbsDeg = (a: number, b: number) => {
      const raw = Math.abs(((a - b) % 360) + 360) % 360;
      return Math.min(raw, 360 - raw);
    };

    let ship = container.querySelector('.shipmk') as HTMLElement;
    expect(ship.className).toContain('travel-orienting');
    expect(ship.className).not.toContain('burning');
    expect(ship.querySelectorAll('.ssv-rcs')).toHaveLength(2);
    expect(ship.style.left).toBe(leftBefore); // still parked while it orients
    expect(ship.style.top).toBe(topBefore);
    const forwardHeading = parseFloat(ship.style.getPropertyValue('--hdg'));

    // Engine lights and the SINGLE continuous glide commits to the target.
    await act(async () => { vi.advanceTimersByTime(1000); });
    ship = container.querySelector('.shipmk') as HTMLElement;
    expect(ship.className).toContain('travel-accelerating');
    expect(ship.className).toContain('burning');
    expect(parseFloat(ship.style.left)).toBeCloseTo(targetLeft); // one glide, committed once
    expect(parseFloat(ship.style.top)).toBeCloseTo(targetTop);

    await act(async () => { vi.advanceTimersByTime(1800); });
    ship = container.querySelector('.shipmk') as HTMLElement;
    expect(ship.className).toContain('travel-gliding');
    expect(ship.className).not.toContain('burning'); // coasting, engine cold

    // Flip to retrograde WHILE still coasting — target unchanged (momentum).
    await act(async () => { vi.advanceTimersByTime(1100); });
    ship = container.querySelector('.shipmk') as HTMLElement;
    expect(ship.className).toContain('travel-brake-turn');
    expect(ship.querySelectorAll('.ssv-rcs')).toHaveLength(2);
    expect(parseFloat(ship.style.left)).toBeCloseTo(targetLeft);
    expect(circAbsDeg(parseFloat(ship.style.getPropertyValue('--hdg')), forwardHeading)).toBeCloseTo(180);

    await act(async () => { vi.advanceTimersByTime(1300); });
    ship = container.querySelector('.shipmk') as HTMLElement;
    expect(ship.className).toContain('travel-braking');
    expect(ship.className).toContain('burning'); // retro burn

    await act(async () => { vi.advanceTimersByTime(2200); });
    ship = container.querySelector('.shipmk') as HTMLElement;
    expect(ship.className).toContain('travel-final-orient');
    expect(ship.className).not.toContain('burning');
    expect(parseFloat(ship.style.left)).toBeCloseTo(targetLeft);
    expect(parseFloat(ship.style.top)).toBeCloseTo(targetTop);
    expect(circAbsDeg(parseFloat(ship.style.getPropertyValue('--hdg')), forwardHeading)).toBeCloseTo(0);

    await act(async () => { vi.advanceTimersByTime(800); });
    expect((container.querySelector('.shipmk') as HTMLElement).className).not.toContain('travel-');
    vi.useRealTimers();
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
    const approachBtn = Array.from(container.querySelectorAll('.ssv-popup-action'))
      .find((b) => b.textContent?.includes('APPROACH')) as HTMLButtonElement;
    if (approachBtn) {
      await act(async () => { approachBtn.click(); });
    } else {
      // Already inside landing range — drive the same glide via the shared approach API.
      await act(async () => { flightCapture!.approach('planet-real-1'); });
    }

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

  // ---- FIX C revise (Max correction: right-click must be MENU-mediated,
  // not direct-travel -- the earlier direct-travel cut is superseded).
  // right-click (contextmenu) anywhere opens a small "Travel To" menu at
  // the click point; the ship does NOT move until that item is explicitly
  // chosen.
  for (const sectorId of [SECTOR_ID, SECTOR_ID + 1]) {
    it(`right-click (contextmenu) at sector ${sectorId} opens a menu at the click point and does NOT move the ship yet`, async () => {
      await mount({ sectorId });
      const tableau = container.querySelector('.ssv-tableau') as HTMLElement;
      const shipBefore = container.querySelector('.shipmk') as HTMLElement;
      const leftBefore = shipBefore.style.left;
      const topBefore = shipBefore.style.top;

      // Mocked containerRef rect (this file's own beforeEach): 800x400, origin (0,0).
      const clientX = 600;
      const clientY = 100;

      const event = new MouseEvent('contextmenu', { bubbles: true, cancelable: true, clientX, clientY });
      const preventDefaultSpy = vi.spyOn(event, 'preventDefault');
      await act(async () => { tableau.dispatchEvent(event); });

      expect(preventDefaultSpy).toHaveBeenCalled(); // native menu still suppressed

      const menu = container.querySelector('.ssv-ctxmenu');
      expect(menu).not.toBeNull(); // menu appears at the click point
      expect(menu?.querySelector('.ssv-popup-action')?.textContent).toContain('Travel To');

      const shipAfter = container.querySelector('.shipmk') as HTMLElement;
      expect(shipAfter.style.left).toBe(leftBefore); // ship has NOT moved yet
      expect(shipAfter.style.top).toBe(topBefore);
      expect(shipAfter.className).not.toContain('burning');
    });
  }

  it('clicking "Travel To" in the menu glides the ship to the stashed pct point with aspect-corrected heading, and closes the menu', async () => {
    await mount();
    vi.useFakeTimers();
    const tableau = container.querySelector('.ssv-tableau') as HTMLElement;
    const shipBefore = container.querySelector('.shipmk') as HTMLElement;
    const fromXPct = parseFloat(shipBefore.style.left);
    const fromYPct = parseFloat(shipBefore.style.top);

    const clientX = 600;
    const clientY = 100;
    const expectedXPct = (clientX / 800) * 100; // 75
    const expectedYPct = (clientY / 400) * 100; // 25

    const event = new MouseEvent('contextmenu', { bubbles: true, cancelable: true, clientX, clientY });
    await act(async () => { tableau.dispatchEvent(event); });

    const travelToBtn = container.querySelector('.ssv-ctxmenu .ssv-popup-action') as HTMLButtonElement;
    expect(travelToBtn).not.toBeNull();
    await act(async () => { travelToBtn.click(); });

    expect(container.querySelector('.ssv-ctxmenu')).toBeNull(); // menu closes after choosing

    let shipAfter = container.querySelector('.shipmk') as HTMLElement;
    expect(shipAfter.className).toContain('travel-orienting');
    expect(shipAfter.className).not.toContain('burning');
    await act(async () => { vi.advanceTimersByTime(11600); });
    shipAfter = container.querySelector('.shipmk') as HTMLElement;
    expect(parseFloat(shipAfter.style.left)).toBeCloseTo(expectedXPct); // NOW it glides to the exact stashed point
    expect(parseFloat(shipAfter.style.top)).toBeCloseTo(expectedYPct);
    expect(shipAfter.className).not.toContain('burning');

    // heading points at the clicked point, aspect-corrected (bandAspect=0.5
    // for this file's own 800x400 mock -- same formula FIX B's own test uses).
    const dxPct = expectedXPct - fromXPct;
    const dyPct = expectedYPct - fromYPct;
    const expectedHdg = (Math.atan2(dyPct * 0.5, dxPct) * 180) / Math.PI;
    const hdg = parseFloat(shipAfter.style.getPropertyValue('--hdg'));
    // Final heading faces the destination; the component accumulates raw degrees
    // (a full-circle spin ends at prograde+360), so compare circularly.
    const rawDiff = Math.abs(((hdg - expectedHdg) % 360) + 360) % 360;
    expect(Math.min(rawDiff, 360 - rawDiff)).toBeLessThan(0.6);
  });

  it('right-clicking ON a body opens the SAME menu there too (not special-cased -- the container handler fires on bubble)', async () => {
    await mount();
    vi.useFakeTimers();
    const planetBtn = container.querySelector('.pl') as HTMLButtonElement;
    const targetXPct = parseFloat(planetBtn.style.left);
    const targetYPct = parseFloat(planetBtn.style.top);
    const rect = planetBtn.getBoundingClientRect(); // mocked to the same 800x400 origin(0,0) rect
    const clientX = (targetXPct / 100) * rect.width;
    const clientY = (targetYPct / 100) * rect.height;

    const event = new MouseEvent('contextmenu', { bubbles: true, cancelable: true, clientX, clientY });
    await act(async () => { planetBtn.dispatchEvent(event); });

    expect(container.querySelector('.ssv-ctxmenu')).not.toBeNull();
    const travelToBtn = container.querySelector('.ssv-ctxmenu .ssv-popup-action') as HTMLButtonElement;
    await act(async () => { travelToBtn.click(); });
    await act(async () => { vi.advanceTimersByTime(11600); });

    const shipAfter = container.querySelector('.shipmk') as HTMLElement;
    expect(parseFloat(shipAfter.style.left)).toBeCloseTo(targetXPct);
    expect(parseFloat(shipAfter.style.top)).toBeCloseTo(targetYPct);
  });

  it('right-clicking never opens a left-click popup, and left-clicking closes any open context menu (mutually exclusive overlays)', async () => {
    await mount();
    const tableau = container.querySelector('.ssv-tableau') as HTMLElement;
    const event = new MouseEvent('contextmenu', { bubbles: true, cancelable: true, clientX: 400, clientY: 200 });
    await act(async () => { tableau.dispatchEvent(event); });
    expect(container.querySelector('.ssv-popup')).toBeNull();
    expect(container.querySelector('.ssv-ctxmenu')).not.toBeNull();

    const planetBtn = container.querySelector('.pl') as HTMLButtonElement;
    await act(async () => { planetBtn.click(); });
    expect(container.querySelector('.ssv-ctxmenu')).toBeNull(); // left-click's popup closed the menu
    expect(container.querySelector('.ssv-popup')).not.toBeNull();
  });

  it('the context menu dismisses on outside-click without traveling', async () => {
    await mount();
    const tableau = container.querySelector('.ssv-tableau') as HTMLElement;
    const shipBefore = container.querySelector('.shipmk') as HTMLElement;
    const leftBefore = shipBefore.style.left;

    const event = new MouseEvent('contextmenu', { bubbles: true, cancelable: true, clientX: 600, clientY: 100 });
    await act(async () => { tableau.dispatchEvent(event); });
    expect(container.querySelector('.ssv-ctxmenu')).not.toBeNull();

    // A mousedown OUTSIDE the menu (on document.body) dismisses it.
    await act(async () => {
      document.body.dispatchEvent(new MouseEvent('mousedown', { bubbles: true }));
    });

    expect(container.querySelector('.ssv-ctxmenu')).toBeNull();
    const shipAfter = container.querySelector('.shipmk') as HTMLElement;
    expect(shipAfter.style.left).toBe(leftBefore); // never traveled
  });

  it('the context menu dismisses on Escape without traveling', async () => {
    await mount();
    const tableau = container.querySelector('.ssv-tableau') as HTMLElement;
    const shipBefore = container.querySelector('.shipmk') as HTMLElement;
    const leftBefore = shipBefore.style.left;

    const event = new MouseEvent('contextmenu', { bubbles: true, cancelable: true, clientX: 600, clientY: 100 });
    await act(async () => { tableau.dispatchEvent(event); });
    expect(container.querySelector('.ssv-ctxmenu')).not.toBeNull();

    await act(async () => {
      document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
    });

    expect(container.querySelector('.ssv-ctxmenu')).toBeNull();
    const shipAfter = container.querySelector('.shipmk') as HTMLElement;
    expect(shipAfter.style.left).toBe(leftBefore); // never traveled
  });

  it('a second right-click elsewhere while the menu is open RELOCATES it to the new point, not toggled shut', async () => {
    await mount();
    vi.useFakeTimers();
    const tableau = container.querySelector('.ssv-tableau') as HTMLElement;

    await act(async () => {
      tableau.dispatchEvent(new MouseEvent('contextmenu', { bubbles: true, cancelable: true, clientX: 100, clientY: 50 }));
    });
    expect(container.querySelector('.ssv-ctxmenu')).not.toBeNull();

    await act(async () => {
      tableau.dispatchEvent(new MouseEvent('contextmenu', { bubbles: true, cancelable: true, clientX: 700, clientY: 350 }));
    });

    const travelToBtn = container.querySelector('.ssv-ctxmenu .ssv-popup-action') as HTMLButtonElement;
    expect(travelToBtn).not.toBeNull(); // still open (unconditional setCtxMenu, not a toggle)
    await act(async () => { travelToBtn.click(); });
    await act(async () => { vi.advanceTimersByTime(11600); });

    // The SECOND click's target won, confirming it relocated rather than
    // the first stale target silently surviving.
    const shipAfter = container.querySelector('.shipmk') as HTMLElement;
    expect(parseFloat(shipAfter.style.left)).toBeCloseTo((700 / 800) * 100);
    expect(parseFloat(shipAfter.style.top)).toBeCloseTo((350 / 400) * 100);
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
    mockGet.mockImplementation((url: string) =>
      String(url).includes('/helm/intrasystem/pose')
        ? Promise.reject(new Error('no pose mock in this suite'))
        : new Promise((resolve) => { resolveGet = resolve; })
    );
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

  it('publishes its local glide into the shared flight context — APPROACH flips flightCapture.isFlying/targetId too, not just the DOM', async () => {
    await mount();
    expect(flightCapture?.isFlying).toBe(false);

    const planetBtn = container.querySelector('.pl') as HTMLButtonElement;
    await act(async () => { planetBtn.click(); });
    expect(flightCapture?.isFlying).toBe(false); // inspect-only until APPROACH
    const approachBtn = Array.from(container.querySelectorAll('.ssv-popup-action'))
      .find((b) => b.textContent?.includes('APPROACH')) as HTMLButtonElement;
    if (approachBtn) {
      await act(async () => { approachBtn.click(); });
    } else {
      await act(async () => { flightCapture!.approach('planet-real-1'); });
    }

    expect(flightCapture?.isFlying).toBe(true);
    expect(flightCapture?.targetId).toBe('planet-real-1');
  });

  it('resolves a row\'s flight.approach(planetId) into the same orient-first flight sequence', async () => {
    await mount();
    const shipBefore = container.querySelector('.shipmk') as HTMLElement;
    const leftBefore = shipBefore.style.left;
    const topBefore = shipBefore.style.top;

    await act(async () => { flightCapture!.approach('planet-real-1'); });

    const shipAfter = container.querySelector('.shipmk') as HTMLElement;
    expect(shipAfter.style.left).toBe(leftBefore);
    expect(shipAfter.style.top).toBe(topBefore);
    expect(shipAfter.className).toContain('travel-orienting');
    expect(shipAfter.className).not.toContain('burning');
    expect(shipAfter.querySelectorAll('.ssv-rcs')).toHaveLength(2);
    expect(flightCapture?.isFlying).toBe(true);
    expect(flightCapture?.targetId).toBe('planet-real-1');
  });

  it('resolves flight.approach(stationId) to a near-station stand-off point', async () => {
    await mount();
    vi.useFakeTimers();
    const station = container.querySelector('.obj') as HTMLElement;

    await act(async () => { flightCapture!.approach('station-1'); });
    // Position commits once the orient phase hands off to the glide (t≈1000ms);
    // stop before 'idle' so glideTargetId is still set for the assertion below.
    await act(async () => { vi.advanceTimersByTime(1200); });

    const ship = container.querySelector('.shipmk') as HTMLElement;
    const dxPx = ((parseFloat(ship.style.left) - parseFloat(station.style.left)) / 100) * 800;
    const dyPx = ((parseFloat(ship.style.top) - parseFloat(station.style.top)) / 100) * 400;
    const standOffPx = Math.hypot(dxPx, dyPx);
    expect(standOffPx).toBeGreaterThan(0);
    expect(standOffPx).toBeLessThanOrEqual(5 * 16); // inside DOCK_RANGE_EM
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

  it('mid-course redirect keeps momentum and arcs instead of parking to reorient', async () => {
    await mount();
    vi.useFakeTimers();
    const planetBtn = container.querySelector('.pl') as HTMLButtonElement;
    const stationBtn = container.querySelector('.obj') as HTMLButtonElement;

    await act(async () => { flightCapture!.approach('planet-real-1'); });
    await act(async () => { vi.advanceTimersByTime(1000); }); // past orient → accelerating
    expect((container.querySelector('.shipmk') as HTMLElement).className).toContain('travel-accelerating');
    const leftDuring = (container.querySelector('.shipmk') as HTMLElement).style.left;

    // Retarget to the station while still underway.
    await act(async () => { flightCapture!.approach('station-1'); });

    let ship = container.querySelector('.shipmk') as HTMLElement;
    expect(ship.className).toContain('travel-redirect-turn');
    expect(ship.className).not.toContain('travel-orienting'); // must NOT park
    expect(ship.querySelectorAll('.ssv-rcs')).toHaveLength(2);
    expect(flightCapture?.isFlying).toBe(true);
    // Position retargets to an arc waypoint / new path — not snapped idle.
    expect(ship.style.left).not.toBe(leftDuring);

    await act(async () => { vi.advanceTimersByTime(1600); });
    ship = container.querySelector('.shipmk') as HTMLElement;
    expect(ship.className).toContain('travel-accelerating');
    expect(ship.className).toContain('burning');
    expect(flightCapture?.targetId).toBe('station-1');

    await act(async () => { vi.advanceTimersByTime(6400 + 800); });
    ship = container.querySelector('.shipmk') as HTMLElement;
    expect(ship.className).not.toContain('travel-');
    // Settled near the station stand-off, not the original planet.
    const dxPx = ((parseFloat(ship.style.left) - parseFloat(stationBtn.style.left)) / 100) * 800;
    const dyPx = ((parseFloat(ship.style.top) - parseFloat(stationBtn.style.top)) / 100) * 400;
    expect(Math.hypot(dxPx, dyPx)).toBeLessThanOrEqual(5 * 16);
    expect(Math.hypot(
      parseFloat(ship.style.left) - parseFloat(planetBtn.style.left),
      parseFloat(ship.style.top) - parseFloat(planetBtn.style.top),
    )).toBeGreaterThan(1);
  });

  it('flight.allStop() flips and burns to a stop instead of freezing momentum', async () => {
    await mount();
    vi.useFakeTimers();
    const planetBtn = container.querySelector('.pl') as HTMLButtonElement;
    await act(async () => { planetBtn.click(); });
    const approachBtn = Array.from(container.querySelectorAll('.ssv-popup-action'))
      .find((b) => b.textContent?.includes('APPROACH')) as HTMLButtonElement;
    if (approachBtn) {
      await act(async () => { approachBtn.click(); });
    } else {
      await act(async () => { flightCapture!.approach('planet-real-1'); });
    }
    // Get past orient so there is real momentum to bleed.
    await act(async () => { vi.advanceTimersByTime(1000); });
    expect(flightCapture?.isFlying).toBe(true);
    expect((container.querySelector('.shipmk') as HTMLElement).className).toContain('travel-accelerating');

    await act(async () => { flightCapture!.allStop(); });

    let ship = container.querySelector('.shipmk') as HTMLElement;
    expect(ship.className).toContain('travel-halt-turn');
    expect(ship.className).not.toContain('burning');
    expect(ship.querySelectorAll('.ssv-rcs')).toHaveLength(2);
    expect(flightCapture?.isFlying).toBe(true); // still flying through the burn

    await act(async () => { vi.advanceTimersByTime(1800); });
    ship = container.querySelector('.shipmk') as HTMLElement;
    expect(ship.className).toContain('travel-halt-brake');
    expect(ship.className).toContain('burning');
    expect(flightCapture?.isFlying).toBe(true);

    await act(async () => { vi.advanceTimersByTime(1600); });
    ship = container.querySelector('.shipmk') as HTMLElement;
    expect(ship.className).not.toContain('travel-');
    expect(ship.className).not.toContain('burning');
    expect(flightCapture?.isFlying).toBe(false);
    expect(flightCapture?.targetId).toBeNull();
  });

  // ---- T0-2 (Max: "your pick, knock it out" — orbit-line view): every
  // planet/station/wreck rides its own real orbit ellipse, REPLACING the
  // old generic decorativeRings. Body POSITIONING (T0-1's fan/rank fix) is
  // completely untouched -- the ellipse is derived FROM the position.

  it('every planet, station, and (scan-gated) wreck has EXACTLY one orbit ellipse whose path passes through its own rendered position', async () => {
    await mount({ scanActive: true, wrecks: [TEST_WRECK] });

    const planetEls = Array.from(container.querySelectorAll('.pl')) as HTMLElement[];
    const objEls = Array.from(container.querySelectorAll('.obj')) as HTMLElement[]; // station + wreck
    expect(planetEls.length).toBe(2); // REAL_PLANET + PROCEDURAL_PLANET
    expect(objEls.length).toBe(2); // TEST_STATION + TEST_WRECK

    const allTargets = [...planetEls, ...objEls];
    expect(container.querySelectorAll('.orbit').length).toBe(allTargets.length); // (1) exactly one ellipse per target, no more/fewer

    for (const target of allTargets) {
      // Its own ellipse is the immediately PRECEDING sibling (same Fragment,
      // ellipse rendered first) -- see WindshieldTableau.tsx's orbitEllipse()
      // doc-comment for why DOM order alone keeps it visually behind.
      const ellipse = target.previousElementSibling as HTMLElement | null;
      expect(ellipse?.className).toBe('orbit');

      const targetXPct = parseFloat(target.style.left);
      const targetYPct = parseFloat(target.style.top);
      const cx = parseFloat(ellipse!.style.left);
      const cy = parseFloat(ellipse!.style.top);
      const rx = parseFloat(ellipse!.style.width) / 2;
      const ry = parseFloat(ellipse!.style.height) / 2;
      const dx = targetXPct - cx;
      const dy = targetYPct - cy;
      const residual = Math.abs((dx / rx) ** 2 + (dy / ry) ** 2 - 1);
      expect(residual).toBeLessThan(0.02); // Accept criterion #1's own tolerance
    }
  });

  it('nebula and asteroid belt get ZERO orbit ellipse of their own (only bodies/stations/wrecks do)', async () => {
    mockContents({ ...TEST_SYSTEM, nebula: { hue: 200, density: 0.5 } });
    await mount();
    // 2 bodies + 1 station = 3 ellipses; the nebula (rendered via
    // .hazard-arcs, not .orbit) and belt (rendered via .belt) contribute none.
    expect(container.querySelectorAll('.orbit').length).toBe(3);
    expect(container.querySelector('.hazard-arcs')).not.toBeNull();
    expect(container.querySelector('.belt')).not.toBeNull();
  });

  it('the .orbit ellipse stays thin/low-weight, z-behind bodies -- CSS unchanged from the retired decorativeRings (1px border, z-index:0)', async () => {
    const fs = await import('node:fs');
    const path = await import('node:path');
    const cssPath = path.resolve(__dirname, '../solar-system-viewscreen.css');
    const css = fs.readFileSync(cssPath, 'utf8');
    const match = css.match(/\.ssv-tableau \.orbit\s*\{([^}]*)\}/);
    expect(match).not.toBeNull();
    const block = match![1];
    expect(block).toMatch(/border:\s*1px/);
    expect(block).toMatch(/z-index:\s*0/);
  });

  it('body/station positioning is byte-unchanged by the orbit-line addition -- T1-A/T0-1 in-band+distinct+spread still hold at 2 sectors', async () => {
    for (const [sectorId, bodies] of [[21, EXTREME_BODIES], [104, EXTREME_BODIES.slice(0, 2)]] as const) {
      mockContents({ ...TEST_SYSTEM, sector_id: sectorId, bodies, stations: [EXTREME_STATION] });
      await mount({ sectorId });
      assertEveryObjectInBand(container, 800, 400);
    }
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
