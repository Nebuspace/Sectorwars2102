// @vitest-environment jsdom
/**
 * NavigationMap — plotted course overlay (WO-NAV-COURSE-OVERLAY).
 *
 * Draws the plotted autopilot course (ADR-0072) as a polyline over the
 * force-directed nav graph, with a ship marker riding at currentHopIndex,
 * replacing the retired ≤6-chip breadcrumb strip. `sectors` only carries
 * the 1-hop neighborhood, so a multi-hop course reaches beyond it — this
 * pins the chain-injection seam: course.hops[] are spliced in as a
 * connected chain so EVERY hop positions and renders, not just the
 * portion already visible in the local graph. Mirrors GalaxyMap.chart.
 * test.tsx's seam: jsdom + react-dom/client createRoot + act(), no RTL.
 *
 * jsdom has no requestAnimationFrame — NavigationMap's force simulation
 * drives its own recursive rAF loop bounded at 120 frames. The polyfill
 * below invokes the callback synchronously, so the whole simulation
 * settles within the initial mount's act() instead of needing 120 real
 * animation-frame ticks.
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import NavigationMap, { type CourseHopPoint } from '../NavigationMap';

const SECTORS = [
  { id: 1, name: 'Sector 1', type: 'normal', connected_sectors: [2, 3] }, // current
  { id: 2, name: 'Sector 2', type: 'normal', connected_sectors: [1] },
  { id: 3, name: 'Sector 3', type: 'normal', connected_sectors: [1] },
];

// hop[0] (sector 2) is already in the 1-hop `sectors` neighborhood above;
// hops 99/100 are NOT -- they only exist via the course's own chain, so
// their markers rendering at all is the chain-injection proof.
const COURSE: CourseHopPoint[] = [
  { sector_id: 2, name: 'Sector 2' },
  { sector_id: 99, name: 'Distant Outpost' },
  { sector_id: 100, name: 'Far Frontier' },
];

describe('NavigationMap — plotted course overlay', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;
  let rafOrig: unknown;
  let cafOrig: unknown;
  // Queued (not synchronously invoked) rAF callbacks -- see drainRaf below.
  // A synchronous "call cb() immediately" polyfill would recurse through
  // simulate()'s own tail call to requestAnimationFrame(simulate) once per
  // frame (up to 120), blowing the JS call stack; queuing + an iterative
  // drain settles the same simulation via a flat loop instead.
  let rafQueue: FrameRequestCallback[] = [];

  beforeEach(() => {
    rafOrig = (global as any).requestAnimationFrame;
    cafOrig = (global as any).cancelAnimationFrame;
    rafQueue = [];
    (global as any).requestAnimationFrame = (cb: FrameRequestCallback): number => {
      rafQueue.push(cb);
      return rafQueue.length;
    };
    (global as any).cancelAnimationFrame = () => {};

    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  // Iteratively drains queued rAF callbacks until the simulation stops
  // re-queueing itself (settled) or a safety cap is hit -- NavigationMap's
  // force simulation is bounded at 120 frames internally.
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
    vi.clearAllMocks();
  });

  // Lets the trailing `setTimeout(() => setIsSimulating(false), 0)` (a
  // real timer, unrelated to node positions -- see NavigationMap.tsx)
  // fire inside an act() boundary instead of after the test moves on.
  const flush = async () => {
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 10));
    });
  };

  // SVG elements' `.className` is an SVGAnimatedString at runtime (verified
  // against jsdom directly), but lib.dom.d.ts types querySelector's generic
  // Element.className as a plain string -- read the attribute instead so
  // this compiles under both.
  const classOf = (el: Element | null): string => el?.getAttribute('class') || '';

  const mount = async (course: CourseHopPoint[] | null, currentHopIndex = 0) => {
    await act(async () => {
      root.render(
        <NavigationMap
          currentSectorId={1}
          sectors={SECTORS}
          availableMoves={[2, 3]}
          onNavigate={vi.fn()}
          width={440}
          height={300}
          course={course}
          currentHopIndex={currentHopIndex}
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

  it('renders a polyline and per-hop markers for every hop, including ones beyond the 1-hop sectors neighborhood', async () => {
    await mount(COURSE, 0);

    const polyline = container.querySelector('[data-testid="course-polyline"]');
    expect(polyline).toBeTruthy();
    // M <origin> + one L per hop -- 3 hops means 3 "L" segments.
    const d = polyline!.getAttribute('d') || '';
    expect(d.startsWith('M ')).toBe(true);
    expect((d.match(/L /g) || []).length).toBe(COURSE.length);

    // Chain injection: hops 99/100 have no entry in `sectors` at all, so a
    // marker rendering for them proves NavigationMap spliced them into the
    // node graph as a connected chain rather than dropping them.
    expect(container.querySelector('[data-testid="course-hop-marker-2"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="course-hop-marker-99"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="course-hop-marker-100"]')).toBeTruthy();
  });

  it('exposes accessible text-equivalents for the course overlay (a11y revision batch)', async () => {
    await mount(COURSE, 1);

    // The overlay group is discoverable as a labeled image, not silent SVG.
    const overlay = container.querySelector('.course-overlay');
    expect(overlay).toBeTruthy();
    expect(overlay!.getAttribute('role')).toBe('img');
    expect((overlay!.getAttribute('aria-label') || '').length).toBeGreaterThan(0);
    expect(overlay!.getAttribute('aria-label')).toContain(String(COURSE.length));

    // The ship marker carries a <title> text-equivalent (SVG's accessible-
    // name mechanism) naming the current leg, not just a bare glyph.
    const shipMarker = container.querySelector('[data-testid="course-ship-marker"]');
    expect(shipMarker).toBeTruthy();
    const shipTitle = shipMarker!.querySelector('title')?.textContent || '';
    expect(shipTitle.toLowerCase()).toContain('leg');
    expect(shipTitle.toLowerCase()).toContain('of');
  });

  it('sits the ship marker on currentHopIndex=0 (hop still in progress)', async () => {
    await mount(COURSE, 0);

    expect(container.querySelectorAll('[data-testid="course-ship-marker"]').length).toBe(1);

    const hop2 = container.querySelector('[data-testid="course-hop-marker-2"]');
    expect(classOf(hop2)).toContain('current');
    expect(hop2!.parentElement!.querySelector('[data-testid="course-ship-marker"]')).toBeTruthy();

    const hop99 = container.querySelector('[data-testid="course-hop-marker-99"]');
    expect(classOf(hop99)).not.toContain('current');
    expect(classOf(hop99)).not.toContain('done');
  });

  it('advances the ship marker as hops complete', async () => {
    await mount(COURSE, 1);

    const hop2 = container.querySelector('[data-testid="course-hop-marker-2"]');
    expect(classOf(hop2)).toContain('done');

    const hop99 = container.querySelector('[data-testid="course-hop-marker-99"]');
    expect(classOf(hop99)).toContain('current');
    expect(hop99!.parentElement!.querySelector('[data-testid="course-ship-marker"]')).toBeTruthy();

    const hop100 = container.querySelector('[data-testid="course-hop-marker-100"]');
    expect(classOf(hop100)).not.toContain('current');
    expect(classOf(hop100)).not.toContain('done');
  });

  it('clamps the ship marker to the final hop once arrived (index past the last hop)', async () => {
    await mount(COURSE, COURSE.length);

    const hop100 = container.querySelector('[data-testid="course-hop-marker-100"]');
    expect(classOf(hop100)).toContain('current');
    expect(hop100!.parentElement!.querySelector('[data-testid="course-ship-marker"]')).toBeTruthy();

    // Every other hop reads as done -- none of them still hold the marker.
    expect(classOf(container.querySelector('[data-testid="course-hop-marker-2"]'))).toContain('done');
    expect(classOf(container.querySelector('[data-testid="course-hop-marker-99"]'))).toContain('done');
  });

  it('renders no polyline or hop markers when no course is plotted (no regression)', async () => {
    await mount(null);

    expect(container.querySelector('[data-testid="course-polyline"]')).toBeNull();
    expect(container.querySelector('[data-testid="course-ship-marker"]')).toBeNull();
    // The underlying graph still renders normally.
    expect(container.querySelectorAll('.node-circle').length).toBeGreaterThan(0);
  });

  it('renders no overlay for an empty course array', async () => {
    await mount([]);

    expect(container.querySelector('[data-testid="course-polyline"]')).toBeNull();
  });
});
