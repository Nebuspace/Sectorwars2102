// @vitest-environment jsdom
/**
 * StarDisc — WO-AAA-SOLAR-TABLEAU phase 2 headless proof.
 *
 * WebGL itself doesn't run in jsdom (`canvas.getContext('webgl')` returns
 * null there), so `THREE.WebGLRenderer` is mocked to a no-op stub -- every
 * OTHER three.js object (`ShaderMaterial`, `PlaneGeometry`, `Mesh`,
 * `Scene`, `OrthographicCamera`, `Vector2`/`Vector3`) is pure data/math with
 * no GL dependency at construction time, so this suite exercises the REAL
 * registration + per-frame uniform math (mapper -> center px, sizeEm/remPx
 * -> radius px, dpr scaling) end-to-end; only the actual GPU draw call is
 * stubbed out. The real visual proof (does it actually roil, is t=0 calm)
 * is the hub's browser-prove -- see the WO's own Proof section.
 *
 * Mount idiom matches WindshieldTableau.test.tsx's own convention
 * (react-dom/client createRoot + act) -- this codebase has no
 * @testing-library/react dependency.
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import type { TableauFxDrawFn, TableauFxHarness } from '../tableauFxHarness';
import { STAR_KINDS, starVisualParams } from '../starShader';

vi.mock('three', async () => {
  const actual = await vi.importActual<typeof import('three')>('three');
  class MockWebGLRenderer {
    setClearColor = vi.fn();
    setPixelRatio = vi.fn();
    setSize = vi.fn();
    render = vi.fn();
    dispose = vi.fn();
  }
  return { ...actual, WebGLRenderer: MockWebGLRenderer };
});

// Imported AFTER the mock so StarDisc's own `import * as THREE from 'three'`
// resolves to the mocked module (vi.mock is hoisted, but the dynamic import
// order here keeps the dependency explicit/readable).
const { default: StarDisc } = await import('../StarDisc');

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

function fakeHarness(): { harness: TableauFxHarness; draws: TableauFxDrawFn[]; unregister: ReturnType<typeof vi.fn> } {
  const draws: TableauFxDrawFn[] = [];
  const unregister = vi.fn();
  const harness: TableauFxHarness = {
    register: vi.fn((_canvas: HTMLCanvasElement, draw: TableauFxDrawFn) => {
      draws.push(draw);
      return unregister;
    }),
    drawNow: vi.fn(),
    destroy: vi.fn(),
  };
  return { harness, draws, unregister };
}

describe('starVisualParams', () => {
  it('returns finite, non-NaN params for all 11 server star kinds', () => {
    expect(STAR_KINDS).toHaveLength(11);
    for (const kind of STAR_KINDS) {
      const p = starVisualParams(kind, '#ffe066');
      for (const v of [
        ...p.colorCore, ...p.colorMid, ...p.colorSpot,
        p.granulationScale, p.granulationContrast, p.domainWarpStrength,
        p.limbPower, p.coronaReach, p.flareRate, p.sunspotAmount,
        p.cmeRate, p.lensingStrength,
      ]) {
        expect(Number.isFinite(v)).toBe(true);
      }
    }
  });

  it('only BLACK_HOLE sets isBlackHole', () => {
    for (const kind of STAR_KINDS) {
      expect(starVisualParams(kind, '#1a1026').isBlackHole).toBe(kind === 'BLACK_HOLE');
    }
  });

  it('derives colorMid from the given hex', () => {
    const p = starVisualParams('G_YELLOW', '#ffe066');
    expect(p.colorMid[0]).toBeCloseTo(1, 2);
    expect(p.colorMid[1]).toBeCloseTo(0xe0 / 255, 2);
    expect(p.colorMid[2]).toBeCloseTo(0x66 / 255, 2);
  });

  it('falls back to neutral grey on an unparseable hex instead of throwing', () => {
    expect(() => starVisualParams('G_YELLOW', 'not-a-color')).not.toThrow();
    const p = starVisualParams('G_YELLOW', 'not-a-color');
    expect(p.colorMid).toEqual([0.6, 0.6, 0.6]);
  });

  it('an unrecognized kind falls back to G_YELLOW-shaped defaults, not a throw', () => {
    expect(() => starVisualParams('SOME_FUTURE_KIND', '#ffe066')).not.toThrow();
    const fallback = starVisualParams('SOME_FUTURE_KIND', '#ffe066');
    const gYellow = starVisualParams('G_YELLOW', '#ffe066');
    expect(fallback.granulationScale).toBe(gYellow.granulationScale);
    expect(fallback.isBlackHole).toBe(false);
  });
});

describe('StarDisc', () => {
  let container: HTMLDivElement;
  let root: ReturnType<typeof createRoot>;

  beforeEach(() => {
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(async () => {
    await act(async () => { root.unmount(); });
    container.remove();
    vi.restoreAllMocks();
  });

  it('renders exactly one canvas, pointer-events:none, aria-hidden', async () => {
    const { harness } = fakeHarness();
    await act(async () => {
      root.render(
        <StarDisc harness={harness} star={{ xPct: 12, yPct: 50, sizeEm: 2.4 }} kind="G_YELLOW" color="#ffe066" />,
      );
    });
    const canvases = container.querySelectorAll('canvas');
    expect(canvases).toHaveLength(1);
    expect(canvases[0].style.pointerEvents).toBe('none');
    expect(canvases[0].getAttribute('aria-hidden')).toBe('true');
  });

  it('registers exactly once with the harness, manageSize:false', async () => {
    const { harness } = fakeHarness();
    await act(async () => {
      root.render(
        <StarDisc harness={harness} star={{ xPct: 12, yPct: 50, sizeEm: 2.4 }} kind="G_YELLOW" color="#ffe066" />,
      );
    });
    expect(harness.register).toHaveBeenCalledTimes(1);
    const [canvasArg, , optionsArg] = (harness.register as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(canvasArg).toBe(container.querySelector('canvas'));
    expect(optionsArg).toEqual({ manageSize: false });
  });

  it('a simulated harness-driven frame (real mapper/dpr math, mocked GL) does not throw at t=0 or t>0', async () => {
    const { harness, draws } = fakeHarness();
    await act(async () => {
      root.render(
        <StarDisc harness={harness} star={{ xPct: 12, yPct: 50, sizeEm: 2.4 }} kind="BLACK_HOLE" color="#1a1026" />,
      );
    });
    const mapper = (xPct: number, yPct: number) => ({ x: (xPct / 100) * 1200, y: (yPct / 100) * 280 });
    expect(draws).toHaveLength(1);
    expect(() => draws[0](0, mapper, { cssWidth: 1200, cssHeight: 280, dpr: 2 })).not.toThrow();
    expect(() => draws[0](12.5, mapper, { cssWidth: 1200, cssHeight: 280, dpr: 2 })).not.toThrow();
  });

  it('unregisters and disposes GL resources on unmount', async () => {
    const { harness, unregister } = fakeHarness();
    await act(async () => {
      root.render(
        <StarDisc harness={harness} star={{ xPct: 12, yPct: 50, sizeEm: 2.4 }} kind="G_YELLOW" color="#ffe066" />,
      );
    });
    await act(async () => { root.unmount(); });
    expect(unregister).toHaveBeenCalledTimes(1);
  });

  it('with harness=null, mounts a canvas and never attempts GL registration', async () => {
    await act(async () => {
      root.render(
        <StarDisc harness={null} star={{ xPct: 10, yPct: 50, sizeEm: 2 }} kind="NEUTRON" color="#b39dff" />,
      );
    });
    expect(container.querySelectorAll('canvas')).toHaveLength(1);
  });
});
