/**
 * websocket.ts — onclose branch coverage for WO-RT-EVICTION-SUPERSEDE.
 *
 * A duplicate-connect eviction closes the OLD socket with code 4001 and
 * reason 'superseded' (server side: websocket_service.py connect()). This
 * must NOT be treated as an auth failure — calling reconnectWithRefresh()
 * here would just reconnect and evict the new tab in turn, ping-ponging the
 * eviction forever. Every OTHER 4001 (a genuine auth rejection) must keep
 * the existing refresh-then-reconnect path.
 *
 * reconnectWithRefresh/scheduleReconnect are stubbed (not exercised for
 * real) — this file asserts only which branch onclose takes, not the
 * already-covered internals of those methods.
 *
 * @vitest-environment jsdom
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../apiClient', () => ({
  getAccessToken: vi.fn(() => 'fake-access-token'),
  refreshAccessToken: vi.fn(),
}));

import { websocketService, type WebSocketMessage } from '../websocket';

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

// websocketService is a module-level singleton; cast to reach the private
// members the onclose handler branches on.
const svc = websocketService as unknown as {
  shouldReconnect: boolean;
  reconnectWithRefresh: () => void;
  scheduleReconnect: () => void;
};

describe('WebSocketService onclose — 4001 superseded vs. genuine auth failure', () => {
  beforeEach(() => {
    FakeSocket.instances = [];
    (globalThis as any).WebSocket = FakeSocket;
    vi.spyOn(svc, 'reconnectWithRefresh').mockImplementation(() => {});
    vi.spyOn(svc, 'scheduleReconnect').mockImplementation(() => {});
    websocketService.connect('fake-access-token');
  });

  afterEach(() => {
    websocketService.disconnect();
    vi.restoreAllMocks();
  });

  function latestSocket(): FakeSocket {
    return FakeSocket.instances[FakeSocket.instances.length - 1];
  }

  it('does not reconnect on a 4001/superseded close, and surfaces a connection_superseded event', () => {
    const received: WebSocketMessage[] = [];
    const handler = (m: WebSocketMessage) => received.push(m);
    websocketService.addMessageHandler(handler);

    latestSocket().onclose?.({ code: 4001, reason: 'superseded' });

    expect(svc.reconnectWithRefresh).not.toHaveBeenCalled();
    expect(svc.scheduleReconnect).not.toHaveBeenCalled();
    expect(svc.shouldReconnect).toBe(false);
    expect(received.some((m) => m.type === 'connection_superseded')).toBe(true);

    websocketService.removeMessageHandler(handler);
  });

  it('still treats a plain 4001 (reason != superseded) as an auth failure', () => {
    latestSocket().onclose?.({ code: 4001, reason: 'Invalid authentication token' });

    expect(svc.reconnectWithRefresh).toHaveBeenCalledTimes(1);
    expect(svc.scheduleReconnect).not.toHaveBeenCalled();
  });
});
