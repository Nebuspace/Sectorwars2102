import { describe, it, expect, beforeEach } from 'vitest';
import {
  requestTeleprinterPanel,
  subscribeTeleprinterPanelRequest,
  getLatestTeleprinterPanelRequest,
  __resetTeleprinterBusForTests,
} from '../teleprinterBus';

describe('teleprinterBus', () => {
  beforeEach(() => {
    __resetTeleprinterBusForTests();
  });

  it('latches and notifies subscribers on panel open', () => {
    const seen: boolean[] = [];
    const unsub = subscribeTeleprinterPanelRequest((req) => {
      seen.push(req.open);
    });
    requestTeleprinterPanel(true);
    expect(seen).toEqual([true]);
    expect(getLatestTeleprinterPanelRequest()?.open).toBe(true);
    unsub();
  });

  it('bumps requestId so repeat opens still fire', () => {
    const ids: number[] = [];
    subscribeTeleprinterPanelRequest((req) => ids.push(req.requestId));
    requestTeleprinterPanel(true);
    requestTeleprinterPanel(true);
    expect(ids).toHaveLength(2);
    expect(ids[1]).toBeGreaterThan(ids[0]);
  });
});
