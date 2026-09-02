// @vitest-environment jsdom
/**
 * AutopilotContext — ADR-0072 Phase 1 client-side autopilot status
 * machine (plotCourse/engage/abort, the per-hop cinematic-cadence timer
 * chain). Follows the useAnnunciatorState.test.tsx harness convention
 * (no @testing-library/react in this repo): a host component renders
 * INSIDE the real AutopilotProvider (the provider itself is under test,
 * not mocked) and stashes useAutopilot()'s return into a module-level
 * slot on every render. `useGame` is mocked for moveToSector/playerState;
 * navAPI.plot and the warpCinematicBus helpers are mocked; fake timers
 * (`vi.useFakeTimers()` + `vi.advanceTimersByTimeAsync`) drive the
 * WARP_TURN_MS / AUTOPILOT_POST_ARRIVE_MS cadence deterministically.
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { CourseHop, CourseReachable, CourseUnreachable } from '../../services/api';

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
const AUTOPILOT_POST_ARRIVE_MS = 3400;

let latestState: AutopilotContextValue | null = null;

function Harness() {
  latestState = useAutopilot();
  return null;
}

let container: HTMLElement;
let root: ReturnType<typeof createRoot>;

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

const unreachable = (over: Partial<CourseUnreachable> = {}): CourseUnreachable => ({
  success: true,
  reachable: false,
  target_sector_id: 99,
  nearest_known: null,
  reason: 'no_route',
  ...over,
});

const mountProvider = async () => {
  await act(async () => {
    root.render(
      <AutopilotProvider>
        <Harness />
      </AutopilotProvider>
    );
  });
};

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

describe('AutopilotContext — plotCourse', () => {
  it('sets course + lastPlot on a reachable plot, ending back at idle', async () => {
    const plot = reachable([hop()]);
    navPlotMock.mockResolvedValue(plot);
    await mountProvider();

    let returned: unknown;
    await act(async () => {
      returned = await latestState!.plotCourse(2);
    });

    expect(navPlotMock).toHaveBeenCalledWith(2, 'min_time');
    expect(returned).toEqual(plot);
    expect(latestState!.course).toEqual(plot);
    expect(latestState!.lastPlot).toEqual(plot);
    expect(latestState!.status).toBe('idle');
    expect(latestState!.currentHopIndex).toBe(0);
  });

  it('surfaces an unreachable plot via lastPlot without setting a course', async () => {
    const plot = unreachable({ reason: 'uncharted' });
    navPlotMock.mockResolvedValue(plot);
    await mountProvider();

    await act(async () => {
      await latestState!.plotCourse(99);
    });

    expect(latestState!.lastPlot).toEqual(plot);
    expect(latestState!.course).toBeNull();
    expect(latestState!.status).toBe('idle');
  });

  it('sets paused with pauseReason and returns null when the plot API call throws', async () => {
    navPlotMock.mockRejectedValue(new Error('network down'));
    await mountProvider();

    let returned: unknown = 'not-set';
    await act(async () => {
      returned = await latestState!.plotCourse(2);
    });

    expect(returned).toBeNull();
    expect(latestState!.status).toBe('paused');
    expect(latestState!.pauseReason).toBe('network down');
  });
});

describe('AutopilotContext — engage guards', () => {
  it('no-ops when there is no course', async () => {
    await mountProvider();
    await act(async () => {
      latestState!.engage();
    });
    expect(moveToSectorMock).not.toHaveBeenCalled();
    expect(latestState!.status).toBe('idle');
  });

  it('no-ops when the course is already fully consumed', async () => {
    navPlotMock.mockResolvedValue(reachable([hop({ sector_id: 2 })]));
    moveToSectorMock.mockResolvedValue({ success: true, new_sector_id: 2 });
    await mountProvider();
    await act(async () => {
      await latestState!.plotCourse(2);
    });
    await act(async () => {
      latestState!.engage();
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(WARP_TURN_MS);
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(AUTOPILOT_POST_ARRIVE_MS + 800);
    });
    expect(latestState!.status).toBe('idle'); // single-hop course fully consumed + auto-cleared

    moveToSectorMock.mockClear();
    await act(async () => {
      latestState!.engage(); // hopIndexRef is back at 0 but courseRef was cleared to null too
    });
    expect(moveToSectorMock).not.toHaveBeenCalled();
  });
});

describe('AutopilotContext — single-hop engage happy path', () => {
  it('arms the cinematic, waits WARP_TURN_MS, moves, then arrives and auto-clears after the post-arrive hold', async () => {
    const h = hop({ sector_id: 5 });
    navPlotMock.mockResolvedValue(reachable([h]));
    moveToSectorMock.mockResolvedValue({ success: true, new_sector_id: 5 });
    await mountProvider();

    await act(async () => {
      await latestState!.plotCourse(5);
    });
    await act(async () => {
      latestState!.engage();
    });
    expect(latestState!.status).toBe('engaged');
    expect(requestWarpDepartMock).toHaveBeenCalledWith(5);
    expect(moveToSectorMock).not.toHaveBeenCalled(); // still turning

    await act(async () => {
      await vi.advanceTimersByTimeAsync(WARP_TURN_MS);
    });
    expect(moveToSectorMock).toHaveBeenCalledWith(5);

    // Hop resolved -> nextIdx (1) >= hops.length (1) -> holds AUTOPILOT_POST_ARRIVE_MS then 'arrived'
    await act(async () => {
      await vi.advanceTimersByTimeAsync(AUTOPILOT_POST_ARRIVE_MS);
    });
    expect(latestState!.status).toBe('arrived');
    expect(latestState!.currentHopIndex).toBe(1);

    // 800ms after arrival the course/status auto-resets for the next plot.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(800);
    });
    expect(latestState!.status).toBe('idle');
    expect(latestState!.course).toBeNull();
    expect(latestState!.currentHopIndex).toBe(0);
  });
});

describe('AutopilotContext — multi-hop engage', () => {
  it('chains into the next hop automatically after the post-arrive hold, without a second engage() call', async () => {
    const hops = [hop({ sector_id: 5 }), hop({ sector_id: 6 })];
    navPlotMock.mockResolvedValue(reachable(hops));
    moveToSectorMock
      .mockResolvedValueOnce({ success: true, new_sector_id: 5 })
      .mockResolvedValueOnce({ success: true, new_sector_id: 6 });
    await mountProvider();

    await act(async () => {
      await latestState!.plotCourse(6);
    });
    await act(async () => {
      latestState!.engage();
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(WARP_TURN_MS);
    });
    expect(moveToSectorMock).toHaveBeenNthCalledWith(1, 5);
    expect(latestState!.currentHopIndex).toBe(1);
    expect(latestState!.status).toBe('engaged'); // not yet arrived -- one more hop queued

    // Hold, then the second hop's own turn-delay, then its move call.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(AUTOPILOT_POST_ARRIVE_MS);
    });
    expect(requestWarpDepartMock).toHaveBeenCalledWith(6);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(WARP_TURN_MS);
    });
    expect(moveToSectorMock).toHaveBeenNthCalledWith(2, 6);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(AUTOPILOT_POST_ARRIVE_MS);
    });
    expect(latestState!.status).toBe('arrived');
    expect(latestState!.currentHopIndex).toBe(2);
  });
});

describe('AutopilotContext — hop-resolution branches', () => {
  const setUpEngagedHop = async (sectorId = 5) => {
    navPlotMock.mockResolvedValue(reachable([hop({ sector_id: sectorId })]));
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

  it('pauses with the server message on a game-logic refusal (success:false), keeping the course', async () => {
    moveToSectorMock.mockResolvedValue({ success: false, message: 'Insufficient turns' });
    await setUpEngagedHop();
    expect(latestState!.status).toBe('paused');
    expect(latestState!.pauseReason).toBe('Insufficient turns');
    expect(latestState!.course).not.toBeNull();
  });

  it('falls back to a generic refusal message when none is provided', async () => {
    moveToSectorMock.mockResolvedValue({ success: false });
    await setUpEngagedHop();
    expect(latestState!.pauseReason).toBe('Movement refused');
  });

  it('pauses and invalidates the course on a bounce (landed sector != planned hop)', async () => {
    moveToSectorMock.mockResolvedValue({ success: true, new_sector_id: 9 });
    await setUpEngagedHop(5);
    expect(latestState!.status).toBe('paused');
    expect(latestState!.pauseReason).toBe('Expected sector 5 but arrived at 9 — course invalidated');
    expect(latestState!.course).toBeNull();
  });

  it('pauses on an encounter without invalidating the course', async () => {
    moveToSectorMock.mockResolvedValue({
      success: true,
      new_sector_id: 5,
      encounters: [{ id: 'e1' }],
    });
    await setUpEngagedHop(5);
    expect(latestState!.status).toBe('paused');
    expect(latestState!.pauseReason).toBe('Encounter detected — autopilot paused');
    expect(latestState!.course).not.toBeNull();
  });

  it('pauses with the API error detail when moveToSector throws', async () => {
    moveToSectorMock.mockRejectedValue({ response: { data: { detail: 'Ship destroyed mid-transit' } } });
    await setUpEngagedHop();
    expect(latestState!.status).toBe('paused');
    expect(latestState!.pauseReason).toBe('Ship destroyed mid-transit');
  });

  it('falls back to a generic message when a thrown error carries none', async () => {
    moveToSectorMock.mockRejectedValue({});
    await setUpEngagedHop();
    expect(latestState!.pauseReason).toBe('Movement failed');
  });

  it('densifies TypeError network collapse without raw Failed to fetch / TypeError in pauseReason (LEG-3332)', async () => {
    moveToSectorMock.mockRejectedValue(new TypeError('Failed to fetch'));
    await setUpEngagedHop();
    expect(latestState!.status).toBe('paused');
    expect(latestState!.pauseReason).toBe('Movement failed');
    expect(latestState!.pauseReason).not.toMatch(/TypeError/i);
    expect(latestState!.pauseReason).not.toMatch(/Failed to fetch/i);
  });
});

describe('formatAutopilotMovementError TypeError densify (LEG-3332)', () => {
  it('falls back on TypeError network collapse', () => {
    const text = formatAutopilotMovementError(new TypeError('Failed to fetch'));
    expect(text).toBe('Movement failed');
    expect(text).not.toMatch(/TypeError/i);
    expect(text).not.toMatch(/Failed to fetch/i);
  });

  it('preserves server detail for structured API errors', () => {
    const err = { response: { data: { detail: 'Insufficient fuel' } } };
    expect(formatAutopilotMovementError(err)).toBe('Insufficient fuel');
  });
});

describe('formatAutopilotMovementError Error Network Error densify (LEG-3402)', () => {
  it('falls back on Error Network Error without raw Network Error', () => {
    const text = formatAutopilotMovementError(new Error('Network Error'));
    expect(text).toBe('Movement failed');
    expect(text).not.toMatch(/Network Error/i);
  });
});

describe('AutopilotContext — abort', () => {
  it('pauses with the given reason when a course is set', async () => {
    navPlotMock.mockResolvedValue(reachable([hop()]));
    await mountProvider();
    await act(async () => {
      await latestState!.plotCourse(2);
    });
    await act(async () => {
      latestState!.abort('manual override');
    });
    expect(latestState!.status).toBe('paused');
    expect(latestState!.pauseReason).toBe('manual override');
  });

  it('returns to idle with no pause reason when there is no course', async () => {
    await mountProvider();
    await act(async () => {
      latestState!.abort('irrelevant');
    });
    expect(latestState!.status).toBe('idle');
    expect(latestState!.pauseReason).toBeNull();
  });

  it('stops an in-flight hop chain from advancing further', async () => {
    navPlotMock.mockResolvedValue(reachable([hop({ sector_id: 5 }), hop({ sector_id: 6 })]));
    let resolveMove!: (v: unknown) => void;
    moveToSectorMock.mockReturnValue(new Promise((res) => { resolveMove = res; }));
    await mountProvider();
    await act(async () => {
      await latestState!.plotCourse(6);
    });
    await act(async () => {
      latestState!.engage();
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(WARP_TURN_MS);
    });
    expect(moveToSectorMock).toHaveBeenCalledTimes(1);

    await act(async () => {
      latestState!.abort('pilot cancelled');
    });
    expect(latestState!.status).toBe('paused');

    // The in-flight move now resolves -- the cancelled chain must not
    // clobber the abort's own paused/reason state or advance the hop.
    await act(async () => {
      resolveMove({ success: true, new_sector_id: 5 });
      await Promise.resolve();
    });
    expect(latestState!.status).toBe('paused');
    expect(latestState!.pauseReason).toBe('pilot cancelled');
    expect(latestState!.currentHopIndex).toBe(0);
  });
});

describe('AutopilotContext — plotCourse cancels a running engage', () => {
  it('a fresh plotCourse call aborts any in-flight hop chain', async () => {
    navPlotMock.mockResolvedValueOnce(reachable([hop({ sector_id: 5 }), hop({ sector_id: 6 })]));
    let resolveMove!: (v: unknown) => void;
    moveToSectorMock.mockReturnValue(new Promise((res) => { resolveMove = res; }));
    await mountProvider();
    await act(async () => {
      await latestState!.plotCourse(6);
    });
    await act(async () => {
      latestState!.engage();
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(WARP_TURN_MS);
    });
    expect(moveToSectorMock).toHaveBeenCalledTimes(1);

    navPlotMock.mockResolvedValueOnce(reachable([hop({ sector_id: 42 })]));
    await act(async () => {
      await latestState!.plotCourse(42);
    });
    expect(latestState!.status).toBe('idle');

    // The stale in-flight move resolving now must not resurrect the old
    // (cancelled) chain's state.
    await act(async () => {
      resolveMove({ success: true, new_sector_id: 5 });
      await Promise.resolve();
    });
    expect(latestState!.status).toBe('idle');
    expect(latestState!.course).toEqual(reachable([hop({ sector_id: 42 })]));
  });
});
