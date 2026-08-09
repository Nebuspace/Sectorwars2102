/**
 * deckNavBus — latched tactical page requests + monotonic request ids.
 */
import { afterEach, describe, expect, it, vi } from 'vitest';
import {
  __resetDeckNavBusForTests,
  getLatestTacticalPageRequest,
  requestTacticalPage,
  subscribeTacticalPageRequest,
} from '../deckNavBus';

describe('deckNavBus', () => {
  afterEach(() => {
    __resetDeckNavBusForTests();
  });

  it('starts with no latched request', () => {
    expect(getLatestTacticalPageRequest()).toBeNull();
  });

  it('latches requests and notifies subscribers with monotonic ids', () => {
    const seen: { page: string; requestId: number }[] = [];
    const unsub = subscribeTacticalPageRequest((req) => seen.push(req));

    requestTacticalPage('threat');
    requestTacticalPage('target');
    requestTacticalPage('target'); // same page still bumps id

    expect(seen.map((r) => r.page)).toEqual(['threat', 'target', 'target']);
    expect(seen[0].requestId).toBe(1);
    expect(seen[1].requestId).toBe(2);
    expect(seen[2].requestId).toBe(3);
    expect(getLatestTacticalPageRequest()).toEqual({ page: 'target', requestId: 3 });

    unsub();
    requestTacticalPage('threat');
    expect(seen).toHaveLength(3);
    expect(getLatestTacticalPageRequest()?.page).toBe('threat');
  });

  it('reset clears latch and restarts request ids', () => {
    requestTacticalPage('threat');
    __resetDeckNavBusForTests();
    expect(getLatestTacticalPageRequest()).toBeNull();

    const fn = vi.fn();
    subscribeTacticalPageRequest(fn);
    requestTacticalPage('target');
    expect(fn).toHaveBeenCalledWith({ page: 'target', requestId: 1 });
  });
});
