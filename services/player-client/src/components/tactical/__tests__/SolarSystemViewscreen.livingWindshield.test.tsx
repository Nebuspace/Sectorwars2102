// @vitest-environment jsdom
/**
 * SolarSystemViewscreen — WO-UI2-LIVING-WINDSHIELD interaction-logic proof.
 *
 * Canvas PIXELS are invisible to jsdom (no real 2D rendering backend) — this
 * suite proves LOGIC only: hitTargets/HitMeta correctness (including the new
 * wreck/formation/empty kinds), the empty-payload forEach fix, the SCAN
 * toggle's gating, and the click / right-click / Escape wiring end-to-end
 * through a REAL mount. The hazard-ARC geometry, glyph placement, reticle,
 * and menu positioning are canvas pixels the Orchestrator's browser-MCP lane
 * proves — NOT claimed here.
 *
 * Two layers:
 *   (a) drawScene() called directly (exported, pure) with a no-op canvas
 *       ctx — hitTargets is a real JS array asserted on with zero DOM.
 *   (b) A REAL mount (createRoot+act, mirrors VistaErrorBoundary.test.tsx /
 *       Annunciator.test.tsx's stubbing conventions) proving the click →
 *       onSelectShip / right-click → context-menu / Escape-close wiring.
 *       Reference coordinates for (b)'s synthetic clicks come from calling
 *       the SAME exported drawScene() once with identical inputs (same w/h/
 *       sectorId/t=0 — reducedMotion pins t at 0 in the live component too),
 *       so a click lands exactly where the mounted component's own draw
 *       pass put the glyph, with zero hand-derived seeded-math duplication.
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { drawScene } from '../SolarSystemViewscreen';
import type { HitTarget } from '../SolarSystemViewscreen';
import type { SectorWreck } from '../../../services/api';
import type { SpecialFormationSummary } from '../../../contexts/GameContext';

// Silences the React 18 "current testing environment is not configured to
// support act(...)" warning — baseline-wide harness quirk in this repo's
// jsdom+createRoot+act tests (mirrors Annunciator.test.tsx / StatusBar.smoke
// .test.tsx).
(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

// ---------------------------------------------------------------------------
// apiClient mock — the component's only network dependency (system snapshot
// fetch + a rename POST unused by this suite).
// ---------------------------------------------------------------------------

const mockGet = vi.fn();
const mockPost = vi.fn();
vi.mock('../../../services/apiClient', () => ({
  default: {
    get: (...args: unknown[]) => mockGet(...args),
    post: (...args: unknown[]) => mockPost(...args),
  },
}));

// eslint-disable-next-line import/first
import SolarSystemViewscreen from '../SolarSystemViewscreen';

// ---------------------------------------------------------------------------
// No-op CanvasRenderingContext2D — every draw call is a black hole; the few
// properties read as VALUES (measureText / createRadialGradient / create
// LinearGradient) get real-shaped stand-ins so nothing downstream chokes.
// ---------------------------------------------------------------------------

function makeNoopCtx(): CanvasRenderingContext2D {
  const store: Record<string, unknown> = {};
  return new Proxy(store, {
    get(target, prop) {
      if (prop === 'measureText') return () => ({ width: 10 });
      if (prop === 'createRadialGradient' || prop === 'createLinearGradient') {
        return () => ({ addColorStop: () => {} });
      }
      if (prop in target) return target[prop as string];
      return () => {};
    },
    set(target, prop, value) {
      target[prop as string] = value;
      return true;
    }
  }) as unknown as CanvasRenderingContext2D;
}

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

const SECTOR_ID = 42;
const W = 800;
const H = 400;

const TEST_SYSTEM = {
  sector_id: SECTOR_ID,
  sector_type: 'normal',
  star: { kind: 'G_YELLOW', label: 'Test Star', color: '#ffdd88' },
  nebula: null,
  belt: null,
  bodies: [],
  stations: []
};

const TEST_SHIP = { ship_id: 'ship-alpha', ship_name: 'Alpha Runner', ship_type: 'SCOUT', is_npc: false };

const TEST_WRECK: SectorWreck = {
  id: 'wreck-1',
  original_owner_id: null,
  original_owner_name: null,
  destroyed_ship_type: 'FREIGHTER',
  cause: 'combat',
  created_at: '2026-01-01T00:00:00Z',
  age_seconds: 10,
  cargo: {},
  would_flag_suspect: false
};

const TEST_FORMATION: SpecialFormationSummary = {
  id: 'formation-1',
  is_discovered: true,
  is_anchor: false,
  name: 'Test Anomaly',
  type: 'NEBULA_CLUSTER'
};

const flush = () => new Promise((resolve) => setTimeout(resolve, 0));

// ---------------------------------------------------------------------------
// (a) drawScene — direct, pure-function tests
// ---------------------------------------------------------------------------

describe('drawScene — empty-payload guard + SCAN layer (pure function)', () => {
  it('does not throw when system.bodies/stations are missing (malformed/empty payload)', () => {
    const broken = {
      sector_id: 1, sector_type: 'normal', star: null, nebula: null, belt: null,
      bodies: undefined, stations: undefined
    } as any; // eslint-disable-line @typescript-eslint/no-explicit-any
    const hitTargets: HitTarget[] = [];
    const ctx = makeNoopCtx();
    expect(() => drawScene(ctx, W, H, 1, broken, 0, hitTargets, null, 0, 0)).not.toThrow();
    expect(hitTargets).toEqual([]);
  });

  it('still renders the star hit target when bodies/stations are missing', () => {
    const broken = {
      sector_id: 1, sector_type: 'normal',
      star: { kind: 'G_YELLOW', label: 'Sun', color: '#ffdd88' },
      nebula: null, belt: null, bodies: undefined, stations: undefined
    } as any; // eslint-disable-line @typescript-eslint/no-explicit-any
    const hitTargets: HitTarget[] = [];
    const ctx = makeNoopCtx();
    expect(() => drawScene(ctx, W, H, 1, broken, 0, hitTargets, null, 0, 0)).not.toThrow();
    expect(hitTargets).toHaveLength(1);
    expect(hitTargets[0].kind).toBe('star');
  });

  it('gates wreck/formation hit targets behind scanActive', () => {
    const ctx = makeNoopCtx();
    let hitTargets: HitTarget[] = [];
    drawScene(
      ctx, W, H, SECTOR_ID, TEST_SYSTEM as any, 0, hitTargets, null, 0, 0, // eslint-disable-line @typescript-eslint/no-explicit-any
      [], [], null, [], null, [TEST_WRECK], [TEST_FORMATION], false
    );
    expect(hitTargets.some((t) => t.kind === 'wreck' || t.kind === 'formation')).toBe(false);

    hitTargets = [];
    drawScene(
      ctx, W, H, SECTOR_ID, TEST_SYSTEM as any, 0, hitTargets, null, 0, 0, // eslint-disable-line @typescript-eslint/no-explicit-any
      [], [], null, [], null, [TEST_WRECK], [TEST_FORMATION], true
    );
    const wreckHit = hitTargets.find((t) => t.kind === 'wreck');
    const formationHit = hitTargets.find((t) => t.kind === 'formation');
    expect(wreckHit?.id).toBe(TEST_WRECK.id);
    expect(formationHit?.id).toBe(TEST_FORMATION.id);
    if (wreckHit && wreckHit.meta.kind === 'wreck') {
      expect(wreckHit.meta.shipType).toBe(TEST_WRECK.destroyed_ship_type);
      expect(wreckHit.meta.suspect).toBe(false);
    }
    if (formationHit && formationHit.meta.kind === 'formation') {
      expect(formationHit.meta.discovered).toBe(true);
    }
  });

  it('deterministic seeded placement: same id/w/h/t always lands the same wreck glyph', () => {
    const ctx = makeNoopCtx();
    const a: HitTarget[] = [];
    const b: HitTarget[] = [];
    drawScene(ctx, W, H, SECTOR_ID, TEST_SYSTEM as any, 0, a, null, 0, 0, [], [], null, [], null, [TEST_WRECK], [], true); // eslint-disable-line @typescript-eslint/no-explicit-any
    drawScene(ctx, W, H, SECTOR_ID, TEST_SYSTEM as any, 0, b, null, 0, 0, [], [], null, [], null, [TEST_WRECK], [], true); // eslint-disable-line @typescript-eslint/no-explicit-any
    expect(a[0].x).toBe(b[0].x);
    expect(a[0].y).toBe(b[0].y);
  });
});

// ---------------------------------------------------------------------------
// (b) Full mount — interaction wiring
// ---------------------------------------------------------------------------

describe('SolarSystemViewscreen — interaction wiring (full mount)', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;

  beforeEach(() => {
    mockGet.mockReset();
    mockPost.mockReset();
    mockGet.mockResolvedValue({ data: TEST_SYSTEM });

    // reducedMotion=true pins drawScene's `t` at 0 in the live component
    // (drawNowRef: `const t = reducedMotionRef.current ? 0 : Date.now()/1000`)
    // — the same t=0 used by the reference drawScene() calls below, so a
    // synthetic click lands exactly where the mounted canvas put the glyph.
    window.matchMedia = vi.fn().mockImplementation((query: string) => ({
      matches: true,
      media: query,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn()
    })) as unknown as typeof window.matchMedia;

    class MockResizeObserver {
      observe() {}
      unobserve() {}
      disconnect() {}
    }
    (globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = MockResizeObserver;

    vi.spyOn(Element.prototype, 'getBoundingClientRect').mockReturnValue({
      width: W, height: H, top: 0, left: 0, right: W, bottom: H, x: 0, y: 0,
      toJSON() { return {}; }
    } as DOMRect);

    vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockImplementation(
      (() => makeNoopCtx()) as unknown as typeof HTMLCanvasElement.prototype.getContext
    );

    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(async () => {
    await act(async () => { root.unmount(); });
    container.remove();
    vi.restoreAllMocks();
  });

  // Discover this scene's real glyph coordinates by calling the SAME exported
  // drawScene() the mounted component itself calls internally (same w/h/
  // sectorId/system/ships/t=0) — no hand-derived seeded-math duplication.
  const referenceTargets = (
    wrecks: SectorWreck[] = [],
    formations: SpecialFormationSummary[] = [],
    scanActive = false
  ): HitTarget[] => {
    const hitTargets: HitTarget[] = [];
    drawScene(
      makeNoopCtx(), W, H, SECTOR_ID, TEST_SYSTEM as any, 0, hitTargets, null, 0, 0, // eslint-disable-line @typescript-eslint/no-explicit-any
      [TEST_SHIP], [], null, [], null, wrecks, formations, scanActive
    );
    return hitTargets;
  };

  const mount = async (props: Record<string, unknown> = {}) => {
    await act(async () => {
      root.render(
        <SolarSystemViewscreen
          sectorId={SECTOR_ID}
          scene="flight"
          ships={[TEST_SHIP]}
          wrecks={[TEST_WRECK]}
          formations={[TEST_FORMATION]}
          {...props}
        />
      );
    });
    await flush();
  };

  const click = async (x: number, y: number) => {
    const canvas = container.querySelector('canvas')!;
    await act(async () => {
      canvas.dispatchEvent(new MouseEvent('click', { bubbles: true, clientX: x, clientY: y }));
    });
  };

  const rightClick = async (x: number, y: number) => {
    const canvas = container.querySelector('canvas')!;
    await act(async () => {
      canvas.dispatchEvent(new MouseEvent('contextmenu', { bubbles: true, cancelable: true, clientX: x, clientY: y }));
    });
  };

  const toggleScan = async () => {
    const btn = Array.from(container.querySelectorAll('button')).find((b) => b.textContent?.includes('SCAN'))!;
    await act(async () => { btn.dispatchEvent(new MouseEvent('click', { bubbles: true })); });
  };

  const closePopupViaEscape = async () => {
    await act(async () => {
      window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
    });
  };

  it('left-clicking a ship glyph calls onSelectShip with its id', async () => {
    const onSelectShip = vi.fn();
    await mount({ onSelectShip });

    const ship = referenceTargets().find((t) => t.kind === 'ship')!;
    expect(ship).toBeDefined();
    await click(ship.x, ship.y);

    expect(onSelectShip).toHaveBeenCalledWith('ship-alpha');
    // Left-click also opens the info popup for the same contact (existing
    // behavior, unaffected by the new SELECT wiring).
    expect(container.querySelector('.ssv-popup')).not.toBeNull();
  });

  it('right-click on empty space opens a context menu with only TRAVEL HERE', async () => {
    await mount();
    const targets = referenceTargets([TEST_WRECK], [TEST_FORMATION], true);
    const safe = [
      { x: 2, y: 2 }, { x: W - 2, y: 2 }, { x: 2, y: H - 2 }, { x: W - 2, y: H - 2 }
    ].find((c) => targets.every((t) => Math.hypot(t.x - c.x, t.y - c.y) > Math.max(12, t.r) + 20));
    expect(safe).toBeDefined();

    await rightClick(safe!.x, safe!.y);

    const popup = container.querySelector('.ssv-popup');
    expect(popup).not.toBeNull();
    expect(popup!.textContent).toContain('SECTOR SPACE');
    expect(popup!.textContent).toContain('TRAVEL HERE');
    expect(popup!.textContent).not.toContain('INSPECT');
    expect(popup!.textContent).not.toContain('SELECT');
  });

  it('right-click on a ship contact offers Travel / Inspect / SELECT, and SELECT calls onSelectShip', async () => {
    const onSelectShip = vi.fn();
    await mount({ onSelectShip });
    const ship = referenceTargets().find((t) => t.kind === 'ship')!;

    await rightClick(ship.x, ship.y);
    let popup = container.querySelector('.ssv-popup');
    expect(popup).not.toBeNull();
    expect(popup!.textContent).toContain('TRAVEL HERE');
    expect(popup!.textContent).toContain('INSPECT');
    expect(popup!.textContent).toContain('SELECT');

    const selectBtn = Array.from(popup!.querySelectorAll('button')).find((b) => b.textContent?.includes('SELECT'))!;
    await act(async () => { selectBtn.dispatchEvent(new MouseEvent('click', { bubbles: true })); });

    expect(onSelectShip).toHaveBeenCalledWith('ship-alpha');
    // SELECT closes the context-menu popup.
    popup = container.querySelector('.ssv-popup');
    expect(popup).toBeNull();
  });

  it('Escape closes a right-click context-menu popup (same effect as the info popup)', async () => {
    await mount();
    const ship = referenceTargets().find((t) => t.kind === 'ship')!;
    await rightClick(ship.x, ship.y);
    expect(container.querySelector('.ssv-popup')).not.toBeNull();

    await closePopupViaEscape();
    expect(container.querySelector('.ssv-popup')).toBeNull();
  });

  it('scanActive gates the wreck/formation hit targets in the live component too', async () => {
    await mount();
    const targets = referenceTargets([TEST_WRECK], [TEST_FORMATION], true);
    const wreck = targets.find((t) => t.kind === 'wreck')!;
    expect(wreck).toBeDefined();

    // SCAN is off by default -- right-clicking the wreck's would-be position
    // hits nothing (the empty-space menu), because scanActive=false means
    // the wreck was never pushed to hitTargets this frame.
    await rightClick(wreck.x, wreck.y);
    let popup = container.querySelector('.ssv-popup');
    expect(popup!.textContent).toContain('SECTOR SPACE');
    await closePopupViaEscape();

    // Toggle SCAN on -- now the SAME position hits the wreck.
    await toggleScan();
    await rightClick(wreck.x, wreck.y);
    popup = container.querySelector('.ssv-popup');
    expect(popup).not.toBeNull();
    expect(popup!.textContent).not.toContain('SECTOR SPACE');
    expect(popup!.textContent).toContain('INSPECT');
    // A wreck is not a ship -- no SELECT offered.
    expect(popup!.textContent).not.toContain('SELECT');
  });

  it('mounts and draws without throwing on an empty/malformed system payload (bodies/stations missing)', async () => {
    mockGet.mockResolvedValue({
      data: {
        sector_id: SECTOR_ID, sector_type: 'normal', star: null, nebula: null, belt: null,
        bodies: undefined, stations: undefined
      }
    });
    // A synchronous throw inside the effect/draw path would propagate out of
    // this act() call and fail the test -- the assertions below are belt-
    // and-suspenders on top of that implicit "did not throw" proof.
    await mount({ ships: [], wrecks: [], formations: [] });
    expect(container.querySelector('canvas')).not.toBeNull();
  });

  // WO-UI5-CANON-PASS(C): the landedCtx guard (`if (landedPlanetId &&
  // system?.bodies)`, SolarSystemViewscreen.tsx ~line 7621) had ZERO direct
  // coverage -- every existing malformed-payload test above mounts the
  // default `scene="flight"`, which never reaches that branch. landedPlanetId
  // is deliberately truthy here so the guard's outcome hinges specifically on
  // `system?.bodies` being falsy (isolates this test's target branch from the
  // sibling `!landedPlanetId` short-circuit, which is untouched by this fix).
  it('mounts scene="landed" without throwing when system.bodies is absent (the landedCtx guard holds)', async () => {
    mockGet.mockResolvedValue({
      data: {
        sector_id: SECTOR_ID, sector_type: 'normal',
        star: { kind: 'G_YELLOW', label: 'Test Star', color: '#ffdd88' },
        nebula: null, belt: null, bodies: undefined, stations: undefined
      }
    });
    await mount({
      scene: 'landed',
      landedPlanetId: 'planet-not-in-system',
      planetType: 'ROCKY',
      habitability: 40,
      citadelLevel: 1,
      ships: [], wrecks: [], formations: []
    });
    // A synchronous throw inside landedCtx/vistaAdaptedInput construction (or
    // the draw loop) would propagate out of this act() call and fail the
    // test above -- the canvas assertion is belt-and-suspenders on top of
    // that implicit "did not throw" proof, same convention as the sibling
    // flight-scene malformed-payload test above.
    expect(container.querySelector('canvas')).not.toBeNull();
  });
});
