// @vitest-environment jsdom
/**
 * NavigationMap — neon chart polish (WO-NAV-CHART-POLISH).
 *
 * Live-mount smoke over a representative chart payload -- known ambient
 * sectors at multiple BFS depths, a frontier stub, a one-way edge, and a
 * plotted course all at once -- asserting ZERO console errors and that
 * every new sub-part's DOM footprint actually lands: the neon depth-tier
 * classes (sub-part a), the frontier glyph (sub-parts b/d), the one-way
 * arrow (sub-part c), and (as a regression guard) the pre-existing
 * COURSE-OVERLAY polyline/ship-marker rendering untouched alongside them.
 *
 * Mirrors NavigationMap.courseOverlay.test.tsx's seam: jsdom +
 * react-dom/client createRoot + act(), no RTL, no new deps, a synchronous
 * rAF polyfill (fine here since this file never asserts a frame count).
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import NavigationMap, { type CourseHopPoint } from '../NavigationMap';

// current(1) -- 2 is the only clickable 1-hop destination (availableMoves).
// 3/4 are ambient known sectors at depth 1/2 (out of range, but known) --
// the neon depth-tier proof. 5 is a frontier stub one hop past 4, linked
// back via connected_sectors (per navChartTransform's per-stub attachment).
const SECTORS = [
  { id: 1, name: 'Sector 1', type: 'normal', connected_sectors: [2, 3], depth: 0 },
  { id: 2, name: 'Sector 2', type: 'normal', connected_sectors: [1], depth: 1 },
  { id: 3, name: 'Sector 3', type: 'normal', connected_sectors: [1, 4], depth: 1 },
  { id: 4, name: 'Sector 4', type: 'normal', connected_sectors: [3], depth: 2 },
  { id: 5, name: 'Sector 5', type: 'frontier', connected_sectors: [4], depth: 3 },
];

const ONE_WAY_EDGES = [{ from: 3, to: 4 }];

const COURSE: CourseHopPoint[] = [{ sector_id: 2, name: 'Sector 2' }];

describe('NavigationMap — neon chart polish', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;
  let rafOrig: unknown;
  let cafOrig: unknown;
  let rafQueue: FrameRequestCallback[] = [];
  let consoleErrorSpy: ReturnType<typeof vi.spyOn>;

  beforeEach(() => {
    rafOrig = (global as any).requestAnimationFrame;
    cafOrig = (global as any).cancelAnimationFrame;
    rafQueue = [];
    (global as any).requestAnimationFrame = (cb: FrameRequestCallback): number => {
      rafQueue.push(cb);
      return rafQueue.length;
    };
    (global as any).cancelAnimationFrame = () => {};

    consoleErrorSpy = vi.spyOn(console, 'error').mockImplementation(() => {});

    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  const drainRaf = () => {
    let guard = 0;
    while (rafQueue.length > 0 && guard++ < 200) {
      const queued = rafQueue;
      rafQueue = [];
      queued.forEach((cb) => cb(0));
    }
  };

  afterEach(async () => {
    await act(async () => {
      root.unmount();
    });
    container.remove();
    (global as any).requestAnimationFrame = rafOrig;
    (global as any).cancelAnimationFrame = cafOrig;
    vi.restoreAllMocks();
  });

  const flush = async () => {
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 10));
    });
  };

  const mount = async () => {
    await act(async () => {
      root.render(
        <NavigationMap
          currentSectorId={1}
          sectors={SECTORS}
          availableMoves={[2]}
          onNavigate={vi.fn()}
          width={440}
          height={300}
          course={COURSE}
          currentHopIndex={0}
          oneWayEdges={ONE_WAY_EDGES}
        />,
      );
    });
    // The initial render's effects enqueue the first simulation frame
    // (rather than run it, under the queueing polyfill above) -- drain the
    // queue to let the force layout fully settle before asserting.
    await act(async () => {
      drainRaf();
    });
    await flush();
  };

  const classOf = (el: Element | null): string => el?.getAttribute('class') || '';

  // The project's vitest config has no `IS_REACT_ACT_ENVIRONMENT` setup
  // (vitest.config.ts has no setupFiles) -- confirmed by probing the
  // UNMODIFIED NavigationMap + the exact courseOverlay-test act()/rAF-
  // polyfill pattern in isolation: React logs "The current testing
  // environment is not configured to support act(...)" on every mount
  // regardless of any code under test. That is pre-existing project-wide
  // test-harness noise, not a real render error -- filtered out here so
  // this assertion catches genuinely NEW errors from the chart-polish
  // render path (a NaN transform, an undefined access, a missing key,
  // etc.), which is what "zero console errors" is actually meant to prove.
  const REACT_ACT_ENV_NOISE = 'The current testing environment is not configured to support act(...)';
  const unexpectedErrors = () =>
    consoleErrorSpy.mock.calls.filter((call) => call[0] !== REACT_ACT_ENV_NOISE);

  it('mounts a representative chart (ambient depths + frontier stub + one-way edge + plotted course) with zero UNEXPECTED console errors', async () => {
    await mount();
    expect(unexpectedErrors()).toEqual([]);
  });

  it('renders every known sector as a full node EXCEPT the frontier stub', async () => {
    await mount();
    expect(container.querySelectorAll('circle.node-circle').length).toBe(4); // 1,2,3,4 -- not 5
  });

  it('renders the frontier stub as a distinct rect glyph, never a circle.node-circle', async () => {
    await mount();
    const glyph = container.querySelector('[data-testid="frontier-node-5"]');
    expect(glyph).toBeTruthy();
    expect(glyph?.tagName.toLowerCase()).toBe('rect');
    expect(classOf(glyph).split(' ')).toContain('frontier-glyph');
    // Never picked up by the full-node query used elsewhere in the suite.
    expect(container.querySelectorAll('circle.node-circle title').length).toBe(4);
  });

  it('tags ambient (non-current, non-available) nodes with a neon depth-tier class matching their BFS depth', async () => {
    await mount();
    const titleToCircle = (title: string): Element | undefined =>
      Array.from(container.querySelectorAll('circle.node-circle')).find(
        (el) => el.querySelector('title')?.textContent === title,
      );

    const sector3 = titleToCircle('Sector 3'); // ambient, depth 1
    const sector4 = titleToCircle('Sector 4'); // ambient, depth 2
    expect(classOf(sector3).split(' ')).toContain('depth-tier-1');
    expect(classOf(sector4).split(' ')).toContain('depth-tier-2');

    // The current node and the clickable 1-hop node never get a depth-tier
    // class -- current/available styling always wins (untouched surface).
    const sector1 = titleToCircle('Sector 1');
    const sector2 = titleToCircle('Sector 2');
    expect(classOf(sector1).split(' ').some((c) => c.startsWith('depth-tier'))).toBe(false);
    expect(classOf(sector2).split(' ').some((c) => c.startsWith('depth-tier'))).toBe(false);
  });

  it('tags the ambient connection line between two depth-tiered nodes with the nearest-first depth-tier class', async () => {
    await mount();
    const line = Array.from(container.querySelectorAll('line.connection-line')).find((el) => {
      const cls = classOf(el).split(' ');
      return cls.includes('depth-tier-1') || cls.includes('depth-tier-2');
    });
    expect(line).toBeTruthy();
    // min(depth(3)=1, depth(4)=2) = 1 -- nearest-first.
    expect(classOf(line!).split(' ')).toContain('depth-tier-1');
  });

  it('renders a one-way arrow for the directed 3->4 edge', async () => {
    await mount();
    const arrow = container.querySelector('[data-testid="direction-arrow-3-4"]');
    expect(arrow).toBeTruthy();
    expect(arrow?.tagName.toLowerCase()).toBe('polygon');
    expect(classOf(arrow).split(' ')).toContain('direction-arrow');
  });

  it('REGRESSION: the shipped course-overlay polyline + hop marker still render alongside the new layers', async () => {
    await mount();
    expect(container.querySelector('[data-testid="course-polyline"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="course-hop-marker-2"]')).toBeTruthy();
  });

  it('REGRESSION: the current/available legend + node styling classes are unaffected by the new depth/frontier layers', async () => {
    await mount();
    const titleToCircle = (title: string): Element | undefined =>
      Array.from(container.querySelectorAll('circle.node-circle')).find(
        (el) => el.querySelector('title')?.textContent === title,
      );
    expect(classOf(titleToCircle('Sector 1')).split(' ')).toContain('current');
    expect(classOf(titleToCircle('Sector 2')).split(' ')).toContain('available');
  });
});
