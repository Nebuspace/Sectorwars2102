// @vitest-environment jsdom
/**
 * WebSocketContext — mining_harvest_notification + mining_license_expiry_warning
 * WS consumers (LEG-2737 / mining.md:258).
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

vi.mock('../AuthContext', () => ({
  useAuth: () => ({ user: null, isAuthenticated: false }),
}));

import { WebSocketProvider, useWebSocket } from '../WebSocketContext';
import { websocketService, type WebSocketMessage } from '../../services/websocket';
import {
  __resetSpacedockVenueBusForTests,
  getLatestSpacedockVenueRequest,
} from '../../services/spacedockVenueBus';

const svc = websocketService as unknown as {
  notifyHandlers: (message: WebSocketMessage) => void;
};

let captured: ReturnType<typeof useWebSocket> | null = null;
function Consumer() {
  captured = useWebSocket();
  return null;
}

const flush = () => new Promise((resolve) => setTimeout(resolve, 0));

describe('WebSocketContext mining WS consumers (LEG-2737)', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;

  beforeEach(async () => {
    __resetSpacedockVenueBusForTests();
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
    captured = null;

    act(() => {
      root.render(
        React.createElement(WebSocketProvider, null, React.createElement(Consumer)),
      );
    });
    await flush();
  });

  afterEach(() => {
    act(() => {
      root.unmount();
    });
    container.remove();
    __resetSpacedockVenueBusForTests();
  });

  it('surfaces harvest_success as a success toast', async () => {
    act(() => {
      svc.notifyHandlers({
        type: 'mining_harvest_notification',
        subtype: 'harvest_success',
        delivery: ['inbox', 'toast'],
        payload: { ore: 10, precious_metals: 0, quantum_shards: 0 },
      } as WebSocketMessage);
    });
    await flush();

    const toasts = captured!.notifications.filter((n) => n.title === 'Harvest Complete');
    expect(toasts).toHaveLength(1);
    expect(toasts[0].content).toContain('10 ore');
    expect(toasts[0].level).toBe('success');
  });

  it('surfaces precious_metals rare-drop toast', async () => {
    act(() => {
      svc.notifyHandlers({
        type: 'mining_harvest_notification',
        subtype: 'precious_metals',
        delivery: ['toast'],
        payload: { drop_type: 'precious_metals', amount: 3 },
      } as WebSocketMessage);
    });
    await flush();

    const toasts = captured!.notifications.filter(
      (n) => n.title === 'Rare Drop — Precious Metals',
    );
    expect(toasts).toHaveLength(1);
    expect(toasts[0].content).toContain('3 precious metals');
  });

  it('surfaces license expiry warning and latches mining venue deep-link', async () => {
    act(() => {
      svc.notifyHandlers({
        type: 'mining_license_expiry_warning',
        delivery: ['inbox', 'toast'],
        payload: {
          license_id: 'lic-9',
          sector_number: 7,
          expires_at: '2026-08-28T13:00:00.000Z',
        },
      } as WebSocketMessage);
    });
    await flush();

    const toasts = captured!.notifications.filter((n) => n.title === 'Mining License Expiring');
    expect(toasts).toHaveLength(1);
    expect(toasts[0].content).toContain('sector 7');
    expect(toasts[0].level).toBe('warning');
    expect(getLatestSpacedockVenueRequest()?.venue).toBe('mining');
  });

  it('is inert for unrelated frame types', async () => {
    act(() => {
      svc.notifyHandlers({ type: 'connection_status', connected: true } as WebSocketMessage);
    });
    await flush();

    expect(captured!.notifications.filter((n) => n.title === 'Harvest Complete')).toHaveLength(0);
    expect(getLatestSpacedockVenueRequest()).toBeNull();
  });
});
