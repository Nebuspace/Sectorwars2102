// @vitest-environment jsdom
/**
 * WebSocketContext — mining_harvest_notification + mining_license_expiry_warning
 * WS consumers (LEG-2658 / LEG-2607 / mining.md:258).
 *
 * Pins WebSocketContext's generalHandler branches for harvest yield, rare/trace
 * drops, and the 1h license-expiry warning. Mirrors hostileDetected harness —
 * real websocketService singleton, AuthContext mocked unauthenticated.
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

vi.mock('../AuthContext', () => ({
  useAuth: () => ({ user: null, isAuthenticated: false }),
}));

import { WebSocketProvider, useWebSocket } from '../WebSocketContext';
import { websocketService, type WebSocketMessage } from '../../services/websocket';

const svc = websocketService as unknown as {
  notifyHandlers: (message: WebSocketMessage) => void;
};

let captured: ReturnType<typeof useWebSocket> | null = null;
function Consumer() {
  captured = useWebSocket();
  return null;
}

const flush = () => new Promise((resolve) => setTimeout(resolve, 0));

describe('WebSocketContext mining notification WS consumers', () => {
  let container: HTMLElement;
  let root: ReturnType<typeof createRoot>;

  beforeEach(async () => {
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
    captured = null;

    act(() => {
      root.render(
        React.createElement(WebSocketProvider, null, React.createElement(Consumer))
      );
    });
    await flush();
  });

  afterEach(() => {
    act(() => {
      root.unmount();
    });
    container.remove();
  });

  it('surfaces harvest_success toast from ore payload', async () => {
    act(() => {
      svc.notifyHandlers({
        type: 'mining_harvest_notification',
        subtype: 'harvest_success',
        delivery: ['inbox', 'toast'],
        payload: { harvest_id: 'h1', ore: 12, precious_metals: 0, quantum_shards: 0 },
      });
    });
    await flush();

    const toasts = captured!.notifications.filter((n) => n.title === 'Harvest Complete');
    expect(toasts).toHaveLength(1);
    expect(toasts[0].content).toBe('Mined 12 ore');
    expect(toasts[0].level).toBe('success');
  });

  it('surfaces precious_metals rare-drop toast', async () => {
    act(() => {
      svc.notifyHandlers({
        type: 'mining_harvest_notification',
        subtype: 'precious_metals',
        delivery: ['inbox', 'toast'],
        payload: { harvest_id: 'h2', drop_type: 'precious_metals', amount: 3 },
      });
    });
    await flush();

    const toasts = captured!.notifications.filter((n) => n.title === 'Rare Drop');
    expect(toasts).toHaveLength(1);
    expect(toasts[0].content).toBe('Found 3 precious metals');
    expect(toasts[0].level).toBe('success');
  });

  it('surfaces quantum_shards trace-drop toast', async () => {
    act(() => {
      svc.notifyHandlers({
        type: 'mining_harvest_notification',
        subtype: 'quantum_shards',
        delivery: ['inbox', 'toast'],
        payload: { harvest_id: 'h3', drop_type: 'quantum_shards', amount: 1 },
      });
    });
    await flush();

    const toasts = captured!.notifications.filter((n) => n.title === 'Trace Drop');
    expect(toasts).toHaveLength(1);
    expect(toasts[0].content).toBe('Found 1 quantum shards');
    expect(toasts[0].level).toBe('success');
  });

  it('surfaces license expiry warning with sector number', async () => {
    act(() => {
      svc.notifyHandlers({
        type: 'mining_license_expiry_warning',
        delivery: ['inbox', 'toast'],
        payload: {
          license_id: 'lic-1',
          sector_number: 42,
          expires_at: '2026-08-28T13:00:00+00:00',
        },
      });
    });
    await flush();

    const toasts = captured!.notifications.filter((n) => n.title === 'Mining License Expiring');
    expect(toasts).toHaveLength(1);
    expect(toasts[0].content).toBe('Your AM claim license in sector 42 expires within one hour.');
    expect(toasts[0].level).toBe('warning');
  });

  it('is inert when delivery omits toast', async () => {
    act(() => {
      svc.notifyHandlers({
        type: 'mining_harvest_notification',
        subtype: 'harvest_success',
        delivery: ['inbox'],
        payload: { ore: 99 },
      });
    });
    await flush();

    expect(captured!.notifications.filter((n) => n.title === 'Harvest Complete')).toHaveLength(0);
  });

  it('is inert for unrelated frame types', async () => {
    act(() => {
      svc.notifyHandlers({ type: 'connection_status', connected: true } as WebSocketMessage);
    });
    await flush();

    expect(captured!.notifications.filter((n) => n.title === 'Harvest Complete')).toHaveLength(0);
    expect(captured!.notifications.filter((n) => n.title === 'Mining License Expiring')).toHaveLength(0);
  });
});
