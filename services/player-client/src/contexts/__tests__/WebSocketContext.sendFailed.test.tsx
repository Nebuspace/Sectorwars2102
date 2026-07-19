// @vitest-environment jsdom
/**
 * WebSocketContext — send_failed → visible toast (WO-UI2-FLIGHT-FEEL UX
 * nit). websocketService.send() previously only console.warn'd on a dead
 * uplink; this pins the new wiring that turns it into a real, in-cockpit
 * warning toast via the SAME addNotification idiom every other toast
 * (teammate_under_attack, medal_awarded, ...) already uses.
 *
 * Mirrors WebSocketContext.teammateUnderAttack.test.tsx's proven seam
 * exactly: real WebSocketProvider + websocketService singleton (not
 * mocked), notifyHandlers reached directly, AuthContext mocked
 * unauthenticated so the auto-connect effect never attempts a real socket.
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

describe('WebSocketContext send_failed toast', () => {
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

  it('surfaces exactly one "Uplink down" warning toast on a send_failed frame', async () => {
    act(() => {
      svc.notifyHandlers({
        type: 'send_failed',
        messageType: 'chat_message',
        timestamp: new Date().toISOString(),
      });
    });
    await flush();

    expect(captured).not.toBeNull();
    const toasts = captured!.notifications.filter((n) => n.title === 'Uplink down');
    expect(toasts).toHaveLength(1);
    expect(toasts[0].level).toBe('warning');
    expect(toasts[0].content).toMatch(/connection is down/i);
  });

  it('does not surface a "Unhandled message type" path for send_failed (consumed, not logged as unknown)', async () => {
    const warnSpy = vi.spyOn(console, 'warn').mockImplementation(() => {});
    act(() => {
      svc.notifyHandlers({
        type: 'send_failed',
        messageType: 'aria_chat',
        timestamp: new Date().toISOString(),
      });
    });
    await flush();

    expect(warnSpy.mock.calls.some((c) => String(c[0]).includes('Unhandled message type'))).toBe(false);
    warnSpy.mockRestore();
  });

  it('is inert for unrelated frame types', async () => {
    act(() => {
      svc.notifyHandlers({ type: 'connection_status', connected: true } as WebSocketMessage);
    });
    await flush();

    expect(captured!.notifications.filter((n) => n.title === 'Uplink down')).toHaveLength(0);
  });
});
