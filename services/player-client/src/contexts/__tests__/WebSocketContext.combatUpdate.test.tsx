// @vitest-environment jsdom
/**
 * WebSocketContext — combat_update WS consumer (WO-WIRE-WS-COMBAT-UPDATE-MISMATCH).
 *
 * Server emits type `combat_update` via connection_manager.send_combat_update;
 * the client previously only handled `combat_event`, silently dropping all
 * live combat notifications. Pins both the canonical type and the legacy alias.
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

describe('WebSocketContext combat_update WS consumer', () => {
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

  it('surfaces a warning toast for combat_update (server wire type)', async () => {
    act(() => {
      svc.notifyHandlers({
        type: 'combat_update',
        combat_id: 'c-1',
        timestamp: new Date().toISOString(),
      });
    });
    await flush();

    const toasts = captured!.notifications.filter((n) => n.title === 'Combat Activity');
    expect(toasts).toHaveLength(1);
    expect(toasts[0].level).toBe('warning');
  });

  it('still accepts legacy combat_event alias', async () => {
    act(() => {
      svc.notifyHandlers({
        type: 'combat_event',
        timestamp: new Date().toISOString(),
      });
    });
    await flush();

    const toasts = captured!.notifications.filter((n) => n.title === 'Combat Activity');
    expect(toasts).toHaveLength(1);
  });
});
