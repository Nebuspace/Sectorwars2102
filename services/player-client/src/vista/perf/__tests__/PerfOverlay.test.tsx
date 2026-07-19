// @vitest-environment jsdom
/**
 * PerfOverlay — PERF-HARNESS sub-part (c).
 *
 * Mirrors collector.test.ts's "disabled is a true no-op" discipline for the
 * consumer side: the overlay must render NOTHING (and never flip
 * perfCollector.enabled) unless explicitly opted in via `?perf=1` or
 * localStorage. jsdom + react-dom/client createRoot + act() -- this
 * project's only available idiom (no RTL installed), mirrors
 * ReputationPage.test.tsx's seam.
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

const mockSnapshot = vi.fn();

vi.mock('../collector', () => ({
  perfCollector: {
    enabled: false,
    snapshot: (...a: unknown[]) => mockSnapshot(...a),
  },
}));

vi.mock('../scenes', () => ({
  TARGET_FRAME_MS: 7,
}));

import PerfOverlay from '../PerfOverlay';
import { perfCollector } from '../collector';

const FULL_SNAPSHOT = {
  layers: { drawScene: 1.2, drawWaterFX: 0.4, drawLandmarks: 2.1 },
  particleCount: 42,
  allocChurn: 5,
  fps: 60,
  frameMs: 3.7,
};

describe('PerfOverlay', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;
  let originalSearch: string;

  beforeEach(() => {
    mockSnapshot.mockReset().mockReturnValue(FULL_SNAPSHOT);
    perfCollector.enabled = false;
    originalSearch = window.location.search;
    window.localStorage.clear();
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(async () => {
    await act(async () => {
      root.unmount();
    });
    container.remove();
    window.history.replaceState(null, '', `${window.location.pathname}${originalSearch}`);
    vi.clearAllMocks();
  });

  const mount = async () => {
    await act(async () => {
      root.render(<PerfOverlay />);
    });
    await act(async () => {
      await new Promise((r) => setTimeout(r, 200)); // past THROTTLE_MS
    });
  };

  it('renders nothing and never flips perfCollector.enabled when the toggle is off', async () => {
    await mount();

    expect(container.querySelector('[data-testid="perf-overlay"]')).toBeNull();
    expect(perfCollector.enabled).toBe(false);
    expect(mockSnapshot).not.toHaveBeenCalled();
  });

  it('renders live via ?perf=1 -- sorted layers, fps/particles/allocs, and flips perfCollector.enabled on mount', async () => {
    window.history.replaceState(null, '', `${window.location.pathname}?perf=1`);

    await mount();

    expect(perfCollector.enabled).toBe(true);
    const overlay = container.querySelector('[data-testid="perf-overlay"]');
    expect(overlay).not.toBeNull();
    expect(container.querySelector('[data-testid="perf-fps"]')?.textContent).toBe('60 fps');

    // Sorted DESCENDING by ms -- drawLandmarks(2.1) > drawScene(1.2) > drawWaterFX(0.4).
    const rows = Array.from(container.querySelectorAll('li')).map((li) => li.textContent);
    expect(rows).toEqual(['drawLandmarks2.10ms', 'drawScene1.20ms', 'drawWaterFX0.40ms']);
  });

  it('renders live via localStorage.vistaPerf=1 too (not just the query param)', async () => {
    window.localStorage.setItem('vistaPerf', '1');

    await mount();

    expect(perfCollector.enabled).toBe(true);
    expect(container.querySelector('[data-testid="perf-overlay"]')).not.toBeNull();
  });

  it('shows the under-budget (green) color when frameMs is below TARGET_FRAME_MS', async () => {
    mockSnapshot.mockReturnValue({ ...FULL_SNAPSHOT, frameMs: 3.0 });
    window.history.replaceState(null, '', `${window.location.pathname}?perf=1`);

    await mount();

    const budget = container.querySelector('[data-testid="perf-budget"]') as HTMLElement;
    expect(budget.textContent).toBe('3.00ms / 7ms');
    expect(budget.style.color).toBe('rgb(96, 200, 144)'); // styles.budgetOk
  });

  it('shows the over-budget (red) color when frameMs exceeds TARGET_FRAME_MS', async () => {
    mockSnapshot.mockReturnValue({ ...FULL_SNAPSHOT, frameMs: 9.5 });
    window.history.replaceState(null, '', `${window.location.pathname}?perf=1`);

    await mount();

    const budget = container.querySelector('[data-testid="perf-budget"]') as HTMLElement;
    expect(budget.textContent).toBe('9.50ms / 7ms');
    expect(budget.style.color).toBe('rgb(200, 104, 120)'); // styles.budgetOver
  });

  it('resets perfCollector.enabled to false on unmount', async () => {
    window.history.replaceState(null, '', `${window.location.pathname}?perf=1`);
    await mount();
    expect(perfCollector.enabled).toBe(true);

    await act(async () => {
      root.unmount();
    });
    expect(perfCollector.enabled).toBe(false);
  });
});
