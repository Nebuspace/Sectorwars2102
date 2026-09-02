// @vitest-environment jsdom
/**
 * LEG-3786 Soft-ORDER — AutopilotContext plot/engage TypeError densify.
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { CourseHop, CourseReachable } from '../../services/api';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const { useGameMock, moveToSectorMock, navPlotMock, requestWarpDepartMock } = vi.hoisted(() => ({
  useGameMock: vi.fn(),
  moveToSectorMock: vi.fn(),
  navPlotMock: vi.fn(),
  requestWarpDepartMock: vi.fn(),
}));

vi.mock('../GameContext', () => ({
  useGame: useGameMock,
}));

vi.mock('../../services/api', () => ({
  navAPI: { plot: navPlotMock },
}));

vi.mock('../../services/warpCinematicBus', () => ({
  requestWarpDepart: requestWarpDepartMock,
  WARP_TURN_MS: 3000,
  WARP_ARRIVE_MS: 3400,
}));

import {
  AutopilotProvider,
  formatAutopilotMovementError,
  useAutopilot,
  type AutopilotContextValue,
} from '../AutopilotContext';

const WARP_TURN_MS = 3000;
const PLOT_FALLBACK = 'Course plot failed. Please try again.';
const MOVE_FALLBACK = 'Movement failed';

let latestState: AutopilotContextValue | null = null;

function Harness() {
  latestState = useAutopilot();
  return null;
}

const hop = (over: Partial<CourseHop> = {}): CourseHop => ({
  sector_id: 2,
  name: 'Beta',
  turn_cost: 1,
  visited: false,
  safety_rating: 0.9,
  via_tunnel: false,
  ...over,
});

const reachable = (hops: CourseHop[]): CourseReachable => ({
  success: true,
  reachable: true,
  target_sector_id: hops[hops.length - 1]?.sector_id ?? 0,
  hops,
  total_turns: hops.reduce((n, h) => n + h.turn_cost, 0),
});

describe('formatAutopilotMovementError typeErrorHonesty (LEG-3786)', () => {
  it.each([
    ['TypeError', new TypeError('Failed to fetch')],
    ['Network Error', new Error('Network Error')],
    ['Failed to fetch', new Error('Failed to fetch')],
  ])('collapses %s to fallback without raw transport text', (_label, err) => {
    const text = formatAutopilotMovementError(err, MOVE_FALLBACK);
    expect(text).toBe(MOVE_FALLBACK);
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
    expect(text).not.toMatch(/Network Error/i);
  });
});

describe('formatAutopilotMovementError 403/429 densify (LEG-4022)', () => {
  const apiRequestError = (status: number, message?: string) => {
    const err = new Error(message ?? `API Error: ${status}`);
    (err as { status?: number }).status = status;
    return err;
  };

  it('surfaces 403/429 without raw status codes', () => {
    expect(formatAutopilotMovementError(apiRequestError(403), MOVE_FALLBACK)).toMatch(/permission/i);
    expect(formatAutopilotMovementError(apiRequestError(403, 'move_denied'), MOVE_FALLBACK)).toBe(
      'move_denied',
    );
    expect(formatAutopilotMovementError(apiRequestError(429), MOVE_FALLBACK)).toMatch(/rate limit/i);
    expect(formatAutopilotMovementError(apiRequestError(429), MOVE_FALLBACK)).not.toMatch(/\b429\b/);
    expect(formatAutopilotMovementError(apiRequestError(403), MOVE_FALLBACK)).not.toMatch(/TypeError/i);
  });
});

describe('AutopilotContext plot/engage transport collapse (LEG-3786)', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;

  beforeEach(() => {
    vi.useFakeTimers();
    latestState = null;
    useGameMock.mockReturnValue({
      moveToSector: moveToSectorMock,
      playerState: { id: 'p1' },
    });
    moveToSectorMock.mockReset();
    navPlotMock.mockReset();
    requestWarpDepartMock.mockReset();
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(async () => {
    vi.useRealTimers();
    await act(async () => {
      root.unmount();
    });
    container.remove();
  });

  async function mountProvider() {
    await act(async () => {
      root.render(
        <AutopilotProvider>
          <Harness />
        </AutopilotProvider>,
      );
    });
  }

  it('plotCourse TypeError sets paused pauseReason without raw transport text', async () => {
    navPlotMock.mockRejectedValue(new TypeError('Failed to fetch'));
    await mountProvider();

    await act(async () => {
      await latestState!.plotCourse(12);
    });

    expect(latestState!.status).toBe('paused');
    expect(latestState!.pauseReason).toBe(PLOT_FALLBACK);
    expect(latestState!.pauseReason).not.toMatch(/Failed to fetch/i);
    expect(latestState!.pauseReason).not.toMatch(/TypeError/i);
  });

  it('plotCourse Network Error sets paused pauseReason without raw transport text', async () => {
    navPlotMock.mockRejectedValue(new Error('Network Error'));
    await mountProvider();

    await act(async () => {
      await latestState!.plotCourse(12);
    });

    expect(latestState!.pauseReason).toBe(PLOT_FALLBACK);
    expect(latestState!.pauseReason).not.toMatch(/Network Error/i);
  });

  it('engage moveToSector TypeError sets pauseReason without raw transport text', async () => {
    const h = hop({ sector_id: 5 });
    navPlotMock.mockResolvedValue(reachable([h]));
    moveToSectorMock.mockRejectedValue(new TypeError('Failed to fetch'));
    await mountProvider();

    await act(async () => {
      await latestState!.plotCourse(5);
    });
    await act(async () => {
      latestState!.engage();
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(WARP_TURN_MS);
    });

    expect(latestState!.status).toBe('paused');
    expect(latestState!.pauseReason).toBe(MOVE_FALLBACK);
    expect(latestState!.pauseReason).not.toMatch(/Failed to fetch/i);
    expect(latestState!.pauseReason).not.toMatch(/TypeError/i);
  });
});
