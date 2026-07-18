// @vitest-environment jsdom
/**
 * tableauFxHarness — DOM-light proof (WO-AAA-SOLAR-TABLEAU phase 1).
 * Deterministic, timing-free: every assertion hangs off the harness's
 * SYNCHRONOUS behavior (register/resize both draw immediately) rather than
 * real requestAnimationFrame scheduling, mirroring
 * SolarSystemViewscreen.livingWindshield.test.tsx's mock conventions
 * (matchMedia / ResizeObserver / getBoundingClientRect) without needing to
 * mount a full component.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { createTableauFxHarness, pctToPx, DEFAULT_MAX_DPR, type TableauFxDrawFn } from '../tableauFxHarness';

const W = 1440;
const H = 334.7;

describe('pctToPx', () => {
  it('scales percentage anchors into the given CSS-pixel box', () => {
    expect(pctToPx(0, 0, W, H)).toEqual({ x: 0, y: 0 });
    expect(pctToPx(50, 50, W, H)).toEqual({ x: W / 2, y: H / 2 });
    expect(pctToPx(100, 100, W, H)).toEqual({ x: W, y: H });
  });
});

describe('createTableauFxHarness', () => {
  let container: HTMLElement;
  let disconnect: ReturnType<typeof vi.fn>;
  let rafSpy: ReturnType<typeof vi.spyOn>;
  /** Captured so a test can simulate the ResizeObserver actually firing
   *  (the mock never observes real layout). */
  let roCallback: (() => void) | undefined;

  const mockMatchMedia = (reduced: boolean) => {
    window.matchMedia = vi.fn().mockImplementation((query: string) => ({
      matches: reduced,
      media: query,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    })) as unknown as typeof window.matchMedia;
  };

  beforeEach(() => {
    mockMatchMedia(false);

    disconnect = vi.fn();
    roCallback = undefined;
    class MockResizeObserver {
      constructor(cb: () => void) { roCallback = cb; }
      observe() {}
      unobserve() {}
      disconnect() { disconnect(); }
    }
    (globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = MockResizeObserver;
    // jsdom has no IntersectionObserver -- the harness's own typeof guard
    // must degrade to "always intersecting" without it; leave it undefined.
    delete (globalThis as { IntersectionObserver?: unknown }).IntersectionObserver;

    Object.defineProperty(window, 'devicePixelRatio', { value: 3, writable: true, configurable: true });

    container = document.createElement('div');
    vi.spyOn(container, 'getBoundingClientRect').mockReturnValue({
      width: W, height: H, top: 0, left: 0, right: W, bottom: H, x: 0, y: 0,
      toJSON() { return {}; },
    } as DOMRect);

    rafSpy = vi.spyOn(window, 'requestAnimationFrame').mockReturnValue(1);
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('draws a newly registered canvas synchronously (never blank until the next tick)', () => {
    const harness = createTableauFxHarness(container);
    const canvas = document.createElement('canvas');
    const draw = vi.fn<TableauFxDrawFn>();

    harness.register(canvas, draw);

    expect(draw).toHaveBeenCalledTimes(1);
    const [, mapper, size] = draw.mock.calls[0];
    expect(mapper(50, 50)).toEqual({ x: W / 2, y: H / 2 });
    expect(size.cssWidth).toBe(W);
    expect(size.cssHeight).toBe(H);
  });

  it('manageSize=true (default) sizes the backing store to the box x capped DPR', () => {
    const harness = createTableauFxHarness(container);
    const canvas = document.createElement('canvas');
    harness.register(canvas, vi.fn());

    // devicePixelRatio=3 mocked above, capped at DEFAULT_MAX_DPR (2).
    expect(canvas.width).toBe(Math.floor(W * DEFAULT_MAX_DPR));
    expect(canvas.height).toBe(Math.floor(H * DEFAULT_MAX_DPR));
  });

  it('manageSize=false leaves the backing store alone (a WebGL consumer owns its own sizing)', () => {
    const harness = createTableauFxHarness(container);
    const canvas = document.createElement('canvas');
    canvas.width = 42;
    canvas.height = 7;
    const draw = vi.fn<TableauFxDrawFn>();

    harness.register(canvas, draw, { manageSize: false });

    expect(canvas.width).toBe(42);
    expect(canvas.height).toBe(7);
    // the draw call still reports the capped dpr so the consumer can size itself.
    const [, , size] = draw.mock.calls[0];
    expect(size.dpr).toBe(DEFAULT_MAX_DPR);
  });

  it('respects a per-registration maxDpr override', () => {
    const harness = createTableauFxHarness(container);
    const canvas = document.createElement('canvas');
    const draw = vi.fn<TableauFxDrawFn>();

    harness.register(canvas, draw, { maxDpr: 1 });

    expect(canvas.width).toBe(W);
    const [, , size] = draw.mock.calls[0];
    expect(size.dpr).toBe(1);
  });

  it('resize re-sizes every managed canvas and redraws all registrations immediately', () => {
    const harness = createTableauFxHarness(container);
    const canvas = document.createElement('canvas');
    const draw = vi.fn<TableauFxDrawFn>();
    harness.register(canvas, draw);
    draw.mockClear();

    (container.getBoundingClientRect as ReturnType<typeof vi.fn>).mockReturnValue({
      width: 800, height: 200, top: 0, left: 0, right: 800, bottom: 200, x: 0, y: 0,
      toJSON() { return {}; },
    } as DOMRect);

    // Simulate the ResizeObserver actually firing (the mock never observes
    // real layout) by invoking the callback the harness registered with it.
    roCallback?.();

    expect(canvas.width).toBe(Math.floor(800 * DEFAULT_MAX_DPR));
    expect(draw).toHaveBeenCalled();
  });

  it('reduced-motion pins t=0, paints one static frame, and never starts the rAF loop', () => {
    mockMatchMedia(true);
    const harness = createTableauFxHarness(container);
    const canvas = document.createElement('canvas');
    const draw = vi.fn<TableauFxDrawFn>();

    harness.register(canvas, draw);

    expect(draw).toHaveBeenCalledTimes(1);
    const [t] = draw.mock.calls[0];
    expect(t).toBe(0);
    expect(rafSpy).not.toHaveBeenCalled();
  });

  it('normal motion starts the rAF loop', () => {
    const harness = createTableauFxHarness(container);
    harness.register(document.createElement('canvas'), vi.fn());
    expect(rafSpy).toHaveBeenCalled();
  });

  it('unregister stops a canvas from receiving further forced draws', () => {
    const harness = createTableauFxHarness(container);
    const canvas = document.createElement('canvas');
    const draw = vi.fn<TableauFxDrawFn>();
    const unregister = harness.register(canvas, draw);
    draw.mockClear();

    unregister();
    harness.drawNow();

    expect(draw).not.toHaveBeenCalled();
  });

  it('destroy disconnects the ResizeObserver', () => {
    const harness = createTableauFxHarness(container);
    harness.destroy();
    expect(disconnect).toHaveBeenCalled();
  });
});
