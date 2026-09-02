// @vitest-environment jsdom
/**
 * LEG-3786 Soft-ORDER — AutopilotContext plot/engage typeErrorHonesty.
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

let latestState: AutopilotContextValue | null = null;

function Harness() {
  latestState = useAutopilot();
  return null;
}

const hop = (sectorId: number): CourseHop => ({
  sector_id: sectorId,
  name: 'Beta',
  turn_cost: 1,
  visited: false,
  safety_rating: 0.9,
  via_tunnel: false,
});

const reachable = (hops: CourseHop[]): CourseReachable => ({
  success: true,
  reachable: true,
  target_sector_id: hops[hops.length - 1]?.sector_id ?? 0,
  hops,
  total_turns: hops.reduce((n, h) => n + h.turn_cost, 0),
});

let container: HTMLElement;
let root: ReturnType<typeof createRoot>;

const mountProvider = async () => {
  await act(async () => {
    root.render(
      <AutopilotProvider>
        <Harness />
      </AutopilotProvider>,
    );
  });
};

describe('formatAutopilotMovementError TypeError densify (LEG-3786)', () => {
  const fallback = 'Movement failed';

  it.each([
    ['TypeError', new TypeError('Failed to fetch')],
    ['Network Error', new Error('Network Error')],
    ['Failed to fetch', new Error('Failed to fetch')],
  ])('falls back on %s network collapse', (_label, err) => {
    const text = formatAutopilotMovementError(err, fallback);
    expect(text).toBe(fallback);
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
    expect(text).not.toMatch(/Network Error/i);
  });

  it('preserves server detail for structured API errors', () => {
    const err = { response: { data: { detail: 'Insufficient fuel' } } };
    expect(formatAutopilotMovementError(err, fallback)).toBe('Insufficient fuel');
  });
});

describe('AutopilotContext plotCourse transport collapse densify (LEG-3786)', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    latestState = null;
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
    useGameMock.mockReturnValue({ moveToSector: moveToSectorMock, playerState: { id: 'p1' } });
    navPlotMock.mockReset();
    moveToSectorMock.mockReset();
    requestWarpDepartMock.mockReset();
  });

  afterEach(async () => {
    await act(async () => {
      root.unmount();
    });
    container.remove();
    vi.useRealTimers();
  });

  it.each([
    ['TypeError', new TypeError('Failed to fetch')],
    ['Network Error', new Error('Network Error')],
    ['Failed to fetch', new Error('Failed to fetch')],
  ])('plotCourse %s returns null idle without raw transport tokens', async (_label, err) => {
    navPlotMock.mockRejectedValue(err);
    await mountProvider();

    let returned: unknown = 'not-set';
    await act(async () => {
      returned = await latestState!.plotCourse(2);
    });

    expect(returned).toBeNull();
    expect(latestState!.status).toBe('idle');
    expect(latestState!.pauseReason).toBeNull();
    expect(latestState!.course).toBeNull();
  });
});

describe('AutopilotContext engage transport collapse densify (LEG-3786)', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    latestState = null;
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
    useGameMock.mockReturnValue({ moveToSector: moveToSectorMock, playerState: { id: 'p1' } });
    navPlotMock.mockReset();
    moveToSectorMock.mockReset();
    requestWarpDepartMock.mockReset();
  });

  afterEach(async () => {
    await act(async () => {
      root.unmount();
    });
    container.remove();
    vi.useRealTimers();
  });

  const setUpEngagedHop = async (sectorId = 5) => {
    navPlotMock.mockResolvedValue(reachable([hop(sectorId)]));
    await mountProvider();
    await act(async () => {
      await latestState!.plotCourse(sectorId);
    });
    await act(async () => {
      latestState!.engage();
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(WARP_TURN_MS);
    });
  };

  it.each([
    ['TypeError', new TypeError('Failed to fetch')],
    ['Network Error', new Error('Network Error')],
    ['Failed to fetch', new Error('Failed to fetch')],
  ])('engage %s surfaces Movement failed without raw transport text', async (_label, err) => {
    moveToSectorMock.mockRejectedValue(err);
    await setUpEngagedHop();
    expect(latestState!.status).toBe('paused');
    expect(latestState!.pauseReason).toBe('Movement failed');
    expect(latestState!.pauseReason).not.toMatch(/Failed to fetch/i);
    expect(latestState!.pauseReason).not.toMatch(/TypeError/i);
    expect(latestState!.pauseReason).not.toMatch(/Network Error/i);
  });
});
