// @vitest-environment jsdom
/**
 * SectorViewport — canvas-drawn sector minimap (planets/stations + entity
 * hover/click hit-testing). jsdom has no real 2D rendering backend, so
 * getContext('2d') is mocked with a recording no-op Proxy (mirrors
 * LandingPage.smoke.test.tsx's makeNoopCtx(), extended here to log each
 * call + the fillStyle/strokeStyle in effect at call time, since a few
 * assertions below need to distinguish *which* draw branch ran without
 * over-fitting to exact pixel math). requestAnimationFrame is mocked to
 * never re-invoke its callback (deterministic single frame per mount,
 * same convention as LandingPage's "boot / ignition" describe block) --
 * `animate()` still runs once synchronously on mount, which is enough to
 * populate the hit-test entity positions the interaction tests rely on.
 *
 * Pins: the toBufferCoords conversion (buffer/display ratio + the
 * uiScale===1/zero-rect no-op guards), hover/click hit-testing against the
 * real planet/station layout formulas, the tooltip's type icon + name, the
 * legend/label chrome, and the 3 sector-effect branches (radiation overlay,
 * hazard>5 pulse, void vignette) via call-log inspection rather than pixel
 * assertions.
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

vi.mock('../../../contexts/SettingsContext', () => ({
  useSettings: () => ({ uiScale: 1 }),
}));

import SectorViewport from '../SectorViewport';

type CtxCall = { method: string; args: unknown[]; fillStyle: unknown; strokeStyle: unknown };

function makeRecordingNoopCtx(): { ctx: CanvasRenderingContext2D; calls: CtxCall[] } {
  const calls: CtxCall[] = [];
  const store: Record<string, unknown> = {};
  const ctx = new Proxy(store, {
    get(target, prop) {
      if (typeof prop !== 'string') return undefined;
      if (prop === 'measureText') return () => ({ width: 10 });
      if (prop === 'createRadialGradient') {
        return (...args: unknown[]) => {
          calls.push({ method: 'createRadialGradient', args, fillStyle: target.fillStyle, strokeStyle: target.strokeStyle });
          return { addColorStop: () => {} };
        };
      }
      if (prop in target && typeof target[prop] !== 'function') return target[prop];
      return (...args: unknown[]) => {
        calls.push({ method: prop, args, fillStyle: target.fillStyle, strokeStyle: target.strokeStyle });
      };
    },
    set(target, prop, value) {
      target[prop as string] = value;
      return true;
    },
  }) as unknown as CanvasRenderingContext2D;
  return { ctx, calls };
}

describe('SectorViewport', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;
  let getContextSpy: ReturnType<typeof vi.spyOn>;
  let rafSpy: ReturnType<typeof vi.spyOn>;
  let cancelSpy: ReturnType<typeof vi.spyOn>;
  let errorSpy: ReturnType<typeof vi.spyOn>;
  let calls: CtxCall[];
  let nextRafId: number;

  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(0);
    const recording = makeRecordingNoopCtx();
    calls = recording.calls;
    getContextSpy = vi
      .spyOn(HTMLCanvasElement.prototype, 'getContext')
      .mockImplementation((() => recording.ctx) as unknown as typeof HTMLCanvasElement.prototype.getContext);
    vi.spyOn(HTMLCanvasElement.prototype, 'getBoundingClientRect').mockImplementation(function (this: HTMLCanvasElement) {
      return {
        left: 0,
        top: 0,
        width: this.width,
        height: this.height,
        right: this.width,
        bottom: this.height,
        x: 0,
        y: 0,
        toJSON: () => {},
      } as DOMRect;
    });
    nextRafId = 1;
    rafSpy = vi.spyOn(window, 'requestAnimationFrame').mockImplementation(() => nextRafId++) as unknown as ReturnType<typeof vi.spyOn>;
    cancelSpy = vi.spyOn(window, 'cancelAnimationFrame').mockImplementation(() => {});
    errorSpy = vi.spyOn(console, 'error').mockImplementation(() => {});
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(async () => {
    await act(async () => {
      root.unmount();
    });
    container.remove();
    getContextSpy.mockRestore();
    rafSpy.mockRestore();
    cancelSpy.mockRestore();
    errorSpy.mockRestore();
    vi.useRealTimers();
  });

  const mount = async (props: Partial<React.ComponentProps<typeof SectorViewport>> = {}) => {
    await act(async () => {
      root.render(<SectorViewport sectorName="Alpha Reach" {...props} />);
    });
  };

  const canvas = () => container.querySelector('canvas.sector-viewport-canvas') as HTMLCanvasElement;

  const moveTo = async (x: number, y: number) => {
    await act(async () => {
      canvas().dispatchEvent(
        new MouseEvent('mousemove', { bubbles: true, cancelable: true, clientX: x, clientY: y })
      );
    });
  };

  const clickAt = async () => {
    await act(async () => {
      canvas().dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true }));
    });
  };

  it('renders the canvas at its default 450x300 size, the sector label, and the legend', async () => {
    await mount();
    expect(canvas().width).toBe(450);
    expect(canvas().height).toBe(300);
    expect(container.querySelector('.viewport-label')?.textContent).toBe('Alpha Reach');
    const legendLabels = Array.from(container.querySelectorAll('.legend-label')).map((n) => n.textContent);
    expect(legendLabels).toEqual(['Planets', 'Stations']);
  });

  it('honors explicit width/height props', async () => {
    await mount({ width: 600, height: 400 });
    expect(canvas().width).toBe(600);
    expect(canvas().height).toBe(400);
  });

  it('draws one animation frame on mount and schedules the next via requestAnimationFrame', async () => {
    await mount();
    expect(getContextSpy).toHaveBeenCalled();
    expect(rafSpy).toHaveBeenCalledTimes(1);
    expect(errorSpy).not.toHaveBeenCalled();
  });

  it('cancels the animation frame on unmount', async () => {
    await mount();
    await act(async () => {
      root.unmount();
    });
    expect(cancelSpy).toHaveBeenCalled();
  });

  describe('hit-testing: planets', () => {
    it('shows a PLANET tooltip when hovering a planet\'s computed position, and clears it when moving away', async () => {
      await mount({ planets: [{ id: 'p1', name: 'Terra Nova', type: 'terran' }], width: 450, height: 300 });

      // Single planet: x = width/(1+1) = 225, y = height*0.5 = 150.
      await moveTo(225, 150);
      expect(container.querySelector('.tooltip-type')?.textContent).toBe('🪐 PLANET');
      expect(container.querySelector('.tooltip-name')?.textContent).toBe('Terra Nova');
      expect(canvas().style.cursor).toBe('pointer');

      await moveTo(10, 10);
      expect(container.querySelector('.viewport-tooltip')).toBeNull();
      expect(canvas().style.cursor).toBe('default');
    });

    it('positions the tooltip at the display-space pointer offset, +10px', async () => {
      await mount({ planets: [{ id: 'p1', name: 'Terra Nova', type: 'terran' }] });
      await moveTo(225, 150);
      const tooltip = container.querySelector('.viewport-tooltip') as HTMLElement;
      expect(tooltip.style.left).toBe('235px');
      expect(tooltip.style.top).toBe('160px');
    });

    it('fires onEntityClick with the planet\'s type/id/name when clicked while hovered', async () => {
      const onEntityClick = vi.fn();
      await mount({ planets: [{ id: 'p1', name: 'Terra Nova', type: 'terran' }], onEntityClick });
      await moveTo(225, 150);
      await clickAt();
      expect(onEntityClick).toHaveBeenCalledWith({ type: 'planet', id: 'p1', name: 'Terra Nova' });
    });

    it('does not fire onEntityClick when clicking without a hovered entity', async () => {
      const onEntityClick = vi.fn();
      await mount({ planets: [{ id: 'p1', name: 'Terra Nova', type: 'terran' }], onEntityClick });
      await clickAt();
      expect(onEntityClick).not.toHaveBeenCalled();
    });
  });

  describe('hit-testing: stations', () => {
    it('hit-tests a station orbiting a planet at its computed (time=0, index=0) position', async () => {
      // time=0 -> orbitAngle=0 -> stationX = planetX + cos(0)*60 = 225+60 = 285, stationY = planetY + sin(0)*60 = 150.
      await mount({
        planets: [{ id: 'p1', name: 'Terra Nova', type: 'terran' }],
        stations: [{ id: 's1', name: 'Outpost Prime' }],
      });
      await moveTo(285, 150);
      expect(container.querySelector('.tooltip-type')?.textContent).toBe('🏢 STATION');
      expect(container.querySelector('.tooltip-name')?.textContent).toBe('Outpost Prime');
    });

    it('hit-tests a standalone station (no planets) at its computed position', async () => {
      // No planets: stationCount=1, spacing=width/2=225, stationX=225*(0+1)=225, stationY=height*0.5=150
      // (floatOffset = sin(Date.now()*0.001 + 0)*5 = sin(0)*5 = 0 at time=0).
      await mount({ stations: [{ id: 's1', name: 'Drift Station' }] });
      await moveTo(225, 150);
      expect(container.querySelector('.tooltip-type')?.textContent).toBe('🏢 STATION');
      expect(container.querySelector('.tooltip-name')?.textContent).toBe('Drift Station');
    });

    it('fires onEntityClick with type "station" for a hovered+clicked station', async () => {
      const onEntityClick = vi.fn();
      await mount({ stations: [{ id: 's1', name: 'Drift Station' }], onEntityClick });
      await moveTo(225, 150);
      await clickAt();
      expect(onEntityClick).toHaveBeenCalledWith({ type: 'station', id: 's1', name: 'Drift Station' });
    });
  });

  describe('sector effects', () => {
    it('draws the radiation overlay only when radiationLevel > 0', async () => {
      await mount({ radiationLevel: 0, planets: [] });
      expect(calls.some((c) => c.method === 'fillRect' && String(c.fillStyle).startsWith('rgba(0, 255, 65,'))).toBe(false);

      calls.length = 0;
      await act(async () => {
        root.unmount();
      });
      root = createRoot(container);
      await mount({ radiationLevel: 5, planets: [] });
      expect(calls.some((c) => c.method === 'fillRect' && String(c.fillStyle).startsWith('rgba(0, 255, 65,'))).toBe(true);
    });

    it('draws the hazard pulse (strokeRect) only when hazardLevel > 5', async () => {
      await mount({ hazardLevel: 5, planets: [] });
      expect(calls.some((c) => c.method === 'strokeRect')).toBe(false);

      calls.length = 0;
      await act(async () => {
        root.unmount();
      });
      root = createRoot(container);
      await mount({ hazardLevel: 6, planets: [] });
      expect(calls.some((c) => c.method === 'strokeRect' && String(c.strokeStyle).startsWith('rgba(255, 107, 0,'))).toBe(true);
    });

    it('draws the void vignette (createRadialGradient) only for sectorType "void"', async () => {
      await mount({ sectorType: 'normal', planets: [] });
      expect(calls.some((c) => c.method === 'createRadialGradient')).toBe(false);

      calls.length = 0;
      await act(async () => {
        root.unmount();
      });
      root = createRoot(container);
      await mount({ sectorType: 'void', planets: [] });
      expect(calls.some((c) => c.method === 'createRadialGradient')).toBe(true);
    });
  });
});
