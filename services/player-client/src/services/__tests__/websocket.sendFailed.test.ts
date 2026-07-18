/**
 * websocket.ts — send() failure feedback (WO-UI2-FLIGHT-FEEL UX nit).
 *
 * send() previously only console.warn'd on a dead uplink — every caller
 * failed silently from the player's point of view. Pins the new
 * `send_failed` synthetic event (notifyHandlers' own pub/sub, same idiom as
 * link_status) that WebSocketContext.tsx turns into a visible toast.
 * Reuses the FakeSocket harness websocket.linkStatus.test.ts established.
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

import { websocketService } from '../websocket';

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
  close(): void {}
  send(): void {}
}

const svc = websocketService as unknown as {
  didAuthRefresh: boolean;
  hadOpen: boolean;
  refreshingAuth: boolean;
  reconnectAttempts: number;
};

function latestSocket(): FakeSocket {
  return FakeSocket.instances[FakeSocket.instances.length - 1];
}

function openLatest(): void {
  latestSocket().readyState = FakeSocket.OPEN;
  latestSocket().onopen?.();
}

describe('WebSocketService — send() failure feedback', () => {
  beforeEach(() => {
    FakeSocket.instances = [];
    getAccessTokenMock.mockReturnValue('fake-access-token');
    refreshAccessTokenMock.mockReset();
    (globalThis as any).WebSocket = FakeSocket;
    svc.didAuthRefresh = false;
    svc.hadOpen = false;
    svc.refreshingAuth = false;
    svc.reconnectAttempts = 0;
    vi.spyOn(console, 'warn').mockImplementation(() => {});
  });

  afterEach(() => {
    websocketService.disconnect();
    vi.restoreAllMocks();
  });

  it('fires onSendFailed with the message type when disconnected, and returns false', () => {
    const seen: string[] = [];
    websocketService.onSendFailed((messageType) => seen.push(messageType));

    const ok = websocketService.send({ type: 'chat_message', content: 'hi' } as any);

    expect(ok).toBe(false);
    expect(seen).toEqual(['chat_message']);
  });

  it('does NOT fire onSendFailed for a successful send while connected', () => {
    const seen: string[] = [];
    websocketService.onSendFailed((messageType) => seen.push(messageType));

    websocketService.connect('fake-access-token');
    openLatest();

    const ok = websocketService.send({ type: 'heartbeat' } as any);

    expect(ok).toBe(true);
    expect(seen).toEqual([]);
  });

  it('unsubscribe stops further callbacks', () => {
    const seen: string[] = [];
    const unsubscribe = websocketService.onSendFailed((messageType) => seen.push(messageType));
    unsubscribe();

    websocketService.send({ type: 'aria_chat' } as any);

    expect(seen).toEqual([]);
  });
});
