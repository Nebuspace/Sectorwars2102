/**
 * marketStream.ts — coverage for WO-RT-MARKET-STREAM-CLIENT's exported
 * seam: MarketStreamService. Mirrors websocket.eviction.test.ts's
 * FakeSocket + jsdom-pragma pattern (no RTL, no new deps) — a real
 * WebSocket is never opened.
 *
 * @vitest-environment jsdom
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../apiClient', () => ({
  getAccessToken: vi.fn(() => 'fake-access-token'),
  refreshAccessToken: vi.fn(),
}));

import { MarketStreamService, type MarketStreamUpdateMessage } from '../marketStream';

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
  closeCalls: Array<{ code?: number; reason?: string }> = [];

  constructor(public url: string) {
    FakeSocket.instances.push(this);
  }
  close(code?: number, reason?: string): void {
    this.closeCalls.push({ code, reason });
  }
  send(): void {}
}

function latestSocket(): FakeSocket {
  return FakeSocket.instances[FakeSocket.instances.length - 1];
}

describe('MarketStreamService', () => {
  let service: MarketStreamService;

  beforeEach(() => {
    FakeSocket.instances = [];
    (globalThis as any).WebSocket = FakeSocket;
    service = new MarketStreamService();
  });

  afterEach(() => {
    service.disconnect();
    vi.restoreAllMocks();
  });

  it('connect() builds the stream URL with the token and comma-joined commodities', () => {
    service.connect(['ore', 'fuel']);

    const url = latestSocket().url;
    expect(url).toContain('/api/v1/ws/market-stream');
    expect(url).toContain('token=fake-access-token');
    expect(url).toContain('commodities=ore%2Cfuel');
  });

  it('does not open a socket when given an empty commodity list', () => {
    service.connect([]);
    expect(FakeSocket.instances.length).toBe(0);
  });

  it('a market_update message reaches onUpdate handlers with the delta intact', () => {
    service.connect(['ore']);
    const received: MarketStreamUpdateMessage[] = [];
    service.onUpdate((message) => received.push(message));

    latestSocket().onmessage?.({
      data: JSON.stringify({
        type: 'market_update',
        commodity: 'ore',
        data: { station_id: 'station-1', buy_price: 12, sell_price: 15 },
        timestamp: '2026-07-09T00:00:00Z',
      }),
    });

    expect(received).toHaveLength(1);
    expect(received[0].commodity).toBe('ore');
    expect(received[0].data.buy_price).toBe(12);
    expect(received[0].data.sell_price).toBe(15);
  });

  it('a connection_established message does NOT reach onUpdate handlers', () => {
    service.connect(['ore']);
    const received: MarketStreamUpdateMessage[] = [];
    service.onUpdate((message) => received.push(message));

    latestSocket().onmessage?.({
      data: JSON.stringify({
        type: 'connection_established',
        commodities: ['ore'],
        update_interval: 1000,
        timestamp: '2026-07-09T00:00:00Z',
      }),
    });

    expect(received).toHaveLength(0);
  });

  it('an unsubscribed handler stops receiving updates', () => {
    service.connect(['ore']);
    const received: MarketStreamUpdateMessage[] = [];
    const unsubscribe = service.onUpdate((message) => received.push(message));
    unsubscribe();

    latestSocket().onmessage?.({
      data: JSON.stringify({
        type: 'market_update',
        commodity: 'ore',
        data: { buy_price: 12, sell_price: 15 },
        timestamp: '2026-07-09T00:00:00Z',
      }),
    });

    expect(received).toHaveLength(0);
  });

  it('onopen marks the service connected and notifies status handlers', () => {
    service.connect(['ore']);
    const statuses: boolean[] = [];
    service.onStatus((connected) => statuses.push(connected));

    latestSocket().onopen?.();

    expect(service.isConnected()).toBe(true);
    expect(statuses).toEqual([true]);
  });

  it('disconnect() closes the live socket and flips connected to false', () => {
    service.connect(['ore']);
    latestSocket().onopen?.();
    expect(service.isConnected()).toBe(true);

    service.disconnect();

    expect(latestSocket().closeCalls).toHaveLength(1);
    expect(service.isConnected()).toBe(false);
  });

  it('connect() with a fresh commodity set tears down the prior socket before opening a new one', () => {
    service.connect(['ore']);
    const first = latestSocket();
    first.onopen?.();

    service.connect(['fuel']);

    expect(first.closeCalls).toHaveLength(1);
    expect(FakeSocket.instances.length).toBe(2);
    expect(latestSocket().url).toContain('commodities=fuel');
  });

  it('a post-disconnect close event does not schedule a reconnect', () => {
    const svc = service as unknown as {
      scheduleReconnect: () => void;
      reconnectWithRefresh: () => void;
    };
    vi.spyOn(svc, 'scheduleReconnect').mockImplementation(() => {});
    vi.spyOn(svc, 'reconnectWithRefresh').mockImplementation(() => {});

    service.connect(['ore']);
    const socket = latestSocket();
    socket.onopen?.();
    service.disconnect();

    // Simulate the browser firing onclose asynchronously after close() —
    // shouldReconnect is already false, so neither reconnect path should fire.
    socket.onclose?.({ code: 1000, reason: 'Client disconnect' });

    expect(svc.scheduleReconnect).not.toHaveBeenCalled();
    expect(svc.reconnectWithRefresh).not.toHaveBeenCalled();
  });

  it('a 4001 close while still subscribed takes the auth-refresh reconnect path', () => {
    const svc = service as unknown as {
      scheduleReconnect: () => void;
      reconnectWithRefresh: () => void;
    };
    vi.spyOn(svc, 'scheduleReconnect').mockImplementation(() => {});
    vi.spyOn(svc, 'reconnectWithRefresh').mockImplementation(() => {});

    service.connect(['ore']);
    latestSocket().onopen?.();
    latestSocket().onclose?.({ code: 4001, reason: 'Invalid authentication token' });

    expect(svc.reconnectWithRefresh).toHaveBeenCalledTimes(1);
    expect(svc.scheduleReconnect).not.toHaveBeenCalled();
  });
});
