// @vitest-environment jsdom
/**
 * QuantumBearingViewport — the ASTROGATION PLOT canvas instrument (ADR-0030
 * Phase 1). Pure instrument: parent owns yaw/pitch/band state, this reports
 * drag-to-aim yaw changes and renders the plot. QuantumDriveConsole.test.tsx
 * stubs this component entirely ("its own canvas/ResizeObserver/rAF
 * machinery is irrelevant here") -- this file covers what that one skips.
 *
 * jsdom has no real 2D canvas backend, no ResizeObserver, no matchMedia, and
 * no pointer-capture methods -- all stubbed below, mirroring the
 * SectorViewport/SolarSystemViewscreen conventions (recording no-op ctx,
 * captured-not-auto-invoked requestAnimationFrame). Pins: the CHARTING /
 * CHART UNAVAILABLE / (neither) status branches, the BRG/PIT readout
 * formatting (incl. the U+2212 minus sign for negative pitch), the
 * yawFromPointer compass math (N/E/S/W) driving onBearingChange, the
 * primary-mouse-button-only drag gate, and that a draw pass runs without
 * throwing once the canvas has real dimensions.
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import QuantumBearingViewport from '../QuantumBearingViewport';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

type CtxCall = { method: string; args: unknown[] };
function makeRecordingNoopCtx(): { ctx: CanvasRenderingContext2D; calls: CtxCall[] } {
  const calls: CtxCall[] = [];
  const store: Record<string, unknown> = {};
  const ctx = new Proxy(store, {
    get(target, prop) {
      if (typeof prop !== 'string') return undefined;
      if (prop === 'createRadialGradient') {
        return (...args: unknown[]) => {
          calls.push({ method: 'createRadialGradient', args });
          return { addColorStop: () => {} };
        };
      }
      if (prop in target && typeof target[prop] !== 'function') return target[prop];
      return (...args: unknown[]) => {
        calls.push({ method: prop, args });
      };
    },
    set(target, prop, value) {
      target[prop as string] = value;
      return true;
    },
  }) as unknown as CanvasRenderingContext2D;
  return { ctx, calls };
}

class MockResizeObserver {
  static instances: MockResizeObserver[] = [];
  cb: ResizeObserverCallback;
  constructor(cb: ResizeObserverCallback) {
    this.cb = cb;
    MockResizeObserver.instances.push(this);
  }
  observe() {}
  unobserve() {}
  disconnect() {}
}

describe('QuantumBearingViewport', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;
  let getContextSpy: ReturnType<typeof vi.spyOn>;
  let calls: CtxCall[];
  let rafCallbacks: FrameRequestCallback[];
  let errorSpy: ReturnType<typeof vi.spyOn>;

  const flushRaf = (t = 16) => {
    const cbs = rafCallbacks;
    rafCallbacks = [];
    cbs.forEach((cb) => cb(t));
  };

  const triggerResize = () => {
    MockResizeObserver.instances.forEach((inst) => inst.cb([] as unknown as ResizeObserverEntry[], inst as unknown as ResizeObserver));
  };

  beforeEach(() => {
    MockResizeObserver.instances = [];
    (globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = MockResizeObserver;

    (window as unknown as { matchMedia: unknown }).matchMedia = vi.fn().mockReturnValue({
      matches: false,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    });

    vi.spyOn(Element.prototype, 'getBoundingClientRect').mockReturnValue({
      width: 300, height: 216, top: 0, left: 0, right: 300, bottom: 216, x: 0, y: 0,
      toJSON() { return {}; },
    } as DOMRect);

    (HTMLElement.prototype as unknown as { setPointerCapture: (id: number) => void }).setPointerCapture = vi.fn();
    (HTMLElement.prototype as unknown as { releasePointerCapture: (id: number) => void }).releasePointerCapture = vi.fn();
    (HTMLElement.prototype as unknown as { hasPointerCapture: (id: number) => boolean }).hasPointerCapture = vi.fn(() => true);

    const recording = makeRecordingNoopCtx();
    calls = recording.calls;
    getContextSpy = vi
      .spyOn(HTMLCanvasElement.prototype, 'getContext')
      .mockImplementation((() => recording.ctx) as unknown as typeof HTMLCanvasElement.prototype.getContext);

    rafCallbacks = [];
    vi.spyOn(window, 'requestAnimationFrame').mockImplementation((cb: FrameRequestCallback) => {
      rafCallbacks.push(cb);
      return rafCallbacks.length;
    });
    vi.spyOn(window, 'cancelAnimationFrame').mockImplementation(() => {});

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
    errorSpy.mockRestore();
    vi.restoreAllMocks();
  });

  const baseProps = {
    yawDeg: 0,
    pitchDeg: 0,
    rangeBand: 'near' as const,
    onBearingChange: () => {},
    phase: 'idle' as const,
    spacing: 1000,
    sectors: [] as { dx: number; dy: number; dz: number }[],
  };

  const mount = async (props: Partial<React.ComponentProps<typeof QuantumBearingViewport>> = {}) => {
    await act(async () => {
      root.render(<QuantumBearingViewport {...baseProps} {...props} />);
    });
  };

  const canvas = () => container.querySelector('canvas.qbv-canvas') as HTMLCanvasElement;

  it('renders the header, canvas with application role + aria-label, and scanlines', async () => {
    await mount();
    expect(container.querySelector('.qbv-header')?.textContent).toBe('ASTROGATION PLOT');
    expect(canvas().getAttribute('role')).toBe('application');
    expect(canvas().getAttribute('aria-label')).toBe(
      'Astrogation plot. Drag to set the yaw bearing; use the pitch slider for elevation.'
    );
    expect(container.querySelector('.qbv-scanlines')).not.toBeNull();
  });

  it('formats the BRG/PIT readout, including the minus sign for negative pitch', async () => {
    await mount({ yawDeg: 45.567, pitchDeg: -12.34 });
    expect(container.querySelector('.qbv-readout')?.textContent).toBe('BRG 45.6° / PIT −12.3°');
  });

  it('formats a non-negative pitch with a leading +', async () => {
    await mount({ yawDeg: 0, pitchDeg: 5 });
    expect(container.querySelector('.qbv-readout')?.textContent).toBe('BRG 0.0° / PIT +5.0°');
  });

  describe('chart status branches', () => {
    it('shows CHARTING when chartLoading is true', async () => {
      await mount({ chartLoading: true, sectors: null });
      expect(container.querySelector('.qbv-chart-loading')?.textContent).toBe('CHARTING…');
      expect(container.querySelector('.qbv-chart-warn')).toBeNull();
    });

    it('shows CHART UNAVAILABLE when sectors is null and not loading', async () => {
      await mount({ sectors: null });
      expect(container.querySelector('.qbv-chart-warn')?.textContent).toBe('CHART UNAVAILABLE — BEARING ONLY');
      expect(container.querySelector('.qbv-chart-loading')).toBeNull();
    });

    it('shows neither status when sectors is a loaded (possibly empty) array', async () => {
      await mount({ sectors: [] });
      expect(container.querySelector('.qbv-chart-warn')).toBeNull();
      expect(container.querySelector('.qbv-chart-loading')).toBeNull();
    });
  });

  describe('draw pass', () => {
    it('runs a draw frame without throwing once the canvas has real dimensions', async () => {
      await mount({ sectors: [{ dx: 3000, dy: 0, dz: 0 }] });
      await act(async () => {
        triggerResize();
      });
      await act(async () => {
        flushRaf(100);
      });
      expect(errorSpy).not.toHaveBeenCalled();
      expect(calls.some((c) => c.method === 'arc')).toBe(true);
      expect(calls.some((c) => c.method === 'clearRect')).toBe(true);
    });
  });

  describe('drag-to-aim pointer math (compass: yaw 0 = N, increases clockwise)', () => {
    const pointerDown = async (clientX: number, clientY: number, opts: Partial<PointerEventInit> = {}) => {
      await act(async () => {
        canvas().dispatchEvent(
          new PointerEvent('pointerdown', { bubbles: true, cancelable: true, clientX, clientY, button: 0, pointerId: 1, pointerType: 'mouse', ...opts })
        );
      });
    };
    const pointerMove = async (clientX: number, clientY: number) => {
      await act(async () => {
        canvas().dispatchEvent(
          new PointerEvent('pointermove', { bubbles: true, cancelable: true, clientX, clientY, pointerId: 1, pointerType: 'mouse' })
        );
      });
    };
    const pointerUp = async () => {
      await act(async () => {
        canvas().dispatchEvent(new PointerEvent('pointerup', { bubbles: true, cancelable: true, pointerId: 1, pointerType: 'mouse' }));
      });
    };

    it.each([
      ['N (straight up from center)', 150, 0, 0],
      ['E (straight right)', 300, 108, 90],
      ['S (straight down)', 150, 216, 180],
      ['W (straight left)', 0, 108, 270],
    ])('reports yaw for %s', async (_label, x, y, expectedYaw) => {
      const onBearingChange = vi.fn();
      await mount({ pitchDeg: 7, onBearingChange });
      await pointerDown(x, y);
      expect(onBearingChange).toHaveBeenCalledWith(expectedYaw, 7);
    });

    it('does not start a drag for a non-primary mouse button, and a later move fires nothing', async () => {
      const onBearingChange = vi.fn();
      await mount({ onBearingChange });
      await pointerDown(300, 108, { button: 2 });
      expect(onBearingChange).not.toHaveBeenCalled();
      await pointerMove(150, 0);
      expect(onBearingChange).not.toHaveBeenCalled();
    });

    it('continues reporting yaw on pointermove while dragging', async () => {
      const onBearingChange = vi.fn();
      await mount({ onBearingChange });
      await pointerDown(150, 0);
      onBearingChange.mockClear();
      await pointerMove(300, 108);
      expect(onBearingChange).toHaveBeenCalledWith(90, 0);
    });

    it('ignores pointermove before any pointerdown', async () => {
      const onBearingChange = vi.fn();
      await mount({ onBearingChange });
      await pointerMove(300, 108);
      expect(onBearingChange).not.toHaveBeenCalled();
    });

    it('stops reporting on pointermove after pointerup ends the drag', async () => {
      const onBearingChange = vi.fn();
      await mount({ onBearingChange });
      await pointerDown(150, 0);
      await pointerUp();
      onBearingChange.mockClear();
      await pointerMove(300, 108);
      expect(onBearingChange).not.toHaveBeenCalled();
    });
  });
});
