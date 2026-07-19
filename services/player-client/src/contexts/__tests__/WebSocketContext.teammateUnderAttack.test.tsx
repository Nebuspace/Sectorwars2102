// @vitest-environment jsdom
/**
 * WebSocketContext — teammate_under_attack WS consumer (WO-RT-TEAM-DEFENSE).
 *
 * combat_service._emit_teammate_under_attack pushes a team-scoped
 * `teammate_under_attack` frame at combat initiation (factions-and-teams.md
 * "Combat advantages: Defensive notifications when any teammate is
 * attacked"). This pins WebSocketContext's generalHandler branch: the frame
 * surfaces exactly one warning-level toast, titled "Teammate Under Attack",
 * with content built from the frame's defender_name/sector_id.
 *
 * Exercises the REAL websocketService singleton (not mocked) — reaches its
 * private notifyHandlers the same way GameContext.quantumHarvest.test.tsx
 * and websocket.eviction.test.ts do. AuthContext is mocked to an
 * unauthenticated user so WebSocketProvider's auto-connect effect never
 * attempts a real WebSocket connection (jsdom has no live server); the
 * message-handler registration effect that owns generalHandler does not
 * depend on auth state and still runs.
 */
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

vi.mock('../AuthContext', () => ({
  useAuth: () => ({ user: null, isAuthenticated: false }),
}));

import { WebSocketProvider, useWebSocket } from '../WebSocketContext';
import { websocketService, type WebSocketMessage } from '../../services/websocket';

// websocketService is a module-level singleton; notifyHandlers is private,
// reached the same way GameContext.quantumHarvest.test.tsx reaches it.
const svc = websocketService as unknown as {
  notifyHandlers: (message: WebSocketMessage) => void;
};

let captured: ReturnType<typeof useWebSocket> | null = null;
function Consumer() {
  captured = useWebSocket();
  return null;
}

const flush = () => new Promise((resolve) => setTimeout(resolve, 0));

describe('WebSocketContext teammate_under_attack WS consumer', () => {
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

  it('surfaces exactly one warning toast built from defender_name/sector_id', async () => {
    act(() => {
      svc.notifyHandlers({
        type: 'teammate_under_attack',
        defender_id: 'defender-1',
        defender_name: 'Ratbone',
        attacker_name: 'Raider',
        sector_id: 42,
        timestamp: new Date().toISOString(),
      });
    });
    await flush();

    expect(captured).not.toBeNull();
    const toasts = captured!.notifications.filter((n) => n.title === 'Teammate Under Attack');
    expect(toasts).toHaveLength(1);
    expect(toasts[0].content).toBe('Ratbone is under attack in sector 42');
    expect(toasts[0].level).toBe('warning');
  });

  it('falls back to a generic name when defender_name is missing', async () => {
    act(() => {
      svc.notifyHandlers({
        type: 'teammate_under_attack',
        defender_id: 'defender-2',
        sector_id: 7,
        timestamp: new Date().toISOString(),
      });
    });
    await flush();

    const toasts = captured!.notifications.filter((n) => n.title === 'Teammate Under Attack');
    expect(toasts).toHaveLength(1);
    expect(toasts[0].content).toBe('A teammate is under attack in sector 7');
  });

  it('is inert for unrelated frame types', async () => {
    act(() => {
      svc.notifyHandlers({ type: 'connection_status', connected: true } as WebSocketMessage);
    });
    await flush();

    expect(captured!.notifications.filter((n) => n.title === 'Teammate Under Attack')).toHaveLength(0);
  });
});
