/**
 * websocket.ts — linkStatus event machinery (WO-PUX-UPLINK-HUD).
 *
 * Pins the coarse 'up' | 'reconnecting' | 'down' projection the reconnect
 * state machine emits, without changing that machine's own behavior (see
 * websocket.eviction.test.ts / websocket.quantumHarvest.test.ts for the
 * unchanged backoff/eviction coverage). Reuses the FakeSocket + onclose-
 * driven harness those files established.
 *
 * @vitest-environment jsdom
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const getAccessTokenMock = vi.fn(() => 'fake-access-token');
const refreshAccessTokenMock = vi.fn();

vi.mock('../apiClient', () => ({
  getAccessToken: () => getAccessTokenMock(),
  refreshAccessToken: () => refreshAccessTokenMock(),
}));

import { websocketService, type LinkStatus } from '../websocket';

class FakeSocket {
  static instances: FakeSocket[] = [];
  static readonly CONNECTING = 0;
  static readonly OPEN = 1;
  static readonly CLOSING = 2;
  static readonly CLOSED = 3;

  readyState = FakeSocket.CONNECTING;
  onopen: (() => void) | null = null;
  onclose: ((event: { code: number; reason: string }) => void) | null = null;
  onmessage: ((event: { data: string }) => void) | null = null;
  onerror: ((event: unknown) => void) | null = null;

  constructor(public url: string) {
    FakeSocket.instances.push(this);
  }
  close(): void {
    /* the real browser close() is not under test here */
  }
  send(): void {}
}

const svc = websocketService as unknown as {
  shouldReconnect: boolean;
  reconnectAttempts: number;
  maxReconnectAttempts: number;
  didAuthRefresh: boolean;
  hadOpen: boolean;
  refreshingAuth: boolean;
};

function latestSocket(): FakeSocket {
  return FakeSocket.instances[FakeSocket.instances.length - 1];
}

function openLatest(): void {
  latestSocket().readyState = FakeSocket.OPEN;
  latestSocket().onopen?.();
}

describe('WebSocketService — linkStatus projection', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    FakeSocket.instances = [];
    getAccessTokenMock.mockReturnValue('fake-access-token');
    refreshAccessTokenMock.mockReset();
    (globalThis as any).WebSocket = FakeSocket;
    // Full isolation between tests -- these otherwise only self-heal via a
    // subsequent successful open, which not every test below reaches.
    svc.didAuthRefresh = false;
    svc.hadOpen = false;
    svc.refreshingAuth = false;
    svc.reconnectAttempts = 0;
  });

  afterEach(() => {
    websocketService.disconnect();
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  function captureStatuses(): LinkStatus[] {
    const seen: LinkStatus[] = [];
    websocketService.onLinkStatus((status) => seen.push(status));
    return seen;
  }

  it('starts "down" before any connect attempt', () => {
    // A fresh module-level singleton would start 'down'; after the previous
    // test's afterEach disconnect(), it is already reset to 'down' too.
    expect(websocketService.getLinkStatus()).toBe('down');
  });

  it('goes reconnecting -> up on a successful first connect', () => {
    const seen = captureStatuses();
    websocketService.connect('fake-access-token');
    expect(websocketService.getLinkStatus()).toBe('reconnecting');

    openLatest();
    expect(websocketService.getLinkStatus()).toBe('up');
    expect(seen).toEqual(['reconnecting', 'up']);
  });

  it('goes up -> reconnecting on a clean drop after a successful open (network blip)', () => {
    websocketService.connect('fake-access-token');
    openLatest();
    const seen = captureStatuses();

    latestSocket().onclose?.({ code: 1006, reason: '' });

    expect(websocketService.getLinkStatus()).toBe('reconnecting');
    expect(seen).toEqual(['reconnecting']);
  });

  it('goes down on a 4001/superseded close (evicted by another tab)', () => {
    websocketService.connect('fake-access-token');
    openLatest();
    const seen = captureStatuses();

    latestSocket().onclose?.({ code: 4001, reason: 'superseded' });

    expect(websocketService.getLinkStatus()).toBe('down');
    expect(seen).toEqual(['down']);
    expect(svc.shouldReconnect).toBe(false);
  });

  it('goes down on a 4002 close (player profile not found -- not retryable)', () => {
    websocketService.connect('fake-access-token');
    openLatest();
    const seen = captureStatuses();

    latestSocket().onclose?.({ code: 4002, reason: 'no_player' });

    expect(websocketService.getLinkStatus()).toBe('down');
    expect(seen).toEqual(['down']);
  });

  it('stays reconnecting through the refresh-then-reconnect window (never flips to down mid-refresh)', async () => {
    let resolveRefresh: (token: string | null) => void = () => {};
    refreshAccessTokenMock.mockReturnValue(
      new Promise<string | null>((resolve) => { resolveRefresh = resolve; })
    );

    // Handshake never opened (hadOpen stays false) -> authSuspect path.
    websocketService.connect('fake-access-token');
    latestSocket().onclose?.({ code: 1006, reason: '' });
    // The browser socket itself has closed -- mark it so the eventual
    // openSocket() retry (below) doesn't hit its own already-CONNECTING/OPEN
    // re-entry guard against this stale instance.
    latestSocket().readyState = FakeSocket.CLOSED;

    expect(websocketService.getLinkStatus()).toBe('reconnecting');

    resolveRefresh('new-fake-token');
    await Promise.resolve();
    await Promise.resolve();

    // Refresh succeeded and a new backoff timer is armed -- still actively
    // retrying, not a terminal state.
    expect(websocketService.getLinkStatus()).toBe('reconnecting');

    vi.advanceTimersByTime(2000);
    expect(websocketService.getLinkStatus()).toBe('reconnecting');
    openLatest();
    expect(websocketService.getLinkStatus()).toBe('up');
  });

  it('goes down when the refresh token is also dead (session_expired)', async () => {
    refreshAccessTokenMock.mockResolvedValue(null);

    websocketService.connect('fake-access-token');
    latestSocket().onclose?.({ code: 1006, reason: '' });
    expect(websocketService.getLinkStatus()).toBe('reconnecting');

    await Promise.resolve();
    await Promise.resolve();

    expect(websocketService.getLinkStatus()).toBe('down');
    expect(svc.shouldReconnect).toBe(false);
  });

  it('goes down once max reconnection attempts are exhausted', () => {
    // scheduleReconnect() checks the attempt budget synchronously at call
    // time (before arming a timer), so the boundary is reachable directly
    // via the private counter -- the climb-to-the-boundary route (repeated
    // clean-drop backoff cycles) is already covered by the "clean drop"
    // case above; this test isolates the exhaustion branch itself.
    websocketService.connect('fake-access-token');
    openLatest();
    svc.reconnectAttempts = svc.maxReconnectAttempts;

    latestSocket().onclose?.({ code: 1006, reason: '' });

    expect(websocketService.getLinkStatus()).toBe('down');
  });

  it('disconnect() during a pending backoff wait (no live ws yet) sets down immediately', () => {
    websocketService.connect('fake-access-token');
    openLatest();
    latestSocket().onclose?.({ code: 1006, reason: '' }); // schedules a reconnect timer
    expect(websocketService.getLinkStatus()).toBe('reconnecting');

    websocketService.disconnect();
    expect(websocketService.getLinkStatus()).toBe('down');

    // The cancelled timer must not resurrect a socket / status flip later.
    const countBefore = FakeSocket.instances.length;
    vi.advanceTimersByTime(30000);
    expect(FakeSocket.instances.length).toBe(countBefore);
    expect(websocketService.getLinkStatus()).toBe('down');
  });
});
