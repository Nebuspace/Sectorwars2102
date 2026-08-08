// @vitest-environment jsdom
/**
 * WebSocketContext — hostile_detected WS consumer (WO-WIRE-WS-HOSTILE-DETECTED-UNCONSUMED).
 *
 * A Long-Range Scanner Array (citadel_service DEFENSE_BUILDINGS "scanner_array")
 * on an owned planet picks up a hostile ship moving within detection range of
 * the planet's sector (movement_service._dispatch_hostile_detected →
 * websocket_service.send_hostile_detected). This pins WebSocketContext's
 * generalHandler branch: the frame surfaces exactly one warning-level toast,
 * titled "Hostile Detected", built from the frame's sector_id.
 *
 * Mirrors WebSocketContext.teammateUnderAttack.test.tsx's harness exactly —
 * exercises the REAL websocketService singleton (not mocked), reaching its
 * private notifyHandlers the same way. AuthContext is mocked to an
 * unauthenticated user so WebSocketProvider's auto-connect effect never
 * attempts a real WebSocket connection (jsdom has no live server).
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

describe('WebSocketContext hostile_detected WS consumer', () => {
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

  it('surfaces exactly one warning toast built from sector_id', async () => {
    act(() => {
      svc.notifyHandlers({
        type: 'hostile_detected',
        sector_id: 42,
        detection_range: 3,
        ship_id: 'ship-1',
        detected_player_id: 'player-1',
        timestamp: new Date().toISOString(),
      });
    });
    await flush();

    expect(captured).not.toBeNull();
    const toasts = captured!.notifications.filter((n) => n.title === 'Hostile Detected');
    expect(toasts).toHaveLength(1);
    expect(toasts[0].content).toBe('A hostile ship was detected in sector 42');
    expect(toasts[0].level).toBe('warning');
  });

  it('falls back to a generic message when sector_id is missing', async () => {
    act(() => {
      svc.notifyHandlers({
        type: 'hostile_detected',
        detection_range: 3,
        ship_id: 'ship-2',
        detected_player_id: 'player-2',
        timestamp: new Date().toISOString(),
      });
    });
    await flush();

    const toasts = captured!.notifications.filter((n) => n.title === 'Hostile Detected');
    expect(toasts).toHaveLength(1);
    expect(toasts[0].content).toBe('A hostile ship was detected near your planet');
  });

  it('is inert for unrelated frame types', async () => {
    act(() => {
      svc.notifyHandlers({ type: 'connection_status', connected: true } as WebSocketMessage);
    });
    await flush();

    expect(captured!.notifications.filter((n) => n.title === 'Hostile Detected')).toHaveLength(0);
  });
});
